"""Source-bound current-schema collaborator HTTP acceptance on a Linux image.

Reuses the authored Django oracle and its private real PostgreSQL/Redis/Caddy
runtime. Native repository cancellation/query groups remain a separate gate;
this is not installed lifecycle, historical-schema or operator-selection proof.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCES = (
    'TESTS/adapters/go/payment_category_image.py', 'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/go/impersonation_management_image.py',
    'TESTS/adapters/django/collaborator_list.py', 'TESTS/contracts/test_collaborator_list.py',
    'TESTS/contracts/test_impersonation_management.py', 'TESTS/adapters/django/production_runtime.py',
    'TESTS/adapters/django/accounts.py', 'TESTS/adapters/django/settings.py',
    'TESTS/adapters/django/payment_info_stats.py', 'TESTS/adapters/caddy.py',
    'TESTS/http_client.py', 'selfhost/caddy/Caddyfile', 'UI/Dockerfile',
)


def source_hashes(source):
    paths = set(SOURCES)
    paths.update(str(path.relative_to(source)) for path in (source/'BE').rglob('*.py'))
    return {'runner':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            **{path:hashlib.sha256((source/path).read_bytes()).hexdigest() for path in sorted(paths)}}


def go_source_hashes(source):
    from TESTS.adapters.go.impersonation_management_image import go_source_hashes as snapshot
    return snapshot(source)


def verify_build_receipt(build, source, image_id, revision):
    assert isinstance(build,dict)
    assert build['success'] is True and build['source_stable'] is True
    assert re.fullmatch(r'sha256:[0-9a-f]{64}',image_id)
    assert re.fullmatch(r'[0-9a-f]{40}',revision)
    assert build['image_id']==image_id and build['source_revision']==revision
    assert build['source_sha256'] and build['source_sha256']==go_source_hashes(source)



def safe_status_failure(error):
    if not isinstance(error,AssertionError):return None
    match=re.fullmatch(r'(\d{3}) != (\d{3}) : (collaborator-list-[a-zA-Z0-9_-]+)',str(error))
    if match:return {'actual_status':int(match[1]),'expected_status':int(match[2]),'case':match[3]}
    return None

def assert_workloads(result, image_id, case_count, required_names):
    for key in ('success','source_stable','cleanup_verified','proxy_cleanup_verified'):
        assert result[key] is True, key
    assert result['cleanup_errors']==[] and result['managed_models']==128
    assert result['repository_required'] is False and result['repository_tests']==[]
    assert result['repository_execution']=='not_run_required_separately'
    assert result['go_runtime']['immutable_id']==image_id
    assert type(result['http_cases']) is int and result['http_cases']==case_count
    workloads=result['workloads']
    assert len(workloads)==4
    indexed={row['backend']:row for row in workloads}
    assert set(indexed)=={'django','go','v1','v2'}
    names=[row['name'] for row in indexed['django']['cases']]
    assert len(names)==len(set(names))==case_count and set(required_names)<=set(names)
    for backend,workload in indexed.items():
        assert [row['name'] for row in workload['cases']]==names
        for row in workload['cases']:
            assert row['managed_state_unchanged'] is True and row['cache_unchanged'] is True
            expected=[{'case':row['name'],'status':row['status']}] if backend in ('django','v1') else []
            assert row['django_requests']==expected
    fallback=result['fallback_negative_control']
    assert fallback['rejected'] is True and fallback['reason']=='upstream_mismatch' and fallback['status']==200
    assert fallback['managed_state_unchanged'] is True and fallback['cache_unchanged'] is True
    assert fallback['django_requests']==[{'case':names[0],'status':200}]
    evidence=result['database_write_evidence']
    for key in ('success','relation_identities_unchanged','statistics_reset_unchanged','database_unchanged'):
        assert evidence[key] is True,key
    assert evidence['user_table_changes']==[]
    assert all(type(value) is int and value==0 for value in evidence['user_totals_delta'].values())
    assert set(evidence['user_totals_delta'])=={'inserted','updated','deleted'}



class CollaboratorImageAcceptanceTests(unittest.TestCase):
    def test_failure_receipt_exposes_only_status_and_authored_case(self):
        self.assertEqual(safe_status_failure(AssertionError('404 != 200 : collaborator-list-owner-list')),
            {'actual_status':404,'expected_status':200,'case':'collaborator-list-owner-list'})
        for error in (AssertionError('private-token-or-command'),RuntimeError('404 != 200 : collaborator-list-owner-list'),
                AssertionError('404 != 200 : collaborator-list-owner-list private-token')):
            self.assertIsNone(safe_status_failure(error))
    def test_build_receipt_binds_exact_source_identity_and_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory);(source/'BE_GO').mkdir()
            (source/'BE_GO/go.mod').write_text('module private.test/fixture\n')
            image='sha256:'+'a'*64;revision='b'*40
            build={'success':True,'source_stable':True,'source_revision':revision,
                'image_id':image,'source_sha256':go_source_hashes(source)}
            verify_build_receipt(build,source,image,revision)
            for key,value in [('success',False),('success',1),('source_stable',False),
                    ('source_revision','c'*40),('image_id','sha256:'+'c'*64),('source_sha256',{})]:
                altered=copy.deepcopy(build);altered[key]=value
                with self.subTest(key=key,value=value),self.assertRaises(AssertionError):
                    verify_build_receipt(altered,source,image,revision)
            (source/'BE_GO/go.mod').write_text('module changed.test/fixture\n')
            with self.assertRaises(AssertionError):verify_build_receipt(build,source,image,revision)
    def fixture(self):
        names=['collaborator-list-owner-list','collaborator-list-anonymous']
        result={'success':True,'source_stable':True,'cleanup_verified':True,
            'proxy_cleanup_verified':True,'cleanup_errors':[],'managed_models':128,
            'repository_required':False,'repository_tests':[],'repository_execution':'not_run_required_separately','http_cases':len(names),
            'go_runtime':{'immutable_id':'sha256:'+'a'*64},'workloads':[],
            'fallback_negative_control':{'rejected':True,'reason':'upstream_mismatch','status':200,
                'managed_state_unchanged':True,'cache_unchanged':True,
                'django_requests':[{'case':names[0],'status':200}]},
            'database_write_evidence':{'success':True,'user_totals_delta':{'inserted':0,'updated':0,'deleted':0},
                'user_table_changes':[],'relation_identities_unchanged':True,
                'statistics_reset_unchanged':True,'database_unchanged':True}}
        for label in ('django','go','v1','v2'):
            result['workloads'].append({'backend':label,'cases':[{'name':name,'status':status,
                'managed_state_unchanged':True,'cache_unchanged':True,
                'django_requests':[{'case':name,'status':status}] if label in ('django','v1') else []}
                for name,status in zip(names,(200,401))]})
        return result,names
    def test_complete_four_workloads_accepted(self):
        result,names=self.fixture()
        assert_workloads(result,'sha256:'+'a'*64,len(names),set(names))
    def test_rejects_reduced_or_misattributed_or_mutating_workloads(self):
        original,names=self.fixture()
        mutations=[lambda r:r['workloads'].pop(),
            lambda r:r['workloads'][1]['cases'].pop(),
            lambda r:r['workloads'][3]['cases'][0].update(django_requests=[{'case':names[0],'status':200}]),
            lambda r:r['workloads'][2]['cases'][0].update(cache_unchanged=False),
            lambda r:r['workloads'][0]['cases'][0].update(managed_state_unchanged=False),
            lambda r:r['database_write_evidence']['user_totals_delta'].update(updated=1),
            lambda r:r['fallback_negative_control'].update(cache_unchanged=False),
            lambda r:r.update(cleanup_verified=False),
            lambda r:r.update(repository_required=True),
            lambda r:r['go_runtime'].update(immutable_id='sha256:'+'b'*64)]
        for index,mutate in enumerate(mutations):
            with self.subTest(index=index):
                result=copy.deepcopy(original);mutate(result)
                with self.assertRaises(AssertionError):assert_workloads(result,'sha256:'+'a'*64,len(names),set(names))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--expected-revision',required=True)
    parser.add_argument('--build-receipt',type=Path,required=True)
    parser.add_argument('--go-source',type=Path,default=ROOT)
    parser.add_argument('--oracle-source',type=Path,default=ROOT,
        help='Checkout containing the authored oracle; defaults to this repository')
    parser.add_argument('--postgres',default='postgres:16-alpine')
    parser.add_argument('--redis',default='redis:7-alpine')
    parser.add_argument('--output',type=Path,default=ROOT/'quality-reports/go-migration/collaborator-list-image')
    args=parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',args.image):parser.error('--image must be an immutable image ID')
    if not re.fullmatch(r'[0-9a-f]{40}',args.expected_revision):parser.error('--expected-revision must be full length')
    args.output=args.output.resolve();args.go_source=args.go_source.resolve();args.oracle_source=args.oracle_source.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    receipt=args.output/'results.json'
    if receipt.exists():parser.error('Use a fresh output directory; receipts must not be overwritten')
    started=time.monotonic()
    report={'success':False,'scope':__doc__,'complete_collaborator_parity':False,
        'source_stable':False,'build_source_stable':False,'cleanup_verified':False,
        'private_directory_removed':False,'source_sha256':{},
        'native_repository_groups':{'status':'not_run','required_separately':True,
            'reason':'The two actual Unix-socket PostgreSQL query/cancellation groups run in the native gate.'},
        'started_at':datetime.now(timezone.utc).isoformat()}
    receipt.write_text(json.dumps(report,indent=2)+'\n')
    build=proof=work=None
    cleanup_errors=[]
    try:
        # Choose the authored checkout before importing any TESTS modules. This
        # supports isolated peer work without copying or mutating shared files.
        sys.path.insert(0,str(args.oracle_source))
        from TESTS.adapters.django.production_runtime import require_production_python
        from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
        from TESTS.adapters.go.payment_category_image import ImageFixture,DATABASE
        from TESTS.contracts.test_collaborator_list import MATRIX_VERSION,MATRIX_CASE_COUNT,MATRIX_REQUIRED_NAMES
        require_production_python()
        report['source_sha256']=source_hashes(args.oracle_source)
        report['matrix_version']=MATRIX_VERSION
        report['matrix_case_count']=MATRIX_CASE_COUNT
        build=json.loads(args.build_receipt.read_text())
        verify_build_receipt(build,args.go_source,args.image,args.expected_revision)
        report['build_receipt']=build
        tool_env={key:value for key,value in os.environ.items()
                  if key in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
        tool_env.update(TESTS_COLLABORATOR_LIST_GO='1',TESTS_COLLABORATOR_LIST_VERSIONED='1')
        with tempfile.TemporaryDirectory(prefix='az-collaborator-image-',dir='/tmp') as directory, \
                (args.output/'services.log').open('w') as log:
            work=Path(directory)
            proof=ImageFixture(args.image,args.expected_revision,work,args.output,log)
            oracle_output=work/'oracle'
            try:
                proof.start_services(args.postgres,args.redis)
                assert proof.image_id==build['image_id']
                initialize_current_schema(work,'127.0.0.1',proof.postgres_port,DATABASE,log)
                from django.test import override_settings
                from TESTS.adapters.django.collaborator_list import run
                cache={'default':{'BACKEND':'django_redis.cache.RedisCache',
                    'LOCATION':f'redis://127.0.0.1:{proof.redis_port}/1',
                    'OPTIONS':{'CLIENT_CLASS':'django_redis.client.DefaultClient','PASSWORD':'synthetic-identity-only'}}}
                with override_settings(CACHES=cache):
                    result=run(work,log,tool_env,f'127.0.0.1:{proof.redis_port}',
                        go_server_factory=proof.server,output=oracle_output)
                assert_workloads(result,proof.image_id,MATRIX_CASE_COUNT,MATRIX_REQUIRED_NAMES)
                report['checks_passed']=True
            finally:
                # Every cleanup/copy attempt is independent; failure never skips
                # owned Docker teardown or erases the safely typed terminal result.
                try:
                    oracle_path=oracle_output/'http-results.json'
                    if oracle_path.exists():
                        oracle=json.loads(oracle_path.read_text())
                        report['oracle']=oracle
                        (args.output/'http-results.json').write_text(json.dumps(oracle,indent=2)+'\n')
                except BaseException as error:
                    cleanup_errors.append({'stage':'safe_oracle_receipt','error_type':type(error).__name__})
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                except BaseException as error:
                    cleanup_errors.append({'stage':'database_connections','error_type':type(error).__name__})
                try:proof.cleanup()
                except BaseException as error:
                    cleanup_errors.append({'stage':'owned_containers_network','error_type':type(error).__name__})
        assert not cleanup_errors,'Fixture cleanup failed'
    except BaseException as error:
        # Provider/command errors may contain private fixture arguments. Export
        # their types only; the safe oracle retains request names and statuses.
        report['error_type']=type(error).__name__
        failure=safe_status_failure(error)
        if failure is not None:report['failed_http_case']=failure
    finally:
        try:
            report['source_stable']=bool(report['source_sha256'] and source_hashes(args.oracle_source)==report['source_sha256'])
            report['build_source_stable']=bool(isinstance(build,dict) and build.get('source_sha256')
                and go_source_hashes(args.go_source)==build['source_sha256'])
        except BaseException as error:
            cleanup_errors.append({'stage':'source_integrity','error_type':type(error).__name__})
        report.update(image=proof.metadata if proof else {},cleanup_errors=cleanup_errors,
            cleanup_verified=bool(proof and proof.cleanup_verified and not cleanup_errors),
            private_directory_removed=bool(work and not work.exists()),
            elapsed_seconds=round(time.monotonic()-started,3),finished_at=datetime.now(timezone.utc).isoformat())
        report['success']=bool(report.get('checks_passed') and report['cleanup_verified']
            and report['private_directory_removed'] and report['source_stable'] and report['build_source_stable']
            and 'error_type' not in report)
        receipt.write_text(json.dumps(report,indent=2)+'\n')
    if not report['success']:
        print('Packaged collaborator checks failed; see typed results.json receipt.',file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({'success':True,'image':proof.image_id,'http_cases_per_workload':report['matrix_case_count'],
        'cleanup_verified':True,'native_repository_groups':'not_run; required separately'}))


if __name__=='__main__':main()
