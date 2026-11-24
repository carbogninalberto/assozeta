"""deliver_whatsapp parity: the Celery task (beat 'deliver-whatsapp', every 10 s) vs the Go server's outbox worker.

Django (BE/communications/tasks.py, whatsapp.py, whatsapp_provider.py): each
run builds CloudAPI() and stops when the integration is not enabled, then calls
deliver_one up to 50 times until no row is due. deliver_one locks the first
queued row with next_attempt_at <= now (order next_attempt_at, id; SKIP
LOCKED), increments attempts and sends it (text, or template with a body
parameter component when body is set; biz_opaque_callback_data = str(id),
'to' without the leading '+'): success stores the provider message id and
'sent' with empty errors; a ProviderError stores its code and message and
either reschedules (transient and attempts < 3: next_attempt_at = now +
30 * 2 ** (attempts - 1) s) or marks the row 'failed'. Provider rules: a
missing resource id or access token is a non-transient 'configuration' error;
connection errors are transient 'network'; a non-2xx answer uses error.code
when it is an int (else the HTTP status) and error.message when the key exists
(else 'WhatsApp API request failed'), redacted and cut to 1000 characters,
transient for 429 and 5xx; a 2xx answer must be a JSON object (else
'invalid_response'/'Invalid WhatsApp API response') whose messages[0].id is
a non-empty string (else 'Missing WhatsApp message ID'); redirects are not
followed.

Go (BE_GO/cmd/server/main.go outbox goroutine -> internal/whatsapp
DeliveryQueue.Run): a 10 s ticker running DeliverOne up to 50 times per tick.

The run reuses the HTTP family driver (private PostgreSQL database, Redis,
migrated Django and Go schemas, the real Go server binary) with a private Meta
stub as WHATSAPP_API_BASE_URL for both backends. Each scenario seeds the
instance WhatsApp integration and the outbox rows. The Django oracle runs the
actual task body in-process inside one transaction that is rolled back, so the
running Go server never sees that scenario; the Go phase commits the same seed
and waits for the outbox worker's first tick. Both phases must produce the
authored rows and provider requests (expectations written from the Django
source above).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.go.whatsapp_meta_stub import MetaStub, json_reply, raw_reply  # noqa: E402

SOURCES = ('TESTS/adapters/go/whatsapp_delivery_process.py', 'TESTS/adapters/go/whatsapp_meta_stub.py',
           'BE/communications/tasks.py', 'BE/communications/whatsapp.py', 'BE/communications/whatsapp_provider.py',
           'BE/communications/models.py', 'BE/core/celery.py', 'BE/instance/integration_configuration.py',
           'BE_GO/internal/whatsapp/delivery.go', 'BE_GO/internal/whatsapp/provider.go', 'BE_GO/internal/config/whatsapp.go',
           'BE_GO/internal/service/instance_admin.go', 'TESTS/adapters/django/family_harness.py')
FAMILY = 'whatsapp-delivery'
OUTPUT = ROOT / 'quality-reports/go-migration/whatsapp-delivery-process'
PHONE, WABA, TOKEN = '1098765432109', '2109876543210', 'synthetic-whatsapp-access-token'
PATH = f'/v23.0/{PHONE}/messages'
SEEDED = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
STUB = {}
DELIVERY = 'communications.WhatsAppDelivery'


def U(n):
    return f'a1000000-0000-4000-8000-{1960001 + n:012d}'


def ok(provider):
    return json_reply(200, {'messaging_product': 'whatsapp', 'contacts': [{'input': '39', 'wa_id': '39'}],
                            'messages': [{'id': provider}]})


def meta_error(status, code, message, **extra):
    error = {'message': message, 'type': 'OAuthException', 'code': code, **extra}
    return json_reply(status, {'error': error})


def row(n, to, *, body='Notifica', template=('', ''), status='queued', attempts=0, provider=None, due=True,
        reply=None, expect=None):
    """One outbox row, the stub reply for its recipient and the authored outcome (None: untouched)."""
    return {'id': U(n), 'recipient': '+' + to, 'body': body, 'template_name': template[0], 'template_language': template[1],
            'status': status, 'attempts': attempts, 'provider_message_id': provider, 'order': n,
            'next_attempt_at': (SEEDED - timedelta(minutes=200 - n)) if due else FUTURE, 'reply': reply, 'expect': expect}


def sent(provider, attempts=1):
    return {'status': 'sent', 'attempts': attempts, 'provider_message_id': provider, 'error_code': '', 'error_message': '', 'retry': None}


def failed(code, message, attempts=1):
    return {'status': 'failed', 'attempts': attempts, 'provider_message_id': None, 'error_code': code, 'error_message': message,
            'retry': None}


def retried(code, message, attempts, seconds):
    return {'status': 'queued', 'attempts': attempts, 'provider_message_id': None, 'error_code': code, 'error_message': message,
            'retry': seconds}


ENABLED = {'enabled': True, 'access_token': TOKEN}
SCENARIOS = [
    {'name': 'mixed outcomes of one run', 'config': ENABLED, 'rows': [
        row(1, '393330000001', body='Ciao Mario', reply=ok('wamid.OK1'), expect=sent('wamid.OK1')),
        row(2, '393330000002', body='Mario', template=('iscrizione_accettata', 'it'), reply=ok('wamid.OK2'), expect=sent('wamid.OK2')),
        row(3, '393330000003', body='', template=('promemoria', 'en_US'), reply=ok('wamid.OK3'), expect=sent('wamid.OK3')),
        row(4, '393330000004', reply=meta_error(400, 131026, 'Message undeliverable'),
            expect=failed('131026', 'Message undeliverable')),
        row(5, '393330000005', reply=meta_error(500, 2, 'Service temporarily unavailable'),
            expect=retried('2', 'Service temporarily unavailable', 1, 30)),
        row(6, '393330000006', attempts=1, reply=meta_error(503, 1, 'Unknown error'), expect=retried('1', 'Unknown error', 2, 60)),
        row(7, '393330000007', attempts=2, reply=meta_error(429, 130429, 'Rate limit hit'),
            expect=failed('130429', 'Rate limit hit', 3)),
        row(8, '393330000008', reply=meta_error(401, 190, f'Invalid OAuth access token {TOKEN}.'),
            expect=failed('190', 'Invalid OAuth access token [redacted].')),
        row(9, '393330000009', reply=json_reply(200, {'messages': []}), expect=failed('invalid_response', 'Missing WhatsApp message ID')),
        row(10, '393330000010', reply=raw_reply(200, 'ok', 'text/plain'), expect=failed('invalid_response', 'Invalid WhatsApp API response')),
        row(11, '393330000011', reply='close', expect=retried('network', 'WhatsApp connection failed', 1, 30)),
        row(12, '393330000012', reply=json_reply(403, {'error': {'message': 'Forbidden'}}), expect=failed('403', 'Forbidden')),
        row(13, '393330000013', reply=raw_reply(404, 'not json', 'text/html'), expect=failed('404', 'WhatsApp API request failed')),
        row(14, '393330000014', reply=raw_reply(302, '', 'text/html', {'Location': '/elsewhere'}),
            expect=failed('302', 'WhatsApp API request failed')),
        row(15, '393330000015', reply=json_reply(400, {'error': {'code': 100, 'message': 'é' * 1100}}),
            expect=failed('100', 'é' * 1000)),
        row(16, '393330000016', reply=json_reply(200, ['wamid.LIST']), expect=failed('invalid_response', 'Invalid WhatsApp API response')),
        row(17, '393330000017', status='failed', reply=ok('wamid.NEVER')),
        row(18, '393330000018', status='sent', provider='wamid.ALREADY', reply=ok('wamid.NEVER2')),
        row(19, '393330000019', due=False, reply=ok('wamid.LATER')),
    ]},
    {'name': 'provider answers Django reads differently', 'config': ENABLED, 'rows': [
        row(31, '393330000031', reply=raw_reply(200, '{"messages": ['), expect=failed('invalid_response', 'Invalid WhatsApp API response')),
        row(32, '393330000032', reply=raw_reply(200, '{"messages": [{"id": "wamid.T"}]} trailing'),
            expect=failed('invalid_response', 'Invalid WhatsApp API response')),
        row(33, '393330000033', reply=json_reply(200, {'messages': [{'id': 5}]}), expect=failed('invalid_response', 'Missing WhatsApp message ID')),
        row(34, '393330000034', reply=json_reply(200, {'MESSAGES': [{'ID': 'wamid.CASE'}]}),
            expect=failed('invalid_response', 'Missing WhatsApp message ID')),
        row(35, '393330000035', reply=json_reply(400, {'error': {'code': '131026', 'message': 'Text code'}}), expect=failed('400', 'Text code')),
        row(36, '393330000036', reply=json_reply(400, {'error': {'code': 100, 'message': ''}}), expect=failed('100', '')),
        row(37, '393330000037', reply=json_reply(400, {'error': {'code': 1.5, 'message': 'Float code'}}), expect=failed('400', 'Float code')),
    ]},
    # The instance flag alone enables the task; the missing token fails each due row without a request.
    {'name': 'enabled integration without access token', 'config': {'enabled': True, 'access_token': ''}, 'rows': [
        row(41, '393330000041', reply=ok('wamid.X1'), expect=failed('configuration', 'Missing WhatsApp resource ID or access token')),
        row(42, '393330000042', reply=ok('wamid.X2'), expect=failed('configuration', 'Missing WhatsApp resource ID or access token')),
    ]},
    {'name': 'disabled integration sends nothing', 'config': {'enabled': False, 'access_token': TOKEN}, 'rows': [
        row(51, '393330000051', reply=ok('wamid.D1')),
    ]},
    {'name': 'one run sends at most fifty rows', 'config': ENABLED, 'rows': [
        row(100 + n, f'3933310{n:05d}', reply=ok(f'wamid.B{n}'), expect=sent(f'wamid.B{n}') if n < 50 else None) for n in range(52)
    ]},
]
EXPECTED = [scenario['name'] for scenario in SCENARIOS]


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def settings_for(config):
    from instance.integration_configuration import encrypt_secret
    plain = {'enabled': config['enabled'], 'phone_number_id': PHONE, 'business_account_id': WABA,
             'access_token': encrypt_secret(config['access_token']), 'app_secret': encrypt_secret('synthetic-app-secret'),
             'webhook_verify_token': encrypt_secret('synthetic-verify-token'), 'notification_routes': '{}'}
    return {'whatsapp': {'revision': 1, 'settings': plain}}


class Spec:
    """The fixture_context_for contract: the base fixture plus a disabled instance integration."""
    require = staticmethod(require)

    @staticmethod
    def load_source():
        return {'cases': []}


def seed_rows(_source, created):
    from instance.models import InstanceConfiguration
    require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    row = InstanceConfiguration(domain='whatsapp-delivery.example.test', name='WhatsApp delivery fixture', self_hosted=True,
                                integration_settings=settings_for({'enabled': False, 'access_token': ''}))
    InstanceConfiguration._base_manager.bulk_create([row])
    created.append((InstanceConfiguration, row.pk))


def _models():
    from communications.models import WhatsAppDelivery, WhatsAppReceipt
    return WhatsAppDelivery, WhatsAppReceipt


def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from TESTS.adapters.django import family_harness as harness
    return harness.fixture_context_for(Spec, seed_rows, purge=(lambda: _models()[0], lambda: _models()[1]))(
        apps, private_work, raw, cleanup_receipt=cleanup_receipt)


@contextmanager
def service_context(report, log):
    """The stub is up before either server starts; Go reads its address from the fixture environment."""
    with MetaStub() as stub:
        STUB['stub'] = stub
        environment = {**json.loads(os.environ.get('GO_FIXTURE_ENVIRONMENT', '{}')),
                       'WHATSAPP_API_BASE_URL': stub.base_url, 'WHATSAPP_API_VERSION': 'v23.0'}
        report['whatsapp_stub'] = 'loopback'
        with patch.dict(os.environ, {'GO_FIXTURE_ENVIRONMENT': json.dumps(environment)}):
            yield


def seed(scenario):
    from communications.models import WhatsAppDelivery
    from instance.models import InstanceConfiguration
    InstanceConfiguration._base_manager.update(integration_settings=settings_for(scenario['config']))
    WhatsAppDelivery._base_manager.bulk_create([WhatsAppDelivery(
        id=item['id'], event_type='subscription.accepted', recipient=item['recipient'], body=item['body'],
        template_name=item['template_name'], template_language=item['template_language'], status=item['status'],
        attempts=item['attempts'], provider_message_id=item['provider_message_id'], created_at=SEEDED, updated_at=SEEDED,
        next_attempt_at=item['next_attempt_at']) for item in scenario['rows']])
    STUB['stub'].reset({item['recipient'][1:]: item['reply'] for item in scenario['rows']})


def unseed():
    from communications.models import WhatsAppDelivery
    from instance.models import InstanceConfiguration
    InstanceConfiguration._base_manager.update(integration_settings=settings_for({'enabled': False, 'access_token': ''}))
    WhatsAppDelivery._base_manager.all().delete()


def forget_effects():
    """Send intents are committed on their own connection, so the rolled-back oracle run leaves them behind.

    The ownership ledger (migration 8) keeps one effect per delivery; an intent left by the Django run
    would make the Go run treat the same rows as already attempted."""
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.assozeta_outbox_effects') IS NOT NULL")
        if cursor.fetchone()[0]:
            cursor.execute("DELETE FROM public.assozeta_outbox_effects WHERE outbox='whatsapp'")


def observed():
    from communications.models import WhatsAppDelivery
    return {str(item['id']): item for item in WhatsAppDelivery._base_manager.values()}


def expected_request(item):
    if item['template_name']:
        content = {'name': item['template_name'], 'language': {'code': item['template_language']}}
        if item['body']:
            content['components'] = [{'type': 'body', 'parameters': [{'type': 'text', 'text': item['body']}]}]
        kind = 'template'
    else:
        kind, content = 'text', {'body': item['body']}
    return {'method': 'POST', 'path': PATH, 'query': {}, 'authorization': f'Bearer {TOKEN}',
            'body': {'messaging_product': 'whatsapp', 'to': item['recipient'].lstrip('+'), 'type': kind, kind: content,
                     'biz_opaque_callback_data': item['id']}}


def verify(scenario, rows, requests, started, finished):
    """Every authored row outcome and the exact provider request sequence; returns the failure codes."""
    problems = []
    for item in scenario['rows']:
        actual, expect = rows[item['id']], item['expect']
        if expect is None:
            same = (actual['status'], actual['attempts'], actual['provider_message_id'], actual['error_code'], actual['error_message'],
                    actual['updated_at'], actual['next_attempt_at']) == (item['status'], item['attempts'], item['provider_message_id'],
                                                                         '', '', SEEDED, item['next_attempt_at'])
            if not same:
                problems.append(f'row-{item["order"]}.untouched')
            continue
        for field in ('status', 'attempts', 'provider_message_id', 'error_code', 'error_message'):
            if actual[field] != expect[field]:
                problems.append(f'row-{item["order"]}.{field}={str(actual[field])[:60]!r}')
        if not (started - timedelta(seconds=1) <= actual['updated_at'] <= finished + timedelta(seconds=1)):
            problems.append(f'row-{item["order"]}.updated_at')
        if expect['retry'] is None:
            if actual['next_attempt_at'] != item['next_attempt_at']:
                problems.append(f'row-{item["order"]}.next_attempt_at')
        elif round((actual['next_attempt_at'] - actual['updated_at']).total_seconds()) != expect['retry']:
            problems.append(f'row-{item["order"]}.retry')
    wanted = [expected_request(item) for item in scenario['rows'] if item['expect'] is not None
              and item['expect']['error_code'] != 'configuration']
    actual = []
    for record in requests:
        try:
            body = json.loads(record['body'])
        except ValueError:
            body = record['body']
        actual.append({'method': record['method'], 'path': record['path'], 'query': record['query'],
                       'authorization': record['authorization'], 'body': body})
        if not (record['content_type'] or '').startswith('application/json'):
            problems.append('request.content-type')
    if actual != wanted:
        problems.append(f'requests: expected {len(wanted)}, got {len(actual)}'
                        + next((f'; first difference at {i}' for i, (a, b) in enumerate(zip(actual, wanted)) if a != b), ''))
    return problems


def run_django(scenario):
    from django.db import transaction
    from django.test.utils import override_settings
    from communications.tasks import deliver_whatsapp
    stub = STUB['stub']
    with override_settings(WHATSAPP_API_BASE_URL=stub.base_url, WHATSAPP_API_VERSION='v23.0'), transaction.atomic():
        seed(scenario)
        started = datetime.now(timezone.utc)
        deliver_whatsapp()
        finished = datetime.now(timezone.utc)
        rows, requests = observed(), stub.taken()
        # The running Go server never sees this scenario.
        transaction.set_rollback(True)
    forget_effects()
    return rows, requests, started, finished


def run_go(scenario):
    from django.db import transaction
    stub = STUB['stub']
    work = [item for item in scenario['rows'] if item['expect'] is not None]
    calls = len([item for item in work if item['expect']['error_code'] != 'configuration'])
    try:
        with transaction.atomic():
            seed(scenario)
        started = datetime.now(timezone.utc)
        deadline = time.monotonic() + 24
        # One outbox tick (10 s) handles what one task run handles; wait for it, or a full tick when nothing should happen.
        while time.monotonic() < deadline:
            rows = observed()
            touched = sum(1 for item in work if rows[item['id']]['updated_at'] != SEEDED)
            if work and touched >= len(work) and len(stub.taken()) >= calls:
                break
            time.sleep(0.25)
        time.sleep(1.5)
        finished = datetime.now(timezone.utc)
        rows, requests = observed(), stub.taken()
    finally:
        with transaction.atomic():
            unseed()
            forget_effects()
    return rows, requests, started, finished - timedelta(seconds=1.5)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    results, failures = [], []
    for scenario in SCENARIOS:
        rows, requests, started, finished = (run_django if backend == 'django' else run_go)(scenario)
        problems = verify(scenario, rows, requests, started, finished)
        if problems:
            print(f'SCENARIO-FAIL {label}:{scenario["name"]}: ' + '; '.join(problems[:40]), flush=True)
            failures.append((scenario['name'], problems))
        else:
            results.append({'case': scenario['name'], 'status': 'passed'})
    if failures:
        error = AssertionError('scenario.' + failures[0][1][0])
        error.case = f'{label}:{failures[0][0]}'
        raise error
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(SCENARIOS)}


def main():
    from TESTS.adapters.django import staff_board_http_acceptance as driver
    module = sys.modules[__name__]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # The driver keeps only its receipt; a previous run's failure log is replaced.
    for item in OUTPUT.iterdir():
        if item.name not in ('results.json', 'failure.log'):
            raise SystemExit(f'remove {item} first')
        item.unlink()
    with patch.object(driver, 'family_runner', lambda family: module):
        return driver.main(['--family', FAMILY, '--output', str(OUTPUT)])


if __name__ == '__main__':
    raise SystemExit(main())
