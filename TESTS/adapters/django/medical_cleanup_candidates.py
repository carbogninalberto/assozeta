"""Logical medical-document candidates; never authorizes or performs deletion."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import subprocess

from .accounts import ROOT
from .scoped_resources import uid


def run(work,tool_env,owner,associations):
    from django.apps import apps
    from django.db import models
    from application.models import Subscription,MedicalCertificate,Payment,Invoice
    from application.models.subscriptions_models import AssociateImportDraft,MedicalAppointments,SubscriptionFile,SubscriptionMembership
    from application.models.courses_models import CourseLocation
    from application.models.user_models import Instructor,InstructorHours,SportAssociationDocumentsArchive
    from application.models.invoices_models import InvoiceSuppliers,CustomerInvoice
    from application.models.modules_models import Module,ModuleResponses
    from docmanager.models import Document
    output=ROOT/'quality-reports/go-migration/medical-orphan-cleanup'
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy}
    before=snapshot();created={};sequence=171000
    def bulk(model,**values):
        nonlocal sequence
        sequence+=1
        if isinstance(model._meta.pk,models.UUIDField):values.setdefault(model._meta.pk.attname,uid(sequence))
        obj=model(**values);model._base_manager.bulk_create([obj]);created.setdefault(model,[]).append(obj.pk);return obj
    def many(model,rows):model._base_manager.bulk_create(rows);created.setdefault(model,[]).extend(row.pk for row in rows)
    now=datetime(2024,1,1,0,0,0,123456,tzinfo=timezone.utc)
    protected=[];expected=[]
    def document(name,*,eligible=False,zero=False,count=1,filepath=None,size=14):
        values=dict(creation_date=now,filename=name,filepath=filepath,file_size_bytes=size)
        if zero:values['document_id']='00000000-0000-0000-0000-000000000000'
        d=bulk(Document,**values)
        for _ in range(count):bulk(MedicalCertificate,document=d,user=owner,creation_date=now)
        if eligible:expected.append(dict(ID=str(d.pk),Created=now.isoformat(),Filename=name,Filepath=filepath,Size=size,Certificates=count))
        else:protected.append(str(d.pk))
        return d
    try:
        document('zero UUID.pdf',eligible=True,zero=True,filepath='',size=0)
        document('shared orphan %.pdf',eligible=True,count=2,filepath='stored/path.pdf',size=None)
        document('日本語.pdf',eligible=True)
        bulk(MedicalCertificate,user=owner,document=None,creation_date=now)
        other=associations[1]
        sub=bulk(Subscription,user=owner,sport_association=other,creation_date=now,deleted=True,archived=True)
        invoice=bulk(Invoice,sport_association=other,membership_fee=Decimal('10.00'),activity_fee=Decimal('0.00'),deleted=True,archived=True)
        payment=bulk(Payment,sport_association=other,amount=Decimal('10.00'),deleted=True,archived=True)
        instructor=bulk(Instructor,user=owner,first_name='Fixture',last_name='Instructor')
        location=bulk(CourseLocation,sport_association=other,title='Fixture')
        module=bulk(Module,sport_association=other,title='Fixture')
        response=bulk(ModuleResponses,module=module)
        membership=bulk(SubscriptionMembership,sport_association=other,subscription=sub)
        # Every actual Document FK/M2M target is explicitly represented. References
        # in another association, including historical rows, must protect the file.
        reference_builders={
            'application.CourseLocation_documents':lambda d:bulk(CourseLocation.documents.through,courselocation=location,document=d),
            'application.CustomerInvoice':lambda d:bulk(CustomerInvoice,sport_association=other,pdf=d),
            'application.Instructor_documents':lambda d:bulk(Instructor.documents.through,instructor=instructor,document=d),
            'application.InstructorHours':lambda d:bulk(InstructorHours,instructor=instructor,hours=Decimal('1.00'),document=d),
            'application.Invoice':lambda d:Invoice._base_manager.filter(pk=invoice.pk).update(document_pdf=d),
            'application.Invoice_attached_documents':lambda d:bulk(Invoice.attached_documents.through,invoice=invoice,document=d),
            'application.InvoiceSuppliers':lambda d:bulk(InvoiceSuppliers,sport_association=other,invoice_identifier='fixture',amount=Decimal('1.00'),document_pdf=d),
            'application.MedicalAppointments':lambda d:bulk(MedicalAppointments,subscription=sub,region='non-agonistica',document=d),
            'application.ModuleResponses_attachments':lambda d:bulk(ModuleResponses.attachments.through,moduleresponses=response,document=d),
            'application.Payment_attachments':lambda d:bulk(Payment.attachments.through,payment=payment,document=d),
            'application.SportAssociationDocumentsArchive':lambda d:bulk(SportAssociationDocumentsArchive,sport_association=other,document=d),
            'application.Subscription':lambda d:Subscription._base_manager.filter(pk=sub.pk).update(document_pdf=d),
            'application.SubscriptionFile':lambda d:bulk(SubscriptionFile,subscription=sub,document=d),
            'application.SubscriptionMembership_attached_membership_documents':lambda d:bulk(SubscriptionMembership.attached_membership_documents.through,subscriptionmembership=membership,document=d),
        }
        actual={model._meta.label for model in apps.get_models(include_auto_created=True) for field in model._meta.local_fields if field.is_relation and field.remote_field.model is Document}
        assert actual==set(reference_builders)|{'application.MedicalCertificate'},('new/uncovered Document relationship',actual)
        for name,build in reference_builders.items():build(document(name+'.pdf'))
        shared=document('attached historical medical.pdf')
        attached=bulk(MedicalCertificate,document=shared,user=owner,creation_date=now)
        Subscription._base_manager.filter(pk=sub.pk).update(medical=attached)
        for i,format_id in enumerate((lambda s:s,lambda s:s.upper(),lambda s:'{'+s.upper()+'}',lambda s:'urn:uuid:'+s.replace('-',''))):
            d=document(f'draft-{i}.pdf')
            med=MedicalCertificate._base_manager.get(document=d)
            # Use alphabetic UUID digits to exercise canonicalization, not only numeric fixtures.
            key=f'aaaaaaaa-bbbb-4ccc-8ddd-{i:012d}'
            MedicalCertificate._base_manager.filter(pk=med.pk).update(medical_id=key)
            created[MedicalCertificate]=[pk for pk in created[MedicalCertificate] if str(pk)!=str(med.pk)];created[MedicalCertificate].append(key)
            bulk(AssociateImportDraft,sport_association=other,data={'medical_certificate':{'medical_id':format_id(key)}})
        for data in (None,[],{}, {'medical_certificate':None},{'medical_certificate':'bad'},{'medical_certificate':{'medical_id':42}}):
            bulk(AssociateImportDraft,sport_association=other,data=data)
        scaled=[uid(200000+i) for i in range(4096)]
        many(Document,[Document(document_id=key,creation_date=now,filename='scaled.pdf') for key in scaled])
        many(MedicalCertificate,[MedicalCertificate(medical_id=uid(210000+i),document_id=key,user=owner,creation_date=now) for i,key in enumerate(scaled)])
        many(AssociateImportDraft,[AssociateImportDraft(associate_import_draft_id=uid(220000+i),sport_association=other,
            data={'medical_certificate':{'medical_id':uid(210000+i)}}) for i in range(0,4096,2)])
        fixture=work/'medical-cleanup-candidates.json'
        fixture.write_text(json.dumps(dict(Expected=sorted(expected,key=lambda x:x['ID']),Protected=protected,Scaled=scaled[1::2],ScaleAfter=uid(199999),Invoice=str(invoice.pk))))
        model_before=snapshot()
        files_before={str(p.relative_to(work)):p.read_bytes() for directory in work.glob('medical-files-*') for p in directory.rglob('*') if p.is_file()}
        completed=subprocess.run(['go','test','-race','-tags=medicalcleanupintegration','./internal/repository','-run','^TestMedicalCleanupCandidatesPostgres$','-json'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_MEDICAL_CANDIDATES_FIXTURE':str(fixture),'ASSOZETA_MEDICAL_CANDIDATES_DSN':f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable',
                'ASSOZETA_MEDICAL_CANDIDATES_PLAN':str(output/'candidate-plan.json')},capture_output=True,text=True,timeout=180)
        (output/'candidate-go-test.jsonl').write_text(completed.stdout)
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'):print(event['Output'],end='',flush=True)
        if completed.stderr:print(completed.stderr,flush=True)
        completed.check_returncode()
        names={'all document references and historical drafts protect candidates','page limits cancellation and zero UUID cursor',
            'caller transaction sees new document references','one statement across scale and concurrent reads',
            'blocked query cancellation and pool recovery','executed plan with mixed protected documents'}
        passed={e.get('Test') for e in events if e['Action']=='pass' and '/' in e.get('Test','')}
        assert passed=={'TestMedicalCleanupCandidatesPostgres/'+n.replace(' ','_') for n in names} and not any(e['Action']=='skip' for e in events),'missing or skipped candidate scenario'
        assert snapshot()==model_before,'candidate reader changed managed state'
        assert {str(p.relative_to(work)):p.read_bytes() for directory in work.glob('medical-files-*') for p in directory.rglob('*') if p.is_file()}==files_before,'candidate discovery changed files'
        return len(names)
    finally:
        # Delete exactly fixture-owned rows in dependency order, including auto-created M2M tables.
        remaining=set(created)
        while remaining:
            leaves=[m for m in remaining if not any(f.is_relation and f.remote_field.model is m for other in remaining if other is not m for f in other._meta.local_fields)]
            assert leaves,'unexpected cyclic candidate fixture dependencies'
            for model in leaves:
                model._base_manager.filter(pk__in=created[model])._raw_delete('default');remaining.remove(model)
        assert snapshot()==before,'candidate fixture changed preexisting rows'
