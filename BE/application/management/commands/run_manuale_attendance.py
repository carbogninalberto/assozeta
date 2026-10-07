"""Run real attendance task logic only inside an explicitly owned manual fixture.

Preparation is fixture data, never evidence of a user creating these records.
Task execution is synchronous, not evidence of Celery beat/broker delivery.
"""
import json
import os
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from application.management.commands.seed_manuale import assert_disposable, fixture_id
from application.models import User, SportAssociation, Associate, Subscription, Course, CourseSubscription, AttendanceRegistry, AttendanceDay
from application.models.carnet_models import Carnet, CarnetSubscription
from application.models.payment_models import Payment
from instance.models import InstanceConfiguration

CASES = [('paid', 'Giulia', 'Bianchi', [(5, False, True)]),
         ('absence', 'Sara', 'Conti', [(5, False, True)]),
         ('unpaid', 'Luca', 'Verdi', [(3, False, False)]),
         ('exhausted', 'Marco', 'Neri', [(0, False, True)]),
         ('disabled', 'Elena', 'Marini', [(4, True, True)]),
         ('multiple', 'Paola', 'Rossi', [(2, False, True), (5, False, True)]),
         ('no-carnet', 'Andrea', 'Costa', []),
         ('unpaid-priority', 'Tommaso', 'Testa', [(1, False, False), (5, False, True)])]


