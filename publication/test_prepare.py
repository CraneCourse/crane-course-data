import copy, importlib.util, json, tempfile, unittest
from pathlib import Path
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
if __name__=='__main__':unittest.main()
