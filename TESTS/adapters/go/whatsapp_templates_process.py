"""whatsapp_templates management command parity: `manage.py whatsapp_templates` vs BE_GO/cmd/whatsapp-templates.

Django (BE/communications/management/commands/whatsapp_templates.py and
whatsapp_provider.CloudAPI): subcommands list | register | get NAME LANGUAGE
(argparse: a missing or unknown action, or missing get arguments, exit 2).
list pages GET <base>/<version>/<WABA>/message_templates with
status=["APPROVED"] and fields=id,name,language,status,components (then
after=<cursor> while paging.next is set; a missing/repeated cursor or a page
without a data list is 'invalid_response') and keeps the APPROVED items; get
returns the first listed item with that name and language (else 'not_found:
Approved template not found'); register json.load()s stdin, requires an
object (else 'Expected a JSON template object on stdin') and POSTs it to the
same edge. The result is printed as json.dumps(result, ensure_ascii=False)
plus a newline; a ProviderError is 'CommandError: <code>: <message>' on
stderr with exit 1. Configuration is effective_integration('whatsapp'): the
self-hosted instance settings when present (the enabled flag is not
consulted), else the WHATSAPP_* settings; a missing WABA id or access token is
the 'configuration' error before any request.

The run reuses the HTTP family driver for its private PostgreSQL database and
migrated schema (the Go server it starts is not used). Django runs in-process
through BaseCommand.run_from_argv with captured stdio; Go is the built
cmd/whatsapp-templates binary given the server's database environment (so it
reads the same instance configuration). Both talk to a private Meta stub
(WHATSAPP_API_BASE_URL) and must produce the authored exit code, stdout,
stderr error line and provider requests.
"""
from contextlib import contextmanager, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.go.whatsapp_meta_stub import MetaStub, json_reply, raw_reply  # noqa: E402

SOURCES = ('TESTS/adapters/go/whatsapp_templates_process.py', 'TESTS/adapters/go/whatsapp_meta_stub.py',
           'BE/communications/management/commands/whatsapp_templates.py', 'BE/communications/whatsapp_provider.py',
           'BE/instance/integration_configuration.py', 'BE_GO/cmd/whatsapp-templates/main.go', 'BE_GO/internal/whatsapp/provider.go',
           'BE_GO/internal/config/whatsapp.go', 'BE_GO/internal/service/instance_admin.go', 'TESTS/adapters/django/family_harness.py')
FAMILY = 'whatsapp-templates'
OUTPUT = ROOT / 'quality-reports/go-migration/whatsapp-templates-process'
PHONE, WABA, TOKEN = '1098765432109', '2109876543210', 'synthetic-whatsapp-access-token'
ENV_WABA, ENV_TOKEN = '3210987654321', 'synthetic-environment-access-token'
STATE = {}
LIST_QUERY = {'status': ['["APPROVED"]'], 'fields': ['id,name,language,status,components']}

APPROVED_IT = {'name': 'iscrizione_accettata', 'components': [{'type': 'BODY', 'text': 'Ciao {{1}}, iscrizione è confermata'}],
               'language': 'it', 'status': 'APPROVED', 'id': '1001'}
PENDING = {'name': 'bozza', 'language': 'it', 'status': 'PENDING', 'id': '1002', 'components': []}
APPROVED_EN = {'id': '1003', 'status': 'APPROVED', 'name': 'promemoria', 'language': 'en_US',
               'components': [{'type': 'BODY', 'text': 'Reminder <b>&</b> {{1}}'}]}
REJECTED = {'id': '1004', 'status': 'REJECTED', 'name': 'promemoria', 'language': 'it', 'components': []}
APPROVED_DUP = {'id': '1005', 'status': 'APPROVED', 'name': 'promemoria', 'language': 'en_US', 'components': []}


def page(items, after=None, more=True):
    paging = {'cursors': {'before': 'BEFORE', 'after': after or 'LAST'}}
    if more:
        paging['next'] = f'https://graph.facebook.com/v23.0/{WABA}/message_templates?after={after}'
    return json_reply(200, {'data': items, 'paging': paging})


PAGES = {'list:': page([APPROVED_IT, PENDING], 'CURSOR2'), 'list:CURSOR2': page([APPROVED_EN, REJECTED, APPROVED_DUP], more=False)}


def dumps(value):
    return json.dumps(value, ensure_ascii=False) + '\n'


def get(after=None, waba=WABA, token=TOKEN):
    query = dict(LIST_QUERY, **({'after': [after]} if after else {}))
    return {'method': 'GET', 'path': f'/v23.0/{waba}/message_templates', 'query': query, 'authorization': f'Bearer {token}', 'body': ''}


def post(definition):
    return {'method': 'POST', 'path': f'/v23.0/{WABA}/message_templates', 'query': {}, 'authorization': f'Bearer {TOKEN}',
            'body': definition}


DEFINITION = {'name': 'conferma_pagamento', 'language': 'it', 'category': 'UTILITY',
              'components': [{'type': 'BODY', 'text': 'Pagamento di {{1}} ricevuto ✓', 'example': {'body_text': [['10 €']]}}]}
