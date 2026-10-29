"""Credential-stage evidence from real Django login and Go transactions.

Django exercises the actual view; Go checks account selection and password writes
only. This deliberately does not claim Go HTTP, OTP, audit/username or last-login
parity. All hashes/passwords are synthetic and the fixture is temporary.
"""
import json
import subprocess
from .scoped_resources import uid


def run(work, log, tool_env):
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.test import override_settings
    from django.apps import apps
    from auditlog.context import disable_auditlog
    from django.contrib.auth.hashers import make_password, check_password, PBKDF2PasswordHasher
    from application.models import User
    from application.views.auth_views import oauth2_login
    from django.core.files.uploadedfile import SimpleUploadedFile
    from rest_framework.test import APIRequestFactory
    from rest_framework_simplejwt.tokens import AccessToken
    import bcrypt
    from .accounts import ROOT

    password = 'synthetic-login-contract-only'
    cases = []
    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS, RATELIMIT_ENABLE=False):
        current = make_password(password)
        old = PBKDF2PasswordHasher().encode(password, 'old-salt', iterations=10)
        raw = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
        missing=object()
        def add(name, accounts, selected=None, changed=(), failure='', identifier=missing, supplied=password, exception='AttributeError'):
            index = len(cases)
            email = f'PASSWORD-CASE-{index}@EXAMPLE.TEST'
            identifier = email if identifier is missing else identifier
            rows = []
            with disable_auditlog():
                for n, values in enumerate(accounts):
                    values = dict(values)
                    encoded = values.pop('password', current)
                    username = values.pop('username', f'PASSWORD-CASE-{index}-USER-{n}')
                    user = User.original_objects.create(user_id=uid(20000+index*10+n), username=username,
                        email=email, password=encoded, role=User.ATHLETE, **values)
                    rows.append({'id':str(user.pk), 'encoded':encoded, 'changed':n in changed})
            cases.append({'name':name,'identifier':identifier,'password':supplied,'rows':rows,
                'selected':rows[selected]['id'] if selected is not None else '', 'failure':failure,'exception':exception})
        add('current singleton', [{}], 0)
        add('incorrect password', [{}], failure='invalid', supplied='wrong')
        add('unknown identifier', [], failure='not_found')
        add('ambiguous Django hashes all upgrade', [{'password':old},{'password':old}], changed=(0,1), failure='invalid')
        add('ambiguous raw bcrypt does not migrate', [{'password':raw},{'password':raw}], failure='invalid')
        add('preferred matching username wins', [{'password':old},{'password':old,'username':'PASSWORD-CASE-5@EXAMPLE.TEST'}], 1, (0,1))
        add('unique credential among duplicates', [{'password':'!disabled'},{}], 1)
        add('wrong preferred candidate cannot win', [{'password':'!disabled','username':'PASSWORD-CASE-7@EXAMPLE.TEST'},{}], 1)
        add('inactive Django password still upgrades', [{'password':old,'is_active':False}], changed=(0,), failure='invalid')
        add('inactive raw bcrypt still migrates', [{'password':raw,'is_active':False}], changed=(0,), failure='invalid')
        add('deleted account excluded', [{'password':old,'deleted':True}], failure='not_found')
        add('short preferred salt upgrades', [{'password':PBKDF2PasswordHasher().encode(password,'short')}], 0, (0,))
        add('higher preferred cost normalizes', [{'password':PBKDF2PasswordHasher().encode(password,'adequate-salt-characters',iterations=1000001)}], 0, (0,))
        for algorithm in ('pbkdf2_sha1','bcrypt_sha256','scrypt','argon2'):
            add(f'{algorithm} upgrades', [{'password':make_password(password,hasher=algorithm)}], 0, (0,))
        add('raw bcrypt selected only', [{'password':raw},{'password':raw,'username':'PASSWORD-CASE-17@EXAMPLE.TEST'}], 1, (1,))
        add('unrecognized hash', [{'password':'unknown$synthetic'}], failure='invalid')
        add('whitespace and username lookup', [{'username':'USERNAME-LOGIN'}], 0, identifier='\u001c username-login \u001f')
        add('Unicode casefold preferred', [{'username':'STRASSE@EXAMPLE.TEST'},{}], 0, identifier='straße@example.test')
        # Both candidates must be in the email lookup; preference uses Python casefold.
        User.original_objects.filter(pk__in=[row['id'] for row in cases[-1]['rows']]).update(email='straße@example.test')
        # Explicit raw-input expectations: Python stringification is not JSON
        # encoding. A current PBKDF2 hash may accept non-string force_bytes input;
        # rehash and raw-bcrypt fallback reject the original non-string value.
        raw_values=[
            ('integer',123,'123'),('large integer',123456789012345678901234567890,'123456789012345678901234567890'),
            ('true',True,'True'),('false',False,'False'),('float',1.5,'1.5'),('float whole',1.0,'1.0'),
            ('float negative zero',-0.0,'-0.0'),('float exponent',1e16,'1e+16'),('float fixed',1e15,'1000000000000000.0'),
            ('float small',1e-5,'1e-05'),('empty list',[],'[]'),('list',[None,True,3.0,'x'],"[None, True, 3.0, 'x']"),
            ('ordered object',{'z':False,'a':['x']},"{'z': False, 'a': ['x']}"),
        ]
        for label,value,text in raw_values:
            assert str(value)==text, (label,'declared Python representation')
            add('raw password '+label,[{'password':make_password(text)}],0,supplied=value)
        add('null password', [{'password':make_password('None')}], failure='value', supplied=None)
        add('wrong numeric password',[{'password':make_password('123')}],failure='value',supplied=124)
        add('unusable numeric password',[{'password':'!unused'}],failure='value',supplied=123)
        add('unknown numeric password',[{'password':'retired$hash'}],failure='value',supplied=123)
        add('raw bcrypt numeric password',[{'password':bcrypt.hashpw(b'123',bcrypt.gensalt(rounds=4)).decode()}],failure='value',supplied=123)
        add('old PBKDF2 numeric password',[{'password':PBKDF2PasswordHasher().encode('123','old-salt',iterations=10)}],failure='value',supplied=123,exception='TypeError')
        for algorithm in ('pbkdf2_sha1','bcrypt_sha256','scrypt','argon2'):
            add(algorithm+' numeric password',[{'password':make_password('123',hasher=algorithm)}],failure='value',supplied=123,exception='TypeError' if algorithm=='pbkdf2_sha1' else 'AttributeError')
        add('unknown account before null password',[],failure='not_found',supplied=None)
        add('unknown account before list password',[],failure='not_found',supplied=[1,2])
        for label,value,text in [('null',None,'None')]+raw_values:
            add('raw identifier '+label,[{'username':text.upper()}],0,identifier=value)
        # Multipart file values stringify to filenames for identifiers/PBKDF2,
        # but retain non-string type for fallback and make_password rejection.
        add('multipart password file matches current hash',[{'password':make_password('credential.txt')}],0,supplied=None)
        cases[-1]['password_upload']={'name':'credential.txt','size':3}
        add('multipart password file mismatch',[{'password':make_password('other.txt')}],failure='value',supplied=None)
        cases[-1]['password_upload']={'name':'credential.txt','size':3}
        add('multipart password file cannot rehash',[{'password':PBKDF2PasswordHasher().encode('credential.txt','old-salt',iterations=10)}],failure='value',supplied=None,exception='TypeError')
        cases[-1]['password_upload']={'name':'credential.txt','size':3}
        add('multipart identifier file',[{'username':'IDENTIFIER.TXT'}],0,identifier=None)
        cases[-1]['identifier_upload']={'name':'identifier.txt','size':3}
        numeric_hash=make_password('123')
        add('raw password inactive account',[{'password':numeric_hash,'is_active':False}],failure='invalid',supplied=123)
        add('raw password deleted account',[{'password':numeric_hash,'deleted':True}],failure='not_found',supplied=123)
        add('raw password ambiguous accounts',[{'password':numeric_hash},{'password':numeric_hash}],failure='invalid',supplied=123)
        add('raw password later candidate failure',[{'password':numeric_hash},{'password':make_password('124')}],failure='value',supplied=123)
        batches = []
        with disable_auditlog():
            for index, size in enumerate((1,16,128,129,257)):
                email = f'PASSWORD-BATCH-{size}@EXAMPLE.TEST'
                ids = []
                for n in range(size):
                    key = uid(30000+index*1000+n)
                    User.original_objects.create(user_id=key, username=f'PASSWORD-BATCH-{size}-USER-{n}',
                        email=email, password='!query-count-only', role=User.ATHLETE)
                    ids.append(key)
                batches.append({'identifier':email,'ids':ids})
        def snapshot():
            return {m._meta.label:list(m._base_manager.order_by('pk').values())
                    for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        before = snapshot()
        factory = APIRequestFactory()
        for case in cases:
            try:
                payload={'username':case['identifier'],'password':case['password']}
                for field in ('identifier','password'):
                    if upload:=case.get(field+'_upload'):
                        payload['username' if field=='identifier' else field]=SimpleUploadedFile(upload['name'],b'x'*upload['size'])
                response = oauth2_login(factory.post('/oauth2/login', payload, format='multipart' if any(k.endswith('_upload') for k in case) else 'json'))
            except (AttributeError,TypeError) as error:
                assert type(error).__name__==case['exception'], (case['name'],'unexpected exception class')
                assert case['failure']=='value', (case['name'],'unexpected Django value failure')
                response=None
            if case['failure']=='value':
                assert response is None, (case['name'],'missing Django value failure')
            elif case['selected']:
                assert response.status_code == 200, f"{case['name']}: Django login did not succeed"
                assert str(AccessToken(response.data['access_token'])['user_id']) == case['selected'], f"{case['name']}: wrong selection"
            else:
                expected = 'invalid credential (not found)' if case['failure']=='not_found' else 'invalid credentials'
                assert response.status_code == 401 and response.data == {'error':expected}, f"{case['name']}: incorrect denial"
            for row in case['rows']:
                user = User.original_objects.get(pk=row['id'])
                assert (user.password != row['encoded']) == row['changed'], f"{case['name']}: wrong Django password write set"
                if row['changed']:
                    assert user.password.startswith('pbkdf2_sha256$1000000$') and check_password(password,user.password), 'Django rehash incompatible'
                User.original_objects.filter(pk=user.pk).update(password=row['encoded'],last_login=None)
        assert snapshot() == before, 'Django characterization left unexpected persisted effects'
        print(f'Django: {len(cases)} explicit password selection/rehash cases passed', flush=True)
        path = work / 'password-login.json'
        path.write_text(json.dumps({'cases':cases,'batches':batches})); path.chmod(0o600)
        try:
            subprocess.run(['go','test','-race','-timeout=5m','-tags=loginintegration','./internal/service','-run','^TestPasswordLogin'],cwd=ROOT/'BE_GO',
                check=True,timeout=330, env={**tool_env,'ASSOZETA_PASSWORD_LOGIN_FIXTURE':str(path),
                    'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
            for case in cases:
                for row in case['rows']:
                    encoded = User.original_objects.get(pk=row['id']).password
                    assert (encoded != row['encoded']) == row['changed'], f"{case['name']}: wrong Go password write set"
                    if row['changed']:
                        assert encoded.startswith('pbkdf2_sha256$1000000$') and check_password(password,encoded), 'Django cannot verify Go password'
                    User.original_objects.filter(pk=row['id']).update(password=row['encoded'])
            assert snapshot() == before, 'Go credential stage changed fields/tables beyond declared passwords'
        finally:
            path.unlink(missing_ok=True)
    return len(cases)
