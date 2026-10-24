"""Actual password login final writes, followed by Go writes read back by Django.

A component contract: public Go login, full response and session delivery remain
unimplemented. Fixed business clocks do not replace credential or JWT code.
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
    from django.db import connection, ProgrammingError
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from auditlog.middleware import AuditlogMiddleware
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration
    from application.models.audit_models import AuditLogIndex
    from application.views import auth_views
    from rest_framework.test import APIRequestFactory
    from rest_framework_simplejwt.tokens import AccessToken
    password = 'synthetic-final-login-only'
    cases = []
    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS, RATELIMIT_ENABLE=False):
        encoded = make_password(password)
        with disable_auditlog():
            actor = User.original_objects.create(user_id=uid(80000), username='FINAL-ACTOR', email='final-actor@example.test', role=1, password='!test-only')
            actor_association = SportAssociation.original_objects.create(sport_association_id=uid(81000), user=actor, denomination='Final actor')
            SportAssociationMembershipCardConfiguration.objects.create(sport_association=actor_association)
            collaborator_actor = User.original_objects.create(user_id=uid(80001), username='FINAL-COLLABORATOR-ACTOR', role=3, connected_user=actor, password='!test-only')
        def add(name, date, clear, *, first='2026-10-21T12:00:00+00:00', second=None, role=2, own=False, deleted_association=False, audit_actor=None, superuser=False, previous=None):
            n = len(cases)
            with disable_auditlog():
                user = User.original_objects.create(user_id=uid(80100+n), username=f'FINAL-TARGET-{n}', email=f'final-{n}@example.test',
                    first_name='First', last_name='Last', password=encoded, role=role, delete_on=date, last_login=previous,
                    is_superuser=superuser, connected_user=actor if role==3 else None)
                association = SportAssociation.original_objects.create(sport_association_id=uid(81100+n), user=user, denomination='Final target', deleted=deleted_association) if own else None
                if role==1 and association:
                    SportAssociationMembershipCardConfiguration.objects.create(sport_association=association)
            indexed = association if association and not deleted_association else actor_association if audit_actor==actor else None
            metadata = {'actor_id':str(audit_actor.pk) if audit_actor else None, 'actor_email':audit_actor.email if audit_actor else None,
                'remote_address':'2001:db8::51' if audit_actor else '127.0.0.1', 'remote_port':8443 if audit_actor else None,
                'correlation_id':'synthetic-final-correlation' if audit_actor else None}
            cases.append({'name':name, 'id':str(user.pk), 'date':date, 'clear':clear, 'first':first, 'second':second or first,
                'previous':previous, 'audit':metadata, 'association':str(indexed.pk) if indexed and clear else None,
                'resolution':('user.sport_association' if association and not deleted_association else 'actor.sport_association') if indexed and clear else None})
        add('null remains null', None, False)
        add('past remains scheduled', '2026-10-20', False)
        add('today cancels', '2026-10-21', True)
        add('future cancels', '2026-10-22', True)
        add('leap-day past', '2024-02-29', False)
        add('far future', '9999-12-31', True)
        add('UTC date before Rome midnight', '2026-10-21', True, first='2026-10-21T22:30:00+00:00')
        add('UTC next day', '2026-10-21', False, first='2026-10-22T00:00:00+00:00')
        add('midnight between clock reads', '2026-10-21', False, first='2026-10-21T23:59:59.999999+00:00', second='2026-10-22T00:00:00+00:00')
        add('existing timestamp overwritten', None, False, previous='2025-01-02T03:04:05+00:00')
        add('owner index', '2026-10-21', True, role=1, own=True)
        add('actor fallback', '2026-10-21', True, audit_actor=actor)
        add('own index precedes actor', '2026-10-21', True, own=True, audit_actor=actor)
        add('deleted association falls back', '2026-10-21', True, own=True, deleted_association=True, audit_actor=actor)
        add('collaborator does not inherit owner index', '2026-10-21', True, role=3)
        add('collaborator actor has no owner index', '2026-10-21', True, audit_actor=collaborator_actor)
        add('administrator finalizes', '2026-10-21', True, superuser=True)
        with disable_auditlog():
            atomic = User.original_objects.create(user_id=uid(82000), username='FINAL-ATOMIC', first_name='First', last_name='Last', role=2,
                password=encoded, delete_on='2026-10-21')
            SportAssociation.original_objects.create(sport_association_id=uid(82100), user=atomic, denomination='Atomic finalization')
        ct = ContentType.objects.get_for_model(User)
        def snapshot():
            return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        before = snapshot()
        def reset(case):
            User.original_objects.filter(pk=case['id']).update(delete_on=case['date'], last_login=case.get('previous'))
            LogEntry.objects.filter(content_type=ct, object_pk=case['id']).delete()
        def check(case):
            user = User.original_objects.get(pk=case['id'])
            assert user.last_login == datetime.datetime.fromisoformat(case['first']), f"{case['name']}: last_login differs"
            assert (user.delete_on.isoformat() if user.delete_on else None) == (None if case['clear'] else case['date']), f"{case['name']}: delete_on differs"
            entries = list(LogEntry.objects.filter(content_type=ct, object_pk=case['id']).values())
            assert len(entries)==int(case['clear']), f"{case['name']}: last_login incorrectly audited or cancellation missing"
            if not entries: return
            actual = entries[0]; entry_id = actual.pop('id'); timestamp = actual.pop('timestamp')
            assert timestamp.tzinfo is not None, 'audit timestamp must be timezone-aware'
            if actual['actor_id']: actual['actor_id'] = str(actual['actor_id'])
            expected = {'content_type_id':ct.pk, 'object_pk':case['id'], 'object_id':None, 'object_repr':'name: First Last',
                'serialized_data':None, 'action':1, 'changes_text':'', 'changes':{'delete_on':[case['date'],'None']},
                'actor_id':case['audit']['actor_id'], 'cid':case['audit']['correlation_id'], 'remote_addr':case['audit']['remote_address'],
                'remote_port':case['audit']['remote_port'], 'additional_data':None, 'actor_email':case['audit']['actor_email']}
            assert actual==expected, f"{case['name']}: audit fields differ: {[k for k in expected if actual.get(k)!=expected[k]]}"
            indexes = list(AuditLogIndex.objects.filter(log_entry_id=entry_id))
            assert len(indexes)==int(case['association'] is not None), 'wrong index count'
            if indexes:
                index = indexes[0]
                assert str(index.sport_association_id)==case['association'] and index.resolution_path==case['resolution'], 'wrong index scope'
                assert index.pk.version==4 and index.created_at>=timestamp, 'invalid index metadata'
                assert index.log_entry.changes_dict==expected['changes'], 'Go-written audit not readable by Django'
        factory = APIRequestFactory()
        def login(case):
            user = User.original_objects.get(pk=case['id'])
            headers = {}
            if case['audit']['actor_id']:
                headers={'HTTP_X_CORRELATION_ID':'synthetic-final-correlation', 'HTTP_X_FORWARDED_FOR':'[2001:db8::51]:1234', 'HTTP_X_FORWARDED_PORT':'8443'}
            request=factory.post('/oauth2/login', {'username':user.username, 'password':password}, format='json', **headers)
            if case['audit']['actor_id']: request.user=User.original_objects.get(pk=case['audit']['actor_id'])
            clock = iter([datetime.datetime.fromisoformat(case['first']), datetime.datetime.fromisoformat(case['second'])])
            with patch.object(auth_views, 'timezone', SimpleNamespace(now=lambda:next(clock))):
                return AuditlogMiddleware(auth_views.oauth2_login)(request)
        for case in cases:
            response = login(case)
            assert response.status_code==200 and str(AccessToken(response.data['access_token'])['user_id'])==case['id'], 'actual Django login failed'
            assert response.cookies['BKN_AUTH'].value==response.data['access_token'], 'Django session delivery changed'
            check(case); reset(case)
        assert snapshot()==before, 'Django finalization left undeclared writes'
        # Actual permission failures expose Django autocommit ordering: denied
        # user UPDATE leaves a pre-save audit; denied index is swallowed.
        with connection.cursor() as cursor:
            for role in ('final_user_denied','final_index_denied'):
                cursor.execute(f'CREATE ROLE {role} NOLOGIN')
                cursor.execute(f'GRANT USAGE ON SCHEMA public TO {role}')
                cursor.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}')
                cursor.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}')
                cursor.execute(f'GRANT INSERT ON auditlog_logentry TO {role}')
            cursor.execute('GRANT INSERT ON application_auditlogindex TO final_user_denied')
            cursor.execute('GRANT UPDATE ON bakney_user TO final_index_denied')
        atomic_case={'id':str(atomic.pk),'date':'2026-10-21','first':'2026-10-21T12:00:00+00:00','second':'2026-10-21T12:00:00+00:00','audit':{'actor_id':None}}
        for role in ('final_user_denied','final_index_denied'):
            try:
                with connection.cursor() as cursor: cursor.execute(f'SET ROLE {role}')
                response=login(atomic_case)
                assert role=='final_index_denied' and response.status_code==200, 'unexpected permission success'
            except ProgrammingError as exc:
                assert role=='final_user_denied' and getattr(exc.__cause__,'pgcode',getattr(exc.__cause__,'sqlstate',None))=='42501', 'unexpected database failure'
            finally:
                with connection.cursor() as cursor: cursor.execute('RESET ROLE')
            row=User.original_objects.get(pk=atomic.pk)
            assert (row.last_login is None)==(role=='final_user_denied'), 'Django save failure boundary changed'
            assert LogEntry.objects.filter(content_type=ct,object_pk=str(atomic.pk)).count()==1, 'Django pre-save audit missing'
            assert AuditLogIndex.objects.filter(log_entry__object_pk=str(atomic.pk)).count()==int(role=='final_user_denied'), 'Django index failure behavior changed'
            reset(atomic_case)
        assert snapshot()==before, 'Django failure rehearsal left undeclared writes'
        # Characterize the stale full-model save too. A change after response
        # construction is overwritten by Django; Go deliberately updates only
        # the two finalization columns and uses current row data for audit.
        build_response = auth_views.JWTTokenService.build_login_response
        def build_then_change(user, tokens):
            content = build_response(user, tokens)
            User.original_objects.filter(pk=user.pk).update(password='!changed-concurrently', first_name='Fresh', is_active=False)
            return content
        with patch.object(auth_views.JWTTokenService, 'build_login_response', side_effect=build_then_change):
            response = login(atomic_case)
        assert response.status_code==200, 'Django stale save characterization failed'
        row = User.original_objects.get(pk=atomic.pk)
        assert row.password==encoded and row.first_name=='First' and row.is_active, 'Django stale overwrite behavior changed'
        entry = LogEntry.objects.get(content_type=ct, object_pk=str(atomic.pk))
        assert entry.changes_dict=={'first_name':['Fresh','First'], 'is_active':['False','True'], 'delete_on':['2026-10-21','None']}, 'Django stale save audit changed'
        reset(atomic_case)
        assert snapshot()==before, 'Django stale-save rehearsal left undeclared writes'
        print(f'Django: {len(cases)} final login cases and permission/stale-save failure boundaries passed', flush=True)
        path=work/'login-finalization.json'; path.write_text(json.dumps({'cases':cases,'atomic':str(atomic.pk)})); path.chmod(0o600)
        try:
            subprocess.run(['go','test','-race','-tags=finalizationintegration','./internal/service','-run','^TestLoginFinalizationPostgres'],
                cwd=ROOT/'BE_GO',check=True,timeout=180,env={**tool_env,'ASSOZETA_LOGIN_FINALIZATION_FIXTURE':str(path),
                'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
            for case in cases: check(case); reset(case)
            assert snapshot()==before, 'Go finalization changed undeclared fields/tables'
        finally: path.unlink(missing_ok=True)
    return len(cases)
