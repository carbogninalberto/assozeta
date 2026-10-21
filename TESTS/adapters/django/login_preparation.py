"""Django login/audit middleware versus composed Go password admission.

Exercises only disposable accounts; checks exact durable username/audit/index
fields, then restores the fixture. Final response/last-login Go writes are not
implemented by this admission stage and are not counted as parity.
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
    from django.contrib.contenttypes.models import ContentType
    from django.test import override_settings
    from django.db import IntegrityError
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
    password = 'synthetic-audit-login-only'
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    cases = []
    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS, RATELIMIT_ENABLE=False):
        encoded = make_password(password)
        with disable_auditlog():
            actor = User.original_objects.create(user_id=uid(50000),username='AUDIT-ACTOR',email='audit-actor@example.test',role=1,password='!test-only')
            actor_association = SportAssociation.original_objects.create(sport_association_id=uid(51000),user=actor,denomination='Audit actor')
            collaborator = User.original_objects.create(user_id=uid(50001),username='AUDIT-COLLABORATOR',email='audit-collaborator@example.test',role=3,connected_user=actor,password='!test-only')
        def add(name, username, normalized, *, role=2, own=False, deleted_association=False, audit_actor=None,
                active=True, deleted=False, supplied=password, otp=None, present=False, two_fa=True, outcome='required', network=False):
            index = len(cases)
            with disable_auditlog():
                user = User.original_objects.create(user_id=uid(50100+index),username=username,email=f'audit-login-{index}@example.test',
                    first_name='First',last_name='Last',password=encoded,role=role,is_active=active,deleted=deleted,
                    connected_user=actor if role==3 else None,two_fa=two_fa,two_fa_secret=secret)
                association = SportAssociation.original_objects.create(sport_association_id=uid(51100+index),user=user,
                    denomination='Audit login fixture',deleted=deleted_association) if own else None
            changed = username != normalized and outcome not in ('invalid_credentials','not_found')
            association_id = str(association.pk) if association and not deleted_association else (str(actor_association.pk) if audit_actor==actor else None)
            path = ('user.sport_association' if association and not deleted_association else 'actor.sport_association') if association_id else None
            metadata = {'actor_id':str(audit_actor.pk) if audit_actor else None,'actor_email':audit_actor.email if audit_actor else None,
                'remote_address':'2001:db8::1' if network else '127.0.0.1','remote_port':8443 if network else None,
                'correlation_id':'synthetic-audit-correlation' if network else None}
            cases.append({'name':name,'id':str(user.pk),'identifier':user.email,'password':supplied,'before':username,
                'after':normalized if changed else username,'changed':changed,'outcome':outcome,'otp':otp,'present':present,
                'audit':metadata,'association':association_id if changed else None,'resolution':path if changed else None})
        add('athlete normalization before missing OTP','Audit-Athlete','AUDIT-ATHLETE')
        add('unchanged username does not audit','AUDIT-UNCHANGED','AUDIT-UNCHANGED')
        add('Unicode expansion','Straße-Audit','STRASSE-AUDIT')
        add('Unicode ligature expansion','ﬃ-audit','FFI-AUDIT')
        add('owner association index','Audit-Owner','AUDIT-OWNER',role=1,own=True)
        add('deleted association excluded','Audit-Deleted-Association','AUDIT-DELETED-ASSOCIATION',role=1,own=True,deleted_association=True)
        add('collaborator does not inherit owner index','Audit-Collaborator-Target','AUDIT-COLLABORATOR-TARGET',role=3)
        add('actor association fallback','Audit-Actor-Fallback','AUDIT-ACTOR-FALLBACK',audit_actor=actor)
        add('target association precedes actor','Audit-Own-Priority','AUDIT-OWN-PRIORITY',role=1,own=True,audit_actor=actor)
        add('collaborator actor has no direct association','Audit-Collaborator-Actor','AUDIT-COLLABORATOR-ACTOR',audit_actor=collaborator)
        add('request audit metadata','Audit-Network','AUDIT-NETWORK',audit_actor=actor,network=True)
        add('disabled two-factor successful admission','Audit-No-OTP','AUDIT-NO-OTP',two_fa=False,outcome='')
        add('valid OTP successful admission','Audit-Valid-OTP','AUDIT-VALID-OTP',otp='287082',present=True,outcome='')
        add('invalid OTP retains normalization','Audit-Invalid-OTP','AUDIT-INVALID-OTP',otp='287083',present=True,outcome='invalid_otp')
        add('unchanged username with actor','AUDIT-UNCHANGED-ACTOR','AUDIT-UNCHANGED-ACTOR',audit_actor=actor)
        add('inactive account unchanged','Audit-Inactive','AUDIT-INACTIVE',active=False,outcome='invalid_credentials')
        add('deleted account unchanged','Audit-Deleted','AUDIT-DELETED',deleted=True,outcome='not_found')
        add('wrong password unchanged','Audit-Wrong-Password','AUDIT-WRONG-PASSWORD',supplied='incorrect',outcome='invalid_credentials')
        add('OTP type failure retains normalization','Audit-Null-OTP','AUDIT-NULL-OTP',otp=None,present=True,outcome='server_error')
        add('empty username unchanged','','')
        with disable_auditlog():
            atomic_user = User.original_objects.create(user_id=uid(52000),username='Audit-Atomic',email='audit-atomic@example.test',password=encoded,role=1)
            SportAssociation.original_objects.create(sport_association_id=uid(52100),user=atomic_user,denomination='Atomic audit fixture')
            User.original_objects.create(user_id=uid(52001),username='AUDIT-COLLISION',email='audit-collision-existing@example.test',password=encoded)
            User.original_objects.create(user_id=uid(52002),username='Audit-Collision',email='audit-collision-target@example.test',password=encoded)
            User.original_objects.create(user_id=uid(52003),username='Audit-Atomic-Peer',email='audit-atomic-peer@example.test',password=encoded)
        content_type = ContentType.objects.get_for_model(User)
        def snapshot():
            return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        before = snapshot()
        def check_audit(case, started):
            user = User.original_objects.get(pk=case['id'])
            assert user.username == case['after'], f"{case['name']}: username differs"
            entries = list(LogEntry.objects.filter(content_type=content_type,object_pk=case['id']))
            assert len(entries) == int(case['changed']), f"{case['name']}: incorrect audit count"
            if not entries:
                return
            entry = entries[0]
            expected = {'content_type_id':content_type.pk,'object_pk':case['id'],'object_id':None,'object_repr':'name: First Last',
                'serialized_data':None,'action':1,'changes_text':'','changes':{'username':[case['before'],case['after']]},
                'actor_id':case['audit']['actor_id'],'cid':case['audit']['correlation_id'],'remote_addr':case['audit']['remote_address'],
                'remote_port':case['audit']['remote_port'],'additional_data':None,'actor_email':case['audit']['actor_email']}
            actual = LogEntry.objects.filter(pk=entry.pk).values().get()
            actual.pop('id'); timestamp = actual.pop('timestamp')
            if actual['actor_id'] is not None: actual['actor_id'] = str(actual['actor_id'])
            assert actual == expected, f"{case['name']}: audit fields differ: {sorted(k for k in expected if actual.get(k)!=expected[k])}"
            assert started <= timestamp <= timezone.now(), 'audit timestamp outside operation'
            indexes = list(AuditLogIndex.objects.filter(log_entry=entry))
            assert len(indexes) == int(case['association'] is not None), f"{case['name']}: audit index count differs"
            if indexes:
                item = indexes[0]
                assert str(item.sport_association_id)==case['association'] and item.resolution_path==case['resolution'], 'audit association/path differs'
                assert timestamp <= item.created_at <= timezone.now() and item.pk.version==4, 'invalid generated index metadata'
                assert item.log_entry.changes_dict == expected['changes'], 'Django cannot process written audit changes'
        def reset(case):
            User.original_objects.filter(pk=case['id']).update(username=case['before'],last_login=None)
            LogEntry.objects.filter(content_type=content_type,object_pk=case['id']).delete()
        class FrozenDateTime(datetime.datetime):
            @classmethod
            def now(cls,tz=None): return cls.fromtimestamp(59,tz)
        factory = APIRequestFactory()
        for case in cases:
            payload={'username':case['identifier'],'password':case['password']}
            if case['present']: payload['otp']=case['otp']
            headers={}
            if case['audit']['correlation_id']:
                headers={'HTTP_X_CORRELATION_ID':case['audit']['correlation_id'],'HTTP_X_FORWARDED_FOR':'[2001:db8::1]:1234, 10.0.0.1','HTTP_X_FORWARDED_PORT':'8443'}
            request=factory.post('/oauth2/login',payload,format='json',**headers)
            if case['audit']['actor_id']: request.user=User.original_objects.get(pk=case['audit']['actor_id'])
            started=timezone.now()
            outcome=''
            try:
                with patch.object(pyotp.totp,'datetime',SimpleNamespace(datetime=FrozenDateTime)):
                    response=AuditlogMiddleware(oauth2_login)(request)
            except TypeError:
                outcome='server_error'
            else:
                if response.status_code==200:
                    assert str(AccessToken(response.data['access_token'])['user_id'])==case['id'], 'login selected wrong account'
                else:
                    outcomes={"OTP code required.":'required',"OTP code not valid.":'invalid_otp',
                        'invalid credentials':'invalid_credentials','invalid credential (not found)':'not_found'}
                    assert response.status_code==401, f"{case['name']}: unexpected Django status"
                    outcome=outcomes.get(response.data.get('msg',response.data.get('error')),'unexpected')
            assert outcome==case['outcome'], f"{case['name']}: incorrect Django admission outcome"
            check_audit(case,started)
            assert (User.original_objects.get(pk=case['id']).last_login is not None)==(outcome==''), 'Django last-login order changed'
            reset(case)
        # Django auditlog emits pre-save outside an encompassing transaction:
        # a unique-name failure retains an event for a write that never happened.
        # Go's required invariant transaction deliberately rolls this back.
        collision_request=factory.post('/oauth2/login',{'username':'audit-collision-target@example.test','password':password},format='json')
        try:
            AuditlogMiddleware(oauth2_login)(collision_request)
        except IntegrityError:
            pass
        else:
            raise AssertionError('Django username collision unexpectedly succeeded')
        collision_entries=list(LogEntry.objects.filter(content_type=content_type,object_pk=uid(52002)))
        assert len(collision_entries)==1 and collision_entries[0].changes_dict=={'username':['Audit-Collision','AUDIT-COLLISION']}, 'Django pre-save collision audit behavior changed'
        assert User.original_objects.get(pk=uid(52002)).username=='Audit-Collision', 'collision changed username'
        LogEntry.objects.filter(pk=collision_entries[0].pk).delete()
        assert snapshot()==before, 'Django admission characterization left undeclared writes'
        print(f'Django: {len(cases)} composed credential/normalization/audit/OTP cases passed',flush=True)
        path=work/'login-preparation.json';path.write_text(json.dumps({'cases':cases}));path.chmod(0o600)
        try:
            started=timezone.now()
            subprocess.run(['go','test','-race','-timeout=5m','-tags=admissionintegration','./internal/service','-run','^TestPasswordAdmissionAgainstDjango|^TestLoginPreparationPostgres'],
                cwd=ROOT/'BE_GO',check=True,timeout=330,env={**tool_env,'ASSOZETA_LOGIN_PREPARATION_FIXTURE':str(path),
                    'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
            for case in cases:
                check_audit(case,started)
                assert User.original_objects.get(pk=case['id']).last_login is None, 'Go admission performed final login writes'
                reset(case)
            assert snapshot()==before, 'Go admission changed undeclared fields/tables'
        finally:
            path.unlink(missing_ok=True)
    return len(cases)
