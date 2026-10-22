"""Offline actual Django installed-bundle applicability and asset-core oracle.

The exact production manual_asset function body is compiled without its HTTP
wrappers; its _load boundary supplies the genuine loaded bundle/context. This
proves no authentication, public HTTP, search, preview, development or sync behavior.
No database, cache service, provider, Docker or existing manual package is used.
"""
import ast
import base64
import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'BE')]
from TESTS.adapters.django.production_runtime import require_production_python

PNG=b'\x89PNG\r\n\x1a\n'+(13).to_bytes(4,'big')+b'IHDR'+(1).to_bytes(4,'big')*2
BYTES={'base':PNG+b'z','host':PNG+b'host','non_png':b'verified bytes, deliberately not a PNG'}

def fingerprint(root):
 return {str(p.relative_to(root)):('symlink:'+os.readlink(p) if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest())
         for p in sorted(root.rglob('*')) if p.is_symlink() or p.is_file()}

def prepare(base,spec):
 from application.manuale.index import canonical,digest
 base.mkdir();bundle=base/'bundle';bundle.mkdir();source=base/'source';source.mkdir()
 for name in ['z.py','a.py','private.py','draft.py','host.py','runtime.py','core/authentication.py']:
  path=source/'BE'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original '+name)
 tool=source/'docs/tool.py';tool.parent.mkdir();tool.write_text('original tooling')
 def sha(relative):return digest((source/relative).read_bytes())
 chunks=[]
 for ident,audience,status,features,file in [('public-z','public','verified',[],'z'),('public-a','public','verified',[],'a'),('private','maintainer','verified',[],'private'),('draft','public','pending',[],'draft'),('host','public','verified',['self_hosted'],'host')]:
  path=bundle/'images'/f'{file}.png';path.parent.mkdir(exist_ok=True);data=BYTES['base'] if file=='z' else PNG+file.encode();path.write_bytes(data)
  chunks.append(dict(id=ident,audience=audience,status=status,features=features,evidence=[dict(path=f'BE/{file}.py',sha256=sha(f'BE/{file}.py'))],runtime_dependencies={},screenshots=[dict(path=f'images/{file}.png',sha256=digest(data))]))
 chunks[0]['runtime_dependencies']={'BE/runtime.py':sha('BE/runtime.py')}
 value=dict(format=1,embedding='italian-tfidf-lsa-v5',dependencies={'BE/core/authentication.py':sha('BE/core/authentication.py'),'docs/tool.py':sha('docs/tool.py')},chunks=chunks,
  metadata=dict(application_revision='revision-fixture',release='release-fixture',publication_status='release-bundled',code_state='committed',manual_revision='c'*40,manual_state='generated-from-committed',verification={'corpus_identity':'d'*64}))
 mutation=spec['mutation'];image=bundle/'images/z.png';record=chunks[0]['screenshots'][0]
 for label,relative in [('source-z-changed','BE/z.py'),('runtime-changed','BE/runtime.py'),('shared-changed','BE/core/authentication.py'),('tooling-changed','docs/tool.py'),('private-source-changed','BE/private.py')]:
  if mutation==label:(source/relative).write_text('changed')
 if mutation=='source-z-missing':(source/'BE/z.py').unlink()
 if mutation=='source-escape':chunks[0]['evidence'][0]['path']='../outside.py'
 if mutation=='conflicting-binding':chunks[0]['runtime_dependencies']['BE/a.py']='0'*64
 if mutation=='empty-chunks':value['chunks']=[]
 if mutation=='missing-evidence':del chunks[0]['evidence']
 if mutation=='tampered-asset':image.write_bytes(b'tampered')
 if mutation=='missing-asset':image.unlink()
 if mutation=='directory-asset':image.unlink();image.mkdir()
 if mutation in ['traversal-asset','absolute-asset','outside-symlink']:
  outside=base/'outside.png';outside.write_bytes(BYTES['base'])
  if mutation=='traversal-asset':record['path']='../outside.png'
  elif mutation=='absolute-asset':record['path']=str(outside);spec=copy.deepcopy(spec);spec['asset_path']=str(outside)
  else:image.unlink();image.symlink_to(outside)
 if mutation in ['inside-png-symlink','inside-text-symlink']:
  target=bundle/('real.png' if mutation=='inside-png-symlink' else 'real.txt');target.write_bytes(BYTES['base']);image.unlink();image.symlink_to(target)
 if mutation=='uppercase':image.rename(bundle/'images/z.PNG');record['path']='images/z.PNG'
 if mutation=='non-png':image.write_bytes(BYTES['non_png']);record['sha256']=digest(BYTES['non_png'])
 if mutation=='duplicate-valid-first':chunks[1]['screenshots'].append(dict(path='images/z.png',sha256='0'*64))
 if mutation=='first-duplicate-mismatch':record['sha256']='0'*64;chunks[1]['screenshots'].append(dict(path='images/z.png',sha256=digest(BYTES['base'])))
 value['identity']=digest(canonical(value));index=bundle/'index.json';index.write_text(json.dumps(value,ensure_ascii=False))
 receipt=dict(format=1,distribution='immutable-backend-image',index_sha256=digest(index.read_bytes()),corpus_identity=value['identity'],application_revision='revision-fixture',release='release-fixture',manual_revision='c'*40,verified_corpus_identity='d'*64)
 if mutation=='bad-receipt':receipt['index_sha256']='0'*64
 (bundle/'bundle.json').write_text(json.dumps(receipt))
 if mutation=='bad-seal':value['identity']='0'*64;index.write_text(json.dumps(value))
 if mutation=='missing-bundle':bundle=base/'absent'
 settings=dict(BundlePath=str(bundle),BundleRequired=True,SourceRoot=str(source) if spec['source'] else '',ApplicationRevision='other' if mutation=='wrong-revision' else 'revision-fixture',Release='other' if mutation=='wrong-release' else 'release-fixture')
 return dict(name=spec['name'],settings=settings,features=spec['features'],asset_path=spec['asset_path'],expected=spec['expected'])

