"""Parent-executed browser acceptance. Importing this module has no runtime effects.

No build, dependency fetch, Docker, response replay, shared fixture edits or ledger
writes. See the owned progress report for the input schemas and live limitations.
"""
import argparse
import base64
import copy
from contextlib import contextmanager, ExitStack, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import select
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from urllib.parse import unquote, urlsplit

from TESTS.adapters.django import message_reads_browser as browser
from TESTS.adapters.django import message_reads_runtime as runtime

ROOT = browser.ROOT
NODE_RUNNER = 'TESTS/browser/message-reads-runner.mjs'
OWNED = (
    'TESTS/adapters/django/message_browser_build.py',
    'TESTS/test_message_browser_build.py',
    'TESTS/adapters/django/message_reads_browser_runtime.py',
    NODE_RUNNER,
    # The shared bridge statically imports the inert mutation diagnostic vocabulary.
    'TESTS/browser/message-mutations.mjs',
    'TESTS/browser/message-mutations.test.mjs',
    'TESTS/test_message_reads_browser_runtime.py',
)
MAX_FRAME = 4 * 1024 * 1024
MAX_BODY = 32 * 1024 * 1024
FIXTURE_PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM0lT3EwMDAxAAGAAyNARjvuuOSAAAAAElFTkSuQmCC'
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate',
               'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}
CHILD_FAILURE_CODES = {
    'startup': 'startup-failed', 'browser-ready': 'parent-ready-failed',
    'browser-launch': 'browser-launch-failed', 'transport-probe': 'transport-probe-failed',
    'browser-exercise': 'browser-exercise-failed', 'browser-close': 'browser-close-failed',
    'binary-after': 'browser-binary-drift',
}
CHILD_ERROR_CLASSES = {'AssertionError', 'Error', 'TypeError', 'RangeError',
                       'SyntaxError', 'TimeoutError', 'ReferenceError', 'URIError', 'Other'}
EXERCISE_FAILURE_KINDS = {'unexpected-request-or-redirect', 'unexpected-list-request',
    'requestfailed', 'response-or-route-validation', 'unexpected-websocket', 'pageerror',
    'unexpected-download', 'unexpected-popup', 'workflow-assertion', 'context-close',
    'incomplete-cleanup', 'invalid-exercise-failure'}
WORKFLOW_STEPS = {'context-initialization', 'login-page-navigation', 'login-form',
    'login-session-observation', 'login-response-status', 'login-response-header',
    'login-response-body', 'login-identity', 'login-onboarding',
    'login-contract-complete', 'login-navigation', 'dashboard-visible', 'login-notice',
    'login-network-idle', 'response-observation', 'checkpoint', 'context-close', 'page-navigation',
    'sidebar-navigation', 'list-navigation', 'list-status', 'list-header', 'list-body',
    'rendered-header', 'rendered-row-count', 'rendered-row-contents', 'rendered-row-comparison',
    'forbidden-row-count', 'controls-headings', 'search', 'search-empty', 'search-clear',
    'sort-ascending', 'sort-descending', 'sort-reset', 'page-size', 'page-controls',
    'controls-comparison', 'reread', 'reload', 'final-response-counts', 'denied-navigation'}
CONTROL_FIELDS = {'headings', 'search_query', 'search_ids', 'search_empty', 'sort_field',
                  'sort_asc_ids', 'sort_desc_ids', 'page_sizes', 'pages', 'next_disabled',
                  'previous_disabled', 'summary', 'list_requests_during_controls'}
RESPONSE_CHECKS = {'classification', 'service-worker', 'redirect', 'backend-header',
                  'static-status', 'static-body', 'api-status', 'body-completion',
                  'invalid-response-diagnostic'}
ROUTE_STAGES = {'fetch', 'observe', 'fulfill', 'dispose', 'abort', 'requestfailed'}
NETWORK_CATEGORIES = {'other', 'redirect-blocked', 'response-disposed', 'target-closed',
    'context-disposed', 'route-already-handled', 'browser-aborted', 'blocked-by-client',
    'network-failed', 'connection-reset', 'connection-refused', 'socket-hang-up', 'timeout', 'upstream-aborted'}
RESOURCE_TYPES = {'document', 'stylesheet', 'image', 'media', 'font', 'script', 'texttrack',
                  'xhr', 'fetch', 'eventsource', 'websocket', 'manifest', 'other'}
NAVIGATION_STATES = {'unobserved', 'login', 'root', 'messages', 'denied', 'other'}


# Separate mutation vocabulary; the existing read diagnostic schema stays closed.
MUTATION_WORKFLOW_STEPS = {
    'mutation-initialization', 'mutation-input-validation', 'mutation-context-initialization',
    'mutation-context-routing', 'mutation-page-initialization', 'mutation-login-navigation',
    'mutation-login-form', 'mutation-login-submit', 'mutation-login-redirect',
    'mutation-login-dashboard', 'mutation-login-session', 'mutation-login-notice',
    'mutation-login-network-idle', 'mutation-login-contract',
    'mutation-restricted-create-absence', 'mutation-restricted-delete-disabled',
    'mutation-restricted-mutation-absence',
    'mutation-sidebar-navigation', 'mutation-list-navigation', 'mutation-list-body',
    'mutation-list-loading', 'mutation-list-page-size', 'mutation-list-row-count',
    'mutation-list-row-contents', 'mutation-list-forbidden-absence', 'mutation-list-controls',
    'mutation-create-open', 'mutation-create-form', 'mutation-create-submit',
    'mutation-created-row', 'mutation-create-reload', 'mutation-created-row-persistence',
    'mutation-delete-open', 'mutation-delete-confirmation', 'mutation-delete-submit',
    'mutation-deleted-row-absence', 'mutation-delete-reload',
    'mutation-barrier-drain', 'mutation-checkpoint', 'mutation-context-close',
    'mutation-final-counts', 'mutation-cleanup-drain',
}
MUTATION_ACTIONS = {'initialization', 'login', 'read', 'create', 'create_reload',
                    'delete', 'delete_reload', 'closed'}
MUTATION_FAILURE_KINDS = {'invalid-mutation-diagnostic', 'unexpected-request',
    *('unexpected-request-' + category for category in
      ('fragment', 'redirect', 'origin', 'query', 'list', 'login', 'api', 'static', 'page')),
    'unexpected-websocket', 'request-failed', 'request-or-response-failed', 'pageerror',
    'download', 'popup', 'workflow-failed', 'context-close-failed', 'incomplete-cleanup',
    'route-redirect-blocked',
    *('route-' + stage + '-failed' for stage in ('fetch', 'observe', 'fulfill', 'dispose', 'abort', 'headers')),
    *('response-' + check + '-failed' for check in ('pairing', 'classification', 'body-completion',
        'backend', 'static-status', 'static-body', 'api-status', 'content-type', 'json', 'mutation-body'))}


def validate_mutation_diagnostics(value):
    code = 'browser-mutation-diagnostic-protocol'
    def location(row):
        return (isinstance(row, dict) and set(row) == {'actor', 'action', 'step'} and
                isinstance(row['step'], str) and row['step'] in MUTATION_WORKFLOW_STEPS and
                isinstance(row['action'], str) and row['action'] in MUTATION_ACTIONS and
                ((row['actor'] is None and row['action'] == 'initialization') or
                 (isinstance(row['actor'], str) and row['actor'] in browser.ACTORS and
                  row['action'] != 'initialization' and
                  (row['action'] in {'login', 'read', 'closed'} if row['actor'] == 'restricted_collaborator'
                   else True))))
    require(isinstance(value, dict) and set(value) ==
            {'workflow_failure', 'failure_count', 'failures_truncated', 'failures'}, code)
    count = value['failure_count']
    require(location(value['workflow_failure']) and type(count) is int and 1 <= count <= 1000000 and
            type(value['failures_truncated']) is bool and value['failures_truncated'] == (count > 16) and
            isinstance(value['failures'], list) and len(value['failures']) == min(count, 16), code)
    for row in value['failures']:
        require(isinstance(row, dict) and set(row) == {'kind', 'actor', 'action', 'step'} and
                isinstance(row['kind'], str) and row['kind'] in MUTATION_FAILURE_KINDS and
                location({key: row[key] for key in ('actor', 'action', 'step')}), code)
    require(value['workflow_failure'] == {key: value['failures'][0][key]
                                        for key in ('actor', 'action', 'step')}, code)
    return copy.deepcopy(value)


