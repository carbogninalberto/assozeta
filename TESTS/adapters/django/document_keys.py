"""Production Python timestamp and actual django-storages medical-key contract.

No database, network, provider credentials, or customer files are accessed.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import posixpath
import random
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    from django.conf import settings
    settings.configure()
    from storages.backends.s3 import S3Storage
    from storages.utils import clean_name
    output = ROOT / 'quality-reports/go-migration/document-keys'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'results.json').write_text('{"success":false}\n')
    cases = []
    tool_env = {key: value for key, value in os.environ.items()
                if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    key_id = str(uuid.UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'))
    def add(created, filename='certificato.pdf', location='', *, expected=None, reject=False):
        # The exact path expression in the Django upload/cleanup source, followed
        # by the installed production S3 backend's own normalization method.
        name = posixpath.join('', str(created.timestamp()), key_id, filename)
        storage = S3Storage(location=location, access_key='fixture', secret_key='fixture', bucket_name='fixture')
        try:
            actual = storage._normalize_name(clean_name(name))
        except Exception as exc:
            from django.core.exceptions import SuspiciousOperation
            if not isinstance(exc, SuspiciousOperation): raise
            assert reject, (name, location, exc)
            actual = None
        if expected is not None: assert actual == expected, (name, actual, expected)
        cases.append(dict(Created=created.isoformat(),ID=key_id,Filename=filename,Location=location,Expected=actual or '',Reject=reject))
    epoch = datetime(1970,1,1,tzinfo=timezone.utc)
    for micros,stamp in ((0,'0.0'),(1,'1e-06'),(-1,'-1e-06'),(99,'9.9e-05'),(100,'0.0001'),(-100,'-0.0001'),(123456,'0.123456'),(1000000,'1.0')):
        add(epoch+timedelta(microseconds=micros),expected=f'{stamp}/{key_id}/certificato.pdf')
    for filename,suffix in (('日本語 %.pdf','日本語 %.pdf'),('nested/../a.pdf','a.pdf'),(r'nested\a.pdf','nested/a.pdf'),('nested//a.pdf/','nested/a.pdf/'),('', '')):
        for location in ('','media/','../media',r'prefix\literal','media/files///'):
            add(epoch,filename,location,expected=f'{location.rstrip("/") + "/" if location else ""}0.0/{key_id}/{suffix}')
    # The old normalizer permits escaping the document directory. Go refuses to
    # resolve these as this document's key; this correction is asserted explicitly.
    for filename in ('../foreign.pdf','nested/../../foreign.pdf','/absolute.pdf',r'..\foreign.pdf',r'nested\..\..\foreign.pdf'):
        add(epoch,filename,reject=True)
        assert f'0.0/{key_id}/' not in cases[-1]['Expected']
    for location in ('media/../other','media//files','.'):
        add(epoch,location=location,reject=True)
    for date in (datetime.min.replace(tzinfo=timezone.utc),datetime.max.replace(tzinfo=timezone.utc),datetime(1900,1,1,tzinfo=timezone.utc),datetime(2024,1,1,1,0,0,123456,tzinfo=timezone(timedelta(hours=1)))):
        add(date)
    rng=random.Random(172024)
    # Exact integer microsecond sampling covers pre-epoch and distant timestamps
    # where converting microseconds to float before division can round twice.
    first=datetime.min.replace(tzinfo=timezone.utc)
    for _ in range(5000):
        date=first+timedelta(days=rng.randrange(3652059),seconds=rng.randrange(86400),microseconds=rng.randrange(1000000))
        add(date,location='private/documents')
    with tempfile.TemporaryDirectory(prefix='assozeta-document-keys-') as directory:
        fixture=Path(directory)/'keys.json'
        fixture.write_text(json.dumps(dict(Cases=cases)))
        completed=subprocess.run(['go','test','-race','-tags=documentkeyintegration','./pkg/storage/documentkey','-run','^TestMedicalS3Key','-json'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_DOCUMENT_KEY_FIXTURE':str(fixture)},capture_output=True,text=True,timeout=90)
        (output/'go-test.jsonl').write_text(completed.stdout)
        if completed.stderr:print(completed.stderr)
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'): print(event['Output'],end='')
        completed.check_returncode()
        passed={event.get('Test') for event in events if event['Action']=='pass'}
        assert {'TestMedicalS3Key','TestMedicalS3KeyRejectsCrossDocumentNames','TestMedicalS3KeyDjango'} <= passed
        assert not any(event['Action']=='skip' for event in events)
    result=dict(success=True,cases=len(cases),scope='legacy medical S3 key resolution only; no provider/file/task execution')
    (output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
