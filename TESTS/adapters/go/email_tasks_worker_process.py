"""Native e-mail periodic task workers on actual Django-created current schema.

Private PostgreSQL, a private Redis broker (DB 0, default queue ``celery``)
and two local fake SMTP servers (STARTTLS and implicit TLS with a throwaway
certificate) that record every client byte. For each task
(send_user_partial_registration_email, send_expiring_certificate_email,
send_expired_certificate_email, send_reminders) the seed is first processed
by the actual Django task; the native Go job must then leave the identical
complete managed-model state, publish the same Celery messages (protocol 2
envelope checked; bodies equal to Django's) and produce the same SMTP
transcripts (only the MIME boundary, which Python randomizes, normalized).
Explicit fixture producers and ownership are not installed activation.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_email_tasks_worker'
REDIS_PASSWORD = 'private-redis-password-canary'
BRAND = 'Assozeta Fixture'
APP_URL = 'https://app.example.test'
HOST_USER = 'noreply@example.test'
SMTP_PASSWORD = 'smtp-fixture-secret'
TASKS = ('send_user_partial_registration_email', 'send_expiring_certificate_email',
         'send_expired_certificate_email', 'send_reminders')
TIMER_WAIT = 33
BOUNDARY = re.compile(r'={15}\d{19}==(\.\d+)?')
# smtplib's EHLO name is socket.getfqdn(), which depends on the host resolver's
# alias list (macOS returns an ip6.arpa alias for ::1 that Go's resolver does
# not); it is the only environment-derived value normalized besides the boundary.
EHLO = re.compile(r'\A(ehlo|helo) \S+\r\n\Z')
EXPECTED = {
    'default Django owner does not claim or send',
    'partial registrations: native worker reproduces the Django task',
    'expiring certificates: native worker reproduces the Django task',
    'expired certificates: native worker reproduces the Django task and its stop',
    'reminders: native worker reproduces the Django task and its delayed sends',
    'unsupported version and tenant fail before business effects',
    'an SMTP subject outside the ported subset refuses the run before any effect',
}


def U(n):
    return UUID(int=0xE5000000 + n)


class FakeSMTP:
    """Answers like a small ESMTP server and records the client bytes per connection."""

    def __init__(self, context, implicit):
        self.context, self.implicit = context, implicit
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.lock = threading.Lock()
        self.transcripts = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def take(self):
        with self.lock:
            taken, self.transcripts = self.transcripts, []
        return taken

    def serve(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.converse, args=(connection,), daemon=True).start()

    def converse(self, connection):
        units = []
        try:
            connection.settimeout(15)
            stream = self.context.wrap_socket(connection, server_side=True) if self.implicit else connection
            tls = self.implicit
            stream.sendall(b'220 fake.smtp.test ESMTP\r\n')
            buffer, data = b'', False
            while True:
                terminator = b'\r\n.\r\n' if data else b'\n'
                while terminator not in buffer:
                    chunk = stream.recv(65536)
                    if not chunk:
                        units.append('<eof>')
                        return
                    buffer += chunk
                end = buffer.index(terminator) + len(terminator)
                unit, buffer = buffer[:end], buffer[end:]
                units.append(unit.decode('latin-1'))
                if data:
                    data = False
                    stream.sendall(b'250 queued\r\n')
                    continue
                verb = unit.split(b' ', 1)[0].strip().upper()
                if verb == b'EHLO':
                    features = ['fake.smtp.test', 'SIZE 10485760', 'AUTH PLAIN LOGIN'] + ([] if tls else ['STARTTLS'])
                    stream.sendall(''.join(f'250{"-" if i < len(features) - 1 else " "}{f}\r\n'
                                           for i, f in enumerate(features)).encode())
                elif verb == b'STARTTLS':
                    stream.sendall(b'220 ready\r\n')
                    stream = self.context.wrap_socket(stream, server_side=True)
                    units.append('<tls>')
                    tls, buffer = True, b''
                elif verb == b'AUTH':
                    parts = unit.split()
                    secret = base64.b64decode(parts[2]).split(b'\0')[-1] if len(parts) > 2 else b''
                    stream.sendall(b'235 ok\r\n' if secret == SMTP_PASSWORD.encode() else b'535 denied\r\n')
                elif verb == b'DATA':
                    data = True
                    stream.sendall(b'354 go ahead\r\n')
                elif verb == b'QUIT':
                    stream.sendall(b'221 bye\r\n')
                    units.append('<quit>')
                    return
                elif verb in (b'MAIL', b'RCPT', b'RSET'):
                    stream.sendall(b'250 ok\r\n')
                else:
                    stream.sendall(b'500 unknown\r\n')
        except (OSError, ssl.SSLError) as error:
            units.append('<error ' + type(error).__name__ + '>')
        finally:
            connection.close()
            with self.lock:
                self.transcripts.append([EHLO.sub(r'\1 <local-hostname>\r\n', BOUNDARY.sub('<boundary>', unit)) for unit in units])

    def close(self):
        self.listener.close()
        self.thread.join(5)


def tls_context(directory):
    from TESTS.adapters.go.smtpsend_golden import certificate
    return certificate(directory)


class EmailFixture:
    """One seed for the four tasks; dates relative to one base instant."""

    def __init__(self, starttls_port, ssl_port):
        self.base = datetime.now(timezone.utc).replace(microsecond=0)
        self.ports = {'TLS': starttls_port, 'SSL': ssl_port}
        self.seeding = (self.base, self.base)

    def snapshot(self):
        from django.apps import apps
        since, until = self.seeding
        def value(item):
            return 'seed-default' if isinstance(item, datetime) and since <= item <= until else item
        return {m._meta.label: [{k: value(v) for k, v in row.items()} for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    def seed(self, variant='main'):
        from django.db import connection
        from application.models import User, SportAssociation, Reminders, UserPartial
        from application.models.user_models import Associate, AssociateTutorRelation, Instructor
        from application.models.subscriptions_models import Subscription, MedicalCertificate
        from communications.models import CommunicationConfiguration
        from instance.models import InstanceConfiguration
        started = datetime.now(timezone.utc)
        now, today = self.base, self.base.date()
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, bakney_user_partial, application_reminders,
              application_instructor, communications_communicationconfiguration, application_associatetutorrelation,
              application_subscription, application_medicalcertificate, application_associate, application_sportassociation,
              instance_instanceconfiguration, bakney_user RESTART IDENTITY CASCADE''')
        InstanceConfiguration.objects.create(domain='fixture.example.test', self_hosted=True, primary_color='#351DC2',
                                             name='Polisportiva <Fixture>')
        owners = [  # n, notifications
            (1, True), (2, False), (3, True), (4, True), (5, True), (6, True), (7, True), (8, True)]
        User.original_objects.bulk_create([User(user_id=U(n), username=f'email-owner-{n}', role=1, password='!private-fixture',
            date_joined=now, email=f'owner{n}@example.test', medical_certificate_notifications=notify) for n, notify in owners] +
            [User(user_id=U(9), username='email-athlete', role=2, password='!private-fixture', date_joined=now,
                  email='User1@Example.TEST')])
        denominations = {1: 'ASD Uno', 2: 'Senza notifiche', 3: "Società Tre & C.", 4: 'Quattro', 5: 'Cinque "SMTP"',
                         6: 'Sei', 7: 'Sette', 8: 'Otto'}
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=U(10 + n), user_id=U(n),
            denomination=name, tax_code='00000000000') for n, name in denominations.items()])
        configurations = [  # association, host, port mode, user, password, sender, encryption, limit, balance
            (13, '127.0.0.1', 'TLS', 'mailer@example.test', SMTP_PASSWORD, 'Società Tre', 'TLS', 1000, 5),
            (14, '127.0.0.1', 'SSL', 'apikey', SMTP_PASSWORD, None, 'SSL', 1000, 0),
            (15, None, None, None, None, 'Cinque', 'NONE', 1000, 0),
            (16, '127.0.0.1', 'TLS', 'mailer@example.test', SMTP_PASSWORD, None, 'TLS', 3, 3),
            (17, '127.0.0.1', 'TLS', 'mailer@example.test', 'wrong-password', None, 'TLS', 1000, 2),
            (18, '', None, None, None, None, 'NONE', 1000, 0)]
        CommunicationConfiguration.objects.bulk_create([CommunicationConfiguration(communication_configuration_id=U(30 + a),
            sport_association_id=U(a), email_smtp_host=h, email_smtp_port=self.ports[p] if p else None, email_smtp_user=u,
            email_smtp_password=pw, email_sender_name=sn, email_encryption=e, daily_email_limit=lim, daily_email_balance=bal)
            for a, h, p, u, pw, sn, e, lim, bal in configurations])
        associates = [  # n, association, first, last, email
            (61, 11, 'Anna', "D'Amico", 'Anna.Rossi@Example.Test'), (62, 11, 'Bruno', '<b>Neri</b>', 'not-an-email'),
            (63, 11, None, None, None), (64, 12, 'Carla', 'Skip', 'carla@example.test'),
            (65, 13, 'Dario', 'Tre', 'dario@example.test'), (66, 14, 'Elena', 'Quattro', 'ELENA@example.test'),
            (67, 15, 'Fabio', 'Cinque', 'fabio@example.test'), (68, 16, 'Gaia', 'Sei', 'gaia@example.test'),
            (69, 17, 'Hugo', 'Sette', 'hugo@example.test'), (70, 18, 'Ivo', 'Otto', 'ivo@example.test'),
            (71, 13, 'Luca', 'Tre bis', 'luca@example.test'), (72, 11, 'Tutor', 'Uno', 'Tutor.One@example.test'),
            (73, 11, 'Tutor', 'Due', 'second@example.test'), (74, 11, 'Zoë', 'Expired', 'zoe@example.test')]
        Associate.objects.bulk_create([Associate(associate_id=U(n), sport_association_id=U(a), first_name=f, last_name=l, email=e)
                                       for n, a, f, l, e in associates])
        # Associate 62: two non-primary tutors (the first by id becomes primary);
        # associate 64 (owner without notifications) keeps its non-primary tutor.
        AssociateTutorRelation.objects.bulk_create([
            AssociateTutorRelation(associate_id=U(62), tutor_id=U(72), is_primary=False),
            AssociateTutorRelation(associate_id=U(62), tutor_id=U(73), is_primary=False),
            AssociateTutorRelation(associate_id=U(64), tutor_id=U(73), is_primary=False)])
        week, month, yesterday = today + timedelta(days=7), today + timedelta(days=30), today - timedelta(days=1)
        subscriptions = [  # n, association, associate, expiration, extra
            (101, 11, 61, week, {}), (102, 11, 62, month, {}), (103, 11, 63, week, {}), (104, 12, 64, week, {}),
            (105, 13, 65, week, {}), (106, 14, 66, week, {}), (107, 15, 67, week, {}), (108, 16, 68, week, {}),
            (109, 17, 69, week, {}), (110, 18, 70, week, {}), (111, 13, 71, month, {}),
            (112, 11, 61, week, {'archived': True}), (113, 11, 61, week, {'deleted': True}),
            (114, 11, 61, week, {'creation_date': now - timedelta(days=400)}),
            (115, 11, 61, week, {'start_date': today - timedelta(days=300), 'end_date': yesterday}),
            (116, 11, 61, today + timedelta(days=8), {}),
            (117, 11, 61, week, {'start_date': today - timedelta(days=10), 'end_date': today}),
            # expired (yesterday): the subscription without associate stops the run.
            (120, 11, 74, yesterday, {}), (121, 13, 65, yesterday, {}), (122, 11, None, yesterday, {}),
            (123, 11, 61, yesterday, {})]
        MedicalCertificate.objects.bulk_create([MedicalCertificate(medical_id=U(n + 100), expiration_date=exp)
                                                for n, _, _, exp, _ in subscriptions])
        Subscription.objects.bulk_create([Subscription(subscription_id=U(n), sport_association_id=U(a),
            associate_id=U(p) if p else None, user_id=U(9), medical_id=U(n + 100), **{'creation_date': now - timedelta(days=10), **extra})
            for n, a, p, _, extra in subscriptions])
        partials = [('New.User@Example.Test', now - timedelta(hours=1)), ('ÇAĞLA@example.test',
                     datetime.combine(today - timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(minutes=30)),
                    ('old@example.test', now - timedelta(days=3))]
        UserPartial.objects.bulk_create([UserPartial(email=e, creation_date=c) for e, c in partials])
        Instructor.objects.bulk_create([Instructor(instructor_id=U(201), user_id=U(1), first_name='I', last_name='Uno',
                                                   email='inst@example.test'),
                                        Instructor(instructor_id=U(202), user_id=U(3), first_name='I', last_name='Tre', email=None)])
        title = 'Gara\nnotturna' if variant == 'unsupported' else 'Allenamento "speciale" & <festa>'
        reminders = [  # n, association, instructor, title, description, text, send_at, completed
            (301, 11, 201, title, 'Porta le scarpe', '1 ora', now - timedelta(minutes=5), False),
            (302, 11, None, 'Senza dettagli', None, None, now - timedelta(minutes=1), False),
            (303, 13, 202, 'Con SMTP', '', '30 minuti', now - timedelta(minutes=2), False),
            (304, 15, None, 'Configurazione senza SMTP', 'x', '2 ore', now - timedelta(minutes=3), False),
            (305, 11, None, 'Futuro', None, None, now + timedelta(hours=2), False),
            (306, 11, None, 'Completato', None, None, now - timedelta(hours=2), True),
            (307, None, None, 'Senza associazione', None, None, now - timedelta(hours=2), False)]
        if variant == 'unsupported':
            reminders = [(301, 13, None, title, 'x', '1 ora', now - timedelta(minutes=5), False)]
        Reminders.objects.bulk_create([Reminders(reminders_id=U(n), event_id=U(n + 1000), sport_association_id=U(a) if a else None, user_id=U(9),
            instructor_id=U(i) if i else None, event_title=t, event_description=d, event_reminder_text=x, send_at=s, completed=c)
            for n, a, i, t, d, x, s, c in reminders])
        self.seeding = (started, datetime.now(timezone.utc))
        return self.snapshot()

    @staticmethod
    def normalized(state, since, until):
        state = dict(state)
        keys, rows = {}, []
        for entry in state['auditlog.LogEntry']:
            entry = dict(entry)
            assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
            keys[entry['id']] = (entry['object_pk'], json.dumps(entry['changes'], sort_keys=True))
            entry.pop('id'); entry['timestamp'] = 'run'
            rows.append(entry)
        state['auditlog.LogEntry'] = sorted(rows, key=lambda row: (row['object_pk'], json.dumps(row['changes'], sort_keys=True)))
        indexes = []
        for index in state['application.AuditLogIndex']:
            index = dict(index)
            assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
            index.pop('audit_log_index_id'); index['created_at'] = 'run'
            index['log_entry_id'] = keys[index['log_entry_id']]
            indexes.append(index)
        state['application.AuditLogIndex'] = sorted(indexes, key=lambda row: row['log_entry_id'])
        return state


class Contract:
    def require(self, condition, label):
        assert condition, label


def spec_from(raw):
    """The authored spec check_celery_message needs, taken from Django's body."""
    from kombu.utils.json import loads
    message = json.loads(raw)
    args, kwargs, _ = loads(base64.b64decode(message['body']))
    def tagged(value):
        if isinstance(value, UUID):
            return {'uuid': str(value)}
        if isinstance(value, list):
            return [tagged(item) for item in value]
        return value
    return {'task': message['headers']['task'], 'args': [tagged(a) for a in args],
            'kwargs': [[key, tagged(value)] for key, value in kwargs.items()]}


class EmailRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries, redis_port, servers):
        import redis
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.servers = servers
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
            REDIS_HOST='127.0.0.1', REDIS_PORT=str(redis_port), REDIS_USERNAME='default', REDIS_PASSWORD=REDIS_PASSWORD,
            WHITELABEL_NAME=BRAND, IS_WHITELABEL='False', APP_URL=APP_URL, EMAIL_HOST_USER=HOST_USER,
            DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.broker = redis.Redis(host='127.0.0.1', port=redis_port, db=0, password=REDIS_PASSWORD, socket_timeout=5)
        self.redis_port = redis_port
        self.fixture = EmailFixture(servers['TLS'].port, servers['SSL'].port)
        self.django = {task: self.django_expected(task) for task in TASKS}

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-email-' + name)
        self.connections.append(connection)
        return connection

    def drain(self):
        raw = [item.decode() for item in reversed(self.broker.lrange('celery', 0, -1))]
        self.broker.delete('celery')
        return raw

    def transcripts(self):
        return {mode: server.take() for mode, server in self.servers.items()}

    def django_expected(self, task):
        """Run the actual Django task on a fresh seed with the production broker settings."""
        from contextlib import redirect_stdout, ExitStack
        import io
        from unittest.mock import patch
        from django.test import override_settings
        from core.celery import app
        from core import settings as core_settings
        from TESTS.adapters.django.family_harness import _reset_celery_app
        from application import tasks
        before = self.fixture.seed()
        self.drain(); self.transcripts()
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.redis_port}', 'celery', None))))
        _reset_celery_app(app)
        values = dict(WHITELABEL_NAME=BRAND, IS_WHITELABEL=False, APP_URL=APP_URL,
                      DEFAULT_FROM_EMAIL=f'{BRAND} <{HOST_USER}>', DEFAULT_TEAM_EMAIL=f'Il Team di {BRAND} <{HOST_USER}>')
        since, started = datetime.now(timezone.utc), time.time()
        try:
            with ExitStack() as stack:
                stack.enter_context(override_settings(**values))
                for name, value in values.items():
                    stack.enter_context(patch.object(core_settings, name, value))
                stack.enter_context(patch.object(tasks, 'WHITELABEL_NAME', BRAND))
                stack.enter_context(redirect_stdout(io.StringIO()))
                assert getattr(tasks, task).run() is None
                if task == 'send_reminders':
                    time.sleep(TIMER_WAIT)  # the Timer threads send after 1-30 s
        finally:
            app.conf.update(saved)
            _reset_celery_app(app)
        until = datetime.now(timezone.utc)
        raw = self.drain()
        specs = [spec_from(item) for item in raw]
        for i, (item, spec) in enumerate(zip(raw, specs)):
            from TESTS.adapters.django.family_harness import check_celery_message
            check_celery_message(Contract(), item, spec, since=started, until=time.time(), where=f':{i}')
        after = self.fixture.snapshot()
        transcripts = self.transcripts()
        self.authored_checks(task, before, after, specs, transcripts)
        bodies = sorted(json.loads(item)['body'] for item in raw)
        return (self.fixture.normalized(after, since, until), bodies, specs,
                {mode: sorted(json.dumps(t) for t in items) for mode, items in transcripts.items()})

    def authored_checks(self, task, before, after, specs, transcripts):
        """The oracle exercised the intended paths."""
        kwargs = [dict(spec['kwargs']) for spec in specs]
        recipients = sorted(str(k['recipient_list']) for k in kwargs)
        delivered = {mode: [t for t in items if '<quit>' in t] for mode, items in transcripts.items()}
        balances = {row['communication_configuration_id']: row['daily_email_balance']
                    for row in after['communications.CommunicationConfiguration']}
        if task == 'send_user_partial_registration_email':
            assert recipients == ["['new.user@example.test']", "['çağla@example.test']"], recipients
            assert [row['email'] for row in after['application.UserPartial']] == ['old@example.test']
            assert not any(transcripts.values())
        elif task == 'send_expiring_certificate_email':
            assert recipients == sorted(["['anna.rossi@example.test']", "['anna.rossi@example.test']",
                                         "['tutor.one@example.test']", "['user1@example.test']",
                                         "['fabio@example.test']", "['hugo@example.test']", "['ivo@example.test']"]), recipients
            fallback = [k for k in kwargs if k.get('skip_association_check')]
            assert sorted(k['from_email'] for k in fallback) == sorted([f'Cinque "SMTP" <{HOST_USER}>', f'Sette <{HOST_USER}>',
                                                                        f'Otto <{HOST_USER}>'])
            assert len(delivered['TLS']) == 2 and len(delivered['SSL']) == 1, transcripts
            assert balances[U(43)] == 7 and balances[U(44)] == 1 and balances[U(46)] == 3, balances
            relations = {(r['associate_id'], r['tutor_id']): r['is_primary'] for r in after['application.AssociateTutorRelation']}
            assert relations[(U(62), U(72))] and not relations[(U(62), U(73))] and not relations[(U(64), U(73))]
        elif task == 'send_expired_certificate_email':
            assert recipients == ["['zoe@example.test']"], recipients
            assert len(delivered['TLS']) == 1 and balances[U(43)] == 6, (transcripts, balances)
        else:
            assert recipients == sorted(["['User1@Example.TEST']", "['inst@example.test']", "['User1@Example.TEST']",
                                         "['User1@Example.TEST']"]), recipients
            completed = {row['reminders_id'] for row in after['application.Reminders'] if row['completed']}
            assert completed == {U(301), U(302), U(303), U(304), U(306)}, completed
            assert len(delivered['TLS']) == 1 and balances[U(43)] == 6, (transcripts, balances)

    def seed_job(self, kind, *, version=1, association=NIL, variant='main'):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(variant)
        self.drain(); self.transcripts()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,'{}',3,clock_timestamp())''', (identifier, association, kind, version, identifier))
        self.started, self.since = time.time(), datetime.now(timezone.utc)
        return identifier, before

    def matches_django(self, task, identifier):
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'registered e-mail worker durable completion', timeout=60)
        assert self.job(identifier)[:3] == ('completed', 1, '')
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        if task == 'send_reminders':
            time.sleep(TIMER_WAIT)
        state, bodies, specs, transcripts = self.django[task]
        actual = self.fixture.normalized(self.fixture.snapshot(), self.since, datetime.now(timezone.utc))
        if actual != state:
            differences = {label: {'django': state.get(label), 'go': actual.get(label)}
                           for label in set(state) | set(actual) if state.get(label) != actual.get(label)}
            (self.output / f'difference-{task}.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == state, f'Go {task} state differs from the Django task on the same seed'
        raw = self.drain()
        go = sorted((json.loads(item)['body'], item) for item in raw)
        if [body for body, _ in go] != bodies:
            decode = lambda items: [json.loads(base64.b64decode(item)) for item in items]
            (self.output / f'messages-{task}.json').write_text(json.dumps({'django': decode(bodies), 'go': decode([body for body, _ in go])},
                                                                         indent=1, ensure_ascii=False) + '\n')
        assert [body for body, _ in go] == bodies, f'Go {task} messages differ from the Django task'
        from TESTS.adapters.django.family_harness import check_celery_message
        for i, (body, item) in enumerate(go):
            check_celery_message(Contract(), item, spec_from(item), since=self.started, until=time.time(), where=f':{i}')
        actual_transcripts = {mode: sorted(json.dumps(t) for t in items) for mode, items in self.transcripts().items()}
        if actual_transcripts != transcripts:
            (self.output / f'smtp-{task}.json').write_text(json.dumps({'django': transcripts, 'go': actual_transcripts}, indent=1) + '\n')
        assert actual_transcripts == transcripts, f'Go {task} SMTP transcripts differ from the Django task'

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_job('send_expiring_certificate_email')
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        assert not self.drain() and not any(self.transcripts().values())
        self.stop(process)
        self.case('default Django owner does not claim or send')

        self.handoff('go')
        labels = {'send_user_partial_registration_email': 'partial registrations: native worker reproduces the Django task',
                  'send_expiring_certificate_email': 'expiring certificates: native worker reproduces the Django task',
                  'send_expired_certificate_email': 'expired certificates: native worker reproduces the Django task and its stop',
                  'send_reminders': 'reminders: native worker reproduces the Django task and its delayed sends'}
        for task in TASKS:
            identifier, _ = self.seed_job(task)
            process = self.worker()
            self.matches_django(task, identifier)
            self.stop(process)
            self.case(labels[task])

        for kwargs, code in [({'version': 2}, 'unsupported_version'), ({'association': str(U(11))}, 'task_failed')]:
            for task in TASKS:
                identifier, before = self.seed_job(task, **kwargs)
                process = self.worker()
                await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid e-mail task envelope rejected')
                self.stop(process)
                assert self.job(identifier)[:2] == ('failed', 1)
                assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
                assert self.fixture.snapshot() == before and not self.drain() and not any(self.transcripts().values())
        self.case('unsupported version and tenant fail before business effects')

        identifier, before = self.seed_job('send_reminders', variant='unsupported')
        process = self.worker()
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'unsupported SMTP subject refused')
        self.stop(process)
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('task_failed',)]
        assert self.fixture.snapshot() == before and not self.drain() and not any(self.transcripts().values())
        self.case('an SMTP subject outside the ported subset refuses the run before any effect')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)

    def worker(self, *arguments):
        return super().worker('--task-timeout', '50s', *arguments)

    def close(self):
        try:
            super().close()
        finally:
            self.broker.close()


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/email-tasks-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server:
        raise RuntimeError('Private PostgreSQL, Redis and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-email-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None; redis_process = None; servers = {}
        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log,
                                  stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work/name), './cmd/'+name], cwd=ROOT/'BE_GO',
                    env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1',
                '--requirepass', REDIS_PASSWORD, '--save', '', '--appendonly', 'no', '--dir', str(work)],
                stdout=log, stderr=subprocess.STDOUT)
            context = tls_context(work)
            servers = {'TLS': FakeSMTP(context, False), 'SSL': FakeSMTP(context, True)}
            run([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = EmailRehearsal(work, output, environment, binaries, redis_port, servers)
            rehearsal.run()
        finally:
            try:
                if rehearsal:
                    result.update(scenarios=rehearsal.completed, scenario_count=len(rehearsal.completed))
                    rehearsal.close()
            finally:
                try:
                    for server in servers.values():
                        server.close()
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    try:
                        if redis_process:
                            redis_process.terminate(); redis_process.wait(timeout=10)
                        if attempted:
                            try: run([binaries/'pg_ctl', '-D', work/'postgres', '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work/'postgres/postmaster.pid').exists(): raise
                    finally:
                        if (work/'postgres.log').exists(): shutil.copyfile(work/'postgres.log', output/'postgres.log')
                        report.write_text(json.dumps(result, indent=2)+'\n')
    paths = ['TESTS/adapters/go/email_tasks_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/internal/service/email_tasks.go', 'BE_GO/internal/repository/email_tasks.go',
             'BE_GO/internal/domain/email_tasks.go', 'BE_GO/internal/smtpsend/smtpsend.go', 'BE_GO/internal/smtpsend/mime.go',
             'BE/application/tasks.py', 'BE/communications/models.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
