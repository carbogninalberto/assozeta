"""Parent-only payment simulation, partial quotes apply, sign and invoiced approve HTTP runner; imports are inert.

Seeds a second association owner (with a token), the subscription fee plans
of the own, foreign and second associations, a payment category, the own
default bank account, two documents, a supplier, associates, invoices (one
with a document and an attached document), the authored payments and, for
every authored subscription, its previous-plan payment and the subscription
(audit disabled). Subscription creation dates are set at seed time inside or
before the current balance year (1 January, the owners' default start).
Each case is checked against the complete model state: only payments and
audit rows change; created payments match the authored columns, changed
payments only the authored columns, every other payment is unchanged, and
the new auditlog entries match the authored ones in order with their index
rows. A '$now' column must be recent and, when rendered, equal to the stored
value. Payments the run creates are removed before the fixture cleanup.

A stored signature adds its Signature row (checked against the authored user
and data) and its create entry ('$signature' is the new row's id). The Celery
broker is the run's Redis (with_celery_broker): each case's published
messages must be exactly its authored 'celery' tasks (none by default), with
'$render' messages rendered by Django's render_to_string. A CURRENT_HOST print
stub (invoice-sync-print's service_context) runs for the whole run: each
case's outbound requests must be exactly its authored 'prints'.

service_context also runs a fake SMTP server (plain, AUTH; email-tasks' FakeSMTP)
and pins send_email_html's Timer delay: application.tasks' random.randint
answers 1 for the run and the Go server gets SEND_EMAIL_HTML_DELAY_SECONDS=1.
A case with 'smtp' gets, only around its request, a CommunicationConfiguration
of the second association pointing at that server (audit disabled); the runner
waits (40 s at most) for the authored deliveries and the stored daily balance,
checks each delivered message (envelope, headers, the html part decoded against
Django's render_to_string), then removes the configuration. The balance save's
audit entry (no actor, no cid) is checked with the case's effect.
"""
from contextlib import ExitStack, contextmanager
import base64
import email
import email.header
import json
import os
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import time
import types

from TESTS.contracts import test_payment_misc_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import invoice_sync_print_http as printer
from TESTS.adapters.django.email_render_routes_http import resolve
from TESTS.adapters.django.camps_periods_http import plain
from TESTS.adapters.django.carnet_subscription_topup_http import auditlog_moment
from TESTS.adapters.django.course_subscriptions_http import default_category
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/payment_misc_http.json', 'TESTS/contracts/test_payment_misc_http.py',
           'TESTS/adapters/django/payment_misc_http.py', 'TESTS/adapters/django/camps_periods_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'TESTS/adapters/django/course_subscriptions_http.py',
           'BE/application/views/payment_views.py', 'BE/application/utils/payments_utils.py', 'BE/application/tasks.py',
           'BE/application/utils/api_utils.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/subscriptions_serializers.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/payment_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/balance_sheet_models.py', 'BE/application/signals.py',
           'BE/application/mixin.py', 'BE/application/impersonation.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py', 'BE/docmanager/models.py', 'TESTS/adapters/django/invoice_sync_print_http.py',
           'TESTS/adapters/django/email_render_routes_http.py', 'BE/application/printing_tasks.py', 'BE/instance/restore/task_base.py',
           'BE/templates/email/payment/fee_assigned.html', 'BE/templates/email/_base.html', 'BE/core/tasks.py',
           'BE/core/celery.py', 'BE/application/impersonation.py', 'BE/communications/models.py',
           'TESTS/adapters/go/email_tasks_worker_process.py') + harness.SOURCES
PAYMENT = 'application.Payment'
SIGNATURE = 'application.Signature'
MANAGED = (PAYMENT, SIGNATURE, harness.AUDIT, harness.INDEX)


SMTP = {}
SMTP_WAIT = 40


