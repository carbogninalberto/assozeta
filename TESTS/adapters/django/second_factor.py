"""Actual Django login versus Go second-factor decisions on synthetic accounts.

The clock is controlled only inside PyOTP; signing, model writes and the real
Django view are exercised. This does not certify the remaining Go login workflow.
"""
import datetime
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from django.apps import apps
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from application.models import User
    from application.views.auth_views import oauth2_login
    from rest_framework.test import APIRequestFactory
    from rest_framework_simplejwt.tokens import AccessToken
    import pyotp.totp

    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'  # Public, synthetic test key.
    cases = []
    def add(name, value=None, code='', enabled=True, key=secret, present=True, at=59):
        cases.append({'name':name,'value':value,'code':code,'enabled':enabled,
                      'secret':key,'present':present,'at':at})
    add('disabled missing OTP', enabled=False, present=False, key=None)
    add('disabled ignores invalid shape and secret', enabled=False, key='!', value=True)
    add('missing OTP', present=False, code='required')
    add('missing OTP precedes missing secret', present=False, key=None, code='required')
    add('short OTP precedes corrupt secret', '12345', key='!', code='required')
    add('empty OTP', '', code='required')
    add('long OTP', '287082 ', code='required')
    add('null OTP', None, code='server_error')
    add('numeric OTP', 287082, code='server_error')
    add('boolean OTP', True, code='server_error')
    add('empty list', [], code='required')
    add('six list elements', [2,8,7,0,8,2], code='invalid')
    add('empty object', {}, code='required')
    add('six object fields', dict.fromkeys('abcdef',1), code='invalid')
    add('list does not bypass corrupt secret', [1]*6, key='!', code='server_error')
    add('current interval', '287082')
    add('same code replay retains existing behavior', '287082')
    add('epoch interval', '755224', at=0)
    add('previous interval denied', '755224', code='invalid')
    add('next interval denied', '359152', code='invalid')
    add('expiry at boundary', '287082', at=60, code='invalid')
    add('new boundary code', '359152', at=60)
    add('leading zero', '081804', at=1111111109)
    add('incorrect code', '287083', code='invalid')
    add('six characters including space', '28708 ', code='invalid')
    add('fullwidth digits normalize', '２８７０８２')
    add('superscript digits normalize', '²⁸⁷⁰⁸²')
    add('astral digits count as six characters', '𝟚𝟠𝟟𝟘𝟠𝟚')
    add('Arabic digits do not normalize to ASCII', '٢٨٧٠٨٢', code='invalid')
    add('lowercase secret', '287082', key=secret.lower())
    add('omitted padding', '300412', key='MY')
    add('explicit padding', '300412', key='my======')
    add('nonzero unused base32 bits', '300412', key='MZ')
    add('empty persisted secret', '812658', key='')
    add('missing persisted secret', '287082', key=None, code='server_error')
    for name, key in [('invalid character','!'),('invalid padding','A'),('linebreak','MY======\n'),
                      ('zero is not O','A0AAAAAA'),('one is not I','A1AAAAAA'),('non-ASCII secret','ſY')]:
        add(name, '287082', key=key, code='server_error')
    add('negative counter', '287082', at=-30, code='server_error')
    for name, filename, size, code in [
            ('file filename is the OTP','287082',6,''),
            ('file content is not the OTP','wrong!',6,'invalid'),
            ('short file requires OTP','287082',5,'required'),
            ('empty file requires OTP','287082',0,'required'),
            ('Unicode filename normalizes','２８７０８２',6,'')]:
        add(name, code=code)
        cases[-1]['upload'] = {'name':filename,'size':size}

    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS, RATELIMIT_ENABLE=False):
        password = 'synthetic-second-factor-password'
        with disable_auditlog():
            user = User.original_objects.create(user_id=uid(40000),username='SECOND-FACTOR-LOGIN',
                email='second-factor@example.test',password=make_password(password),role=User.ATHLETE)
        def snapshot():
            return {m._meta.label:list(m._base_manager.order_by('pk').values())
                    for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        before = snapshot()
        factory = APIRequestFactory()
        for case in cases:
            User.original_objects.filter(pk=user.pk).update(two_fa=case['enabled'],two_fa_secret=case['secret'])
            payload = {'username':user.username,'password':password}
            if case['present']:
                payload['otp'] = case['value']
            if 'upload' in case:
                upload = case['upload']
                payload['otp'] = SimpleUploadedFile(upload['name'],b'x'*upload['size'])
            class FrozenDateTime(datetime.datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls.fromtimestamp(case['at'],tz)
            outcome = ''
            try:
                with patch.object(pyotp.totp,'datetime',SimpleNamespace(datetime=FrozenDateTime)):
                    response = oauth2_login(factory.post('/oauth2/login',payload,format='multipart' if 'upload' in case else 'json'))
            except (TypeError, ValueError):
                outcome = 'server_error'
            else:
                if response.status_code == 200:
                    assert str(AccessToken(response.data['access_token'])['user_id']) == str(user.pk), 'wrong account'
                elif response.status_code == 401 and response.data == {'msg':'OTP code required.'}:
                    outcome = 'required'
                elif response.status_code == 401 and response.data == {'msg':'OTP code not valid.'}:
                    outcome = 'invalid'
                else:
                    raise AssertionError(f"{case['name']}: unexpected Django response")
            assert outcome == case['code'], f"{case['name']}: wrong Django OTP outcome"
            persisted = User.original_objects.get(pk=user.pk)
            assert (persisted.last_login is not None) == (outcome == ''), 'failed OTP changed last_login'
            assert persisted.username == user.username, 'fixture unexpectedly normalized username'
            User.original_objects.filter(pk=user.pk).update(two_fa=False,two_fa_secret=None,last_login=None)
        assert snapshot() == before, 'OTP characterization left undeclared persisted changes'
        print(f'Django: {len(cases)} explicit second-factor login cases passed', flush=True)
        path = work / 'second-factor.json'
        path.write_text(json.dumps({'cases':cases})); path.chmod(0o600)
        try:
            subprocess.run(['go','test','-race','-tags=otpintegration','./internal/service','-run','^TestSecondFactorAgainstDjango'],
                cwd=ROOT/'BE_GO',check=True,timeout=120,env={**tool_env,'ASSOZETA_SECOND_FACTOR_FIXTURE':str(path)})
            assert snapshot() == before, 'Go second-factor checks changed persisted state'
        finally:
            path.unlink(missing_ok=True)
    return len(cases)
