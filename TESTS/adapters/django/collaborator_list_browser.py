"""Unchanged collaborator-list browser consumer against Django/v1 and Go/v2.

The parent supplies the immutable Go image and disposable PostgreSQL/Redis
images. This adapter owns no installed-backend selection claim and treats each
ordinary browser login as a separate state baseline from the following read.
"""
import argparse
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]

from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.django.profile_browser import fingerprints, prepare_ui
from TESTS.adapters.django.profile_browser import WORKFLOW_SOURCES as PROFILE_SOURCES
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.impersonation_management_image import go_source_hashes
from TESTS.adapters.go.payment_category_image import DATABASE, LABEL, ImageFixture
from TESTS.adapters.caddy import proxy as versioned_proxy
from TESTS.http_client import Client, poll

SELECTED_ROUTES = (
    'instance/status', 'instance/config', 'instance/access',
    'oauth2/login', 'oauth2/refresh-token', 'profile/info',
    'profile/settings/tables', 'collaborators/list',
)
WEBSOCKET_ROUTES = ('/ws/health/', '/ws/notifications/')
COEXISTENCE_ROUTES = (
    'instance/manifest.json', 'instance/logo.png', 'association/export/active',
    'statistic/dashboard', 'communications/staff-board/list',
)
WORKFLOW_SOURCES = PROFILE_SOURCES + (
    'src/components/Sidebar.svelte',
    'src/routes/dashboard/Dashboard.svelte',
    'src/routes/dashboard/EmptyDasbhoard.svelte',
    'src/components/widgets/StaffBoard.svelte',
    'src/components/tables/BKNDatatable.svelte',
    'src/routes/connectedCollaborators/ConnectedCollaborators.svelte',
    'src/routes/connectedCollaborators/CollaboratorActions.svelte',
    'src/routes/connectedCollaborators/modals/EditModal.svelte',
    'src/components/PermissionsComponent.svelte',
    'src/components/buttons/EditButton.svelte',
    'src/components/buttons/DeleteButton.svelte',
    'src/utils/Permissions.js',
    'src/utils/Functions.js',
    'src/store/stores.js',
)
SOURCE_PATHS = (
    'TESTS/adapters/django/collaborator_list_browser.py',
    'TESTS/browser/collaborator-list.mjs',
    'TESTS/adapters/django/profile_browser.py',
    'TESTS/adapters/django/collaborator_list.py',
    'TESTS/contracts/test_collaborator_list.py',
    'TESTS/adapters/caddy.py',
    'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/age_worker_fixture.py',
    'TESTS/adapters/go/impersonation_management_image.py',
    'TESTS/adapters/django/settings.py',
    'TESTS/http_client.py',
    'UI/Dockerfile',
    'selfhost/caddy/Caddyfile', 'selfhost/.env.example',
    'TESTS/test_collaborator_browser_checkpoints.py',
)
CHECKPOINTS = (
    ('owner', 'login'), ('owner', 'read'), ('owner', 'reload'),
    ('restricted', 'login'), ('restricted', 'read'), ('restricted', 'reload'),
    ('foreign-owner', 'login'), ('foreign-owner', 'read'), ('foreign-owner', 'reload'),
    ('denied', 'login'), ('denied', 'denied'),
)
READ_ACTORS = ('owner', 'restricted', 'foreign-owner')
EXPECTED_LIST_REQUESTS = 7
EXPECTED_MANAGED_MODELS = 128
LOGIN_ACTORS = ('owner', 'restricted', 'foreign-owner', 'denied')
USER_MODEL = 'application.User'


def uid(number):
    return f'a2000000-0000-4000-8000-{number:012d}'


def write_private_json(path, value):
    pending = path.with_name(path.name + '.pending')
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(value, output)
    os.replace(pending, path)


def model_changes(before, after):
    assert list(before) == list(after), 'Managed model scope or order changed'
    changes = {}
    for model in before:
        old_rows, new_rows = before[model], after[model]
        if model != USER_MODEL:
            if old_rows != new_rows:
                changes[model] = [{'before': old_rows, 'after': new_rows}]
            continue
        old = {str(row['user_id']): row for row in old_rows}
        new = {str(row['user_id']): row for row in new_rows}
        assert len(old) == len(old_rows), 'User model has duplicate user_id rows'
        assert len(new) == len(new_rows), 'User model has duplicate user_id rows'
        old_order = [str(row['user_id']) for row in old_rows]
        new_order = [str(row['user_id']) for row in new_rows]
        assert old_order == new_order, 'User model primary-key row order changed'
        rows = []
        for key in old_order:
            assert set(old[key]) == set(new[key]), 'User model field scope changed'
            fields = {name: (old[key][name], new[key][name])
                      for name in old[key]
                      if old[key][name] != new[key][name]}
            if fields:
                rows.append({'key': key, 'fields': fields})
        if rows:
            changes[model] = rows
    return changes


def assert_managed_equal(before, after):
    assert len(before) == len(after) == EXPECTED_MANAGED_MODELS, 'Managed model count differs'
    assert before == after, 'Managed model state changed during read'


def assert_login_transition(before, after, actor_id):
    changes = model_changes(before, after)
    assert set(changes) == {USER_MODEL}, 'Login changed models outside User'
    rows = changes[USER_MODEL]
    assert len(rows) == 1 and rows[0]['key'] == actor_id, 'Login changed another account'
    assert set(rows[0]['fields']) == {'last_login'}, 'Login changed fields beyond last_login'
    previous, current = rows[0]['fields']['last_login']
    assert current is not None and current != previous, 'Login did not advance last_login'
    return {'model': USER_MODEL, 'user_id': actor_id, 'fields': ['last_login'],
            'last_login': current}


def assert_read_baseline(baseline, current):
    assert_managed_equal(baseline, current)
    return {'managed_models': len(current), 'managed_state_sha256': state_digest(current)}


def assert_final_login_projection(baseline, current, effects, expected_actors):
    """Require exactly the four declared User.last_login transitions from baseline."""
    assert len(effects) == len(expected_actors) == 4, 'Expected four ordinary logins'
    assert tuple(effect['actor'] for effect in effects) == tuple(expected_actors), \
        'Login effects differ from the declared actor order'
    expected = copy.deepcopy(baseline)
    rows = expected[USER_MODEL]
    by_id = {str(row['user_id']): row for row in rows}
    assert len(by_id) == len(rows), 'User model has duplicate user_id rows'
    changed_ids = []
    for effect in effects:
        assert effect['model'] == USER_MODEL, 'Declared login effect used another model'
        assert effect['fields'] == ['last_login'], 'Declared login effect changed another field'
        actor_id = str(effect['user_id'])
        assert actor_id in by_id, 'Declared login actor is absent from User baseline'
        assert actor_id not in changed_ids, 'Declared login actor changed last_login twice'
        assert effect['last_login'] is not None, 'Declared login has no last_login value'
        by_id[actor_id]['last_login'] = effect['last_login']
        changed_ids.append(actor_id)
    assert current == expected, \
        'Final state differs beyond the four declared actor last_login changes'
    return {
        'baseline_sha256': state_digest(baseline),
        'observed_sha256': state_digest(current),
        'declared_actors': list(expected_actors),
        'changed_user_ids': changed_ids,
        'changed_fields': ['last_login'],
        'non_login_state_exact': True,
        'last_login_rows_retained': True,
    }