def _assert_case(case, base_url, *, token, sessions):
    """The authored request, with the print stub scripted for it and its outbound requests checked after."""
    stub = printer.STUB
    with stub['lock']:
        stub['log'] = []
        stub['script'] = [item['reply'] for item in case['prints']]
    stub['token'] = token
    spec = case.get('smtp')
    if spec is None:
        status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    else:
        with smtp_configuration():
            status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
            check_deliveries(spec)
    printer.check_prints(case, None)
    return status


@contextmanager
def smtp_configuration():
    """The second association's CommunicationConfiguration, only around the request (audit disabled)."""
    from auditlog.context import disable_auditlog
    from communications.models import CommunicationConfiguration
    from TESTS.adapters.go.email_tasks_worker_process import SMTP_PASSWORD
    source = base_contract.load_source()
    config = source['smtp_config']
    SMTP['server'].take()
    with disable_auditlog():
        CommunicationConfiguration._base_manager.bulk_create([CommunicationConfiguration(
            communication_configuration_id=config['id'], sport_association_id=source['plans_owner']['association'],
            email_smtp_host='127.0.0.1', email_smtp_port=SMTP['server'].port, email_smtp_user=config['user'],
            email_smtp_password=SMTP_PASSWORD, email_sender_name=config['sender'], email_encryption='NONE',
            daily_email_limit=config['limit'], daily_email_balance=config['balance'])])
    try:
        yield
    finally:
        with disable_auditlog():
            CommunicationConfiguration._base_manager.filter(pk=config['id']).delete()
        base_contract.require(not CommunicationConfiguration._base_manager.exists(), 'fixture.smtp-configuration-removed')


def delivered(transcripts):
    return [t for t in transcripts if any(unit.endswith('\r\n.\r\n') for unit in t)]


def check_deliveries(spec):
    """Wait for the Timer sends and the balance save, then check every delivered message."""
    from django.conf import settings
    from communications.models import CommunicationConfiguration
    require = base_contract.require
    source = base_contract.load_source()
    config = source['smtp_config']
    expected = spec['deliveries']
    require(len(expected) > 0, 'smtp.authored')
    seen, deadline = [], time.monotonic() + SMTP_WAIT
    while time.monotonic() < deadline:
        seen += SMTP['server'].take()
        balance = CommunicationConfiguration._base_manager.filter(pk=config['id']).values_list('daily_email_balance', flat=True).first()
        if len(delivered(seen)) >= len(expected) and balance == spec['balance']:
            break
        time.sleep(0.2)
    time.sleep(0.5)
    seen += SMTP['server'].take()
    messages = delivered(seen)
    require(len(messages) == len(expected), f'smtp.delivery-count:{len(messages)}')
    require(balance == spec['balance'], f'smtp.balance:{balance}')
    values = {'$whitelabel': settings.WHITELABEL_NAME}
    for transcript, authored in zip(messages, resolve(expected, values)):
        units = [unit for unit in transcript if not unit.startswith('<')]
        verbs = [unit.split(' ', 1)[0].strip().upper() for unit in units]
        require(verbs[0] in ('EHLO', 'HELO') and 'AUTH' in verbs and verbs[-1] == 'QUIT', 'smtp.session')
        mail_from = next(u for u in units if u.upper().startswith('MAIL FROM:'))
        rcpts = [u for u in units if u.upper().startswith('RCPT TO:')]
        require(mail_from.upper().startswith(f"MAIL FROM:<{config['user']}>".upper()), 'smtp.mail-from')
        require([r.strip()[8:] for r in rcpts] == [f"<{authored['to']}>"], 'smtp.rcpt')
        data = next(u for u in units if u.endswith('\r\n.\r\n'))
        # FakeSMTP records the MIME boundary as '<boundary>', which the parser would read as an angle-quoted value.
        message = email.message_from_string(data[:-5].replace('\r\n..', '\r\n.').replace('<boundary>', 'fixture-boundary'))
        require(message.get_content_type() == 'multipart/alternative' and message['MIME-Version'] == '1.0', 'smtp.container')
        require([(k, str(email.header.make_header(email.header.decode_header(v)))) for k, v in message.items()
                 if k not in ('Content-Type', 'MIME-Version')]
                == [('From', authored['from']), ('To', authored['to']), ('Subject', authored['subject']), ('Reply-To', authored['reply_to'])],
                'smtp.headers')
        parts = message.get_payload()
        require(len(parts) == 1 and parts[0].get_content_type() == 'text/html' and parts[0].get_content_charset() == 'utf-8'
                and parts[0]['Content-Transfer-Encoding'] == 'base64', 'smtp.part')
        require(parts[0].get_payload(decode=True).decode('utf-8') == authored['html'], 'smtp.html')


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 NOW=base_contract.NOW)


