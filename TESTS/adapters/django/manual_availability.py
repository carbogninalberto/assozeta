"""Offline Django availability oracle and Go differential; no DB, provider or customer files."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'BE')]
from TESTS.adapters.django.production_runtime import require_production_python

CASES = {
    'absent': False, 'remote-only': True, 'remote-whitespace': False,
    'required-missing-precedes-remote': False, 'placeholder-permits-remote': True,
    'invalid-bundle-precedes-remote': False, 'bundle-valid': True,
    'bundle-revision-mismatch': False, 'bundle-release-mismatch': False,
    'bundle-index-hash-mismatch': False, 'bundle-corpus-mismatch': False,
    'bundle-verification-mismatch': False, 'bundle-manual-revision-invalid': False,
    'bundle-publication-invalid': False, 'bundle-empty-chunks': False,
    'bundle-assets-not-an-availability-gate': True, 'bundle-private-chunk-still-available': True,
    'index-invalid-seal': False, 'index-wrong-format': False, 'index-wrong-embedding': False,
    'index-conflicting-evidence': False, 'index-conflicting-runtime-dependency': False,
    'runtime-valid': True, 'runtime-empty-chunks': True, 'runtime-unpublished': False,
    'runtime-uncommitted': False, 'runtime-explicit-run': True, 'runtime-wrong-revision': False,
    'runtime-missing-revision': False, 'runtime-no-source-rehash': True,
    'runtime-numeric-unicode-seal': True, 'runtime-resealed-edit': True,
    'runtime-invalid-json': False, 'runtime-invalid-utf8': False,
    'preview-valid': True, 'preview-invalid-hash': False, 'preview-production-inert': False,
    'preview-bound-revision-inert': False, 'preview-run-id-inert': False,
    'development-valid': True, 'development-missing-source': False,
    'development-deleted-reappeared': False, 'development-deleted-absent': True,
    'development-invalid-pointer': False, 'development-invalid-provenance': False,
    'development-mismatched-package': False, 'bundle-path-escape': False,
}


def main():
    require_production_python()
    from django.conf import settings
    settings.configure()
    from django.test import override_settings
    from application.manuale.availability import manual_available
    from application.manuale.index import canonical, digest
    output = ROOT / 'quality-reports/go-migration/manual-availability'
    output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'scope': 'offline availability only; no reader, asset validity or release delivery claim'}
    (output / 'results.json').write_text(json.dumps(report)+'\n')
    with tempfile.TemporaryDirectory(prefix='az-manual-availability-') as directory:
        root = Path(directory)
        fixtures = []
        for name, expected in CASES.items():
            base = root / name
            base.mkdir()
            config = dict(BundlePath=str(base/'release-manual'), BundleRequired=False,
                IndexPath=str(base/'runtime.json'), CorpusBaseURL='', DeploymentMode='production',
                ApplicationRevision='revision-fixture', Release='v1-fixture', RunID='',
                SourceRoot=str(base/'source'), DevelopmentRoot=str(base/'development'))
            value = dict(format=1, embedding='italian-tfidf-lsa-v5', dependencies={'BE/example.py':'a'*64},
                chunks=[{'id':'fixture', 'audience':'public', 'status':'verified', 'evidence':[],
                         'runtime_dependencies':{}, 'screenshots':[{'path':'missing.png','sha256':'b'*64}]}],
                metadata=dict(application_revision=config['ApplicationRevision'], release=config['Release'],
                    publication_status='published', code_state='committed', manual_revision='c'*40,
                    manual_state='generated-from-committed', verification={'corpus_identity':'d'*64}),
                semantics={'vectors':[[0.1, -0.0, 1e-9, 1e16]]})
            def seal(item):
                item = copy.deepcopy(item)
                item.pop('identity',None)
                item['identity']=digest(canonical(item))
                return item
            def write(path,item):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(item,ensure_ascii=False))
            if name in ('remote-only','required-missing-precedes-remote','placeholder-permits-remote','invalid-bundle-precedes-remote'):
                config['CorpusBaseURL']='https://private.invalid/manual'
            if name=='remote-whitespace': config['CorpusBaseURL']=' \t\n'
            if name=='required-missing-precedes-remote': config['BundleRequired']=True
            if name=='placeholder-permits-remote':
                Path(config['BundlePath']).mkdir(); (Path(config['BundlePath'])/'.gitkeep').touch()
            if name=='invalid-bundle-precedes-remote':
                write(Path(config['BundlePath'])/'bundle.json',{})
            if name.startswith('bundle-') and name not in ('bundle-path-escape',):
                value['metadata']['publication_status']='release-bundled'
                if name=='bundle-empty-chunks': value['chunks']=[]
                if name=='bundle-private-chunk-still-available': value['chunks'][0]['audience']='maintainer'
                if name=='bundle-publication-invalid': value['metadata']['publication_status']='draft'
                if name=='bundle-manual-revision-invalid': value['metadata']['manual_revision']='invalid'
                value=seal(value); path=Path(config['BundlePath'])/'index.json';write(path,value)
                receipt=dict(format=1,distribution='immutable-backend-image',index_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    corpus_identity=value['identity'],application_revision=config['ApplicationRevision'],release=config['Release'],
                    manual_revision=value['metadata']['manual_revision'],verified_corpus_identity='d'*64)
                changes={'bundle-revision-mismatch':('application_revision','other'), 'bundle-release-mismatch':('release','other'),
                    'bundle-index-hash-mismatch':('index_sha256','0'*64), 'bundle-corpus-mismatch':('corpus_identity','0'*64),
                    'bundle-verification-mismatch':('verified_corpus_identity','0'*64)}
                if name in changes: key,val=changes[name];receipt[key]=val
                write(Path(config['BundlePath'])/'bundle.json',receipt)
            if name.startswith(('runtime-','index-')):
                if name=='runtime-unpublished': value['metadata']['publication_status']='draft'
                if name=='runtime-uncommitted': value['metadata']['code_state']='dirty'
                if name=='runtime-explicit-run':
                    value['metadata'].update(publication_status='draft',code_state='dirty');config['RunID']='fixture-run'
                if name=='runtime-empty-chunks': value['chunks']=[]
                if name=='runtime-wrong-revision': config['ApplicationRevision']='other'
                if name=='runtime-missing-revision': config['ApplicationRevision']=''
                if name=='runtime-numeric-unicode-seal':
                    value['extra']={'è😀\u2028': [10**40,-0.0,1.0,1e-4,1e-5,1e15,1e16,1.2345678901234567]}
                if name=='index-wrong-format':value['format']=2
                if name=='index-wrong-embedding':value['embedding']='other'
                if name=='index-conflicting-evidence':value['chunks'][0]['evidence']=[{'path':'BE/example.py','sha256':'b'*64}]
                if name=='index-conflicting-runtime-dependency':value['chunks'][0]['runtime_dependencies']={'BE/example.py':'b'*64}
                value=seal(value)
                if name=='index-invalid-seal':value['identity']='0'*64
                write(Path(config['IndexPath']),value)
                if name=='runtime-invalid-json':Path(config['IndexPath']).write_text('{')
                if name=='runtime-invalid-utf8':Path(config['IndexPath']).write_bytes(b'\xff')
            if name.startswith(('preview-','development-')):
                config.update(DeploymentMode='development',ApplicationRevision='')
                if name.startswith('preview-'):
                    contents=json.dumps({'sections':[]}).encode();identity=hashlib.sha256(contents).hexdigest()
                    preview=Path(config['DevelopmentRoot'])/'preview'
                    target=preview/identity/'index.json';target.parent.mkdir(parents=True);target.write_bytes(contents)
                    write(preview/'active.json',{'identity':identity})
                    if name=='preview-invalid-hash':target.write_text('{}')
                    if name=='preview-production-inert':config['DeploymentMode']='production'
                    if name=='preview-bound-revision-inert':config['ApplicationRevision']='revision-fixture'
                    if name=='preview-run-id-inert':config['RunID']='fixture-run'
                else:
                    value['metadata'].update(publication_status='development-local',verified_corpus_identity='e'*64)
                    if name=='development-invalid-provenance':value['metadata']['publication_status']='draft'
                    if name in ('development-deleted-reappeared','development-deleted-absent'):
                        value['metadata']['development_deleted_files']=['BE/removed.py']
                    if name=='development-deleted-reappeared':write(Path(config['SourceRoot'])/'BE/removed.py',{})
                    if name=='development-missing-source':config['SourceRoot']=''
                    value=seal(value); identity=value['identity']
                    write(Path(config['DevelopmentRoot'])/'packages'/identity/'index.json',value)
                    write(Path(config['DevelopmentRoot'])/'active.json',{'format':2 if name=='development-invalid-pointer' else 1,'corpus_identity':identity})
                    if name=='development-mismatched-package':
                        value['changed']=True;write(Path(config['DevelopmentRoot'])/'packages'/identity/'index.json',seal(value))
            if name=='bundle-path-escape':
                path=Path(config['BundlePath']);path.mkdir();outside=base/'outside.json';write(outside,seal(value));(path/'index.json').symlink_to(outside)
                write(path/'bundle.json',{})
            kwargs={
                'MANUAL_BUNDLE_PATH':config['BundlePath'],'MANUAL_BUNDLE_REQUIRED':config['BundleRequired'],
                'MANUAL_INDEX_PATH':config['IndexPath'],'MANUAL_CORPUS_BASE_URL':config['CorpusBaseURL'],
                'ASSOZETA_DEPLOYMENT_MODE':config['DeploymentMode'],'MANUAL_APPLICATION_REVISION':config['ApplicationRevision'],
                'RUNNING_VERSION':config['Release'],'MANUAL_RUN_ID':config['RunID'],
                'MANUAL_SOURCE_ROOT':config['SourceRoot'],'MANUAL_DEVELOPMENT_ROOT':config['DevelopmentRoot'],
            }
            with override_settings(**kwargs),patch.object(socket,'create_connection',side_effect=AssertionError('availability attempted network')):
                actual=manual_available()
                if name=='runtime-resealed-edit':
                    value['metadata']['code_state']='dirty';write(Path(config['IndexPath']),seal(value))
                    assert manual_available() is False, 'runtime cache ignored file replacement'
                    value['metadata']['code_state']='committed';write(Path(config['IndexPath']),seal(value))
                    assert manual_available() is True
            assert actual is expected,(name,actual,expected)
            fixtures.append({'Name':name,'Expected':expected,'Settings':config})
        fixture=root/'cases.json';fixture.write_text(json.dumps(fixtures))
        (output/'django.json').write_text(json.dumps({'success':True,'cases':{f['Name']:f['Expected'] for f in fixtures}},indent=2)+'\n')
        completed=subprocess.run(['go','test','-race','-count=1','-tags=manualavailabilityintegration','./pkg/manual','-run','^TestManualAvailabilityOracle$','-json'],cwd=ROOT/'BE_GO',
            env={**os.environ,'ASSOZETA_MANUAL_AVAILABILITY_FIXTURE':str(fixture)},capture_output=True,text=True,timeout=120)
        (output/'go.jsonl').write_text(completed.stdout)
        print(completed.stdout);print(completed.stderr)
        completed.check_returncode()
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        expected={'TestManualAvailabilityOracle/'+name for name in CASES}
        passed={e.get('Test') for e in events if e.get('Action')=='pass' and '/' in e.get('Test','')}
        assert passed==expected and not any(e.get('Action')=='skip' for e in events),'missing or skipped availability cases'
        report.update(cases=len(CASES),race=True,network_calls=0)
    report.update(success=True,private_directory_removed=not root.exists())
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
