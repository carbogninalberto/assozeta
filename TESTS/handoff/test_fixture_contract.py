"""Phase 0 fixture contract: three sanitized, reproducible starting installations.

CONTRACT (t-p0; fixtures and loader by i-p0)

  TESTS/handoff/fixtures/manifest.json
    {"format": 1, "fixtures": {"fresh" | "django-default" | "legacy-mixed": {
        "description": str,
        "revision": <40-hex commit whose distribution produced the state>,
        "captured_at": <ISO-8601 UTC; the fixed clock for ETAs and due times>,
        "generator": <command that regenerates the fixture reproducibly>,
        "files": {"env" | "database" | "broker" | "browser": <path relative to fixtures/> or null},
        "sha256": {<path>: <hex digest>} for every non-null file,
        "observed": {"running_services": [<Compose service>...]}   old behavior at `revision`}}}
  TESTS/handoff/fixtures/__init__.py
    load(fixture_id) -> object with
      manifest  dict, the fixture's manifest entry
      revision  str
      env       dict[str, str] or None (fresh: None, `configure` creates it)
      database  dict[table, list[row dict]] or None   e.g. instance_instanceconfiguration,
                django_celery_beat_periodictask, assozeta_go_jobs, communications_whatsappdelivery
      broker    dict[queue, list[envelope]]           Kombu/Celery protocol-2 JSON messages:
                {"body": str, "content-type": "application/json", "content-encoding": "utf-8",
                 "headers": {"task", "id", "eta", "retries", "root_id", "parent_id", ...},
                 "properties": {"delivery_info": {"exchange": "", "routing_key": <queue>}, "priority", ...}}
      browser   list[{"name": str, "localStorage": dict, "sessionStorage": dict}]

Sanitized: no real credentials, keys, tokens, personal data or private hosts. Secret-like
env values are empty, "fixture-..." or the public stock default of selfhost/.env.example.
"""
from datetime import datetime
import importlib
import json
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import uuid

from TESTS.handoff.topology import Installation

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'TESTS/handoff/fixtures'
MANIFEST = FIXTURES / 'manifest.json'
REQUIRED = ('fresh', 'django-default', 'legacy-mixed')
FIELDS = ('description', 'revision', 'captured_at', 'generator', 'files', 'sha256', 'observed')
SECRET_NAME = re.compile(r'PASSWORD|SECRET|TOKEN|PRIVATE|_KEY_B64|API_KEY|STRIPE_KEY|ACCESS_KEY')
SECRET_PATTERNS = {
    'PEM key': re.compile(r'-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)'),
    'AWS key': re.compile(r'\bA(KIA|SIA)[0-9A-Z]{16}\b'),
    'Stripe key': re.compile(r'\b(sk|rk|pk)_(live|test)_[0-9A-Za-z]{8,}'),
    'GitHub token': re.compile(r'\b(ghp|gho|ghs|ghu)_[0-9A-Za-z]{20,}|github_pat_'),
    'Slack token': re.compile(r'\bxox[abprs]-'),
    'JWT': re.compile(r'\beyJ[\w-]{10,}\.[\w-]{10,}\.'),
}
SAFE_DOMAINS = re.compile(r'(^|\.)(example\.(com|org|net)|invalid|test|example|localhost)$')
OVERRIDE_KEY = 'assozeta_api_override'          # UI/src/utils/apiVersion.js
CONFIG_CACHE_KEY = 'assozeta_instance_config'   # UI/src/store/instanceStore.js


def stock_env():
    values = {}
    for line in (ROOT / 'selfhost/.env.example').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value
    return values


def public_hosts():
    text = (ROOT / 'selfhost/.env.example').read_text() + (ROOT / 'selfhost/bin/assozeta').read_text()
    return set(re.findall(r'https?://([A-Za-z0-9.-]+)', text))


def go_task_names(revision):
    source = subprocess.run(['git', '-C', str(ROOT), 'show', f'{revision}:selfhost/updater/go_tasks.py'],
                            capture_output=True, text=True, check=True).stdout
    match = re.search(r'GO_TASK_NAMES = \((.*?)\)', source, re.S)
    return set(re.findall(r"'([^']+)'", match.group(1)))


def configured_keys(revision):
    with tempfile.TemporaryDirectory(prefix='assozeta-handoff-env-') as directory:
        installation = Installation(directory, revision=revision)
        installation.cli('configure', '--domain', 'localhost', '--version', 'test')
        return set(installation.env())


def simulated_services(revision, env):
    with tempfile.TemporaryDirectory(prefix='assozeta-handoff-observed-') as directory:
        installation = Installation(directory, revision=revision)
        installation.cli('configure', '--domain', 'localhost', '--version', 'test')
        installation.env_file.write_text(''.join(f'{key}={value}\n' for key, value in env.items()))
        installation.cli('start')
        return sorted(installation.running())


