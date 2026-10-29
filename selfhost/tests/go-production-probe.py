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
from uuid import UUID, uuid4

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


CATEGORY_SNAPSHOT = r"""
import json, sys
from django.db import connection
from application.models import (PaymentCategory, VatManagement, Payment, CampsAndRetreats,
    CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService)
arguments = json.load(sys.stdin)
state = {}
with connection.cursor() as cursor:
    for key, model in (('categories', PaymentCategory), ('vat', VatManagement), ('payments', Payment),
                       ('camps', CampsAndRetreats), ('periods', CampsAndRetreatsPeriod),
                       ('services', CampsAndRetreatsPeriodsService)):
        cursor.execute(f'SELECT row_to_json(t)::text FROM "{model._meta.db_table}" t ORDER BY "{model._meta.pk.column}"')
        state[key] = [json.loads(row[0]) for row in cursor.fetchall()]
    cursor.execute('''SELECT row_to_json(l)::text, ct.model FROM auditlog_logentry l
        JOIN django_content_type ct ON ct.id=l.content_type_id
        WHERE ct.app_label='application' AND ct.model IN ('paymentcategory','vatmanagement') ORDER BY l.id''')
    state['audits'] = [{**json.loads(row), 'model': model} for row, model in cursor.fetchall()]
    cursor.execute('''SELECT row_to_json(i)::text FROM application_auditlogindex i
        JOIN auditlog_logentry l ON l.id=i.log_entry_id JOIN django_content_type ct ON ct.id=l.content_type_id
        WHERE ct.app_label='application' AND ct.model IN ('paymentcategory','vatmanagement') ORDER BY i.audit_log_index_id''')
    state['indexes'] = [json.loads(row[0]) for row in cursor.fetchall()]
    cursor.execute('''SELECT json_build_object('user_id',user_id::text,
        'default_payment_category_id',default_payment_category_id::text,
        'default_payment_category_courses_id',default_payment_category_courses_id::text)::text
        FROM bakney_user WHERE email=%s AND NOT deleted''', [arguments['owner_email']])
    owners = cursor.fetchall()
    assert len(owners) == 1, 'Expected one synthetic category owner'
    state['owner_references'] = json.loads(owners[0][0])
print('CATEGORY_STATE=' + json.dumps(state, sort_keys=True))
"""