def validate_network_diagnostics(value):
    code = 'browser-network-diagnostic-protocol'
    integer = lambda item: type(item) is int and 0 <= item <= 1000000
    require(isinstance(value, dict) and set(value) ==
            {'failure_count', 'failures_truncated', 'failures', 'counts'}, code)
    count = value['failure_count']
    require(integer(count) and type(value['failures_truncated']) is bool and
            value['failures_truncated'] == (count > 16) and isinstance(value['failures'], list) and
            len(value['failures']) == min(count, 16) and isinstance(value['counts'], dict) and
            set(value['counts']) == ROUTE_STAGES, code)
    for row in value['counts'].values():
        require(isinstance(row, dict) and set(row) == {'completed', 'failed'} and
                all(integer(item) for item in row.values()), code)
    require(count == min(sum(row['failed'] for row in value['counts'].values()), 1000000), code)
    numbers = {'request_id', 'request_event', 'request_navigation_epoch', 'navigation_epoch',
               'navigation_request_id', 'navigation_event', 'event'}
    bools = {'fetch_complete', 'fulfill_complete', 'page_closed', 'context_closing'}
    keys = numbers | bools | {'kind', 'target', 'method', 'resource_type', 'actor', 'action',
                              'navigation_state', 'stage', 'category'}
    previous_event = 0
    for row in value['failures']:
        require(isinstance(row, dict) and set(row) == keys and
                all(integer(row[key]) for key in numbers) and
                all(type(row[key]) is bool for key in bools), code)
        require(all(isinstance(row[key], str) for key in
                    ('actor', 'action', 'method', 'resource_type', 'navigation_state', 'stage', 'category', 'kind')) and
                row['actor'] in browser.ACTORS and row['action'] in {'initialization', 'login', 'read', 'reload', 'denied'} and
                row['method'] in {'GET', 'POST', 'OTHER'} and row['resource_type'] in RESOURCE_TYPES and
                row['navigation_state'] in NAVIGATION_STATES and row['stage'] in ROUTE_STAGES and
                row['category'] in NETWORK_CATEGORIES, code)
        require((row['kind'] == 'unknown' and row['target'] is None) or
                (row['kind'] == 'static' and isinstance(row['target'], str) and
                 re.fullmatch(r'[a-f0-9]{64}', row['target'])) or
                (row['kind'] in {'selected', 'coexistence'} and isinstance(row['target'], str) and
                 row['target'] in (browser.SELECTED_ROUTES | browser.COEXISTENCE_ROUTES)), code)
        require(0 < row['request_id'] and 0 < row['request_event'] < row['event'] and
                previous_event < row['event'] and row['request_navigation_epoch'] <= row['navigation_epoch'] and
                row['navigation_event'] < row['event'], code)
        previous_event = row['event']
    return copy.deepcopy(value)


class HarnessError(RuntimeError):
    def __init__(self, code, child_failure=None):
        self.code = code
        self.child_failure = child_failure
        super().__init__(code)


def require(condition, code):
    # Unlike assert, these containment checks remain enabled under python -O.
    if not condition:
        raise HarnessError(code)


def public_error(stage, error):
    return {'stage': stage, 'type': type(error).__name__,
            **({'code': error.code} if isinstance(error, HarnessError) else {}),
            **({'child_failure': error.child_failure} if isinstance(error, HarnessError)
               and error.child_failure is not None else {})}


def validate_child_failure(frame, *, consumer='reads'):
    base = {'type', 'stage', 'code', 'error_class'}
    require(consumer in ('reads', 'mutations'), 'browser-failure-protocol')
    if isinstance(frame, dict) and 'mutation_diagnostics' in frame:
        require(consumer == 'mutations' and set(frame) == base | {'mutation_diagnostics'} and
                frame['type'] == 'failure' and frame['stage'] == 'browser-exercise' and
                frame['code'] == CHILD_FAILURE_CODES['browser-exercise'] and
                isinstance(frame['error_class'], str) and frame['error_class'] in CHILD_ERROR_CLASSES,
                'browser-failure-protocol')
        return {key: frame[key] for key in ('stage', 'code', 'error_class')} | {
            'mutation_diagnostics': validate_mutation_diagnostics(frame['mutation_diagnostics'])}
    require(consumer == 'reads' or isinstance(frame, dict) and set(frame) == base,
            'browser-failure-protocol')
    diagnostics = {'workflow_step', 'exercise_failures'}
    extended = diagnostics | {'failure_count', 'failures_truncated', 'response_checks'}
    keys = set(frame) - {'controls_mismatches'} if isinstance(frame, dict) else set()
    require(isinstance(frame, dict) and (set(frame) == base or
            (keys in (base | diagnostics, base | extended, base | extended | {'network_diagnostics'}) and
             frame['stage'] == 'browser-exercise')) and
            frame['type'] == 'failure' and isinstance(frame['stage'], str) and
            isinstance(frame['error_class'], str) and frame['stage'] in CHILD_FAILURE_CODES and
            frame['code'] == CHILD_FAILURE_CODES[frame['stage']] and
            frame['error_class'] in CHILD_ERROR_CLASSES, 'browser-failure-protocol')
    result = {key: frame[key] for key in ('stage', 'code', 'error_class')}
    if 'workflow_step' in frame:
        rows = frame['exercise_failures']
        require(isinstance(frame['workflow_step'], str) and frame['workflow_step'] in WORKFLOW_STEPS and
                isinstance(rows, list) and 1 <= len(rows) <= 16, 'browser-exercise-diagnostic-protocol')
        for row in rows:
            require(isinstance(row, dict) and set(row) == {'kind', 'actor', 'action', 'error_class'} and
                    isinstance(row['kind'], str) and row['kind'] in EXERCISE_FAILURE_KINDS and
                    isinstance(row['action'], str) and row['action'] in
                    {'initialization', 'login', 'read', 'reload', 'denied'} and
                    (row['actor'] in browser.ACTORS or
                     (row['actor'] is None and row['action'] == 'initialization')) and
                    isinstance(row['error_class'], str) and row['error_class'] in CHILD_ERROR_CLASSES,
                    'browser-exercise-diagnostic-protocol')
        result.update(workflow_step=frame['workflow_step'], exercise_failures=copy.deepcopy(rows))
        if 'controls_mismatches' in frame:
            fields = frame['controls_mismatches']
            require(isinstance(fields, list) and len(fields) <= len(CONTROL_FIELDS) and
                    all(isinstance(field, str) and field in CONTROL_FIELDS for field in fields) and
                    len(set(fields)) == len(fields) and
                    (not fields or frame['workflow_step'] == 'controls-comparison'),
                    'browser-exercise-diagnostic-protocol')
            result['controls_mismatches'] = list(fields)
        if 'failure_count' in frame:
            count, truncated, checks = frame['failure_count'], frame['failures_truncated'], frame['response_checks']
            require(type(count) is int and 0 <= count <= 1000000 and type(truncated) is bool and
                    truncated == (count > 16) and isinstance(checks, list) and len(checks) <= 32,
                    'browser-exercise-diagnostic-protocol')
            for row in checks:
                require(isinstance(row, dict) and set(row) == {'actor', 'check'} and
                        isinstance(row['check'], str) and row['check'] in RESPONSE_CHECKS and
                        (row['actor'] in browser.ACTORS or
                         (row['actor'] is None and row['check'] == 'invalid-response-diagnostic')),
                        'browser-exercise-diagnostic-protocol')
            result.update(failure_count=count, failures_truncated=truncated, response_checks=copy.deepcopy(checks))
        if 'network_diagnostics' in frame:
            result['network_diagnostics'] = validate_network_diagnostics(frame['network_diagnostics'])
    return result


def cleanup_all(operations, code):
    failed = False
    for operation in operations:
        try:
            operation()
        except BaseException:
            failed = True
    require(not failed, code)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def required_login_cookie(values):
    matching = [value for value in values if value.startswith('BKN_AUTH=')]
    if len(matching) != 1:
        return False
    first, *parts = matching[0].split(';')
    if not re.fullmatch(r'BKN_AUTH=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', first):
        return False
    attributes = {}
    for part in parts:
        name, _, value = part.strip().partition('=')
        attributes[name.lower()] = value
    return ('httponly' in attributes and 'secure' in attributes and attributes.get('path') == '/' and
            attributes.get('samesite', '').lower() == 'strict' and
            re.fullmatch(r'[1-9][0-9]*', attributes.get('max-age', '')) is not None)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate-json-key')
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_object)


