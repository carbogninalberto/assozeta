"""Differential harness for the Go Celery consumer's e-mail tasks.

Extends ``celery_consumer_process.py`` (same private PostgreSQL with the
current Django schema, private Redis and recording fake SMTP server) to
``send_email_text``, ``send_email_template`` and ``send_email_html``
(BE/application/tasks.py) and to the ``core.tasks.send_mail_async`` paths that
the first slice handed to Django: ``CommunicationConfiguration.send_email``
(SMTP delivery with the balance save and its audit entry, daily limit,
fallback publish under the association's name), the instance email source
(``instance.email_configuration.effective_email``) and ``fail_silently=True``.

Each scenario publishes one message with Django's producer, routed with the
scenario's ``GO_TASK_QUEUE_TASKS`` names, runs it on a real Django worker (the
production prefork pool in a subprocess; CELERY_CONSUMER_ORACLE=thread selects
the earlier in-thread solo worker, see celery_consumer_process.py) and,
after re-seeding, on the race-built Go consumer, and compares the SMTP
transcripts, the complete managed-model state (TaskResult, EmailLog,
CommunicationConfiguration, auditlog rows; run-window timestamps
normalized), and every broker key. Messages the task publishes (children)
are compared field by field with their random id, origin, reply_to and
publish stamp normalized; their root and parent ids are compared against the
original. In the prefork worker a message published from the task body (and a
Task.retry message) is recorded in the stored ``meta.children``; one published
from send_email_html's Timer thread is not. ``send_email_html``'s Timer send (1-30 s after the task) is waited
for on both sides; one scenario stops Go right after the task result so the
consumer's shutdown must wait for the pending send. Handoff scenarios require
Go to move the message unchanged to the ``celery`` queue, where a Django
worker reaches the Django-only final state.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import hashlib
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go import celery_consumer_process as base
from TESTS.adapters.go.celery_consumer_process import (U, ASSOCIATION, CONFIGURED, OWNER, BRAND, HOST_USER, GO_QUEUE, DJANGO_QUEUE,
                                                       REDIS_PASSWORD, DATABASE)
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import FakeSMTP, SMTP_PASSWORD, tls_context, free_port, EHLO, BOUNDARY

OUTPUT = ROOT / 'quality-reports/go-migration/celery-consumer-email-process'
base.OUTPUT = OUTPUT  # consumer logs and difference.json of the shared Harness
DJANGO_ONLY = base.DJANGO_ONLY
SECRET = 'private-worker-secret-canary'  # the consumer's SECRET_KEY (env file)
APP_URL = 'https://app.example.test'
MAIL = 'core.tasks.send_mail_async'
TEXT, TEMPLATE, HTML = 'send_email_text', 'send_email_template', 'send_email_html'
NEW_SUBSCRIPTION = 'email/account/email_new_subscription.html'
CHANGED = 'email/account/email_subscription_medical_certificate_changed.html'

SMTP_OK, LIMITED, SMTP_BAD = U(14), U(15), U(16)
CONFIG_OK, CONFIG_LIMITED, CONFIG_BAD = U(32), U(33), U(34)


class RefusingSMTP(FakeSMTP):
    """FakeSMTP that refuses RCPT for addresses containing 'rifiutato' (550)."""

    def converse(self, connection):
        units = []
        try:
            connection.settimeout(15)
            stream = connection
            tls = False
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
                    features = ['fake.smtp.test', 'SIZE 10485760', 'AUTH PLAIN LOGIN']
                    stream.sendall(''.join(f'250{"-" if i < len(features) - 1 else " "}{f}\r\n'
                                           for i, f in enumerate(features)).encode())
                elif verb == b'AUTH':
                    import base64
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
                elif verb == b'RCPT' and b'rifiutato' in unit:
                    stream.sendall(b'550 mailbox unavailable\r\n')
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


class Scenario(base.Scenario):
    """A message of ``task`` routed by ``routes``; ``unchecked`` publishes with app.send_task (no argument check).

    ``celery_left`` messages stay on Django's queue (children routed there); ``effect`` is a delayed effect both
    sides must reach before stopping (send_email_html's Timer); ``early_stop`` stops Go once the task result exists."""

    def __init__(self, name, task, kwargs=None, *, args=(), routes=(), unchecked=False, celery_left=0, effect=None,
                 early_stop=False, **rest):
        super().__init__(name, kwargs or {}, **rest)
        self.task, self.args, self.routes, self.unchecked = task, list(args), tuple(routes) or (task,), unchecked
        self.celery_left, self.effect, self.early_stop = celery_left, effect, early_stop


def subscription_data(first='Giulia', last='Rossi', denomination='ASD Uno'):
    return {'athlete_first_name': first, 'athlete_last_name': last, 'sport_association': {'denomination': denomination},
            'app_host': APP_URL, 'settings': {'WHITELABEL_NAME': BRAND, 'IS_WHITELABEL': False}}


def delivered(count):
    return lambda stack: len([t for t in stack.smtp.transcripts if any(unit.endswith('\r\n.\r\n') for unit in t)]) >= count


def balance(config, value):
    def check(stack):
        from communications.models import CommunicationConfiguration
        return CommunicationConfiguration.objects.filter(pk=config, daily_email_balance=value).exists()
    return check


def queued(count):
    return lambda stack: stack.broker.llen(DJANGO_QUEUE) >= count


def both(*checks):
    return lambda stack: all(check(stack) for check in checks)


SUBJECT = 'Conferma iscrizione'
TO = ['socio@example.test']


def mail(**extra):
    kwargs = {'subject': SUBJECT, 'message': 'Ciao,\nla tua iscrizione è confermata.\n', 'from_email': f'{BRAND} <{HOST_USER}>',
              'recipient_list': TO}
    kwargs.update(extra)
    return kwargs


SCENARIOS = [
    # -- send_email_text ------------------------------------------------------------------------
    Scenario('send_email_text publishes send_mail_async with the team address', TEXT,
             {'message': 'Il tuo account è stato eliminato.', 'subject': f'{BRAND} | Account eliminato', 'recipient_list': TO},
             celery_left=1, expect={'status': 'SUCCESS', 'rows': 1, 'children': 1, 'emaillog': [], 'mails': 0, 'celery': 1}),
    Scenario('send_email_text child routed to Go sends with the association and reply-to', TEXT,
             {'message': 'Testo', 'subject': SUBJECT, 'recipient_list': TO, 'sport_association_id': ASSOCIATION,
              'reply_to': ['owner1@example.test']}, routes=(TEXT, MAIL),
             expect={'status': 'SUCCESS', 'rows': 2, 'children': 1, 'emaillog': [('socio@example.test', SUBJECT, 'Sent', ASSOCIATION)],
                     'mails': 1}),
    Scenario('send_email_text child SMTP failure retries after 180 s', TEXT,
             {'message': 'Testo', 'subject': SUBJECT, 'recipient_list': TO}, routes=(TEXT, MAIL), smtp='auth', done='held',
             expect={'status': 'SUCCESS', 'rows': 2, 'children': 1, 'emaillog': [], 'mails': 0, 'child': 'RETRY',
                     'child_exc': 'SMTPAuthenticationError'}),
    Scenario('send_email_text without subject is handed off', TEXT, {'message': 'Testo', 'recipient_list': TO}, unchecked=True,
             handoff=True, expect={'status': 'FAILURE', 'exc': 'TypeError', 'rows': 1, 'children': 0, 'emaillog': [], 'mails': 0}),
    # -- send_email_template --------------------------------------------------------------------
    Scenario('send_email_template renders and publishes with the team address', TEMPLATE,
             {'recipient_list': ['owner1@example.test'], 'subject': "[ASD Uno] Ricordati di approvare l'iscrizione di Giulia Rossi",
              'template': NEW_SUBSCRIPTION, 'data': subscription_data(), 'sport_association_id': ASSOCIATION},
             celery_left=1, expect={'status': 'SUCCESS', 'rows': 1, 'children': 1, 'emaillog': [], 'mails': 0, 'celery': 1}),
    Scenario('send_email_template with a configuration sends through its SMTP server and saves the balance', TEMPLATE,
             {'recipient_list': ['socio@example.test'], 'subject': '[ASD Quattro] Certificato in scadenza', 'template': NEW_SUBSCRIPTION,
              'data': subscription_data('Lùca', "D'Amico", 'ASD Quattro'), 'sport_association_id': SMTP_OK,
              'reply_to': ['owner4@example.test']},
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [], 'mails': 1, 'balance': {CONFIG_OK: 6}}),
    Scenario('send_email_template at the daily limit sends nothing', TEMPLATE,
             {'recipient_list': TO, 'subject': 'Limite', 'template': NEW_SUBSCRIPTION, 'data': subscription_data(),
              'sport_association_id': str(LIMITED)},
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [], 'mails': 0, 'smtp': 0, 'balance': {CONFIG_LIMITED: 3}}),
    Scenario('send_email_template with a failing SMTP login publishes under the association name', TEMPLATE,
             {'recipient_list': TO, 'subject': 'Ripiego', 'template': NEW_SUBSCRIPTION, 'data': subscription_data(),
              'sport_association_id': SMTP_BAD}, routes=(TEMPLATE, MAIL),
             expect={'status': 'SUCCESS', 'rows': 2, 'children': 1, 'emaillog': [('socio@example.test', 'Ripiego', 'Sent', SMTP_BAD)],
                     'mails': 1, 'balance': {CONFIG_BAD: 0}}),
    Scenario('send_email_template with a configuration without SMTP publishes under the association name', TEMPLATE,
             {'recipient_list': TO, 'subject': 'Senza SMTP', 'template': NEW_SUBSCRIPTION, 'data': subscription_data(),
              'sport_association_id': CONFIGURED, 'reply_to': 'owner2@example.test'},
             celery_left=1, expect={'status': 'SUCCESS', 'rows': 1, 'children': 1, 'emaillog': [], 'mails': 0, 'celery': 1}),
    Scenario('send_email_template refused after a completed restore', TEMPLATE,
             {'recipient_list': TO, 'subject': 'Ripristino', 'template': NEW_SUBSCRIPTION, 'data': subscription_data()},
             setup='restore-completed', expect={'status': 'FAILURE', 'exc': 'StaleDataTask', 'rows': 1, 'children': 0, 'emaillog': [],
                                                'mails': 0}),
    Scenario('send_email_template outside the embedded templates is handed off', TEMPLATE,
             {'recipient_list': TO, 'subject': 'Certificato aggiornato', 'template': CHANGED,
              'data': {**subscription_data(), 'certificate_expiring_date': '01/02/2027'}, 'sport_association_id': ASSOCIATION},
             routes=(TEMPLATE, MAIL), handoff=True,
             expect={'status': 'SUCCESS', 'rows': 2, 'children': 1, 'emaillog': [('socio@example.test', 'Certificato aggiornato', 'Sent',
                                                                                    ASSOCIATION)], 'mails': 1}),
    # -- send_email_html ------------------------------------------------------------------------
    Scenario('send_email_html publishes with the default sender', HTML,
             {'html_message': '<p>Promemoria</p>', 'subject': f'{BRAND} | Promemoria', 'recipient_list': TO},
             celery_left=1, expect={'status': 'SUCCESS', 'rows': 1, 'children': 1, 'emaillog': [], 'mails': 0, 'celery': 1}),
    Scenario('send_email_html with a configuration sends after the Timer delay, also across a consumer stop', HTML,
             {'html_message': '<p>Promemoria <b>evento</b></p>', 'subject': 'Promemoria evento', 'recipient_list': ['uno@example.test'],
              'sport_association_id': SMTP_OK, 'reply_to': ['owner4@example.test']},
             effect=both(delivered(1), balance(CONFIG_OK, 6)), early_stop=True,
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [], 'mails': 1, 'balance': {CONFIG_OK: 6}}),
    Scenario('send_email_html with a configuration without SMTP publishes from the Timer', HTML,
             {'html_message': '<p>Ciao</p>', 'subject': 'Dal timer', 'recipient_list': TO, 'sport_association_id': str(CONFIGURED)},
             effect=queued(1), celery_left=1,
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [], 'mails': 0, 'celery': 1, 'timer': True}),
    Scenario('send_email_html deferred by a pending restore', HTML,
             {'html_message': '<p>Ciao</p>', 'subject': 'Rinviata', 'recipient_list': TO}, setup='restore-pending', done='held',
             expect={'status': 'RETRY', 'exc': 'Busy', 'rows': 1, 'children': 'self', 'emaillog': [], 'mails': 0}),
    Scenario('send_email_html with a malformed association id is handed off', HTML,
             {'html_message': '<p>Ciao</p>', 'subject': 'Id', 'recipient_list': TO, 'sport_association_id': 'non-un-uuid'},
             handoff=True, expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [], 'mails': 0}),
    # -- send_mail_async paths ------------------------------------------------------------------
    Scenario('send_mail_async through a configuration with SMTP (html)', MAIL,
             mail(html_message='<p>Quota <b>già</b> scaduta</p>', sport_association_id=SMTP_OK, reply_to=['owner4@example.test']),
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [('socio@example.test', SUBJECT, 'Sent', SMTP_OK)],
                     'mails': 1, 'balance': {CONFIG_OK: 6}}),
    Scenario('send_mail_async through a configuration with SMTP (plain text)', MAIL, mail(sport_association_id=str(SMTP_OK)),
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [('socio@example.test', SUBJECT, 'Sent', SMTP_OK)],
                     'mails': 1, 'balance': {CONFIG_OK: 6}}),
    Scenario('send_mail_async at the configuration daily limit logs Sent without sending', MAIL, mail(sport_association_id=LIMITED),
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [('socio@example.test', SUBJECT, 'Sent', LIMITED)],
                     'mails': 0, 'smtp': 0, 'balance': {CONFIG_LIMITED: 3}}),
    Scenario('send_mail_async configuration login failure falls back to a child message', MAIL,
             mail(sport_association_id=SMTP_BAD, html_message='<p>ok</p>'),
             expect={'status': 'SUCCESS', 'rows': 2, 'children': 1, 'mails': 1,
                     'emaillog': [('socio@example.test', SUBJECT, 'Sent', SMTP_BAD)] * 2}),
    Scenario('send_mail_async through the instance email settings', MAIL, mail(html_message='<p>istanza</p>'), setup='instance',
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [('socio@example.test', SUBJECT, 'Sent', None)],
                     'mails': 1}),
    Scenario('send_mail_async with an undecryptable instance password retries', MAIL, mail(), setup='instance-bad-password',
             done='held', expect={'status': 'RETRY', 'exc': 'ImproperlyConfigured', 'rows': 1, 'children': 'self', 'emaillog': [],
                                  'mails': 0, 'retry': (1, 180)}),
    Scenario('fail_silently with an SMTP login failure logs Sent', MAIL, mail(fail_silently=True), smtp='auth',
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [('socio@example.test', SUBJECT, 'Sent', None)],
                     'mails': 0}),
    Scenario('fail_silently with a refused recipient logs Sent', MAIL, mail(fail_silently=True, recipient_list=['rifiutato@example.test']),
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'mails': 0,
                     'emaillog': [('rifiutato@example.test', SUBJECT, 'Sent', None)]}),
    Scenario('send_mail_async with a non-string subject is handed off', MAIL, mail(subject=12345), handoff=True,
             expect={'status': 'SUCCESS', 'rows': 1, 'children': 0, 'emaillog': [('socio@example.test', '12345', 'Sent', None)],
                     'mails': 1}),
]