def assert_private_docker_ownership(proof):
    """Reject resources not labeled and named by this disposable fixture instance."""
    identifier = proof.identifier
    assert re.fullmatch(r'az-category-image-[0-9a-f]{32}', identifier), \
        'Unexpected Docker fixture owner identifier'
    assert proof.network == identifier + '-net', 'Fixture network escaped its owner prefix'
    assert proof.network_created, 'Fixture did not create its private network'
    assert proof.containers, 'Fixture owns no started containers'
    network = json.loads(proof.docker(
        'network', 'inspect', proof.network).stdout)[0]
    assert network['Name'] == identifier + '-net', 'Private network name differs'
    assert network.get('Labels', {}).get(LABEL) == identifier, \
        'Private network ownership label differs'
    for name in proof.containers:
        assert name.startswith(identifier + '-'), 'Fixture container escaped its owner prefix'
        info = json.loads(proof.docker('inspect', name).stdout)[0]
        assert info['Config']['Labels'].get(LABEL) == identifier, \
            'Private container ownership label differs: ' + name
        assert identifier + '-net' in info['NetworkSettings']['Networks'], \
            'Private container is outside the fixture network: ' + name


def require_server_digest(value):
    assert isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), \
        'Build receipt and runtime must bind a valid server SHA256'
    return value


def assert_server_binary_binding(runtime, expected):
    actual = require_server_digest(runtime.get('metadata', {}).get('server_sha256'))
    assert actual == require_server_digest(expected), \
        'Packaged server binary differs from build receipt'
    return actual


def canonical_rows(rows):
    return sorted(rows, key=lambda row: (
        row['email'], row['name'], row['status'], row['role'],
        row.get('user_id') or '', row.get('collaboration_invite_id') or '',
    ))


def assert_browser_acceptance(browser, actors):
    assert browser['success'] and browser['browser_created']
    assert browser['contexts_closed'] and browser['browser_closed']
    assert browser['browser_cache_matches_invocation']
    assert not browser['failures']
    checkpoints = [row for row in browser['checks'] if 'checkpoint' in row]
    observed = [(row['actor'], row['action']) for row in checkpoints]
    assert tuple(observed) == CHECKPOINTS
    assert [row['checkpoint'] for row in checkpoints] == list(range(1, len(CHECKPOINTS) + 1))
    assert checkpoints[-1]['http_status'] == 403
    reads = {}
    for actor in READ_ACTORS:
        displayed = [row for row in browser['checks']
                     if row['actor'] == actor and row['action'] == 'displayed']
        reloaded = [row for row in browser['checks']
                    if row['actor'] == actor and row['action'] == 'reloaded']
        assert len(displayed) == len(reloaded) == 1, actor + ' display/reload evidence missing'
        assert displayed[0]['count'] == len(actors[actor]['expected_rows'])
        assert reloaded[0]['count'] == len(actors[actor]['expected_rows'])
        assert [page['row_count'] for page in displayed[0]['pages']] == actors[actor]['expected_pages']
        assert [page['row_count'] for page in reloaded[0]['pages']] == actors[actor]['expected_pages']
        for result in (displayed[0], reloaded[0]):
            rows = [row for page in result['pages'] for row in page['rows']]
            expected = actors[actor]['expected_rows']
            visible = lambda values: sorted(
                (row['name'], row['email'], row['status'], row['role']) for row in values)
            assert visible(rows) == visible(expected), actor + ' displayed rows differ'
            expected_by_email = {row['email']: row for row in expected}
            assert len(expected_by_email) == len(expected), 'Ambiguous authored row identity'
            for row in rows:
                reference = expected_by_email[row['email']]
                assert row['edit_control'] == (1 if reference['user_id'] else 0)
                assert row['delete_disabled'] == (actors[actor]['action_mode'] != 'manage')
            assert result['user_ids'] == sorted(row['user_id'] for row in expected if row['user_id'])
            assert actors[actor]['forbidden_emails'], 'Foreign contrast fixture is empty'
            assert result.get('foreign_emails_absent') == len(actors[actor]['forbidden_emails'])
            assert result['no_empty_state']
            assert result['create_control_visible'] == (actors[actor]['action_mode'] == 'manage')
        reads[actor] = displayed[0]
    denied = [row for row in browser['checks']
              if row['actor'] == 'denied' and row['action'] == 'denied'
              and 'checkpoint' not in row and 'http_status' in row]
    assert len(denied) == 1 and denied[0]['http_status'] == 403
    assert not denied[0]['sidebar_link_visible']
    assert denied[0]['no_false_empty_state']
    assert denied[0]['ui_error'] == 'Impossibile caricare i dati.'
    selected = 'django-v1' if browser['version'] == 'v1' else 'go-v2'
    logins = [row for row in browser['responses'] if row['path'] == '/api/oauth2/login']
    assert tuple(row['actor'] for row in logins) == LOGIN_ACTORS, \
        'Ordinary login response count/order differs'
    assert all(row['method'] == 'POST' and row['status'] == 200
               and row['backend'] == selected for row in logins)
    list_responses = [row for row in browser['responses']
                      if row['path'] == '/api/collaborators/list']
    assert len(list_responses) == EXPECTED_LIST_REQUESTS
    assert [row['status'] for row in list_responses].count(200) == 6
    assert [row['status'] for row in list_responses].count(403) == 1
    assert all(row['method'] == 'GET' for row in list_responses)
    assert all(row['backend'] == selected for row in list_responses), \
        'Collaborator list backend responses differ from the selected path'
    return reads


