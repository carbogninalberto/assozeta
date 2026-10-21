"""Complete password service composition against the real Django login view.

Synthetic users on the accounts adapter's private database. These are service
contracts; HTTP parsing, middleware, cookies and image dispatch remain separate.
"""
import datetime
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid
from .login_response import base_profile, association_profile


def run(work, log, tool_env):
    from django.apps import apps
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.contrib.contenttypes.models import ContentType
    from django.test import override_settings
    from django.db import connection, IntegrityError
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.middleware import AuditlogMiddleware
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration
    from application.models.audit_models import AuditLogIndex
    from application.views.auth_views import oauth2_login
    from rest_framework.test import APIRequestFactory
    from rest_framework.renderers import JSONRenderer
    import jwt
    import pyotp.totp

    now=timezone.now().replace(microsecond=0)
    password='synthetic-composed-session-only'
    secret='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    cases=[]
    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS, RATELIMIT_ENABLE=False,
            FAMILY_REGISTRATION_ENABLED=True,FEDERATION_REGISTRATION_ENABLED=False,MULTIPLE_REGISTRATION_ENABLED=False):
        encoded=make_password(password)
        with disable_auditlog():
            owner=User.original_objects.create(user_id=uid(610000),username='SESSION-OWNER',email='session-owner@example.test',role=1,password='!unused')
            owner_association=SportAssociation.original_objects.create(sport_association_id=uid(620000),user=owner,denomination='Fixture ASD',tax_code='12345678901',additional_sections=[])
            SportAssociationMembershipCardConfiguration.objects.create(sport_association=owner_association)
        def add(name,*,role=2,admin=False,active=True,deleted=False,otp=None,present=False,two_fa=False,
                outcome='',supplied=password,delete_days=None,missing_association=False,create_card=False,uppercase=False,queries=8):
            n=len(cases);id=uid(611000+n);username=f'Session-{n}';username=username.upper() if uppercase else username
            delete_on=None if delete_days is None else now.date()+datetime.timedelta(days=delete_days)
            with disable_auditlog():
                user=User.original_objects.create(user_id=id,username=username,email=f'session-{n}@example.test',first_name='First',last_name='Last',
                    password=encoded,role=role,is_superuser=admin,is_active=active,deleted=deleted,date_joined=now-datetime.timedelta(days=60),
                    connected_user=owner if role==3 else None,two_fa=two_fa,two_fa_secret=secret,delete_on=delete_on)
                association=owner_association if role==3 else None
                if role==1 and not admin and not missing_association:
                    association=SportAssociation.original_objects.create(sport_association_id=uid(621000+n),user=user,denomination='Fixture ASD',tax_code='12345678901',additional_sections=[])
                    if not create_card:SportAssociationMembershipCardConfiguration.objects.create(sport_association=association)
            changed=username!=username.upper() and outcome not in ('invalid_credentials','not_found')
            expected=None
            if not outcome:
                expected={'expires_in':14400,'token_type':'Bearer','role':'administrator' if admin else 'athlete' if role==2 else 'association',
                    'user_data':base_profile(id,role,admin,None,role==1 and not admin)}
                expected['user_data'].update(username=username.upper(),email=user.email)
                if admin:expected['user_data']['is_superuser']=True;expected['requires_welcome']=False
                elif role in (1,3):
                    if role==3:expected['requires_welcome']=False
                    expected['user_data']['sport_association']=association_profile(str(association.pk),None,None,None)
                    # The collaborator owner is newly joined; the serializer treats
                    # associations younger than five days as reviewed.
                    if role==3:expected['user_data']['sport_association']['reviewed']=True
                    expected['user_data']['temporary_invoice_deletion']=True
                    expected['user_data']['preview_and_custom_features']=[{'name':'Iscrizione per famiglia','description':''}]
                    expected['tables_settings']={}
            cases.append({'name':name,'id':id,'identifier':user.email,'password':supplied,'otp':otp,'present':present,'role':role,
                'queries':queries,
                'outcome':outcome,'before':username,'after':username.upper() if changed else username,'changed':changed,
                'delete_before':delete_on.isoformat() if delete_on else None,
                'delete_after':None if not outcome and delete_days is not None and delete_days>=0 else delete_on.isoformat() if delete_on else None,
                'association':str(association.pk) if association else None,'create_card':create_card,'expected':expected})
        add('athlete complete session')
        add('association creates default card',role=1,create_card=True,queries=18)
        add('association existing card',role=1,uppercase=True,queries=11)
        add('collaborator owner association',role=3,queries=14)
        add('administrator avoids association',role=1,admin=True)
        add('valid OTP',two_fa=True,otp='287082',present=True)
        add('missing OTP retains normalization',two_fa=True,outcome='required',role=1,create_card=True,queries=6)
        add('wrong OTP retains normalization',two_fa=True,otp='287083',present=True,outcome='invalid_otp',queries=5)
        add('wrong password prevents normalization',supplied='wrong',outcome='invalid_credentials',queries=1)
        add('disabled account',active=False,outcome='invalid_credentials',queries=1)
        add('deleted account',deleted=True,outcome='not_found',queries=1)
        add('unsupported role retains normalization',role=4,outcome='role',queries=5)
        add('missing association retains normalization',role=1,missing_association=True,outcome='association',queries=6)
        add('future deletion clears after response',delete_days=1,queries=10)
        add('today deletion clears after response',delete_days=0,queries=10)
        add('past deletion retained',delete_days=-1)
        add('historical zero role',role=0)
        add('final database write fails after card creation',role=1,create_card=True,outcome='finalization',queries=18)
        with connection.cursor() as cursor:
            cursor.execute("""CREATE FUNCTION session_reject_finalization() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.user_id='00000000-0000-4000-8000-000000611017'::uuid AND NEW.last_login IS NOT NULL THEN
                  RAISE EXCEPTION 'synthetic finalization failure' USING ERRCODE='23514';
                END IF; RETURN NEW; END $$""")
            cursor.execute('CREATE TRIGGER session_reject_finalization BEFORE UPDATE OF last_login ON bakney_user FOR EACH ROW EXECUTE FUNCTION session_reject_finalization()')
        content_type=ContentType.objects.get_for_model(User)
        def snapshot():
            return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        before=snapshot()
        def check_and_reset(case):
            user=User.original_objects.get(pk=case['id'])
            assert user.username==case['after'],(case['name'],'username')
            assert user.last_login==(now if not case['outcome'] else None),(case['name'],'last_login')
            assert (user.delete_on.isoformat() if user.delete_on else None)==case['delete_after'],(case['name'],'delete_on')
            entries=list(LogEntry.objects.filter(content_type=content_type,object_pk=case['id']).order_by('id'))
            changes=[]
            if case['changed']:changes.append({'username':[case['before'],case['after']]})
            if case['delete_before']!=case['delete_after']:changes.append({'delete_on':[case['delete_before'],'None']})
            assert [e.changes_dict for e in entries]==changes,(case['name'],'audit changes')
            for entry in entries:
                assert entry.action==1 and entry.actor_id is None and entry.remote_addr=='127.0.0.1' and entry.object_repr=='name: First Last',(case['name'],'audit attribution')
                indexes=list(AuditLogIndex.objects.filter(log_entry=entry))
                own=case['association'] if case['role']==1 else None
                assert len(indexes)==int(own is not None),(case['name'],'audit index count')
                if own:assert str(indexes[0].sport_association_id)==own and indexes[0].resolution_path=='user.sport_association'
            if case['create_card']:
                cards=SportAssociationMembershipCardConfiguration.objects.filter(sport_association_id=case['association'])
                assert cards.count()==int(case['outcome'] in ('','finalization')),(case['name'],'card mutation order')
                if case['outcome'] in ('','finalization'):
                    card=cards.get();event=LogEntry.objects.get(object_pk=str(card.pk))
                    assert event.action==0 and event.changes_dict['sport_association']==['None',case['association']]
                    index=AuditLogIndex.objects.get(log_entry=event)
                    assert str(index.sport_association_id)==case['association'] and index.resolution_path=='direct'
                    with disable_auditlog():card.delete();event.delete()
            User.original_objects.filter(pk=case['id']).update(username=case['before'],last_login=None,delete_on=case['delete_before'])
            LogEntry.objects.filter(content_type=content_type,object_pk=case['id']).delete()
        def check_tokens(case,data):
            claims=[]
            for name,kind,lifetime in [('access_token','access',14400),('refresh_token','refresh',2592000)]:
                claim=jwt.decode(data.pop(name),settings.SIMPLE_JWT['VERIFYING_KEY'],algorithms=['EdDSA'],audience='bakney',issuer='https://bakney.com')
                assert claim['user_id']==case['id'] and claim['role']==case['role'] and claim['email']==case['identifier']
                assert claim['token_type']==kind and claim['iat']==int(now.timestamp()) and claim['exp']==int(now.timestamp())+lifetime
                assert len(claim['jti'])==32 and all(c in '0123456789abcdef' for c in claim['jti'])
                claims.append(claim)
            assert claims[0]['jti']!=claims[1]['jti'],'token identifiers reused'
            assert data==case['expected'],(case['name'],'declared full response differs')
        class FrozenDateTime(datetime.datetime):
            @classmethod
            def now(cls,tz=None):return cls.fromtimestamp(59,tz)
        factory=APIRequestFactory()
        with patch('django.utils.timezone.now',return_value=now),patch('rest_framework_simplejwt.tokens.aware_utcnow',return_value=now),patch.object(pyotp.totp,'datetime',SimpleNamespace(datetime=FrozenDateTime)):
            for case in cases:
                payload={'username':case['identifier'],'password':case['password']}
                if case['present']:payload['otp']=case['otp']
                outcome=''
                try:response=AuditlogMiddleware(oauth2_login)(factory.post('/oauth2/login',payload,format='json'))
                except IndexError:outcome='role'
                except SportAssociation.DoesNotExist:outcome='association'
                except IntegrityError:outcome='finalization'
                else:
                    if response.status_code==200:
                        data=json.loads(JSONRenderer().render(response.data));check_tokens(case,data)
                        cookie=response.cookies['BKN_AUTH']
                        assert cookie.value==response.data['access_token'] and cookie['httponly'] and cookie['secure'] and cookie['samesite']=='Strict' and cookie['path']=='/' and cookie['max-age']==14400
                    else:
                        assert response.status_code==401
                        outcome={'OTP code required.':'required','OTP code not valid.':'invalid_otp','invalid credentials':'invalid_credentials','invalid credential (not found)':'not_found'}.get(response.data.get('msg',response.data.get('error')),'unexpected')
                        assert not response.cookies
                assert outcome==case['outcome'],(case['name'],'Django outcome',outcome)
                check_and_reset(case)
        assert snapshot()==before,'Django complete login changed undeclared state'
        print(f'Django: {len(cases)} complete password session cases passed',flush=True)
        fixture=work/'password-session.json';result=work/'password-session-results.json'
        fixture.write_text(json.dumps({'now':now.isoformat(),'private_key':settings.SIMPLE_JWT['SIGNING_KEY'],'public_key':settings.SIMPLE_JWT['VERIFYING_KEY'],'cases':cases}));fixture.chmod(0o600)
        try:
            subprocess.run(['go','test','-race','-timeout=5m','-tags=sessionintegration','./internal/service','-run','^TestPasswordSessionPostgres$'],cwd=ROOT/'BE_GO',check=True,timeout=330,
                env={**tool_env,'ASSOZETA_PASSWORD_SESSION_FIXTURE':str(fixture),'ASSOZETA_PASSWORD_SESSION_RESULT':str(result),'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
            results=json.loads(result.read_text());assert len(results)==len(cases)
            for case,data in zip(cases,results):
                if not case['outcome']:check_tokens(case,data)
                else:assert data is None,'Go exposed response after failure'
                check_and_reset(case)
            assert snapshot()==before,'Go complete login changed undeclared state'
        finally:
            fixture.unlink(missing_ok=True);result.unlink(missing_ok=True)
            with connection.cursor() as cursor:
                cursor.execute('DROP TRIGGER session_reject_finalization ON bakney_user')
                cursor.execute('DROP FUNCTION session_reject_finalization()')
    return len(cases)
