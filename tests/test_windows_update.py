"""Exercise update packaging with synthetic portable folders, never an installation."""
import hashlib,importlib.util,json,tempfile,unittest,zipfile
from pathlib import Path
b=Path(__file__).resolve().parent.parent
s=importlib.util.spec_from_file_location('windows_update',b/'scripts/build_windows_update.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class UpdateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.new=self.fixture('new','1.14.3');self.old=self.fixture('old','1.14.1');self.out=self.root/'PCA Windows Update 1.14.3'
 def fixture(self,name,version):
  root=self.root/name;(root/'runtime').mkdir(parents=True);(root/'runtime/python.exe').write_bytes(b'MZ-fixture')
  for f in m.REQUIRED_APP_FILES:
   path=root/'app'/f;path.parent.mkdir(parents=True,exist_ok=True)
   path.write_text(f"APP_VERSION = {version!r}\n" if f=='app_runtime.py' else '# fixture\n')
  for f in m.REQUIRED_FILES:(root/f).write_text('@echo off\n' if f.endswith('.cmd') else 'fixture\n')
  manifest=dict(source_version=version,python_archive={'name':'python-embed-amd64.zip','sha256':'a'*64},wheels={'numpy-none-win_amd64.whl':'b'*64},source_files={p.relative_to(root/'app').as_posix():m.digest(p) for p in (root/'app').rglob('*.py')})
  (root/'BUILD-DETAILS.json').write_text(json.dumps(manifest))
  return root
 def test_complete_app_and_launchers_without_runtime_or_absolute_paths(self):
  before={p.relative_to(self.old).as_posix():m.digest(p) for p in self.old.rglob('*') if p.is_file()}
  archive=m.build(self.new,self.old,self.out)
  with zipfile.ZipFile(archive) as z:
   names=z.namelist();self.assertTrue(all(n.startswith(self.out.name+'/') for n in names))
   self.assertFalse(any('/runtime/' in n for n in names))
   for f in m.REQUIRED_APP_FILES:self.assertIn(self.out.name+'/app/'+f,names)
   self.assertIn(self.out.name+'/Create PCA Desktop Icon.cmd',names)
   for n in names:self.assertNotIn('/Users/',z.read(n).decode())
  self.assertEqual(before,{p.relative_to(self.old).as_posix():m.digest(p) for p in self.old.rglob('*') if p.is_file()})
  report=json.loads((self.out/'UPDATE-DETAILS.json').read_text());self.assertFalse(report['runtime_included'])
  self.assertEqual(report['required_baseline_version'],'1.14.1')
 def test_multiple_baselines_are_proven_before_claiming_compatibility(self):
  newer=self.fixture('newer-baseline','1.14.2')
  m.build(self.new,newer,self.out,[self.old])
  report=json.loads((self.out/'UPDATE-DETAILS.json').read_text())
  self.assertEqual(report['compatible_baseline_versions'],['1.14.1','1.14.2'])
  self.assertEqual(report['required_baseline_version'],'1.14.2')
  self.assertEqual(set(report['baseline_manifests_sha256']),{'1.14.1','1.14.2'})
 def test_additional_incompatible_runtime_rejected_before_output(self):
  extra=self.fixture('extra-baseline','1.14.2')
  p=extra/'BUILD-DETAILS.json';data=json.loads(p.read_text());data['wheels']['bad']='changed';p.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'Runtime'):m.build(self.new,self.old,self.out,[extra])
  self.assertFalse(self.out.exists())
 def test_changed_runtime_rejected_before_creating_output(self):
  for key in ('python_archive','wheels'):
   with self.subTest(key=key):
    path=self.old/'BUILD-DETAILS.json';original=path.read_text();data=json.loads(original);data[key]['extra']='changed';path.write_text(json.dumps(data))
    with self.assertRaisesRegex(ValueError,'Runtime'):m.build(self.new,self.old,self.out)
    self.assertFalse(self.out.exists());path.write_text(original)
 def test_wrong_baseline_and_modified_source_rejected(self):
  path=self.old/'BUILD-DETAILS.json';original=path.read_text();data=json.loads(original);data['source_version']='1.12.0';path.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'baseline'):m.build(self.new,self.old,self.out)
  path.write_text(original);(self.new/'app/desktop_bootstrap.py').write_text('# changed after full build\n')
  with self.assertRaisesRegex(ValueError,'source differs'):m.build(self.new,self.old,self.out)
  self.assertFalse(self.out.exists())
 def test_existing_output_or_archive_never_overwritten(self):
  self.out.mkdir();marker=self.out/'keep.txt';marker.write_text('keep')
  with self.assertRaises(FileExistsError):m.build(self.new,self.old,self.out)
  self.assertEqual(marker.read_text(),'keep');marker.unlink();self.out.rmdir()
  archive=self.out.with_name(self.out.name+'.zip');archive.write_bytes(b'keep')
  with self.assertRaises(FileExistsError):m.build(self.new,self.old,self.out)
  self.assertEqual(archive.read_bytes(),b'keep')

if __name__=='__main__':unittest.main(verbosity=2)