class PortableBrowserControls(unittest.TestCase):
    def setUp(self):
        owner_rows = [
            {'name': 'ELENA ACCENTI', 'email': 'elena@example.test', 'status': 'Accettato',
             'role': 'Accesso Completo', 'user_id': uid(1),
             'collaboration_invite_id': None, 'is_invite': False},
            {'name': '-', 'email': 'pending@example.test', 'status': 'In attesa',
             'role': 'Personalizzato', 'user_id': None,
             'collaboration_invite_id': uid(90), 'is_invite': True},
        ]
        self.actors = {
            'owner': {'expected_rows': owner_rows, 'expected_pages': [1, 1],
                      'action_mode': 'manage'},
            'restricted': {'expected_rows': copy.deepcopy(owner_rows), 'expected_pages': [1, 1],
                           'action_mode': 'read'},
            'foreign-owner': {
                'expected_rows': [{'name': 'FRANCA FOREIGN', 'email': 'franca@example.test',
                    'status': 'Accettato', 'role': 'Accesso Completo',
                    'user_id': uid(50), 'collaboration_invite_id': None, 'is_invite': False}],
                'expected_pages': [1], 'action_mode': 'manage'},
        }
        for actor, data in self.actors.items():
            other = 'owner' if actor == 'foreign-owner' else 'foreign-owner'
            data['forbidden_emails'] = [row['email'] for row in self.actors[other]['expected_rows']]
        checks = []
        for index, (actor, action) in enumerate(CHECKPOINTS, 1):
            checks.append({'actor': actor, 'action': action, 'checkpoint': index,
                           **({'http_status': 403} if action == 'denied' else {})})
        for actor in READ_ACTORS:
            row = self.actors[actor]
            for action in ('displayed', 'reloaded'):
                cursor = 0
                pages = []
                for count in row['expected_pages']:
                    pages.append({'row_count': count,
                        'rows': [{**{key: item[key] for key in ('name', 'email', 'status', 'role')},
                                  'edit_control': 1 if item['user_id'] else 0,
                                  'delete_disabled': row['action_mode'] != 'manage'}
                                 for item in row['expected_rows'][cursor:cursor + count]]})
                    cursor += count
                checks.append({'actor': actor, 'action': action,
                    'count': len(row['expected_rows']),
                    'pages': pages,
                    'user_ids': sorted(item['user_id'] for item in row['expected_rows']
                                       if item['user_id']),
                    'no_empty_state': True, 'foreign_emails_absent': len(row['forbidden_emails']),
                    'create_control_visible': row['action_mode'] == 'manage'})
        checks.append({'actor': 'denied', 'action': 'denied', 'http_status': 403,
            'sidebar_link_visible': False, 'no_false_empty_state': True,
            'ui_error': 'Impossibile caricare i dati.'})
        statuses = [200, 200, 200, 200, 200, 200, 403]
        self.browser = {
            'success': True, 'version': 'v1', 'browser_created': True, 'contexts_closed': True,
            'browser_closed': True, 'browser_cache_matches_invocation': True,
            'failures': [], 'checks': checks,
            'responses': [{'path': '/api/collaborators/list', 'method': 'GET',
                           'status': status, 'backend': 'django-v1'}
                          for status in statuses] + [
                {'actor': actor, 'path': '/api/oauth2/login', 'method': 'POST',
                 'status': 200, 'backend': 'django-v1'} for actor in LOGIN_ACTORS],
        }

    def test_complete_evidence_passes(self):
        assert_browser_acceptance(self.browser, self.actors)

    def test_missing_or_vacuous_foreign_contrast_fails(self):
        for count in (None, 0):
            altered = copy.deepcopy(self.browser)
            displayed = next(row for row in altered['checks'] if row['action'] == 'displayed')
            displayed['foreign_emails_absent'] = count
            with self.subTest(count=count), self.assertRaises(AssertionError):
                assert_browser_acceptance(altered, self.actors)

    def test_duplicate_ui_denial_and_extra_login_fail(self):
        altered = copy.deepcopy(self.browser)
        denial = next(row for row in altered['checks']
                      if row['action'] == 'denied' and 'checkpoint' not in row)
        altered['checks'].append(copy.deepcopy(denial))
        with self.assertRaises(AssertionError):
            assert_browser_acceptance(altered, self.actors)
        altered = copy.deepcopy(self.browser)
        altered['responses'].append(copy.deepcopy(altered['responses'][-1]))
        with self.assertRaises(AssertionError):
            assert_browser_acceptance(altered, self.actors)

    def test_false_empty_or_incomplete_rows_fail(self):
        for action in ('displayed', 'reloaded'):
            altered = copy.deepcopy(self.browser)
            check = next(row for row in altered['checks']
                         if row['actor'] == 'owner' and row['action'] == action)
            check['pages'][0]['rows'] = []
            check['count'] = 0
            with self.assertRaises(AssertionError):
                assert_browser_acceptance(altered, self.actors)

    def test_role_status_name_email_and_identity_are_exact(self):
        mutations = (
            lambda row: row.__setitem__('name', 'WRONG NAME'),
            lambda row: row.__setitem__('email', 'wrong@example.test'),
            lambda row: row.__setitem__('status', 'Scaduto'),
            lambda row: row.__setitem__('role', 'Personalizzato'),
            lambda row: row.__setitem__('edit_control', 0),
            lambda row: row.__setitem__('delete_disabled', True),
        )
        for mutate in mutations:
            altered = copy.deepcopy(self.browser)
            check = next(row for row in altered['checks']
                         if row['actor'] == 'owner' and row['action'] == 'displayed')
            mutate(check['pages'][0]['rows'][0])
            with self.assertRaises(AssertionError):
                assert_browser_acceptance(altered, self.actors)

    def test_denial_cannot_become_empty_success(self):
        altered = copy.deepcopy(self.browser)
        denied = next(row for row in altered['checks'] if row['action'] == 'denied')
        denied['http_status'] = 200
        denied['no_false_empty_state'] = False
        with self.assertRaises(AssertionError):
            assert_browser_acceptance(altered, self.actors)

    def test_backend_misattribution_and_missing_checkpoint_fail(self):
        altered = copy.deepcopy(self.browser)
        altered['responses'][0]['backend'] = 'go-v2'
        with self.assertRaises(AssertionError):
            assert_browser_acceptance(altered, self.actors)
        broken = copy.deepcopy(self.browser)
        broken['checks'] = [row for row in broken['checks'] if 'checkpoint' not in row
                            or row['checkpoint'] != len(CHECKPOINTS)]
        with self.assertRaises(AssertionError):
            assert_browser_acceptance(broken, self.actors)
        closed = copy.deepcopy(self.browser)
        closed['browser_closed'] = False
        with self.assertRaises(AssertionError):
            assert_browser_acceptance(closed, self.actors)

    def test_login_and_read_state_controls(self):
        before = {'application.User': [
            {'user_id': uid(1), 'last_login': None, 'email': 'owner@example.test'}]}
        after = {'application.User': [
            {'user_id': uid(1), 'last_login': '2026-10-25T12:00:00Z',
             'email': 'owner@example.test'}]}
        effect = assert_login_transition(before, after, uid(1))
        self.assertEqual(effect['fields'], ['last_login'])
        wrong_actor = copy.deepcopy(after)
        wrong_actor['application.User'][0]['user_id'] = uid(2)
        with self.assertRaises(AssertionError):
            assert_login_transition(before, wrong_actor, uid(1))
        extra = copy.deepcopy(after)
        extra['application.User'][0]['email'] = 'changed@example.test'
        with self.assertRaises(AssertionError):
            assert_login_transition(before, extra, uid(1))

    def test_database_binding_reuses_exported_fixture_name(self):
        from TESTS.adapters.go import payment_category_image
        self.assertIs(DATABASE, payment_category_image.DATABASE)
        self.assertEqual(DATABASE, 'assozeta_contract_category_image')

    def test_repeated_fk_rows_are_never_collapsed_for_login_checks(self):
        owner = uid(1)
        common = {'user_id': owner, 'accepted': False, 'email': 'same@example.test'}
        invites = [
            {**common, 'collaboration_invite_id': uid(90), 'token': 'first'},
            {**common, 'collaboration_invite_id': uid(91), 'token': 'second'},
            {**common, 'collaboration_invite_id': uid(92), 'token': 'third'},
        ]
        login_before = {'application.User': [
            {'user_id': owner, 'last_login': None, 'email': 'owner@example.test'}],
            'application.CollaborationInvites': copy.deepcopy(invites)}
        login_after = copy.deepcopy(login_before)
        login_after['application.User'][0]['last_login'] = '2026-10-25T12:00:00Z'
        mutations = (
            lambda rows: rows[1].__setitem__('email', 'changed@example.test'),
            lambda rows: rows.pop(1),
            lambda rows: rows.append({**common, 'collaboration_invite_id': uid(93),
                                      'token': 'fourth'}),
        )
        for mutate in mutations:
            altered = copy.deepcopy(login_after)
            mutate(altered['application.CollaborationInvites'])
            with self.assertRaises(AssertionError):
                assert_login_transition(login_before, altered, owner)
        assert_login_transition(login_before, login_after, owner)

    def test_login_rejects_removed_last_login_and_duplicate_user_rows(self):
        before = {'application.User': [
            {'user_id': uid(1), 'last_login': None, 'email': 'owner@example.test'}]}
        removed = copy.deepcopy(before)
        removed['application.User'][0].pop('last_login')
        with self.assertRaises(AssertionError):
            assert_login_transition(before, removed, uid(1))
        duplicate = copy.deepcopy(before)
        duplicate['application.User'].append(copy.deepcopy(duplicate['application.User'][0]))
        with self.assertRaises(AssertionError):
            assert_login_transition(before, duplicate, uid(1))

    def test_four_login_projection_and_unrelated_writes(self):
        users = [
            {'user_id': uid(index), 'last_login': None,
             'date_joined': '2026-01-01T00:00:00Z', 'email': f'user-{index}@example.test'}
            for index in range(1, 6)
        ]
        child_rows = [
            {'user_id': uid(1), 'child_id': uid(90), 'value': 'unchanged'},
            {'user_id': uid(1), 'child_id': uid(91), 'value': 'unchanged'},
        ]
        baseline = {'application.User': copy.deepcopy(users),
                    'application.Child': copy.deepcopy(child_rows)}
        effects, current = [], copy.deepcopy(baseline)
        for index, actor in enumerate(LOGIN_ACTORS, 1):
            before = copy.deepcopy(current)
            current['application.User'][index - 1]['last_login'] = datetime(
                2026, 10, 25, 12, index, tzinfo=timezone.utc)
            effect = assert_login_transition(before, current, uid(index))
            effects.append({'actor': actor, **effect})
        receipt = assert_final_login_projection(
            baseline, current, effects, LOGIN_ACTORS)
        self.assertEqual(receipt['declared_actors'], list(LOGIN_ACTORS))
        self.assertTrue(receipt['last_login_rows_retained'])
        self.assertIsInstance(json_safe(effects), list)

        unrelated = copy.deepcopy(current)
        unrelated['application.User'][4]['date_joined'] = '2026-10-25T12:05:00Z'
        with self.assertRaises(AssertionError):
            assert_final_login_projection(
                baseline, unrelated, effects, LOGIN_ACTORS)
        extra_login = copy.deepcopy(current)
        extra_login['application.User'][4]['last_login'] = '2026-10-25T12:05:00Z'
        with self.assertRaises(AssertionError):
            assert_final_login_projection(
                baseline, extra_login, effects, LOGIN_ACTORS)
        child_change = copy.deepcopy(current)
        child_change['application.Child'][1]['value'] = 'changed'
        with self.assertRaises(AssertionError):
            assert_final_login_projection(
                baseline, child_change, effects, LOGIN_ACTORS)
        with self.assertRaises(AssertionError):
            assert_final_login_projection(
                baseline, current, effects[1:], LOGIN_ACTORS)

    def test_each_read_must_equal_current_baseline(self):
        snapshot = {'model-%03d' % index: [{'pk': index}]
                    for index in range(EXPECTED_MANAGED_MODELS)}
        assert_read_baseline(snapshot, copy.deepcopy(snapshot))
        changed = copy.deepcopy(snapshot)
        changed['model-127'][0]['pk'] = -1
        with self.assertRaises(AssertionError):
            assert_read_baseline(snapshot, changed)


