import copy, importlib.util, json, tempfile, unittest, os
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('publication_prepare',Path(__file__).with_name('prepare.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'site/courses').mkdir(parents=True)
        candidate={'id':'example','name':'Example','city':'Town','state':'NY','center':{'lat':42,'lon':-77}}
        doc={'candidate':candidate,'holes':[{}]*9,'builtAt':'2026-10-19T00:00:00Z','dataVersion':'approved','sources':['a','b','c']}
        data=json.dumps(doc).encode();(self.root/'site/courses/example.json').write_bytes(data)
        entry=dict(candidate,holes=9,builtAt=doc['builtAt'],dataVersion='approved',file='courses/example.json',sha256=module.digest(data))
        index={'dataVersion':'approved','builtAt':doc['builtAt'],'sources':doc['sources'],'courses':[entry]}
        (self.root/'site/index.json').write_text(json.dumps(index));(self.root/'app-index.json').write_text(json.dumps(dict(index,schemaVersion=1)))
        self.manifest={'catalogVersion':'approved','builtAt':doc['builtAt'],'appCourses':1,'legacyCourses':1,'courseFiles':1,'files':{}}
        for name in ['courses/example.json','index.json','app-index.json']:
            path=self.root/name if name=='app-index.json' else self.root/'site'/name
            self.manifest['files'][name]=module.digest(path.read_bytes())
    def test_valid_snapshot(self): self.assertEqual(len(module.verify_source(self.root,self.manifest)),3)
    def verify_with_course_size(self, size):
        original=Path.stat
        course=self.root/'site/courses/example.json'
        def override(path,*args,**kwargs):
            stat=original(path,*args,**kwargs)
            if path==course:
                fields=list(stat); fields[6]=size; return os.stat_result(fields)
            return stat
        with patch.object(Path,'stat',override):
            return module.verify_source(self.root,self.manifest)
    def test_publication_packaging_accepts_reviewed_900mb_snapshot(self):
        self.assertEqual(module.MAX_SNAPSHOT_BYTES,1_000_000_000)
        self.assertEqual(len(self.verify_with_course_size(900_000_000)),3)
    def test_publication_packaging_rejects_1gb_snapshot(self):
        with self.assertRaisesRegex(AssertionError,'packaging limit'):
            self.verify_with_course_size(1_000_000_000)
    def test_corrupted_course(self):
        (self.root/'site/courses/example.json').write_text('{}')
        with self.assertRaises(AssertionError):module.verify_source(self.root,self.manifest)
    def test_unreviewed_file(self):
        (self.root/'site/internal-report.txt').write_text('not approved')
        with self.assertRaises(AssertionError):module.verify_source(self.root,self.manifest)
    def test_missing_file(self):
        (self.root/'site/index.json').unlink()
        with self.assertRaises(AssertionError):module.verify_source(self.root,self.manifest)
    def test_mixed_version(self):
        p=self.root/'app-index.json';d=json.loads(p.read_text());d['courses'][0]['dataVersion']='other';p.write_text(json.dumps(d));self.manifest['files']['app-index.json']=module.digest(p.read_bytes())
        with self.assertRaises(AssertionError):module.verify_source(self.root,self.manifest)
    def test_unsafe_path(self):
        for name in ['../secret','/secret','a/../b']:
            with self.assertRaises(AssertionError):module.safe_path(name)
    def test_symlink(self):
        (self.root/'site/link').symlink_to(self.root/'app-index.json')
        with self.assertRaises(AssertionError):module.verify_source(self.root,self.manifest)
class LiveSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.before = {'app-index.json':b'unchanged catalog','index.html':b'old page','report.json':b'old report'}
        self.after = dict(self.before, **{'index.html':b'new page','report.json':b'new notice'})
        self.manifests = [{'files':{n:module.digest(b) for n,b in files.items()}} for files in (self.before,self.after)]
    def check(self, live):
        with patch.object(module,'fetch',side_effect=lambda n,**kwargs:live[n]), patch.object(module.time,'sleep'):
            module.check_live(*self.manifests)
    def test_static_cleanup_before(self): self.check(self.before)
    def test_static_cleanup_after_repeat(self): self.check(self.after)
    def test_static_cleanup_mixture_rejected(self):
        with self.assertRaises(AssertionError): self.check(dict(self.before, **{'index.html':self.after['index.html']}))
    def test_unapproved_index_rejected(self):
        with self.assertRaises(AssertionError): self.check(dict(self.before, **{'app-index.json':b'unknown catalog'}))

class FetchRecoveryTests(unittest.TestCase):
    def response(self, data=b'ok', url=None, status=200):
        from unittest.mock import MagicMock
        r=MagicMock();r.__enter__.return_value=r;r.read.return_value=data
        r.status=status;r.url=url or module.BASE+'example.json';return r
    def error(self, status=503, retry_after=None):
        from urllib.error import HTTPError
        import io
        headers={'X-GitHub-Request-Id':'test-request'}
        if retry_after is not None:headers['Retry-After']=retry_after
        return HTTPError(module.BASE+'example.json',status,'Test response',headers,io.BytesIO())
    def test_recovers_same_url_and_exact_bytes(self):
        error=self.error();r=self.response(b'exact bytes')
        with patch.object(module.urllib.request,'urlopen',side_effect=[error,r]) as fetch, patch.object(module.time,'sleep') as sleep:
            self.assertEqual(module.fetch('example.json'),b'exact bytes')
        self.assertEqual(fetch.call_count,2);sleep.assert_called_once_with(2)
        self.assertEqual([c.args[0].full_url for c in fetch.call_args_list],[module.BASE+'example.json']*2)
        self.assertTrue(error.fp.closed)
    def test_persistent_503_has_four_attempt_limit(self):
        errors=[self.error() for _ in range(4)]
        with patch.object(module.urllib.request,'urlopen',side_effect=errors) as fetch, patch.object(module.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'Cannot verify live file'):module.fetch('example.json')
        self.assertEqual(fetch.call_count,4);self.assertEqual([c.args[0] for c in sleep.call_args_list],[2,4,8])
    def test_permanent_and_rate_limit_errors_are_not_retried(self):
        for status in [400,401,403,404,429]:
            with self.subTest(status=status), patch.object(module.urllib.request,'urlopen',side_effect=self.error(status)) as fetch, patch.object(module.time,'sleep') as sleep:
                with self.assertRaises(RuntimeError):module.fetch('example.json')
                self.assertEqual(fetch.call_count,1);sleep.assert_not_called()
    def test_retry_after_seconds_is_honored(self):
        with patch.object(module.urllib.request,'urlopen',side_effect=[self.error(retry_after='20'),self.response()]), patch.object(module.time,'sleep') as sleep:
            module.fetch('example.json')
        sleep.assert_called_once_with(20)
    def test_retry_after_date_is_honored(self):
        from datetime import datetime,timezone
        with patch.object(module,'datetime') as clock:
            clock.now.return_value=datetime(2026,10,27,0,0,0,tzinfo=timezone.utc)
            self.assertEqual(module.retry_delay('Tue, 27 Oct 2026 00:00:30 GMT',1),30)
    def test_long_retry_after_stops_without_early_retry(self):
        with patch.object(module.urllib.request,'urlopen',side_effect=self.error(retry_after='600')) as fetch, patch.object(module.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'defer publication'):module.fetch('example.json')
        self.assertEqual(fetch.call_count,1);sleep.assert_not_called()
    def test_invalid_retry_after_uses_bounded_backoff(self):
        self.assertEqual(module.retry_delay('invalid',2),4)
    def test_timeout_recovers_but_certificate_failure_stops(self):
        import ssl
        from urllib.error import URLError
        with patch.object(module.urllib.request,'urlopen',side_effect=[URLError(TimeoutError('timed out')),self.response()]), patch.object(module.time,'sleep'):
            self.assertEqual(module.fetch('example.json'),b'ok')
        with patch.object(module.urllib.request,'urlopen',side_effect=URLError(ssl.SSLCertVerificationError('invalid certificate'))) as fetch, patch.object(module.time,'sleep') as sleep:
            with self.assertRaises(RuntimeError):module.fetch('example.json')
        self.assertEqual(fetch.call_count,1);sleep.assert_not_called()
    def test_retry_budget_is_shared_and_exhaustion_stops(self):
        budget={'remaining':1}
        with patch.object(module.urllib.request,'urlopen',side_effect=[self.error(),self.response(),self.error()]) as fetch, patch.object(module.time,'sleep') as sleep:
            module.fetch('example.json',retry_budget=budget)
            with self.assertRaises(RuntimeError):module.fetch('example.json',retry_budget=budget)
        self.assertEqual(budget['remaining'],0);self.assertEqual(fetch.call_count,3);sleep.assert_called_once_with(2)
    def test_complete_baseline_uses_one_shared_budget(self):
        files={'app-index.json':b'index','one.json':b'one','two.json':b'two'}
        manifest={'files':{n:module.digest(b) for n,b in files.items()}};budgets=[]
        def fetch(name,retry_budget=None):budgets.append(retry_budget);return files[name]
        with patch.object(module,'fetch',side_effect=fetch),patch.object(module.time,'sleep'):
            module.check_live(manifest,manifest)
        self.assertEqual(len(budgets),3);self.assertTrue(all(b is budgets[0] for b in budgets));self.assertEqual(budgets[0]['remaining'],12)
    def test_recovery_does_not_accept_a_hash_mismatch(self):
        expected={'files':{'app-index.json':module.digest(b'expected')}}
        r=self.response(b'corrupt',module.BASE+'app-index.json')
        with patch.object(module.urllib.request,'urlopen',side_effect=[self.error(),r]) as fetch,patch.object(module.time,'sleep'):
            with self.assertRaisesRegex(AssertionError,'Live catalog changed'):module.check_live(expected,expected)
        self.assertEqual(fetch.call_count,2)
    def test_response_guards_are_not_retried(self):
        for r in [self.response(b'x'*16_000_001),self.response(url='https://example.com/other'),self.response(status=206)]:
            with patch.object(module.urllib.request,'urlopen',return_value=r) as fetch,patch.object(module.time,'sleep') as sleep:
                with self.assertRaises(AssertionError):module.fetch('example.json')
                self.assertEqual(fetch.call_count,1);sleep.assert_not_called()
    def test_failure_logs_url_status_and_request_id(self):
        import io,contextlib
        out=io.StringIO()
        with contextlib.redirect_stdout(out),patch.object(module.urllib.request,'urlopen',side_effect=self.error(404)):
            with self.assertRaises(RuntimeError):module.fetch('example.json')
        event=json.loads(out.getvalue());self.assertEqual(event['url'],module.BASE+'example.json');self.assertEqual(event['status'],404);self.assertEqual(event['requestID'],'test-request')

if __name__=='__main__':unittest.main()
