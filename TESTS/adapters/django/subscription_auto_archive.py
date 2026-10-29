"""Automatic archive effects after explicitly characterized legacy task repairs."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import io
import gc
import json
import subprocess
from unittest.mock import patch
from uuid import UUID

from .accounts import ROOT
from .scoped_resources import uid


def _check_audit_indexes(entries, ghost_entries, expected, indexes_for_entry,
                         resolve_association, now, index_count_delta):
    """Check archive index evidence; callbacks read actual ORM rows at runtime."""
    for entry in entries:
        model = entry.content_type.model
        source = next(r for r in expected['application.' + model.capitalize()]
                      if str(r[model + '_id']) == entry.object_pk)
        indexes = list(indexes_for_entry(entry))
        assert len(indexes) == 1, ('wrong index count', model, entry.object_pk, len(indexes))
        index = indexes[0]
        path = 'changes_json.sport_association' if entry in ghost_entries else 'direct'
        assert index.log_entry_id == entry.pk, ('wrong index log entry', model, entry.object_pk)
        assert index.sport_association_id == source['sport_association_id'], ('wrong index association', model, entry.object_pk)
        assert index.resolution_path == path, ('wrong index resolution', model, entry.object_pk)
        assert index.created_at == now, ('wrong index timestamp', model, entry.object_pk)
        if entry in ghost_entries:
            # Signals accept auditlog's tuples; persisted changes reload as lists.
            # Require the stored index as well as independent readback resolution.
            resolved, resolved_path = resolve_association(entry)
            assert resolved is not None and str(resolved.pk) == str(source['sport_association_id']) and resolved_path == path, (
                'wrong historical readback resolution', model, entry.object_pk)
    assert index_count_delta == len(entries), ('wrong total index count', index_count_delta, len(entries))


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from django_celery_beat.models import PeriodicTask
    from application import tasks
    from application.models import User, SportAssociation, Subscription, Payment, PaymentCategory, Invoice
    from application.models.audit_models import AuditLogIndex
    from application.signals import resolve_sport_association_for_log_entry

    output=ROOT/'quality-reports/go-migration/subscription-auto-archive'
    output.mkdir(parents=True,exist_ok=True)
    (output/'body-results.json').write_text('{"success":false}\n')
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    owned=(Subscription,Payment,PaymentCategory,Invoice)
    assert all(not m._base_manager.exists() for m in owned),'archive fixture requires empty owned tables'
    now=datetime(2030,1,1,tzinfo=timezone.utc)
    users=[User(user_id=uid(160000+i),username=f'ARCHIVE-{i}',password='!fixture',role=1,
        auto_archive=i!=1,is_active=i!=2,deleted=i==2,subscription_duration=(2,2,1,0)[i],balance_sheet_start_day=1,
        balance_sheet_start_month=9 if i==2 else 1,subscription_start_day=1,subscription_start_month=9,
        custom_end_date=True,subscription_end_day=30,subscription_end_month=6) for i in range(4)]
    User.original_objects.bulk_create(users)
    associations=[SportAssociation(sport_association_id=uid(160010+i),user=u,denomination='Archive fixture',tax_code=f'{i:011d}',deleted=i==2) for i,u in enumerate(users)]
    SportAssociation.original_objects.bulk_create(associations)
    category=PaymentCategory(payment_category_id=uid(160020),name='entrate e proventi da attività tipiche')
    PaymentCategory.objects.bulk_create([category])
    ct={m._meta.model_name:ContentType.objects.get_for_model(m) for m in (Subscription,Payment,Invoice)}
    def clear_audits():
        LogEntry.objects.filter(content_type_id__in=[c.pk for c in ct.values()]).delete()
    def call():
        captured=io.StringIO()
        # The existing task cannot reach its body without both confirmed repairs.
        # Only the clock and diagnostic PK access are patched; model saves/signals
        # execute unchanged. These repairs are explicit, not baseline production parity.
        with patch.object(tasks,'datetime',wraps=datetime) as clock, patch.object(Subscription,'id',property(lambda s:s.pk),create=True), patch('django.utils.timezone.datetime',wraps=datetime) as audit_clock,redirect_stdout(captured):
            clock.now.return_value=now;audit_clock.now.return_value=now
            assert tasks.auto_archive_subscription.run() is None
        return captured.getvalue()
    try:
        # Explicit eligibility and date expectations, independent of the utility.
        specs=[
            ('zero UUID expired',0,'2028-10-01',False,False,True,'2028-09-01','2029-06-30'),
            ('last fiscal year',0,'2029-01-02',False,False,True,'2028-09-01','2029-06-30'),
            ('current fiscal year',0,'2030-01-01',False,False,False,None,None),
            ('deleted subscription',0,'2028-10-01',True,False,False,None,None),
            ('already archived',0,'2028-10-01',False,True,False,None,None),
            ('owner disabled archive',1,'2028-10-01',False,False,False,None,None),
            ('deleted association leap defaults',2,'2028-02-29',False,False,True,'2028-02-29','2029-02-28'),
            ('unhandled duration',3,'2028-10-01',False,False,True,None,None),
            ('shared payment',0,'2028-10-01',False,False,True,'2028-09-01','2029-06-30'),
            ('existing dates historical links',0,'2028-10-01',False,False,True,'2011-01-02','2099-03-04'),
        ]
        payments=[];invoices=[];rows=[]
        for i,(name,owner,created,deleted,archived,changed,start,end) in enumerate(specs):
            association=associations[owner]
            inv=Invoice(invoice_id=uid(160100+i),sport_association=association,membership_fee=Decimal('1234567.89'),activity_fee=Decimal('0.01'),deleted=i==9,archived=i==9)
            pay=Payment(payment_id=uid(160200+i),sport_association=association,invoice=inv,subject=1,amount=Decimal('1234567.89'),
                description=None if i==0 else 'Preserved',payment_category=None if i==0 else category,deleted=i==9,archived=i==9)
            invoices.append(inv);payments.append(pay)
            rows.append(dict(Name=name,ID='00000000-0000-0000-0000-000000000000' if i==0 else uid(160300+i),Association=str(association.pk),
                Payment=str(payments[0].pk if i==8 else pay.pk),Changed=changed,Start=start,End=end))
        Invoice._base_manager.bulk_create(invoices);Payment._base_manager.bulk_create(payments)
        Subscription._base_manager.bulk_create([Subscription(subscription_id=row['ID'],sport_association_id=row['Association'],user=users[specs[i][1]],
            payment_id=row['Payment'],creation_date=datetime.fromisoformat(specs[i][2]).replace(tzinfo=timezone.utc),deleted=specs[i][3],archived=specs[i][4],
            start_date=date(2011,1,2) if i==9 else None,end_date=date(2099,3,4) if i==9 else None,notes='preserve unrelated fields') for i,row in enumerate(rows)])
        before=snapshot();expected=deepcopy(before);expected_logs={}
        selected_payments={row['Payment'] for row in rows if row['Changed']}
        selected_invoices={str(pay.invoice_id) for pay in payments if str(pay.pk) in selected_payments}
        for key,model_name,id_field in (('application.Subscription','subscription','subscription_id'),('application.Payment','payment','payment_id'),('application.Invoice','invoice','invoice_id')):
            for record in expected[key]:
                old=dict(record);pk=str(record[id_field])
                if model_name=='subscription':
                    spec=next(r for r in rows if r['ID']==pk)
                    if spec['Changed']:
                        record.update(archived=True,start_date=date.fromisoformat(spec['Start']) if spec['Start'] else None,end_date=date.fromisoformat(spec['End']) if spec['End'] else None)
                elif model_name=='payment' and pk in selected_payments:
                    record['archived']=True
                    if pk==str(payments[0].pk):record.update(description='Iscrizione',payment_category_id=UUID(str(category.pk)))
                elif model_name=='invoice' and pk in selected_invoices:record['archived']=True
                changes={('payment_category' if k=='payment_category_id' else k):[str(old[k]),str(v)] for k,v in record.items() if old[k]!=v}
                if changes:expected_logs[(model_name,pk)]=changes
        def check(legacy=False):
            after=snapshot()
            for key in expected:
                if key not in ('auditlog.LogEntry','application.AuditLogIndex'):
                    assert after[key]==expected[key],('unexpected archive field effects',key)
            entries=list(LogEntry.objects.filter(content_type_id__in=[c.pk for c in ct.values()]))
            ghosts={(model,str(obj.pk)) for model,obj in (('payment',payments[9]),('invoice',invoices[9]))}
            ghost_entries=[e for e in entries if (e.content_type.model,e.object_pk) in ghosts]
            if legacy:
                assert len(ghost_entries)==2
                for e in ghost_entries:
                    assert all(change[0]=='None' for change in e.changes.values())
                    assert e.changes['archived']==['None','True'] and e.changes['deleted']==['None','True']
                    assert e.changes[e.content_type.model+'_id']==['None',e.object_pk]
            else:assert not ghost_entries,'unchanged historical rows gained false audits'
            real_entries=[e for e in entries if e not in ghost_entries]
            observed={(entry.content_type.model,entry.object_pk):entry.changes for entry in real_entries}
            assert observed==expected_logs and len(real_entries)==len(expected_logs),(observed,expected_logs)
            for entry in entries:
                model=entry.content_type.model
                assert entry.object_repr==f'{model.capitalize()} object ({entry.object_pk})'
                assert entry.action==LogEntry.Action.UPDATE and entry.timestamp==now and entry.changes_text==''
                assert all(getattr(entry,key) is None for key in ('object_id','actor_id','actor_email','cid','serialized_data','additional_data','remote_addr','remote_port'))
            _check_audit_indexes(entries,ghost_entries,expected,
                lambda entry:AuditLogIndex.objects.filter(log_entry=entry),
                resolve_sport_association_for_log_entry,now,
                len(after['application.AuditLogIndex'])-len(before['application.AuditLogIndex']))
        message=call();assert '[DEPLOY TASK] OK!' in message,message
        check(legacy=True)
        # Repeating the repaired task produces no additional audit or row changes.
        repeated=snapshot();call();assert snapshot()==repeated
        with disable_auditlog():
            clear_audits()
            for model in (Subscription,Payment,Invoice):
                for record in before[model._meta.label]:
                    model._base_manager.filter(pk=record[model._meta.pk.attname]).update(**record)
        assert snapshot()==before
        # Legal independent subscriptions reach the save before failing at payment.
        first=rows[0]['ID']
        Subscription._base_manager.filter(pk=first).update(payment=None)
        message=call()
        assert "'NoneType' object has no attribute 'archived'" in message,message
        assert Subscription._base_manager.get(pk=first).archived
        with disable_auditlog():
            clear_audits()
            for model in (Subscription,Payment,Invoice):
                for record in before[model._meta.label]:model._base_manager.filter(pk=record[model._meta.pk.attname]).update(**record)
        assert snapshot()==before
        Payment._base_manager.filter(pk=rows[0]['Payment']).update(invoice=None)
        message=call()
        assert "'NoneType' object has no attribute 'archived'" in message,message
        assert Subscription._base_manager.get(pk=first).archived and Payment._base_manager.get(pk=rows[0]['Payment']).archived
        with disable_auditlog():
            clear_audits()
            for model in (Subscription,Payment,Invoice):
                for record in before[model._meta.label]:model._base_manager.filter(pk=record[model._meta.pk.attname]).update(**record)
        assert snapshot()==before
        gc.collect()  # Close the legacy iterator retained by the handled exception.
        task=tasks.auto_archive_subscription
        policy={key:getattr(task,key) for key in ('acks_late','max_retries','default_retry_delay','rate_limit','time_limit','soft_time_limit')}
        assert policy==dict(acks_late=False,max_retries=3,default_retry_delay=180,rate_limit=None,time_limit=None,soft_time_limit=None),policy
        policy.update(timezone=task.app.conf.timezone,
            configured_occurrences=[name for name,entry in task.app.conf.beat_schedule.items() if entry['task']==task.name],
            persisted_occurrences=list(PeriodicTask.objects.filter(task=task.name).values('name','enabled')))
        fixture=work/'subscription-auto-archive.json'
        fixture.write_text(json.dumps(dict(Now=now.isoformat(),Rows=rows,Audits=len(expected_logs),Owner=str(users[0].pk),OtherAssociation=str(associations[1].pk))))
        (output/'django-oracle.json').write_text(json.dumps(dict(success=True,model_cases=len(rows),audit_count=len(expected_logs),
            repairs=['aware UTC clock','diagnostic uses subscription primary key'],null_payment_legacy_partial_write=True,null_invoice_legacy_partial_write=True,
            false_historical_audits=2,missing_historical_indexes=0,historical_index_count=2,
            historical_index_resolution='changes_json.sport_association',policy=policy),indent=2)+'\n')
        print(f'Django archive body: {len(rows)} authored cases and {len(expected_logs)} exact audits after two explicit repairs; optional-payment partial failure confirmed',flush=True)
        completed=subprocess.run(['go','test','-race','-tags=subscriptionarchiveintegration','./internal/service','-run','^TestSubscriptionAutoArchivePostgres$','-json'],
            cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_ARCHIVE_FIXTURE':str(fixture),
                'ASSOZETA_ARCHIVE_PLAN':str(output/'query-plan.json'),'ASSOZETA_ARCHIVE_DSN':f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},capture_output=True,text=True,timeout=180)
        (output/'go-test.jsonl').write_text(completed.stdout)
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'):print(event['Output'],end='',flush=True)
        if completed.stderr:print(completed.stderr,flush=True)
        completed.check_returncode()
        names={'exact archive defaults audits and replay','invalid envelope before SQL','optional relationships remain supported',
            'foreign payment fails atomically','audit and index failures roll back the entire archive','stale completion rolls back all effects','strict fiscal threshold and invalid calendars',
            'bounded pages statement counts and executed plan','blocked query cancellation and connection recovery',
            'concurrent archives audit each change once','executor heartbeat during archive','later page failure rolls back earlier changes',
            'historical linked changes use stored values and direct indexes'}
        expected_names={'TestSubscriptionAutoArchivePostgres/'+n.replace(' ','_') for n in names}
        passed={e.get('Test') for e in events if e['Action']=='pass' and '/' in e.get('Test','')}
        assert passed==expected_names and not any(e['Action']=='skip' for e in events),'missing or skipped archive scenario'
        check()
        # Re-save preserves rows; only the historical false audits recur.
        stable=snapshot();known_logs=set(LogEntry.objects.values_list('pk',flat=True))
        with patch('django.utils.timezone.datetime',wraps=datetime) as audit_clock:
            audit_clock.now.return_value=now
            for model in (Subscription,Payment,Invoice):
                for obj in model._base_manager.filter(archived=True):
                    if model is Subscription and str(obj.pk)==rows[4]['ID']:continue
                    obj.save()
        added=list(LogEntry.objects.exclude(pk__in=known_logs))
        assert len(added)==2 and {(e.content_type.model,e.object_pk) for e in added}=={('payment',str(payments[9].pk)),('invoice',str(invoices[9].pk))}
        assert all(all(change[0]=='None' for change in e.changes.values()) for e in added)
        _check_audit_indexes(added,added,expected,
            lambda entry:AuditLogIndex.objects.filter(log_entry=entry),
            resolve_sport_association_for_log_entry,now,
            AuditLogIndex.objects.count()-len(stable['application.AuditLogIndex']))
        with disable_auditlog():LogEntry.objects.filter(pk__in=[e.pk for e in added]).delete()
        assert snapshot()==stable,'Django re-save changed Go-written archive values beyond confirmed historical audit defect'
        (output/'body-results.json').write_text(json.dumps(dict(success=True,scenario_groups=len(names),model_cases=len(rows)),indent=2)+'\n')
        return len(names)
    finally:
        with disable_auditlog():
            clear_audits()
            for model in (Subscription,Payment,Invoice,PaymentCategory):model._base_manager.all()._raw_delete('default')
            SportAssociation.original_objects.filter(pk__in=[a.pk for a in associations]).delete()
            User.original_objects.filter(pk__in=[u.pk for u in users]).delete()
        assert snapshot()==original,'archive fixture changed preexisting managed rows'
