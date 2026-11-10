"""Parent-only communications/settings/smtp info, update and verify HTTP runner; imports are inert.

Seeds CommunicationConfiguration rows for the foreign association (two, in
reverse key order) and the soft-deleted association (audit disabled); the own
association starts without one. Each case is checked against the complete
model state: only CommunicationConfiguration rows and auditlog entries with
their index rows may change. info's create must add exactly one row for the own
association (its key is bound for later cases); updates must leave every other
row and column unchanged; each new auditlog entry matches the authored one
(object, action, changes without the password, actor, impersonation data,
correlation id, direct index row). Rows the run creates are removed before the
fixture cleanup compares the model state.

service_context owns fake SMTP servers for the whole run (STARTTLS, implicit
TLS, plain, without AUTH, refusing the recipient, refusing the sender, never
greeting) plus a port nothing listens on. A verify case with a setup first
writes the listed columns of the own row (queryset update, no audit); the
effect check then requires exactly that row, no audit entry and the authored
SMTP transcripts on the named server (none on the others).
"""
from contextlib import contextmanager
import errno
import ipaddress
import re
import tempfile
import time
import types
from pathlib import Path

from TESTS.contracts import test_communications_smtp_http as base_contract
from TESTS.adapters.django.communications_send_email_http import FakeSMTP, STUB, unused_port
from TESTS.adapters.django.instance_outbound_http import certificate_authority

from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/communications_smtp_http.json', 'TESTS/contracts/test_communications_smtp_http.py',
           'TESTS/adapters/django/communications_smtp_http.py', 'TESTS/adapters/django/communications_send_email_http.py',
           'TESTS/adapters/django/instance_outbound_http.py', 'BE/core/settings.py', 'BE/communications/views.py', 'BE/communications/serializers.py',
           'BE/communications/models.py', 'BE/communications/urls.py', 'BE/application/models/user_models.py',
           'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py', 'BE/application/impersonation.py',
           'BE/application/impersonation_scope.py') + harness.SOURCES
CONFIGURATION = 'communications.CommunicationConfiguration'
MANAGED = (CONFIGURATION, harness.AUDIT, harness.INDEX)
COLUMNS = ('email_smtp_host', 'email_smtp_port', 'email_smtp_user', 'email_smtp_password', 'email_sender_name', 'email_encryption',
           'daily_email_limit', 'daily_email_balance')
MODES = ('starttls', 'ssl', 'plain', 'noauth', 'norcpt', 'nomail', 'silent')


@contextmanager
def service_context(report, log):
    directory = tempfile.mkdtemp(prefix='az-smtp-verify-')
    servers = {}
    report['service_cleanup']['smtp_servers'] = 'running'
    try:
        _ca, context = certificate_authority(directory, 'smtp', ('fake.smtp.test',))
        for mode in MODES:
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


def resolve_row(row):
    port = row.get('email_smtp_port')
    if isinstance(port, str) and port.startswith('$port:'):
        return {**row, 'email_smtp_port': STUB['ports'][port[6:]]}
    return dict(row)


def owner_email(source):
    from application.models import User
    email = User._base_manager.values_list('email', flat=True).get(user_id=source['ids']['owner'])
    base_contract.require(bool(email) and '$' not in email, 'fixture.owner-email')
    return email


EHLO = re.compile(r'(?i)^ehlo (\S+)\r\n\Z')


def normalized_transcript(units, email):
    out, local = [], set()
    for unit in units:
        if unit.startswith('<error') or unit == '<eof>':
            out.append('<closed>')
            continue
        match = EHLO.match(unit)
        if match:
            local.add(match.group(1))
            out.append('ehlo <local>\r\n')
            continue
        out.append(unit.replace(email, '$owner'))
    base_contract.require(len(local) <= 1, 'smtp.local-name')
    return out


def check_smtp(case, email):
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
            require(sorted(normalized_transcript(units, email) for units in transcripts) == sorted(spec['connections']),
                    'smtp.transcripts')
        else:
            require(transcripts == [], 'smtp.unexpected-connection')