REGISTERED = {'id': '594425479261596', 'status': 'PENDING', 'category': 'UTILITY'}
INSTANCE = {'enabled': True, 'business_account_id': WABA, 'access_token': TOKEN}


def scenario(name, argv, *, code=0, stdout='', error=None, requests=(), replies=None, stdin='', config=INSTANCE):
    """error: the exact last stderr line (exit 1), or ('argparse', message) for an exit-2 usage error ending 'error: <message>'."""
    return {'name': name, 'argv': argv, 'code': code, 'stdout': stdout, 'error': error, 'requests': list(requests),
            'replies': replies or {}, 'stdin': stdin, 'config': config}


SCENARIOS = [
    scenario('list keeps approved templates across pages', ['list'], stdout=dumps([APPROVED_IT, APPROVED_EN, APPROVED_DUP]),
             requests=[get(), get('CURSOR2')], replies=PAGES),
    scenario('get returns the first matching approved template', ['get', 'promemoria', 'en_US'], stdout=dumps(APPROVED_EN),
             requests=[get(), get('CURSOR2')], replies=PAGES),
    scenario('get of a template that is not approved', ['get', 'promemoria', 'it'], code=1,
             error='CommandError: not_found: Approved template not found', requests=[get(), get('CURSOR2')], replies=PAGES),
    scenario('register posts the stdin definition', ['register'], stdin=json.dumps(DEFINITION), stdout=dumps(REGISTERED),
             requests=[post(DEFINITION)], replies={'register': json_reply(200, REGISTERED)}),
    scenario('register refuses a JSON list', ['register'], stdin='[{"name": "x"}]', code=1,
             error='CommandError: Expected a JSON template object on stdin'),
    scenario('register refuses invalid JSON', ['register'], stdin='{"name": ', code=1,
             error='CommandError: Expected a JSON template object on stdin'),
    scenario('register refuses trailing data after the object', ['register'], stdin='{"name": "x"} {"name": "y"}', code=1,
             error='CommandError: Expected a JSON template object on stdin'),
    scenario('register provider error', ['register'], stdin=json.dumps(DEFINITION), code=1,
             error='CommandError: 100: Invalid parameter', requests=[post(DEFINITION)],
             replies={'register': json_reply(400, {'error': {'message': 'Invalid parameter', 'type': 'OAuthException', 'code': 100}})}),
    scenario('list provider error redacts the token', ['list'], code=1, error='CommandError: 190: Token [redacted] expired',
             requests=[get()], replies={'list:': json_reply(401, {'error': {'message': f'Token {TOKEN} expired', 'code': 190}})}),
    scenario('list page without data', ['list'], code=1, error='CommandError: invalid_response: Missing WhatsApp template list',
             requests=[get()], replies={'list:': json_reply(200, {'paging': {}})}),
    scenario('list repeated cursor', ['list'], code=1, error='CommandError: invalid_response: Invalid WhatsApp template cursor',
             requests=[get(), get('CURSOR2')],
             replies={'list:': page([APPROVED_IT], 'CURSOR2'), 'list:CURSOR2': page([APPROVED_EN], 'CURSOR2')}),
    scenario('list non-JSON answer', ['list'], code=1, error='CommandError: invalid_response: Invalid WhatsApp API response',
             requests=[get()], replies={'list:': raw_reply(200, 'ok', 'text/plain')}),
    scenario('missing access token', ['list'], code=1, error='CommandError: configuration: Missing WhatsApp resource ID or access token',
             config={'enabled': True, 'business_account_id': WABA, 'access_token': ''}),
    scenario('disabled integration still lists', ['list'], stdout=dumps([APPROVED_IT, APPROVED_EN, APPROVED_DUP]),
             requests=[get(), get('CURSOR2')], replies=PAGES, config={**INSTANCE, 'enabled': False}),
    scenario('environment configuration without instance settings', ['list'], stdout=dumps([APPROVED_IT]),
             requests=[get(waba=ENV_WABA, token=ENV_TOKEN)], replies={'list:': page([APPROVED_IT, PENDING], more=False)}, config=None),
    scenario('no action', [], code=2, error=('argparse', 'the following arguments are required: action')),
    scenario('get without language', ['get', 'promemoria'], code=2,
             error=('argparse', 'the following arguments are required: language')),
    scenario('unknown action', ['delete'], code=2,
             error=('argparse', "argument action: invalid choice: 'delete' (choose from 'list', 'register', 'get')")),
]
EXPECTED = [item['name'] for item in SCENARIOS]


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def settings_for(config):
    from instance.integration_configuration import encrypt_secret
    plain = {'enabled': config['enabled'], 'phone_number_id': PHONE, 'business_account_id': config['business_account_id'],
             'access_token': encrypt_secret(config['access_token']), 'app_secret': encrypt_secret('synthetic-app-secret'),
             'webhook_verify_token': encrypt_secret('synthetic-verify-token'), 'notification_routes': '{}'}
    return {'whatsapp': {'revision': 1, 'settings': plain}}


class Spec:
    require = staticmethod(require)

    @staticmethod
    def load_source():
        return {'cases': []}