def api_preference(client):
    override = (client.get('localStorage') or {}).get(OVERRIDE_KEY) or \
        (client.get('sessionStorage') or {}).get(OVERRIDE_KEY)
    if override:
        return override
    cached = (client.get('localStorage') or {}).get(CONFIG_CACHE_KEY)
    if cached:
        return ((json.loads(cached).get('features') or {}).get('uiApiVersion')) or 'v1'
    return None


class FixtureContract(unittest.TestCase):
    maxDiff = None

    def manifest(self):
        if not MANIFEST.is_file():
            self.fail(f'fixture manifest missing: {MANIFEST.relative_to(ROOT)}')
        return json.loads(MANIFEST.read_text())

    def load(self, name):
        try:
            module = importlib.import_module('TESTS.handoff.fixtures')
        except ModuleNotFoundError as exc:
            self.fail(f'fixture loader missing: TESTS/handoff/fixtures/__init__.py ({exc})')
        self.assertTrue(callable(getattr(module, 'load', None)), 'TESTS.handoff.fixtures.load is not defined')
        return module.load(name)

    def test_manifest_lists_the_three_fixtures_with_required_fields(self):
        document = self.manifest()
        self.assertEqual(document.get('format'), 1)
        self.assertEqual(sorted(set(REQUIRED) - set(document['fixtures'])), [])
        for name in REQUIRED:
            entry = document['fixtures'][name]
            with self.subTest(name):
                self.assertEqual(sorted(set(FIELDS) - set(entry)), [])
                self.assertRegex(entry['revision'], r'^[0-9a-f]{40}$')
                reachable = subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', entry['revision'] + '^{commit}'],
                                           capture_output=True)
                self.assertEqual(reachable.returncode, 0, f'{name}: revision not in this clone')
                self.assertRegex(entry['captured_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$')
                self.assertTrue(entry['generator'].strip())
                self.assertEqual(sorted(entry['files']), ['broker', 'browser', 'database', 'env'])
                self.assertTrue(entry['observed']['running_services'])

    def test_files_match_recorded_digests(self):
        for name, entry in self.manifest()['fixtures'].items():
            for kind, relative in entry['files'].items():
                if relative is None:
                    continue
                with self.subTest(fixture=name, file=relative):
                    path = FIXTURES / relative
                    self.assertTrue(path.resolve().is_relative_to(FIXTURES.resolve()) and path.is_file(), relative)
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'].get(relative))

    def test_fixture_files_are_sanitized(self):
        self.manifest()
        stock, hosts = stock_env(), public_hosts()
        for path in sorted(p for p in FIXTURES.rglob('*') if p.is_file() and '__pycache__' not in p.parts):
            text = path.read_text(errors='replace')
            relative = path.relative_to(ROOT)
            for label, pattern in SECRET_PATTERNS.items():
                with self.subTest(file=str(relative), check=label):
                    self.assertIsNone(pattern.search(text), f'{label} in {relative}')
            for email_domain in re.findall(r'[\w.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})', text):
                with self.subTest(file=str(relative), email=email_domain):
                    self.assertRegex(email_domain.lower(), SAFE_DOMAINS)
            for host in re.findall(r'https?://([A-Za-z0-9.-]+)', text):
                with self.subTest(file=str(relative), host=host):
                    self.assertTrue(SAFE_DOMAINS.search(host.lower()) or host in hosts or '.' not in host
                                    or host.startswith('127.'), f'non-fixture host {host} in {relative}')
        for name in REQUIRED:
            env = self.load(name).env or {}
            for key, value in env.items():
                if SECRET_NAME.search(key):
                    with self.subTest(fixture=name, key=key):
                        self.assertTrue(value == '' or value.startswith('fixture-') or
                                        (value == stock.get(key) and value != 'replace-me'),
                                        f'{name}: {key} is not a fixture placeholder')

    def test_loader_returns_typed_state(self):
        for name in REQUIRED:
            with self.subTest(name):
                fixture = self.load(name)
                self.assertEqual(fixture.manifest, self.manifest()['fixtures'][name])
                self.assertEqual(fixture.revision, fixture.manifest['revision'])
                self.assertTrue(fixture.env is None or all(isinstance(v, str) for v in fixture.env.values()))
                self.assertTrue(fixture.database is None or all(
                    isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
                    for rows in fixture.database.values()))
                self.assertIsInstance(fixture.broker, dict)
                self.assertIsInstance(fixture.browser, list)
                self.assertTrue(fixture.browser, 'each fixture needs at least one browser client')
                known = {row['name'] for row in json.loads(
                    (ROOT / 'docs/architecture/go-migration/go-tasks-inventory.json').read_text())}
                for queue, envelopes in fixture.broker.items():
                    for envelope in envelopes:
                        headers = envelope['headers']
                        self.assertIn(headers['task'], known)
                        uuid.UUID(headers['id'])
                        self.assertIn('eta', headers)
                        self.assertEqual(envelope['content-type'], 'application/json')
                        self.assertIsInstance(envelope['body'], str)
                        self.assertEqual(envelope['properties']['delivery_info']['routing_key'], queue)

    def test_fresh_fixture_is_an_empty_host_and_a_cold_browser(self):
        fixture = self.load('fresh')
        self.assertIsNone(fixture.env)
        self.assertIsNone(fixture.database)
        self.assertEqual({queue: items for queue, items in fixture.broker.items() if items}, {})
        for client in fixture.browser:
            self.assertEqual((client.get('localStorage'), client.get('sessionStorage')), ({}, {}), client['name'])

    def test_django_default_fixture_is_an_existing_default_installation(self):
        fixture = self.load('django-default')
        env = fixture.env
        self.assertEqual(sorted(configured_keys(fixture.revision) ^ set(env)), [],
                         'env keys differ from the configuration written by that revision')
        self.assertIn(env.get('ASSOZETA_GO_TASKS'), ('', '0'))
        self.assertEqual(env.get('GO_TASK_QUEUE_TASKS'), '')
        self.assertEqual(env.get('ASSOZETA_REALTIME_RELAY_BACKEND'), 'django')
        self.assertEqual(env.get('ASSOZETA_BAKNEY_PROTOCOL_BACKEND'), 'django')
        configuration, = fixture.database['instance_instanceconfiguration']
        self.assertEqual((configuration['ui_api_version'], configuration['go_tasks_enabled']), ('v1', False))
        schedules = fixture.database['django_celery_beat_periodictask']
        self.assertTrue(any(row['enabled'] is False for row in schedules), 'no disabled schedule')
        declared = set(re.findall(r"^    '([^']+)': \{", subprocess.run(
            ['git', '-C', str(ROOT), 'show', f'{fixture.revision}:BE/core/celery.py'],
            capture_output=True, text=True, check=True).stdout, re.M))
        self.assertTrue(any(row['name'] not in declared for row in schedules), 'no custom schedule')
        self.assertTrue(fixture.broker.get('celery'), 'no pending work on the celery queue')
        self.assertEqual(fixture.broker.get('assozeta-go', []), [])
        self.assertTrue(any(api_preference(client) in (None, 'v1') for client in fixture.browser))
        self.assertEqual(sorted(fixture.manifest['observed']['running_services']),
                         simulated_services(fixture.revision, env))

    def test_legacy_mixed_fixture_has_v2_browsers_go_email_routing_and_work_on_both_queues(self):
        fixture = self.load('legacy-mixed')
        env = fixture.env
        self.assertEqual(sorted(configured_keys(fixture.revision) - set(env)), [])
        self.assertEqual(env.get('ASSOZETA_GO_TASKS'), '1')
        routed = {name for name in env.get('GO_TASK_QUEUE_TASKS', '').split(',') if name}
        self.assertEqual(routed, go_task_names(fixture.revision))
        configuration, = fixture.database['instance_instanceconfiguration']
        self.assertEqual((configuration['ui_api_version'], configuration['go_tasks_enabled']), ('v2', True))
        self.assertTrue(any(row['state'] == 'pending' for row in fixture.database['assozeta_go_jobs']))
        self.assertTrue(any(row['status'] == 'queued' for row in fixture.database['communications_whatsappdelivery']))
        self.assertTrue(fixture.broker.get('celery'), 'no pending work on the celery queue')
        self.assertTrue(fixture.broker.get('assozeta-go'), 'no pending work on the assozeta-go queue')
        self.assertLessEqual({envelope['headers']['task'] for envelope in fixture.broker['assozeta-go']},
                             go_task_names(fixture.revision))
        def instant(value):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        captured = instant(fixture.manifest['captured_at'])
        etas = [instant(envelope['headers']['eta']) for queue in fixture.broker.values() for envelope in queue
                if envelope['headers'].get('eta')]
        self.assertTrue(any(eta > captured for eta in etas), 'no future ETA envelope')
        preferences = [api_preference(client) for client in fixture.browser]
        self.assertIn('v2', preferences)
        self.assertTrue(any(preference in (None, 'v1') for preference in preferences))
        self.assertEqual(sorted(fixture.manifest['observed']['running_services']),
                         simulated_services(fixture.revision, env))


if __name__ == '__main__':
    unittest.main()