class Stack(base.Stack):
    phase = None

    def seed(self, scenario, stamp=None):
        result = super().seed(scenario, stamp)
        from application.models import User, SportAssociation
        from communications.models import CommunicationConfiguration
        from instance.models import InstanceConfiguration
        from instance.email_configuration import cipher
        started = self.seeding[0]
        User.original_objects.bulk_create([User(user_id=U(n), username=f'consumer-owner-{n}', role=1, password='!private-fixture',
                                                date_joined=started, email=f'owner{n}@example.test') for n in (4, 5, 6)])
        SportAssociation.original_objects.bulk_create([
            SportAssociation(sport_association_id=SMTP_OK, user_id=U(4), denomination='ASD Quattro', tax_code='00000000000'),
            SportAssociation(sport_association_id=LIMITED, user_id=U(5), denomination='ASD Cinque', tax_code='00000000000'),
            SportAssociation(sport_association_id=SMTP_BAD, user_id=U(6), denomination='ASD Sei', tax_code='00000000000')])
        smtp = dict(email_smtp_host='127.0.0.1', email_smtp_port=self.smtp.port, email_smtp_user='segreteria@example.test',
                    email_encryption='NONE')
        CommunicationConfiguration.objects.bulk_create([
            CommunicationConfiguration(communication_configuration_id=CONFIG_OK, sport_association_id=SMTP_OK,
                                       email_smtp_password=SMTP_PASSWORD, email_sender_name='Segreteria Quattro',
                                       daily_email_limit=1000, daily_email_balance=5, **smtp),
            CommunicationConfiguration(communication_configuration_id=CONFIG_LIMITED, sport_association_id=LIMITED,
                                       email_smtp_password=SMTP_PASSWORD, email_sender_name=None, daily_email_limit=3,
                                       daily_email_balance=3, **smtp),
            CommunicationConfiguration(communication_configuration_id=CONFIG_BAD, sport_association_id=SMTP_BAD,
                                       email_smtp_password='wrong-password', email_sender_name=None, daily_email_limit=1000,
                                       daily_email_balance=0, **smtp)])
        if scenario.setup in ('instance', 'instance-bad-password'):
            secret = SECRET if scenario.setup == 'instance' else 'another-secret-key'
            # Fernet tokens carry a random IV and a timestamp: one token per secret keeps the seed reproducible.
            tokens = self.__dict__.setdefault('tokens', {})
            if secret not in tokens:
                tokens[secret] = cipher(secret).encrypt(SMTP_PASSWORD.encode()).decode()
            InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(
                domain='consumer.example.test', name='Istanza', self_hosted=True,
                email_settings={'host': '127.0.0.1', 'port': self.smtp.port, 'username': HOST_USER, 'security': 'none',
                                'from_email': 'istanza@example.test', 'sender_name': 'Istanza Fixture'},
                email_password_encrypted=tokens[secret], email_revision=2)])
        self.seeding = (started, datetime.now(timezone.utc))
        return result

    def publish(self, scenario):
        from core.celery import app
        import application.tasks  # noqa: F401  registers the e-mail tasks
        options = {}
        if scenario.retries is not None:
            options['retries'] = scenario.retries
        self.broker.flushdb()
        if scenario.unchecked:
            app.send_task(scenario.task, args=scenario.args, kwargs=scenario.kwargs, **options)
        else:
            app.tasks[scenario.task].apply_async(args=scenario.args, kwargs=scenario.kwargs, **options)
        raw = self.broker.rpop(GO_QUEUE)
        assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
        return raw.decode()

    def quiescent(self, scenario, task_id, held=None):
        from django_celery_results.models import TaskResult
        if self.broker.llen(GO_QUEUE) or self.broker.llen(DJANGO_QUEUE) != scenario.celery_left:
            return False
        if held is None:
            held = 1 if scenario.done == 'held' else 0
        if self.broker.hlen('unacked') != held:
            return False
        rows = {row.task_id: row.status for row in TaskResult.objects.all()}
        if task_id not in rows or len(rows) != scenario.expect.get('rows', 1):
            return False
        final = ('SUCCESS', 'FAILURE', 'REVOKED', 'RETRY') if held else ('SUCCESS', 'FAILURE', 'REVOKED')
        if not all(status in final for status in rows.values()):
            return False
        if scenario.effect and not (self.phase == 'go' and scenario.early_stop):
            return scenario.effect(self)
        return True