def seed_rows(_source, created):
    from instance.models import InstanceConfiguration
    require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    row = InstanceConfiguration(domain='whatsapp-templates.example.test', name='WhatsApp templates fixture', self_hosted=True,
                                integration_settings=settings_for(INSTANCE))
    InstanceConfiguration._base_manager.bulk_create([row])
    created.append((InstanceConfiguration, row.pk))


def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from TESTS.adapters.django import family_harness as harness
    return harness.fixture_context_for(Spec, seed_rows)(apps, private_work, raw, cleanup_receipt=cleanup_receipt)


@contextmanager
def service_context(report, log):
    with MetaStub() as stub, tempfile.TemporaryDirectory(prefix='az-whatsapp-templates-', dir='/tmp') as directory:
        binary = Path(directory) / 'whatsapp-templates'
        subprocess.run(['go', 'build', '-o', str(binary), './cmd/whatsapp-templates'], cwd=ROOT / 'BE_GO', check=True,
                       timeout=300, stdout=log, stderr=subprocess.STDOUT)
        STATE.update(stub=stub, binary=binary)
        report['whatsapp_stub'] = 'loopback'
        yield


@contextmanager
def configured(item):
    from instance.models import InstanceConfiguration
    row = InstanceConfiguration._base_manager.get()
    original = (row.self_hosted, row.integration_settings)
    if item['config'] is None:
        InstanceConfiguration._base_manager.filter(pk=row.pk).update(self_hosted=False)
    else:
        InstanceConfiguration._base_manager.filter(pk=row.pk).update(integration_settings=settings_for(item['config']))
    STATE['stub'].reset(item['replies'])
    try:
        yield
    finally:
        InstanceConfiguration._base_manager.filter(pk=row.pk).update(self_hosted=original[0], integration_settings=original[1])


def environment_values():
    """The WHATSAPP_* settings both backends fall back to without instance settings."""
    return {'WHATSAPP_API_BASE_URL': STATE['stub'].base_url, 'WHATSAPP_API_VERSION': 'v23.0', 'WHATSAPP_PHONE_NUMBER_ID': PHONE,
            'WHATSAPP_BUSINESS_ACCOUNT_ID': ENV_WABA, 'WHATSAPP_ACCESS_TOKEN': ENV_TOKEN}


def run_django(item):
    from django.core.management import load_command_class
    from django.test.utils import override_settings
    out, err = io.StringIO(), io.StringIO()
    command = type(load_command_class('communications', 'whatsapp_templates'))(stdout=out, stderr=err)
    code = 0
    with override_settings(**environment_values()), redirect_stderr(err), patch.object(sys, 'stdin', io.StringIO(item['stdin'])):
        try:
            command.run_from_argv(['manage.py', 'whatsapp_templates', *item['argv']])
        except SystemExit as exit_:
            code = exit_.code if isinstance(exit_.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def run_go(item):
    # The private process environment Django was configured from (database, secret key, Redis), like the Go server's.
    environment = {**os.environ, **environment_values()}
    result = subprocess.run([str(STATE['binary']), *item['argv']], input=item['stdin'].encode(), capture_output=True,
                            env=environment, timeout=60)
    return result.returncode, result.stdout.decode('utf-8', 'replace'), result.stderr.decode('utf-8', 'replace')


def verify(item, code, stdout, stderr, requests):
    problems = []
    if code != item['code']:
        problems.append(f'exit={code}')
    if stdout != item['stdout']:
        problems.append(f'stdout={stdout[:200]!r}')
    lines = stderr.strip().splitlines()
    last = lines[-1] if lines else ''
    if isinstance(item['error'], tuple):
        if not last.endswith('error: ' + item['error'][1]):
            problems.append(f'stderr={last[:200]!r}')
    elif item['error'] is not None and last != item['error']:
        problems.append(f'stderr={last[:200]!r}')
    actual = []
    for record in requests:
        body = record['body']
        if record['method'] == 'POST':
            try:
                body = json.loads(body)
            except ValueError:
                pass
            if not (record['content_type'] or '').startswith('application/json'):
                problems.append('request.content-type')
        actual.append({key: record[key] for key in ('method', 'path', 'query', 'authorization')} | {'body': body})
    if actual != item['requests']:
        problems.append(f'requests: expected {len(item["requests"])}, got {len(actual)}'
                        + next((f'; first difference at {i}' for i, (a, b) in enumerate(zip(actual, item['requests'])) if a != b), ''))
    return problems


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    results, failures = [], []
    for item in SCENARIOS:
        try:
            with configured(item):
                code, stdout, stderr = (run_django if backend == 'django' else run_go)(item)
                problems = verify(item, code, stdout, stderr, STATE['stub'].taken())
        except Exception as error:
            traceback.print_exc()
            problems = [f'exception {type(error).__name__}']
        if problems:
            print(f'SCENARIO-FAIL {label}:{item["name"]}: ' + '; '.join(problems), flush=True)
            failures.append((item['name'], problems))
        else:
            results.append({'case': item['name'], 'status': 'passed'})
    if failures:
        error = AssertionError('scenario.' + failures[0][1][0][:80])
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