@contextmanager
def service_context(report, log):
    """The print stub, the fake SMTP server and the pinned send_email_html delay."""
    from TESTS.adapters.go.email_tasks_worker_process import FakeSMTP
    import application.tasks as tasks
    saved_env = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    with ExitStack() as stack:
        server = FakeSMTP(None, False)
        report['service_cleanup']['payment_misc_smtp'] = 'running'
        stack.callback(server.close)
        SMTP['server'] = server
        stack.callback(SMTP.clear)
        stack.enter_context(printer.service_context(report, log))
        environment = json.loads(os.environ.get('GO_FIXTURE_ENVIRONMENT') or '{}')
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({**environment, 'SEND_EMAIL_HTML_DELAY_SECONDS': '1'})
        stack.enter_context(mock.patch.object(tasks, 'random', types.SimpleNamespace(randint=lambda low, high: 1)))
        try:
            yield
        finally:
            restored = os.environ.get('GO_FIXTURE_ENVIRONMENT')
            if restored is not None:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({k: v for k, v in json.loads(restored).items()
                                                                   if k != 'SEND_EMAIL_HTML_DELAY_SECONDS'})
            report['service_cleanup']['payment_misc_smtp'] = 'stopped'
    if saved_env is None:
        os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
TIMES = ('creation_date', 'payment_date')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def created_at(label):
    """Inside the current balance year (from 1 January) or in the previous one."""
    year = datetime.now(timezone.utc).year
    return datetime(year, 1, 1, 0, 1, tzinfo=timezone.utc) if label == 'in_year' else datetime(year - 1, 6, 1, tzinfo=timezone.utc)


def seed_rows(source, created):
    from application.models import (Associate, Invoice, Payment, PaymentCategory, SportAssociation, Subscription, SupplierAndCustomers,
                                    User)
    from application.models.balance_sheet_models import CustomAccounts
    from docmanager.models import Document
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    second = source['plans_owner']
    insert(User, User(user_id=second['id'], username=second['username'], email=second['email'], password='!synthetic', role=User.ASSOCIATION,
                      first_name=second['first'], last_name=second['last']))
    insert(SportAssociation, SportAssociation(sport_association_id=second['association'], user_id=second['id'],
                                              denomination=second['denomination'], tax_code='79900000001'))
    member = source['plans_member']
    insert(User, User(user_id=member['id'], username=member['username'], email=member['email'], password='!synthetic', role=User.ATHLETE,
                      first_name=member['first'], last_name=member['last']))
    for association, plans in source['plans'].items():
        SportAssociation._base_manager.filter(pk=association).update(subscription_fee_plans=plans)
    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category']['id'], name=source['category']['name'],
                                            sport_association_id=own, creation_date=moment('2026-01-01T00:00:00Z')))
    bank = source['bank']
    insert(CustomAccounts, CustomAccounts(custom_account_id=bank['id'], name=bank['name'], initial_balance=Decimal('0.00'),
                                          account_type=CustomAccounts.BANK, account_code=bank['code'], sport_association_id=own,
                                          editable=False, enabled=True))
    for key in ('document', 'attached'):
        doc = source[key]
        insert(Document, Document(document_id=doc['id'], token=doc['token'], filename=doc['filename'], filepath=doc['filepath'],
                                  file_size_bytes=doc['size'], creation_date=moment(doc['created'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=own))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'],
                                    user_id={own: owner, second['association']: second['id']}.get(row['association'], ids['foreign_owner']),
                                    first_name=row['first'],
                                    last_name=row['last'], sex='M', born_date=date(1990, 5, 10), is_minor=False,
                                    group_id=ids[row['group']] if row['group'] else None))
    for row in source['invoices'].values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=own, creation_date=moment(row['created']),
                                membership_fee=Decimal(row['membership_fee']), activity_fee=Decimal(row['activity_fee']),
                                meta_payment_categories=row['meta_payment_categories'], description=row['description'], number=row['number'],
                                document_pdf_id=source['document']['id'] if row['document'] else None))
        if row['attached']:
            Invoice.attached_documents.through.objects.bulk_create([Invoice.attached_documents.through(
                invoice_id=row['id'], document_id=source['attached']['id'])])
    for row in source['payments'].values():
        values = {key: value for key, value in row.items() if key != 'id'}
        for column in TIMES:
            values[column] = moment(values[column])
        values['amount'] = Decimal(values['amount'])
        insert(Payment, Payment(payment_id=row['id'], **values))
    for row in source['subscriptions'].values():
        if row['payment']:
            insert(Payment, Payment(payment_id=row['payment'], subject=1, amount=Decimal('10.00'), meta=row['payment_meta'], user_id=row['user'],
                                    associate_id=row['associate'], sport_association_id=row['association'], type='default',
                                    description='Quota precedente', creation_date=moment('2026-01-01T00:00:00Z')))
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
                                          associate_id=row['associate'], payment_id=row['payment'], creation_date=created_at(row['created']),
                                          deleted=row['deleted'], archived=row['archived'], meta=row['meta'],
                                          group_id=ids[row['group']] if row['group'] else None, status_flag=Subscription.ACCEPTED))


