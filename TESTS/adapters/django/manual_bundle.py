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
# A release may carry the manual verified for an earlier release: the bundle's
# parent directory names that origin in MANUAL_CARRIED_FROM ("<release> <revision>").
# Each case: (expected success, marker bytes or kind, package verified for, extra).
ORIGIN_REVISION='e'*40;ORIGIN_RELEASE='release-origin'
ORIGIN=f'{ORIGIN_RELEASE} {ORIGIN_REVISION}'.encode()
CARRIED={
 'carried-valid':(True,ORIGIN+b'\n','origin',{}),
 'carried-extra-whitespace':(True,b'  release-origin \t '+ORIGIN_REVISION.encode()+b'\r\n\n','origin',{}),
 'carried-unit-separator':(True,b'release-origin\x1f'+ORIGIN_REVISION.encode(),'origin',{}),
 'carried-nbsp-separator':(True,'release-origin\u00a0'.encode()+ORIGIN_REVISION.encode(),'origin',{}),
 'carried-root-trailing-slash':(True,ORIGIN,'origin',{'trailing_slash':True}),
 'carried-symlink-marker':(True,ORIGIN,'origin',{'symlink':True}),
 'carried-running-is-origin':(True,ORIGIN,'origin',{'running_is_origin':True}),
 'carried-optional-placeholder':(True,ORIGIN,'placeholder',{'optional':True}),
 'carried-required-placeholder':(False,ORIGIN,'placeholder',{}),
 'carried-invalid-optional-absent':(True,b'malformed','absent',{'optional':True}),
 'carried-invalid-optional-placeholder':(False,b'malformed','placeholder',{'optional':True}),
 'carried-invalid-current-package':(False,b'malformed','running',{}),
 'carried-blank-marker-current':(True,b' \n\t','running',{}),
 'carried-blank-marker-origin':(False,b' \n\t','origin',{}),
 'carried-directory-marker-current':(True,'directory','running',{}),
 'carried-broken-symlink-current':(True,'broken-symlink','running',{}),
 'carried-one-field':(False,b'release-origin','origin',{}),
 'carried-three-fields':(False,ORIGIN+b' extra','origin',{}),
 'carried-uppercase-revision':(False,b'release-origin '+ORIGIN_REVISION.upper().encode(),'origin',{}),
 'carried-short-revision':(False,b'release-origin '+ORIGIN_REVISION[:39].encode(),'origin',{}),
 'carried-invalid-utf8':(False,b'\xff '+ORIGIN_REVISION.encode(),'origin',{}),
 'carried-byte-order-mark':(False,'\ufeff'.encode()+ORIGIN,'origin',{}),
 'carried-other-release':(False,b'release-other '+ORIGIN_REVISION.encode(),'origin',{}),
 'carried-other-revision':(False,b'release-origin '+b'f'*40,'origin',{}),
 'carried-current-package-named-origin':(False,ORIGIN,'running',{}),
 'carried-wrong-image-hash':(False,ORIGIN,'origin',{'wrong_image_hash':True}),
}
CASES.update({name:spec[0] for name,spec in CARRIED.items()})
def prepare_fixtures(root):
 from application.manuale.index import canonical,digest
 fixtures=[];observed={}
 for name,expected in CASES.items():
  if name in CARRIED:
   args=prepare_carried(root,name,*CARRIED[name][1:])
   command=subprocess.run([sys.executable,'-m','application.manuale.bundle',*args],cwd=root,env={**os.environ,'PYTHONPATH':str(ROOT/'BE')},capture_output=True,text=True,timeout=15)
   assert (command.returncode==0)==expected,(name,command.returncode,command.stderr)
   observed[name]={'success':command.returncode==0};fixtures.append((name,expected,args))
   continue
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

def prepare_carried(root,name,marker,verified_for,extra):
 from application.manuale.index import canonical,digest
 base=root/name;bundle=base/'release-manual';base.mkdir()
 revision,release='revision-fixture','release-fixture'
 if extra.get('running_is_origin'):revision,release=ORIGIN_REVISION,ORIGIN_RELEASE
 if verified_for=='placeholder':
  bundle.mkdir();(bundle/'.gitkeep').touch()
 elif verified_for!='absent':
  package_revision,package_release=(ORIGIN_REVISION,ORIGIN_RELEASE) if verified_for=='origin' else (revision,release)
  bundle.mkdir()
  png=b'\x89PNG\r\n\x1a\n'+(13).to_bytes(4,'big')+b'IHDR'+(1).to_bytes(4,'big')*2
  (bundle/'image.png').write_bytes(png+b'changed' if extra.get('wrong_image_hash') else png)
  value=dict(format=1,embedding='italian-tfidf-lsa-v5',dependencies={},
   chunks=[{'id':'fixture','evidence':[],'runtime_dependencies':{},'audience':'public',
    'screenshots':[{'path':'image.png','sha256':hashlib.sha256(png).hexdigest()}]}],
   metadata=dict(application_revision=package_revision,release=package_release,publication_status='release-bundled',
    code_state='committed',manual_revision='c'*40,manual_state='generated-from-committed',verification={'corpus_identity':'d'*64}))
  value['identity']=digest(canonical(value))
  index=bundle/'index.json';index.write_text(json.dumps(value,ensure_ascii=False))
  (bundle/'bundle.json').write_text(json.dumps(dict(format=1,distribution='immutable-backend-image',
   index_sha256=hashlib.sha256(index.read_bytes()).hexdigest(),corpus_identity=value['identity'],
   application_revision=package_revision,release=package_release,manual_revision='c'*40,verified_corpus_identity='d'*64)))
 path=base/'MANUAL_CARRIED_FROM'
 if marker=='directory':path.mkdir()
 elif marker=='broken-symlink':path.symlink_to('missing-origin')
 elif extra.get('symlink'):(base/'origin.txt').write_bytes(marker);path.symlink_to('origin.txt')
 else:path.write_bytes(marker)
 required='false' if extra.get('optional') else 'true'
 return ['--root',str(bundle)+('/' if extra.get('trailing_slash') else ''),'--revision',revision,'--release',release,'--required',required]

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