def asset_core():
 from django.http import FileResponse,Http404
 from application.manuale.index import digest,owned_path
 source=ROOT/'BE/application/manuale/views.py';nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='manual_asset'];assert len(nodes)==1;node=nodes[0]
 node.decorator_list=[]
 def unexpected_preview():raise AssertionError('installed-bundle oracle entered preview branch')
 namespace=dict(BytesIO=BytesIO,FileResponse=FileResponse,Http404=Http404,digest=digest,owned_path=owned_path,load_preview=unexpected_preview)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
 return namespace

def observe(tc,namespace):
 from django.http import Http404
 from application.manuale.bundle import load_bundle
 s=tc['settings'];context=dict(revision=s['ApplicationRevision'],release=s['Release'],features=tc['features'],maintainer=False,code_root=s['SourceRoot'] or None)
 try:
  index=load_bundle(s['BundlePath'],revision=context['revision'],release=context['release'],required=True)
  applicable=index.applicable(**context)
 except (OSError,ValueError,KeyError,TypeError):return dict(unavailable=True,ids=[],asset='unavailable')
 result=dict(unavailable=False,ids=[c['id'] for c in applicable])
 namespace['_load']=lambda _: (index,context)
 try:
  response=namespace['manual_asset'](None,tc['asset_path']);contents=b''.join(response.streaming_content);response.close()
  result['asset']=next((key for key,value in BYTES.items() if value==contents),'unexpected_bytes')
  assert response['Content-Type']=='image/png' and response['Cache-Control']=='private, no-store' and response['X-Content-Type-Options']=='nosniff'
 except Http404:result['asset']='not_found'
 return result

def main():
 require_production_python()
 from django.conf import settings
 settings.configure()
 fixture=json.loads((ROOT/'TESTS/fixtures/manual-reader-library.json').read_text());output=ROOT/'quality-reports/go-migration/manual-reader';output.mkdir(parents=True,exist_ok=True)
 report={'success':False,'scope':__doc__};(output/'results.json').write_text(json.dumps(report))
 with tempfile.TemporaryDirectory(prefix='az-manual-reader-') as directory:
  root=Path(directory);cases=[prepare(root/spec['name'],spec) for spec in fixture['cases']];namespace=asset_core();before=fingerprint(root);observed={}
  with patch.object(socket,'socket',side_effect=AssertionError('offline oracle attempted network')):
   for tc in cases:
    result=observe(tc,namespace);assert result==tc['expected'],(tc['name'],result,tc['expected']);observed[tc['name']]=result
  assert before==fingerprint(root),'reader modified fixture files'
  (output/'django.json').write_text(json.dumps(dict(success=True,cases=observed,file_effects='unchanged',network='denied'),indent=2)+'\n')
  manifest=root/'fixtures.json';manifest.write_text(json.dumps(dict(cases=cases,bytes={k:base64.b64encode(v).decode() for k,v in BYTES.items()})))
  result=subprocess.run(['go','test','-race','-tags','manualreaderintegration','./pkg/manual','-run','^TestManualReader','-count=1','-v'],cwd=ROOT/'BE_GO',env={**os.environ,'ASSOZETA_MANUAL_READER_FIXTURE':str(manifest)},capture_output=True,text=True,timeout=180)
  (output/'go.log').write_text(result.stdout+result.stderr);print(result.stdout,result.stderr);result.check_returncode()
  assert before=={k:v for k,v in fingerprint(root).items() if k!='fixtures.json'},'Go reader modified fixture files'
 report.update(success=True,cases=len(cases),private_directory_removed=not root.exists(),file_effects='unchanged',network='Django denied; Go library has no network client')
 (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
