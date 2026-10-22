"""Check shipped Go mutations and restored effects in the disposable production smoke.

The existing fixture login stays in a private file. This probe neither creates
accounts nor reads production configuration; the named smoke project is required.
Receipt files contain only synthetic fixture state and never the access token.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
from common import atomic_write, compose_command, compose_environment, read_env

SNAPSHOT = r"""
import json, sys
from django.db import connection
arguments = json.load(sys.stdin)
with connection.cursor() as cursor:
    cursor.execute('''
        SELECT u.user_id::text, u.tables_settings::text, u.first_name, u.last_name,
               u.username, u.email, u.is_active, u.two_fa, a.sport_association_id::text
          FROM bakney_user u
          JOIN application_sportassociation a ON a.user_id = u.user_id
         WHERE u.email = %s AND NOT u.deleted
    ''', [arguments['owner_email']])
    rows = cursor.fetchall()
    assert len(rows) == 1, 'Expected one synthetic association owner'
    row = rows[0]
    owner = dict(zip(('id', 'tables_settings', 'first_name', 'last_name', 'username',
                      'email', 'active', 'two_fa', 'association_id'), row))
    owner['tables_settings'] = json.loads(owner['tables_settings'])
    cursor.execute('''
        SELECT l.id, l.action, l.actor_id::text, l.actor_email, l.changes::text,
               l.object_repr, l.cid, l.additional_data::text, ct.model,
               i.sport_association_id::text, i.resolution_path
          FROM auditlog_logentry l
          JOIN django_content_type ct ON ct.id = l.content_type_id
          LEFT JOIN application_auditlogindex i ON i.log_entry_id = l.id
         WHERE l.object_pk = %s AND l.cid LIKE %s
         ORDER BY l.id
    ''', [owner['id'], 'go-production-probe:%'])
    audits = []
    for row in cursor.fetchall():
        audit = dict(zip(('id', 'action', 'actor_id', 'actor_email', 'changes',
                          'object_repr', 'cid', 'additional_data', 'model',
                          'association_id', 'resolution_path'), row))
        audit['changes'] = json.loads(audit['changes'])
        audit['additional_data'] = json.loads(audit['additional_data']) if audit['additional_data'] is not None else None
        audits.append(audit)
