"""Repository-wide OpenGolfAPI request ledger.

OpenGolfAPI's documentation states 1,000 anonymous requests per day per IP, but its live
responses report `X-RateLimit-Limit: 500` (observed in validation run 38987496757, which
stopped cleanly at remaining=0). The ledger uses the lower, observed limit. Every GitHub
runner has a new IP, so the provider limit alone would never bind; this ledger is the safeguard.

- A job reserves requests by committing `ledger/opengolfapi/<UTC day>.json` to main
  BEFORE it makes any request. Reservations are keyed by workflow/run/attempt/purpose
  and never refunded, so a failed or cancelled run still counts.
- All reservations for one UTC day share DAILY_LIMIT.
- Every caller uses Budget, which fails closed without a reservation for the current
  UTC day, never exceeds its granted count and paces requests.
- HTTP 429, any error with Retry-After (e.g. 503) and `X-RateLimit-Remaining: 0` stop
  every further request; the stop is committed at once and other active holders see it.
  `record` writes the later reset time into the ledger, and later reservations are
  granted nothing until it passes. There are no retries around a limit and no IP rotation.
- Concurrent writers use fetch/update/commit/push with retries, so two jobs cannot
  both claim the same headroom.
"""
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from pipeline.common import read, write, now

DAILY_LIMIT = 500  # Observed X-RateLimit-Limit; lower than the documented 1,000
LEDGER = Path('ledger/opengolfapi')
RESERVATION = Path('work/api-budget/reservation.json')
USAGE = Path('work/api-budget/usage.json')
PACE_SECONDS = 1.5


def utc_now():
    return datetime.now(timezone.utc)


def plan(entries, key, requested, at, earlier_entries=()):
    """Decide one reservation. Pure: returns (entries, granted).

    Idempotent per key (a retried reserve step never double-books). Every earlier
    reservation counts in full, whether or not its run used or finished it.
    """
    for entry in entries:
        if entry['key'] == key:
            return entries, entry['granted']
    blocked = [e['resumeAfterUTC'] for e in list(entries) + list(earlier_entries) if e.get('resumeAfterUTC')]
    used = sum(e['granted'] for e in entries)
    if requested <= 0 or any(datetime.fromisoformat(b) > at for b in blocked):
        granted = 0
    else:
        granted = max(0, min(requested, DAILY_LIMIT - used))
    return list(entries) + [{'key': key, 'requested': requested, 'granted': granted, 'reservedAt': at.isoformat()}], granted