class DjangoObserver:
    """Transparent ASGI attribution, independent of gateway response headers."""
    def __init__(self, application):
        self.application = application
        self.lock = threading.Lock()
        self.label = 'startup'
        self.requests = {}

    def begin(self, label):
        with self.lock:
            self.label = label
            self.requests[label] = []

    async def __call__(self, scope, receive, send):
        row = None
        if scope['type'] in ('http', 'websocket'):
            path = scope['path'].lstrip('/')
            if path == 'collaborators/list':
                classification = 'selected'
            elif path in SELECTED_ROUTES or path in COEXISTENCE_ROUTES:
                classification = 'django-coexistence'
            elif scope['type'] == 'websocket' and scope['path'] in WEBSOCKET_ROUTES:
                classification = 'django-coexistence'
            else:
                classification = 'unknown'
            row = {'classification': classification, 'type': scope['type'],
                   'method': scope.get('method'), 'path': scope['path'], 'status': None}
            with self.lock:
                self.requests.setdefault(self.label, []).append(row)

        async def observed_send(message):
            if row is not None and message['type'] == 'http.response.start':
                row['status'] = message['status']
            await send(message)

        await self.application(scope, receive, observed_send)

    def snapshot(self, label):
        with self.lock:
            rows = copy.deepcopy(self.requests.get(label, []))
        selected = [row for row in rows if row['classification'] == 'selected']
        unknown = [row for row in rows if row['classification'] == 'unknown']
        return {'selected': selected, 'unknown': unknown, 'requests': rows,
                'selected_count': len(selected), 'unknown_count': len(unknown)}

    def assert_no_unknown(self, label):
        snapshot = self.snapshot(label)
        assert snapshot['unknown_count'] == 0, 'Unlisted Django dependency'


class CollaboratorBrowserFixture(ImageFixture):
    def run_container(self, suffix, image, *args):
        if suffix.startswith('browser-'):
            args = (*args, '--no-healthcheck')
        return super().run_container(suffix, image, *args)