print('GO_PRODUCTION_SNAPSHOT=' + json.dumps({'owner': owner, 'audits': audits}, sort_keys=True))
"""


def payload(checkpoint):
    return {'delivery_probe': {'checkpoint': checkpoint, 'columns': ['nome', 'quota'],
                               'visible': True, 'exact_integer': 9007199254740993,
                               'label': 'Preferenze attività'}}


class Probe:
    def __init__(self, arguments):
        self.args = arguments
        values = read_env(arguments.env_file)
        if values.get('COMPOSE_PROJECT_NAME') != 'assozeta-production-smoke':
            raise ValueError('This probe requires the disposable production smoke project')
        location = urlsplit(arguments.base_url)
        if (location.scheme != 'http' or location.hostname not in ('localhost', '127.0.0.1') or
                location.path not in ('', '/') or location.query or location.fragment or location.username):
            raise ValueError('This probe requires the local production smoke HTTP origin')
        login = json.loads(arguments.login_json.read_text())
        self.token = login['access_token']
        if not isinstance(self.token, str) or not self.token:
            raise ValueError('The fixture login has no access token')
        self.base = arguments.base_url.rstrip('/')
        self.runtime()

    def runtime(self):
        command = compose_command(ROOT / 'selfhost', self.args.env_file)
        ids = subprocess.check_output(command + ['ps', '-q', 'api-go'],
                                      env=compose_environment(ROOT / 'selfhost', self.args.env_file),
                                      text=True, timeout=15).split()
        if len(ids) != 1:
            raise AssertionError('Expected exactly one shipped Go API container')
        state = subprocess.check_output(['docker', 'inspect', '--format',
            '{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{.State.Running}}|{{.State.Health.Status}}', ids[0]],
            text=True, timeout=15).strip()
        if state != '10001:10001|true|true|healthy':
            raise AssertionError('Go API is not running healthy with UID10001 and a read-only root filesystem')

    def http(self, method, path, body=None, cid=None):
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept-Language': 'en'}
        if cid:
            headers['X-Correlation-ID'] = cid
        content = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            content = json.dumps(body, ensure_ascii=False).encode()
        request = Request(self.base + path, data=content, method=method, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                status = response.status
                value = json.load(response)
        except HTTPError as exc:
            raise AssertionError(f'{method} {path} returned HTTP {exc.code}') from None
        if status != 200:
            raise AssertionError(f'{method} {path} did not return HTTP 200')
        return value

    def snapshot(self):
        command = compose_command(ROOT / 'selfhost', self.args.env_file)
        result = subprocess.run(command + ['exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c', SNAPSHOT],
                                env=compose_environment(ROOT / 'selfhost', self.args.env_file),
                                input=json.dumps({'owner_email': 'owner-smoke@example.invalid'}),
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError('Django PostgreSQL fixture readback failed')
        rows = [line.removeprefix('GO_PRODUCTION_SNAPSHOT=') for line in result.stdout.splitlines()
                if line.startswith('GO_PRODUCTION_SNAPSHOT=')]
        if len(rows) != 1:
            raise AssertionError('Django PostgreSQL fixture readback was incomplete')
        return json.loads(rows[0])

    def readback(self, expected):
        value = self.http('GET', '/api/v1/profile/settings')
        if value.get('settings', {}).get('table_settings') != expected:
            raise AssertionError('Django did not read the expected Go-written table preferences')

    def write(self):
        if self.args.receipt.exists():
            raise ValueError('Refusing to overwrite existing mutation evidence')
        expected = payload(self.args.checkpoint)
        # Initialize the existing lazy Django settings reader before measuring
        # the Go mutation, so its independent card-configuration creation is not
        # confused with the tested table-preference write.
        self.http('GET', '/api/v1/profile/settings')
        before = self.snapshot()
        if before['owner']['tables_settings'] == expected:
            raise AssertionError('The fixture already contains this checkpoint; mutation proof would be empty')
        cid = 'go-production-probe:' + str(uuid4())
        response = self.http('POST', '/api/v2/profile/settings/tables', {'tables_settings': expected}, cid)
        if response != {'status': 'success'}:
            raise AssertionError('Unexpected Go table-preference response')
        after = self.snapshot()
        old_ids = {audit['id'] for audit in before['audits']}
        added = [audit for audit in after['audits'] if audit['id'] not in old_ids]
        if len(added) != 1:
            raise AssertionError('Go mutation did not create exactly one indexed audit')
        event = added[0]
        owner = before['owner']
        expected_audit = {
            'id': event['id'], 'action': 1, 'actor_id': owner['id'], 'actor_email': owner['email'],
            'changes': {'tables_settings': [json.dumps(owner['tables_settings'], sort_keys=True),
                                             json.dumps(expected, sort_keys=True)]},
            'object_repr': 'name: ' + owner['first_name'] + ' ' + owner['last_name'],
            'cid': cid, 'additional_data': None, 'model': 'user',
            'association_id': owner['association_id'], 'resolution_path': 'user.sport_association',
        }
        if event != expected_audit:
            raise AssertionError('Go audit attribution, delta or association index differs from the contract')
        if after['owner'] != {**owner, 'tables_settings': expected}:
            raise AssertionError('Go mutated unexpected owner fields or stored different preferences')
        if [audit for audit in after['audits'] if audit['id'] in old_ids] != before['audits']:
            raise AssertionError('Go changed a previous probe audit')
        self.readback(expected)
        # Repeating the same write must preserve the preference and audit/index
        # count. This uses the shipped HTTP path and real PostgreSQL records.
        duplicate = self.http('POST', '/api/v2/profile/settings/tables', {'tables_settings': expected}, cid)
        if duplicate != {'status': 'success'} or self.snapshot() != after:
            raise AssertionError('Repeated Go preferences changed durable state or duplicated an audit')
        receipt = {'schema_version': 1, 'checkpoint': self.args.checkpoint, 'snapshot': after,
                   'proof': {'go_http_mutation': True, 'django_http_readback': True,
                             'postgres_audit_and_index': True, 'repeat_without_duplicate_audit': True}}
        atomic_write(self.args.receipt, json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(f'PASS: Go {self.args.checkpoint} mutation, Django readback, PostgreSQL audit/index and repeat.')

    def verify(self):
        receipt = json.loads(self.args.receipt.read_text())
        checkpoint = receipt.get('checkpoint')
        if receipt.get('schema_version') != 1 or checkpoint not in ('before-backup', 'after-backup'):
            raise ValueError('Verification requires a saved mutation receipt')
        expected = payload(checkpoint)
        if receipt['snapshot']['owner']['tables_settings'] != expected:
            raise ValueError('Mutation receipt contains a different checkpoint')
        self.readback(expected)
        if self.snapshot() != receipt['snapshot']:
            raise AssertionError('Restore did not recover the exact Go preference and audit/index state')
        print(f'PASS: Go {checkpoint} preference and audit/index exactly match the saved receipt.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('write', 'verify'))
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--login-json', type=Path, required=True)
    parser.add_argument('--base-url', default='http://localhost:58080')
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--checkpoint', choices=('before-backup', 'after-backup'))
    arguments = parser.parse_args()
    if arguments.mode == 'write' and arguments.checkpoint is None:
        parser.error('write requires --checkpoint')
    probe = Probe(arguments)
    getattr(probe, arguments.mode)()


if __name__ == '__main__':
    try:
        main()
    except (AssertionError, ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        # Never dump credential-bearing response bodies, environment, or subprocess output.
        print(f'Go production probe failed: {exc.__class__.__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1) from None
