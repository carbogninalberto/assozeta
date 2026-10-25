"""Focused actual-HTTP deletion characterization in an owned current-schema DB.

The original global-email deletion and partial-commit failures are retained in
local red receipts. This contract requires owner scope and atomic audit writes;
it does not establish complete cascade, invitation acceptance, or Go parity.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import threading
import unittest
from uuid import UUID

ROOT=Path(__file__).resolve().parents[3]


def uid(number):return f'a2000000-0000-4000-8000-{number:012d}'


def run(work,log,tool_env,address):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.core.asgi import get_asgi_application
    from django.db import connections
    from application.models import User,SportAssociation
    from application.models.user_models import CollaborationInvites
    from application.models.audit_models import AuditLogIndex
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.contracts.test_collaborator_delete import assert_case
    from TESTS.adapters.django.impersonation_effects import assert_retained_rows
    from TESTS.http_client import poll
    import redis
    import uvicorn

    output=ROOT/'quality-reports/go-migration/collaborator-delete';output.mkdir(parents=True,exist_ok=True)
    report={'success':False,'cleanup_verified':False,'complete_collaborator_delete_parity':False,'cases':[],'lock_cases':[],
        'scope':'Focused corrected ownership and atomic deletion contract; full parity unverified'}
    receipt=output/'http-results.json';receipt.write_text(json.dumps(report)+'\n')
    def hashes():
        paths=set((ROOT/'BE').rglob('*.py'))
        paths.update(ROOT/p for p in ('TESTS/adapters/django/collaborator_delete.py','TESTS/adapters/django/collaborator_delete_locks.py','TESTS/contracts/test_collaborator_delete.py','TESTS/contracts/test_impersonation_management.py','TESTS/adapters/django/accounts.py','TESTS/adapters/django/settings.py','TESTS/adapters/django/impersonation_effects.py','TESTS/http_client.py'))
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    sources=hashes()
    models=[m for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy]
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in models}
    host,port=address.split(':');raw=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    def cache_snapshot():return {key:raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}
    original,original_cache=snapshot(),cache_snapshot();created=[];new_audits=[]
    def seed(model,number,**values):
        assert not model._base_manager.filter(pk=uid(number)).exists()
        row=model(pk=UUID(uid(number)),**values);model._base_manager.bulk_create([row]);created.append((model,row.pk));return row
    def person(number,role,**values):return seed(User,number,username=f'delete-owned-{number}',email=values.pop('email',f'delete-owned-{number}@example.test'),password='!synthetic',role=role,first_name='Owned',last_name=f'Person{number}',**values)
    @contextmanager
    def fault(kind,target,invite):
        if kind is None:
            yield None;return
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT current_database()');assert cursor.fetchone()[0]=='assozeta_contract_accounts'
            assert str(connections['default'].settings_dict['HOST']).startswith('/tmp/az-accounts-')
            cursor.execute("SELECT count(*) FROM pg_proc WHERE proname='az_collaborator_delete_fault'");assert cursor.fetchone()[0]==0
            if kind=='user-delete':table='bakney_user';event='DELETE';predicate=f"OLD.user_id='{target.pk}'::uuid"
            elif kind=='invite-delete':table='application_collaborationinvites';event='DELETE';predicate=f"OLD.collaboration_invite_id='{invite.pk}'::uuid"
            elif kind.endswith('-audit'):
                table='auditlog_logentry';event='INSERT';identifier=target.pk if kind.startswith('user') else invite.pk
                predicate=f"NEW.object_pk='{identifier}' AND NEW.action=2"
            else:
                assert kind in ('user-index','invite-index')
                table='application_auditlogindex';event='INSERT';identifier=target.pk if kind.startswith('user') else invite.pk
                predicate=f"(SELECT object_pk FROM auditlog_logentry WHERE id=NEW.log_entry_id)='{identifier}'"
            cursor.execute(f"CREATE FUNCTION az_collaborator_delete_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {predicate} THEN RAISE EXCEPTION 'AZ_COLLABORATOR_DELETE_FAULT'; END IF; RETURN {'OLD' if event=='DELETE' else 'NEW'}; END $$")
            cursor.execute(f'CREATE TRIGGER az_collaborator_delete_fault BEFORE {event} ON {table} FOR EACH ROW EXECUTE FUNCTION az_collaborator_delete_fault()')
        offset=(work/'postgres.log').stat().st_size
        evidence={'kind':kind,'observed':False,'cleanup_verified':False}
        try:
            yield evidence
            evidence['observed']='AZ_COLLABORATOR_DELETE_FAULT' in (work/'postgres.log').read_text()[offset:]
            assert evidence['observed'],'Real PostgreSQL fault was not observed'
        finally:
            with connections['default'].cursor() as cursor:
                cursor.execute(f'DROP TRIGGER az_collaborator_delete_fault ON {table}')
                cursor.execute('DROP FUNCTION az_collaborator_delete_fault()')
                cursor.execute("SELECT count(*) FROM pg_proc WHERE proname='az_collaborator_delete_fault'")
                evidence['cleanup_verified']=cursor.fetchone()[0]==0
            assert evidence['cleanup_verified']
    test=unittest.TestCase();server=None;thread=None
    try:
        owner=person(710001,1);foreign=person(710002,1)
        association=seed(SportAssociation,711001,user=owner,denomination='Delete fixture owner')
        seed(SportAssociation,711002,user=foreign,denomination='Delete fixture foreign')
        server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start();poll(lambda:server.started,bool,timeout=15)
        port=server.servers[0].sockets[0].getsockname()[1];base=f'http://127.0.0.1:{port}'
        scenarios=[
            ('owned-user',None,False,None,200,True,False),
            ('unaccepted-invite',False,True,None,200,False,True),
            ('accepted-owned-invite',True,False,None,200,True,True),
            ('accepted-foreign-email',True,True,None,200,False,True),
            ('accepted-own-and-foreign-email',True,True,None,200,False,True),
            ('user-delete-failure',None,False,'user-delete',500,False,False),
            ('user-audit-failure',True,False,'user-audit',500,False,False),
            ('invite-delete-failure',True,False,'invite-delete',500,False,False),
            ('invite-audit-failure',True,False,'invite-audit',500,False,False),
            ('user-index-failure',True,False,'user-index',500,False,False),
            ('invite-index-failure',True,False,'invite-index',500,False,False),
            ('direct-index-failure',None,False,'user-index',500,False,False),
        ]
        for index,(name,accepted,foreign_target,fault_kind,status,deleted_user,deleted_invite) in enumerate(scenarios):
            target=person(712000+index,3,connected_user=foreign if foreign_target else owner)
            invite=None
            if accepted is not None:invite=seed(CollaborationInvites,713000+index,user=owner,email=target.email,accepted=accepted,token='synthetic-owned-invite')
            own_target=person(714000+index,3,connected_user=owner,email=target.email) if name=='accepted-own-and-foreign-email' else None
            identifier=invite.pk if invite else target.pk
            access=AccessToken();access['user_id']=str(owner.pk)
            case={'name':'collaborator-delete-'+name,'path':f'/collaborators/{identifier}/delete','token':str(access),'status':status,'response':{'msg':'collaborator deleted.'} if status==200 else '\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Server Error (500)</title>\n</head>\n<body>\n  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n'}
            before=snapshot();before_cache=cache_snapshot();prior_ids=set(LogEntry.objects.values_list('pk',flat=True))
            with fault(fault_kind,target,invite) as fault_evidence:
                assert_case(test,base,case)
            removed_users={target.pk} if deleted_user else ({own_target.pk} if own_target else set())
            removed={User._meta.label:removed_users,CollaborationInvites._meta.label:{invite.pk} if deleted_invite else set()}
            after=snapshot();audit_rows=list(LogEntry.objects.exclude(pk__in=prior_ids).order_by('pk').values());ids=[row['id'] for row in audit_rows];new_audits.extend(ids)
            expected_objects=sorted(str(pk) for pk in removed_users)
            if deleted_invite:expected_objects.append(str(invite.pk))
            assert len(audit_rows)==len(expected_objects)
            assert sorted(row['object_pk'] for row in audit_rows)==sorted(expected_objects)
            for audit in audit_rows:
                assert audit['action']==2 and str(audit['actor_id'])==str(owner.pk) and audit['cid']==case['name']
                assert audit['changes'] and 'password' not in audit['changes'] and 'last_login' not in audit['changes']
            index_rows=list(AuditLogIndex.objects.filter(log_entry_id__in=ids).values())
            assert len(index_rows)==len(ids)
            assert all(row['sport_association_id']==association.pk and row['resolution_path']=='actor.sport_association' for row in index_rows)
            for model in models:
                label=model._meta.label;pk=model._meta.pk.attname
                if model is LogEntry:assert_retained_rows(before[label],after[label],key=pk,created=ids)
                elif model is AuditLogIndex:assert_retained_rows(before[label],after[label],key=pk,created=[row[pk] for row in index_rows])
                else:
                    expected=[row for row in before[label] if row[pk] not in removed.get(label,set())]
                    assert after[label]==expected,('unexpected durable effects',label)
            assert cache_snapshot()==before_cache
            report['cases'].append({'name':name,'status':status,'fault':fault_evidence,'target_scope':'foreign' if foreign_target else 'owned','target_deleted':deleted_user,'own_same_email_user_deleted':own_target is not None,'invite_deleted':deleted_invite,
                'audit_actions':[row['action'] for row in audit_rows],'audit_objects':[row['object_pk'] for row in audit_rows],
                'audit_field_names':[sorted(row['changes']) for row in audit_rows],'audit_indexes':len(index_rows),'unrelated_models_and_cache_unchanged':True})
            print(f'{name}: HTTP{status}, user_deleted={deleted_user}, invite_deleted={deleted_invite}, audits={len(audit_rows)}',flush=True)
        foreign_user = person(717001, 3, connected_user=foreign)
        owned_user = person(717002, 3, connected_user=owner)
        foreign_invite = seed(CollaborationInvites, 717003, user=foreign,
            email=foreign_user.email, accepted=True, token='synthetic-foreign-invite')
        owner_access = AccessToken(); owner_access['user_id'] = str(owner.pk)
        denials = [
            ('foreign-user', str(foreign_user.pk), 400, ['Collaborator not found'], 'DELETE', str(owner_access)),
            ('foreign-invite', str(foreign_invite.pk), 400, ['Collaborator not found'], 'DELETE', str(owner_access)),
            ('missing', uid(717999), 400, ['Collaborator not found'], 'DELETE', str(owner_access)),
            ('invalid-uuid', 'bad', 400, ['badly formed hexadecimal UUID string'], 'DELETE', str(owner_access)),
            ('anonymous', str(owned_user.pk), 401, {'detail': 'Authentication credentials were not provided.'}, 'DELETE', None),
            ('wrong-method', str(owned_user.pk), 405, {'detail': 'Method "GET" not allowed.'}, 'GET', str(owner_access)),
        ]
        for name, identifier, status, response, method, access in denials:
            case = {'name': 'collaborator-delete-' + name,
                'path': f'/collaborators/{identifier}/delete', 'token': access,
                'status': status, 'response': response, 'method': method}
            before, before_cache = snapshot(), cache_snapshot()
            assert_case(test, base, case)
            assert snapshot() == before and cache_snapshot() == before_cache
            report['cases'].append({'name': name, 'status': status,
                'all_managed_models_and_cache_unchanged': True})
        report['denial_cases'] = len(denials)
        from TESTS.adapters.django.collaborator_delete_locks import run as collaborator_delete_locks
        collaborator_delete_locks(base=base,owner=owner,foreign=foreign,association=association,
            person=person,seed=seed,snapshot=snapshot,cache_snapshot=cache_snapshot,models=models,report=report)
        report.update(success=True,http_cases=len(report['cases']),managed_models=len(models),ownership_boundary='email and resolved owner; original unsafe receipt retained')
    except BaseException as error:report['error_type']=type(error).__name__;raise
    finally:
        errors=[]
        def clean(stage,operation):
            try:operation()
            except BaseException as error:errors.append({'stage':stage,'error_type':type(error).__name__})
        def stop():
            if thread:server.should_exit=True;thread.join(timeout=30);assert not thread.is_alive()
        clean('server',stop)
        # Also capture audit rows created before an assertion failed.
        original_audit_ids={row['id'] for row in original[LogEntry._meta.label]}
        with disable_auditlog():
            clean('audit',lambda:LogEntry.objects.exclude(pk__in=original_audit_ids).delete())
            for model,pk in reversed(created):clean('fixture',lambda model=model,pk=pk:model._base_manager.filter(pk=pk).delete())
        clean('connections',connections.close_all)
        def verify():assert snapshot()==original and cache_snapshot()==original_cache
        clean('state',verify);clean('redis',raw.close)
        report['cleanup_verified']=not errors;report['cleanup_errors']=errors
        report['source_stable']=hashes()==sources;report['source_sha256']=sources
        report['success']=bool(report['success'] and report['cleanup_verified'] and report['source_stable'])
        receipt.write_text(json.dumps(report,indent=2)+'\n')
    assert report['success'];return report