def payments():
    from application.models import Payment
    return Payment


def signatures():
    from application.models import Signature
    return Signature


base_fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows, purge=(payments, signatures)))


def prepare(source):
    return {'started': time.time()}


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from django.conf import settings
    from rest_framework_simplejwt.tokens import AccessToken
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'], fixture['default_category_name'] = category, name
        fixture['sessions'] = {**fixture['sessions'], '$whitelabel': settings.WHITELABEL_NAME}
        token = AccessToken()
        token['user_id'] = contract.load_source()['plans_owner']['id']
        fixture['tokens'] = {**fixture['tokens'], 'plans_owner': str(token)}
        yield fixture


def rows_of(state):
    return {key: plain(row) for key, row in keyed(state[PAYMENT], 'payment_id').items()}


def recent(text):
    stamp = moment(text) if isinstance(text, str) else None
    return stamp is not None and abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=10)


def check_row(stored, authored, fixture, where):
    bindings = {'$default_category': fixture['default_category']}
    for column, value in authored.items():
        actual = stored.get(column)
        if value == '$now':
            contract.require(recent(actual), 'state.now:' + column + where)
        else:
            contract.require(actual == bindings.get(value, value) if isinstance(value, str) else actual == value,
                             'state.created-column:' + column + where)
    contract.require(set(stored) - {'payment_id'} == set(authored), 'state.created-columns' + where)