CATEGORY_REFERENCES = r"""
import json, sys
from datetime import datetime, timezone
from auditlog.context import disable_auditlog
from django.db import transaction
from application.models import (User, SportAssociation, PaymentCategory, Payment,
    CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService)
arguments = json.load(sys.stdin)
with transaction.atomic(), disable_auditlog():
    owner = User.original_objects.get(pk=arguments['owner_id'], email='owner-smoke@example.invalid')
    association = SportAssociation.original_objects.get(pk=arguments['association_id'], user=owner)
    category = PaymentCategory.objects.get(pk=arguments['category_id'], sport_association=association, deleted=False)
    payment = Payment._base_manager.create(sport_association=association, payment_category=category,
        amount='17.25', description='Installed category reference fixture',
        creation_date=datetime(2026, 9, 1, tzinfo=timezone.utc))
    camp = CampsAndRetreats.objects.create(sport_association=association, title='Installed category fixture camp')
    period = CampsAndRetreatsPeriod.objects.create(camps_and_retreats=camp, title='Fixture period',
        start_date=datetime(2040, 1, 1, tzinfo=timezone.utc), end_date=datetime(2040, 1, 2, tzinfo=timezone.utc))
    service = CampsAndRetreatsPeriodsService.objects.create(camps_and_retreats_period=period,
        title='Installed category reference service', payment_category=category)
    User.original_objects.filter(pk=owner.pk).update(default_payment_category=category,
        default_payment_category_courses=category)
print('CATEGORY_STATE=' + json.dumps({'payment_id': str(payment.pk), 'camp_id': str(camp.pk),
    'period_id': str(period.pk), 'service_id': str(service.pk)}))
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

    def category_shell(self, program, arguments):
        command = compose_command(ROOT / 'selfhost', self.args.env_file)
        result = subprocess.run(command + ['exec', '-T', 'api', 'python', 'manage.py', 'shell', '-c', program],
            env=compose_environment(ROOT / 'selfhost', self.args.env_file), input=json.dumps(arguments),
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError('Installed category PostgreSQL fixture operation failed')
        values = [line.removeprefix('CATEGORY_STATE=') for line in result.stdout.splitlines()
                  if line.startswith('CATEGORY_STATE=')]
        if len(values) != 1:
            raise AssertionError('Installed category PostgreSQL receipt was incomplete')
        return json.loads(values[0])

    def category_snapshot(self):
        return self.category_shell(CATEGORY_SNAPSHOT, {'owner_email': 'owner-smoke@example.invalid'})

    def category_readback(self):
        # The shipped Caddy configuration binds these fixed versions to separate
        # packaged runtimes; the compatibility alias remains Django.
        django = self.http('GET', '/api/v1/payment/category/list')
        golang = self.http('GET', '/api/v2/payment/category/list')
        if django.keys() != golang.keys() or django.keys() != {'data'}:
            raise AssertionError('Unexpected installed category list envelope')
        ordered = lambda value: sorted(value['data'], key=lambda row: row['payment_category_id'])
        if ordered(django) != ordered(golang):
            raise AssertionError('Installed Django/Go category readback differs')
        return ordered(django)

    def assert_category_mutation(self, before, after, expected, vat, changes, cid):
        """Exact managed-table delta, with complete category/VAT audit changes."""
        category_id = expected['payment_category_id']
        row = {**expected, 'sport_association_id': expected['sport_association'],
               'vat_management_id': expected['vat_management']['vat_management_id'],
               'creation_date': expected['creation_date'].replace('Z', '+00:00')}
        del row['sport_association'], row['vat_management']
        categories = [item for item in before['categories'] if item['payment_category_id'] != category_id] + [row]
        if after['categories'] != sorted(categories, key=lambda item: item['payment_category_id']):
            raise AssertionError('Installed category mutation changed unexpected category state')
        vats = before['vat'] + ([vat] if vat else [])
        if after['vat'] != sorted(vats, key=lambda item: item['vat_management_id']):
            raise AssertionError('Installed category mutation changed unexpected VAT state')
        for key in ('payments', 'camps', 'periods', 'services', 'owner_references'):
            if before[key] != after[key]:
                raise AssertionError('Category mutation changed an existing reference: ' + key)
        old_audits = {event['id'] for event in before['audits']}
        if [event for event in after['audits'] if event['id'] in old_audits] != before['audits']:
            raise AssertionError('Category mutation changed prior audit records')
        added = [event for event in after['audits'] if event['id'] not in old_audits]
        if len(added) != len(changes):
            raise AssertionError('Unexpected total category/VAT audit count')
        owner = self.snapshot()['owner']
        old_indexes = {index['audit_log_index_id'] for index in before['indexes']}
        if [index for index in after['indexes'] if index['audit_log_index_id'] in old_indexes] != before['indexes']:
            raise AssertionError('Category mutation changed a prior audit index')
        added_indexes = [index for index in after['indexes'] if index['audit_log_index_id'] not in old_indexes]
        if len(added_indexes) != len(added):
            raise AssertionError('Category/VAT mutation did not create exactly one owner index per audit')
        for event, (model, action, object_id, delta) in zip(added, changes):
            if (event['model'], event['action'], event['object_pk'], event['changes'], event['actor_id'],
                event['actor_email'], event['cid'], event['additional_data']) != (
                    model, action, object_id, delta, owner['id'], owner['email'], cid, None):
                raise AssertionError('Installed category/VAT audit attribution or complete changes differ')
            indexes = [index for index in added_indexes if index['log_entry_id'] == event['id']]
            path = 'direct' if model == 'paymentcategory' else 'actor.sport_association'
            if len(indexes) != 1 or (indexes[0]['sport_association_id'], indexes[0]['resolution_path']) != (owner['association_id'], path):
                raise AssertionError('Installed category/VAT audit association index differs')
        listed = self.category_readback()
        if next((item for item in listed if item['payment_category_id'] == category_id), None) != expected:
            raise AssertionError('Both installed runtimes did not read the exact expected category')

    def category_route_control(self):
        """No category request is in flight: this proves routing, not draining."""
        command = compose_command(ROOT / 'selfhost', self.args.env_file)
        environment = compose_environment(ROOT / 'selfhost', self.args.env_file)
        def compose(*arguments):
            return subprocess.run(command + list(arguments), env=environment, capture_output=True,
                                  text=True, check=True, timeout=90).stdout.strip()
        def status(prefix):
            request = Request(self.base + prefix + '/payment/category/list',
                              headers={'Authorization': 'Bearer ' + self.token})
            try:
                with urlopen(request, timeout=30) as response:
                    response.read()
                    return response.status
            except HTTPError as error:
                error.close()
                return error.code
        go_id = compose('ps', '-q', 'api-go')
        django_id = compose('ps', '-q', 'api')
        if not go_id or not django_id or len(go_id.splitlines()) != 1 or len(django_id.splitlines()) != 1:
            raise AssertionError('Expected one disposable container per API for category routing control')
        try:
            compose('stop', '--timeout', '30', 'api-go')
            running = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Running}}', go_id],
                                             text=True, timeout=15).strip()
            if running != 'false':
                raise AssertionError('Go category routing control did not stop its disposable upstream')
            django_status, go_status = status('/api/v1'), status('/api/v2')
            if (django_status, go_status) != (200, 502):
                raise AssertionError('Stopped Go must give category v2 HTTP502 while Django v1 stays HTTP200')
            if compose('ps', '-q', 'api') != django_id:
                raise AssertionError('Category routing control changed the Django container')
        finally:
            # Always restore only this owned upstream, even when the control
            # fails. Keep normal image/readiness requirements unchanged.
            compose('up', '-d', '--wait', '--no-deps', 'api-go')
            self.runtime()
            if compose('ps', '-q', 'api-go') != go_id:
                raise AssertionError('Category routing control unexpectedly replaced its Go container')
            ready = self.http('GET', '/api/v2/readyz')
            if ready != {'status': 'ready', 'checks': {'database': True, 'redis': True}}:
                raise AssertionError('Go dependencies did not recover after category routing control')
        print('PASS: stopped packaged Go gives category v2 HTTP502 and Django v1 HTTP200; same Go container restarted healthy.')
        return {'go_stopped': True, 'v1_category_status': django_status, 'v2_category_status': go_status,
                'same_go_container_restarted_healthy': True, 'django_container_unchanged': True,
                'active_request_drain_claimed': False}

    def write_categories(self):
        if self.args.checkpoint == 'before-backup':
            route_control = self.category_route_control()
            before = self.category_snapshot()
            owner = self.snapshot()['owner']
            payload = {'name': 'Installed category before backup', 'creation_date': '2026-09-01T12:34:56.123456Z',
                       'expense': False, 'tax_deductible': True, 'archived': False, 'type': 1,
                       'vat_management': {'vat': 1, 'deduction': 0}}
            cid = 'go-category-installed:' + str(uuid4())
            response = self.http('POST', '/api/v2/payment/category/add', payload, cid)
            if set(response) != {'data', 'msg'} or response['msg'] != 'payment category created':
                raise AssertionError('Unexpected installed Go category CREATE envelope')
            created = response['data']
            category_id = str(UUID(created['payment_category_id']))
            vat = {'vat_management_id': str(UUID(created['vat_management']['vat_management_id'])), 'vat': 1, 'deduction': 0}
            expected = {**payload, 'payment_category_id': category_id, 'sport_association': owner['association_id'],
                        'vat_management': vat, 'deleted': False}
            if created != expected:
                raise AssertionError('Installed Go CREATE did not preserve exact category/VAT input')
            creation_values = {'payment_category_id': category_id, 'name': payload['name'],
                'creation_date': '2026-09-01 12:34:56.123456', 'sport_association': owner['association_id'],
                'expense': 'False', 'tax_deductible': 'True', 'archived': 'False', 'type': '1',
                'vat_management': vat['vat_management_id'], 'deleted': 'False',
                'users_default_category': 'application.User.None', 'users_default_category_courses': 'application.User.None'}
            after = self.category_snapshot()
            self.assert_category_mutation(before, after, expected, vat, [
                ('vatmanagement', 0, vat['vat_management_id'], {key: ['None', str(value)] for key, value in vat.items()}),
                ('paymentcategory', 0, category_id, {key: ['None', value] for key, value in creation_values.items()}),
            ], cid)
            # Seed only reference fixtures through the actual installed Django
            # ORM. This is fixture preparation, not an asserted product write.
            references = self.category_shell(CATEGORY_REFERENCES, {'category_id': category_id, 'owner_id': owner['id'],
                                                                   'association_id': owner['association_id']})
            final = self.category_snapshot()
            for key in ('categories', 'vat', 'audits', 'indexes'):
                if final[key] != after[key]:
                    raise AssertionError('Reference fixture unexpectedly changed category/VAT/audit state')
            for key, field in (('payments', 'payment_id'), ('camps', 'camps_and_retreats_id'),
                               ('periods', 'camps_and_retreats_period_id'), ('services', 'camps_and_retreats_period_service_id')):
                prior = {item[field] for item in after[key]}
                if len(final[key]) != len(after[key]) + 1 or [item for item in final[key] if item[field] in prior] != after[key]:
                    raise AssertionError('Reference fixture changed preexisting rows: ' + key)
            metadata = {'category_id': category_id, 'original_vat_id': vat['vat_management_id'],
                        'association_id': owner['association_id'], 'owner_id': owner['id'], 'references': references}
        else:
            if self.args.prior_receipt is None:
                raise ValueError('Category after-backup write requires its prior checkpoint receipt')
            prior = json.loads(self.args.prior_receipt.read_text())
            if prior.get('checkpoint') != 'before-backup':
                raise ValueError('Category write requires the before-backup checkpoint')
            metadata = prior['categories']['fixture']
            route_control = prior['categories']['route_control']
            if self.category_snapshot() != prior['categories']['database'] or self.category_readback() != prior['categories']['http']:
                raise AssertionError('Category state changed before the after-backup mutation')
            category_id = metadata['category_id']
            before = self.category_snapshot()
            original = next(row for row in prior['categories']['http'] if row['payment_category_id'] == category_id)
            patch = {'name': 'Installed category after backup', 'type': 2, 'tax_deductible': False,
                     'archived': True, 'vat_management': {'vat': 16, 'deduction': 2}}
            cid = 'go-category-installed:' + str(uuid4())
            response = self.http('PATCH', f'/api/v2/payment/category/{category_id}/update', patch, cid)
            vat = {'vat_management_id': str(UUID(response['data']['vat_management']['vat_management_id'])), 'vat': 16, 'deduction': 2}
            expected = {**original, **patch, 'vat_management': vat}
            if response != {'data': expected, 'msg': 'payment category updated'} or vat['vat_management_id'] == metadata['original_vat_id']:
                raise AssertionError('Installed Go PATCH did not create the expected replacement VAT')
            after = self.category_snapshot()
            self.assert_category_mutation(before, after, expected, vat, [
                ('vatmanagement', 0, vat['vat_management_id'], {key: ['None', str(value)] for key, value in vat.items()}),
                ('paymentcategory', 1, category_id, {'name': [original['name'], patch['name']], 'type': ['1', '2'],
                    'tax_deductible': ['True', 'False'], 'archived': ['False', 'True'],
                    'vat_management': [metadata['original_vat_id'], vat['vat_management_id']]}),
            ], cid)
            cid = 'go-category-installed:' + str(uuid4())
            deleted = self.http('DELETE', f'/api/v2/payment/category/{category_id}/delete', cid=cid)
            if deleted != {'msg': 'payment category deleted'}:
                raise AssertionError('Unexpected installed Go category DELETE envelope')
            expected = {**expected, 'deleted': True}
            final = self.category_snapshot()
            self.assert_category_mutation(after, final, expected, None,
                [('paymentcategory', 1, category_id, {'deleted': ['False', 'True']})], cid)
            repeated = self.http('DELETE', f'/api/v2/payment/category/{category_id}/delete', cid=cid)
            if repeated != deleted or self.category_snapshot() != final:
                raise AssertionError('Repeated installed category deletion changed durable state or audit/index count')
        self.assert_category_references(final, metadata)
        return {'fixture': metadata, 'database': final, 'http': self.category_readback(), 'route_control': route_control,
                'proof': {'packaged_go_category_writes': True, 'django_and_go_http_readback': True,
                          'exact_category_vat_audit_index_state': True, 'all_four_category_foreign_keys': True}}

    def assert_category_references(self, state, fixture):
        category_id = fixture['category_id']
        refs = fixture['references']
        if state['owner_references'] != {'user_id': fixture['owner_id'], 'default_payment_category_id': category_id,
                                         'default_payment_category_courses_id': category_id}:
            raise AssertionError('Installed category owner defaults changed')
        payment = next((row for row in state['payments'] if row['payment_id'] == refs['payment_id']), None)
        service = next((row for row in state['services'] if row['camps_and_retreats_period_service_id'] == refs['service_id']), None)
        if payment is None or service is None or payment['payment_category_id'] != category_id or service['payment_category_id'] != category_id:
            raise AssertionError('Installed category payment/camp foreign keys changed')
        if payment['sport_association_id'] != fixture['association_id'] or payment['amount'] != 17.25 or payment['deleted']:
            raise AssertionError('Installed category reference payment changed')
        if next((row for row in state['vat'] if row['vat_management_id'] == fixture['original_vat_id']), None) != {
                'vat_management_id': fixture['original_vat_id'], 'vat': 1, 'deduction': 0}:
            raise AssertionError('Original installed category VAT row was lost or changed')

    def verify_categories(self, receipt):
        expected = receipt['categories']
        if self.category_snapshot() != expected['database'] or self.category_readback() != expected['http']:
            raise AssertionError('Installed restore did not recover exact category/VAT/audit/index/reference state')
        self.assert_category_references(expected['database'], expected['fixture'])

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
        categories = self.write_categories()
        receipt = {'schema_version': 1, 'checkpoint': self.args.checkpoint, 'snapshot': after, 'categories': categories,
                   'proof': {'go_http_mutation': True, 'django_http_readback': True,
                             'postgres_audit_and_index': True, 'repeat_without_duplicate_audit': True}}
        atomic_write(self.args.receipt, json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(f'PASS: Go {self.args.checkpoint} preference/category mutations, both HTTP runtimes, exact category/VAT/audit/index and four retained FKs.')

    def verify(self):
        receipt = json.loads(self.args.receipt.read_text())
        checkpoint = receipt.get('checkpoint')
        if receipt.get('schema_version') != 1 or checkpoint not in ('before-backup', 'after-backup'):
            raise ValueError('Verification requires a saved mutation receipt')
        if 'categories' not in receipt:
            raise AssertionError('Installed checkpoint lacks category/VAT/audit/index/FK restore evidence')
        expected = payload(checkpoint)
        if receipt['snapshot']['owner']['tables_settings'] != expected:
            raise ValueError('Mutation receipt contains a different checkpoint')
        self.readback(expected)
        if self.snapshot() != receipt['snapshot']:
            raise AssertionError('Restore did not recover the exact Go preference and audit/index state')
        self.verify_categories(receipt)
        print(f'PASS: Go {checkpoint} preference and category/VAT/audit/index/four-FK state exactly match the saved receipt.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('write', 'verify'))
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--login-json', type=Path, required=True)
    parser.add_argument('--base-url', default='http://localhost:58080')
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--checkpoint', choices=('before-backup', 'after-backup'))
    parser.add_argument('--prior-receipt', type=Path, help='Required before-backup receipt for the later category mutation')
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
