"""Parent-only communications/send/email HTTP runner; imports are inert.

service_context owns, for the whole run, four fake SMTP servers (STARTTLS,
implicit TLS, plain with AUTH, plain without AUTH or STARTTLS) that record the
client bytes of every connection, plus a port nothing listens on. Both
backends reach them directly (smtplib and smtpsend skip certificate checks).

Seeds three messages (two of the own association, one foreign) and the
foreign association's configuration (audit disabled). A case's setup first
replaces the own association's configuration (deleted, or one row with a
fixed key; audit disabled). The effect check compares the complete model
state: the created message (or none when a stored one is reused), its
transactions in creation order, the own configuration row afterwards, every
other row unchanged, the audit entries (message create, balance update) with
their index rows, the Celery messages on the run's broker and the normalized
SMTP transcripts.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import ipaddress
import re
import socket
import ssl
import tempfile
import threading
import time
import types
from pathlib import Path

from TESTS.contracts import test_communications_send_email_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.instance_outbound_http import certificate_authority
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/communications_send_email_http.json', 'TESTS/contracts/test_communications_send_email_http.py',
           'TESTS/adapters/django/communications_send_email_http.py', 'TESTS/adapters/django/instance_outbound_http.py',
           'BE/communications/views.py', 'BE/communications/serializers.py', 'BE/communications/models.py',
           'BE/communications/urls.py', 'BE/core/tasks.py', 'BE/application/utils/api_utils.py',
           'BE/application/models/user_models.py', 'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py',
           'BE/application/impersonation.py', 'BE/application/impersonation_scope.py', 'BE/core/settings.py',
           'BE/core/celery.py', 'BE/instance/restore/task_base.py') + harness.SOURCES
CONFIGURATION = 'communications.CommunicationConfiguration'
MESSAGE = 'communications.Message'
TRANSACTION = 'communications.MessageTransaction'
MANAGED = (CONFIGURATION, MESSAGE, TRANSACTION, harness.AUDIT, harness.INDEX)
COLUMNS = ('email_smtp_host', 'email_smtp_port', 'email_smtp_user', 'email_smtp_password', 'email_sender_name', 'email_encryption',
           'daily_email_limit', 'daily_email_balance')
STUB = {}


# ---------------------------------------------------------------------------
# Fake SMTP servers
# ---------------------------------------------------------------------------

class FakeSMTP:
    """A small ESMTP server; records the client bytes per connection."""

    def __init__(self, context, mode):
        self.context, self.mode = context, mode
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

    def count(self):
        with self.lock:
            return len(self.transcripts)

    def serve(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.converse, args=(connection,), daemon=True).start()

    def features(self, tls):
        names = ['fake.smtp.test', 'SIZE 10485760']
        if self.mode != 'noauth':
            names.append('AUTH PLAIN LOGIN')
        if self.mode == 'starttls' and not tls:
            names.append('STARTTLS')
        return ''.join(f'250{"-" if i < len(names) - 1 else " "}{name}\r\n' for i, name in enumerate(names)).encode()

    def converse(self, connection):
        units = []
        password = STUB['smtp_password'].encode()
        try:
            connection.settimeout(15)
            stream = self.context.wrap_socket(connection, server_side=True) if self.mode == 'ssl' else connection
            tls = self.mode == 'ssl'
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
                    stream.sendall(self.features(tls))
                elif verb == b'STARTTLS' and self.mode == 'starttls' and not tls:
                    stream.sendall(b'220 ready\r\n')
                    stream = self.context.wrap_socket(stream, server_side=True)
                    units.append('<tls>')
                    tls, buffer = True, b''
                elif verb == b'AUTH' and self.mode != 'noauth':
                    parts = unit.split()
                    secret = base64.b64decode(parts[2]).split(b'\0')[-1] if len(parts) > 2 else b''
                    stream.sendall(b'235 ok\r\n' if secret == password else b'535 denied\r\n')
                elif verb == b'DATA':
                    data = True
                    stream.sendall(b'354 go ahead\r\n')
                elif verb == b'QUIT':
                    stream.sendall(b'221 bye\r\n')
                    units.append('<quit>')
                    return
                elif verb == b'RCPT':
                    stream.sendall(b'550 refused\r\n' if b'rifiutato@' in unit else b'250 ok\r\n')
                elif verb in (b'MAIL', b'RSET'):
                    stream.sendall(b'250 ok\r\n')
                else:
                    stream.sendall(b'500 unknown\r\n')
        except (OSError, ssl.SSLError) as error:
            units.append('<error ' + type(error).__name__ + '>')
        finally:
            connection.close()
            with self.lock:
                self.transcripts.append(units)

    def close(self):
        self.listener.close()
        self.thread.join(5)


def unused_port():
    probe = socket.create_server(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


@contextmanager
def service_context(report, log):
    directory = tempfile.mkdtemp(prefix='az-send-email-')
    servers = {}
    report['service_cleanup']['smtp_servers'] = 'running'
    try:
        _ca, context = certificate_authority(directory, 'smtp', ('fake.smtp.test',))
        for mode in ('starttls', 'ssl', 'plain', 'noauth'):
            servers[mode] = FakeSMTP(context, mode)
        STUB.update(servers=servers, ports={name: server.port for name, server in servers.items()}, smtp_password='')
        STUB['ports']['closed'] = unused_port()
        yield
    finally:
        stopped = True
        for server in servers.values():
            server.close()
            stopped = stopped and not server.thread.is_alive()
        for path in Path(directory).iterdir():
            path.unlink()
        Path(directory).rmdir()
        report['service_cleanup']['smtp_servers'] = 'stopped' if stopped else 'cleanup_failed'
        STUB.clear()


# ---------------------------------------------------------------------------
# Setup, transcripts and the wrapped case
# ---------------------------------------------------------------------------

def resolve_row(row):
    port = row['email_smtp_port']
    if isinstance(port, str) and port.startswith('$port:'):
        port = STUB['ports'][port[6:]]
    return {**row, 'email_smtp_port': port}


def apply_setup(case, source):
    setup = case.get('setup')
    if not setup:
        return
    from auditlog.context import disable_auditlog
    from communications.models import CommunicationConfiguration
    with disable_auditlog():
        CommunicationConfiguration._base_manager.filter(sport_association_id=source['ids']['own_association']).delete()
        if setup['configuration'] is not None:
            CommunicationConfiguration._base_manager.bulk_create([CommunicationConfiguration(
                communication_configuration_id=source['own_configuration'], sport_association_id=source['ids']['own_association'],
                **resolve_row(setup['configuration']))])


EHLO = re.compile(r'(?i)^ehlo (\S+)\r\n\Z')
SIZE = re.compile(r'^(mail from:<[^>]*>) size=(\d+)\r\n\Z')
BOUNDARY = re.compile(r'={15}\d{19}==')


def normalized_transcript(units):
    require = base_contract.require
    out, local = [], set()
    data = [unit for unit in units if unit.endswith('\r\n.\r\n')]
    for unit in units:
        if unit.startswith('<error') or unit == '<eof>':
            out.append('<closed>')
            continue
        match = EHLO.match(unit)
        if match:
            local.add(match.group(1))
            out.append('ehlo <local>\r\n')
            continue
        match = SIZE.match(unit)
        if match:
            # SIZE is len(msg) of the CRLF message; without DATA (all refused) it is only positive.
            size = int(match.group(2))
            require(size == len(data[0]) - 3 if data else size > 0, 'smtp.size')
            out.append(match.group(1) + ' size=<size>\r\n')
            continue
        if unit.endswith('\r\n.\r\n'):
            boundaries = set(BOUNDARY.findall(unit))
            require(len(boundaries) == 1, 'smtp.boundary')
            unit = unit.replace(boundaries.pop(), '<boundary>')
        out.append(unit)
    require(len(local) <= 1, 'smtp.local-name')
    return out


def check_smtp(case):
    require = base_contract.require
    spec = (case.get('effect') or {}).get('smtp')
    servers = STUB['servers']
    expected = len(spec['connections']) if spec else 0
    deadline = time.monotonic() + 15
    while spec and servers[spec['server']].count() < expected and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.1)
    for name, server in servers.items():
        transcripts = server.take()
        if spec and name == spec['server']:
            require(len(transcripts) == expected, 'smtp.connections')
            actual = sorted(normalized_transcript(units) for units in transcripts)
            require(actual == sorted(spec['connections']), 'smtp.transcripts')
        else:
            require(transcripts == [], 'smtp.unexpected-connection')


def _assert_case(case, base_url, *, token, sessions):
    source = base_contract.load_source()
    STUB['smtp_password'] = source['smtp_password']
    apply_setup(case, source)
    for server in STUB['servers'].values():
        server.take()
    status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    check_smtp(case)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


# ---------------------------------------------------------------------------
# Fixture and effects
# ---------------------------------------------------------------------------

def models():
    from communications.models import CommunicationConfiguration, Message, MessageTransaction
    return CommunicationConfiguration, Message, MessageTransaction


def seed_rows(source, created):
    configuration, message, _transaction = models()
    for row in source['messages'].values():
        message._base_manager.bulk_create([message(message_id=row['id'], sport_association_id=row['association'], type='EMAIL',
                                                   message=row['message'], subject=row['subject'])])
        created.append((message, row['id']))
    for row in source['configurations'].values():
        configuration._base_manager.bulk_create([configuration(communication_configuration_id=row['id'],
                                                               sport_association_id=row['association'],
                                                               **{column: row[column] for column in COLUMNS})])
        created.append((configuration, row['id']))


fixture_context = harness.with_celery_broker(harness.fixture_context_for(
    contract, seed_rows, purge=(lambda: models()[0], lambda: models()[1], lambda: models()[2])))


def prepare(source):
    return {'started': time.time()}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if isinstance(text, str) else text


def naive_utc(text):
    value = moment(text).astimezone(timezone.utc).replace(tzinfo=None)
    return str(value)


def bindings(before, ids):
    from django.conf import settings
    associations = keyed(before['application.SportAssociation'], 'sport_association_id')
    values = {}
    for label, key in (('own', 'own_association'), ('foreign', 'foreign_association')):
        values[f'$from:{label}'] = settings.DEFAULT_FROM_EMAIL.replace(settings.WHITELABEL_NAME, associations[ids[key]]['denomination'])
    return values


def resolve(value, values):
    if isinstance(value, str):
        return values.get(value, value)
    if isinstance(value, list):
        return [resolve(item, values) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, values) for key, item in value.items()}
    return value


def check_audit(case, event, indexes, before, ids, *, model, key, action, changes, effect, context):
    require = contract.require
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'communications'}
    require(event['content_type_id'] == types[model.lower()] and event['action'] == action and event['object_pk'] == key
            and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
    require(event['changes'] == changes, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if effect['session']:
        additional = {'impersonation_session': context['fixture']['sessions'][effect['session']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    require(index is not None and index['sport_association_id'] == effect['association'] and index['resolution_path'] == 'direct',
            'index.fields')


def assert_effect(case, before, after, source, context):
    require = contract.require
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    ids = source['ids']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    celery = resolve(effect['celery'], bindings(before, ids)) if effect else []
    harness.celery_published(contract, context['fixture'], celery, since=since, until=until)

    # Configurations: the own row is the setup's (and the effect's) row; every other row is unchanged.
    old_c, new_c = keyed(before[CONFIGURATION], 'communication_configuration_id'), keyed(after[CONFIGURATION], 'communication_configuration_id')
    own = source['own_configuration']
    require({k: v for k, v in old_c.items() if k != own} == {k: v for k, v in new_c.items() if k != own}, 'state.configurations')
    if case.get('setup'):
        expected = (effect or {}).get('configuration') or case['setup']['configuration']
        if expected is None:
            require(own not in new_c, 'state.own-configuration-deleted')
        else:
            row = new_c.get(own)
            require(row is not None and str(row['sport_association_id']) == ids['own_association'], 'state.own-configuration')
            require({column: row[column] for column in COLUMNS} == resolve_row(expected), 'state.own-configuration-columns')
    else:
        require(old_c.get(own) == new_c.get(own), 'state.own-configuration-unchanged')

    old_m, new_m = keyed(before[MESSAGE], 'message_id'), keyed(after[MESSAGE], 'message_id')
    old_t, new_t = keyed(before[TRANSACTION], 'message_transaction_id'), keyed(after[TRANSACTION], 'message_transaction_id')
    require(all(new_m.get(k) == v for k, v in old_m.items()) and all(new_t.get(k) == v for k, v in old_t.items()), 'state.kept')
    created_m = [new_m[k] for k in set(new_m) - set(old_m)]
    created_t = sorted((new_t[k] for k in set(new_t) - set(old_t)), key=lambda row: moment(row['sent_on']))
    added, indexes = harness.audit_delta(contract, before, after)
    if effect is None:
        require(not created_m and not created_t and not added and not indexes, 'state.no-effect')
        return
    started = datetime.fromtimestamp(since - 1, timezone.utc)
    ended = datetime.fromtimestamp(until + 1, timezone.utc)
    audits = []
    if effect['message']:
        require(len(created_m) == 1, 'state.message-created')
        message = created_m[0]
        subject, text = effect['message']
        require(str(message['sport_association_id']) == effect['association'] and message['type'] == 'EMAIL'
                and message['message'] == text and message['subject'] == subject, 'state.message-fields')
        require(started <= moment(message['created_at']) <= ended, 'state.message-created-at')
        message_id = str(message['message_id'])
        audits.append(('Message', message_id, 0, {
            'message_id': ['None', message_id], 'sport_association': ['None', effect['association']], 'type': ['None', 'EMAIL'],
            'message': ['None', text], 'subject': ['None', subject], 'created_at': ['None', naive_utc(message['created_at'])]}))
    else:
        require(not created_m, 'state.no-message')
        message_id = source['messages'][effect['stored']]['id']
    require([row['recipient'] for row in created_t] == effect['recipients'], 'state.transactions')
    for row in created_t:
        require(str(row['message_id']) == message_id and started <= moment(row['sent_on']) <= ended
                and all(row[k] is None for k in ('delivered_on', 'opened_on', 'clicked_on', 'bounced_on', 'bounced_reason',
                                                  'bounced_type', 'bounced_status')), 'state.transaction-fields')
    if effect['balance_audit']:
        audits.append(('CommunicationConfiguration', own, 1, {'daily_email_balance': effect['balance_audit']}))
    require(len(added) == len(audits) and len(indexes) == len(added), 'audit.count')
    for event, (model, key, action, changes) in zip(sorted(added, key=lambda event: event['id']), audits):
        check_audit(case, event, indexes, before, ids, model=model, key=key, action=action, changes=changes, effect=effect,
                    context=context)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
