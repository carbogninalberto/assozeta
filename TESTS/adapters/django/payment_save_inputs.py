"""Batched payment-save inputs, real Django descriptions and explicit scope fixes."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import subprocess

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from application.models import (User,SportAssociation,Payment,PaymentCategory,Invoice,CustomAccounts,
        Subscription,Course,CourseSubscription,CourseSubscriptionInstallment)
    from application.models.carnet_models import Carnet,CarnetSubscription

    output=ROOT/'quality-reports/go-migration/payment-save-inputs'
    output.mkdir(parents=True,exist_ok=True)
    (output/'body-results.json').write_text('{"success":false}\n')
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    owned=(Payment,PaymentCategory,Invoice,CustomAccounts,Subscription,Course,CourseSubscription,CourseSubscriptionInstallment,Carnet,CarnetSubscription)
    assert all(not model._base_manager.exists() for model in owned),'payment-input fixture requires empty owned tables'
    users=[User(user_id=uid(130001+i),username=f'PAYMENT-INPUT-{i}',password='!fixture',role=1) for i in range(3)]
    User.original_objects.bulk_create(users)
    associations=[SportAssociation(sport_association_id=uid(130011+i),user=user,denomination='Input fixture',tax_code=f'{i:011d}') for i,user in enumerate(users)]
    SportAssociation.original_objects.bulk_create(associations)
    own,other,foreign=associations
    default='entrate e proventi da attività tipiche'
    foreign_cat=PaymentCategory(payment_category_id=uid(130020),sport_association=foreign,name=default,deleted=True,archived=True)
    own_cat=PaymentCategory(payment_category_id=uid(130021),sport_association=own,name=default,deleted=True,archived=True)
    global_cat=PaymentCategory(payment_category_id=uid(130022),name=default)
    explicit_cat=PaymentCategory(payment_category_id=uid(130023),name='La categoria globale')
    PaymentCategory.objects.bulk_create([foreign_cat,own_cat,global_cat,explicit_cat])
    invoices=[Invoice(invoice_id=uid(130030+i),sport_association=a,membership_fee=Decimal('10.00'),activity_fee=Decimal('0.00'),archived=True,deleted=True) for i,a in enumerate((own,other))]
    Invoice.objects.bulk_create(invoices)
    accounts=[CustomAccounts(custom_account_id=uid(130040+i),sport_association=a,name='Conto',initial_balance=Decimal('0.00'),account_code='fixture') for i,a in enumerate((own,other))]
    CustomAccounts.objects.bulk_create(accounts)
    subscriptions=[Subscription(subscription_id=uid(130050+i),sport_association=a,user=users[i]) for i,a in enumerate((own,other))]
    Subscription._base_manager.bulk_create(subscriptions)
    valid=[];denied=[];counter=0
    def payment(name,expected=None,*,association=own,subject=2,description=None,category=None,meta=None,course=None,invoice=None,account=None,archived=False,deleted=False,deny=False,zero=False):
        nonlocal counter
        pk='00000000-0000-0000-0000-000000000000' if zero else uid(131000+counter);counter+=1
        p=Payment(payment_id=pk,sport_association=association,subject=subject,description=description,payment_category=category,
            meta=meta,course=course,invoice=invoice,custom_accounts=account,archived=archived,deleted=deleted,amount=Decimal('1234567.89'))
        Payment._base_manager.bulk_create([p])
        if deny: denied.append(dict(Name=name,ID=pk,Association=str(own.pk)))
        else: valid.append(dict(Name=name,ID=pk,Association=str(association.pk),Category=str((category or (own_cat if association==own else global_cat)).pk),
            OldCategory=str(category.pk) if category else None,Description=expected,Invoice=str(invoice.pk) if invoice else None,
            Archived=archived,InvoiceArchived=bool(invoice and invoice.archived)))
        return p
    relation_count=0
    def relation(p,kind,title,*,association=own,parent=None,missing=False):
        nonlocal relation_count
        relation_count+=1;pk=uid(132000+relation_count)
        if kind in ('course','installment'):
            c=Course(course_id=pk,sport_association=association,title=title,deleted=True)
            Course._base_manager.bulk_create([c])
            cs=CourseSubscription(course_subscription_id=pk,course=c,subscription=parent,payment=p if kind=='course' else None,deleted=True)
            CourseSubscription._base_manager.bulk_create([cs])
            if kind=='installment':CourseSubscriptionInstallment._base_manager.bulk_create([CourseSubscriptionInstallment(
                course_subscription_installment_id=pk,course_subscription=cs,payment=p,deleted=True,payment_date=datetime(2024,1,1,tzinfo=timezone.utc))])
        else:
            c=None
            if not missing:
                c=Carnet(carnet_id=pk,sport_association=association,title=title);Carnet.objects.bulk_create([c])
            CarnetSubscription.objects.bulk_create([CarnetSubscription(carnet_subscription_id=pk,carnet_id=c,subscription=parent,payment=p,disabled=True)])
    try:
        payment('zero UUID membership','Iscrizione',subject=1,invoice=invoices[0],account=accounts[0],deleted=True,zero=True)
        payment('explicit global category','La categoria globale',subject=0,category=explicit_cat,archived=True)
        payment('course label','Danza, ',course={'label':'Danza'})
        p=payment('lowest deleted course','Corso storico');relation(p,'course','Corso storico');relation(p,'course','Corso successivo')
        p=payment('deleted installment','Rata');relation(p,'installment','Rata',parent=subscriptions[0])
        p=payment('disabled carnet','Carnet');relation(p,'carnet','Carnet')
        p=payment('course before other relations','Corso prioritario');relation(p,'course','Corso prioritario');relation(p,'installment','Ignorata');relation(p,'carnet','Ignorato')
        payment('post-placeholder JSON integer','17',description='Quota Corso',meta={'description':17})
        p=payment('related JSON false','False',meta={'description':False});relation(p,'course','Ignorato')
        p=payment('missing carnet with override','Override',meta={'description':'Override'});relation(p,'carnet',None,missing=True)
        payment('second association global default',default,association=other,subject=0)
        p=payment('explicit empty ignores reverse foreign course','',description='');relation(p,'course','FOREIGN',association=other)
        denied.append(dict(Name='foreign top-level payment',ID=valid[1]['ID'],Association=str(other.pk)))
        denied.append(dict(Name='missing payment',ID=uid(139999),Association=str(own.pk)))
        payment('foreign category',category=foreign_cat,deny=True)
        payment('foreign invoice',invoice=invoices[1],deny=True)
        payment('foreign account',account=accounts[1],deny=True)
        p=payment('foreign course',deny=True);relation(p,'course','FOREIGN',association=other)
        p=payment('foreign course membership',deny=True);relation(p,'course','FOREIGN',parent=subscriptions[1])
        p=payment('foreign installment course',deny=True);relation(p,'installment','FOREIGN',association=other)
        p=payment('foreign installment membership',deny=True);relation(p,'installment','FOREIGN',parent=subscriptions[1])
        p=payment('foreign carnet',deny=True);relation(p,'carnet','FOREIGN',association=other)
        p=payment('foreign carnet membership',deny=True);relation(p,'carnet','FOREIGN',parent=subscriptions[1])
        assert len(valid)==12 and len(denied)==11
        malformed=Payment(payment_id=uid(133000),sport_association=own,subject=2,meta={'description':17})
        Payment._base_manager.bulk_create([malformed]);relation(malformed,'course','Unused')
        scaled=[uid(140000+i) for i in range(4096)]
        Payment._base_manager.bulk_create([Payment(payment_id=key,sport_association=own,subject=2,amount=Decimal('0.01')) for key in scaled])
        scale_course=Course(course_id=uid(133010),sport_association=own,title='Corso scala',deleted=True)
        Course._base_manager.bulk_create([scale_course])
        CourseSubscription._base_manager.bulk_create([CourseSubscription(course_subscription_id=uid(150000+i),
            course=scale_course,payment_id=key,deleted=True) for i,key in enumerate(scaled)])
        before=snapshot();ct=ContentType.objects.get_for_model(Payment)
        # Authored expected descriptions are checked against actual model saves.
        # Category expectations explicitly differ: the old lookup selects the
        # lower-PK foreign category; the new reader must select own/global only.
        for row in valid:
            p=Payment._base_manager.get(pk=row['ID']);old_description=p.description;old_category=p.payment_category_id
            p.save();p.refresh_from_db()
            assert p.description==row['Description'],(row['Name'],p.description,row['Description'])
            assert str(p.payment_category_id)==str(old_category or foreign_cat.pk),(row['Name'],'unexpected legacy category')
            Payment._base_manager.filter(pk=p.pk).update(description=old_description,payment_category_id=old_category)
        # The legacy relation helper also exposes foreign titles. Rejecting those
        # references is an explicit scope correction, not a hidden normalization.
        for row in denied[5:]:
            p=Payment._base_manager.get(pk=row['ID'])
            assert p.get_course_carnet_name()=='FOREIGN',row['Name']
        with disable_auditlog():LogEntry.objects.filter(content_type=ct,object_pk__in=[r['ID'] for r in valid]).delete()
        assert snapshot()==before,'Django input characterization changed unrelated data'
        print('Django: 12 authored save descriptions, foreign default category and six reverse-reference scope defects established',flush=True)
        fixture=work/'payment-save-inputs.json'
        fixture.write_text(json.dumps(dict(Valid=valid,Denied=denied,Malformed=str(malformed.pk),Association=str(own.pk),Scaled=scaled)))
        completed=subprocess.run(['go','test','-race','-tags=paymentsaveinputsintegration','./internal/service','-run','^TestPaymentSaveInputsPostgres$','-json'],
            cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_PAYMENT_INPUTS_FIXTURE':str(fixture),
                'ASSOZETA_PAYMENT_INPUTS_DSN':f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable',
                'ASSOZETA_PAYMENT_INPUTS_PLAN':str(output/'query-plan.json')},capture_output=True,text=True,timeout=120)
        (output/'go-test.jsonl').write_text(completed.stdout)
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        for e in events:
            if e.get('Output'):print(e['Output'],end='',flush=True)
        if completed.stderr:print(completed.stderr,flush=True)
        completed.check_returncode()
        names={'one batch reproduces actual model descriptions and owned defaults','foreign and missing references fail the whole batch',
            'batch bounds duplicate scope and nil UUID','description failure does not return partial plans',
            'caller transaction sees its changes and missing default','constant queries and bounded pool under concurrent reads',
            'blocked query cancellation and connection recovery','executed plan over existing table indexes'}
        passed={e.get('Test') for e in events if e['Action']=='pass' and '/' in e.get('Test','')}
        expected={'TestPaymentSaveInputsPostgres/'+name.replace(' ','_') for name in names}
        assert passed==expected and not any(e['Action']=='skip' for e in events),'missing or skipped payment input group'
        assert snapshot()==before,'Go payment input reads changed persisted state'
        evidence=dict(success=True,scenario_groups=len(names),model_save_cases=len(valid),denial_cases=len(denied),
            scope='background save inputs only; caller authorization, locked mutation/audits and archive remain open')
        (output/'body-results.json').write_text(json.dumps(evidence,indent=2)+'\n')
        return len(names)
    finally:
        with disable_auditlog():
            LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Payment),object_pk__in=[str(pk) for pk in Payment._base_manager.values_list('pk',flat=True)]).delete()
            # Seeded with bulk writes: remove in FK order without unrelated model
            # deletion workflows. Every owned table was required to be empty.
            for model in (CarnetSubscription,CourseSubscriptionInstallment,CourseSubscription,Course,Carnet,Subscription,Payment,Invoice,PaymentCategory,CustomAccounts):
                model._base_manager.all()._raw_delete('default')
            SportAssociation.original_objects.filter(pk__in=[a.pk for a in associations]).delete()
            User.original_objects.filter(pk__in=[u.pk for u in users]).delete()
        assert snapshot()==original,'payment input fixture cleanup changed preexisting records'
