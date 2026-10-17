"""Repository-wide OpenGolfAPI request ledger.

OpenGolfAPI allows 1,000 anonymous requests per day per IP. Every GitHub runner has a
new IP, so the provider limit alone would never bind; this ledger is the safeguard.

- A job reserves requests by committing `ledger/opengolfapi/<UTC day>.json` to main
  BEFORE it makes any request. Reservations are keyed by workflow/run/attempt/purpose
  and never refunded, so a failed or cancelled run still counts.
- All reservations for one UTC day share DAILY_LIMIT.
- Every caller uses Budget, which fails closed without a reservation for the current
  UTC day, never exceeds its granted count and paces requests.
- HTTP 429, `X-RateLimit-Remaining: 0` and Retry-After stop every further request.
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

DAILY_LIMIT = 800
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


class Budget:
    """The only way to spend OpenGolfAPI requests. Fails closed without a reservation."""

    def __init__(self, reservation=None, pause=time.sleep, clock=utc_now, usage_path=USAGE):
        if reservation is None:
            if not RESERVATION.exists():
                raise RuntimeError('No OpenGolfAPI reservation for this job; refusing requests (fail closed)')
            reservation = read(RESERVATION)
        self.day = reservation['day']; self.budget = int(reservation['granted']); self.key = reservation['key']
        self.pause = pause; self.clock = clock; self.usage_path = usage_path
        self.requests = 0; self.stopped = None; self.resume_after = None

    def consume(self):
        if self.stopped:
            raise RuntimeError(self.stopped)
        if self.clock().date().isoformat() != self.day:
            raise RuntimeError(f'Reservation is for UTC day {self.day}; reserve again for the current day')
        if self.requests >= self.budget:
            raise RuntimeError(f'Reserved request budget exhausted ({self.budget})')
        self.pause(PACE_SECONDS)
        self.requests += 1
        self.save()

    def rate_stop(self, headers):
        self.resume_after = reset_after(headers or {}, self.clock()).isoformat()
        self.stopped = 'Source rate limit reached; resume only after ' + self.resume_after
        self.save()

    def observe(self, headers):
        if headers is not None and headers.get('X-RateLimit-Remaining') == '0':
            self.rate_stop(headers)

    def http_error(self, exc):
        if getattr(exc, 'code', None) == 429:
            self.rate_stop(exc.headers)

    def save(self):
        if self.usage_path:
            write(self.usage_path, {'key': self.key, 'day': self.day, 'granted': self.budget, 'used': self.requests,
                                    'stopped': self.stopped, 'resumeAfterUTC': self.resume_after, 'updatedAt': now()})


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
    previous_day = (at - timedelta(days=1)).date().isoformat()

    def apply(root):
        path = root / LEDGER / f'{day}.json'
        entries = read(path)['reservations'] if path.exists() else []
        earlier = root / LEDGER / f'{previous_day}.json'
        earlier_entries = read(earlier)['reservations'] if earlier.exists() else []
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
