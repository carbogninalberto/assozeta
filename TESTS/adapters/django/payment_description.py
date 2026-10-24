"""Actual Payment.save description effects; no Go persistence/authorization claim."""
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
    from application.models import (User, SportAssociation, Payment, PaymentCategory,
        Course, CourseSubscription, CourseSubscriptionInstallment, CustomAccounts)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.models.audit_models import AuditLogIndex

    output=ROOT/'quality-reports/go-migration/payment-description'
    output.mkdir(parents=True,exist_ok=True)
    (output/'body-results.json').write_text('{"success":false}\n')
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    for model in (Payment,PaymentCategory,Course,CourseSubscription,CourseSubscriptionInstallment,Carnet,CarnetSubscription):
        assert not model._base_manager.exists(),f'{model._meta.label}: fixture requires empty owned table'
    ct=ContentType.objects.get_for_model(Payment)
    owner=User(user_id=uid(109000),username='payment-description-fixture',password='!fixture',role=1)
    User.original_objects.bulk_create([owner])
    association=SportAssociation(sport_association_id=uid(109001),user=owner,denomination='Payment descriptions',tax_code='00000000000')
    SportAssociation.original_objects.bulk_create([association])
    category=PaymentCategory(payment_category_id=uid(109002),name='Categoria attività',sport_association=association)
    PaymentCategory.objects.bulk_create([category])
    account=CustomAccounts(custom_account_id=uid(109003),name='Banca',initial_balance=Decimal('0.00'),account_code='fixture',sport_association=association)
    CustomAccounts.objects.bulk_create([account])
    cases=[]
    def add(name,expected=None,*,subject=2,description=None,meta=None,course=None,related=None,error=None,category_name='Categoria attività',pending_meta=False,bank=False):
        cases.append(dict(Name=name,Expected=expected,Subject=subject,Description=description,Meta=meta,Course=course,
            Related=related or [None,None,None],Error=bool(error),exception=error,CategoryName=category_name,pending_meta=pending_meta,bank=bank))
    def relation(slot,title='Titolo attività',meta=None,missing=False):
        values=[None,None,None];values[slot]=dict(Title=title,Meta=meta,MissingCarnet=missing);return values
    for subject,expected in ((0,'Categoria attività'),(1,'Iscrizione'),(2,'Quota Corso'),(99,None)):
        add(f'subject-{subject}',expected,subject=subject)
        add(f'empty-{subject}','',subject=subject,description='',meta={'description':'ignored'})
        add(f'explicit-{subject}','Descrizione già scelta',subject=subject,description='Descrizione già scelta',meta={'description':'ignored'})
        add(f'placeholder-{subject}','Override',subject=subject,description='QUOTA CORSO',meta={'description':'Override'})
    add('category-absent',None,subject=0,category_name=None)
    add('transfer-null-account',error=AttributeError,subject=3)
    add('transfer-missing-destination',error=AttributeError,subject=3,bank=True)
    add('transfer-explicit','Giroconto esistente',subject=3,description='Giroconto esistente')
    add('course-json-label','Danza, ',course={'label':'Danza'})
    add('course-json-empty-label',', ',course={'label':''})
    add('course-json-other','Quota Corso',course={'value':'ignored'})
    for i,value in enumerate((None,0,False,[],{})):
        add(f'course-json-invalid-label-{i}',error=TypeError,course={'label':value})
    for slot,name in enumerate(('course','installment','carnet')):
        add(f'{name}-title','Titolo attività',related=relation(slot))
        add(f'{name}-empty-title','',related=relation(slot,''))
        add(f'{name}-null-title','Quota Corso',related=relation(slot,None))
        add(f'{name}-meta','Da metadata',meta={'description':'Da metadata'},related=relation(slot,meta={'description':'Da metadata'}))
        add(f'{name}-meta-null',None,meta={'description':None},related=relation(slot,meta={'description':None}))
    add('course-before-installment-carnet','Corso',related=[dict(Title='Corso',Meta=None),dict(Title='Rata',Meta=None),dict(Title='Carnet',Meta=None)])
    add('installment-before-carnet','Rata',related=[None,dict(Title='Rata',Meta=None),dict(Title='Carnet',Meta=None)])
    add('null-course-does-not-fall-through','Quota Corso',related=[dict(Title=None,Meta=None),dict(Title='Rata',Meta=None),dict(Title='Carnet',Meta=None)])
    add('missing-carnet',error=AttributeError,related=relation(2,missing=True))
    add('missing-carnet-meta-bypass','Override',meta={'description':'Override'},related=relation(2,meta={'description':'Override'},missing=True))
    # Assignment after the lower() test accepts arbitrary JSON via CharField prep;
    # the same truthy non-string from a related payment fails before super().save.
    for name,value,expected in (('null',None,None),('empty','', ''),('zero',0,'0'),('false',False,'False'),('list-empty',[],'[]'),
            ('object-empty',{},'{}'),('true',True,'True'),('integer',17,'17'),('decimal',1.25,'1.25'),
            ('list',[None,True,"l'attività"], '[None, True, "l\'attività"]'),('object',{'a':True},"{'a': True}")):
        add(f'placeholder-value-{name}',expected,description='Quota Corso',meta={'description':value})
        add(f'related-value-{name}',expected,meta={'description':value},related=relation(0,meta={'description':value}),error=AttributeError if value and not isinstance(value,str) else None)
    for name,value,error in (('number',2,TypeError),('boolean',True,TypeError),('string-hit','description',TypeError),
            ('list-hit',['description'],TypeError),('string-miss','other',None),('list-miss',['other'],None),('empty-list',[],None)):
        add(f'placeholder-container-{name}','Quota Corso',description='Quota Corso',meta=value,error=error)
    add('pending-meta-does-not-replace-stored-relation','Stored',meta={'description':'Pending'},related=relation(0,meta={'description':'Stored'}),pending_meta=True)
    add('pending-placeholder-applies-after-stored-relation','Pending',meta={'description':'Pending'},related=relation(0,meta={'description':'Quota Corso'}),pending_meta=True)
    add('spaces-not-placeholder',' Quota Corso ',description=' Quota Corso ',meta={'description':'ignored'})
    add('lower-not-casefold','Quota Corſo',description='Quota Corſo',meta={'description':'ignored'})
    assert len(cases)==81 and len({case['Name'] for case in cases})==81
    go_cases=[]
    try:
        for index,row in enumerate(cases):
            payment=Payment(payment_id=uid(110000+index),sport_association=association,user=owner,subject=row['Subject'],
                payment_category=category if row['CategoryName'] else None,description=row['Description'],course=row['Course'],
                meta=row['Meta'],amount=Decimal('1234567.89'),custom_accounts=account if row['bank'] else None)
            if row['pending_meta']:
                payment.meta=next(r['Meta'] for r in row['Related'] if r is not None)
            Payment._base_manager.bulk_create([payment])
            for slot,r in enumerate(row['Related']):
                if r is None: continue
                # All course/instalment relations are soft-deleted on purpose:
                # get_course_carnet_name explicitly includes them.
                key=111000+index*10+slot
                if slot<2:
                    course=Course(course_id=uid(key),sport_association=association,title=r['Title'],deleted=True)
                    Course._base_manager.bulk_create([course])
                    cs=CourseSubscription(course_subscription_id=uid(key),course=course,payment=payment if slot==0 else None,deleted=True)
                    CourseSubscription._base_manager.bulk_create([cs])
                    if slot==1:
                        CourseSubscriptionInstallment._base_manager.bulk_create([CourseSubscriptionInstallment(
                            course_subscription_installment_id=uid(key),course_subscription=cs,payment=payment,deleted=True,
                            payment_date=datetime(2024,1,1,tzinfo=timezone.utc))])
                else:
                    carnet=None
                    if not r.get('MissingCarnet'):
                        carnet=Carnet(carnet_id=uid(key),sport_association=association,title=r['Title'])
                        Carnet.objects.bulk_create([carnet])
                    CarnetSubscription.objects.bulk_create([CarnetSubscription(carnet_subscription_id=uid(key),carnet_id=carnet,payment=payment,disabled=True)])
            payment=Payment._base_manager.get(pk=payment.pk)
            before=Payment._base_manager.filter(pk=payment.pk).values().get()
            if row['pending_meta']: payment.meta=row['Meta']
            if row['Error']:
                before_all=snapshot()
                try: payment.save()
                except row['exception']: pass
                else: raise AssertionError((row['Name'],'expected model error'))
                assert snapshot()==before_all,(row['Name'],'failed description wrote records')
            else:
                payment.save()
                after=Payment._base_manager.filter(pk=payment.pk).values().get()
                assert after==dict(before,description=row['Expected'],meta=row['Meta']),(row['Name'],after['description'],row['Expected'])
                changes={}
                if row['Description']!=row['Expected']: changes['description']=[str(row['Description']),str(row['Expected'])]
                # Pending JSON metadata has its own auditlog representation;
                # description assertion is independent of that field.
                logs=list(LogEntry.objects.filter(content_type=ct,object_pk=str(payment.pk)))
                assert len(logs)==int(bool(changes) or row['pending_meta']),row['Name']
                if logs:
                    assert logs[0].action==LogEntry.Action.UPDATE
                    assert logs[0].changes.get('description')==changes.get('description'),(row['Name'],logs[0].changes,changes)
                    assert set(logs[0].changes)==set(changes)|({'meta'} if row['pending_meta'] else set())
                    audit_index=AuditLogIndex.objects.get(log_entry=logs[0])
                    assert str(audit_index.sport_association_id)==str(association.pk) and audit_index.resolution_path=='direct'
            go_cases.append({**{k:row[k] for k in ('Name','Subject','Description','CategoryName','Meta','Course','Expected','Error')},
                             'Related':[r if r is not None else {} for r in row['Related']]})
        # Characterize rather than silently reproduce the unscoped category lookup.
        foreign_owner=User(user_id=uid(109007),username='foreign-payment-description-fixture',password='!fixture',role=1)
        User.original_objects.bulk_create([foreign_owner])
        foreign=SportAssociation(sport_association_id=uid(109004),user=foreign_owner,denomination='Foreign',tax_code='00000000001')
        SportAssociation.original_objects.bulk_create([foreign])
        default_name='entrate e proventi da attività tipiche'
        foreign_category=PaymentCategory(payment_category_id=uid(109005),sport_association=foreign,name=default_name,deleted=True,archived=True)
        PaymentCategory.objects.bulk_create([foreign_category,PaymentCategory(payment_category_id=uid(109006),name=default_name)])
        legacy=Payment(payment_id=uid(119999),sport_association=association,subject=0,amount=Decimal('0.01'))
        Payment._base_manager.bulk_create([legacy]);legacy.save();legacy.refresh_from_db()
        assert str(legacy.payment_category_id)==str(foreign_category.pk) and legacy.description==default_name
        fixture=work/'payment-description.json'
        fixture.write_text(json.dumps(dict(Cases=go_cases)))
        evidence=dict(success=True,actual_save_cases=len(cases),legacy_foreign_category_selected=True,
            scope='description calculation only; category authorization, batch relationship reads and full save/archive remain open')
        (output/'django-oracle.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(f'Django payment description: {len(cases)} actual-save cases and unscoped category defect verified',flush=True)
        completed=subprocess.run(['go','test','-race','-tags=paymentdescriptionintegration','./internal/service','-run','^TestPaymentDescriptionDjangoOracle$','-json'],
            cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_PAYMENT_DESCRIPTION_FIXTURE':str(fixture)},capture_output=True,text=True,timeout=120)
        (output/'go-test.jsonl').write_text(completed.stdout)
        for line in completed.stdout.splitlines():
            event=json.loads(line)
            if event.get('Action')=='fail' or ('Output' in event and ('FAIL' in event['Output'] or 'Error' in event['Output'])):print(event.get('Output',event),flush=True)
        if completed.stderr: print(completed.stderr,flush=True)
        completed.check_returncode()
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        expected={f"TestPaymentDescriptionDjangoOracle/{row['Name']}" for row in go_cases}
        passed={e.get('Test') for e in events if e['Action']=='pass' and '/' in e.get('Test','')}
        assert passed==expected and not any(e['Action']=='skip' for e in events),'missing or skipped payment description case'
        (output/'body-results.json').write_text(json.dumps(evidence,indent=2)+'\n')
        return len(cases)
    finally:
        with disable_auditlog():
            LogEntry.objects.filter(content_type=ct,object_pk__in=[str(pk) for pk in Payment._base_manager.values_list('pk',flat=True)]).delete()
            CarnetSubscription.objects.all().delete()
            CourseSubscriptionInstallment._base_manager.all().delete()
            CourseSubscription._base_manager.all().delete()
            Course._base_manager.all().delete()
            Carnet.objects.all().delete()
            Payment._base_manager.all().delete()
            PaymentCategory.objects.all().delete()
            CustomAccounts.objects.filter(pk=account.pk).delete()
            SportAssociation.original_objects.filter(pk__in=[association.pk,uid(109004)]).delete()
            User.original_objects.filter(pk__in=[owner.pk,uid(109007)]).delete()
        assert snapshot()==original,'payment description fixture changed preexisting managed state'
