"""Exercise the composer and its PostgreSQL writes through native Django and Go."""
from contextlib import contextmanager
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from secrets import token_hex
from time import monotonic, sleep

from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.communications_send_email_http import FakeSMTP, STUB
from TESTS.adapters.django.workflow_writes_http import keyed
from TESTS.contracts import test_message_templates_http as contract

SOURCES = (
    'TESTS/fixtures/message_templates_http.json', 'TESTS/contracts/test_message_templates_http.py',
    'TESTS/adapters/django/message_templates_http.py',
    'TESTS/adapters/django/communications_send_email_http.py',
    'BE/communications/migrations/0015_message_templates.py', 'BE/communications/message_templates.py',
    'BE/communications/message_template_views.py', 'BE/communications/urls.py', 'BE/communications/models.py',
    'BE/application/permissions_registry.py', 'BE/application/audit_resolvers.py',
    'BE_GO/cmd/server/communications_send_email.go',
    'BE_GO/internal/domain/message_templates.go', 'BE_GO/internal/dto/message_templates.go',
    'BE_GO/internal/handler/message_templates.go', 'BE_GO/internal/repository/audit_index.go',
    'BE_GO/internal/repository/message_templates.go', 'BE_GO/internal/service/communications_send_email.go',
    'BE_GO/internal/service/message_templates.go', 'BE_GO/internal/service/permission_registry.go',
) + harness.SOURCES
TEMPLATE, OUTGOING = 'communications.MessageTemplate', 'communications.OutgoingMessage'
CONFIGURATION = 'communications.CommunicationConfiguration'


@contextmanager
def service_context(_report, _log):
    smtp = FakeSMTP(None, 'plain')
    STUB.update(template_smtp=smtp, smtp_password=token_hex(20))
    try:
        yield
    finally:
        smtp.close()
        STUB.clear()


def models():
    from communications.models import CommunicationConfiguration, MessageTemplate, OutgoingMessage
    return MessageTemplate, OutgoingMessage, CommunicationConfiguration


def seed(source, created):
    from django.utils import timezone
    from application.models import Associate, User

    template, outgoing, configuration = models()
    ids = source['ids']
    association = ids['association']
    User._base_manager.filter(pk=ids['restricted_collaborator']).update(
        collaborator_permissions=['association.communication.messages.create'],
    )

    def add(model, **fields):
        row = model(sport_association_id=association, **fields)
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    add(Associate, pk=ids['recipient'], group_id=ids['group'], first_name='Giulia', last_name='Rossi',
        email='recipient@example.test', phone='+393331234567')
    add(template, **source['template'])
    add(configuration, email_smtp_host='127.0.0.1', email_smtp_port=STUB['template_smtp'].port,
        email_smtp_user='mailer@example.test', email_smtp_password=STUB['smtp_password'])
    for label in ('send', 'discard', 'interrupted'):
        text = {'subject': 'Rendered subject', 'body': 'Rendered body'}
        add(outgoing, id=ids[label], actor_id=ids['owner'], recipient_id=ids['recipient'],
            template_id=source['template']['id'], channel='email', locale='it', recipient='recipient@example.test',
            rendered_subject=text['subject'], rendered_body=text['body'], **text,
            status='sending' if label == 'interrupted' else 'preview', action='send' if label == 'interrupted' else '',
            updated_at=timezone.now() - timedelta(minutes=11))


fixture_context = harness.fixture_context_for(contract, seed, purge=(lambda: models()[0], lambda: models()[1]))


def check_rows(before, after, model, expected):
    old = keyed(before[model], 'id')
    new = keyed(after[model], 'id')
    changes = [row for pk, row in new.items() if old.get(pk) != row]
    contract.require(old.keys() <= new.keys() and len(changes) == len(expected), 'state.row-count')
    for fields in expected:
        matches = [row for row in changes if all(row[key] == value for key, value in fields.items())]
        contract.require(len(matches) == 1, 'state.row-fields')
        changes.remove(matches[0])
    return new


def check_smtp(expected):
    smtp = STUB['template_smtp']
    deadline = monotonic() + 5
    while expected and smtp.count() < 2 and monotonic() < deadline:
        sleep(0.01)
    transcripts = smtp.take()
    messages = [unit for units in transcripts for unit in units if unit.endswith('\r\n.\r\n')]
    contract.require(len(transcripts) == (2 if expected else 0), 'smtp.connections')
    contract.require(len(messages) == bool(expected), 'smtp.deliveries')
    if expected:
        mail = BytesParser(policy=policy.default).parsebytes(messages[0][:-3].encode('latin-1'))
        body = mail.get_body(preferencelist=('plain',)).get_content()
        contract.require(str(mail['Subject']) == expected['subject'] and body == expected['body']
                         and str(mail['To']) == 'recipient@example.test', 'smtp.approved-content')


def effect(case, before, after, source, _context):
    expected = case.get('effect', {})
    sent = expected.get('smtp')
    managed = (TEMPLATE, OUTGOING, harness.AUDIT, harness.INDEX)
    if sent:
        managed += (CONFIGURATION,)
        configurations = [{**row, 'daily_email_balance': row['daily_email_balance'] + 1}
                          for row in before[CONFIGURATION]]
        contract.require(after[CONFIGURATION] == configurations, 'state.delivery-balance')
    harness.require_unmanaged_unchanged(contract, before, after, managed)
    templates = check_rows(before, after, TEMPLATE, expected.get('templates', ()))
    check_rows(before, after, OUTGOING, expected.get('outgoing', ()))
    audit, indexes = harness.audit_delta(contract, before, after)
    expected_audit = expected.get('audit', [])
    contract.require(len(audit) == len(indexes) == len(expected_audit) + bool(sent), 'audit.count')
    types = {row['model']: row['id'] for row in before['contenttypes.ContentType'] if row['app_label'] == 'communications'}
    owner_id = source['ids']['owner']
    owner = keyed(before['application.User'], 'user_id')[owner_id]
    for specification in expected_audit:
        template_id, = [pk for pk, row in templates.items() if row['name'] == specification['template']]
        event, = [row for row in audit if row['object_pk'] == template_id]
        index = indexes[str(event['id'])]
        contract.require(event['content_type_id'] == types['messagetemplate'] and
                         event['actor_id'] == owner_id and event['actor_email'] == owner['email'] and
                         event['cid'] == case['name'] and
                         event['action'] == specification['action'] and event['changes'] == specification['changes'],
                         'audit.template-edit')
        contract.require(index['sport_association_id'] == source['ids']['association'] and
                         index['resolution_path'] == 'direct', 'audit.association-index')
    check_smtp(sent)


run_workload = harness.run_workload_for(contract, effect)