class Harness(base.Harness):
    def oracle_settings(self, scenario):
        config = super().oracle_settings(scenario)
        config['routes'] = ','.join(scenario.routes)
        config['settings'].update(APP_URL=APP_URL, IS_WHITELABEL=False, SECRET_KEY=SECRET, SECRET_KEY_FALLBACKS=[])
        config['core'].update(DEFAULT_TEAM_EMAIL=f'Il Team di {BRAND} <{HOST_USER}>', APP_URL=APP_URL)
        return config

    def env_file(self, scenario):
        path = super().env_file(scenario)
        lines = [line for line in path.read_text().splitlines() if not line.startswith(('GO_TASK_QUEUE_TASKS=', 'SECRET_KEY='))]
        lines += [f'GO_TASK_QUEUE_TASKS={",".join(scenario.routes)}', f'SECRET_KEY={SECRET}', f'APP_URL={APP_URL}']
        path.write_text(''.join(line + '\n' for line in lines))
        return path

    def run_go(self, scenario, payload, push=True, handoff=None):
        self.stack.phase = 'go'
        try:
            task_id = super().run_go(scenario, payload, push=push, handoff=handoff)
        finally:
            self.stack.phase = None
        if scenario.effect and not (handoff if handoff is not None else scenario.handoff):
            # The consumer stopped; a delayed send must have completed before it exited.
            assert scenario.effect(self.stack), 'Go consumer exited before the delayed send: ' + scenario.name
        return task_id

    @staticmethod
    def wait(predicate, description, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError('Timed out: ' + description)

    def observe(self, scenario, payload, since, until):
        observed = super().observe(scenario, payload, since, until)
        original = json.loads(payload)['headers']['id']

        def window(value):
            if isinstance(value, datetime) and since <= value <= until:
                return 'run'
            if isinstance(value, dict):
                return {k: window(v) for k, v in value.items()}
            if isinstance(value, list):
                return [window(v) for v in value]
            return value
        observed['state'] = window(observed['state'])
        for row in observed['state'].get('application.AuditLogIndex', []):
            UUID(str(row['audit_log_index_id']))
            row['audit_log_index_id'] = '<uuid4>'  # AuditLogIndex's random primary key
        for row in observed['task_results']:
            meta = json.loads(row['meta'])
            meta['children'] = [[[child if child == original else '<child-task-id>', rest], extra]
                                for (child, rest), extra in meta['children']]
            row['meta'] = json.dumps(meta)
        return observed

    @staticmethod
    def normalize_message(raw, original, reference):
        message = json.loads(raw)
        own = original['headers']['id']
        if message['headers']['id'] == own:
            return base.Harness.normalize_message(raw, original, reference)
        # A message the task published: fresh id, the publishing process's origin and reply_to, its own stamp.
        child = message['headers']['id']
        UUID(child)
        headers, properties = message['headers'], message['properties']
        assert properties['correlation_id'] == child
        stamp = headers['assozeta_published_at']
        assert isinstance(stamp, float) and abs(stamp - time.time()) < 600, stamp
        assert re.fullmatch(r'gen\d+@.+', headers['origin']), headers['origin']
        UUID(properties['delivery_tag']); UUID(properties['reply_to'])
        headers['assozeta_published_at'], headers['origin'] = '<stamp>', '<origin>'
        if 'origin' in properties:  # Task.retry copies the request's origin into the properties
            assert re.fullmatch(r'gen\d+@.+', properties['origin']), properties['origin']
            properties['origin'] = '<origin>'
        properties['delivery_tag'], properties['reply_to'] = '<new-tag>', '<reply-to>'
        if headers.get('eta'):
            offset = datetime.fromisoformat(headers['eta']).timestamp() - reference.timestamp()
            assert 0 <= offset < 400, offset
            headers['eta'] = 'date_done+%ds' % round(offset)
        text = json.dumps(message, ensure_ascii=False).replace(child, '<child-task-id>')
        message = json.loads(text)
        import base64
        message['body'] = json.loads(base64.b64decode(message['body']))
        return json.dumps(message, ensure_ascii=False)

    def authored(self, scenario, payload, observed):
        """The Django oracle took the path the scenario was written for."""
        expect = scenario.expect
        own_id = json.loads(payload)['headers']['id']
        results = observed['task_results']
        own = [row for row in results if row['task_id'] == own_id]
        others = [row for row in results if row['task_id'] != own_id]
        assert len(results) == expect['rows'] and len(own) == 1 and own[0]['status'] == expect['status'], results
        children = json.loads(own[0]['meta'])['children']
        # 'children' is the prefork worker's count; the thread oracle records only the maintenance deferral.
        expected = expect['children'] if base.ORACLE == 'prefork' or expect.get('exc') == 'Busy' else 0
        if expected == 'self':
            assert children == [[[own_id, None], None]], children
        else:
            assert children == [[['<child-task-id>', None], None]] * expected, children
        if 'exc' in expect:
            assert json.loads(own[0]['result'])['exc_type'] == expect['exc'], own
        else:
            assert own[0]['result'] == 'null', own
        if 'child' in expect:
            assert [row['status'] for row in others] == [expect['child']], others
            assert json.loads(others[0]['result'])['exc_type'] == expect['child_exc'], others
        logs = [(r['recipient'], r['subject'], r['result'], r['sport_association_id']) for r in observed['email_logs']]
        assert logs == expect['emaillog'], logs
        mails = [t for t in observed['smtp'] if '\\r\\n.\\r\\n' in t]
        assert len(mails) == expect['mails'], observed['smtp']
        if 'smtp' in expect:
            assert len(observed['smtp']) == expect['smtp'], observed['smtp']
        queue = observed['broker'].get(DJANGO_QUEUE, ['list', []])[1]
        assert len(queue) == expect.get('celery', 0), observed['broker']
        for message in queue:
            message = json.loads(message)
            assert message['headers']['task'] == MAIL, message
            published_in_task = not expect.get('timer')
            assert message['headers']['parent_id'] == (own_id if published_in_task else None), message['headers']
            assert message['headers']['root_id'] == (own_id if published_in_task else '<child-task-id>'), message['headers']
        configs = {row['communication_configuration_id']: row['daily_email_balance']
                   for row in observed['state']['communications.CommunicationConfiguration']}
        for config, value in expect.get('balance', {}).items():
            assert configs[config] == value, configs
        if 'retry' in expect:
            retries, countdown = expect['retry']
            held = observed['broker'][GO_QUEUE][1]
            message = json.loads(held[0])
            assert message['headers']['id'] == own_id and message['headers']['retries'] == retries
            assert message['headers']['eta'] == 'date_done+%ds' % countdown, message['headers']['eta']


def main():
    require_production_python()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = OUTPUT / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server:
        raise RuntimeError('Private PostgreSQL, Redis and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    only = os.environ.get('CELERY_CONSUMER_ONLY')
    harness = None
    with tempfile.TemporaryDirectory(prefix='az-celery-email-', dir='/tmp') as directory, (OUTPUT / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; redis_process = None; smtp = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            if not DJANGO_ONLY:
                subprocess.run(['go', 'build', '-race', '-o', str(work / 'celery-consumer'), './cmd/celery-consumer'], cwd=ROOT / 'BE_GO',
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass', REDIS_PASSWORD,
                                              '--save', '', '--appendonly', 'no', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
            smtp = RefusingSMTP(tls_context(work), False)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            stack = Stack(work, environment, redis_port, smtp)
            harness = Harness(stack, work / 'celery-consumer')
            for scenario in SCENARIOS:
                if only and only not in scenario.name:
                    continue
                harness.execute(scenario)
            if (work / 'celery-consumer').exists():
                result['binary_sha256'] = hashlib.sha256((work / 'celery-consumer').read_bytes()).hexdigest()
        finally:
            try:
                if harness:
                    result.update(scenarios=harness.completed, scenario_count=len(harness.completed))
                    (OUTPUT / 'observations.json').write_text(json.dumps(harness.observations, indent=1, default=str,
                                                                         ensure_ascii=False) + '\n')
                    for process, stream, _ in harness.stack.children:
                        if process.poll() is None:
                            process.kill()
                        stream.close()
            finally:
                try:
                    if smtp:
                        smtp.close()
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    if redis_process:
                        redis_process.terminate(); redis_process.wait(timeout=10)
                    if attempted:
                        try:
                            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                        except subprocess.CalledProcessError:
                            if (work / 'postgres/postmaster.pid').exists():
                                raise
                    report.write_text(json.dumps(result, indent=2) + '\n')
    expected = [s.name for s in SCENARIOS if not only or only in s.name]
    assert harness.completed == expected, 'not every scenario completed'
    paths = ['TESTS/adapters/go/celery_consumer_email_process.py', 'TESTS/adapters/go/celery_consumer_process.py', *base.ORACLE_SOURCES,
             'BE/core/tasks.py',
             'BE/application/tasks.py', 'BE/communications/models.py', 'BE/instance/email_configuration.py',
             'BE/instance/restore/task_base.py', 'BE/templates/email/account/email_new_subscription.html']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/', 'celery_consumer',
                                                       'send_mail_async', 'consumer_email', 'internal/smtpsend/', 'internal/emailrender/')))
    result.update(success=True, partial=bool(only), race_instrumented=not DJANGO_ONLY, oracle=base.ORACLE,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_hashes'}))


if __name__ == '__main__':
    main()