@contextlib.contextmanager
def ui_gateway(proof, work, ui, django_port, versioned_upstream, version, label):
    """Exact API allowlist plus unchanged static UI on the owned network."""
    image = re.search(r'^FROM (caddy:\S+) AS production$',
                      (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
    selected_backend = 'django-v1' if version == 'v1' else 'go-v2'
    config = '''{
 admin off
}
:80 {
 root * /srv
 route {
  handle /fixture-health {
   respond 200
  }
  @selected path SELECTED
  handle @selected {
   header X-Fixture-Backend LABEL
   uri replace /api/ /api/VERSION/ 1
   reverse_proxy UPSTREAM
  }
  @coexistence path COEXISTENCE
  handle @coexistence {
   header X-Fixture-Backend django-coexistence
   uri strip_prefix /api
   reverse_proxy host.docker.internal:DJANGO
  }
  @sockets path SOCKETS
  handle @sockets {
   header X-Fixture-Backend django-coexistence
   reverse_proxy host.docker.internal:DJANGO
  }
  @unknown path /api/* /instance-* /ws/*
  handle @unknown {
   header X-Fixture-Backend unsupported-fixture-route
   respond 502
  }
  handle {
   header X-Fixture-Backend ui-static
   header Cache-Control no-cache
   try_files {path} /index.html
   file_server
  }
 }
}
'''.replace('SELECTED', ' '.join('/api/' + route for route in SELECTED_ROUTES)
    ).replace('COEXISTENCE', ' '.join('/api/' + route for route in COEXISTENCE_ROUTES)
    ).replace('SOCKETS', ' '.join(WEBSOCKET_ROUTES)
    ).replace('LABEL', selected_backend).replace('VERSION', version
    ).replace('UPSTREAM', versioned_upstream).replace('DJANGO', str(django_port))
    path = work / (label + '.Caddyfile')
    path.write_text(config)
    name = 'browser-' + uuid4().hex[:10]
    caddy_id = proof.image(image)['Id']
    name = proof.run_container(name, caddy_id,
        '--add-host', 'host.docker.internal:host-gateway',
        '--publish', '127.0.0.1::80',
        '--mount', f'type=bind,source={path},target=/etc/caddy/Caddyfile,readonly',
        '--mount', f'type=bind,source={ui},target=/srv,readonly')
    origin = 'http://127.0.0.1:' + str(proof.published_port(name, 80))
    def ready():
        try:
            return Client(origin, timeout=1).request('GET', '/fixture-health')[0] == 200
        except OSError:
            return False
    poll(ready, bool, timeout=30)
    proof.docker('exec', name, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile')
    try:
        yield origin, {
            'configuration_sha256': hashlib.sha256(config.encode()).hexdigest(),
            'selected_backend': selected_backend,
            'versioned_prefix': '/api/' + version,
            'selected_routes': list(SELECTED_ROUTES),
            'django_coexistence_routes': list(COEXISTENCE_ROUTES),
            'django_websocket_routes': list(WEBSOCKET_ROUTES),
            'unknown_api_response': 502,
            'static_artifact': 'unchanged extracted UI',
        }
    finally:
        proof.docker('rm', '--force', name, check=False)
        assert proof.docker('inspect', name, check=False).returncode != 0, \
            'Private UI gateway cleanup failed'


def capture_tool_environment():
    keys = ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN',
            'PLAYWRIGHT_BROWSERS_PATH')
    result = {key: value for key, value in os.environ.items() if key in keys}
    result.update(LC_ALL='C', LANG='C')
    return result


def source_hashes(go_source=ROOT):
    paths = set(SOURCE_PATHS)
    paths.update(str(p.relative_to(ROOT)) for p in (ROOT / 'BE').rglob('*.py'))
    paths.update(str(Path('UI') / source) for source in WORKFLOW_SOURCES)
    files = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
             for path in sorted(paths)}
    return files, go_source_hashes(go_source)


def display_row(name, email, status, role, *, user_id=None, invite_id=None, foreign=False):
    return {'name': name, 'email': email, 'status': status, 'role': role,
            'user_id': user_id, 'collaboration_invite_id': invite_id,
            'is_invite': invite_id is not None, 'foreign': foreign}


def expected_user(person):
    return display_row(
        f'{person.first_name.upper()} {person.last_name.upper()}',
        person.email, 'Accettato',
        'Accesso Completo' if person.collaborator_role == 1 else 'Personalizzato',
        user_id=str(person.pk),
    )


def expected_invite(invite, now):
    if invite.expiration_date and invite.expiration_date < now:
        status = 'Scaduto'
    else:
        status = 'In attesa'
    return display_row(
        '-', invite.email, status,
        'Accesso Completo' if invite.collaborator_role == 1 else 'Personalizzato',
        invite_id=str(invite.pk),
    )


def response_display_rows(body, now):
    rows = []
    for row in body:
        if row.get('is_invite'):
            expiration = row.get('expiration_date')
            expired = expiration is not None and datetime.fromisoformat(
                expiration.replace('Z', '+00:00')) < now
            rows.append(display_row(
                '-', row['email'], 'Scaduto' if expired else 'In attesa',
                'Accesso Completo' if row['collaborator_role'] == 1 else 'Personalizzato',
                invite_id=row['collaboration_invite_id'],
            ))
        else:
            rows.append(display_row(
                f"{row['first_name'].upper()} {row['last_name'].upper()}",
                row['email'], 'Accettato',
                'Accesso Completo' if row['collaborator_role'] == 1 else 'Personalizzato',
                user_id=row['user_id'],
            ))
    return rows


def drive_browser(process, checkpoints, on_checkpoint, timeout=360):
    deadline = time.monotonic() + timeout
    seen = 0
    while seen < len(CHECKPOINTS):
        if process.poll() is not None:
            raise AssertionError('Browser exited before all checkpoints')
        assert time.monotonic() < deadline, 'Browser checkpoint deadline exceeded'
        path = checkpoints / f'{seen + 1}.json'
        if not path.exists():
            time.sleep(.05)
            continue
        payload = json.loads(path.read_text())
        expected = {'id': seen + 1, 'actor': CHECKPOINTS[seen][0],
                    'action': CHECKPOINTS[seen][1]}
        assert {key: payload.get(key) for key in expected} == expected, \
            'Browser checkpoint sequence differs'
        ack = checkpoints / f'{seen + 1}.ack.json'
        try:
            receipt = on_checkpoint(payload)
            write_private_json(ack, {'success': True, **receipt})
        except BaseException as error:
            try:
                write_private_json(ack, {'success': False,
                    'error_type': type(error).__name__})
            except BaseException:
                pass
            raise
        seen += 1
    return seen


def state_digest(snapshot):
    payload = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def json_safe(value):
    """Normalize database values for a receipt without changing comparisons."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def cache_digest(snapshot):
    digest = hashlib.sha256()
    for key, value in sorted(snapshot.items()):
        digest.update(len(key).to_bytes(8, 'big'))
        digest.update(key)
        digest.update(b'\0' if value is None else (len(value).to_bytes(8, 'big') + value))
    return digest.hexdigest()


def exercise(proof, work, output, log, options, tool_env, report):
    import redis
    import uvicorn
    from django.apps import apps
    from django.contrib.auth.hashers import make_password
    from django.db import connections
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration
    from application.models.user_models import CollaborationInvites
    from instance.models import InstanceConfiguration
    from django.conf import settings

    password = 'private-collaborator-browser-password'
    proof.private_values.add(password)
    cache_password = 'synthetic-identity-only'
    raw = redis.Redis(host='127.0.0.1', port=proof.redis_port, db=1,
                      password=cache_password)
    now = datetime.now(timezone.utc)

    def snapshot():
        result = {model._meta.label: list(model._base_manager.order_by('pk').values())
                  for model in apps.get_models(include_auto_created=True)
                  if model._meta.managed and not model._meta.proxy}
        assert len(result) == EXPECTED_MANAGED_MODELS, 'Expected exactly 128 managed models'
        return result

    def cache_snapshot():
        return {key: raw.get(key) for key in raw.scan_iter()
                if not key.startswith(b'assozeta:go:readiness:')}

    runtime = {
        'CACHES': {'default': {'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
            'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                        'PASSWORD': cache_password}}},
        'CHANNEL_LAYERS': {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {'hosts': [f'redis://:{cache_password}@127.0.0.1:{proof.redis_port}/2']}}},
        'PASSWORD_HASHERS': ['django.contrib.auth.hashers.PBKDF2PasswordHasher'],
        'AI_API_KEY': '', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '',
        'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '', 'STRIPE_KEY': '',
        'STRIPE_PUBLIC_KEY': '', 'STRIPE_WEBHOOK_SECRET': '',
        'MANUAL_BUNDLE_REQUIRED': False, 'MANUAL_BUNDLE_PATH': str(work / 'absent-manual'),
        'MANUAL_CORPUS_BASE_URL': '', 'MANUAL_RUN_ID': '', 'MANUAL_SOURCE_ROOT': '',
    }
    server = thread = notification_pool = None
    import core.settings as core_settings
    assert 'notifications.utils' not in sys.modules, 'Bind private notification Redis before import'
    private_redis = dict(REDIS_HOST='127.0.0.1', REDIS_PORT=proof.redis_port,
                         REDIS_USERNAME='default', REDIS_PASSWORD=cache_password,
                         REDIS_SSLAUTH=False)
    try:
        # This legacy dependency imports core constants directly. Configure its
        # real private Redis before import, as in the impersonation browser fixture.
        with patch.multiple(core_settings, **private_redis), override_settings(**runtime, **private_redis):
            encoded = make_password(password)
            assert encoded.startswith('pbkdf2_sha256$')
            people, associations, invites = {}, {}, []

            def person(number, username, first, last, role, **values):
                record = User.original_objects.create(
                    user_id=UUID(uid(number)), username=username,
                    email=username.lower() + '@example.test', first_name=first, last_name=last,
                    role=role, password=encoded if username in (
                        'BROWSER-OWNER', 'BROWSER-RESTRICTED', 'BROWSER-DENIED', 'BROWSER-FOREIGN')
                        else '!private-unused',
                    dashboard_layout={'rows': []},
                    date_joined=now - timedelta(days=60), **values)
                return record

            with disable_auditlog():
                people['owner'] = person(1, 'BROWSER-OWNER', 'Nadia', 'Proprietaria', User.ASSOCIATION)
                people['foreign-owner'] = person(2, 'BROWSER-FOREIGN', 'Franca', 'Estranea', User.ASSOCIATION)
                people['elena'] = person(3, 'BROWSER-ELENA', 'Elena', 'Accenti', User.COLLABORATOR,
                    connected_user=people['owner'], collaborator_role=User.FULL)
                people['restricted'] = person(4, 'BROWSER-RESTRICTED', 'Rita', 'Riservata',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                    collaborator_permissions=['association.dashboard.read',
                                              'other.users.collaborators.read'])
                people['denied'] = person(5, 'BROWSER-DENIED', 'Dario', 'Negato',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                    collaborator_permissions=['association.dashboard.read'])
                people['inactive'] = person(6, 'BROWSER-INACTIVE', 'Ines', 'Inattiva',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                    collaborator_permissions=['association.dashboard.read'], is_active=False)
                people['legacy'] = person(7, 'BROWSER-LEGACY', 'Luca', 'Legacy',
                    User.ATHLETE, connected_user=people['owner'])
                people['accounting'] = person(8, 'BROWSER-ACCOUNTING', 'Nora', 'Nome',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.ONLY_ACCOUNTING, collaborator_permissions=[])
                people['untyped'] = person(9, 'BROWSER-UNTYPED', 'Omar', 'Altro',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                    collaborator_permissions=None)
                people['json'] = person(10, 'BROWSER-JSON', 'Paola', 'Permessi',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
                    collaborator_permissions={'nested': [True, 1, 1.0, 1.25,
                                                         9007199254740993, None]})
                people['staff'] = person(11, 'BROWSER-STAFF', 'Sara', 'Staff',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.FULL)
                people['deleted'] = person(12, 'BROWSER-DELETED', 'Dalia', 'Cancellata',
                    User.COLLABORATOR, connected_user=people['owner'],
                    collaborator_role=User.FULL, deleted=True)
                people['foreign-person'] = person(13, 'BROWSER-FRANCA', 'Franca', 'Foreign',
                    User.COLLABORATOR, connected_user=people['foreign-owner'],
                    collaborator_role=User.FULL)
                associations['owner'] = SportAssociation.original_objects.create(
                    sport_association_id=UUID(uid(100)), user=people['owner'],
                    denomination='Collaborator Browser ASD', tax_code='12345678901',
                    logo='/api/instance/logo.png', additional_sections=[])
                associations['foreign'] = SportAssociation.original_objects.create(
                    sport_association_id=UUID(uid(101)), user=people['foreign-owner'],
                    denomination='Foreign Browser ASD', tax_code='98765432109',
                    logo='/api/instance/logo.png', additional_sections=[])
                for association in associations.values():
                    SportAssociationMembershipCardConfiguration.objects.create(
                        sport_association=association)
                invitation_values = (
                    (200, people['owner'], 'pending@example.test',
                     now + timedelta(days=30), False, 'private-pending-invite', 3,
                     {'nested': [1, True, None]}),
                    (201, people['owner'], 'expired@example.test',
                     now - timedelta(days=30), False, 'private-expired-invite', 1, None),
                    (202, people['owner'], 'accepted@example.test',
                     None, True, None, 3, {}),
                    (203, people['foreign-owner'], 'foreign-pending@example.test',
                     now + timedelta(days=10), False, 'private-foreign-invite', 1, None),
                )
                for number, owner, email, expiration, accepted, token, role, grants in invitation_values:
                    invite = CollaborationInvites.objects.create(
                        collaboration_invite_id=UUID(uid(number)), user=owner, email=email,
                        expiration_date=expiration, accepted=accepted, token=token,
                        collaborator_role=role, collaborator_permissions=grants)
                    invites.append(invite)
                    if token:
                        proof.private_values.add(token)
                InstanceConfiguration.objects.create(
                    domain='collaborator-browser.example.test',
                    name='Collaborator Browser Fixture',
                    primary_association=associations['owner'],
                    setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_FRESH,
                    onboarding_completed_at=now,
                    support_email='support@example.test',
                    logo_path='/api/instance/logo.png',
                    display_settings={'login': {'allowOauthLogin': False},
                                      'general': {'cookieConsent': False}})
            logo = Path(settings.MEDIA_ROOT) / 'instance/logo.png'
            logo.parent.mkdir(parents=True, exist_ok=True)
            logo.write_bytes(base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM0lT3E'
                'wMDAxAAGAAyNARjvuuOSAAAAAElFTkSuQmCC'))

            owner_rows = [expected_user(people[name]) for name in (
                'elena', 'restricted', 'denied', 'inactive', 'legacy',
                'accounting', 'untyped', 'json', 'staff')]
            owner_rows += [expected_invite(invite, now)
                           for invite in invites if invite.user_id == people['owner'].pk]
            foreign_rows = [expected_user(people['foreign-person'])]
            foreign_rows += [expected_invite(invite, now)
                             for invite in invites
                             if invite.user_id == people['foreign-owner'].pk]
            assert len(owner_rows) == 12 and len(foreign_rows) == 2
            actors = {
                'owner': {'username': people['owner'].username,
                    'user_id': str(people['owner'].pk), 'expected_rows': owner_rows,
                    'expected_pages': [10, 2], 'action_mode': 'manage'},
                'restricted': {'username': people['restricted'].username,
                    'user_id': str(people['restricted'].pk),
                    'expected_rows': owner_rows, 'expected_pages': [10, 2],
                    'action_mode': 'read'},
                'foreign-owner': {'username': people['foreign-owner'].username,
                    'user_id': str(people['foreign-owner'].pk),
                    'expected_rows': foreign_rows, 'expected_pages': [2],
                    'action_mode': 'manage'},
                'denied': {'username': people['denied'].username,
                    'user_id': str(people['denied'].pk),
                    'expected_rows': owner_rows, 'expected_pages': [10, 2],
                    'action_mode': 'read'},
            }
            for name, actor in actors.items():
                other_rows = owner_rows if name == 'foreign-owner' else foreign_rows
                actor['forbidden_emails'] = [row['email'] for row in other_rows]
            report['fixture'] = {
                'supported_empty_dashboard_layout': True,
                'owner_visible_rows': len(owner_rows),
                'owner_user_rows': 9,
                'owner_invitation_rows': 3,
                'foreign_visible_rows': len(foreign_rows),
                'deleted_connected_user_excluded': True,
                'inactive_and_noncollaborator_users_included': True,
                'actors': {name: {'user_id': actor['user_id'],
                    'expected_row_count': len(actor['expected_rows'])}
                    for name, actor in actors.items()},
            }

            from notifications.services import NotificationService
            from notifications.utils import REDIS_POOL
            notification_pool = REDIS_POOL
            NotificationService.get_manager()
            for person_record in people.values():
                assert NotificationService.get_user_broadcasts(person_record) == ['all_notifications']
            report['notification_dependency'] = {
                'real_private_redis': True,
                'broadcast_cache_warmed_before_baseline': len(people),
                'notification_database': 0, 'django_cache_database': 1,
            }
            from core.asgi import application
            observer = DjangoObserver(application)
            server = uvicorn.Server(uvicorn.Config(
                observer, host='0.0.0.0', port=0, lifespan='off',
                log_level='critical', access_log=False))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            poll(lambda: server.started, bool, timeout=20)
            django_port = server.servers[0].sockets[0].getsockname()[1]
            django_base = f'http://127.0.0.1:{django_port}'
            state = snapshot()
            cache = cache_snapshot()

            login_receipt = Client(django_base).login({
                'username': people['owner'].username, 'password': password})[1]
            assert login_receipt['user_data']['user_id'] == str(people['owner'].pk)
            assert login_receipt['user_data']['requires_welcome'] is False
            assert login_receipt['user_data']['dashboard_layout'] == {'rows': []}
            after_login = snapshot()
            login_effect = assert_login_transition(state, after_login,
                                                   str(people['owner'].pk))
            after_login_cache = cache_snapshot()
            assert after_login_cache == cache, 'Real login changed Redis DB1 values'
            state, cache = after_login, after_login_cache
            report['misroute_login_baseline'] = {
                'ordinary_password_login': True,
                'legitimate_effect': json_safe(login_effect),
                'cache_unchanged': True,
            }

            with proof.server(work, log, tool_env,
                              f'127.0.0.1:{proof.redis_port}', versioned=True) as go_runtime:
                report['server_sha256'] = assert_server_binary_binding(
                    go_runtime, report['build_receipt_server_sha256'])
                report['server_binary_bound'] = True
                assert_private_docker_ownership(proof)
                assertion_token = login_receipt['access_token']
                with versioned_proxy(
                        f'host.docker.internal:{django_port}',
                        f'host.docker.internal:{django_port}', log,
                        network=proof.network, network_alias='collaborator-versioned'
                ) as (_, caddy_image):
                    observer.begin('misroute-v2-to-django')
                    state_before_read, cache_before_read = snapshot(), cache_snapshot()
                    with ui_gateway(proof, work, options.ui_dir, django_port,
                                    'collaborator-versioned:80', 'v2',
                                    'misroute-v2') as (misroute_origin, _):
                        status, body, headers = Client(
                            misroute_origin, token=assertion_token).request(
                                'GET', '/api/collaborators/list', include_headers=True)
                        assert status == 200
                        assert headers.get('X-Fixture-Backend') == 'go-v2'
                        assert canonical_rows(response_display_rows(body, now)) == \
                            canonical_rows(owner_rows), 'Misroute response was not equivalent'
                        unknown_status, _, unknown_headers = Client(
                            misroute_origin).request(
                                'GET', '/api/__unsupported-collaborator-browser-probe',
                                include_headers=True)
                        assert unknown_status == 502
                        assert unknown_headers.get('X-Fixture-Backend') == \
                            'unsupported-fixture-route'
                    observed = observer.snapshot('misroute-v2-to-django')
                    rejected = False
                    try:
                        assert observed['selected_count'] == 0
                    except AssertionError:
                        rejected = True
                    assert rejected and observed['selected_count'] == 1
                    assert observed['unknown_count'] == 0
                    assert snapshot() == state_before_read
                    assert cache_snapshot() == cache_before_read
                    state, cache = state_before_read, cache_before_read
                    report['routing_negative_control'] = {
                        'deliberately_misrouted_v2_to': 'real Django',
                        'misleading_gateway_header': 'go-v2',
                        'real_response_status': status,
                        'equivalent_display_payload': True,
                        'independent_django_observer_rejected': rejected,
                        'django_requests': observed,
                        'unknown_api_status': unknown_status,
                        'unknown_api_rejected_before_django': True,
                        'managed_state_unchanged': True,
                        'raw_cache_unchanged': True,
                    }

                with versioned_proxy(
                        f'host.docker.internal:{django_port}',
                        go_runtime['proxy_upstream'], log,
                        network=proof.network, network_alias='collaborator-versioned'
                ) as (_, caddy_image):
                    report['shipped_caddy_image'] = caddy_image
                    report['versions'] = {}
                    for version in ('v1', 'v2'):
                        observer.begin(version)
                        tracker = {'initial_state': state, 'initial_cache': cache,
                                   'state': state, 'cache': cache,
                                   'login_effects': [], 'read_checks': []}
                        target = output / version
                        target.mkdir(exist_ok=True)
                        for stale in ('browser.json', 'validation.json'):
                            (target / stale).unlink(missing_ok=True)
                        checkpoints = work / ('checkpoints-' + version)
                        checkpoints.mkdir(mode=0o700)
                        with ui_gateway(
                                proof, work, options.ui_dir, django_port,
                                'collaborator-versioned:80', version,
                                'ui-' + version) as (origin, routing):
                            fixture = {
                                'origin': origin, 'version': version,
                                'password': password, 'actors': actors,
                                'playwright_module': str(options.playwright_module),
                                'browser_cache_path': tool_env.get(
                                    'PLAYWRIGHT_BROWSERS_PATH'),
                                'selected_routes': list(SELECTED_ROUTES),
                                'coexistence_routes': list(COEXISTENCE_ROUTES),
                                'websocket_routes': list(WEBSOCKET_ROUTES),
                                'checkpoints': str(checkpoints),
                            }
                            fixture_path = work / (version + '-browser.json')
                            write_private_json(fixture_path, fixture)

                            def on_checkpoint(payload, tracker=tracker):
                                current, current_cache = snapshot(), cache_snapshot()
                                if payload['action'] == 'login':
                                    assert payload['context']['user_id'] == actors[
                                        payload['actor']]['user_id'], 'Login checkpoint actor differs'
                                    effect = assert_login_transition(
                                        tracker['state'], current,
                                        payload['context']['user_id'])
                                    assert current_cache == tracker['cache'], \
                                        'Login changed Redis DB1 values'
                                    tracker['login_effects'].append({
                                        'actor': payload['actor'], **effect})
                                else:
                                    assert_read_baseline(tracker['state'], current)
                                    assert current_cache == tracker['cache'], \
                                        'Read phase changed raw Redis DB1 values'
                                    tracker['read_checks'].append({
                                        'actor': payload['actor'],
                                        'action': payload['action'],
                                        'managed_models': len(current),
                                        'managed_state_sha256': state_digest(current),
                                        'cache_keys': len(current_cache),
                                        'cache_sha256': cache_digest(current_cache),
                                    })
                                tracker['state'], tracker['cache'] = current, current_cache
                                return {'managed_models': len(current),
                                        'cache_keys': len(current_cache)}

                            process = subprocess.Popen(
                                ['node', str(ROOT / 'TESTS/browser/collaborator-list.mjs')],
                                cwd=ROOT,
                                env={**tool_env, 'TESTS_FIXTURE': str(fixture_path),
                                     'TESTS_OUTPUT': str(target)},
                                stdout=log, stderr=subprocess.STDOUT)
                            try:
                                drive_browser(process, checkpoints, on_checkpoint)
                                process.wait(timeout=30)
                            except BaseException:
                                if process.poll() is None:
                                    process.terminate()
                                    try:
                                        process.wait(timeout=10)
                                    except subprocess.TimeoutExpired:
                                        process.kill()
                                        process.wait(timeout=5)
                                raise
                            finally:
                                fixture_path.unlink(missing_ok=True)
                            assert process.returncode == 0, 'Browser consumer failed'
                            browser = json.loads((target / 'browser.json').read_text())
                            assert_browser_acceptance(browser, actors)
                            observed = observer.snapshot(version)
                            observer.assert_no_unknown(version)
                            list_responses = [row for row in browser['responses']
                                              if row['path'] == '/api/collaborators/list']
                            assert len(list_responses) == EXPECTED_LIST_REQUESTS
                            if version == 'v1':
                                assert observed['selected_count'] == EXPECTED_LIST_REQUESTS
                                assert [row['status'] for row in observed['selected']].count(
                                    200) == 6
                                assert [row['status'] for row in observed['selected']].count(
                                    403) == 1
                            else:
                                assert observed['selected_count'] == 0, \
                                    'v2 collaborator list reached real Django'
                            assert len(tracker['login_effects']) == 4
                            assert len(tracker['read_checks']) == 7
                            final_state = snapshot()
                            final_cache = cache_snapshot()
                            final_projection = assert_final_login_projection(
                                tracker['initial_state'], final_state,
                                tracker['login_effects'], LOGIN_ACTORS)
                            assert final_cache == tracker['initial_cache'], \
                                'Browser run changed raw Redis DB1 values outside login checks'
                            validation = {
                                'success': True,
                                'routing': routing,
                                'browser': str((target / 'browser.json').relative_to(output)),
                                'ordinary_password_logins': 4,
                                'exact_displayed_rows_per_actor': {
                                    actor: len(data['expected_rows'])
                                    for actor, data in actors.items()},
                                'pagination_and_reload_verified': True,
                                'protected_denial_via_unchanged_ui': True,
                                'collaborator_list_requests': EXPECTED_LIST_REQUESTS,
                                'independent_django_observer': observed,
                                'login_effects': json_safe(tracker['login_effects']),
                                'final_login_projection': final_projection,
                                'read_state_checks': tracker['read_checks'],
                                'managed_models': EXPECTED_MANAGED_MODELS,
                                'raw_cache_exact_except_readiness_namespace': True,
                                'browser_contexts_and_process_closed': True,
                                'mutations_clicked': False,
                            }
                            (target / 'validation.json').write_text(
                                json.dumps(validation, indent=2) + '\n')
                            report['versions'][version] = validation
                            # Advance the cross-version baseline only after the
                            # complete projection and raw-cache check above passes.
                            state, cache = final_state, final_cache
                            print('PASS: ' + version +
                                  ' unchanged collaborator list UI with exact read state',
                                  flush=True)
    finally:
        try:
            if server:
                server.should_exit = True
            if thread:
                thread.join(timeout=30)
                assert not thread.is_alive(), 'Django ASGI thread did not stop'
        finally:
            try:
                connections.close_all()
            finally:
                try:
                    raw.close()
                finally:
                    if notification_pool is not None:
                        notification_pool.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-dir', type=Path, required=True)
    parser.add_argument('--ui-provenance', type=Path, required=True)
    parser.add_argument('--prepare-ui-image')
    parser.add_argument('--image', help='Existing immutable Linux Go server image')
    parser.add_argument('--expected-revision')
    parser.add_argument('--build-receipt', type=Path)
    parser.add_argument('--go-source', type=Path, default=ROOT)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--playwright-module', type=Path,
                        default=ROOT / 'selfhost/tests/browser/node_modules/@playwright/test/index.mjs')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'quality-reports/go-migration/collaborator-browser')
    options = parser.parse_args()
    options.ui_dir = options.ui_dir.resolve()
    options.go_source = options.go_source.resolve()
    options.playwright_module = options.playwright_module.resolve()
    if options.prepare_ui_image:
        prepare_ui(options.prepare_ui_image, options.ui_dir,
                   options.ui_provenance.resolve(), WORKFLOW_SOURCES)
        return
    require_production_python()
    assert options.build_receipt, 'Source-bound image build receipt is required for acceptance'
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', options.image or ''), 'Immutable Go image ID required'
    assert options.image and options.expected_revision, \
        'Existing immutable image and expected revision are required'
    assert re.fullmatch(r'[0-9a-f]{40}', options.expected_revision)
    assert (options.ui_dir / 'index.html').is_file()
    assert options.playwright_module.is_file()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    provenance_bytes = options.ui_provenance.read_bytes()
    provenance = json.loads(provenance_bytes)
    ui_hashes = fingerprints(options.ui_dir)
    assert provenance['success'] and provenance['extraction_container_never_started']
    assert provenance['extraction_container_removed']
    assert provenance['files'] == ui_hashes, 'UI artifact differs from prepared provenance'
    for source in WORKFLOW_SOURCES:
        matches = provenance['workflow_source_maps'].get(source)
        assert matches, 'Missing workflow source map: ' + source
        current = hashlib.sha256((ROOT / 'UI' / source).read_bytes()).hexdigest()
        assert all(item['embedded_sha256'] == current for item in matches)
    tool_env = capture_tool_environment()
    sources, go_sources = source_hashes(options.go_source)
    report = {
        'success': False, 'scope': __doc__.strip(),
        'cleanup_verified': False, 'private_directory_removed': False,
        'complete_collaborator_parity': False,
        'build_receipt_bound': False,
        'server_binary_bound': False,
        'runtime_ui_build_reused': {
            'image_id': provenance['image_id'],
            'artifact_files': len(ui_hashes),
            'artifact_sha256': hashlib.sha256(
                json.dumps(ui_hashes, sort_keys=True).encode()).hexdigest(),
            'provenance_sha256': hashlib.sha256(provenance_bytes).hexdigest(),
            'full_fingerprints_match': True,
            'source_maps_match': True,
        },
        'source_sha256': sources, 'go_source_sha256': go_sources,
        'source_file_count': len(sources), 'go_source_file_count': len(go_sources),
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    report_path = output / 'results.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    build = None
    proof = None
    work = None
    cleanup_errors = []
    try:
        if options.build_receipt:
            build = json.loads(options.build_receipt.read_text())
            assert build['success'] and build['source_stable']
            assert build['source_revision'] == options.expected_revision
            assert build['source_sha256'] == go_sources, \
                'Go build receipt differs from selected source'
            report['build_receipt_server_sha256'] = require_server_digest(
                build.get('server_sha256'))
            report['build_receipt_bound'] = True
        with tempfile.TemporaryDirectory(
                prefix='az-collaborator-browser-', dir='/tmp') as directory, \
                (output / 'services.log').open('w') as log:
            work = Path(directory)
            proof = CollaboratorBrowserFixture(
                options.image, options.expected_revision, work, output, log)
            try:
                proof.start_services(options.postgres, options.redis)
                assert_private_docker_ownership(proof)
                if build:
                    assert proof.image_id == build['image_id'], \
                        'Immutable image differs from build receipt'
                report['image'] = proof.metadata
                initialize_current_schema(work, '127.0.0.1', proof.postgres_port,
                                          DATABASE, log)
                exercise(proof, work, output, log, options, tool_env, report)
                report['checks_passed'] = True
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                except BaseException as error:
                    cleanup_errors.append({
                        'stage': 'database_connections',
                        'error_type': type(error).__name__})
                try:
                    proof.cleanup()
                except BaseException as error:
                    cleanup_errors.append({
                        'stage': 'owned_containers_network',
                        'error_type': type(error).__name__})
        if cleanup_errors:
            raise RuntimeError('Packaged collaborator browser cleanup failed')
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        current_sources, current_go = source_hashes(options.go_source)
        report.update(
            cleanup_errors=cleanup_errors,
            cleanup_verified=bool(proof and proof.cleanup_verified
                                  and not cleanup_errors),
            private_directory_removed=bool(work and not work.exists()),
            source_stable=current_sources == sources and current_go == go_sources,
            ui_stable=fingerprints(options.ui_dir) == ui_hashes,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        report['success'] = bool(
            report.get('checks_passed') and report['cleanup_verified']
            and report['private_directory_removed'] and report['source_stable']
            and report['ui_stable'] and report['build_receipt_bound']
            and report['server_binary_bound'] and 'error_type' not in report)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
    assert report['success'], 'Collaborator browser acceptance incomplete'
    print(json.dumps({'success': True, 'image': proof.image_id,
                      'versions': ['v1', 'v2'], 'cleanup_verified': True}))


if __name__ == '__main__':
    main()