def private_write(path, data):
    """Exclusive creation; never follow links or briefly expose credentials."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(data)


def fresh_output(path):
    path = Path(path).absolute()
    require(not path.exists() and not path.is_symlink(), 'output-must-be-fresh')
    path.mkdir(mode=0o700, parents=False)
    return path


def validate_build_record(path, provenance):
    """Hash the actual independent record, never trust a supplied record digest."""
    data = read_json(path)
    require(set(data) == {'invocation', 'source_sha256', 'artifact_sha256', 'record_sha256'},
            'build-record-schema')
    record_path = Path(path).with_name('build-record.json')
    require(data['record_sha256'] == sha(record_path), 'raw-build-record-digest')
    record = read_json(record_path)
    require(record.get('success') is True and record.get('exit_code') == 0 and
            record.get('source_unchanged') is True and record.get('build_inputs_unchanged') is True,
            'unsuccessful-build-record')
    require(all(record.get(key) == data[key] for key in
                ('invocation', 'source_sha256', 'artifact_sha256')), 'raw-build-record-binding')
    require(isinstance(data['invocation'], list) and data['invocation'] and
            all(isinstance(arg, str) and arg and '\x00' not in arg
                for arg in data['invocation']), 'build-invocation')
    require(data['source_sha256'] == {k: v for k, v in provenance['source_sha256'].items()
                                     if k.startswith('UI/')}, 'build-source-mismatch')
    require(data['artifact_sha256'] == provenance['artifact_sha256'], 'build-output-mismatch')
    return data


def native_source_hashes(root=ROOT):
    """Conservative compiler-input projection; unrelated HTTP oracles excluded."""
    root = Path(root)
    paths = {path for path in (root / 'BE_GO').rglob('*') if path.is_file() and
             ((path.suffix == '.go' and not path.name.endswith('_test.go')) or
              path.suffix in {'.c', '.h', '.mod', '.sum', '.sql', '.pem'})}
    paths.update(runtime.go_embedded_inputs(root))
    paths.update(root / name for name in (
        'TESTS/adapters/go/server.py', 'TESTS/adapters/django/login_jpeg.py'))
    require(paths, 'native-build-inputs-empty')
    return {str(path.relative_to(root)): sha(path) for path in sorted(paths)}


def validate_go_build_record(path, executable, codec, *, root=ROOT):
    record = read_json(path)
    require(set(record) == {'success', 'source_stable', 'source_sha256', 'artifacts', 'builder'} and
            record['success'] is True and record['source_stable'] is True and
            record['builder'] == 'TESTS.adapters.go.server.build_server', 'native-build-record-schema')
    inputs = native_source_hashes(root)
    require(isinstance(record['source_sha256'], dict), 'native-build-source-map')
    recorded_inputs = {name: value for name, value in record['source_sha256'].items()
        if name in ('TESTS/adapters/go/server.py', 'TESTS/adapters/django/login_jpeg.py') or
        (name.startswith('BE_GO/') and name != 'BE_GO/Dockerfile' and not name.endswith('_test.go'))}
    require(recorded_inputs == inputs,
            'native-build-source-mismatch')
    artifacts = {'server': sha(executable), 'image-codec': sha(codec)}
    require(record['artifacts'] == artifacts, 'native-build-artifact-mismatch')
    return {'record_sha256': sha(path), 'source_sha256': inputs, 'artifacts': artifacts,
            'builder': record['builder'], 'binding_scope': 'go-and-codec-source-projection',
            'old_full_source_map_revalidated': False}


def source_hashes():
    result = runtime.source_hashes()
    result.update({name: sha(ROOT / name) for name in (*OWNED, *browser.required_source_paths())})
    result['TESTS/test_message_reads_manifest.py'] = sha(ROOT / 'TESTS/test_message_reads_manifest.py')
    return result


def module_fingerprints(directory):
    directory = Path(directory).resolve()
    data = read_json(directory / 'package.json')
    require(data.get('name') == '@playwright/test' and
            re.fullmatch(r'\d+\.\d+\.\d+', data.get('version', '')), 'pinned-playwright-required')
    # Include the actual implementations, not just the facade's package version.
    packages = [directory, directory.parent.parent / 'playwright',
                directory.parent.parent / 'playwright-core']
    require(all(path.is_dir() for path in packages), 'playwright-layout-required')
    result = {}
    for package in packages:
        require(read_json(package / 'package.json')['version'] == data['version'],
                'playwright-package-version-mismatch')
        for path in sorted(package.rglob('*')):
            require(not path.is_symlink(), 'playwright-package-symlink')
            if path.is_file():
                result[package.name + '/' + str(path.relative_to(package))] = sha(path)
    # Public receipts sort JSON keys. Canonicalize only this file-map producer;
    # json_digest also hashes evidence where sequence/order is significant.
    return {'version': data['version'], 'files': dict(sorted(result.items()))}


def consumer_routes(consumer='reads'):
    require(consumer in ('reads', 'mutations', 'mutation-denials'), 'unknown-browser-consumer')
    if consumer == 'mutation-denials':
        from TESTS.adapters.django import message_mutations_browser as mutations
        return {'/api' + case['path']: case['method']
                for case in mutations.authored_spec()['denials']}
    selected = dict(browser.SELECTED_ROUTES)
    if consumer == 'mutations':
        from TESTS.adapters.django import message_mutations_browser as mutations
        selected['/api/communications/messages/add'] = 'POST'
        spec = mutations.authored_spec()
        for actor in mutations.READERS:
            selected['/api' + spec['actors'][actor]['delete']['path']] = 'DELETE'
    return selected


def correlated_route(path, consumer='reads'):
    selected = consumer_routes(consumer)
    if consumer == 'mutation-denials':
        return '/api' + path in selected
    return path == browser.UPSTREAM_PATH or (consumer == 'mutations' and
        (path == '/oauth2/login' or selected.get('/api' + path) in ('POST', 'DELETE')))


def validate_correlation(cid, route, consumer='reads'):
    consumer_routes(consumer)
    if consumer == 'mutation-denials':
        from TESTS.adapters.django import message_mutations_browser as mutations
        version = {'django-v1': 'v1', 'go-v2': 'v2'}.get(route.get('backend'), r'v[12]')
        # Bind the actor/action to an authored denial on this exact route.
        # Preserve the existing mutation CID format, without allowing UI actions.
        patterns = [f'message-mutations-{version}-{case["actor"]}-'
                    + ('create' if case['method'] == 'POST' else 'delete') + r'-[1-9][0-9]*'
                    for case in mutations.authored_spec()['denials'] if case['path'] == route['path']]
        require(bool(cid) and any(re.fullmatch(pattern, cid) for pattern in patterns),
                'invalid-denial-correlation')
        return
    pattern = (r'message-browser-v[12]-(owner|full_collaborator|foreign_owner)-(read|reload)-[1-9][0-9]*'
        if consumer == 'reads' else
        r'message-mutations-v[12]-(owner|full_collaborator|foreign_owner|restricted_collaborator)-'
        r'(login|read|create|create_reload|delete|delete_reload)-[1-9][0-9]*')
    require(not cid or re.fullmatch(pattern, cid), 'invalid-correlation')
    require(bool(cid) == correlated_route(route['path'], consumer), 'missing-or-extra-correlation')


def route_target(target, method, *, version, artifacts, upgrade=False, consumer='reads'):
    """Pure exact-path policy; reject ambiguous decoding and all SPA fallbacks."""
    selected = consumer_routes(consumer)
    require(version in ('v1', 'v2'), 'route-version')
    require(isinstance(target, str) and target.startswith('/') and
            not target.startswith('//') and all(ord(c) > 32 and ord(c) < 127 for c in target),
            'invalid-request-target')
    parsed = urlsplit(target)
    require(not parsed.scheme and not parsed.netloc and not parsed.fragment,
            'nonlocal-request-target')
    require(not re.search(r'%(?![0-9a-fA-F]{2})', parsed.path), 'invalid-path-escape')
    path = unquote(parsed.path, errors='strict')
    parts = path.split('/')[1:]
    if path == '/' or (upgrade and path in browser.WEBSOCKET_ROUTES):
        parts = parts[:-1]
    require('\\' not in path and '%' not in path and '\x00' not in path and
            not any(part in ('.', '..', '') for part in parts), 'ambiguous-path')
    require(path == parsed.path, 'encoded-path-not-allowed')
    if consumer == 'mutation-denials':
        # Python-only HTTP companion: no bootstrap, assets, queries or tunnels,
        # even if a caller supplies a nonempty artifact allowlist.
        require(not upgrade and '?' not in target and '#' not in target and path in selected,
                'denial-route-not-allowed')
    if upgrade:
        require(method == 'GET' and path in browser.WEBSOCKET_ROUTES, 'websocket-not-allowed')
        require(not parsed.query or (path == '/ws/notifications/' and re.fullmatch(
            r'token=[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', parsed.query)),
            'websocket-query-not-allowed')
        return {'kind': 'websocket', 'backend': 'django-websocket', 'path': path}
    require(path not in browser.WEBSOCKET_ROUTES, 'websocket-upgrade-required')
    if path in selected:
        require(method == selected[path], 'selected-method-not-allowed')
        require(not parsed.query, 'selected-query-not-allowed')
        return {'kind': 'selected', 'backend': 'django-v1' if version == 'v1' else 'go-v2',
                'path': path.removeprefix('/api')}
    if path in browser.COEXISTENCE_ROUTES:
        require(method == browser.COEXISTENCE_ROUTES[path], 'coexistence-method-not-allowed')
        # Existing widget components issue these literal reads during ordinary
        # login navigation. They remain Django coexistence, never Go evidence.
        widget_queries = {'widget=' + name for name in (
            'subscriptions', 'payments', 'associates', 'bestcourses', 'todaylessons',
            'expiringcarnets', 'expiringmedicalcertificates', 'expiredmedicalcertificates',
            'subscriptionstoapprove', 'incomeAndExpenses', 'expiredPayments')}
        require(not parsed.query or (path == '/api/instance/logo.png' and
                re.fullmatch(r'v=[0-9]{20}', parsed.query)) or
                (path == '/api/statistic/dashboard' and parsed.query in widget_queries),
                'coexistence-query-not-allowed')
        return {'kind': 'coexistence', 'backend': 'django-coexistence',
                'path': path.removeprefix('/api')}
    require(method == 'GET' and not path.startswith(('/api/', '/instance-')),
            'unknown-api-or-method')
    if consumer == 'mutations':
        require(not parsed.query or parsed.query == 'v=0', 'mutation-static-query-not-allowed')
    name = 'index.html' if path == '/' else path[1:]
    require(name in artifacts, 'unknown-static-path')
    # Cache-busting static queries are forwarded nowhere and are never logged.
    return {'kind': 'static', 'backend': 'ui-static', 'path': path, 'file': name}


def clean_headers(headers, *, response=False):
    connection = {part.strip().lower() for key, value in headers
                  if key.lower() == 'connection' for part in value.split(',')}
    excluded = HOP_HEADERS | connection | {'x-fixture-backend'}
    if response:
        excluded |= {'content-length'}
    return [(key, value) for key, value in headers if key.lower() not in excluded]


def actual_backend(peer, django_port, go_port, *, websocket=False, coexistence=False):
    require(peer[0] == '127.0.0.1', 'nonloopback-upstream')
    require(django_port != go_port, 'aliased-upstreams')
    if peer[1] == django_port:
        return 'django-websocket' if websocket else 'django-coexistence' if coexistence else 'django-v1'
    require(peer[1] == go_port and not websocket and not coexistence, 'unknown-upstream-peer')
    return 'go-v2'


class ProxyJournal:
    """Public events exclude raw data; private_traffic is parent-only bytes."""
    def __init__(self):
        self.events, self.failures = [], []
        self.private_traffic = []
        self.lock = threading.Lock()
        self.active_http = 0
        self.checkpointing = False
        self.stopping = threading.Event()
        self.sockets = set()

    def record(self, row):
        with self.lock:
            self.events.append(copy.deepcopy(row))

    def record_private(self, row):
        with self.lock:
            require(len(self.private_traffic) < 26 and
                    row['cid'] not in [item['cid'] for item in self.private_traffic],
                    'private-traffic-bound-or-duplicate')
            self.private_traffic.append(copy.deepcopy(row))

    def fail(self, code):
        with self.lock:
            self.failures.append(code)

    def snapshot(self):
        with self.lock:
            require(self.active_http == 0, 'checkpoint-with-inflight-http')
            require(not self.failures, 'proxy-failure')
            return copy.deepcopy(self.events)

    @contextmanager
    def checkpoint(self):
        with self.lock:
            require(not self.active_http and not self.checkpointing and not self.failures,
                    'checkpoint-with-inflight-http')
            self.checkpointing = True
        try:
            yield
            self.snapshot()
        finally:
            with self.lock:
                self.checkpointing = False


def observed_body(wire, headers):
    """Independent decoded bytes plus strict complete bounded wire framing.

    Playwright exposes decoded bodies. Retain original wire bytes privately too;
    never compare a gzip stream's digest with a browser's decoded JSON digest.
    """
    require(isinstance(wire, bytes) and len(wire) <= MAX_BODY, 'observed-wire-bound')
    encodings = [value.lower().strip() for key, value in headers if key.lower() == 'content-encoding']
    require(len(encodings) <= 1, 'observed-encoding-duplicate')
    if not encodings or encodings == ['identity']:
        return wire
    require(encodings == ['gzip'], 'observed-encoding-unsupported')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        body = decoder.decompress(wire, MAX_BODY + 1)
    except zlib.error:
        raise HarnessError('observed-gzip-invalid') from None
    require(len(body) <= MAX_BODY and decoder.eof and not decoder.unused_data and
            not decoder.unconsumed_tail, 'observed-gzip-incomplete-or-bound')
    return body


def proxy_handler(*, ui, artifacts, version, django_port, go_port, journal, consumer='reads'):
    consumer_routes(consumer)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(35)
            with journal.lock:
                journal.sockets.add(self.connection)

        def finish(self):
            try:
                super().finish()
            finally:
                with journal.lock:
                    journal.sockets.discard(self.connection)

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except Exception:
                journal.fail('proxy-request-error')
                self.close_connection = True

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler otherwise reflects request targets/errors.
            journal.fail('http-protocol-rejected')
            self.send_response_only(code)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        if consumer in ('mutations', 'mutation-denials'):
            def do_DELETE(self):
                self.dispatch()

        def dispatch(self):
            connection = None
            with journal.lock:
                journal.active_http += 1
                self.counted_http = True
            try:
                require(not journal.stopping.is_set(), 'proxy-stopping')
                with journal.lock:
                    require(not journal.checkpointing, 'request-during-checkpoint')
                require(self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}',
                        'unexpected-host')
                upgrade = self.headers.get('Upgrade', '').lower() == 'websocket'
                require(not self.headers.get('Upgrade') or upgrade, 'unsupported-upgrade')
                route = route_target(self.path, self.command, version=version,
                                     artifacts=artifacts, upgrade=upgrade, consumer=consumer)
                require(not self.headers.get('Transfer-Encoding'), 'request-transfer-encoding')
                lengths = self.headers.get_all('Content-Length', [])
                require(len(lengths) <= 1 and (not lengths or lengths[0].isdigit()), 'request-length')
                length = int(lengths[0]) if lengths else 0
                require(0 <= length <= 1024 * 1024, 'request-body-bound')
                require(self.command == 'POST' or length == 0, 'unexpected-get-body')
                body = self.rfile.read(length) if length else None
                require(not length or len(body) == length, 'incomplete-request-body')
                if route['kind'] == 'static':
                    path = (ui / route['file']).resolve()
                    require(path.is_relative_to(ui.resolve()) and path.is_file() and
                            sha(path) == artifacts[route['file']], 'static-file-drift')
                    data = path.read_bytes()
                    self.send_response_only(200)
                    self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('X-Fixture-Backend', 'ui-static')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(data)
                    journal.record({'method': self.command, 'path': route['path'], 'status': 200,
                                    'intended_backend': 'ui-static', 'backend': 'ui-static',
                                    'static_sha256': artifacts[route['file']], 'cid': ''})
                elif upgrade:
                    self.websocket(route)
                else:
                    cid = self.headers.get('X-Correlation-ID', '')
                    validate_correlation(cid, route, consumer)
                    port = go_port if route['backend'] == 'go-v2' else django_port
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
                    connection.connect()
                    peer = connection.sock.getpeername()
                    backend = actual_backend(peer, django_port, go_port,
                                             coexistence=route['kind'] == 'coexistence')
                    if backend != route['backend']:
                        journal.record({'method': self.command, 'path': route['path'], 'status': None,
                            'cid': cid, 'intended_backend': route['backend'], 'backend': backend,
                            'peer': list(peer[:2]), 'intended_peer': ['127.0.0.1', port], 'fallback': False})
                    require(backend == route['backend'], 'proxy-misroute')
                    query = urlsplit(self.path).query
                    upstream_target = route['path'] + ('?' + query if query else '')
                    connection.putrequest(self.command, upstream_target, skip_host=True, skip_accept_encoding=True)
                    headers = clean_headers(list(self.headers.items()))
                    for key, value in headers:
                        connection.putheader(key, value)
                    connection.putheader('Connection', 'close')
                    connection.endheaders(body)
                    response = connection.getresponse()
                    response_headers = response.getheaders()
                    cookies = [value for key, value in response_headers if key.lower() == 'set-cookie']
                    row = {'method': self.command, 'path': route['path'], 'status': response.status,
                           'cid': cid, 'intended_backend': route['backend'], 'backend': backend,
                           'peer': list(peer[:2]), 'intended_peer': ['127.0.0.1', port], 'fallback': False,
                           'set_cookie_sha256': json_digest(cookies)}
                    if route['path'] == '/oauth2/login':
                        row['bkn_auth_cookie_present'] = required_login_cookie(cookies)
                    journal.record(row)
                    if route['path'] == '/oauth2/login':
                        require(row['bkn_auth_cookie_present'], 'login-auth-cookie-missing-or-invalid')
                    require(not 300 <= response.status < 400, 'upstream-redirect-rejected')
                    data = response.read(MAX_BODY + 1)
                    require(len(data) <= MAX_BODY and response.isclosed() and
                            response.length in (0, None), 'response-body-bound-or-incomplete')
                    if consumer in ('mutations', 'mutation-denials') and cid:
                        # Private complete bytes independently observed at the real HTTP
                        # boundary. Never add these to publishable journal.events.
                        journal.record_private(dict(row, body=observed_body(data, response_headers),
                            wire_body=data, response_headers=response_headers, request_body=body,
                            headers=dict(response_headers), complete=True))
                    self.send_response_only(response.status)
                    for key, value in clean_headers(response_headers, response=True):
                        self.send_header(key, value)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('X-Fixture-Backend', backend)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(data)
                self.close_connection = True
            except Exception as error:
                journal.fail(error.code if isinstance(error, HarnessError) else 'proxy-transport-failed')
                self.close_connection = True
                try:
                    self.send_response_only(502)
                    self.send_header('Content-Length', '0')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                except OSError:
                    pass
            finally:
                if connection is not None:
                    connection.close()
                with journal.lock:
                    if self.counted_http:
                        journal.active_http -= 1

        def websocket(self, route):
            # A raw bounded tunnel preserves the real handshake/frames. Only the
            # two authored paths reach Django; query JWTs never enter the journal.
            upstream = socket.create_connection(('127.0.0.1', django_port), timeout=30)
            with journal.lock:
                journal.sockets.add(upstream)
            try:
                peer = upstream.getpeername()
                backend = actual_backend(peer, django_port, go_port, websocket=True)
                require(backend == route['backend'], 'websocket-misroute')
                request = f'GET {self.path} HTTP/1.1\r\n'
                for key, value in self.headers.items():
                    require(key.lower() not in {'proxy-authorization', 'x-fixture-backend'},
                            'websocket-proxy-header')
                    request += f'{key}: {value}\r\n'
                upstream.sendall((request + '\r\n').encode('latin1'))
                response = bytearray()
                while not response.endswith(b'\r\n\r\n'):
                    require(len(response) < 65536, 'websocket-header-bound')
                    chunk = upstream.recv(1)
                    require(chunk, 'websocket-handshake-eof')
                    response.extend(chunk)
                status = int(response.split(b' ', 2)[1])
                journal.record({'method': 'GET', 'path': route['path'], 'cid': '',
                                'status': status, 'intended_backend': route['backend'],
                                'backend': backend, 'peer': list(peer[:2]),
                                'intended_peer': ['127.0.0.1', django_port], 'fallback': False})
                require(status == 101, 'websocket-upstream-rejected')
                self.wfile.write(response)
                self.wfile.flush()
                # Long-lived WS is excluded from HTTP checkpoint in-flight count.
                with journal.lock:
                    journal.active_http -= 1
                    self.counted_http = False
                while not journal.stopping.is_set():
                    readable, _, _ = select.select([self.connection, upstream], [], [], .25)
                    for source in readable:
                        chunk = source.recv(65536)
                        if not chunk:
                            return
                        (upstream if source is self.connection else self.connection).sendall(chunk)
            finally:
                with journal.lock:
                    journal.sockets.discard(upstream)
                upstream.close()
    return Handler


@contextmanager
def owned_http_server(handler, *, journal=None):
    class Server(ThreadingHTTPServer):
        # The unchanged UI requests many assets concurrently. Explicitly size
        # the listen backlog for that burst before TCPServer activates it.
        request_queue_size = 128
        daemon_threads = True
        block_on_close = False

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(35)
            with self.ownership_lock:
                self.connections.add(connection)
            return connection, address

        def process_request(self, request, client_address):
            worker = threading.Thread(target=self.process_request_thread,
                                      args=(request, client_address), daemon=True)
            with self.ownership_lock:
                self.workers.append(worker)
            worker.start()

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                with self.ownership_lock:
                    self.connections.discard(request)

        def handle_error(self, *_args):
            if journal is not None:
                journal.fail('http-handler-failed')

    server = Server(('127.0.0.1', 0), handler)
    server.connections, server.workers = set(), []
    server.ownership_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1})
    try:
        thread.start()
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        if journal is not None:
            journal.stopping.set()
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=5)
        with server.ownership_lock:
            connections = list(server.connections)
        if journal is not None:
            with journal.lock:
                connections.extend(journal.sockets)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                connection.close()
        server.server_close()
        for worker in server.workers:
            worker.join(timeout=5)
        require(not thread.is_alive(), 'http-thread-cleanup-failed')
        require(all(not worker.is_alive() for worker in server.workers), 'http-worker-cleanup-failed')


@contextmanager
def transport_probe():
    """Real HTTP transport controls, separate from all business observations."""
    counts = {'cookie': 0, 'redirect': 0, 'followed': 0, 'cookies_received': False,
              'post': 0, 'post_body_preserved': False}

    class Probe(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.connection.settimeout(5)
            if self.path == '/cookie':
                counts['cookie'] += 1
                if counts['cookie'] == 2:
                    supplied = set(self.headers.get('Cookie', '').split('; '))
                    counts['cookies_received'] = supplied == {'probe_a=one', 'probe_b=two'}
                self.send_response_only(200)
                self.send_header('Set-Cookie', 'probe_a=one; Path=/; HttpOnly; SameSite=Strict')
                self.send_header('Set-Cookie', 'probe_b=two; Path=/; SameSite=Lax')
            elif self.path in tuple(f'/redirect/{status}' for status in (301, 302, 303, 307, 308)):
                counts['redirect'] += 1
                self.send_response_only(int(self.path.rsplit('/', 1)[1]))
                self.send_header('Location', '/forbidden')
            else:
                counts['followed'] += 1
                self.send_response_only(500)
            self.send_header('Content-Length', '0')
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()

        def do_POST(self):
            counts['post'] += 1
            length = int(self.headers.get('Content-Length', '0'))
            require(0 <= length <= 1024, 'probe-post-bound')
            body = self.rfile.read(length)
            counts['post_body_preserved'] = self.path == '/post' and body == b'private-probe-body=unchanged'
            self.send_response_only(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    with owned_http_server(Probe) as origin:
        yield origin, counts


def verify_proxy_asgi_window(events, django_events):
    """Require complete exact HTTP attribution, including coexistence traffic."""
    allowed_django = {(row['method'], row['path'], row['cid'], row['status'])
                      for row in events if row['backend'] in ('django-v1', 'django-coexistence')}
    require(all((row['method'], row['path'], row['cid'], row['status']) in allowed_django and
                row['error_type'] is None for row in django_events), 'unexpected-django-window-event')
    # Cardinality is significant: duplicates and requests outside proxy traffic fail.
    expected_events = [row for row in events if row['backend'] in ('django-v1', 'django-coexistence')]
    require(len(expected_events) == len(django_events), 'incomplete-django-window')
    from collections import Counter
    signature = lambda row: (row['method'], row['path'], row['cid'], row['status'])
    require(Counter(map(signature, expected_events)) == Counter(map(signature, django_events)),
            'django-window-multiplicity')


def attribution_for(receipt, events, django_events):
    """Consume complete proxy/ASGI windows; never derive routing from JS headers."""
    selected = [row for row in events if row['path'] == browser.UPSTREAM_PATH]
    responses = [row for row in receipt['responses'] if row['path'] == browser.LIST_PATH]
    require(len(selected) == len(responses) == 6, 'extra-or-missing-list-traffic')
    require([row['cid'] for row in selected] == [row['cid'] for row in responses],
            'unobserved-list-correlation')
    verify_proxy_asgi_window(events, django_events)
    result = []
    for response, row in zip(responses, selected):
        backend = 'django-v1' if receipt['version'] == 'v1' else 'go-v2'
        require(row['backend'] == row['intended_backend'] == backend and not row['fallback'],
                'selected-upstream-misroute')
        correlated = [event for event in django_events if event['cid'] == row['cid']]
        browser.oracle.assert_attribution('django' if receipt['version'] == 'v1' else 'go',
                                          'GET', browser.UPSTREAM_PATH, 200, correlated, row['cid'])
        result.append({'cid': row['cid'], 'proxy': {key: row[key] for key in
                       ('cid', 'method', 'path', 'status', 'backend', 'fallback')},
                       'django_events': correlated})
    return result


class Checkpoints:
    """A single request/ack outstanding; snapshot completes before acknowledgment."""
    def __init__(self, snapshot, closed_snapshot=None, *, consumer='reads'):
        require(consumer in ('reads', 'mutations'), 'unknown-browser-consumer')
        self.consumer, self.failed = consumer, False
        if consumer == 'mutations':
            from TESTS.adapters.django.message_mutations_browser import CHECKPOINTS
            self.order = CHECKPOINTS
        else:
            self.order = browser.CHECKPOINTS
        self.snapshot = snapshot
        self.closed_snapshot = closed_snapshot
        self.states = []
        self.closed = []

    def receive(self, frame):
        if self.consumer == 'mutations':
            try:
                return self._receive_mutation(frame)
            except BaseException:
                self.failed = True
                raise
        index = len(self.states)
        require(set(frame) == {'type', 'seq', 'actor', 'action'} and
                frame['type'] == 'checkpoint' and type(frame['seq']) is int and
                frame['seq'] == index + 1 and index < len(browser.CHECKPOINTS), 'checkpoint-protocol')
        require((frame['actor'], frame['action']) == browser.CHECKPOINTS[index], 'checkpoint-order')
        state = self.snapshot(frame['actor'], frame['action'])
        require(set(state) == {'models', 'cache'} and len(state['models']) == 128,
                'checkpoint-snapshot-scope')
        self.states.append(dict(state, actor=frame['actor'], action=frame['action']))
        return {'type': 'ack', 'seq': frame['seq'], 'success': True}

    def _receive_mutation(self, frame):
        index = len(self.states)
        require(not self.failed and isinstance(frame, dict) and
                set(frame) == {'type', 'seq', 'payload'} and frame['type'] == 'checkpoint' and
                type(frame['seq']) is int and frame['seq'] == index + 1 and index < len(self.order),
                'checkpoint-protocol')
        payload = frame['payload']
        actor, action = self.order[index]
        fields = {'actor', 'action', 'requests'} | ({'identity'} if action == 'login' else
            {'denied'} if action == 'denied' else set() if action == 'closed' else {'view'})
        require(isinstance(payload, dict) and set(payload) == fields and
                (payload['actor'], payload['action']) == (actor, action) and
                isinstance(payload['requests'], list), 'checkpoint-order-or-payload')
        state = self.snapshot(copy.deepcopy(payload))
        require(set(state) == {'models', 'cache'} and len(state['models']) == 128,
                'checkpoint-snapshot-scope')
        self.states.append(dict(state, actor=actor, action=action))
        if action == 'closed':
            self.closed.append({'actor': actor, 'observed_after_context_close': True})
        return {'type': 'ack', 'seq': frame['seq'], 'success': True}

    def complete(self):
        require(not self.failed and len(self.states) == len(self.order), 'missing-checkpoints')
        require(len(self.closed) == len(browser.ACTORS), 'missing-context-close-proof')

    def context_closed(self, frame):
        require(self.consumer == 'reads', 'unexpected-context-close-frame')
        index = len(self.closed)
        require(set(frame) == {'type', 'seq', 'actor'} and frame['type'] == 'context_closed' and
                type(frame['seq']) is int and frame['seq'] == index + 1 and
                index < len(browser.ACTORS) and frame['actor'] == browser.ACTORS[index],
                'context-close-protocol')
        require(len(self.states) == (2, 5, 8, 10)[index] and self.closed_snapshot is not None,
                'context-close-order')
        proof = self.closed_snapshot(frame['actor'], self.states[-1])
        self.closed.append(proof)
        return {'type': 'context_ack', 'seq': frame['seq'], 'success': True}


class CompleteAsgiObserver(browser.oracle.AsgiObserver):
    """Wait for application return, including work after the last response byte."""
    def __init__(self, application):
        super().__init__(application)
        self.condition = threading.Condition()
        self.active = 0
        self.active_websockets = 0
        self.websocket_started = 0
        self.websocket_completed = 0
        self.websocket_errors = 0

    async def __call__(self, scope, receive, send):
        kind = scope.get('type')
        if kind not in ('http', 'websocket'):
            return await super().__call__(scope, receive, send)
        with self.condition:
            if kind == 'http':
                self.active += 1
            else:
                self.active_websockets += 1
                self.websocket_started += 1
        try:
            await super().__call__(scope, receive, send)
        except BaseException:
            if kind == 'websocket':
                with self.condition:
                    self.websocket_errors += 1
            raise
        finally:
            with self.condition:
                if kind == 'http':
                    self.active -= 1
                else:
                    self.active_websockets -= 1
                    self.websocket_completed += 1
                self.condition.notify_all()

    def wait_idle(self, timeout=10, *, include_websockets=False):
        with self.condition:
            require(self.condition.wait_for(lambda: self.active == 0 and
                (not include_websockets or self.active_websockets == 0), timeout), 'asgi-window-incomplete')
            require(not self.websocket_errors, 'asgi-websocket-error')


def stop_child(process):
    if process.poll() is None:
        # Each child is a session leader, including its browser descendants.
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    # Browser grandchildren can survive a crashed Node leader.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_node(*, node, module, work, private_log, environment, input_data,
             checkpoint, probe_origin, timeout=360, consumer='reads'):
    from TESTS.adapters.go.message_image_execution import defer_interruption
    require(consumer in ('reads', 'mutations'), 'unknown-browser-consumer')
    credentials = work / 'browser-input.json'
    process = None
    child_failure = None
    selector = selectors.DefaultSelector()
    try:
        private_write(credentials, json.dumps(input_data))
        temporary = work / 'browser-temp'
        temporary.mkdir(mode=0o700)
        child_environment = dict(environment, TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
        with defer_interruption():
            process = subprocess.Popen([str(node), str(ROOT / NODE_RUNNER),
                '--input', str(credentials), '--playwright-module', str(module),
                '--probe-origin', probe_origin, *(['--consumer', consumer] if consumer != 'reads' else [])],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=private_log, cwd=work, env=child_environment, start_new_session=True, bufsize=0)
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline, pending, result = time.monotonic() + timeout, bytearray(), None
        browser_binary = None
        eof = False
        while not eof:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'browser-child-deadline')
            ready = selector.select(min(remaining, 1))
            if not ready:
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                eof = True
                break
            pending.extend(chunk)
            require(len(pending) <= MAX_FRAME, 'browser-protocol-frame-bound')
            while b'\n' in pending:
                raw_frame, _, pending = pending.partition(b'\n')
                frame = json.loads(raw_frame, object_pairs_hook=unique_object)
                require(result is None, 'browser-frame-after-result')
                if frame.get('type') == 'failure':
                    child_failure = validate_child_failure(frame, consumer=consumer)
                    raise HarnessError('browser-child-failed', child_failure=child_failure)
                elif frame.get('type') == 'ready':
                    require(browser_binary is None and not checkpoint.states and
                            set(frame) == {'type', 'browser_binary'}, 'browser-ready-protocol')
                    browser_binary = frame['browser_binary']
                    require(set(browser_binary) == {'path', 'sha256'} and
                            Path(browser_binary['path']).is_absolute() and
                            os.access(browser_binary['path'], os.X_OK) and
                            sha(browser_binary['path']) == browser_binary['sha256'],
                            'browser-binary-input')
                    process.stdin.write(b'{"type":"ready_ack","success":true}\n')
                    process.stdin.flush()
                elif frame.get('type') == 'checkpoint':
                    require(browser_binary is not None, 'checkpoint-before-ready')
                    ack = checkpoint.receive(frame)
                    process.stdin.write((json.dumps(ack) + '\n').encode())
                    process.stdin.flush()
                elif frame.get('type') == 'context_closed':
                    require(browser_binary is not None, 'context-close-before-ready')
                    ack = checkpoint.context_closed(frame)
                    process.stdin.write((json.dumps(ack) + '\n').encode())
                    process.stdin.flush()
                else:
                    require(set(frame) == {'type', 'receipt', 'transport', 'cookies', 'browser_closed',
                                            'browser_binary'} and frame['type'] == 'result',
                            'browser-result-protocol')
                    checkpoint.complete()
                    result = frame
        require(not pending and result is not None, 'browser-protocol-eof')
        process.wait(timeout=max(.1, min(10, deadline - time.monotonic())))
        require(process.returncode == 0 and result['browser_closed'] is True,
                'browser-child-cleanup-or-exit')
        require(result['browser_binary'] == browser_binary and
                sha(browser_binary['path']) == browser_binary['sha256'], 'browser-binary-drift')
        return result
    finally:
        operations = [selector.close]
        if process is not None:
            operations.append(lambda: stop_child(process))
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    operations.append(pipe.close)
        operations.append(lambda: credentials.unlink(missing_ok=True))
        try:
            cleanup_all(operations, 'browser-child-cleanup-failed')
        except HarnessError as error:
            error.child_failure = child_failure
            raise


def cache_document(snapshot):
    return [{'key_b64': base64.b64encode(key).decode(),
             'type_b64': base64.b64encode(entry.redis_type).decode(),
             'dump_b64': base64.b64encode(entry.serialized_value).decode(),
             'expires_at_ms': entry.expires_at_ms}
            for key, entry in sorted(snapshot.entries.items())]


def durable_snapshot(apps, raw):
    from django.db import connection, transaction
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        models = browser.oracle.snapshot_models(apps)
    require(len(models) == 128, 'managed-model-inventory-drift')
    return {'models': models, 'cache': snapshot_historical_read_cache(raw)}


def save_snapshot(path, state):
    private_write(path, json.dumps({'models': state['models'], 'cache': cache_document(state['cache'])}))


def login_effect(before, after, actor):
    """Separate complete login changes from the subsequent read-only baseline."""
    changed = [name for name in before['models']
               if before['models'][name] != after['models'][name]]
    return {'actor': actor, 'changed_models': changed,
            'before_models': browser.oracle.state_receipt(before['models']),
            'after_models': browser.oracle.state_receipt(after['models']),
            'before_cache_sha256': before['cache'].digest,
            'after_cache_sha256': after['cache'].digest,
            'cache_changed': before['cache'] != after['cache'],
            'read_only_login_claimed': False}


def validate_login_projection(before, after, user_id, started, finished):
    """Prepared uppercase users/cards/hashes leave only last_login to change.

    DB1 changes are separately recorded by login_effect. They do not become
    implicit exclusions from the subsequent complete cache comparisons.
    """
    projected = copy.deepcopy(before['models'])
    identifier = {'type': 'uuid', 'value': user_id}
    old = [row for row in projected.get('application.User', []) if row['user_id'] == identifier]
    new = [row for row in after['models'].get('application.User', []) if row['user_id'] == identifier]
    require(len(old) == len(new) == 1, 'login-state-identity')
    previous, current = old[0], new[0]
    timestamp = current.get('last_login')
    require(isinstance(timestamp, dict) and set(timestamp) == {'type', 'value'} and
            timestamp['type'] == 'datetime', 'login-timestamp-missing')
    observed = datetime.fromisoformat(timestamp['value'].replace('Z', '+00:00'))
    require(started <= observed <= finished and timestamp != previous['last_login'],
            'login-timestamp-window')
    require(previous['username'] == previous['username'].upper() and
            previous['password'].startswith('pbkdf2_sha256$'), 'login-prerequisite-state')
    previous['last_login'] = timestamp
    require(projected == after['models'], 'undeclared-login-model-change')
    return {'last_login_advanced': True, 'password_hash_preserved': True,
            'other_model_fields_preserved': True}


def observe_closed_state(observer, snapshot, baseline, contrasts, *, timeout=10):
    observer.wait_idle(timeout=timeout, include_websockets=True)
    current = snapshot()
    models = browser.oracle.assert_state_unchanged(baseline['models'], current['models'])
    cache = browser.oracle.assert_cache_unchanged(baseline['cache'], current['cache'], contrasts)
    return current, {'models': models, 'cache': cache}


def prepare_browser_identities(apps, work, *, consumer='reads'):
    """Prepare the owned fixture before baseline; mutations also seed notification startup."""
    require(consumer in ('reads', 'mutations'), 'unknown-browser-consumer')
    from auditlog.context import disable_auditlog
    from django.contrib.auth.hashers import check_password, make_password
    from django.utils import timezone
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration
    from instance.models import InstanceConfiguration
    from application.serializers.auth_serializers import UserAuthSerializer
    import secrets

    identities = {}
    if consumer == 'mutations':
        from TESTS.adapters.django import message_mutations_browser as authored
    else:
        authored = browser
    spec = authored.authored_spec()
    with disable_auditlog():
        if consumer == 'mutations':
            from notifications.models import NotificationsBroadcast
            # NotificationManager lazily creates this audited row at startup.
            # Seed each fresh fixture directly: its service manager can remain
            # cached after the parent restores the previous fixture's database.
            require(not NotificationsBroadcast.objects.exists(), 'unexpected-notification-broadcasts')
            NotificationsBroadcast.objects.create(broadcast_name='all_notifications')
            require(list(NotificationsBroadcast.objects.values_list('broadcast_name', flat=True)) ==
                    ['all_notifications'], 'notification-prerequisite-state')
        for actor in browser.ACTORS:
            identifier = spec['actors'][actor]['user_id']
            user = User.original_objects.get(pk=identifier)
            password = 'Browser1!' + secrets.token_hex(24)
            encoded = make_password(password, hasher='pbkdf2_sha256')
            require(check_password(password, encoded) and encoded.startswith('pbkdf2_sha256$'),
                    'usable-production-password-required')
            User.original_objects.filter(pk=identifier).update(
                password=encoded, username=user.username.upper(), is_active=True, two_fa=False,
                date_joined=timezone.now() - timedelta(days=60),
                balance_sheet_start_day=1, balance_sheet_start_month=1)
            identities[actor] = {'user_id': identifier, 'username': user.username.upper(),
                                 'password': password}
        for actor in ('owner', 'foreign_owner'):
            user_id = identities[actor]['user_id']
            association = SportAssociation.original_objects.get(user_id=user_id)
            # Association logos are data URIs; the separate OEM logo is served
            # through the named Django coexistence endpoint below.
            SportAssociation.original_objects.filter(pk=association.pk).update(
                logo='data:image/png;base64,' + FIXTURE_PNG, additional_sections=[])
            SportAssociationMembershipCardConfiguration.objects.get_or_create(sport_association=association)
        require(not InstanceConfiguration.objects.exists(), 'unexpected-instance-configuration')
        owner_association = SportAssociation.original_objects.get(user_id=identities['owner']['user_id'])
        InstanceConfiguration.objects.create(domain='127.0.0.1', name='Private Message Fixture',
            primary_association=owner_association, support_email='fixture@example.test',
            logo_path='/api/instance/logo.png', setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_IMPORT)
    from django.conf import settings
    logo = Path(settings.MEDIA_ROOT) / 'instance/logo.png'
    logo.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with logo.open('xb') as stream:
        stream.write(base64.b64decode(FIXTURE_PNG))
    logo.chmod(0o600)
    for actor in browser.ACTORS:
        user = User.original_objects.get(pk=identities[actor]['user_id'])
        require(UserAuthSerializer(user).data['requires_welcome'] is False, 'onboarding-prerequisite')
    return identities


@contextmanager
def owned_django(report, *, liveness_observer=None):
    """Own Django; optionally expose its actual ready handles through a guard."""
    import uvicorn
    from core.asgi import application
    from TESTS.http_client import poll

    observer = CompleteAsgiObserver(application)
    server = uvicorn.Server(uvicorn.Config(observer, host='127.0.0.1', port=0,
        lifespan='off', access_log=False, log_level='critical', timeout_graceful_shutdown=5))
    thread = threading.Thread(target=server.run)
    report['service_cleanup']['django'] = 'start_attempted'
    try:
        thread.start()
        poll(lambda: server.started and thread.is_alive(), bool, timeout=25)
        report['service_cleanup']['django'] = 'running'
        if liveness_observer is not None:
            liveness_observer(runtime.django_liveness_guard(server, thread, observer))
        yield observer, server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=10)
        for listener in getattr(server, 'servers', []):
            for item in listener.sockets or []:
                item.close()
        report['service_cleanup']['django'] = 'stopped' if not thread.is_alive() else 'cleanup_failed'
        require(not thread.is_alive(), 'django-thread-cleanup-failed')


@contextmanager
def owned_go(work, executable, codec, environment, private_log, report, native_build,
             *, liveness_observer=None):
    """Own Go; optionally expose the actual ready Popen through its guard."""
    from TESTS.http_client import poll
    from TESTS.adapters.go.message_image_execution import defer_interruption

    # Reserve then release a private port; readiness must come from this child.
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    target = work / 'message-browser-go'
    codec_target = work / 'image-codec'
    shutil.copyfile(executable, target)
    shutil.copyfile(codec, codec_target)
    target.chmod(0o700)
    codec_target.chmod(0o700)
    require(sha(target) == sha(executable) and sha(codec_target) == sha(codec), 'binary-copy-drift')
    require(native_build['artifacts'] == {'server': sha(target), 'image-codec': sha(codec_target)},
            'executed-native-build-binding')
    child_env = dict(environment, GO_LISTEN_ADDR=f'127.0.0.1:{port}',
                     ASSOZETA_LOGIN_JPEG_CODEC=str(codec_target))
    process = None
    report['service_cleanup']['go'] = 'start_attempted'
    try:
        # Register the returned handle before cancellation can unwind the owner.
        with defer_interruption():
            process = subprocess.Popen([str(target)], cwd=work, env=child_env,
                stdout=private_log, stderr=subprocess.STDOUT, start_new_session=True)
        def ready():
            require(process.poll() is None, 'owned-go-exited')
            try:
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                try:
                    connection.request('GET', '/readyz')
                    response = connection.getresponse()
                    response.read()
                    return response.status == 200
                finally:
                    connection.close()
            except OSError:
                return False
        poll(ready, bool, timeout=30)
        report['service_cleanup']['go'] = 'running'
        if liveness_observer is not None:
            from TESTS.adapters.django.message_read_scopes_runtime import go_liveness_guard
            liveness_observer(go_liveness_guard(process))
        yield port
    finally:
        if process is not None:
            stop_child(process)
        report['service_cleanup']['go'] = 'stopped'
        require(sha(target) == sha(executable) and sha(codec_target) == sha(codec), 'running-binary-drift')


class FixtureRestore:
    """Restore the entire original owned DB and DB1, including partial setup writes."""
    def __init__(self, apps, raw, work, binaries, log, environment):
        self.apps, self.raw, self.work = apps, raw, Path(work)
        self.log, self.environment = log, environment
        self.binaries = Path(binaries)
        self.original = durable_snapshot(apps, raw)
        self.dump = self.work / 'original.sql'
        private_write(self.dump, '')
        self.command('pg_dump', '--clean', '--if-exists', '--no-owner', '--no-privileges',
                     '--file', str(self.dump))
        require(self.dump.stat().st_size > 0, 'original-database-backup-empty')

    def command(self, binary, *args):
        return subprocess.run([str(self.binaries / binary), '--host', str(self.work),
            '--username', 'contracts', '--dbname', runtime.DATABASE, *args],
            env=self.environment, stdout=self.log, stderr=subprocess.STDOUT,
            check=True, timeout=120)

    def restore(self):
        from django.db import connections
        connections.close_all()
        self.command('psql', '--no-psqlrc', '--single-transaction', '--set', 'ON_ERROR_STOP=1',
                     '--file', str(self.dump))
        self.raw.flushdb()
        for key, entry in self.original['cache'].entries.items():
            options = {'absttl': True} if entry.expires_at_ms != -1 else {}
            self.raw.restore(key, entry.expires_at_ms if options else 0,
                             entry.serialized_value, replace=True, **options)
        actual = durable_snapshot(self.apps, self.raw)
        require(actual == self.original, 'full-original-state-not-restored')
        self.dump.unlink()
        return {'all_128_models_restored': True, 'complete_db1_restored': True,
                'models': browser.oracle.state_receipt(actual['models']),
                'cache_sha256': actual['cache'].digest}


def verify_transport_and_cookies(child, probe_counts, events):
    require(child['transport'] == {'redirect_blocked': True, 'cookies_preserved': True,
                                   'post_body_preserved': True},
            'live-transport-probe-failed')
    require(probe_counts == {'cookie': 2, 'redirect': 5, 'followed': 0, 'cookies_received': True,
                            'post': 1, 'post_body_preserved': True},
            'live-transport-probe-counts')
    logins = [row for row in events if row['path'] == '/oauth2/login']
    require(len(logins) == 4 and all(row.get('bkn_auth_cookie_present') is True and
            row['set_cookie_sha256'] != json_digest([]) for row in logins),
            'actual-login-auth-cookie-required')
    require(child['cookies'] == [{'actor': actor, 'method': 'POST', 'path': '/api/oauth2/login',
                                  'sha256': row['set_cookie_sha256'], 'bkn_auth_cookie_present': True}
                                 for actor, row in zip(browser.ACTORS, logins)] and
            len(logins) == 4, 'real-login-cookie-forwarding-mismatch')


def run_version(*, version, args, work, private_log, environment, identities, apps,
                raw, fixture, observer, django_port, go_port, build, report):
    before = browser.capture_provenance(args.ui_dir)
    require(before == report['frontend_provenance'], 'frontend-or-helper-drift')
    require(build == validate_build_record(args.build_observation, before), 'build-record-drift')
    journal = ProxyJournal()
    directory = work / version
    directory.mkdir(mode=0o700)
    previous = durable_snapshot(apps, raw)
    previous_time = datetime.now(timezone.utc)
    save_snapshot(directory / 'before-login.json', previous)
    effects = []
    attempt = {'version': version, 'checkpoint_count': 0, 'last_checkpoint': None,
               'proxy_failure_codes': [], 'proxy_events': 0}
    report.setdefault('attempts', []).append(attempt)
    def snapshot(actor, action):
        nonlocal previous, previous_time
        with journal.checkpoint():
            observer.wait_idle()
            state = durable_snapshot(apps, raw)
            observed_time = datetime.now(timezone.utc)
            save_snapshot(directory / f'{actor}-{action}.json', state)
            if action == 'login':
                effect = login_effect(previous, state, actor)
                effect['projection'] = validate_login_projection(previous, state,
                    identities[actor]['user_id'], previous_time, observed_time)
                effects.append(effect)
            previous = state
            previous_time = observed_time
            attempt['checkpoint_count'] += 1
            attempt['last_checkpoint'] = {'actor': actor, 'action': action}
        return state
    def closed_snapshot(actor, baseline):
        with journal.checkpoint():
            current, proof = observe_closed_state(observer, lambda: durable_snapshot(apps, raw),
                                                  baseline, fixture['contrasts'])
            save_snapshot(directory / f'{actor}-context-closed.json', current)
            return dict(proof, actor=actor)
    checkpoints = Checkpoints(snapshot, closed_snapshot)
    websocket_start = observer.websocket_started
    websocket_completed = observer.websocket_completed
    cursor = observer.cursor()
    with owned_http_server(proxy_handler(ui=args.ui_dir, artifacts=before['artifact_sha256'],
            version=version, django_port=django_port, go_port=go_port, journal=journal),
            journal=journal) as origin:
        with transport_probe() as (probe_origin, counts):
            try:
                child = run_node(node=args.node, module=args.playwright_module, work=directory,
                    private_log=private_log, environment=environment,
                    input_data=browser.browser_input(version=version, origin=origin,
                        identities=identities, provenance=before), checkpoint=checkpoints,
                    probe_origin=probe_origin, timeout=args.timeout)
            finally:
                with journal.lock:
                    attempt['proxy_failure_codes'] = list(journal.failures)
                    attempt['proxy_events'] = len(journal.events)
                    attempt['proxy_observations'] = copy.deepcopy(journal.events)
    events = journal.snapshot()
    final_state, final_proof = observe_closed_state(observer, lambda: durable_snapshot(apps, raw),
                                                   checkpoints.states[-1], fixture['contrasts'])
    save_snapshot(directory / 'final-after-all-contexts.json', final_state)
    require(observer.websocket_started - websocket_start ==
            observer.websocket_completed - websocket_completed ==
            len([row for row in events if row['backend'] == 'django-websocket']),
            'websocket-lifetime-observation-mismatch')
    complete_window = observer.delta(cursor)
    after = browser.capture_provenance(args.ui_dir)
    require(build == validate_build_record(args.build_observation, after), 'build-record-drift')
    attribution = attribution_for(child['receipt'], events, complete_window)
    verify_transport_and_cookies(child, counts, events)
    acceptance = browser.verify_acceptance(child['receipt'], state_checkpoints=checkpoints.states,
        contrasts=fixture['contrasts'], attribution=attribution, provenance_before=before,
        provenance_after=after, build_observation=build)
    # Raw receipts/snapshots remain private until teardown; public state is hashes.
    private_write(directory / 'receipt.json', json.dumps(child))
    return {'version': version, 'acceptance': acceptance, 'login_effects': effects,
            'context_close_proofs': checkpoints.closed, 'final_state_proof': final_proof,
            'websocket_applications_completed': observer.websocket_completed - websocket_completed,
            'proxy_observations': events, 'django_window': complete_window,
            'browser_binary_sha256': child['browser_binary']['sha256'],
            'transport_probe': child['transport'], 'real_cookie_forwarding': True,
            'cookie_evidence': 'proxy-http-headers-and-exact-forwarded-apiresponse-digests',
            'routed_page_set_cookie_headers_observable': False,
            'proxy_cleanup_verified': True, 'browser_child_reaped': True,
            'snapshots_retained_privately_until_teardown': True,
            'provenance_before': before, 'provenance_after': after}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ui-dir', 'build-observation', 'playwright-module', 'go-binary', 'image-codec',
                 'go-build-record', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--node', type=Path, default=Path(shutil.which('node') or '/missing-node'))
    parser.add_argument('--timeout', type=int, default=360, help='Per-version browser deadline in seconds')
    args = parser.parse_args(argv)
    require(30 <= args.timeout <= 900, 'browser-timeout-range')
    for name in ('ui_dir', 'build_observation', 'playwright_module', 'go_binary', 'image_codec',
                 'go_build_record', 'node'):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    return args


@contextmanager
def private_database(directory, report):
    with runtime.owned_message_database(directory, report) as owned:
        with redirect_stdout(owned[2]), redirect_stderr(owned[2]):
            yield owned


def termination(_signum, _frame):
    raise KeyboardInterrupt('parent requested termination')


def main(argv=None):
    args = parse_args(argv)
    output = fresh_output(args.output)
    report = {'success': False, 'live_executed': False, 'acceptance_gate': False,
              'scope': 'unchanged saved-message list browser workflow only',
              'complete_message_family_parity': False, 'ledger_updated': False,
              'versions': [], 'errors': [], 'cleanup_errors': [],
              'service_cleanup': {name: 'not_started' for name in ('postgres', 'redis', 'django', 'go')},
              'parent_review_required': True}
    original_environment = dict(os.environ)
    original_umask = os.umask(0o077)
    previous_sigterm = signal.signal(signal.SIGTERM, termination)
    sources, binaries, module_before = None, None, None
    provenance, build, native_build = None, None, None
    private_values = set()
    stage = 'preflight'
    try:
        require(not sys.flags.optimize, 'assert-enabled-python-required-for-existing-verifier')
        from TESTS.adapters.django.production_runtime import require_production_python
        report['python'] = require_production_python()
        provenance = browser.capture_provenance(args.ui_dir)
        report['frontend_provenance'] = provenance
        build = validate_build_record(args.build_observation, provenance)
        native_build = validate_go_build_record(args.go_build_record, args.go_binary, args.image_codec)
        report['native_build'] = native_build
        module_before = module_fingerprints(args.playwright_module)
        sources = source_hashes()
        report['source_sha256_before'] = sources
        paths = runtime.service_binary_paths(False)
        binaries_dir = Path(paths['postgres']).parent
        paths.update({'pg_dump': binaries_dir / 'pg_dump', 'node': args.node,
                      'go-server': args.go_binary, 'image-codec': args.image_codec})
        binaries = runtime.binary_hashes(paths)
        report['binary_sha256_before'] = {key: row['sha256'] for key, row in binaries.items()}
        report['playwright'] = module_before
        report['build_record_sha256'] = build['record_sha256']
        tool_environment = runtime.sanitized_tool_environment()
        tool_environment.update({'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC',
                                 'GOPROXY': 'off', 'GOTOOLCHAIN': 'local', 'GOSUMDB': 'off'})
        runtime.apply_private_environment(tool_environment)
        with tempfile.TemporaryDirectory(prefix='az-message-browser-', dir='/tmp') as private:
            private_path = Path(private)
            private_path.chmod(0o700)
            # Reused lifecycle puts logs ONLY in this private directory.
            with private_database(private_path, report) as (work, redis_port, log):
                work.chmod(0o700)
                (private_path / 'services.log').chmod(0o600)
                environment, private_values = runtime.private_environment(work, redis_port, tool_environment)
                runtime.apply_private_environment(environment)
                apps, settings = runtime.initialize_django_apps()
                from django.conf.global_settings import PASSWORD_HASHERS
                from django.test.utils import override_settings
                from django.db import connections
                import redis
                from TESTS.http_client import poll
                with override_settings(CACHES=runtime.cache_override(redis_port), PASSWORD_HASHERS=PASSWORD_HASHERS):
                    raw = redis.Redis(host='127.0.0.1', port=redis_port, db=1,
                                      password='synthetic-identity-only')
                    restore = None
                    try:
                        def redis_ready():
                            try:
                                return raw.ping()
                            except redis.ConnectionError:
                                return False
                        poll(redis_ready, bool, timeout=15)
                        stage = 'schema-and-original-backup'
                        report['schema'] = runtime.migrate_full_schema(log)
                        restore = FixtureRestore(apps, raw, work, binaries_dir, log, environment)
                        stage = 'fixture-and-login-prerequisites'
                        fixture_work = work / 'message-reads-private'
                        fixture_work.mkdir(mode=0o700)
                        fixture = browser.oracle.seed_fixture(apps, fixture_work, raw)
                        private_values.update(fixture['tokens'].values())
                        identities = prepare_browser_identities(apps, work)
                        private_values.update(row['password'] for row in identities.values())
                        with ExitStack() as services:
                            stage = 'owned-services'
                            observer, django_port = services.enter_context(owned_django(report))
                            go_port = services.enter_context(owned_go(work, args.go_binary, args.image_codec,
                                environment, log, report, native_build))
                            for version in ('v1', 'v2'):
                                stage = 'browser-' + version
                                report['live_executed'] = True
                                report['versions'].append(run_version(version=version, args=args,
                                    work=fixture_work, private_log=log, environment=tool_environment,
                                    identities=identities, apps=apps, raw=raw, fixture=fixture,
                                    observer=observer, django_port=django_port, go_port=go_port,
                                    build=build, report=report))
                    finally:
                        try:
                            if restore is not None:
                                report['fixture_cleanup'] = restore.restore()
                        finally:
                            cleanup_all([lambda: (work / 'original.sql').unlink(missing_ok=True),
                                         connections.close_all, raw.close], 'fixture-client-cleanup-failed')
    except BaseException as error:
        # Exception text can contain tokens, SQL, passwords or query strings.
        report['errors'].append(public_error(stage, error))
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        os.umask(original_umask)
        signal.signal(signal.SIGTERM, previous_sigterm)
        try:
            require(provenance is not None and build is not None, 'missing-frontend-inputs')
            report['frontend_provenance_after'] = browser.capture_provenance(args.ui_dir)
            require(provenance == report['frontend_provenance_after'], 'frontend-or-helper-drift')
            require(build == validate_build_record(args.build_observation, provenance), 'build-record-drift')
            report['frontend_stable'] = True
        except BaseException:
            report['frontend_stable'] = False
            report['cleanup_errors'].append('frontend-stability-failed')
        try:
            report['source_sha256_after'] = source_hashes()
            require(sources is not None and sources == report['source_sha256_after'], 'source-drift')
            report['source_stable'] = True
        except BaseException:
            report['source_stable'] = False
            report['cleanup_errors'].append('source-stability-failed')
        try:
            require(binaries is not None, 'missing-binary-inputs')
            after = runtime.binary_hashes({key: row['path'] for key, row in binaries.items()},
                                          include_python=False)
            require(after == binaries, 'binary-drift')
            require(module_before == module_fingerprints(args.playwright_module), 'playwright-drift')
            require(native_build is not None and native_build == validate_go_build_record(
                args.go_build_record, args.go_binary, args.image_codec), 'native-build-drift')
            report['binary_sha256_after'] = {key: row['sha256'] for key, row in after.items()}
            report['binaries_stable'] = True
        except BaseException:
            report['binaries_stable'] = False
            report['cleanup_errors'].append('binary-stability-failed')
        report['cleanup_verified'] = bool(report.get('fixture_cleanup') and
            report.get('owned_directory_removed') and report.get('sensitive_cleanup', {}).get('verified') and
            all(state in ('stopped', 'not_started') for state in report['service_cleanup'].values()) and
            not report['cleanup_errors'])
        report['success'] = bool(len(report['versions']) == 2 and report['source_stable'] and
            report['frontend_stable'] and report['binaries_stable'] and
            report['cleanup_verified'] and not report['errors'])
        report['acceptance_gate'] = report['success']
        # Constructed public fields contain no arbitrary browser errors/URLs,
        # raw credential-bearing snapshots or raw child/service diagnostics.
        browser.oracle.write_publishable(output / 'results.json', report, private_values)
    print(json.dumps({'success': report['success'], 'live_executed': report['live_executed'],
                      'receipt': str(output / 'results.json')}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