class Command(BaseCommand):
    help = 'Prepare, invoke and inspect the actual attendance task in a disposable manual DB.'

    def add_arguments(self, parser):
        parser.add_argument('--action', required=True, choices=['prepare', 'run-worker', 'inspect', 'cleanup'])
        parser.add_argument('--reference-date', required=True)
        parser.add_argument('--manifest', default='/manual-run/attendance-case.json')
        parser.add_argument('--result', default='/manual-run/attendance-result.json')

    def handle(self, *args, **options):
        assert_disposable()
        self.reference = date.fromisoformat(options['reference_date'])
        self.when = datetime.combine(self.reference, time(12), timezone.utc)
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.association = SportAssociation.objects.get(pk=fixture_id('association'), user=self.owner)
        config = InstanceConfiguration.get_config()
        if config is None or config.primary_association_id != self.association.pk:
            raise CommandError('Attendance fixture is not the instance association.')
        # The real task loops across owners. Refuse any other owner/courses before invoking it.
        if SportAssociation.objects.exclude(pk=self.association.pk).exists():
            raise CommandError('Attendance task refuses a database containing another association.')
        manifest = Path(options['manifest'])
        if manifest.parent != Path('/manual-run') or Path(options['result']).parent != Path('/manual-run'):
            raise CommandError('Private attendance files must remain in the owned run mount.')
        if options['action'] == 'prepare':
            if manifest.exists():
                raise CommandError('Existing attendance manifest must be cleaned before preparation.')
            from freezegun import freeze_time
            with freeze_time(self.when), transaction.atomic():
                data = self.prepare()
                self.write_private(manifest, data)
        else:
            data = json.loads(manifest.read_text())
            if data['run_id'] != os.environ['ASSOZETA_MANUAL_RUN_ID'] or data['reference_date'] != self.reference.isoformat():
                raise CommandError('Attendance manifest is not owned by this run/date.')
            self.validate(data)
            if options['action'] == 'cleanup':
                with transaction.atomic():
                    self.cleanup(data)
                manifest.unlink()
                return
            if options['action'] == 'run-worker':
                if User._base_manager.filter(role=User.ASSOCIATION, auto_mark_attendance=True).exclude(pk=self.owner.pk).exists():
                    raise CommandError('Attendance task refuses another enabled owner.')
                if AttendanceDay.objects.exclude(pk=data['day_id']).exists():
                    raise CommandError('Attendance task refuses other lesson rows outside this manifest.')
                if not self.owner.auto_mark_attendance:
                    raise CommandError('The real settings UI must enable automatic attendance first.')
                payments = Payment.objects.filter(pk__in=data['settle_payment_ids'])
                if payments.count() != len(data['settle_payment_ids']) or payments.filter(paid=False).exists():
                    raise CommandError('Carnet payments must first be settled by the production handler.')
                from application.tasks import auto_mark_attendance
                from freezegun import freeze_time
                with freeze_time(self.when + timedelta(hours=1)), redirect_stdout(self.stderr):
                    auto_mark_attendance.run()
            result = self.inspect(data)
            if options['action'] == 'run-worker':
                self.assert_worker_result(data, result)
            self.write_private(Path(options['result']), result)

    @staticmethod
    def write_private(path, data):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
            json.dump(data, stream, default=str, indent=2)
            stream.write('\n')
        os.chmod(path, 0o600)

    def prepare(self):
        if AttendanceDay.objects.exists():
            raise CommandError('Attendance preparation requires the reset baseline without other lesson rows.')
        keys = ['automatic-athlete', 'automatic-course', 'automatic-registry', 'automatic-day']
        for key, model in zip(keys, [User, Course, AttendanceRegistry, AttendanceDay]):
            if model._base_manager.filter(pk=fixture_id(key)).exists():
                raise CommandError('Attendance fixture identity collision: ' + key)
        athlete = User.objects.create(pk=fixture_id(keys[0]), role=User.ATHLETE, first_name='Giulia', last_name='Bianchi',
            username='manuale.atleta.automatiche', email='automatiche@aurora.example.test', is_active=True, deleted=False)
        athlete.set_unusable_password(); athlete.save(update_fields=['password'])
        course = Course.objects.create(pk=fixture_id(keys[1]), sport_association=self.association,
            title='Ginnastica presenze automatiche', status_flag=Course.ACTIVE, fee=0, creation_date=self.when,
            start_date=self.when, end_date=self.when + timedelta(days=30))
        start = self.when + timedelta(minutes=30)
        event_id = str(fixture_id('automatic-event'))
        event = {'event_id': event_id, 'title': 'Lezione automatica di ginnastica', 'start': start.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                 'end': (start + timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S.000Z'), 'allDay': False,
                 'extendedProps': {'description': 'Lezione dimostrativa delle presenze automatiche.'}}
        registry = AttendanceRegistry.objects.create(pk=fixture_id(keys[2]), course=course, status=AttendanceRegistry.PUBLISHED, events=[event])
        day = AttendanceDay.objects.create(pk=fixture_id(keys[3]), attendance_registry=registry, title=event['title'], date=start,
            associated_event=event_id, attendees=[], expected_absences=[], auto_marked=False)
        data = {'run_id': os.environ['ASSOZETA_MANUAL_RUN_ID'], 'reference_date': self.reference.isoformat(),
                'owner_id': str(self.owner.pk), 'association_id': str(self.association.pk), 'course_id': str(course.pk),
                'registry_id': str(registry.pk), 'day_id': str(day.pk), 'athlete_id': str(athlete.pk),
                'settle_payment_ids': [], 'cases': []}
        for index, (key, first, last, balances) in enumerate(CASES):
            user = athlete if key in ('paid', 'absence') else self.owner
            person = Associate.objects.create(pk=fixture_id('automatic-person-' + key), sport_association=self.association,
                first_name=first, last_name=last, user=user if user==athlete else None, email=first.lower()+'@example.test',
                # Distinguish these fictional cases from baseline names with
                # overlapping dates; keep the production duplicate guard active.
                tax_code=f'MANUALEAUTO{index:05d}',
                born_date=date(1995, 3, 10), born_city='Roma', draft=False, deleted=False, creation_date=self.when)
            subscription = Subscription.objects.create(pk=fixture_id('automatic-subscription-' + key), sport_association=self.association,
                associate=person, user=user, type=Subscription.ASSOCIATE_AND_MEMBER, status_flag=Subscription.ACCEPTED,
                start_date=self.reference - timedelta(days=1), end_date=self.reference + timedelta(days=365),
                draft=False, archived=False, deleted=False, creation_date=self.when)
            enrolled = CourseSubscription.objects.create(pk=fixture_id('automatic-enrollment-' + key), course=course, subscription=subscription)
            item = {'key': key, 'name': first+' '+last, 'person_id': str(person.pk), 'subscription_id': str(subscription.pk),
                    'enrollment_id': str(enrolled.pk), 'carnets': []}
            for number, (remaining, disabled, settle) in enumerate(balances):
                suffix = key+'-'+str(number)
                carnet = Carnet.objects.create(pk=fixture_id('automatic-carnet-'+suffix), user_id=self.owner, sport_association=self.association,
                    title='Carnet '+first+' '+str(number+1), lessons_number=5, fee=50, public=False)
                payment = Payment.objects.create(pk=fixture_id('automatic-payment-'+suffix), user=self.owner,
                    sport_association=self.association, associate=person, amount=50, type=Payment.CASH, subject=Payment.COURSE,
                    description='Carnet '+first+' '+str(number+1), paid=False, expense=False, archived=False, creation_date=self.when)
                assignment = CarnetSubscription.objects.create(pk=fixture_id('automatic-assignment-'+suffix), user_id=self.owner,
                    subscription=subscription, carnet_id=carnet, payment=payment, disabled=disabled,
                    meta={'lessons_left':remaining, 'lessons_counter':5, 'lessons_registry':[]})
                assignment.course_subscription.add(enrolled)
                item['carnets'].append({'id':str(assignment.pk), 'carnet_id':str(carnet.pk), 'payment_id':str(payment.pk),
                    'initial_balance':remaining,'disabled':disabled,'settle':settle})
                if settle: data['settle_payment_ids'].append(str(payment.pk))
            data['cases'].append(item)
        from freezegun import freeze_time
        from application.services.jwt_token_service import JWTTokenService
        with freeze_time(self.when):
            tokens=JWTTokenService.generate_tokens_for_user(athlete)
        data['athlete']={'user_id':str(athlete.pk),'token':tokens['access_token'],'refresh_token':tokens['refresh_token']}
        return data

    def validate(self, data):
        if data['owner_id'] != str(self.owner.pk) or data['association_id'] != str(self.association.pk):
            raise CommandError('Attendance manifest owner mismatch.')
        course=Course._base_manager.get(pk=data['course_id'])
        if str(course.pk)!=str(fixture_id('automatic-course')) or course.sport_association_id != self.association.pk:
            raise CommandError('Attendance course ownership mismatch.')
        athlete=User._base_manager.get(pk=data['athlete_id'])
        if str(athlete.pk)!=str(fixture_id('automatic-athlete')) or athlete.role!=User.ATHLETE or athlete.username!='manuale.atleta.automatiche':
            raise CommandError('Attendance athlete identity mismatch.')
        if CourseSubscription.objects.filter(course=course).exclude(pk__in=[item['enrollment_id'] for item in data['cases']]).exists():
            raise CommandError('Attendance course contains an unrecorded enrollment.')
        for item in data['cases']:
            key=item['key']
            if not any(case[0]==key for case in CASES): raise CommandError('Unknown attendance case.')
            for field, prefix, model in [('subscription_id','automatic-subscription-',Subscription),('person_id','automatic-person-',Associate)]:
                obj=model._base_manager.get(pk=item[field])
                if str(obj.pk)!=str(fixture_id(prefix+key)) or obj.sport_association_id!=self.association.pk:
                    raise CommandError('Attendance case ownership mismatch.')
            enrolled=CourseSubscription.objects.get(pk=item['enrollment_id'],course=course,subscription_id=item['subscription_id'])
            for n,carnet in enumerate(item['carnets']):
                suffix=key+'-'+str(n)
                if any(carnet[field]!=str(fixture_id(prefix+suffix)) for field,prefix in [('id','automatic-assignment-'),('carnet_id','automatic-carnet-'),('payment_id','automatic-payment-')]):
                    raise CommandError('Attendance carnet identity mismatch.')
                assignment=CarnetSubscription.objects.get(pk=carnet['id'],subscription_id=item['subscription_id'],
                    carnet_id=carnet['carnet_id'],payment_id=carnet['payment_id'],carnet_id__sport_association=self.association)
                if assignment.course_subscription.exclude(pk=enrolled.pk).exists():
                    raise CommandError('Attendance carnet has an unrecorded course link.')
                Payment.objects.get(pk=carnet['payment_id'],sport_association=self.association,associate_id=item['person_id'])

    def inspect(self, data):
        day=AttendanceDay.objects.get(pk=data['day_id'],attendance_registry_id=data['registry_id'],attendance_registry__course_id=data['course_id'])
        return {'day_id':str(day.pk),'auto_marked':day.auto_marked,'attendees':day.attendees,'expected_absences':day.expected_absences,
                'cases':{item['key']:[{'id':c['id'],'balance':CarnetSubscription.objects.get(pk=c['id']).meta['lessons_left'],
                    'usage':CarnetSubscription.objects.get(pk=c['id']).meta['lessons_registry'],
                    'paid':Payment.objects.get(pk=c['payment_id']).paid} for c in item['carnets']] for item in data['cases']}}

    def assert_worker_result(self, data, result):
        expected=['paid','disabled','multiple','no-carnet']
        attendee_ids=sorted(a['course_subscription_id'] for a in result['attendees'])
        if not result['auto_marked'] or attendee_ids!=sorted(item['enrollment_id'] for item in data['cases'] if item['key'] in expected):
            raise CommandError('Actual attendance task did not persist the expected participants.')
        balances={'paid':[4],'absence':[5],'unpaid':[3],'exhausted':[0],'disabled':[4],'multiple':[1,5],'no-carnet':[],'unpaid-priority':[1,5]}
        for key,values in balances.items():
            if [c['balance'] for c in result['cases'][key]]!=values:
                raise CommandError('Actual task carnet balance mismatch: '+key)
        if sorted(a['course_subscription_id'] for a in result['expected_absences'])!=[next(i['enrollment_id'] for i in data['cases'] if i['key']=='absence')]:
            raise CommandError('Real athlete absence must be recorded before invoking the task.')
        for key,rows in result['cases'].items():
            for index,row in enumerate(rows):
                if (key not in ['paid','multiple'] or index>0) and row['usage']!=[]:
                    raise CommandError('Actual task changed an unconsumed usage register: '+key)
        for key in ['paid','multiple']:
            usage=result['cases'][key][0]['usage']
            if len(usage)!=1 or usage[0]['course']['id']!=data['course_id'] or usage[0]['title']!='Lezione automatica di ginnastica':
                raise CommandError('Actual task must persist the carnet usage entry.')

    def cleanup(self, data):
        # Refuse shared/foreign parent links rather than cascade outside this manifest.
        subscription_ids=[i['subscription_id'] for i in data['cases']]
        if CourseSubscription.objects.filter(subscription_id__in=subscription_ids).exclude(course_id=data['course_id']).exists():
            raise CommandError('Attendance cleanup refuses subscriptions linked to another course.')
        if Subscription._base_manager.filter(user_id=data['athlete_id']).exclude(pk__in=subscription_ids).exists():
            raise CommandError('Attendance cleanup refuses unrelated athlete subscriptions.')
        if Associate._base_manager.filter(user_id=data['athlete_id']).exclude(pk__in=[i['person_id'] for i in data['cases']]).exists():
            raise CommandError('Attendance cleanup refuses unrelated athlete people.')
        for item in data['cases']:
            if Subscription._base_manager.filter(associate_id=item['person_id']).exclude(pk=item['subscription_id']).exists():
                raise CommandError('Attendance cleanup refuses a shared person.')
            for c in item['carnets']:
                if Subscription._base_manager.filter(payment_id=c['payment_id']).exists() or CourseSubscription.objects.filter(payment_id=c['payment_id']).exists():
                    raise CommandError('Attendance cleanup refuses a shared payment.')
                if CarnetSubscription.objects.filter(carnet_id=c['carnet_id']).exclude(pk=c['id']).exists():
                    raise CommandError('Attendance cleanup refuses a shared carnet.')
        Course.objects.filter(pk=data['course_id'],sport_association=self.association).delete()
        for item in data['cases']:
            for c in item['carnets']:
                CarnetSubscription.objects.filter(pk=c['id'],subscription_id=item['subscription_id']).delete()
                Carnet.objects.filter(pk=c['carnet_id'],sport_association=self.association).delete()
                Payment._base_manager.filter(pk=c['payment_id'],sport_association=self.association).delete()
            Subscription._base_manager.filter(pk=item['subscription_id'],sport_association=self.association).delete()
            Associate._base_manager.filter(pk=item['person_id'],sport_association=self.association).delete()
        User._base_manager.filter(pk=data['athlete_id'],role=User.ATHLETE,username='manuale.atleta.automatiche').delete()
