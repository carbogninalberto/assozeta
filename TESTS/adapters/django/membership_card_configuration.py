"""Login-response membership-card configuration and its exact durable audit."""
import json
import subprocess
from django.http import JsonResponse

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from django.test import RequestFactory
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.middleware import AuditlogMiddleware
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation
    from application.models.user_models import SportAssociationMembershipCardConfiguration as Configuration
    from application.models.audit_models import AuditLogIndex
    from application.services.jwt_token_service import JWTTokenService
    from django.db import connection

    with disable_auditlog():
        actor=User.original_objects.create(user_id=uid(70000),username='CARD-AUDIT-ACTOR',email='card-actor@example.test',role=1,password='!synthetic-unused')
        SportAssociation.original_objects.create(sport_association_id=uid(71000),user=actor,denomination='Actor association')
    cases=[]
    def add(name, *, count=0, template=None, emit=False, json_null=False, audit=False, role=1,
            superuser=False, deleted=False, missing=False, owner_deleted=False, owner_active=True):
        index=len(cases)
        owner_id=uid(70100+index); association_id=uid(71100+index)
        with disable_auditlog():
            owner=User.original_objects.create(user_id=owner_id,username=f'CARD-OWNER-{index}',email=f'card-owner-{index}@example.test',
                role=role if role!=3 else 1,is_superuser=superuser,password='!synthetic-unused',is_active=owner_active,deleted=owner_deleted)
            association=SportAssociation.original_objects.create(sport_association_id=association_id,user=owner,denomination='Card fixture',deleted=deleted) if not missing else None
            user=User.original_objects.create(user_id=uid(70200+index),username=f'CARD-COLLABORATOR-{index}',email=f'card-collaborator-{index}@example.test',
                role=3,connected_user=owner,password='!synthetic-unused') if role==3 else owner
            if association:
                # Insert largest UUID first. The smallest UUID still wins .first().
                for ordinal in range(count,0,-1):
                    Configuration.objects.create(sport_association_membership_card_configuration_id=uid(200000000+index*10000+ordinal),
                        sport_association=association,emit_only_on_approval=emit if ordinal==1 else not emit,
                        customized_template=template if ordinal==1 else {'ignored':ordinal})
        if json_null:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE application_sportassociationmembershipcardconfiguration SET customized_template='null'::jsonb WHERE sport_association_id=%s",[association_id])
        skip=superuser or role==2
        outcome='missing' if (missing or deleted) and not skip else ''
        expected=None if skip or outcome else {'emit_only_on_approval':emit if count else False,'customized_template':template if count else None}
        cases.append({'name':name,'id':str(user.pk),'owner':owner_id,'association':association_id,'role':role,'superuser':superuser,
            'count':count,'created':not skip and not outcome and count==0,'expected':expected,'outcome':outcome,
            'first':uid(200000000+index*10000+1) if count else None,
            'audit':{'actor_id':str(actor.pk) if audit else None,'actor_email':actor.email if audit else None,
                'remote_address':'2001:db8::42' if audit else '127.0.0.1','remote_port':8443 if audit else None,
                'correlation_id':'synthetic-card-correlation' if audit else None}})
    add('create default configuration')
    add('existing SQL null configuration',count=1)
    add('existing JSON null configuration',count=1,json_null=True)
    add('custom template and approval',count=1,emit=True,template={'id':9007199254740993,'sections':['name','number']})
    for label,value in [('false',False),('array',[]),('string','legacy'),('zero',0)]:
        add(f'existing {label} template',count=1,template=value)
    for count in (2,128,512):
        add(f'duplicate configurations {count}',count=count,emit=True,template={'selected':'lowest UUID'})
    add('create with independent request actor',audit=True)
    add('collaborator creates owner configuration',role=3,audit=True)
    add('collaborator reuses owner configuration',role=3,count=1,emit=True)
    add('administrator does not create configuration',superuser=True)
    add('athlete does not create configuration',role=2)
    add('deleted association rejected',deleted=True)
    add('missing association rejected',missing=True)
    add('inactive owner response component',owner_active=False)
    add('deleted owner response component',owner_deleted=True)
    content_type=ContentType.objects.get_for_model(Configuration)
    def snapshot():
        models={m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        with connection.cursor() as cursor:
            cursor.execute('SELECT sport_association_membership_card_configuration_id, customized_template IS NULL FROM application_sportassociationmembershipcardconfiguration ORDER BY 1')
            models['configuration_sql_null']=cursor.fetchall()
        return models
    before=snapshot()
    def check(case,started):
        rows=list(Configuration.objects.filter(sport_association_id=case['association']).order_by('pk'))
        assert len(rows)==case['count']+int(case['created']),f"{case['name']}: configuration count changed"
        if not case['created']:
            assert not LogEntry.objects.filter(content_type=content_type,object_pk__in=[str(row.pk) for row in rows]).exists(),'existing configuration was audited'
            return
        row=rows[0]; key=str(row.pk)
        assert row.pk.version==4 and not row.emit_only_on_approval and row.customized_template is None,'wrong creation defaults'
        with connection.cursor() as cursor:
            cursor.execute('SELECT customized_template IS NULL FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_membership_card_configuration_id=%s',[row.pk])
            assert cursor.fetchone()==(True,),'default template must be SQL NULL'
        entry=LogEntry.objects.get(content_type=content_type,object_pk=key)
        changes={'sport_association_membership_card_configuration_id':['None',key], 'sport_association':['None',case['association']],
            'emit_only_on_approval':['None','False'],'customized_template':['None','null']}
        expected={'content_type_id':content_type.pk,'object_pk':key,'object_id':None,
            'object_repr':f'SportAssociationMembershipCardConfiguration object ({key})','serialized_data':None,'action':0,
            'changes_text':'','changes':changes,'actor_id':case['audit']['actor_id'],'actor_email':case['audit']['actor_email'],
            'cid':case['audit']['correlation_id'],'remote_addr':case['audit']['remote_address'],'remote_port':case['audit']['remote_port'],'additional_data':None}
        actual=LogEntry.objects.filter(pk=entry.pk).values().get();actual.pop('id');stamp=actual.pop('timestamp')
        if actual['actor_id'] is not None:actual['actor_id']=str(actual['actor_id'])
        assert actual==expected,f"{case['name']}: audit differs: {sorted(k for k in expected if actual.get(k)!=expected[k])}"
        assert started<=stamp<=timezone.now() and entry.changes_dict==changes,'audit timestamp/readback differs'
        index=AuditLogIndex.objects.get(log_entry=entry)
        assert str(index.sport_association_id)==case['association'] and index.resolution_path=='direct','wrong association audit scope'
        assert index.pk.version==4 and stamp<=index.created_at<=timezone.now(),'invalid index metadata'
    def reset(case):
        if case['created']:
            ids=list(Configuration.objects.filter(sport_association_id=case['association']).values_list('pk',flat=True))
            LogEntry.objects.filter(content_type=content_type,object_pk__in=[str(pk) for pk in ids]).delete()
            with disable_auditlog():Configuration.objects.filter(pk__in=ids).delete()
    factory=RequestFactory()
    for case in cases:
        headers={}
        if case['audit']['actor_id']:
            headers={'HTTP_X_FORWARDED_FOR':'[2001:db8::42]:1234','HTTP_X_FORWARDED_PORT':'8443','HTTP_X_CORRELATION_ID':'synthetic-card-correlation'}
        request=factory.post('/oauth2/login',**headers)
        if case['audit']['actor_id']:request.user=actor
        user=User.original_objects.get(pk=case['id'])
        def response(request):return JsonResponse(JWTTokenService.build_login_response(user,{}))
        started=timezone.now();outcome=''
        try:
            result=AuditlogMiddleware(response)(request)
        except SportAssociation.DoesNotExist:
            outcome='missing'
        else:
            actual=json.loads(result.content)['user_data'].get('sport_association',{}).get('membership_card_configuration')
            assert actual==case['expected'],f"{case['name']}: response configuration differs"
        assert outcome==case['outcome'],f"{case['name']}: builder outcome differs"
        check(case,started);reset(case)
    # Characterize the current Django race instead of assuming the Go parent
    # lock coordinates with an unmodified Django writer during coexistence.
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from unittest.mock import patch
    from django.db import connections
    from django.db.models.query import QuerySet
    original_first=QuerySet.first
    barrier=Barrier(2)
    def racing_first(query):
        result=original_first(query)
        if query.model is Configuration and result is None:
            barrier.wait(timeout=10)
        return result
    def race_request():
        try:
            race_user=User.original_objects.get(pk=cases[0]['id'])
            request=RequestFactory().post('/oauth2/login')
            result=AuditlogMiddleware(lambda request:JsonResponse(JWTTokenService.build_login_response(race_user,{})))(request)
            assert result.status_code==200
        finally:
            connections['default'].close()
    with patch.object(QuerySet,'first',racing_first):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(race_request) for _ in range(2)]
            for future in futures:future.result(timeout=15)
    raced=list(Configuration.objects.filter(sport_association_id=cases[0]['association']).values_list('pk',flat=True))
    assert len(raced)==2,'Django missing-configuration race behavior changed'
    assert LogEntry.objects.filter(content_type=content_type,object_pk__in=[str(pk) for pk in raced]).count()==2,'Django raced creation audits differ'
    assert AuditLogIndex.objects.filter(sport_association_id=cases[0]['association']).count()==2,'Django raced creation indexes differ'
    reset(cases[0])
    print('Django: concurrent missing lookups can create two configurations; cross-runtime creation coordination remains open',flush=True)
    # Real permission failures demonstrate why the requested Go write invariant
    # is stronger than Django's current autocommit signal chain.
    from django.db import ProgrammingError
    for role,allow_log in [('card_log_denied',False),('card_index_denied',True)]:
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE {role} NOLOGIN')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO {role}')
            cursor.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}')
            cursor.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}')
            cursor.execute(f'GRANT UPDATE ON application_sportassociation TO {role}')
            cursor.execute(f'GRANT INSERT ON application_sportassociationmembershipcardconfiguration TO {role}')
            if allow_log:cursor.execute(f'GRANT INSERT ON auditlog_logentry TO {role}')
            cursor.execute(f'SET ROLE {role}')
        try:
            fault_user=User.original_objects.get(pk=cases[0]['id'])
            try:
                result=JWTTokenService.build_login_response(fault_user,{})
            except ProgrammingError as error:
                assert not allow_log and getattr(error.__cause__,'sqlstate',getattr(error.__cause__,'pgcode',None))=='42501','unexpected Django audit failure'
            else:
                assert allow_log and result['user_data']['sport_association']['membership_card_configuration']==cases[0]['expected'],'Django index failure response differs'
        finally:
            with connection.cursor() as cursor:cursor.execute('RESET ROLE')
        rows=list(Configuration.objects.filter(sport_association_id=cases[0]['association']).values_list('pk',flat=True))
        assert len(rows)==1,'Django permission failure did not retain its earlier configuration insert'
        assert LogEntry.objects.filter(content_type=content_type,object_pk__in=[str(pk) for pk in rows]).count()==int(allow_log),'Django permission failure audit state differs'
        assert not AuditLogIndex.objects.filter(sport_association_id=cases[0]['association']).exists(),'denied index insert unexpectedly succeeded'
        reset(cases[0])
    print('Django: denied audit/index inserts leave partial creation effects; Go atomic rollback is tested separately',flush=True)
    assert snapshot()==before,'Django configuration stage left undeclared effects'
    print(f'Django: {len(cases)} login-response membership-card cases and exact audit fields passed',flush=True)
    path=work/'membership-card-configuration.json';path.write_text(json.dumps({'cases':cases}));path.chmod(0o600)
    try:
        started=timezone.now()
        subprocess.run(['go','test','-race','-tags=cardconfigurationintegration','./internal/service','-run','^TestMembershipCard'],
            cwd=ROOT/'BE_GO',check=True,timeout=180,env={**tool_env,'ASSOZETA_CARD_CONFIGURATION_FIXTURE':str(path),
                'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        for case in cases:check(case,started);reset(case)
        assert snapshot()==before,'Go configuration stage changed undeclared fields/tables'
    finally:path.unlink(missing_ok=True)
    return len(cases)
