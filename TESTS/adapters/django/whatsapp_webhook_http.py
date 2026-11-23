"""Parent-only communications/whatsapp/webhook HTTP runner; imports are inert.

seed_rows inserts (audit disabled) a self-hosted InstanceConfiguration whose
WhatsApp integration holds the fixture's synthetic verify token and app
secret encrypted like instance settings, with no access token (so neither the
Celery task nor the Go outbox worker ever sends during the run), and one
WhatsAppDelivery per fixture label. A case's 'config' overrides those settings
for its request only (restored before the state is read). Every case must
leave every model except WhatsAppDelivery and WhatsAppReceipt unchanged; the
authored deliveries change exactly the listed fields (updated_at moves inside
the request window) and the authored receipts are the only new receipts.
Unlike harness.run_workload_for, every case runs even after a failure, the
effect is checked even when the response differs, each failure is printed
(CASE-FAIL), and the first one fails the workload.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import traceback

from TESTS.contracts import test_whatsapp_webhook_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/whatsapp_webhook_http.json', 'TESTS/contracts/test_whatsapp_webhook_http.py',
           'TESTS/adapters/django/whatsapp_webhook_http.py', 'BE/communications/whatsapp_webhook.py',
           'BE/communications/whatsapp_provider.py', 'BE/communications/whatsapp.py', 'BE/communications/models.py',
           'BE/communications/urls.py', 'BE/core/middleware.py', 'BE/instance/integration_configuration.py') + harness.SOURCES
DELIVERY, RECEIPT = 'communications.WhatsAppDelivery', 'communications.WhatsAppReceipt'


def parse(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def settings_for(source, override=None):
    from instance.integration_configuration import encrypt_secret
    plain = {'enabled': True, 'phone_number_id': source['whatsapp']['phone_number_id'],
             'business_account_id': source['whatsapp']['business_account_id'], 'access_token': '',
             'app_secret': source['whatsapp']['app_secret'], 'webhook_verify_token': source['whatsapp']['webhook_verify_token'],
             'notification_routes': '{}', **(override or {})}
    for key in ('access_token', 'app_secret', 'webhook_verify_token'):
        plain[key] = encrypt_secret(plain[key])
    return {'whatsapp': {'revision': 1, 'settings': plain}}


def seed_rows(source, created):
    from communications.models import WhatsAppDelivery
    from instance.models import InstanceConfiguration

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    insert(InstanceConfiguration, InstanceConfiguration(domain='whatsapp-webhook.example.test', name='WhatsApp webhook fixture',
                                                        self_hosted=True, integration_settings=settings_for(source)))
    seeded = parse(source['whatsapp']['seeded_at'])
    for row in source['deliveries'].values():
        insert(WhatsAppDelivery, WhatsAppDelivery(
            id=row['id'], event_type=row['event_type'], recipient=row['recipient'], body=row['body'], status=row['status'],
            provider_message_id=row['provider_message_id'], attempts=row['attempts'], error_code=row['error_code'],
            error_message=row['error_message'], created_at=seeded, updated_at=seeded, next_attempt_at=parse(row['next_attempt_at'])))


@contextmanager
def case_config(case, source):
    if not case['config']:
        yield
        return
    from instance.models import InstanceConfiguration
    row = InstanceConfiguration._base_manager.get()
    original = row.integration_settings
    InstanceConfiguration._base_manager.filter(pk=row.pk).update(integration_settings=settings_for(source, case['config']))
    try:
        yield
    finally:
        InstanceConfiguration._base_manager.filter(pk=row.pk).update(integration_settings=original)


def assert_effect(case, before, after, source, started, finished):
    harness.require_unmanaged_unchanged(contract, before, after, {DELIVERY, RECEIPT})
    ids = {row['id']: label for label, row in source['deliveries'].items()}
    old, new = keyed(before[DELIVERY], 'id'), keyed(after[DELIVERY], 'id')
    contract.require(set(old) == set(new), 'effect.delivery-rows')
    expected = case['effect']['deliveries']
    for key, row in new.items():
        label = ids.get(key)
        changes = expected.get(label) if label else None
        if not changes:
            contract.require(row == old[key], 'effect.delivery-unchanged:' + str(label))
            continue
        moved = parse(row['updated_at'])
        contract.require(started - timedelta(seconds=1) <= moved <= finished + timedelta(seconds=1), 'effect.updated-at:' + label)
        wanted = {**old[key], **changes, 'updated_at': row['updated_at']}
        for field in wanted:
            contract.require(row[field] == wanted[field], f'effect.delivery:{label}.{field}')
    before_receipts, after_receipts = keyed(before[RECEIPT], 'id'), keyed(after[RECEIPT], 'id')
    contract.require(all(after_receipts.get(key) == row for key, row in before_receipts.items()), 'effect.receipts-unchanged')
    added = [after_receipts[key] for key in sorted(set(after_receipts) - set(before_receipts), key=int)]
    contract.require(len(added) == len(case['effect']['receipts']), 'effect.receipt-count')
    for row, wanted in zip(added, case['effect']['receipts']):
        contract.require(row['delivery_id'] == source['deliveries'][wanted['delivery']]['id'], 'effect.receipt-delivery')
        for field in ('provider_message_id', 'status', 'error_code', 'error_message'):
            contract.require(row[field] == wanted[field], 'effect.receipt.' + field)
        contract.require(parse(row['occurred_at']) == parse(wanted['occurred_at']), 'effect.receipt.occurred_at')


fixture_context = harness.fixture_context_for(contract, seed_rows)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, failures = [], []
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        errors = []
        started = datetime.now(timezone.utc)
        try:
            with case_config(case, source):
                status = contract.assert_case(case, base_url, token=token)
        except contract.ResponseFailure as error:
            # The effect is still checked, so a response difference cannot hide a state difference.
            status = error.actual_status
            errors.append(error)
        finished = datetime.now(timezone.utc)
        try:
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, started, finished)
        except Exception as error:
            errors.append(error)
        for error in errors:
            error.case = f'{label}:{case["name"]}'
            print(f'CASE-FAIL {error.case} {type(error).__name__} {str(error)[:160]} status={getattr(error, "actual_status", None)}',
                  flush=True)
            if not isinstance(error, contract.ResponseFailure):
                traceback.print_exc()
        if errors:
            failures.append(errors[0])
        else:
            results.append({'case': case['name'], 'status': status})
    if failures:
        raise failures[0]
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
