"""Reproduce credential snapshot conflicts with real PostgreSQL interleavings."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import subprocess
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from django.apps import apps
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.db import connections
    from django.test import override_settings
    from application.models import User
    from application.views.auth_views import oauth2_login
    from rest_framework.test import APIRequestFactory

    output=ROOT/'quality-reports/go-migration/credential-concurrency'
    output.mkdir(parents=True,exist_ok=True)
    (output/'body-results.json').write_text('{"success":false}\n')
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    ids=[uid(120000+i) for i in range(130)]
    email='CREDENTIAL-CONCURRENCY@EXAMPLE.TEST'
    batch_email='CREDENTIAL-PAGE@EXAMPLE.TEST'
    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS,RATELIMIT_ENABLE=False):
        password='synthetic-concurrency-only';encoded=make_password(password)
        User.original_objects.bulk_create([User(user_id=key,username=f'CREDENTIAL-{i}',email=email if i==0 else batch_email,
            password=encoded,role=User.ATHLETE) for i,key in enumerate(ids)])
        try:
            before=snapshot()
            calls=[]
            check_password=User.check_password
            def checked(user,value):
                def write_last_login():
                    try:
                        assert User.original_objects.filter(pk=user.pk).update(last_login=datetime(2030,1,2,tzinfo=timezone.utc))==1
                    finally: connections.close_all()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(write_last_login).result(timeout=10)
                calls.append(str(user.pk))
                return check_password(user,value)
            with patch.object(User,'check_password',checked):
                response=oauth2_login(APIRequestFactory().post('/oauth2/login',{'username':email,'password':password},format='json'))
            assert response.status_code==200 and calls==[ids[0]],'Django rejects credential selection after unrelated last-login write'
            User.original_objects.filter(pk=ids[0]).update(last_login=None)
            assert snapshot()==before,'Django interleaving changed unrelated state'
            print('Django login survives an actual second-connection last_login update during password verification',flush=True)
            fixture=work/'credential-concurrency.json'
            fixture.write_text(json.dumps(dict(ID=ids[0],Email=email,Encoded=encoded,BatchEmail=batch_email,BatchIDs=ids[1:])))
            fixture.chmod(0o600)
            completed=subprocess.run(['go','test','-race','-tags=credentialconcurrencyintegration','./internal/service','-run','^TestCredentialConcurrencyPostgres$','-json'],
                cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_CREDENTIAL_CONCURRENCY_FIXTURE':str(fixture),
                    'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},capture_output=True,text=True,timeout=120)
            (output/'go-test.jsonl').write_text(completed.stdout)
            events=[json.loads(line) for line in completed.stdout.splitlines()]
            for event in events:
                if event.get('Output'):print(event['Output'],end='',flush=True)
            if completed.stderr:print(completed.stderr,flush=True)
            completed.check_returncode()
            names={'unrelated finalization cannot exhaust credential attempts','concurrent reset is not overwritten by rehash',
                'disabled account revoked after snapshot','deleted account revoked after snapshot',
                'later page retains the original candidate snapshot','blocked rehash cancellation releases pool and writes'}
            expected={'TestCredentialConcurrencyPostgres/'+name.replace(' ','_') for name in names}
            passed={e.get('Test') for e in events if e['Action']=='pass' and '/' in e.get('Test','')}
            assert passed==expected and not any(e['Action']=='skip' for e in events),'missing/skipped credential concurrency scenario'
            assert snapshot()==before,'Go credential interleaving changed persisted state'
            (output/'body-results.json').write_text(json.dumps(dict(success=True,scenario_groups=len(names),
                scope='credential-stage interleavings; full session and public login verification separate'),indent=2)+'\n')
            return len(names)
        finally:
            with disable_auditlog(): User.original_objects.filter(pk__in=ids).delete()
            assert snapshot()==original,'credential concurrency cleanup changed preexisting rows'