def _assert_case(case, base_url, *, token, sessions):
    source = base_contract.load_source()
    STUB['smtp_password'] = source['smtp_password']
    email = owner_email(source)
    if case.get('setup'):
        changed = configuration_model()._base_manager.filter(sport_association_id=source['ids']['own_association']).update(
            **resolve_row(case['setup']))
        base_contract.require(changed == 1, 'setup.own-configuration')
    for server in STUB['servers'].values():
        server.take()
    response = case['response']
    if isinstance(response, dict) and isinstance(response.get('msg'), str):
        refused = f'[Errno {errno.ECONNREFUSED}] Connection refused'
        response = {**response, 'msg': response['msg'].replace('$owner', email).replace('$refused', refused)}
    status = base_contract.assert_case({**case, 'response': response}, base_url, token=token, sessions=sessions)
    check_smtp(case, email)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.family.ResponseFailure)


def configuration_model():
    from communications.models import CommunicationConfiguration
    return CommunicationConfiguration


def seed_rows(source, created):
    model = configuration_model()
    for row in source['configurations'].values():
        model._base_manager.bulk_create([model(communication_configuration_id=row['id'], sport_association_id=row['association'],
                                               **{column: row[column] for column in COLUMNS})])
        created.append((model, row['id']))


def check_event(case, event, indexes, before, source, key, spec, context):
    effect = case['effect']
    ids = source['ids']
    types = [ct['id'] for ct in before['contenttypes.ContentType']
             if ct['app_label'] == 'communications' and ct['model'] == 'communicationconfiguration']
    contract.require(len(types) == 1, 'audit.content-type')
    contract.require(event['content_type_id'] == types[0] and event['action'] == spec['action'] and event['object_pk'] == key
                     and event['object_repr'] == f'CommunicationConfiguration object ({key})', 'audit.identity')
    changes = {field: [key if value == '$own' else value for value in pair] for field, pair in spec['changes'].items()}
    contract.require(event['changes'] == changes, 'audit.changes')
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
    additional = None
    if effect['session']:
        additional = {'impersonation_session': context['fixture']['sessions'][effect['session']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                     'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    contract.require(index is not None and index['sport_association_id'] == ids['own_association']
                     and index['resolution_path'] == 'direct', 'index.fields')


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    old, new = keyed(before[CONFIGURATION], 'communication_configuration_id'), keyed(after[CONFIGURATION], 'communication_configuration_id')
    if effect['kind'] == 'verify':
        # Only the setup's columns of the own row differ from the state before the case; nothing is audited.
        key = context.get('own')
        contract.require(key is not None and set(new) == set(old), 'state.verify-rows')
        contract.require(all(new[k] == old[k] for k in old if k != key), 'state.verify-others')
        expected = resolve_row(effect['row'])
        contract.require({c: new[key][c] for c in COLUMNS} == expected, 'state.verify-row')
        contract.require({c: v for c, v in new[key].items() if c not in COLUMNS} == {c: v for c, v in old[key].items() if c not in COLUMNS},
                         'state.verify-keys')
        contract.require(before[harness.AUDIT] == after[harness.AUDIT] and before[harness.INDEX] == after[harness.INDEX],
                         'state.verify-no-audit')
        return
    if effect['kind'] == 'create':
        fresh = set(new) - set(old)
        contract.require(len(fresh) == 1 and set(old) <= set(new), 'state.created')
        key = fresh.pop()
        context['own'] = key
        contract.require(new[key]['sport_association_id'] == source['ids']['own_association'], 'state.created-association')
    else:
        key = context.get('own')
        contract.require(key is not None and set(new) == set(old), 'state.updated')
    row = new[key]
    for column, value in effect['row'].items():
        contract.require(row[column] == value and type(row[column]) is type(value), 'state.row-' + column)
    contract.require(all(new[k] == old[k] for k in old if k != key), 'state.others')
    if effect['kind'] == 'update':
        contract.require({c: v for c, v in row.items() if c not in COLUMNS} == {c: v for c, v in old[key].items() if c not in COLUMNS},
                         'state.updated-keys')
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(effect['audit']) and len(indexes) == len(added), 'audit.count')
    for event, spec in zip(sorted(added, key=lambda event: event['id']), effect['audit']):
        check_event(case, event, indexes, before, source, key, spec, context)


fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(configuration_model,))
run_workload = harness.run_workload_for(contract, assert_effect)
