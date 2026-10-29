"""Actual Django bundle CLI versus Go packaged-check command; synthetic files only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'BE')]
from TESTS.adapters.django.production_runtime import require_production_python

CASES={
 'optional-absent':True,'required-absent':False,'optional-placeholder':True,'required-placeholder':False,
 'valid':True,'optional-invalid':False,'wrong-revision':False,'wrong-release':False,'wrong-index-hash':False,
 'missing-image':False,'wrong-image-hash':False,'wrong-image-header':False,'image-not-png':False,
 'image-escape':False,'image-symlink-escape':False,'private-chunk-image-still-required':False,
 'no-screenshots':True,'empty-chunks':False,'empty-root-nonempty-cwd':False,
}
def prepare_fixtures(root):
 from application.manuale.index import canonical,digest
 fixtures=[];observed={}
 for name,expected in CASES.items():
  bundle=root/name/'release-manual';required=name not in {'optional-absent','optional-placeholder','optional-invalid','empty-root-nonempty-cwd'}
  revision='revision-fixture';release='release-fixture'
  if name.endswith('placeholder'):
   bundle.mkdir(parents=True);(bundle/'.gitkeep').touch()
  elif not name.endswith('absent'):
   bundle.mkdir(parents=True)
   png=b'\x89PNG\r\n\x1a\n'+(13).to_bytes(4,'big')+b'IHDR'+(1).to_bytes(4,'big')*2
   if name=='wrong-image-header':png=b'x'*24
   image=bundle/'image.png';image.write_bytes(png)
   relative='image.png'
   if name=='image-not-png':relative='image.jpg';image.rename(bundle/relative)
   if name=='image-escape':relative='../outside.png';(bundle.parent/'outside.png').write_bytes(png)
   if name=='image-symlink-escape':
    outside=bundle.parent/'outside.png';outside.write_bytes(png);image.unlink();image.symlink_to('../outside.png')
   value=dict(format=1,embedding='italian-tfidf-lsa-v5',dependencies={},
    chunks=[{'id':'fixture','evidence':[],'runtime_dependencies':{},'audience':'public',
     'screenshots':[{'path':relative,'sha256':hashlib.sha256(png).hexdigest()}]}],
    metadata=dict(application_revision=revision,release=release,publication_status='release-bundled',
     code_state='committed',manual_revision='c'*40,manual_state='generated-from-committed',verification={'corpus_identity':'d'*64}))
   if name=='empty-chunks':value['chunks']=[]
   if name=='no-screenshots':value['chunks'][0]['screenshots']=[]
   if name=='private-chunk-image-still-required':value['chunks'][0]['audience']='maintainer'
   if name in ('missing-image','private-chunk-image-still-required'):image.unlink()
   if name=='wrong-image-hash':image.write_bytes(png+b'changed')
   value['identity']=digest(canonical(value))
   index=bundle/'index.json';index.write_text(json.dumps(value,ensure_ascii=False))
   receipt=dict(format=1,distribution='immutable-backend-image',index_sha256=hashlib.sha256(index.read_bytes()).hexdigest(),
    corpus_identity=value['identity'],application_revision=revision,release=release,manual_revision='c'*40,verified_corpus_identity='d'*64)
   if name=='wrong-index-hash':receipt['index_sha256']='0'*64
   if name=='optional-invalid':receipt={}
   (bundle/'bundle.json').write_text(json.dumps(receipt))
  if name=='wrong-revision':revision='other'
  if name=='wrong-release':release='other'
  args=['--root','' if name=='empty-root-nonempty-cwd' else str(bundle),'--revision',revision,'--release',release,'--required',str(required).lower()]
  command=subprocess.run([sys.executable,'-m','application.manuale.bundle',*args],cwd=root,env={**os.environ,'PYTHONPATH':str(ROOT/'BE')},capture_output=True,text=True,timeout=15)
  assert (command.returncode==0)==expected,(name,command.returncode,command.stderr)
  observed[name]={'success':command.returncode==0};fixtures.append((name,expected,args))
 return fixtures,observed

def main():
 require_production_python()
 output=ROOT/'quality-reports/go-migration/manual-bundle';output.mkdir(parents=True,exist_ok=True)
 report={'success':False};(output/'results.json').write_text(json.dumps(report))
 with tempfile.TemporaryDirectory(prefix='az-manual-bundle-') as directory:
  root=Path(directory)
  fixtures,observed=prepare_fixtures(root)
  (output/'django.json').write_text(json.dumps(observed,indent=2)+'\n')
  binary=root/'check-manual'
  build=subprocess.run(['go','build','-race','-o',str(binary),'./cmd/check-manual'],cwd=ROOT/'BE_GO',capture_output=True,text=True,timeout=120)
  print(build.stdout,build.stderr);build.check_returncode()
  results={}
  for name,expected,args in fixtures:
   command=subprocess.run([str(binary),*args],cwd=root,capture_output=True,text=True,timeout=15)
   assert (command.returncode==0)==expected,(name,command.returncode,command.stderr)
   assert str(root) not in command.stdout+command.stderr,'private path leaked'
   results[name]={'success':command.returncode==0}
  assert set(results)==set(CASES)
  report.update(cases=len(CASES),observations=results,race=True)
 report.update(success=True,private_directory_removed=not root.exists())
 (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
