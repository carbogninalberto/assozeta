"""Real orphan-medical cleanup: document/file effects and protected-reference gaps."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import gc
import io
import json
from unittest.mock import patch
from types import SimpleNamespace
from uuid import UUID

from .accounts import ROOT
from .scoped_resources import uid


def _check_audit_indexes(kind, entries, before, after, now):
    """Require only the archive DELETE's source-derived fallback index."""
    label = 'application.AuditLogIndex'
    prior_ids = {row['audit_log_index_id'] for row in before[label]}
    retained = [row for row in after[label] if row['audit_log_index_id'] in prior_ids]
    assert retained == before[label], (kind, 'preexisting index changed')
    added = [row for row in after[label] if row['audit_log_index_id'] not in prior_ids]
    assert len(added) == int(kind == 'archive'), (kind, 'unexpected deletion indexes', added)
    if kind != 'archive':
        return

    # The archive has a direct association FK; MedicalCertificate and Document
    # do not. The task has no actor. Once the archive is deleted, only its old
    # association in audit changes can resolve it (tuples in memory, lists on read).
    sources = before['application.SportAssociationDocumentsArchive']
    assert len(sources) == 1, (kind, 'wrong archive source count')
    source = sources[0]
    archive_entries = [entry for entry in entries
                       if entry.content_type.app_label == 'application'
                       and entry.content_type.model == 'sportassociationdocumentsarchive']
    assert len(archive_entries) == 1, (kind, 'wrong archive audit count')
    entry = archive_entries[0]
    assert entry.object_pk == str(source['sport_association_documents_archive_id']), (kind, 'wrong archive audit object')
    changes = entry.changes.get('sport_association')
    assert isinstance(changes, (list, tuple)) and list(changes) == [str(source['sport_association_id']), 'None'], (kind, 'wrong archive audit association')
    assert entry.timestamp == now, (kind, 'wrong archive audit timestamp')
    index = added[0]
    assert UUID(str(index['audit_log_index_id'])).version == 4, (kind, 'invalid generated index UUID')
    assert index == dict(audit_log_index_id=index['audit_log_index_id'],
                         log_entry_id=entry.pk,
                         sport_association_id=source['sport_association_id'],
                         resolution_path='changes_json.sport_association',
                         created_at=now), (kind, 'wrong archive deletion index', index)


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from django.core.cache import cache
    from django.core.files.base import ContentFile
    from django.core.files.storage import FileSystemStorage
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from django_celery_beat.models import PeriodicTask
    from application import tasks
    from application.models import User, SportAssociation, Subscription, MedicalCertificate, Invoice
    from application.models.subscriptions_models import AssociateImportDraft
    from application.models.user_models import SportAssociationDocumentsArchive
    from application.services.subscription_service import MedicalCertificateService
    from docmanager.models import Document

    output=ROOT/'quality-reports/go-migration/medical-orphan-cleanup'
    output.mkdir(parents=True,exist_ok=True)
    (output/'body-results.json').write_text('{"success":false}\n')
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    owned=(Subscription,MedicalCertificate,Document,AssociateImportDraft,Invoice,SportAssociationDocumentsArchive)
    assert all(not m._base_manager.exists() for m in owned),'medical fixture requires empty owned tables'
    ctypes={m:ContentType.objects.get_for_model(m) for m in owned}
    assert not LogEntry.objects.filter(content_type_id__in=[c.pk for c in ctypes.values()]).exists(),'fixture owns only empty audit sets'
    owner=User(user_id=uid(170000),username='MEDICAL-CLEANUP',password='!fixture',role=1)
    other=User(user_id=uid(170001),username='MEDICAL-OTHER',password='!fixture',role=1)
    User.original_objects.bulk_create([owner,other])
    associations=[SportAssociation(sport_association_id=uid(170010+i),user=u,denomination='Medical cleanup fixture',tax_code=f'{i:011d}') for i,u in enumerate((owner,other))]
    SportAssociation.original_objects.bulk_create(associations)
    base=snapshot();results=[];cache_keys=[]
    now=datetime(2024,1,1,tzinfo=timezone.utc)
    through=Invoice.attached_documents.through
    def reset():
        with disable_auditlog():
            LogEntry.objects.filter(content_type_id__in=[c.pk for c in ctypes.values()]).delete()
            for model in (through,SportAssociationDocumentsArchive,AssociateImportDraft,Subscription,Invoice,MedicalCertificate,Document):
                model._base_manager.all()._raw_delete('default')
        assert snapshot()==base,'medical scenario cleanup changed preexisting rows'
    class ObservedStorage(FileSystemStorage):
        def __init__(self,path,fail=False):
            super().__init__(location=path);self.deleted=[];self.fail=fail
        def delete(self,name):
            self.deleted.append(name)
            if self.fail:raise OSError('private storage deletion failure')
            return super().delete(name)
    def call(storage):
        captured=io.StringIO()
        # Freeze audit defaults only; the task's storage/deletion logic is real.
        with patch.object(tasks,'default_storage',storage),patch.object(tasks,'STORAGE_DIR',''),patch('django.utils.timezone.datetime',wraps=datetime) as audit_clock,redirect_stdout(captured),CaptureQueriesContext(connection) as queries:
            audit_clock.now.return_value=now
            assert tasks.auto_delete_orphan_medical_certificate.run() is None
        gc.collect()
        return captured.getvalue(),len(queries)
    specs=[
        ('ordinary orphan','orphan',True,True,None),
        ('active subscription protects','active',False,False,None),
        ('deleted archived subscription protects','historical',False,False,None),
        ('draft JSON reference is ignored','draft',True,True,None),
        ('active upload grant is ignored','upload-grant',True,True,None),
        ('shared document destroys attached certificate','shared',True,True,None),
        ('foreign invoice PDF is cleared','invoice',True,True,None),
        ('foreign invoice attachment is removed','attachment',True,True,None),
        ('association document archive is cascaded','archive',True,True,None),
        ('null document aborts unchanged','null',False,False,"'NoneType' object has no attribute 'creation_date'"),
        ('missing file deletion is idempotent','missing',True,True,None),
        ('stored filepath is ignored','filepath',True,False,None),
        ('storage failure preserves database','storage-failure',False,False,'private storage deletion failure'),
        ('database failure follows irreversible file deletion','database-failure',False,True,'private document deletion failure'),
    ]
    try:
        for number,(name,kind,removed,file_removed,error) in enumerate(specs):
            reset();store=ObservedStorage(work/f'medical-files-{number}',fail=kind=='storage-failure')
            doc=None
            if kind!='null':
                doc=Document(document_id=uid(170100),creation_date=now,filename='certificato.pdf',filepath='alternate/preserved.pdf' if kind=='filepath' else None,file_size_bytes=15)
                Document.objects.bulk_create([doc])
            medical=MedicalCertificate(medical_id=uid(170200),document=doc,user=owner,creation_date=now,expiration_date=now.date(),notes='preserve certificate data')
            MedicalCertificate.objects.bulk_create([medical])
            key=f'1704067200.0/{uid(170100)}/certificato.pdf'
            actual_key='alternate/preserved.pdf' if kind=='filepath' else key
            if kind not in ('null','missing'):assert store.save(actual_key,ContentFile(b'fixture-medical'))==actual_key
            if kind in ('active','historical','shared'):
                linked=medical
                if kind=='shared':
                    linked=MedicalCertificate(medical_id=uid(170201),document=doc,user=other,creation_date=now)
                    MedicalCertificate.objects.bulk_create([linked])
                Subscription._base_manager.bulk_create([Subscription(subscription_id=uid(170300),sport_association=associations[int(kind=='shared')],user=other if kind=='shared' else owner,
                    medical=linked,deleted=kind=='historical',archived=kind=='historical',creation_date=now)])
            if kind=='draft':
                draft=AssociateImportDraft(associate_import_draft_id=uid(170400),sport_association=associations[0],data={})
                AssociateImportDraft.objects.bulk_create([draft])
                MedicalCertificateService.attach_certificate_to_draft(medical,doc,draft)
                assert draft.data['medical_certificate']['medical_id']==str(medical.pk)
            grant_key=None
            if kind=='upload-grant':
                from application.impersonation_scope import medical_upload_key,remember_medical_upload
                request=SimpleNamespace(headers={'X-Impersonation-Id':uid(170999)},impersonation_association=associations[0])
                grant_key=medical_upload_key(request,medical.pk)
                assert cache.get(grant_key) is None,'fixture upload cache key already exists'
                cache_keys.append(grant_key);remember_medical_upload(request,medical.pk)
                assert cache.get(grant_key)==str(associations[0].pk)
            if kind in ('invoice','attachment'):
                inv=Invoice(invoice_id=uid(170500),sport_association=associations[1],membership_fee=Decimal('10.00'),activity_fee=Decimal('0.00'),document_pdf=doc if kind=='invoice' else None)
                Invoice._base_manager.bulk_create([inv])
                if kind=='attachment':through.objects.bulk_create([through(invoice_id=inv.pk,document_id=doc.pk)])
            if kind=='archive':
                SportAssociationDocumentsArchive.objects.bulk_create([SportAssociationDocumentsArchive(sport_association_documents_archive_id=uid(170600),sport_association=associations[1],document=doc,date=now.date())])
            before=snapshot();expected=deepcopy(before)
            if removed:
                expected['application.MedicalCertificate']=[];expected['docmanager.Document']=[]
                if kind=='shared':
                    for row in expected['application.Subscription']:row['medical_id']=None
                if kind=='invoice':
                    for row in expected['application.Invoice']:row['document_pdf_id']=None
                if kind=='attachment':expected[through._meta.label]=[]
                if kind=='archive':expected['application.SportAssociationDocumentsArchive']=[]
            if kind=='database-failure':
                with connection.cursor() as cursor:
                    cursor.execute("CREATE FUNCTION contract_document_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private document deletion failure'; END $$")
                    cursor.execute('CREATE TRIGGER contract_document_failure BEFORE DELETE ON docmanager_document FOR EACH STATEMENT EXECUTE FUNCTION contract_document_failure()')
            try:message,queries=call(store)
            finally:
                if kind=='database-failure':
                    with connection.cursor() as cursor:
                        cursor.execute('DROP TRIGGER contract_document_failure ON docmanager_document')
                        cursor.execute('DROP FUNCTION contract_document_failure()')
            if error:assert error in message,(name,message)
            else:assert '[DEPLOY TASK] OK!' in message,(name,message)
            expected_calls=[] if kind in ('active','historical','null') else [key]
            assert store.deleted==expected_calls,(name,store.deleted)
            expected_exists=kind not in ('null','missing') and not file_removed
            assert store.exists(actual_key)==expected_exists,(name,'wrong file effects')
            if grant_key is not None:assert cache.get(grant_key)==str(associations[0].pk),'cleanup unexpectedly changed upload grant'
            after=snapshot()
            for model,rows in expected.items():
                if model not in ('auditlog.LogEntry','application.AuditLogIndex'):
                    assert after[model]==rows,(name,model,'unexpected field mutation')
            entries=list(LogEntry.objects.filter(content_type_id__in=[c.pk for c in ctypes.values()]).exclude(pk__in=[r['id'] for r in before['auditlog.LogEntry']]))
            expected_audits={'medicalcertificate':(3 if kind=='shared' else 2),'document':1} if removed else {}
            if kind=='archive':expected_audits['sportassociationdocumentsarchive']=1
            observed={}
            for entry in entries:
                model=entry.content_type.model;observed[model]=observed.get(model,0)+1
                assert entry.action==LogEntry.Action.DELETE and entry.changes_text==''
                assert all(v[1]=='None' for v in entry.changes.values()),(name,'nondelete audit')
                assert all(getattr(entry,k) is None for k in ('actor_id','actor_email','cid','remote_addr','remote_port','serialized_data','additional_data'))
            assert observed==expected_audits,(name,observed,expected_audits)
            assert [r for r in after['auditlog.LogEntry'] if r['id'] in {b['id'] for b in before['auditlog.LogEntry']}]==before['auditlog.LogEntry'],(name,'preexisting audit changed')
            _check_audit_indexes(kind,entries,before,after,now)
            results.append(dict(name=name,queries=queries,removed=removed,file_removed=file_removed,audits=observed,error=error))
            print('Django medical cleanup:',name,'passed',flush=True)
        task=tasks.auto_delete_orphan_medical_certificate
        policy={key:getattr(task,key) for key in ('acks_late','max_retries','default_retry_delay','rate_limit','time_limit','soft_time_limit')}
        policy.update(timezone=task.app.conf.timezone,configured_occurrences=[name for name,entry in task.app.conf.beat_schedule.items() if entry['task']==task.name],
            persisted_occurrences=list(PeriodicTask.objects.filter(task=task.name).values('name','enabled')))
        references=[]
        for model in apps.get_models(include_auto_created=True):
            for field in model._meta.local_fields:
                if field.is_relation and field.remote_field.model is Document:
                    references.append(dict(model=model._meta.label,table=model._meta.db_table,column=field.column,on_delete=field.remote_field.on_delete.__name__))
        result=dict(success=True,scenario_groups=len(results),cases=results,document_references=sorted(references,key=lambda r:(r['table'],r['column'])),policy=policy,
            scope='actual Django characterization with private filesystem; Go cleanup, S3 protocol and production scheduling remain open')
        (output/'django-oracle.json').write_text(json.dumps(result,indent=2)+'\n')
        reset()
        from .medical_cleanup_candidates import run as candidates
        candidate_groups=candidates(work,tool_env,owner,associations)
        (output/'body-results.json').write_text(json.dumps(dict(success=True,django_scenarios=len(results),candidate_groups=candidate_groups,
            scope='Django characterization and read-only Go candidates; destructive cleanup and S3 remain open'),indent=2)+'\n')
        return len(results)+candidate_groups
    finally:
        cache.delete_many(cache_keys)
        reset()
        with disable_auditlog():
            SportAssociation.original_objects.filter(pk__in=[a.pk for a in associations]).delete()
            User.original_objects.filter(pk__in=[owner.pk,other.pk]).delete()
        assert snapshot()==original,'medical fixture cleanup changed preexisting managed rows'
