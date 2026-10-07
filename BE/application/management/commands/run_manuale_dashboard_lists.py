"""Owned fixture preparation/inspection for real dashboard lists; not UI evidence."""
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from application.management.commands.seed_manuale import assert_disposable, fixture_id
from application.models import User, SportAssociation, Associate, Subscription, Course, CourseSubscription, Invoice
from application.models.attendee_models import AttendanceRegistry, AttendanceDay
from application.models.subscriptions_models import MedicalCertificate
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.payment_models import Payment
from instance.models import InstanceConfiguration

MODELS = {m._meta.label_lower: m for m in [Associate, Subscription, Course, CourseSubscription,
    AttendanceRegistry, AttendanceDay, MedicalCertificate, Carnet, CarnetSubscription, Payment]}


class Command(BaseCommand):
    help = 'Prepare and inspect boundary fixtures for six dashboard list widgets in an owned disposable DB.'

    def add_arguments(self, parser):
        parser.add_argument('--action', required=True, choices=['prepare', 'inspect', 'cleanup'])
        parser.add_argument('--reference-date', required=True)
        parser.add_argument('--manifest', default='/manual-run/dashboard-lists-case.json')
        parser.add_argument('--result', default='/manual-run/dashboard-lists-result.json')

    def handle(self, *args, **options):
        assert_disposable()
        self.reference = date.fromisoformat(options['reference_date'])
        self.when = datetime.combine(self.reference, time(12), timezone.utc)
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.association = SportAssociation.objects.get(pk=fixture_id('association'), user=self.owner)
        config = InstanceConfiguration.get_config()
        if config is None or config.primary_association_id != self.association.pk:
            raise CommandError('Dashboard fixture must be the instance association.')
        if SportAssociation.objects.exclude(pk=self.association.pk).exists():
            raise CommandError('Dashboard fixture refuses another association.')
        manifest, result = Path(options['manifest']), Path(options['result'])
        if manifest.parent != Path('/manual-run') or result.parent != Path('/manual-run'):
            raise CommandError('Dashboard private evidence must remain in the owned run mount.')
        if options['action'] == 'prepare':
            if manifest.exists():
                raise CommandError('Existing dashboard fixture must be cleaned before preparation.')
            from freezegun import freeze_time
            with freeze_time(self.when), transaction.atomic():
                data = self.prepare()
                self.write_private(manifest, data)
            return
        data = json.loads(manifest.read_text())
        self.validate(data)
        if options['action'] == 'cleanup':
            with transaction.atomic():
                self.cleanup(data)
            manifest.unlink()
            return
        self.write_private(result, self.inspect(data))

    @staticmethod
    def write_private(path, data):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
            json.dump(data, stream, default=str, indent=2)
            stream.write('\n')
        os.chmod(path, 0o600)

    def prepare(self):
        if AttendanceDay.objects.exists() or CarnetSubscription.objects.exists():
            raise CommandError('Dashboard lists require reset baseline without lesson or carnet assignments.')
        if Subscription._base_manager.filter(sport_association=self.association).count() != 3:
            raise CommandError('Dashboard lists require exactly three reset baseline subscriptions.')
        data = {'run_id': os.environ['ASSOZETA_MANUAL_RUN_ID'], 'reference_date': self.reference.isoformat(),
            'owner_id': str(self.owner.pk), 'association_id': str(self.association.pk), 'records': [],
            'subscriptions': {}, 'medical': {}, 'payments': {}, 'carnets': {}, 'days': {},
            'fixture_only': True, 'baseline_invoice_ids': list(map(str,Invoice.objects.filter(sport_association=self.association).values_list('pk',flat=True)))}
        self.data = data
        def make(model, key, **fields):
            key = 'dashboard-lists-' + key
            identifier = fixture_id(key)
            if model._base_manager.filter(pk=identifier).exists():
                raise CommandError('Dashboard fixture identity collision: ' + key)
            obj = model.objects.create(pk=identifier, **fields)
            data['records'].append({'model': model._meta.label_lower, 'key': key, 'id': str(obj.pk)})
            return obj
        # New people preserve the three standard seed registrations and their existing payments.
        cases = [('pending', 'Marta', 'Riva', Subscription.PENDING, False, None),
            ('unsigned', 'Paolo', 'Russo', Subscription.NOT_SIGNED, False, None),
            ('past-pending', 'Elena', 'Sala', Subscription.PENDING, False, None),
            ('archived-pending', 'Nora', 'Villa', Subscription.PENDING, True, None),
            ('medical-today', 'Chiara', 'Fontana', Subscription.ACCEPTED, False, 0),
            ('medical-thirty', 'Davide', 'Greco', Subscription.ACCEPTED, False, 30),
            ('medical-expired', 'Emma', 'Ferrari', Subscription.ACCEPTED, False, -1),
            ('medical-outside', 'Fabio', 'Gallo', Subscription.ACCEPTED, False, 31),
            ('medical-archived', 'Irene', 'Marino', Subscription.ACCEPTED, True, -5)]
        for key, first, last, status, archived, offset in cases:
            person = make(Associate, 'person-' + key, sport_association=self.association, first_name=first,
                last_name=last, born_date=date(1995, 3, 10), born_city='Roma', email=first.lower()+'@example.test',
                creation_date=self.when, draft=False, deleted=False)
            certificate = None
            if offset is not None:
                certificate = make(MedicalCertificate, 'medical-' + key, user=self.owner,
                    expiration_date=self.reference + timedelta(days=offset), creation_date=self.when)
            registration = make(Subscription, 'subscription-' + key, sport_association=self.association,
                associate=person, user=self.owner, type=Subscription.ASSOCIATE_AND_MEMBER, status_flag=status,
                start_date=self.reference - timedelta(days=365),
                end_date=self.reference - timedelta(days=1) if key=='past-pending' else self.reference+timedelta(days=365),
                draft=False, archived=archived, deleted=False, creation_date=self.when, medical=certificate)
            data['subscriptions'][key] = {'id': str(registration.pk), 'name': first+' '+last, 'status': status}
            if certificate:
                data['medical'][key] = {'id': str(certificate.pk), 'subscription_id': str(registration.pk),
                    'offset': offset, 'expiration': str(certificate.expiration_date), 'archived': archived}
        course = make(Course, 'course', sport_association=self.association, title='Ginnastica in bacheca',
            status_flag=Course.ACTIVE, fee=0, creation_date=self.when, start_date=self.when-timedelta(days=3),
            end_date=self.when+timedelta(days=30))
        data['course_id'] = str(course.pk)
        data['enrollment_ids'] = []
        for index in (1, 2):
            enrollment = make(CourseSubscription, 'enrollment-'+str(index), course=course,
                subscription=Subscription.objects.get(pk=fixture_id('subscription-'+str(index))))
            data['enrollment_ids'].append(str(enrollment.pk))
        events=[]
        for key, offset in [('yesterday',-1),('today',0),('tomorrow',1)]:
            start=self.when+timedelta(days=offset)
            events.append({'event_id': str(fixture_id('dashboard-lists-event-'+key)),
                'title': 'Ginnastica in bacheca '+('oggi' if offset==0 else key),
                'start': start.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'end': (start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'allDay': False, 'extendedProps': {'description': 'Lezione dimostrativa del widget.'}})
        registry=make(AttendanceRegistry,'registry',course=course,status=AttendanceRegistry.PUBLISHED,events=events)
        for event,key in zip(events,['yesterday','today','tomorrow']):
            row=make(AttendanceDay,'day-'+key,attendance_registry=registry,title=event['title'],
                date=datetime.strptime(event['start'],'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc),
                associated_event=event['event_id'],attendees=[],expected_absences=[],auto_marked=False)
            data['days'][key]=str(row.pk)
        giulia=Subscription.objects.get(pk=fixture_id('subscription-1'))
        for key,left,activity in [('zero-recent',0,-1),('zero-old',0,-31),('one',1,None),('three',3,None),('four',4,None)]:
            carnet=make(Carnet,'carnet-'+key,user_id=self.owner,sport_association=self.association,
                title={'zero-recent':'Carnet esaurito recente','zero-old':'Carnet esaurito senza attività recente',
                       'one':'Carnet ultima lezione','three':'Carnet tre lezioni','four':'Carnet quattro lezioni'}[key],
                lessons_number=5,fee=50,public=False)
            usage=[] if activity is None else [{'date':(self.when+timedelta(days=activity)).strftime('%Y-%m-%d %H:%M:%S%z'),
                'title':'Lezione dimostrativa precedente','course':{'id':str(course.pk),'title':course.title}}]
            assignment=make(CarnetSubscription,'assignment-'+key,user_id=self.owner,subscription=giulia,
                carnet_id=carnet,disabled=False,meta={'lessons_left':left,'lessons_counter':5,'lessons_registry':usage})
            data['carnets'][key]={'id':str(assignment.pk),'title':carnet.title,'left':left,'subscription_id':str(giulia.pk)}
        # payment_date intentionally present: the current widget's subscription join uses it.
        payment_cases=[('overdue',-29,False,False,False,20),('today',0,False,False,False,30),
            ('future-seven',7,False,False,False,40),('past-outside',-31,False,False,False,50),
            ('future-outside',8,False,False,False,50),('paid',0,True,False,False,50),
            ('expense',0,False,True,False,50),('archived',0,False,False,True,50),('zero',0,False,False,False,0)]
        for key, offset, paid, expense, archived, amount in payment_cases:
            payment=make(Payment,'payment-'+key,user=self.owner,sport_association=self.association,
                associate=giulia.associate,amount=amount,type=Payment.CASH,subject=Payment.COURSE,
                description='Pagamento bacheca '+key,paid=paid,expense=expense,archived=archived,
                creation_date=self.when+timedelta(days=offset),payment_date=self.when)
            data['payments'][key]={'id':str(payment.pk),'offset':offset,'amount':amount}
        return data

    def validate(self, data):
        if (data.get('run_id')!=os.environ['ASSOZETA_MANUAL_RUN_ID'] or data.get('reference_date')!=str(self.reference)
                or data.get('owner_id')!=str(self.owner.pk) or data.get('association_id')!=str(self.association.pk)):
            raise CommandError('Dashboard manifest does not belong to this run/date/owner.')
        records=data.get('records',[])
        if not records or len({(r['model'],r['id']) for r in records})!=len(records):
            raise CommandError('Dashboard manifest records are missing or duplicated.')
        owned={label:{r['id'] for r in records if r['model']==label} for label in MODELS}
        for record in records:
            if record['model'] not in MODELS or not record['key'].startswith('dashboard-lists-') or record['id']!=str(fixture_id(record['key'])):
                raise CommandError('Dashboard manifest has an unowned identity.')
            obj=MODELS[record['model']]._base_manager.get(pk=record['id'])
            if hasattr(obj,'sport_association_id') and obj.sport_association_id!=self.association.pk:
                raise CommandError('Dashboard row belongs to another association.')
            if isinstance(obj,(Subscription,Payment,MedicalCertificate)) and obj.user_id!=self.owner.pk:
                raise CommandError('Dashboard row belongs to another user.')
            if isinstance(obj,(Carnet,CarnetSubscription)) and obj.user_id_id!=self.owner.pk:
                raise CommandError('Dashboard carnet belongs to another user.')
            if isinstance(obj,Payment) and str(obj.associate_id)!=str(fixture_id('associate-1')):
                raise CommandError('Dashboard payment person ownership mismatch.')
            if isinstance(obj,CourseSubscription) and (str(obj.course_id)!=data['course_id'] or str(obj.subscription_id) not in [str(fixture_id('subscription-1')),str(fixture_id('subscription-2'))]):
                raise CommandError('Dashboard enrollment parent ownership mismatch.')
            if isinstance(obj,CarnetSubscription) and (str(obj.carnet_id_id) not in owned[Carnet._meta.label_lower]
                    or str(obj.subscription_id)!=str(fixture_id('subscription-1'))):
                raise CommandError('Dashboard carnet assignment parent ownership mismatch.')
            if isinstance(obj,AttendanceRegistry) and str(obj.course_id)!=data['course_id']:
                raise CommandError('Dashboard calendar ownership mismatch.')
            if isinstance(obj,AttendanceDay) and str(obj.attendance_registry_id) not in owned[AttendanceRegistry._meta.label_lower]:
                raise CommandError('Dashboard lesson ownership mismatch.')
            if isinstance(obj,Subscription) and (str(obj.associate_id) not in owned[Associate._meta.label_lower]
                    or (obj.medical_id and str(obj.medical_id) not in owned[MedicalCertificate._meta.label_lower])):
                raise CommandError('Dashboard registration parent ownership mismatch.')

    def inspect(self,data):
        return {'fixture_only':False,'days':{key:AttendanceDay.objects.get(pk=pk).attendees for key,pk in data['days'].items()},
            'subscriptions':{key:Subscription._base_manager.get(pk=row['id']).status_flag for key,row in data['subscriptions'].items()},
            'medical':{key:str(MedicalCertificate.objects.get(pk=row['id']).expiration_date) for key,row in data['medical'].items()},
            'payments':{key:{'paid':Payment._base_manager.get(pk=row['id']).paid,
                'payment_date':str(Payment._base_manager.get(pk=row['id']).payment_date)} for key,row in data['payments'].items()},
            'carnets':{key:CarnetSubscription.objects.get(pk=row['id']).meta for key,row in data['carnets'].items()}}

    def cleanup(self,data):
        records=data['records']
        payment_ids=[r['id'] for r in records if r['model']==Payment._meta.label_lower]
        receipt_ids=list(Payment._base_manager.filter(pk__in=payment_ids).exclude(invoice=None).values_list('invoice_id',flat=True))
        if (set(map(str,receipt_ids)) & set(data['baseline_invoice_ids']) or
                Invoice.objects.filter(pk__in=receipt_ids).exclude(sport_association=self.association).exists() or
                Payment._base_manager.filter(invoice_id__in=receipt_ids).exclude(pk__in=payment_ids).exists()):
            raise CommandError('Dashboard cleanup refuses baseline/foreign/shared generated receipts.')
        owned={label:{r['id'] for r in records if r['model']==label} for label in MODELS}
        # Refuse every unrecorded concrete reverse FK/M2M relation before any cascade.
        # This also refuses generated receipts or shared clinical records; the run reset owns those.
        for record in records:
            model=MODELS[record['model']]
            for relation in model._meta.related_objects:
                related=relation.related_model
                ids=owned.get(related._meta.label_lower,set())
                query=related._base_manager.filter(**{relation.field.name:record['id']})
                if query.exclude(pk__in=ids).exists():
                    raise CommandError('Dashboard cleanup refuses unrecorded related rows: '+related._meta.label_lower)
        # Child-first deletion keeps soft-deleted registrations from retaining fixture parents.
        order=[AttendanceDay,AttendanceRegistry,CourseSubscription,CarnetSubscription,Carnet,Payment,Subscription,MedicalCertificate,Associate,Course]
        for model in order:
            model._base_manager.filter(pk__in=owned[model._meta.label_lower]).delete()
        generated_documents=list(Invoice.objects.filter(pk__in=receipt_ids).exclude(document_pdf=None).values_list('document_pdf_id',flat=True))
        Invoice.objects.filter(pk__in=receipt_ids,sport_association=self.association).delete()
        # Receipt rendering is asynchronous. Do not infer task completion or delete shared files.
        # Record observed orphan candidates privately; the capture runner's owned DB teardown/reset handles them.
        self.write_private(Path('/manual-run/dashboard-lists-cleanup.json'), {
            'run_id':data['run_id'],'fixture_records_removed':len(records),'generated_receipts_removed':list(map(str,receipt_ids)),
            'observed_document_ids_for_owned_reset':list(map(str,generated_documents)),
            'receipt_delivery_verified':False,'runner_reset_required':True})