def mail_bindings(before, ids):
    from django.conf import settings
    users = keyed(before['application.User'], 'user_id')
    values = {'$whitelabel': settings.WHITELABEL_NAME, '$from_email': settings.DEFAULT_FROM_EMAIL}
    for actor in ('owner', 'athlete'):
        values[f'$email:{actor}'] = users[ids[actor]]['email']
    return values


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    fixture, ids = context['fixture'], {**source['ids'], 'plans_owner': source['plans_owner']['id']}
    since, until = context['prepared']['started'], time.time()
    celery = resolve(effect.get('celery', []), mail_bindings(before, ids)) if effect else []
    harness.celery_published(contract, fixture, celery, since=since, until=until)
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    old_signatures, new_signatures = keyed(before[SIGNATURE], 'signature_id'), keyed(after[SIGNATURE], 'signature_id')
    fresh_signatures = sorted(set(new_signatures) - set(old_signatures))
    authored_signatures = effect.get('signatures', [])
    contract.require(set(old_signatures) <= set(new_signatures) and len(fresh_signatures) == len(authored_signatures), 'state.signature-count')
    contract.require(all(new_signatures[k] == v for k, v in old_signatures.items()), 'state.signatures-kept')
    for key, spec in zip(fresh_signatures, authored_signatures):
        stored = plain(new_signatures[key])
        contract.require(str(stored['user_id']) == spec['user'] and stored['signature'] == spec['signature'], 'state.signature:' + key)
    signature = fresh_signatures[0] if fresh_signatures else None
    old, new = rows_of(before), rows_of(after)
    fresh = sorted(set(new) - set(old))
    authored = effect['created']
    contract.require(len(fresh) == len(authored) and set(old) <= set(new), 'state.created-count')
    for key, spec in zip(fresh, authored):
        check_row(new[key], spec, fixture, ':' + key)
    labels = {row['id']: label for label, row in source['payments'].items()}
    bindings = {'$default_category': fixture['default_category'], '$signature': signature}
    for key, row in old.items():
        changes = effect['changed'].get(labels.get(key), {})
        expected = dict(row)
        for column, value in changes.items():
            if value == '$now':
                contract.require(recent(new[key].get(column)), 'state.now:' + column)
                if contract.NOW:
                    contract.require(new[key].get(column) in contract.NOW, 'response.now-stored')
                value = new[key].get(column)
            expected[column] = bindings.get(value, value) if isinstance(value, str) else value
        contract.require(new[key] == expected, 'state.payment:' + labels.get(key, key))
    events, indexes = harness.audit_delta(contract, before, after)
    events.sort(key=lambda row: int(row['id']))
    events = check_configuration_audit(effect, events, indexes, before, source)
    contract.require(len(events) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    for event, spec in zip(events, effect['audit']):
        model = spec.get('model', 'payment')
        if model == 'signature':
            key = signature
        else:
            key = fresh[0] if spec['key'] == '$id' else source['payments'][spec['key']]['id']
        stored = new.get(key, {})

        def value(item, column):
            if item == '$id':
                return key
            if item == '$now':
                return auditlog_moment(moment(stored[column]))
            return bindings.get(item, item)
        changes = {column: [value(item, column) for item in pair] for column, pair in spec['changes'].items()}
        where = f":{spec['key']}:{spec['action']}"
        contract.require(event['content_type_id'] == types[model] and event['action'] == spec['action'] and event['object_pk'] == key
                         and event['object_repr'] == f'{model.capitalize()} object ({key})', 'audit.identity' + where)
        contract.require(event['changes'] == changes, 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor' + where)
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context' + where)
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                             and index['resolution_path'] == spec['index'][1], 'index.fields' + where)


def check_configuration_audit(effect, events, indexes, before, source):
    """The Timer's balance save entry (authored 'smtp_config_audit'); returns the other entries."""
    kinds = {ct['id']: (ct['app_label'], ct['model']) for ct in before['contenttypes.ContentType']}
    config = [e for e in events if kinds.get(e['content_type_id']) == ('communications', 'communicationconfiguration')]
    rest = [e for e in events if e not in config]
    spec = effect.get('smtp_config_audit')
    contract.require(len(config) == (1 if spec else 0), 'audit.configuration-count')
    if spec:
        event, key = config[0], source['smtp_config']['id']
        contract.require(event['action'] == 1 and event['object_pk'] == key
                         and event['object_repr'] == f'CommunicationConfiguration object ({key})', 'audit.configuration-identity')
        contract.require(event['changes'] == spec['changes'], 'audit.configuration-changes')
        contract.require(event['actor_id'] is None and event['cid'] is None and event['remote_addr'] is None
                         and event['additional_data'] is None, 'audit.configuration-context')
        index = indexes.pop(str(event['id']), None)
        contract.require(index is not None and index['sport_association_id'] == spec['index'][0]
                         and index['resolution_path'] == spec['index'][1], 'audit.configuration-index')
    return rest


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