def reset_after(headers, current):
    """The later of X-RateLimit-Reset and Retry-After; next UTC midnight if neither parses."""
    headers = {k.lower(): v for k, v in dict(headers).items()}
    targets = []
    reset = headers.get('x-ratelimit-reset')
    if reset:
        try:
            targets.append(datetime.fromtimestamp(float(reset), timezone.utc))
        except (ValueError, TypeError, OverflowError, OSError):
            try:
                parsed = datetime.fromisoformat(reset.replace('Z', '+00:00'))
                targets.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
            except (ValueError, TypeError):
                pass
    retry = headers.get('retry-after')
    if retry:
        try:
            parsed = current + timedelta(seconds=int(retry)) if retry.isdigit() else parsedate_to_datetime(retry)
            targets.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
        except (ValueError, TypeError, OverflowError):
            pass
    # Respect both headers when present; never choose the shorter wait.
    return max(targets) if targets else (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


SHARED_CHECK_EVERY = 25   # requests between checks for a stop recorded by another job


class Budget:
    """The only way to spend OpenGolfAPI requests. Fails closed without a reservation.

    Several processes in one job (e.g. scorecards then controls) share one reservation:
    each resumes the recorded use and stop, so the grant is never spent twice. A stop
    is committed to the ledger at once, and active holders check the ledger every
    SHARED_CHECK_EVERY requests, so one job's stop halts the others.
    """

    def __init__(self, reservation=None, pause=time.sleep, clock=utc_now, usage_path=USAGE, shared_stop=None, publish=None):
        production = reservation is None
        if production:
            if not RESERVATION.exists():
                raise RuntimeError('No OpenGolfAPI reservation for this job; refusing requests (fail closed)')
            reservation = read(RESERVATION)
        self.day = reservation['day']; self.budget = int(reservation['granted']); self.key = reservation['key']
        self.pause = pause; self.clock = clock; self.usage_path = usage_path
        self.shared_stop = shared_stop if shared_stop is not None else (ledger_stop if production else None)
        self.publish = production if publish is None else publish
        self.requests = 0; self.stopped = None; self.resume_after = None
        if usage_path and Path(usage_path).exists():
            used = read(usage_path)
            if used.get('key') == self.key and used.get('day') == self.day:
                self.requests = int(used.get('used', 0)); self.stopped = used.get('stopped'); self.resume_after = used.get('resumeAfterUTC')

    def consume(self):
        if self.stopped:
            raise RuntimeError(self.stopped)
        if self.clock().date().isoformat() != self.day:
            raise RuntimeError(f'Reservation is for UTC day {self.day}; reserve again for the current day')
        if self.requests >= self.budget:
            raise RuntimeError(f'Reserved request budget exhausted ({self.budget})')
        if self.shared_stop and self.requests % SHARED_CHECK_EVERY == 0:
            until = self.shared_stop(self.clock())
            if until:
                self.resume_after = until
                self.stopped = 'Another job recorded a source stop; resume only after ' + until
                self.save()
                raise RuntimeError(self.stopped)
        self.pause(PACE_SECONDS)
        self.requests += 1
        self.save()

    def rate_stop(self, headers):
        self.resume_after = reset_after(headers or {}, self.clock()).isoformat()
        self.stopped = 'Source rate limit reached; resume only after ' + self.resume_after
        self.save()
        if self.publish:
            try:
                publish_stop(self.key, self.day, self.resume_after, self.stopped)
            except Exception as exc:  # The local stop still holds; record retries at job end.
                print('Could not publish stop to ledger yet:', exc, flush=True)

    def observe(self, headers):
        if headers is not None and headers.get('X-RateLimit-Remaining') == '0':
            self.rate_stop(headers)

    def http_error(self, exc):
        """429, or any error carrying Retry-After (e.g. 503), stops every further request."""
        headers = getattr(exc, 'headers', None)
        if getattr(exc, 'code', None) == 429 or (headers is not None and headers.get('Retry-After')):
            self.rate_stop(headers)

    def save(self):
        if self.usage_path:
            write(self.usage_path, {'key': self.key, 'day': self.day, 'granted': self.budget, 'used': self.requests,
                                    'stopped': self.stopped, 'resumeAfterUTC': self.resume_after, 'updatedAt': now()})


def unexpired_stop(ledgers, at):
    """Latest resumeAfterUTC still in the future across all ledger days, or None."""
    stops = [e['resumeAfterUTC'] for data in ledgers for e in data.get('reservations', []) if e.get('resumeAfterUTC')]
    future = [s for s in stops if datetime.fromisoformat(s) > at]
    return max(future, key=datetime.fromisoformat) if future else None


def ledger_stop(at):
    """Read every ledger day on origin/main; an infrastructure error never invents a stop."""
    try:
        git('fetch', '--quiet', 'origin', 'main')
        import json
        names = git('ls-tree', '--name-only', 'FETCH_HEAD', str(LEDGER) + '/').stdout.split()
        ledgers = [json.loads(git('show', f'FETCH_HEAD:{name}').stdout) for name in names if name.endswith('.json')]
    except Exception as exc:
        print('Ledger stop check unavailable:', exc, flush=True)
        return None
    return unexpired_stop(ledgers, at)


def publish_stop(key, day, resume_after, stopped):
    def apply(root):
        path = root / LEDGER / f'{day}.json'
        data = read(path)
        for entry in data['reservations']:
            if entry['key'] == key:
                entry.update(resumeAfterUTC=resume_after, stopped=stopped)
        write(path, data)
    commit_files(apply, f'Record OpenGolfAPI source stop for {key}')


def git(*args, check=True):
    return subprocess.run(['git', *args], check=check, capture_output=True, text=True)


def commit_files(apply, message, attempts=8):
    """Apply a change to a clean worktree of origin/main, commit and push; retry on races.

    `apply(root)` edits files under root and returns a value. The job's own checkout
    and work files are never touched.
    """
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    for attempt in range(attempts):
        root = Path(tempfile.mkdtemp()) / 'main'
        git('fetch', '--quiet', 'origin', 'main')
        # FETCH_HEAD works in shallow Actions checkouts without a tracking ref.
        git('worktree', 'add', '--detach', str(root), 'FETCH_HEAD')
        try:
            value = apply(root)
            git('-C', str(root), 'add', '-A')
            if git('-C', str(root), 'diff', '--cached', '--quiet', check=False).returncode == 0:
                return value
            git('-C', str(root), 'commit', '-m', message)
            if git('-C', str(root), 'push', 'origin', 'HEAD:main', check=False).returncode == 0:
                return value
        finally:
            git('worktree', 'remove', '--force', str(root), check=False)
        time.sleep(2 + 3 * attempt)
    raise RuntimeError('Could not record to main after retries; refusing to proceed')


def job_key(purpose):
    import os
    return '/'.join([os.environ['GITHUB_WORKFLOW'], os.environ['GITHUB_RUN_ID'], os.environ.get('GITHUB_RUN_ATTEMPT', '1'), purpose])


def reserve(requested, purpose):
    at = utc_now(); day = at.date().isoformat(); key = job_key(purpose)

    def apply(root):
        path = root / LEDGER / f'{day}.json'
        entries = read(path)['reservations'] if path.exists() else []
        # A stop recorded on any earlier day still applies until its reset passes.
        earlier_entries = [e for other in (root / LEDGER).glob('*.json') if other.name != f'{day}.json'
                           for e in read(other)['reservations']]
        entries, granted = plan(entries, key, requested, at, earlier_entries)
        write(path, {'day': day, 'dailyLimit': DAILY_LIMIT, 'reservations': entries})
        return granted

    granted = commit_files(apply, f'Reserve {requested} OpenGolfAPI requests for {key}')
    write(RESERVATION, {'day': day, 'key': key, 'purpose': purpose, 'requested': requested, 'granted': granted, 'reservedAt': at.isoformat()})
    print(f'OpenGolfAPI reservation {key}: granted {granted}/{requested} for {day}', flush=True)


def record():
    """Record actual use and any rate stop against the reservation; never refunds."""
    if not RESERVATION.exists():
        print('No reservation was made; nothing to record', flush=True); return
    reservation = read(RESERVATION)
    usage = read(USAGE) if USAGE.exists() else {'used': 0, 'stopped': None, 'resumeAfterUTC': None}

    def apply(root):
        path = root / LEDGER / f'{reservation["day"]}.json'
        data = read(path)
        for entry in data['reservations']:
            if entry['key'] == reservation['key']:
                entry.update(used=usage['used'], stopped=usage.get('stopped'), recordedAt=now())
                if usage.get('resumeAfterUTC'):
                    entry['resumeAfterUTC'] = usage['resumeAfterUTC']
        write(path, data)

    commit_files(apply, f'Record OpenGolfAPI use for {reservation["key"]}')


def need(kind):
    """Requests a job may make, so it reserves no more than it can use."""
    from pipeline.common import sample, state_for
    if kind == 'scorecards':
        missing = [c for c in sample() if state_for(c) and not (Path('work/scorecards') / (c['id'] + '.json')).exists()]
        return 2 * len(missing)          # at most two attempts per card
    if kind == 'controls':
        return 2 * 4                     # two controls x holes/features, two attempts
    if kind == 'layout-details':
        rows = read('reports/build-2026-10-16-38973050174.json')['rows']
        return 2 * sum(r['status'] not in ('data-ready', 'outside-state-scope') for r in rows)
    if kind == 'catalog':
        from pipeline.catalog_inventory import MAX_REQUESTS
        return MAX_REQUESTS
    raise SystemExit('Unknown request kind: ' + kind)


def main():
    from pipeline.common import cloud_only
    cloud_only()
    command = sys.argv[1]
    if command == 'reserve':
        reserve(int(sys.argv[2]), sys.argv[3])
    elif command == 'record':
        record()
    elif command == 'need':
        print(need(sys.argv[2]))
    else:
        raise SystemExit('Unknown command')


if __name__ == '__main__':
    main()
