"""Real disposable MinIO: Django deletion and Go bounded multi-object protocol."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
IMAGE='ghcr.io/carbogninalberto/minio:RELEASE.2025-04-22T22-12-26Z-assozeta.1@sha256:8d35f39307b12cbc9ba3eedd26587d77ab30c21c3e904aee552df8b17b600389'


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    from django.conf import settings
    settings.configure()
    from storages.backends.s3 import S3Storage
    out=ROOT/'quality-reports/go-migration/s3-objects';out.mkdir(parents=True,exist_ok=True)
    (out/'results.json').write_text('{"success":false}\n')
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN','DOCKER_HOST','DOCKER_CONTEXT')}
    def docker(*args):return subprocess.check_output(['docker',*args],text=True,timeout=120,env=env).strip()
    try:docker('image','inspect',IMAGE)
    except subprocess.CalledProcessError:docker('pull',IMAGE)
    access='fixture-'+uuid.uuid4().hex[:12];secret=uuid.uuid4().hex+uuid.uuid4().hex
    container=docker('run','-d','--rm','--name','az-s3-'+uuid.uuid4().hex[:12],'-p','127.0.0.1::9000','--tmpfs','/data:rw,size=536870912',
        '-e','MINIO_ROOT_USER='+access,'-e','MINIO_ROOT_PASSWORD='+secret,IMAGE,'server','/data','--address',':9000')
    try:
        minio_version=docker('exec',container,'minio','--version')
        assert 'RELEASE.2025-04-22T22-12-26Z' in minio_version,'MinIO image version differs from fixture contract'
        bound=docker('port',container,'9000/tcp');assert bound.startswith('127.0.0.1:') and '\n' not in bound
        endpoint='http://'+bound
        ready=False
        for _ in range(100):
            try:
                with urllib.request.urlopen(endpoint+'/minio/health/ready',timeout=1) as response:ready=response.status==200
            except (OSError,TimeoutError):pass
            if ready:break
            time.sleep(.1)
        assert ready,'disposable MinIO did not become ready'
        client=boto3.client('s3',endpoint_url=endpoint,aws_access_key_id=access,aws_secret_access_key=secret,region_name='us-east-1',config=Config(signature_version='s3v4',retries={'max_attempts':0},s3={'addressing_style':'path'}))
        plain='fixture-plain';versioned='fixture-versions';locked='fixture-locked'
        client.create_bucket(Bucket=plain);client.create_bucket(Bucket=versioned);client.create_bucket(Bucket=locked,ObjectLockEnabledForBucket=True)
        client.put_bucket_versioning(Bucket=versioned,VersioningConfiguration={'Status':'Enabled'})
        storage=S3Storage(access_key=access,secret_key=secret,bucket_name=plain,endpoint_url=endpoint,region_name='us-east-1',location='private')
        client.put_object(Bucket=plain,Key='private/django.pdf',Body=b'Django delete fixture')
        assert storage.delete('django.pdf') is None and storage.delete('missing.pdf') is None
        try:client.head_object(Bucket=plain,Key='private/django.pdf');raise AssertionError('Django failed to remove object')
        except ClientError as exc:assert exc.response['ResponseMetadata']['HTTPStatusCode']==404
        missing=S3Storage(access_key=access,secret_key=secret,bucket_name='fixture-bucket-missing',endpoint_url=endpoint,region_name='us-east-1')
        assert missing.delete('any.pdf') is None,'legacy 404 missing-bucket behavior changed'
        client.put_object(Bucket=plain,Key='keep.pdf',Body=b'keep exact bytes')
        batches=[]
        for size in (1,16,128,512,1000):
            keys=[f'private/{size}/{i}-日本語 % & <.pdf' for i in range(size)]
            if size==16:keys[0]='private/16/line\r\n\t % & <日本語.pdf'
            batches.append(keys)
            # Last object intentionally does not exist. Every returned key still
            # needs a successful outcome; setup fan-out is bounded separately.
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda key:client.put_object(Bucket=plain,Key=key,Body=b'private fixture'),keys[:-1]))
        old=client.put_object(Bucket=versioned,Key='versioned.pdf',Body=b'old')['VersionId']
        new=client.put_object(Bucket=versioned,Key='versioned.pdf',Body=b'new')['VersionId']
        marker_version=client.put_object(Bucket=versioned,Key='marker.pdf',Body=b'preserve history')['VersionId']
        client.put_object(Bucket=versioned,Key='recover-marker.pdf',Body=b'recovered history')
        recover_marker=client.delete_object(Bucket=versioned,Key='recover-marker.pdf')['VersionId']
        client.put_object(Bucket=versioned,Key='probe-marker.pdf',Body=b'probe history')
        probe_marker=client.delete_object(Bucket=versioned,Key='probe-marker.pdf')['VersionId']
        probe=client.delete_objects(Bucket=versioned,Delete={'Objects':[{'Key':'probe-marker.pdf','VersionId':probe_marker}]})
        assert probe['Deleted']==[{'Key':'probe-marker.pdf','VersionId':probe_marker}] and not probe.get('Errors'),'MinIO marker-removal metadata changed'
        (out/'marker-removal-fields.json').write_text(json.dumps(sorted(probe['Deleted'][0]))+'\n')
        django_version=client.put_object(Bucket=versioned,Key='django-versioned.pdf',Body=b'Django history')['VersionId']
        versioned_storage=S3Storage(access_key=access,secret_key=secret,bucket_name=versioned,endpoint_url=endpoint,region_name='us-east-1')
        assert versioned_storage.delete('django-versioned.pdf') is None
        assert client.get_object(Bucket=versioned,Key='django-versioned.pdf',VersionId=django_version)['Body'].read()==b'Django history'
        locked_version=client.put_object(Bucket=locked,Key='locked.pdf',Body=b'locked',ObjectLockMode='COMPLIANCE',ObjectLockRetainUntilDate=datetime.now(timezone.utc)+timedelta(days=1))['VersionId']
        free=client.put_object(Bucket=locked,Key='free.pdf',Body=b'free')['VersionId']
        retention=client.delete_objects(Bucket=locked,Delete={'Objects':[{'Key':'locked.pdf','VersionId':locked_version}]})
        retention_codes=[item['Code'] for item in retention.get('Errors',[])]
        assert retention_codes==['InvalidRequest'] and not retention.get('Deleted'),'compliance lock must refuse deletion'
        (out/'retention-code.json').write_text(json.dumps({'codes':retention_codes})+'\n')
        fixture=dict(Endpoint=endpoint,AccessKey=access,SecretKey=secret,PlainBucket=plain,VersionedBucket=versioned,LockBucket=locked,OldVersion=old,LockedVersion=locked_version,FreeVersion=free,RecoverMarker=recover_marker,Batches=batches)
        with tempfile.TemporaryDirectory(prefix='az-s3-fixture-') as directory:
            path=Path(directory)/'fixture.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
            marker_result=Path(directory)/'marker.txt'
            run=subprocess.run(['go','test','-race','-tags=s3integration','./pkg/storage/s3objects','-run','^TestMinIODelete$','-json'],cwd=ROOT/'BE_GO',env={**env,'ASSOZETA_S3_FIXTURE':str(path),'ASSOZETA_S3_MARKER_RESULT':str(marker_result)},capture_output=True,text=True,timeout=120)
            (out/'minio-go-test.jsonl').write_text(run.stdout)
            if run.stderr:print(run.stderr)
            events=[json.loads(line) for line in run.stdout.splitlines()]
            for event in events:
                if event.get('Output'):print(event['Output'],end='')
            run.check_returncode()
            names={'bounded_batches_preserve_keys_and_delete_missing_objects','specific_old_version_preserves_current_object','current_version_creates_an_explicit_delete_marker','removing_a_specific_marker_restores_previous_bytes','object_lock_returns_explicit_partial_outcomes','invalid_credentials_leave_objects_intact','missing_bucket_is_not_successful_cleanup'}
            assert {e.get('Test') for e in events if e['Action']=='pass' and '/' in e.get('Test','')}=={'TestMinIODelete/'+n for n in names}
            assert not any(e['Action']=='skip' for e in events)
            marker_id=marker_result.read_text()
        assert [o['Key'] for o in client.list_objects_v2(Bucket=plain).get('Contents',[])]==['keep.pdf']
        assert client.get_object(Bucket=plain,Key='keep.pdf')['Body'].read()==b'keep exact bytes'
        assert client.get_object(Bucket=versioned,Key='versioned.pdf')['Body'].read()==b'new'
        assert client.get_object(Bucket=versioned,Key='versioned.pdf',VersionId=new)['Body'].read()==b'new'
        assert client.get_object(Bucket=locked,Key='locked.pdf',VersionId=locked_version)['Body'].read()==b'locked'
        assert client.get_object(Bucket=versioned,Key='marker.pdf',VersionId=marker_version)['Body'].read()==b'preserve history'
        assert client.get_object(Bucket=versioned,Key='recover-marker.pdf')['Body'].read()==b'recovered history'
        assert client.get_object(Bucket=versioned,Key='probe-marker.pdf')['Body'].read()==b'probe history'
        markers=client.list_object_versions(Bucket=versioned).get('DeleteMarkers',[])
        assert len(markers)==2 and {(item['Key'],item['IsLatest']) for item in markers}=={('marker.pdf',True),('django-versioned.pdf',True)}
        assert next(item['VersionId'] for item in markers if item['Key']=='marker.pdf')==marker_id
        for key in ('marker.pdf','django-versioned.pdf'):
            try:client.head_object(Bucket=versioned,Key=key);raise AssertionError('deleted current object remains visible')
            except ClientError as exc:assert exc.response['ResponseMetadata']['HTTPStatusCode']==404
        for bucket,key,version in ((versioned,'versioned.pdf',old),(locked,'free.pdf',free)):
            try:client.head_object(Bucket=bucket,Key=key,VersionId=version);raise AssertionError('selected version remains')
            except ClientError as exc:assert exc.response['ResponseMetadata']['HTTPStatusCode']==404
        result=dict(success=True,minio_groups=7,batch_sizes=[1,16,128,512,1000],django_cases=4,image=IMAGE,minio_version=minio_version,scope='real isolated S3 deletion; workflow authorization/intent/restore coordination remain open')
        (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        docker('rm','-f',container)


if __name__=='__main__':main()
