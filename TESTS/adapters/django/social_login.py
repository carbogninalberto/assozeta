"""Social account selection/admission against the real Django login view.

Provider responses are explicitly controlled here: this proves the account,
normalization/audit and OTP boundary, not Google/Apple network or token validation.
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
    from django.test import override_settings
    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.middleware import AuditlogMiddleware
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation
    from application.models.audit_models import AuditLogIndex
    from application.views.auth_views import oauth2_login
    from rest_framework.test import APIRequestFactory
    from rest_framework_simplejwt.tokens import AccessToken
    import pyotp.totp

    cases=[]
    unset=object()
    secret='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    with disable_auditlog():
        actor=User.original_objects.create(user_id=uid(729900),username='SOCIAL-AUDIT-ACTOR',email='social-audit-actor@example.test',role=1,password='!unused')
        association=SportAssociation.original_objects.create(sport_association_id=uid(729901),user=actor,denomination='Social audit fixture')
    def add(name,*,rows=None,selected=0,backend='google',identifier=unset,token='synthetic-social-token',email=unset,
            valid=True,provider_error=False,provider_calls=1,outcome='required',otp=None,present=False,two_fa=True,audit_actor=False):
        index=len(cases);address=f'social-{index}@example.test'
        if rows is None:rows=[{}]
        users=[]
        with disable_auditlog():
            for n,values in enumerate(rows):
                values=dict(values)
                user=User.original_objects.create(user_id=uid(730000+index*10+n),username=values.pop('username',f'Social-{index}-{n}'),
                    email=values.pop('email',address),password='!social-does-not-check-password',role=2,first_name='First',last_name='Last',
                    two_fa=two_fa,two_fa_secret=secret,**values)
                users.append({'id':str(user.pk),'username':user.username})
        selected_id=users[selected]['id'] if selected is not None else ''
        changed=selected is not None and outcome in ('','required','invalid_otp','otp_type') and users[selected]['username']!=users[selected]['username'].upper()
        cases.append({'name':name,'identifier':address if identifier is unset else identifier,'token':token,'backend':backend,
            'email':address if email is unset else email,'valid':valid,'provider_error':provider_error,'provider_calls':provider_calls,
            'provider':'apple' if backend in ('apple','apple-id') else 'google','outcome':outcome,'otp':otp,'present':present,
            'selected':selected_id,'changed':changed,'rows':users,'actor':str(actor.pk) if audit_actor else None})
    for backend in ('google','google-oauth2','google-identity','apple','apple-id'):
        add('accepted alias '+backend,backend=backend)
    add('unknown before unsupported backend',rows=[],selected=None,backend=None,provider_calls=0,outcome='not_found')
    add('ambiguous before unsupported backend',rows=[{},{}],selected=None,backend=None,provider_calls=0,outcome='invalid')
    add('ambiguous before token',rows=[{},{}],selected=None,provider_calls=0,outcome='invalid')
    add('preferred username selects second',rows=[{}, {'username':'social-8@example.test'}],selected=1)
    add('two preferred usernames ambiguous',rows=[{'username':'social-9@example.test'},{'username':'SOCIAL-9@EXAMPLE.TEST'}],selected=None,provider_calls=0,outcome='invalid')
    add('invalid token',valid=False,outcome='token')
    add('email mismatch',email='else@example.test',outcome='email')
    add('case insensitive email',email='SOCIAL-12@EXAMPLE.TEST')
    add('email sharp-s is not casefold',rows=[{'email':'straße-social-lower@example.test','username':'Social-sharp'}],identifier='social-sharp',email='STRASSE-SOCIAL-LOWER@example.test',outcome='email')
    add('email final sigma',rows=[{'email':'ΟΣ@example.test','username':'Social-sigma'}],identifier='social-sigma',email='ος@example.test')
    add('email dotted I',rows=[{'email':'İ@example.test','username':'Social-dot'}],identifier='social-dot',email='i\u0307@example.test')
    add('missing email',email=None,outcome='identity')
    add('numeric email',email=123,outcome='identity')
    add('inactive after token',rows=[{'is_active':False}],valid=False,outcome='token')
    add('inactive after email',rows=[{'is_active':False}],email='else@example.test',outcome='email')
    add('inactive valid identity',rows=[{'is_active':False}],outcome='invalid')
    add('deleted invisible',rows=[{'deleted':True}],selected=None,provider_calls=0,outcome='not_found')
    add('inactive preferred wins',rows=[{}, {'username':'social-22@example.test','is_active':False}],selected=1,outcome='invalid')
    for backend,text in ((None,'None'),(True,'True'),(123,'123'),([],'[]'),({},'{}'),('GOOGLE','GOOGLE'),('unknown','unknown')):
        add('unsupported backend '+text,backend=backend,provider_calls=0,outcome='backend')
        cases[-1]['backend_text']=text
    add('provider exception',provider_error=True,outcome='provider')
    add('opaque token null forwarded',token=None,valid=False,outcome='token')
    add('opaque token object forwarded',token={'raw':[1,None]},valid=False,outcome='token')
    add('valid OTP',otp='287082',present=True,outcome='')
    add('invalid OTP retains normalization',otp='287083',present=True,outcome='invalid_otp')
    add('null OTP retains normalization',otp=None,present=True,outcome='otp_type')
    add('disabled OTP admits',two_fa=False,outcome='')
    add('trusted actor audit',audit_actor=True)
    add('unchanged username has no audit',rows=[{'username':'SOCIAL-UNCHANGED'}])
    add('numeric identifier',rows=[{'username':'730039'}],identifier=730039)
    add('Unicode fold preference',rows=[{'email':'straße-social@example.test','username':'STRASSE-SOCIAL@EXAMPLE.TEST'},{'email':'straße-social@example.test'}],identifier='straße-social@example.test',email='straße-social@example.test')

    query_counts=[5,5,5,5,5,1,1,1,5,1,1,1,5,1,5,5,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,5,5,5,5,6,2,2,2]
    assert len(cases)==len(query_counts)==41
    for case,count in zip(cases,query_counts):case['queries']=count
    batches=[]
    with disable_auditlog():
        for size in (1,16,128,512):
            address=f'social-batch-{size}@example.test'
            for n in range(size):
                User.original_objects.create(user_id=uid(800000+size*1000+n),username=address if n==size-1 else f'SOCIAL-BATCH-{size}-{n}',
                    email=address,role=2,password='!unused')
            batches.append({'identifier':address,'selected':uid(800000+size*1000+size-1)})
    content_type=ContentType.objects.get_for_model(User)
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    def readback(case,django=False):
        for row in case['rows']:
            user=User.original_objects.get(pk=row['id'])
            normalized=case['changed'] and row['id']==case['selected']
            assert user.username==(row['username'].upper() if normalized else row['username']),(case['name'],'username')
            assert (user.last_login is not None)==(django and case['outcome']=='' and row['id']==case['selected']),(case['name'],'last_login stage')
            entries=list(LogEntry.objects.filter(content_type=content_type,object_pk=row['id']))
            assert len(entries)==int(normalized),(case['name'],'audit count')
            if entries:
                event=entries[0]
                assert event.changes_dict=={'username':[row['username'],row['username'].upper()]}
                assert event.action==1 and event.object_repr=='name: First Last' and event.remote_addr=='127.0.0.1'
                assert (str(event.actor_id) if event.actor_id else None)==case['actor']
                assert event.actor_email==(actor.email if case['actor'] else None)
                assert event.cid is None and event.remote_port is None and event.additional_data is None
                indexes=list(AuditLogIndex.objects.filter(log_entry=event))
                assert len(indexes)==int(case['actor'] is not None)
                if indexes:assert str(indexes[0].sport_association_id)==str(association.pk) and indexes[0].resolution_path=='actor.sport_association'
            User.original_objects.filter(pk=row['id']).update(username=row['username'],last_login=None)
            LogEntry.objects.filter(content_type=content_type,object_pk=row['id']).delete()
    class FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls,tz=None):return cls.fromtimestamp(59,tz)
    factory=APIRequestFactory()
    with override_settings(RATELIMIT_ENABLE=False),patch.object(pyotp.totp,'datetime',SimpleNamespace(datetime=FrozenDateTime)):
        for case in cases:
            calls=[]
            def verify(provider,token):
                assert token==case['token'],(case['name'],'provider token changed')
                calls.append(provider)
                if case['provider_error']:raise RuntimeError('synthetic provider failure')
                return case['valid'],{'email':case['email'],'email_verified':False}
            with patch('application.views.auth_views.SocialAuthService.verify_google_token',side_effect=lambda token:verify('google',token)),patch('application.views.auth_views.SocialAuthService.verify_apple_token',side_effect=lambda token:verify('apple',token)):
                payload={'username':case['identifier'],'token':case['token'],'backend':case['backend'],'password':'ignored-social-branch'}
                if case['present']:payload['otp']=case['otp']
                request=factory.post('/oauth2/login',payload,format='json')
                if case['actor']:request.user=actor
                outcome=''
                try:response=AuditlogMiddleware(oauth2_login)(request)
                except AttributeError:outcome='identity'
                except TypeError:outcome='otp_type'
                except RuntimeError:outcome='provider'
                else:
                    if response.status_code==200:
                        assert str(AccessToken(response.data['access_token'])['user_id'])==case['selected']
                    elif response.status_code==400:
                        assert response.data=={'error':'Unsupported backend: '+str(case['backend'])};outcome='backend'
                    else:
                        assert response.status_code==401
                        outcome={'invalid credential (not found)':'not_found','invalid credentials':'invalid','Invalid social token':'token',
                            'Email mismatch':'email','OTP code required.':'required','OTP code not valid.':'invalid_otp'}[response.data.get('error',response.data.get('msg'))]
                    if outcome:assert not response.cookies
                assert outcome==case['outcome'],(case['name'],'Django outcome',outcome)
                assert calls==[case['provider']]*case['provider_calls'],(case['name'],'provider order')
                readback(case,True)
    assert snapshot()==before,'Django social admission left undeclared state'
    print(f'Django: {len(cases)} controlled-provider social admission cases passed',flush=True)
    fixture=work/'social-login.json'
    fixture.write_text(json.dumps({'cases':cases,'batches':batches}));fixture.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-timeout=3m','-tags=socialintegration','./internal/service','-run','^TestSocialPostgres$'],cwd=ROOT/'BE_GO',check=True,timeout=210,
            env={**tool_env,'ASSOZETA_SOCIAL_FIXTURE':str(fixture),'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        for case in cases:readback(case)
        assert snapshot()==before,'Go social admission changed undeclared state'
    finally:fixture.unlink(missing_ok=True)
    return len(cases)
