"""Offline controls only: no sockets, service startup, installed browser or Django."""
import copy
from contextlib import contextmanager, ExitStack, nullcontext
from email.message import Message
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace, MappingProxyType
import unittest
from unittest.mock import Mock, patch

from TESTS.adapters.django import message_reads_browser_runtime as harness
from TESTS.adapters.django.historical_read_cache import (
    HistoricalReadCacheSnapshot, HistoricalReadCacheEntry,
    HistoricalReadCacheContrasts, PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY,
)

LOGIN_COOKIE = ('BKN_AUTH=TOKEN-SECRET.PAYLOAD.SIGNATURE; Path=/; Secure; HttpOnly; '
                'SameSite=Strict; Max-Age=14400')


def state():
    return {'models': {f'model.{i}': [] for i in range(128)},
            'cache': HistoricalReadCacheSnapshot(MappingProxyType({}))}


class PathPolicyTests(unittest.TestCase):
    def route(self, path, method='GET', version='v2', upgrade=False):
        return harness.route_target(path, method, version=version,
            artifacts={'index.html': 'a' * 64, 'build-assets/main.js': 'b' * 64}, upgrade=upgrade)

    def test_every_exact_selected_endpoint_has_only_the_selected_backend(self):
        for version, backend in (('v1', 'django-v1'), ('v2', 'go-v2')):
            for path, method in harness.browser.SELECTED_ROUTES.items():
                with self.subTest(version=version, path=path):
                    self.assertEqual(self.route(path, method, version),
                        {'kind': 'selected', 'backend': backend, 'path': path.removeprefix('/api')})

    def test_named_coexistence_always_django(self):
        for version in ('v1', 'v2'):
            for path in harness.browser.COEXISTENCE_ROUTES:
                self.assertEqual(self.route(path, version=version)['backend'], 'django-coexistence')
        self.assertEqual(self.route('/api/instance/logo.png?v=20261026010101000000')['path'],
                         '/instance/logo.png')

    def test_unchanged_dashboard_widget_requests_keep_django_coexistence(self):
        # Literal requests authored in UI/src/components/widgets/*.svelte and
        # handled by BE/application/views/statistic_views.py.
        widgets = ('subscriptions', 'payments', 'associates', 'bestcourses',
                   'todaylessons', 'expiringcarnets', 'expiringmedicalcertificates',
                   'expiredmedicalcertificates', 'subscriptionstoapprove',
                   'incomeAndExpenses', 'expiredPayments')
        for version in ('v1', 'v2'):
            for widget in widgets:
                with self.subTest(version=version, widget=widget):
                    self.assertEqual(self.route('/api/statistic/dashboard?widget=' + widget,
                                               version=version),
                                     {'kind': 'coexistence', 'backend': 'django-coexistence',
                                      'path': '/statistic/dashboard'})
        for query in ('widget=unknown', 'widget=', 'widget=payments&widget=associates',
                      'widget=payments&group_id=foreign', 'widget=payments&',
                      'widget=%70ayments', 'Widget=payments', 'token=SECRET'):
            with self.subTest(query=query), self.assertRaises(RuntimeError):
                self.route('/api/statistic/dashboard?' + query)
        for path in ('/api/communications/messages/list', '/api/profile/info',
                     '/api/communications/staff-board/list', '/api/association/export/active'):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.route(path + '?widget=payments')

    def test_traversal_ambiguity_unknown_paths_external_targets_and_methods_fail(self):
        invalid = [
            ('/../index.html', 'GET'), ('/%2e%2e/index.html', 'GET'),
            ('/%252e%252e/index.html', 'GET'), ('/build-assets/../index.html', 'GET'),
            ('/build-assets//main.js', 'GET'), ('/build-assets%2fmain.js', 'GET'),
            ('/build-assets\\main.js', 'GET'), ('/%00', 'GET'), ('/%XX', 'GET'),
            ('/%E0%A4%A', 'GET'), ('//external.test/', 'GET'),
            ('https://external.test/', 'GET'), ('/index.html#secret', 'GET'),
            ('/unknown', 'GET'), ('/api/unknown', 'GET'),
            ('/api/communications/messages/list/extra', 'GET'),
            ('/api/communications/messages/list?token=secret', 'GET'),
            ('/api/instance/logo.png?v=secret', 'GET'), ('/api/profile/info', 'POST'),
            ('/api/oauth2/login', 'GET'), ('/', 'HEAD'), ('/', 'POST'),
        ]
        for path, method in invalid:
            with self.subTest(path=path), self.assertRaises((RuntimeError, UnicodeError, ValueError)):
                self.route(path, method)

    def test_static_requires_exact_manifest_entry_and_never_spa_fallback(self):
        self.assertEqual(self.route('/')['file'], 'index.html')
        self.assertEqual(self.route('/build-assets/main.js?v=1')['file'], 'build-assets/main.js')
        for path in ('/login', '/communication/messages', '/missing.js'):
            with self.assertRaises(RuntimeError):
                self.route(path)

    def test_websocket_exact_paths_and_explicit_query_only(self):
        for path in harness.browser.WEBSOCKET_ROUTES:
            self.assertEqual(self.route(path, upgrade=True)['backend'], 'django-websocket')
            with self.assertRaises(RuntimeError):
                self.route(path)
        self.route('/ws/notifications/?token=abc.def.ghi', upgrade=True)
        for path in ('/ws/unknown/', '/ws/health/?token=abc.def.ghi',
                     '/ws/notifications/?token=abc.def.ghi&impersonation=other'):
            with self.assertRaises(RuntimeError):
                self.route(path, upgrade=True)

    def test_observed_peer_cannot_be_inferred_from_requested_backend(self):
        self.assertEqual(harness.actual_backend(('127.0.0.1', 1001), 1001, 1002), 'django-v1')
        self.assertEqual(harness.actual_backend(('127.0.0.1', 1002), 1001, 1002), 'go-v2')
        for peer, django, go, options in ((('8.8.8.8', 1002), 1001, 1002, {}),
                (('127.0.0.1', 1003), 1001, 1002, {}),
                (('127.0.0.1', 1001), 1001, 1001, {}),
                (('127.0.0.1', 1002), 1001, 1002, {'coexistence': True})):
            with self.assertRaises(RuntimeError):
                harness.actual_backend(peer, django, go, **options)


class ProtocolTests(unittest.TestCase):
    def test_context_closure_cannot_be_missing_reordered_or_acknowledged_before_snapshot(self):
        observed = []
        gate = harness.Checkpoints(lambda *_: state(), lambda actor, baseline: observed.append(actor))
        frame = {'type': 'context_closed', 'seq': 1, 'actor': 'owner'}
        with self.assertRaises(RuntimeError):
            gate.context_closed(frame)
        for seq, action in ((1, 'login'), (2, 'read')):
            gate.receive({'type': 'checkpoint', 'seq': seq, 'actor': 'owner', 'action': action})
        for changes in ({'actor': 'foreign_owner'}, {'seq': 2}, {'seq': True}, {'extra': 'SECRET'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                gate.context_closed(dict(frame, **changes))
        self.assertEqual(gate.context_closed(frame), {'type': 'context_ack', 'seq': 1, 'success': True})
        self.assertEqual(observed, ['owner'])
        with self.assertRaises(RuntimeError):
            gate.context_closed(frame)

    def test_delayed_websocket_application_must_finish_before_lifecycle_snapshot(self):
        class Suspend:
            def __await__(self): yield 'disconnect-work'
        async def app(scope, receive, send):
            await Suspend()
        observer = harness.CompleteAsgiObserver(app)
        running = observer({'type': 'websocket', 'path': '/ws/health/'}, None, None)
        self.assertEqual(running.send(None), 'disconnect-work')
        observer.wait_idle(timeout=0)  # live HTTP checkpoints may coexist with WS
        snapshot = Mock(return_value=state())
        with self.assertRaises(RuntimeError):
            harness.observe_closed_state(observer, snapshot, state(), None, timeout=0)
        snapshot.assert_not_called()
        with self.assertRaises(StopIteration): running.send(None)
        observer.wait_idle(timeout=0, include_websockets=True)
        self.assertEqual(observer.websocket_started, observer.websocket_completed)

    def test_disconnect_model_and_cache_writes_fail_after_application_return(self):
        contrasts = HistoricalReadCacheContrasts(PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY,
                                                 2000000000000, b'persistent', b'expiring')
        for mutation in ('none', 'model', 'cache'):
            baseline = state()
            baseline['cache'] = HistoricalReadCacheSnapshot(MappingProxyType({
                PERSISTENT_CONTRAST_KEY: HistoricalReadCacheEntry(b'string', b'persistent', -1),
                EXPIRING_CONTRAST_KEY: HistoricalReadCacheEntry(b'string', b'expiring', 2000000000000)}))
            current = {'models': copy.deepcopy(baseline['models']), 'cache': baseline['cache']}
            class Suspend:
                def __await__(self): yield 'disconnect'
            async def app(scope, receive, send):
                await Suspend()
                if mutation == 'model': current['models']['model.1'] = [{'late-write': True}]
                if mutation == 'cache':
                    entries = dict(current['cache'].entries)
                    entries[b'late-write'] = HistoricalReadCacheEntry(b'string', b'changed', -1)
                    current['cache'] = HistoricalReadCacheSnapshot(MappingProxyType(entries))
            observer = harness.CompleteAsgiObserver(app)
            running = observer({'type': 'websocket'}, None, None)
            running.send(None)
            with self.assertRaises(StopIteration): running.send(None)
            if mutation == 'none':
                _actual, proof = harness.observe_closed_state(observer, lambda: current, baseline, contrasts, timeout=0)
                self.assertEqual(proof['models']['models'], 128)
            else:
                with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                    harness.observe_closed_state(observer, lambda: current, baseline, contrasts, timeout=0)

    def test_eleven_real_snapshots_finish_before_each_ack(self):
        observations = []
        def snapshot(actor, action):
            observations.append((actor, action))
            return state()
        checkpoints = harness.Checkpoints(snapshot, lambda actor, baseline: {'actor': actor})
        for index, (actor, action) in enumerate(harness.browser.CHECKPOINTS, 1):
            if action in ('reload', 'denied'):
                checkpoints.context_closed({'type': 'context_closed', 'seq': len(checkpoints.closed) + 1,
                                            'actor': actor})
            ack = checkpoints.receive({'type': 'checkpoint', 'seq': index, 'actor': actor, 'action': action})
            self.assertEqual(len(observations), index)
            self.assertEqual(len(checkpoints.states), index)
            self.assertEqual(ack, {'type': 'ack', 'seq': index, 'success': True})
        checkpoints.complete()

    def test_bad_missing_duplicate_reordered_or_out_of_scope_checkpoint_fails(self):
        for mutation in ({'seq': 0}, {'seq': True}, {'seq': 2}, {'actor': 'foreign_owner'},
                         {'action': 'read'}, {'extra': 'secret'}, {'type': 'result'}):
            gate = harness.Checkpoints(lambda *_: state())
            frame = {'type': 'checkpoint', 'seq': 1, 'actor': 'owner', 'action': 'login', **mutation}
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                gate.receive(frame)
            self.assertEqual(gate.states, [])
        with self.assertRaises(RuntimeError):
            harness.Checkpoints(lambda *_: state()).complete()
        gate = harness.Checkpoints(lambda *_: {'models': {}, 'cache': None})
        with self.assertRaises(RuntimeError):
            gate.receive({'type': 'checkpoint', 'seq': 1, 'actor': 'owner', 'action': 'login'})

    def test_snapshot_failure_never_acknowledges(self):
        def snapshot(*_args):
            raise OSError('secret password')
        gate = harness.Checkpoints(snapshot)
        with self.assertRaises(OSError):
            gate.receive({'type': 'checkpoint', 'seq': 1, 'actor': 'owner', 'action': 'login'})
        self.assertEqual(gate.states, [])

    def test_proxy_checkpoint_rejects_active_requests_and_resets_after_failure(self):
        journal = harness.ProxyJournal()
        journal.active_http = 1
        with self.assertRaises(RuntimeError), journal.checkpoint():
            self.fail('must not yield')
        journal.active_http = 0
        with self.assertRaises(ValueError), journal.checkpoint():
            self.assertTrue(journal.checkpointing)
            raise ValueError('snapshot failed')
        self.assertFalse(journal.checkpointing)
        with self.assertRaises(RuntimeError), journal.checkpoint():
            journal.fail('unexpected-request')
            with self.assertRaises(RuntimeError):
                journal.snapshot()

    def test_complete_asgi_waits_past_last_body_and_records_whole_window(self):
        class Suspend:
            def __await__(self):
                yield 'after-body'
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200})
            await send({'type': 'http.response.body', 'body': b'complete'})
            await Suspend()
        async def send(_message):
            return None
        observer = harness.CompleteAsgiObserver(app)
        running = observer({'type': 'http', 'method': 'GET', 'path': '/actual', 'headers': []}, None, send)
        self.assertEqual(running.send(None), 'after-body')
        self.assertEqual(observer.delta(0)[0]['status'], 200)
        with self.assertRaises(RuntimeError):
            observer.wait_idle(timeout=0)
        with self.assertRaises(StopIteration):
            running.send(None)
        observer.wait_idle(timeout=0)
        self.assertEqual(observer.delta(0)[0]['path'], '/actual')


class ProvenanceAndPrivacyTests(unittest.TestCase):
    def test_native_record_binds_exact_compiler_builder_embedded_inputs_and_both_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {'BE_GO/cmd/main.go': 'package main\n//go:embed data/*\n',
                     'BE_GO/cmd/data/embedded.dat': 'embedded', 'BE_GO/native/codec.c': 'C input',
                     'BE_GO/cmd/data/retained.dat': 'retained wildcard entry',
                     'BE_GO/native/codec.h': 'header', 'BE_GO/go.mod': 'module fixture',
                     'BE_GO/go.sum': 'modules', 'TESTS/adapters/go/server.py': 'builder',
                     'TESTS/adapters/django/login_jpeg.py': 'codec builder'}
            for name, value in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            executable, codec = root / 'server', root / 'image-codec'
            executable.write_bytes(b'reviewed Go bytes')
            codec.write_bytes(b'reviewed codec bytes')
            record = {'success': True, 'source_stable': True,
                      'source_sha256': harness.native_source_hashes(root),
                      'artifacts': {'server': harness.sha(executable), 'image-codec': harness.sha(codec)},
                      'builder': 'TESTS.adapters.go.server.build_server'}
            record['source_sha256']['TESTS/adapters/django/message_reads.py'] = 'unrelated-old-http-hash'
            record['source_sha256']['BE_GO/ignored_test.go'] = 'uncompiled-old-test-hash'
            path = root / 'build-record.json'
            path.write_text(json.dumps(record))
            proof = harness.validate_go_build_record(path, executable, codec, root=root)
            self.assertFalse(proof['old_full_source_map_revalidated'])
            self.assertEqual(len(proof['source_sha256']), len(files))
            for name in files:
                source = root / name
                original = source.read_text()
                source.write_text(original + '\nchanged')
                with self.subTest(name=name), self.assertRaises(RuntimeError):
                    harness.validate_go_build_record(path, executable, codec, root=root)
                source.write_text(original)
            for name in ('BE_GO/cmd/data/embedded.dat', 'BE_GO/native/codec.h', 'BE_GO/cmd/main.go'):
                source = root / name
                original = source.read_bytes()
                source.unlink()
                with self.subTest(deleted=name), self.assertRaises(RuntimeError):
                    harness.validate_go_build_record(path, executable, codec, root=root)
                source.write_bytes(original)
            added = root / 'BE_GO/added.go'
            added.write_text('package added')
            with self.assertRaises(RuntimeError):
                harness.validate_go_build_record(path, executable, codec, root=root)
            added.unlink()
            for artifact in (executable, codec):
                original = artifact.read_bytes()
                artifact.write_bytes(b'older unbound executable')
                with self.subTest(artifact=artifact.name), self.assertRaises(RuntimeError):
                    harness.validate_go_build_record(path, executable, codec, root=root)
                artifact.write_bytes(original)
            missing = copy.deepcopy(record)
            missing['source_sha256'].pop('BE_GO/cmd/data/embedded.dat')
            path.write_text(json.dumps(missing))
            with self.assertRaises(RuntimeError):
                harness.validate_go_build_record(path, executable, codec, root=root)

    def test_independent_parent_record_exact_schema_digest_and_source_output_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provenance = {'source_sha256': {'UI/postcss.config.cjs': 'a' * 64, 'helper.py': 'b' * 64},
                          'artifact_sha256': {'index.html': 'c' * 64}}
            raw = {'invocation': ['node', 'vite', 'build', '--sourcemap'],
                   'source_sha256': {'UI/postcss.config.cjs': 'a' * 64},
                   'artifact_sha256': provenance['artifact_sha256'], 'success': True,
                   'exit_code': 0, 'source_unchanged': True, 'build_inputs_unchanged': True}
            (root / 'build-record.json').write_text(json.dumps(raw))
            observation = {key: raw[key] for key in ('invocation', 'source_sha256', 'artifact_sha256')}
            observation['record_sha256'] = harness.sha(root / 'build-record.json')
            path = root / 'build-observation.json'
            path.write_text(json.dumps(observation))
            self.assertEqual(harness.validate_build_record(path, provenance), observation)
            for key, value in (('source_sha256', {}), ('artifact_sha256', {}),
                               ('record_sha256', 'd' * 64), ('invocation', [])):
                bad = dict(observation, **{key: value})
                path.write_text(json.dumps(bad))
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    harness.validate_build_record(path, provenance)
            path.write_text(json.dumps(observation))
            (root / 'build-record.json').write_text(json.dumps(dict(raw, success=False)))
            with self.assertRaises(RuntimeError):
                harness.validate_build_record(path, provenance)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(RuntimeError):
            json.loads('{"x":1,"x":2}', object_pairs_hook=harness.unique_object)

    def test_credentials_exclusive_0600_and_no_symlink_follow(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'secret'
            harness.private_write(path, 'ordinary-password')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                harness.private_write(path, 'changed')
            link = Path(temporary) / 'link'
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                harness.private_write(link, 'changed')
            self.assertEqual(path.read_text(), 'ordinary-password')

    def test_output_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'receipt'
            harness.fresh_output(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(RuntimeError):
                harness.fresh_output(path)

    def test_private_raw_cache_snapshot_preserves_dump_type_and_absolute_expiry(self):
        raw_state = state()
        entry = HistoricalReadCacheEntry(b'hash', b'raw-secret-dump', 2000000000000)
        raw_state['cache'] = HistoricalReadCacheSnapshot(MappingProxyType({b'secret-key': entry}))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'checkpoint.json'
            harness.save_snapshot(path, raw_state)
            data = json.loads(path.read_text())
            self.assertEqual(len(data['models']), 128)
            self.assertEqual(data['cache'][0]['expires_at_ms'], 2000000000000)
            self.assertEqual(data['cache'][0]['type_b64'], 'aGFzaA==')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_login_changes_are_separate_from_read_baseline(self):
        before, after = state(), state()
        after['models']['model.1'] = [{'last_login': 'now'}]
        effect = harness.login_effect(before, after, 'owner')
        self.assertEqual(effect['changed_models'], ['model.1'])
        self.assertFalse(effect['read_only_login_claimed'])
        self.assertNotEqual(effect['before_models']['sha256'], effect['after_models']['sha256'])

    def test_prepared_login_only_advances_actual_actor_last_login(self):
        before = state()
        before['models']['application.User'] = before['models'].pop('model.0')
        user_id = harness.browser.authored_spec()['actors']['owner']['user_id']
        before['models']['application.User'] = [{'user_id': {'type': 'uuid', 'value': user_id},
            'last_login': None, 'username': 'PREPARED-OWNER', 'password': 'pbkdf2_sha256$unchanged'}]
        after = copy.deepcopy(before['models'])
        after['application.User'][0]['last_login'] = {'type': 'datetime', 'value': '2026-10-26T01:00:01Z'}
        after = {'models': after, 'cache': before['cache']}
        started = harness.datetime(2026, 10, 26, 1, 0, tzinfo=harness.timezone.utc)
        finished = started + harness.timedelta(seconds=2)
        self.assertTrue(harness.validate_login_projection(before, after, user_id, started, finished)
                        ['password_hash_preserved'])
        for mutation in ('message-write', 'password', 'wrong-user', 'clock', 'unchanged-clock'):
            changed = {'models': copy.deepcopy(after['models']), 'cache': before['cache']}
            user = changed['models']['application.User'][0]
            if mutation == 'message-write': changed['models']['model.1'] = [{'unexpected': True}]
            if mutation == 'password': user['password'] = 'pbkdf2_sha256$changed'
            if mutation == 'wrong-user': user['user_id'] = {'type': 'uuid', 'value': 'foreign-id'}
            if mutation == 'clock': user['last_login']['value'] = '2026-10-26T01:00:03Z'
            if mutation == 'unchanged-clock': user['last_login'] = None
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                harness.validate_login_projection(before, changed, user_id, started, finished)

    def test_public_error_never_reflects_exception_text(self):
        for error in (RuntimeError('Authorization: bearer-secret?token=x'),
                      ValueError('password=cookie-secret'), OSError('/private/secret')):
            encoded = json.dumps(harness.public_error('runtime', error))
            for secret in ('bearer-secret', 'password', 'cookie-secret', '/private'):
                self.assertNotIn(secret, encoded)
        self.assertEqual(harness.public_error('runtime', harness.HarnessError('proxy-failure'))['code'],
                         'proxy-failure')

    def test_child_failures_have_only_bounded_stage_class_and_fixed_reason(self):
        valid = {'type': 'failure', 'stage': 'transport-probe', 'code': 'transport-probe-failed',
                 'error_class': 'AssertionError'}
        self.assertEqual(harness.validate_child_failure(valid), {key: valid[key] for key in
                                                               ('stage', 'code', 'error_class')})
        for changes in ({'stage': 'secret-url'}, {'code': 'token-secret'},
                        {'error_class': 'password-secret'}, {'message': 'cookie-secret'},
                        {'stage': []}, {'error_class': []}, {'code': 'startup-failed'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                harness.validate_child_failure(dict(valid, **changes))

    def test_exercise_diagnostic_protocol_retains_authored_context_and_rejects_secret_fields(self):
        frame = {'type': 'failure', 'stage': 'browser-exercise', 'code': 'browser-exercise-failed',
                 'error_class': 'AssertionError', 'workflow_step': 'login-identity',
                 'exercise_failures': [{'kind': 'workflow-assertion', 'actor': 'owner',
                                        'action': 'login', 'error_class': 'AssertionError'}]}
        checked = harness.validate_child_failure(frame)
        self.assertEqual(checked['workflow_step'], 'login-identity')
        self.assertEqual(checked['exercise_failures'][0]['actor'], 'owner')
        overflow = dict(frame, exercise_failures=frame['exercise_failures'] * 16,
                        failure_count=25, failures_truncated=True,
                        response_checks=[{'actor': 'owner', 'check': 'backend-header'}])
        retained = harness.validate_child_failure(overflow)
        self.assertEqual(retained['failure_count'], 25)
        self.assertTrue(retained['failures_truncated'])
        self.assertEqual(retained['response_checks'][0]['check'], 'backend-header')
        for key, value in (('failure_count', True), ('failures_truncated', False),
                           ('response_checks', [{'actor': 'owner', 'check': 'TOKEN-SECRET'}]),
                           ('response_checks', [{'actor': 'owner', 'check': 'backend-header', 'body': 'SECRET'}])):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                harness.validate_child_failure(dict(overflow, **{key: value}))
        for change in ('top-secret', 'message', 'actor', 'action', 'kind', 'error-class', 'step', 'oversize'):
            bad = copy.deepcopy(frame)
            row = bad['exercise_failures'][0]
            if change == 'top-secret': bad['body'] = 'TOKEN-SECRET'
            if change == 'message': row['message'] = 'PASSWORD-SECRET'
            if change in ('actor', 'action', 'kind'): row[change] = 'TOKEN-SECRET'
            if change == 'error-class': row['error_class'] = 'COOKIE-SECRET'
            if change == 'step': bad['workflow_step'] = 'https://SECRET/'
            if change == 'oversize': bad['exercise_failures'] *= 17
            with self.subTest(change=change), self.assertRaises(harness.HarnessError) as raised:
                harness.validate_child_failure(bad)
            self.assertNotIn('SECRET', json.dumps(harness.public_error('browser-v1', raised.exception)))

    def test_workflow_field_protocol_is_closed_and_matches_helper_enums(self):
        program = """
import {WORKFLOW_STEPS, CONTROL_FIELDS} from './TESTS/browser/message-reads.mjs';
console.log(JSON.stringify({steps:WORKFLOW_STEPS, fields:CONTROL_FIELDS}));
"""
        result = subprocess.run(['node', '--input-type=module', '-e', program], cwd=harness.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        enums = json.loads(result.stdout)
        self.assertEqual(set(enums['steps']), harness.WORKFLOW_STEPS)
        self.assertEqual(set(enums['fields']), harness.CONTROL_FIELDS)
        frame = {'type': 'failure', 'stage': 'browser-exercise', 'code': 'browser-exercise-failed',
                 'error_class': 'AssertionError', 'workflow_step': 'controls-comparison',
                 'exercise_failures': [{'kind': 'workflow-assertion', 'actor': 'owner',
                                       'action': 'read', 'error_class': 'AssertionError'}],
                 'controls_mismatches': enums['fields']}
        self.assertEqual(harness.validate_child_failure(frame)['controls_mismatches'], enums['fields'])
        for fields in (None, {}, 'summary', ['TOKEN-SECRET'], ['summary', 'summary'],
                       [{'body': 'TOKEN-SECRET'}], ['summary'] * 14):
            with self.subTest(fields=fields), self.assertRaises(harness.HarnessError) as raised:
                harness.validate_child_failure(dict(frame, controls_mismatches=fields))
            self.assertNotIn('SECRET', json.dumps(harness.public_error('browser-v1', raised.exception)))
        for extra in ({'actual': 'TOKEN-SECRET'}, {'expected': 'TOKEN-SECRET'},
                      {'body': 'TOKEN-SECRET'}, {'error': 'TOKEN-SECRET'}, {'dom': 'TOKEN-SECRET'},
                      {'workflow_step': 'search'}):
            with self.subTest(extra=extra), self.assertRaises(harness.HarnessError):
                harness.validate_child_failure(dict(frame, **extra))

    def test_actual_helper_workflow_failures_keep_stage_and_only_known_mismatch_fields(self):
        program = r'''
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {exercise} from './TESTS/browser/message-reads.mjs';
import {childFailure} from './TESTS/browser/message-reads-runner.mjs';
let text='';for await(const chunk of process.stdin)text+=chunk;
const spec=JSON.parse(text), frames=[], unhandled=[];
process.on('unhandledRejection',error=>unhandled.push(error));
const targets=['sidebar-navigation','list-navigation','list-status','list-header','list-body',
    'rendered-header','rendered-row-count','rendered-row-contents','rendered-row-comparison',
    'forbidden-row-count','controls-headings','search','search-empty','search-clear',
    'sort-ascending','sort-descending','sort-reset','page-size','page-controls',
    'controls-comparison','reread','reload','context-close','callback','callback-close',
    'callback-comparison','complete'];
for(const target of targets){
    const input={...structuredClone(spec),origin:'http://127.0.0.1:9',version:'v2',
        artifact_sha256:{'index.html':'a'.repeat(64)}};
    let stage, actorIndex=0, checkpoints=0, closes=0, faulted=false;
    const steps=[], originals=[];
    const guard=()=>{if(stage===target&&!faulted){faulted=true;assert.fail('TOKEN-SECRET DOM BODY');}};
    const browser={async newContext(){
        const actor=input.actors[Object.keys(input.actors)[actorIndex++]];
        const context=new EventEmitter(),page=new EventEmitter();
        let routeHandler,query='',sort=0,size='10',collects=0;
        const ids=()=> query===actor.negative_query?[]:query?actor.controls.search_ids:
            sort===1?actor.controls.sort_asc_ids:sort===2?actor.controls.sort_desc_ids:actor.visible_rows.map(r=>r.id);
        const send=async(path,method)=>{
            const request={url:()=>input.origin+path,method:()=>method,redirectedFrom:()=>null,
                allHeaders:async()=>({}),resourceType:()=> 'fetch'};
            const response={request:()=>request,url:request.url,fromServiceWorker:()=>false,
                status:()=>stage==='list-status'&&target==='list-status'?599:200,
                headerValue:async()=>stage==='list-header'&&target==='list-header'?'TOKEN-SECRET':'go-v2',
                body:async()=>Buffer.from('{}'),finished:async()=>null,dispose:async()=>{},
                json:async()=>path.endsWith('/login')?{user_data:{user_id:actor.user_id,requires_welcome:false}}:
                    stage==='list-body'&&target==='list-body'?{body:'TOKEN-SECRET'}:actor.expected_response};
            context.emit('request',request);
            await routeHandler({request:()=>request,fetch:async()=>response,
                fulfill:async()=>{context.emit('response',response);page.emit('response',response);},
                abort:async()=>assert.fail('Unexpected abort')});
        };
        const locator=(selector,row)=>({
            selector,first(){return this;},getByText(){return this;},
            locator:child=>locator(child==='p'?selector:child,row),
            fill:async value=>{guard();if(selector==='search')query=value;},
            click:async()=>{guard();
                if(selector==='#bkn_login_signin_submit')await send('/api/oauth2/login','POST');
                if(selector==='a[href="/#/communication/messages"]')await send('/api/communications/messages/list','GET');
                if(selector==='clear')query='';
                if(selector==='.datatable-head [data-field="created_at"]')sort=(sort+1)%3;
            },
            isVisible:async()=>{guard();return selector!=='notice';},
            count:async()=>{guard();return selector.startsWith('#action-col-')||selector==='.datatable-loading'||
                selector==='a[href="/#/communication/messages"]'&&actorIndex===4?0:
                selector==='.datatable-pager-link-number'?(target.includes('comparison')&&
                    target!=='rendered-row-comparison'?2:1):ids().length;},
            evaluateAll:async fn=>{guard();return fn(ids().map(id=>({querySelector:()=>({id:'action-col-'+id})})));},
            all:async()=>{guard();collects++;return actor.visible_rows.map(value=>locator('row',value));},
            getAttribute:async name=>{guard();return name==='id'?'action-col-'+row.id:
                row[(selector.includes('message')?'message':'subject')+'_title'];},
            textContent:async()=>{guard();const field=selector.match(/data-field="([^"]+)"/)?.[1];
                return {message:'Contenuto',type:'Tipo',subject:'Oggetto',created_at:'Data'}[field]||'Messaggi';},
            innerText:async()=>{guard();
                if(row){const field=selector.match(/data-field="([^"]+)"/)?.[1];
                    return target==='rendered-row-comparison'&&field==='message'||
                        target==='reread'&&collects===2&&field==='message'?'TOKEN-SECRET':row[field==='created_at'?'date':field];}
                if(selector==='.datatable-pager-detail')return ['controls-comparison','callback-comparison'].includes(target)?
                    'TOKEN-SECRET':actor.controls.summary;
                const field=selector.match(/data-field="([^"]+)"/)?.[1];
                return {message:'CONTENUTO',type:'TIPO',subject:'OGGETTO',created_at:'DATA'}[field]||'Messaggi';
            },
            inputValue:async()=>{guard();return selector==='search'?query:size;},
            selectOption:async value=>{guard();size=value;},isDisabled:async()=>{guard();return true;},
        });
        page.locator=selector=>{guard();return locator(selector);};
        page.getByRole=(role,options)=>{guard();return locator(role==='textbox'?'search':
            options.name==='Cancella ricerca'?'clear':'notice');};
        page.getByText=()=>{guard();return locator('empty');};
        page.goto=async url=>{guard();if(url.endsWith('/communication/messages'))query=actor.negative_query;};
        page.waitForURL=page.waitForLoadState=async()=>{guard();};
        page.evaluate=async()=>{guard();return null;};
        page.reload=async()=>{guard();await send('/api/communications/messages/list','GET');};
        page.setDefaultTimeout=page.setDefaultNavigationTimeout=()=>{};
        page.url=()=>input.origin+'/#/404';
        context.routeWebSocket=async()=>{};context.route=async(pattern,handler)=>{routeHandler=handler;};
        context.newPage=async()=>page;
        context.close=async()=>{guard();closes++;page.emit('close');};
        originals.push([page,page.getByRole,page.locator,page.reload]);
        return context;
    }};
    const expect=value=>({
        toBeVisible:async()=>{guard();},toHaveText:async wanted=>{guard();assert.equal(await value.textContent(),wanted);},
        toHaveAttribute:async()=>{guard();},toHaveValue:async wanted=>{guard();assert.equal(await value.inputValue(),wanted);},
        toHaveCount:async wanted=>{guard();assert.equal(await value.count(),wanted);},
        toContainText:async wanted=>{guard();assert((await value.innerText()).includes(wanted));},
    });
    expect.poll=fn=>({toBe:async wanted=>{guard();assert.equal(await fn(),wanted);},
        toEqual:async wanted=>{guard();assert.deepEqual(await fn(),wanted);}});
    // Session polling executes browser code only in live runs; this fake supplies
    // the authored identity while the helper still performs its original checks.
    const realPoll=expect.poll;
    expect.poll=fn=>stage==='login-session-observation'?{toBe:async()=>{guard();}}:realPoll(fn);
    const receipt=await exercise({browser,expect,input,checkpoint:async()=>{checkpoints++;return {success:true};},
        onWorkflowStep:(step,fields)=>{stage=step;steps.push([step,fields]);
            if(target==='callback'&&step==='search'||target==='callback-close'&&step==='context-close')
                throw new Error('TOKEN-SECRET');
            if(target==='callback-comparison'&&fields.length){fields.push('TOKEN-SECRET');throw new Error('TOKEN-SECRET');}
        }});
    for(const [page,role,loc,reload]of originals){assert.equal(page.getByRole,role);assert.equal(page.locator,loc);assert.equal(page.reload,reload);}
    if(target==='complete'){
        assert.equal(receipt.success,true,JSON.stringify(receipt.workflow_failure));
        assert.equal(checkpoints,11);assert.equal(closes,4);
        continue; // Synthetic helper control only; no acceptance verifier is invoked.
    }
    assert.equal(receipt.success,false,target);
    assert.equal(receipt.workflow_failure.step,target==='callback'?'search':
        target==='callback-close'?'context-close':target==='callback-comparison'?'controls-comparison':target,target);
    assert.equal(steps.at(-1)[0],'context-close',target);
    assert.equal(closes,1,'Cleanup must still close the actual context after an observer error');
    assert(checkpoints>=1,'Actual login checkpoint was reached');
    const mismatches=['controls-comparison','callback-comparison'].includes(target)?['pages','summary']:[];
    assert.deepEqual(receipt.workflow_failure.controls_mismatches,mismatches,target);
    if(target==='callback-comparison')assert.equal(receipt.failures[0].error_class,'AssertionError');
    const frame=childFailure('browser-exercise',new assert.AssertionError({message:'TOKEN-SECRET'}),{
        workflow_step:receipt.workflow_failure.step,controls_mismatches:receipt.workflow_failure.controls_mismatches,
        failures:receipt.failures,network_diagnostics:receipt.network_diagnostics});
    assert(!JSON.stringify(frame).includes('SECRET'),target);
    assert(!JSON.stringify(receipt.workflow_failure).includes('SECRET'),target);
    assert(receipt.failures.some(row=>row.kind==='workflow-assertion'),target);
    frames.push(frame);
}
await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(unhandled,[]);
console.log(JSON.stringify(frames));
'''
        result = subprocess.run(['node', '--input-type=module', '-e', program], cwd=harness.ROOT,
                                input=json.dumps(harness.browser.authored_spec()),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = json.loads(result.stdout)
        self.assertEqual(len(frames), 26)
        for frame in frames:
            checked = harness.validate_child_failure(frame)
            self.assertEqual(checked['workflow_step'], frame['workflow_step'])
            self.assertEqual(checked['controls_mismatches'], frame['controls_mismatches'])

    def test_independent_proxy_and_forwarded_cookie_evidence_requires_exact_pairing(self):
        counts = {'cookie': 2, 'redirect': 5, 'followed': 0, 'cookies_received': True,
                  'post': 1, 'post_body_preserved': True}
        events = [{'path': '/oauth2/login', 'set_cookie_sha256': str(i) * 64,
                   'bkn_auth_cookie_present': True}
                  for i in range(4)]
        child = {'transport': {'redirect_blocked': True, 'cookies_preserved': True,
                               'post_body_preserved': True},
                 'cookies': [{'actor': actor, 'method': 'POST', 'path': '/api/oauth2/login',
                              'sha256': events[i]['set_cookie_sha256'], 'bkn_auth_cookie_present': True}
                             for i, actor in enumerate(harness.browser.ACTORS)]}
        harness.verify_transport_and_cookies(child, counts, events)
        for key, value in (('actor', 'foreign_owner'), ('method', 'GET'),
                           ('path', '/other'), ('sha256', 'empty-page-headers')):
            changed = copy.deepcopy(child)
            changed['cookies'][0][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                harness.verify_transport_and_cookies(changed, counts, events)
        for presence, digest in ((False, events[0]['set_cookie_sha256']),
                                 (True, harness.json_digest([]))):
            changed_events = copy.deepcopy(events)
            changed_events[0].update(bkn_auth_cookie_present=presence, set_cookie_sha256=digest)
            with self.assertRaises(RuntimeError):
                harness.verify_transport_and_cookies(child, counts, changed_events)

    def test_actual_login_cookie_must_be_nonempty_expected_name_and_supported_contract(self):
        self.assertTrue(harness.required_login_cookie([LOGIN_COOKIE, 'other=value']))
        for values in ([], ['other=value'], [LOGIN_COOKIE.replace('BKN_AUTH=', 'WRONG_NAME=')],
                       [LOGIN_COOKIE.replace('TOKEN-SECRET.PAYLOAD.SIGNATURE', '')],
                       [LOGIN_COOKIE, LOGIN_COOKIE], [LOGIN_COOKIE.replace('; Secure', '')],
                       [LOGIN_COOKIE.replace('SameSite=Strict', 'SameSite=Lax')]):
            with self.subTest(values=values):
                self.assertFalse(harness.required_login_cookie(values))


class ProxyOfflineTests(unittest.TestCase):
    def dispatch(self, *, path='/api/communications/messages/list', method='GET', status=200,
                 peer=('127.0.0.1', 1002), body=None, raw_headers=None, checkpointing=False,
                 cookie_headers=None):
        journal = harness.ProxyJournal()
        journal.checkpointing = checkpointing
        Handler = harness.proxy_handler(ui=Path('/not-used'), artifacts={}, version='v2',
            django_port=1001, go_port=1002, journal=journal)
        handler = Handler.__new__(Handler)
        handler.command, handler.path = method, path
        handler.server = SimpleNamespace(server_port=9000)
        handler.headers = Message()
        headers = {'Host': '127.0.0.1:9000', 'Authorization': 'Bearer TOKEN-SECRET',
                   'Cookie': 'SESSION-SECRET', **(raw_headers or {})}
        if path == harness.browser.LIST_PATH:
            headers['X-Correlation-ID'] = 'message-browser-v2-owner-read-1'
        if body is not None:
            headers['Content-Length'] = str(len(body))
        for key, value in headers.items():
            handler.headers[key] = value
        handler.rfile, handler.wfile = io.BytesIO(body or b''), io.BytesIO()
        handler.send_response_only, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        response = Mock(status=status, length=0)
        values = [LOGIN_COOKIE, 'other=SESSION-SECRET; SameSite=Strict'] if cookie_headers is None else cookie_headers
        response.getheaders.return_value = [('Set-Cookie', value) for value in values] + [('Content-Length', '2')]
        response.read.return_value = b'{}'
        response.isclosed.return_value = True
        connection = Mock()
        connection.sock.getpeername.return_value = peer
        connection.getresponse.return_value = response
        with patch.object(harness.http.client, 'HTTPConnection', return_value=connection) as factory:
            handler.dispatch()
        return handler, journal, connection, factory

    def test_real_response_duplicate_cookies_and_post_body_forwarded_unchanged(self):
        handler, journal, upstream, factory = self.dispatch(path='/api/oauth2/login', method='POST',
                                                           body=b'ordinary=PASSWORD-SECRET')
        upstream.endheaders.assert_called_once_with(b'ordinary=PASSWORD-SECRET')
        factory.assert_called_once_with('127.0.0.1', 1002, timeout=30)
        cookies = [call.args for call in handler.send_header.call_args_list if call.args[0] == 'Set-Cookie']
        self.assertEqual(cookies, [('Set-Cookie', LOGIN_COOKIE),
                                   ('Set-Cookie', 'other=SESSION-SECRET; SameSite=Strict')])
        self.assertEqual(handler.wfile.getvalue(), b'{}')
        self.assertEqual(journal.events[0]['backend'], 'go-v2')
        self.assertEqual(journal.events[0]['set_cookie_sha256'], harness.json_digest([
            LOGIN_COOKIE, 'other=SESSION-SECRET; SameSite=Strict']))
        self.assertEqual(journal.failures, [])
        encoded = json.dumps(journal.events)
        for secret in ('TOKEN-SECRET', 'SESSION-SECRET', 'PASSWORD-SECRET', 'Bearer'):
            self.assertNotIn(secret, encoded)

    def test_actual_peer_misroute_fails_before_forwarding_no_fallback(self):
        handler, journal, upstream, _factory = self.dispatch(peer=('127.0.0.1', 1001))
        self.assertTrue(journal.failures)
        upstream.putrequest.assert_not_called()
        upstream.close.assert_called_once()
        self.assertEqual(journal.events[0]['peer'], ['127.0.0.1', 1001])
        self.assertEqual(journal.events[0]['intended_peer'], ['127.0.0.1', 1002])
        self.assertEqual(journal.failures, ['proxy-misroute'])

    def test_actual_upstream_login_missing_or_wrong_cookie_is_rejected_before_fulfillment(self):
        for cookies in ([], ['WRONG_NAME=value']):
            handler, journal, upstream, _factory = self.dispatch(path='/api/oauth2/login', method='POST',
                body=b'private-login-body', cookie_headers=cookies)
            self.assertEqual(journal.failures, ['login-auth-cookie-missing-or-invalid'])
            self.assertFalse(journal.events[0]['bkn_auth_cookie_present'])
            self.assertEqual(handler.wfile.getvalue(), b'')
            upstream.close.assert_called_once()

    def test_websocket_real_handshake_forwarding_never_logs_token_or_reenters_http_barrier(self):
        journal = harness.ProxyJournal()
        Handler = harness.proxy_handler(ui=Path('/unused'), artifacts={}, version='v2',
            django_port=1001, go_port=1002, journal=journal)
        handler = Handler.__new__(Handler)
        handler.path = '/ws/notifications/?token=TOKEN.SECRET.VALUE'
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:9000'
        handler.headers['Upgrade'] = 'websocket'
        handler.wfile, handler.connection = io.BytesIO(), Mock()
        handler.connection.recv.return_value = b''
        handler.counted_http, journal.active_http = True, 1
        upstream = Mock()
        upstream.getpeername.return_value = ('127.0.0.1', 1001)
        handshake = b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n'
        upstream.recv.side_effect = [bytes([byte]) for byte in handshake]
        with patch.object(harness.socket, 'create_connection', return_value=upstream), \
             patch.object(harness.select, 'select', return_value=([handler.connection], [], [])):
            handler.websocket({'backend': 'django-websocket', 'path': '/ws/notifications/'})
        self.assertEqual(handler.wfile.getvalue(), handshake)
        self.assertIn(b'token=TOKEN.SECRET.VALUE', upstream.sendall.call_args.args[0])
        self.assertNotIn('TOKEN', json.dumps(journal.events))
        self.assertEqual(journal.active_http, 0)
        self.assertFalse(handler.counted_http)
        self.assertEqual(journal.events[0]['status'], 101)
        upstream.close.assert_called_once()

    def test_all_redirect_statuses_fail_closed_never_follow_location(self):
        for status in (301, 302, 303, 307, 308):
            handler, journal, upstream, factory = self.dispatch(status=status)
            self.assertTrue(journal.failures)
            self.assertEqual(journal.events[0]['status'], status)
            self.assertEqual(factory.call_count, 1)
            upstream.putrequest.assert_called_once()
            self.assertEqual(handler.wfile.getvalue(), b'')

    def test_unknown_path_query_host_or_snapshot_window_never_connects(self):
        for args in ({'path': '/unknown?token=SECRET'}, {'path': '/api/unknown'},
                     {'raw_headers': {'Host': 'external.test'}}, {'checkpointing': True}):
            handler, journal, _upstream, factory = self.dispatch(**args)
            self.assertTrue(journal.failures)
            factory.assert_not_called()
            self.assertNotIn('SECRET', json.dumps(journal.events + journal.failures))

    def test_hop_headers_removed_cookie_headers_preserved_as_list(self):
        headers = [('Connection', 'keep-alive, X-Private-Hop'), ('X-Private-Hop', 'drop'),
                   ('Set-Cookie', 'a=1'), ('Set-Cookie', 'b=2'), ('Transfer-Encoding', 'chunked'),
                   ('X-Fixture-Backend', 'spoof'), ('Content-Type', 'application/json')]
        self.assertEqual(harness.clean_headers(headers, response=True),
            [('Set-Cookie', 'a=1'), ('Set-Cookie', 'b=2'), ('Content-Type', 'application/json')])


class AttributionTests(unittest.TestCase):
    def receipts(self, version='v2'):
        events, responses, django = [], [], []
        backend = 'go-v2' if version == 'v2' else 'django-v1'
        for index, (actor, action) in enumerate(harness.browser.CHECKPOINTS):
            if action not in ('read', 'reload'):
                continue
            cid = f'message-browser-{version}-{actor}-{action}-{index+1}'
            row = {'cid': cid, 'method': 'GET', 'path': harness.browser.UPSTREAM_PATH,
                   'status': 200, 'backend': backend, 'intended_backend': backend, 'fallback': False}
            events.append(row)
            responses.append(dict(row, path=harness.browser.LIST_PATH))
            if version == 'v1':
                django.append({'seq': len(django), 'method': 'GET', 'path': row['path'],
                               'cid': cid, 'status': 200, 'error_type': None})
        return {'version': version, 'responses': responses}, events, django

    def test_complete_windows_v1_and_v2(self):
        for version in ('v1', 'v2'):
            receipt, events, django = self.receipts(version)
            proof = harness.attribution_for(receipt, events, django)
            self.assertEqual(len(proof), 6)
            self.assertEqual(sum(len(row['django_events']) for row in proof), 6 if version == 'v1' else 0)

    def test_extra_uncorrelated_misrouted_or_duplicate_django_traffic_fails(self):
        for version in ('v1', 'v2'):
            for change in ('extra-list', 'django-misroute', 'fallback', 'backend', 'missing', 'duplicate'):
                receipt, events, django = self.receipts(version)
                if change == 'extra-list': events.append(copy.deepcopy(events[0]))
                if change == 'django-misroute':
                    django.append({'seq': len(django), 'method': 'GET', 'path': harness.browser.UPSTREAM_PATH,
                                   'cid': '', 'status': 200, 'error_type': None})
                if change == 'fallback': events[0]['fallback'] = True
                if change == 'backend': events[0]['backend'] = 'django-coexistence'
                if change == 'missing': events.pop()
                if change == 'duplicate':
                    events.append({'method': 'GET', 'path': '/statistic/dashboard', 'cid': '',
                                   'status': 200, 'backend': 'django-coexistence'})
                    django.extend([{'seq': len(django), 'method': 'GET', 'path': '/statistic/dashboard',
                                    'cid': '', 'status': 200, 'error_type': None}] * 2)
                with self.subTest(version=version, change=change), self.assertRaises(RuntimeError):
                    harness.attribution_for(receipt, events, django)


class CleanupAndChildTests(unittest.TestCase):
    def test_owned_http_listener_activates_burst_backlog_and_cleans_up_without_sockets(self):
        # Keep the real TCPServer.server_activate/listen path; replace only the
        # constructor's socket allocation/bind and thread execution. No socket
        # or service is created by this control.
        for body_fails in (False, True):
            with self.subTest(body_fails=body_fails):
                listener, connection, upstream = Mock(), Mock(), Mock()
                listener.accept.return_value = (connection, ('127.0.0.1', 1234))
                servers, threads = [], []

                def initialize(server, address, handler):
                    self.assertEqual(address, ('127.0.0.1', 0))
                    server.socket, server.server_port = listener, 12345
                    servers.append(server)
                    server.server_activate()

                class FakeThread:
                    def __init__(self, **kwargs):
                        self.options, self.alive, self.joins = kwargs, False, []
                        threads.append(self)

                    def start(self): self.alive = True
                    def is_alive(self): return self.alive
                    def join(self, timeout):
                        self.joins.append(timeout)
                        self.alive = False

                journal = harness.ProxyJournal()
                journal.sockets.add(upstream)
                with patch.object(harness.ThreadingHTTPServer, '__init__', initialize), \
                     patch.object(harness.ThreadingHTTPServer, 'shutdown') as shutdown, \
                     patch.object(harness.threading, 'Thread', FakeThread), \
                     patch.object(harness.socket, 'socket', side_effect=AssertionError('no sockets')), \
                     patch.object(harness.socket, 'create_connection', side_effect=AssertionError('no connections')):
                    try:
                        with harness.owned_http_server(object, journal=journal) as origin:
                            server = servers[0]
                            self.assertEqual(origin, 'http://127.0.0.1:12345')
                            # Assert the actual value handed to listen, rather
                            # than merely checking a declared class attribute.
                            listener.listen.assert_called_once_with(128)
                            request, address = server.get_request()
                            self.assertIs(request, connection)
                            connection.settimeout.assert_called_once_with(35)
                            self.assertIn(connection, server.connections)
                            server.process_request(request, address)
                            self.assertEqual(server.workers, threads[1:])
                            self.assertTrue(threads[1].options['daemon'])
                            if body_fails:
                                raise ValueError('owned body failure')
                    except ValueError as error:
                        self.assertTrue(body_fails)
                        self.assertEqual(str(error), 'owned body failure')
                    shutdown.assert_called_once_with()
                    listener.close.assert_called_once_with()
                    for item in (connection, upstream):
                        item.shutdown.assert_called_once_with(harness.socket.SHUT_RDWR)
                        item.close.assert_called_once_with()
                    self.assertTrue(journal.stopping.is_set())
                    self.assertEqual([thread.joins for thread in threads], [[5], [5]])
                    self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_child_failure_retained_before_throw_even_when_cleanup_fails(self):
        class Pipe(io.BytesIO):
            def fileno(self): return 100
        failure = {'type': 'failure', 'stage': 'transport-probe',
                   'code': 'transport-probe-failed', 'error_class': 'AssertionError'}
        for teardown_fails in (False, True):
            with self.subTest(teardown_fails=teardown_fails), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                child = Mock(stdin=Pipe(), stdout=Pipe())
                selector = Mock()
                selector.select.return_value = [object()]
                with patch.object(harness.subprocess, 'Popen', return_value=child), \
                     patch.object(harness.selectors, 'DefaultSelector', return_value=selector), \
                     patch.object(harness.os, 'read', return_value=(json.dumps(failure) + '\n').encode()), \
                     patch.object(harness, 'stop_child', side_effect=OSError('SECRET') if teardown_fails else None):
                    with self.assertRaises(harness.HarnessError) as raised:
                        harness.run_node(node=Path('/node'), module=Path('/module'), work=root,
                            private_log=io.StringIO(), environment={}, input_data={'password': 'SECRET'},
                            checkpoint=harness.Checkpoints(lambda *_: state()), probe_origin='http://127.0.0.1:9')
                public = harness.public_error('browser-v1', raised.exception)
                self.assertEqual(public['child_failure']['stage'], 'transport-probe')
                self.assertEqual(public['child_failure']['code'], 'transport-probe-failed')
                self.assertNotIn('SECRET', json.dumps(public))
                self.assertFalse((root / 'browser-input.json').exists())
                self.assertTrue(child.stdin.closed and child.stdout.closed)

    def test_orchestrator_failure_stops_services_before_full_restore_and_restores_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(output=root / 'receipt', ui_dir=root, build_observation=root / 'build.json',
                playwright_module=root, go_binary=root / 'go', image_codec=root / 'codec', node=root / 'node',
                go_build_record=root / 'native.json')
            events = []
            @contextmanager
            def database(directory, report):
                work = directory / 'database'
                work.mkdir()
                report['service_cleanup'].update(postgres='running', redis='running')
                with (directory / 'services.log').open('w') as log:
                    try: yield work, 1000, log
                    finally:
                        events.append('database-stop')
                        report['service_cleanup'].update(postgres='stopped', redis='stopped')
                        report['owned_directory_removed'] = True
                        report['sensitive_cleanup'] = {'verified': True}
            @contextmanager
            def django(report):
                report['service_cleanup']['django'] = 'running'
                events.append('django-start')
                try: yield Mock(), 1001
                finally:
                    events.append('django-stop')
                    report['service_cleanup']['django'] = 'stopped'
            @contextmanager
            def go(work, executable, codec, environment, log, report, native_build):
                report['service_cleanup']['go'] = 'running'
                events.append('go-start')
                try: yield 1002
                finally:
                    events.append('go-stop')
                    report['service_cleanup']['go'] = 'stopped'
            def fail_run(**kwargs):
                events.append('browser-failure')
                raise harness.HarnessError('controlled-browser-failure')
            def restore():
                events.append('full-restore')
                return {'all_128_models_restored': True, 'complete_db1_restored': True}
            raw = Mock()
            raw.close.side_effect = lambda: events.append('redis-close')
            connections = Mock()
            connections.close_all.side_effect = lambda: events.append('connections-close')
            modules = {'django.conf.global_settings': SimpleNamespace(PASSWORD_HASHERS=['production']),
                       'django.test.utils': SimpleNamespace(override_settings=lambda **kw: nullcontext()),
                       'django.db': SimpleNamespace(connections=connections),
                       'redis': SimpleNamespace(Redis=lambda **kw: raw, ConnectionError=OSError)}
            environment_before = dict(os.environ)
            fingerprints = {'postgres': {'path': str(root / 'postgres'), 'sha256': 'a' * 64}}
            fixture = {'tokens': {'owner': 'TOKEN-SECRET'}, 'contrasts': None}
            identities = {actor: {'password': 'PASSWORD-SECRET'} for actor in harness.browser.ACTORS}
            replacements = {
                'parse_args': lambda argv: args,
                'validate_build_record': lambda *a: {'record_sha256': 'a' * 64},
                'validate_go_build_record': lambda *a: {'record_sha256': 'a' * 64},
                'module_fingerprints': lambda *a: {'version': '1.58.2', 'files': {}},
                'source_hashes': lambda: {'owned.py': 'a' * 64}, 'private_database': database,
                'FixtureRestore': lambda *a: SimpleNamespace(restore=restore),
                'prepare_browser_identities': lambda *a: identities, 'owned_django': django,
                'owned_go': go, 'run_version': fail_run,
            }
            with ExitStack() as patches:
                patches.enter_context(patch.dict(sys.modules, modules))
                for name, replacement in replacements.items():
                    patches.enter_context(patch.object(harness, name, replacement))
                patches.enter_context(patch('TESTS.adapters.django.production_runtime.require_production_python',
                                           return_value={'success': True}))
                patches.enter_context(patch.object(harness.browser, 'capture_provenance', return_value={}))
                patches.enter_context(patch.object(harness.runtime, 'service_binary_paths',
                                                  return_value={'postgres': root / 'postgres'}))
                patches.enter_context(patch.object(harness.runtime, 'binary_hashes', return_value=fingerprints))
                patches.enter_context(patch.object(harness.runtime, 'private_environment',
                                                  return_value=({'PATH': '/synthetic-only'}, {'TOKEN-SECRET'})))
                patches.enter_context(patch.object(harness.runtime, 'initialize_django_apps',
                                                  return_value=(Mock(), Mock())))
                patches.enter_context(patch.object(harness.runtime, 'migrate_full_schema', return_value={}))
                patches.enter_context(patch.object(harness.browser.oracle, 'seed_fixture', return_value=fixture))
                patches.enter_context(patch('builtins.print'))
                self.assertEqual(harness.main([]), 1)
            self.assertEqual(events, ['django-start', 'go-start', 'browser-failure', 'go-stop',
                'django-stop', 'full-restore', 'connections-close', 'redis-close', 'database-stop'])
            self.assertEqual(dict(os.environ), environment_before)
            report = harness.read_json(root / 'receipt/results.json')
            self.assertFalse(report['success'])
            self.assertFalse(report['acceptance_gate'])
            self.assertTrue(report['cleanup_verified'])
            self.assertEqual(report['errors'][0]['stage'], 'browser-v1')
            self.assertNotIn('SECRET', json.dumps(report))

    def test_full_database_restore_includes_original_schema_and_entire_cache(self):
        raw, log = Mock(), io.StringIO()
        original = state()
        original['cache'] = HistoricalReadCacheSnapshot(MappingProxyType({
            b'old': HistoricalReadCacheEntry(b'hash', b'original-dump', 2000000000000),
            b'persistent': HistoricalReadCacheEntry(b'string', b'dump', -1)}))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def command(argv, **kwargs):
                if argv[0].endswith('pg_dump'):
                    Path(argv[argv.index('--file') + 1]).write_text('complete original schema and state')
                return SimpleNamespace(returncode=0)
            fake_django_db = SimpleNamespace(connections=Mock())
            with patch.object(harness, 'durable_snapshot', return_value=original), \
                 patch.object(harness.subprocess, 'run', side_effect=command) as commands, \
                 patch.dict(sys.modules, {'django.db': fake_django_db}):
                restore = harness.FixtureRestore(None, raw, root, root, log, {})
                receipt = restore.restore()
            self.assertTrue(receipt['all_128_models_restored'])
            self.assertTrue(receipt['complete_db1_restored'])
            self.assertIn('--clean', commands.call_args_list[0].args[0])
            self.assertIn('--single-transaction', commands.call_args_list[1].args[0])
            self.assertIn('ON_ERROR_STOP=1', commands.call_args_list[1].args[0])
            raw.flushdb.assert_called_once()
            raw.restore.assert_any_call(b'old', 2000000000000, b'original-dump', replace=True, absttl=True)
            raw.restore.assert_any_call(b'persistent', 0, b'dump', replace=True)
            self.assertFalse((root / 'original.sql').exists())

    def test_failed_or_hung_child_credential_file_removed_and_pipes_closed(self):
        class Pipe(io.BytesIO):
            def fileno(self): return 100
        for mode in ('eof', 'bad-frame', 'deadline', 'checkpoint-before-ready'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                child = Mock(stdin=Pipe(), stdout=Pipe())
                child.poll.return_value = 1
                selector = Mock()
                selector.select.return_value = [object()]
                chunks = [b''] if mode == 'eof' else [b'{"type":"unknown"}\n', b'']
                if mode == 'checkpoint-before-ready':
                    chunks = [b'{"type":"checkpoint","seq":1,"actor":"owner","action":"login"}\n']
                with patch.object(harness.subprocess, 'Popen', return_value=child), \
                     patch.object(harness.selectors, 'DefaultSelector', return_value=selector), \
                     patch.object(harness.os, 'read', side_effect=chunks), \
                     patch.object(harness, 'stop_child') as stopped:
                    with self.assertRaises(RuntimeError):
                        harness.run_node(node=Path('/node'), module=Path('/module'), work=root,
                            private_log=io.StringIO(), environment={}, input_data={'password': 'SECRET'},
                            checkpoint=harness.Checkpoints(lambda *_: state()),
                            probe_origin='http://127.0.0.1:9', timeout=-1 if mode == 'deadline' else 1)
                self.assertFalse((root / 'browser-input.json').exists())
                self.assertTrue(child.stdin.closed and child.stdout.closed)
                stopped.assert_called_once_with(child)
                selector.close.assert_called_once()

    def test_full_pipe_exchange_and_snapshot_failure_before_ack(self):
        class Pipe(io.BytesIO):
            def fileno(self): return 100
        for fail_snapshot in (False, True):
            with self.subTest(fail_snapshot=fail_snapshot), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binary = root / 'existing-browser'
                binary.write_bytes(b'preexisting executable, never executed')
                binary.chmod(0o700)
                stamp = {'path': str(binary), 'sha256': harness.sha(binary)}
                child = Mock(stdin=Pipe(), stdout=Pipe(), returncode=0)
                selector = Mock()
                selector.select.return_value = [object()]
                frames = [{'type': 'ready', 'browser_binary': stamp}]
                closure_index = 0
                for index, (actor, action) in enumerate(harness.browser.CHECKPOINTS, 1):
                    if action in ('reload', 'denied'):
                        closure_index += 1
                        frames.append({'type': 'context_closed', 'seq': closure_index, 'actor': actor})
                    frames.append({'type': 'checkpoint', 'seq': index, 'actor': actor, 'action': action})
                frames.append({'type': 'result', 'receipt': {}, 'transport': {}, 'cookies': [],
                               'browser_closed': True, 'browser_binary': stamp})
                chunks = [(json.dumps(frame) + '\n').encode() for frame in frames] + [b'']
                order, writes = [], []
                def snapshot(actor, action):
                    order.append((actor, action))
                    if fail_snapshot:
                        raise OSError('secret SQL details')
                    return state()
                def write(data):
                    ack = json.loads(data)
                    writes.append(ack)
                    if ack['type'] == 'ack':
                        self.assertEqual(len(order), ack['seq'])
                    return len(data)
                child.stdin.write = write
                with patch.object(harness.subprocess, 'Popen', return_value=child), \
                     patch.object(harness.selectors, 'DefaultSelector', return_value=selector), \
                     patch.object(harness.os, 'read', side_effect=chunks), \
                     patch.object(harness, 'stop_child'):
                    def run():
                        return harness.run_node(node=Path('/node'), module=Path('/module'), work=root,
                            private_log=io.StringIO(), environment={}, input_data={'password': 'SECRET'},
                            checkpoint=harness.Checkpoints(snapshot, lambda actor, baseline: {'actor': actor}),
                            probe_origin='http://127.0.0.1:9')
                    if fail_snapshot:
                        with self.assertRaises(OSError):
                            run()
                        self.assertEqual(writes, [{'type': 'ready_ack', 'success': True}])
                    else:
                        result = run()
                        self.assertEqual(result['browser_binary'], stamp)
                        self.assertEqual(len(writes), 16)
                        self.assertEqual(tuple(order), harness.browser.CHECKPOINTS)
                self.assertFalse((root / 'browser-input.json').exists())

    def test_teardown_failure_still_removes_credentials_and_closes_both_pipes(self):
        class Pipe(io.BytesIO):
            def fileno(self): return 100
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = Mock(stdin=Pipe(), stdout=Pipe())
            selector = Mock()
            selector.select.return_value = [object()]
            selector.close.side_effect = OSError('private selector failure')
            with patch.object(harness.subprocess, 'Popen', return_value=child), \
                 patch.object(harness.selectors, 'DefaultSelector', return_value=selector), \
                 patch.object(harness.os, 'read', return_value=b''), \
                 patch.object(harness, 'stop_child', side_effect=OSError('private teardown failure')):
                with self.assertRaises(harness.HarnessError) as error:
                    harness.run_node(node=Path('/node'), module=Path('/module'), work=root,
                        private_log=io.StringIO(), environment={}, input_data={'password': 'SECRET'},
                        checkpoint=harness.Checkpoints(lambda *_: state()), probe_origin='http://127.0.0.1:9')
            self.assertEqual(error.exception.code, 'browser-child-cleanup-failed')
            self.assertFalse((root / 'browser-input.json').exists())
            self.assertTrue(child.stdin.closed and child.stdout.closed)

    def test_child_launch_failure_removes_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(harness.subprocess, 'Popen', side_effect=OSError('launch failed')):
                with self.assertRaises(OSError):
                    harness.run_node(node=Path('/node'), module=Path('/module'), work=root,
                        private_log=io.StringIO(), environment={}, input_data={'password': 'SECRET'},
                        checkpoint=harness.Checkpoints(lambda *_: state()), probe_origin='http://127.0.0.1:9')
            self.assertFalse((root / 'browser-input.json').exists())

    def test_cleanup_escalates_terminated_child_and_reaps_browser_group(self):
        child = Mock(pid=1234)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('node', 10), 0]
        with patch.object(harness.os, 'killpg') as kill:
            harness.stop_child(child)
        child.terminate.assert_called_once()
        self.assertEqual(kill.call_count, 2)
        child.wait.assert_any_call(timeout=5)

    def test_imports_have_no_service_network_process_or_filesystem_writes(self):
        script = '''
import builtins, socket, subprocess, os
def forbidden(*a, **kw): raise RuntimeError('runtime side effect during import')
class GuardedSocket(socket.socket):
    def __new__(cls, *a, **kw): return forbidden()
socket.socket = GuardedSocket
socket.create_connection = forbidden
subprocess.Popen = forbidden
os.mkdir = forbidden
import TESTS.adapters.django.message_reads_browser_runtime
'''
        result = subprocess.run([sys.executable, '-B', '-c', script], cwd=harness.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_node_bridge_offline_ack_order_timeout_eof_and_cleanup(self):
        script = r'''
import assert from 'node:assert/strict';
import {PassThrough} from 'node:stream';
import {createBridge} from './TESTS/browser/message-reads-runner.mjs';
const tick = () => new Promise(resolve => setImmediate(resolve));
const unhandled = [];
process.on('unhandledRejection', error => unhandled.push(error));
const create = (timeoutMs = 100) => {
    const readable = new PassThrough(), sent = [];
    const bridge = createBridge({readable, send: row => sent.push(row), timeoutMs});
    const ack = row => readable.write(JSON.stringify(row) + '\n');
    return {readable, sent, bridge, ack};
};
{
    const {bridge,sent,ack} = create();
    const ready = bridge.ready({}); ack({type:'ready_ack',success:true}); await ready;
    let sequence = 0, closures = 0;
    for (const actor of ['owner','full_collaborator','foreign_owner','restricted_collaborator']) {
        for (const action of actor === 'restricted_collaborator' ? ['login','denied'] : ['login','read','reload']) {
            if (action === 'reload' || action === 'denied') {
                let complete = false;
                const closing = bridge.contextClosed(actor).then(()=>{complete=true;});
                assert.deepEqual(sent.at(-1), {type:'context_closed',seq:++closures,actor});
                await tick(); assert.equal(complete,false,'Disconnect snapshot must precede next actor');
                ack({type:'context_ack',seq:closures,success:true}); await closing;
            }
            const pending = bridge.checkpoint({actor,action});
            ack({type:'ack',seq:++sequence,success:true}); await pending;
        }
    }
    bridge.complete(); bridge.close();
}
{
    const {bridge, readable, sent, ack} = create();
    let done = false;
    const ready = bridge.ready({path: '/existing/browser', sha256: 'a'.repeat(64)});
    assert.equal(sent[0].type, 'ready');
    ack({type:'ready_ack', success:true}); await ready;
    const pending = bridge.checkpoint({actor:'owner', action:'login'}).then(() => {done = true;});
    await tick(); assert.equal(done, false, 'No UI progress before durable parent ack');
    ack({type:'ack', seq:1, success:true}); await pending; assert.equal(done, true);
    await assert.rejects(bridge.checkpoint({actor:'foreign_owner', action:'read'}));
    bridge.close(); assert.equal(readable.listenerCount('data'), 0);
}
for (const mode of ['wrong-seq','negative','eof','timeout','extra-key','concurrent']) {
    const {bridge, readable, ack} = create(10);
    const ready = bridge.ready({}); ack({type:'ready_ack',success:true}); await ready;
    const pending = bridge.checkpoint({actor:'owner',action:'login'});
    const outcome = pending.then(() => false, () => true);
    if (mode === 'wrong-seq') ack({type:'ack',seq:2,success:true});
    if (mode === 'negative') ack({type:'ack',seq:1,success:false});
    if (mode === 'extra-key') ack({type:'ack',seq:1,success:true,token:'secret'});
    if (mode === 'eof') readable.end();
    if (mode === 'concurrent') {
        await assert.rejects(bridge.checkpoint({actor:'owner',action:'read'}));
        bridge.close();
    }
    assert.equal(await outcome, true); bridge.close();
}
await tick(); assert.deepEqual(unhandled, []);
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=harness.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_response_observer_pairs_request_digest_and_preserves_helper_failure_rules(self):
        script = r'''
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {forwardContained} from './TESTS/browser/message-reads.mjs';
import {observeForwardedRoute, forwardedLoginCookie, childFailure, main}
    from './TESTS/browser/message-reads-runner.mjs';
const unhandled = [];
process.on('unhandledRejection', error => unhandled.push(error));
const cookies = ['BKN_AUTH=TOKEN-SECRET.PAYLOAD.SIGNATURE; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=14400',
    'second=COOKIE-SECRET; Path=/; SameSite=Lax'];
const url = 'http://127.0.0.1:9/api/oauth2/login';
for (const mode of ['ok','observer-fails','redirect-301','redirect-302','redirect-303',
    'redirect-307','redirect-308','wrong-response-url']) {
    const events = [], forwarded = [];
    const request = {url: () => url, method: () => 'POST'};
    const response = {
        url: () => mode === 'wrong-response-url' ? url + '/other' : url,
        status: () => mode.startsWith('redirect-') ? Number(mode.slice(9)) : 200,
        headersArray: async () => {events.push('headers'); return cookies.map(value => ({name:'Set-Cookie',value}));},
        dispose: async () => {events.push('dispose');},
    };
    const route = {
        request: () => request,
        fetch: async options => {
            events.push('fetch'); assert.equal(options.maxRedirects,0); assert.equal(options.maxRetries,0);
            return response;
        },
        fulfill: async options => {events.push('fulfill'); assert.equal(options.response,response);
            assert.deepEqual(Object.keys(options),['response']);},
        abort: async () => {events.push('abort');},
    };
    const observed = observeForwardedRoute(route, async (actualRequest, actualResponse) => {
        assert.equal(actualRequest,request); assert.equal(actualResponse,response);
        events.push('observe');
        if (mode === 'observer-fails') throw new Error('SECRET callback detail');
        forwarded.push(await forwardedLoginCookie('owner',{origin:'http://127.0.0.1:9'},actualRequest,actualResponse));
    });
    if (mode === 'ok') {
        await forwardContained(observed);
        assert.deepEqual(events,['fetch','observe','headers','fulfill','dispose']);
        assert.deepEqual(forwarded,[{actor:'owner',method:'POST',path:'/api/oauth2/login',
            sha256:createHash('sha256').update(JSON.stringify(cookies)).digest('hex'),bkn_auth_cookie_present:true}]);
        assert(!JSON.stringify(forwarded).includes('SECRET'));
    } else {
        await assert.rejects(forwardContained(observed));
        assert.equal(events.filter(x=>x==='abort').length,1);
        assert.equal(events.filter(x=>x==='fulfill').length,0);
        assert.equal(events.at(-1),'dispose');
        if (mode.startsWith('redirect-') || mode === 'wrong-response-url')
            assert(!events.includes('observe'), 'No callback before redirect/pairing rejection');
    }
}
const request = {url:()=>url,method:()=> 'POST'};
const response = {url:()=>url,status:()=>200,headersArray:async()=>cookies.map(value=>({name:'set-cookie',value}))};
for (const [actor,input,req,res] of [
    ['unknown',{origin:'http://127.0.0.1:9'},request,response],
    ['owner',{origin:'http://127.0.0.1:10'},request,response],
    ['owner',{origin:'http://127.0.0.1:9'},{...request,method:()=> 'GET'},response],
    ['owner',{origin:'http://127.0.0.1:9'},request,{...response,url:()=>url+'/wrong'}],
]) await assert.rejects(forwardedLoginCookie(actor,input,req,res));
for (const values of [[], ['other=value'], [cookies[0].replace('BKN_AUTH=', 'wrong=')],
    [cookies[0].replace('TOKEN-SECRET.PAYLOAD.SIGNATURE','')], [cookies[0],cookies[0]]]) {
    await assert.rejects(forwardedLoginCookie('owner',{origin:'http://127.0.0.1:9'},request,
        {...response,headersArray:async()=>values.map(value=>({name:'Set-Cookie',value}))}));
}
const arbitrary = {name:'TOKEN-SECRET',message:'PASSWORD-SECRET',stack:'COOKIE-SECRET'};
assert.deepEqual(childFailure('https://secret/',arbitrary),
    {type:'failure',stage:'startup',code:'startup-failed',error_class:'Other'});
const frames = [];
assert.equal(await main(['--unexpected-secret'], frame=>frames.push(frame)),1);
assert.equal(frames.length,1); assert.equal(frames[0].stage,'startup');
assert(!JSON.stringify(frames).includes('SECRET'));
await new Promise(resolve=>setImmediate(resolve)); assert.deepEqual(unhandled,[]);
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=harness.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exercise_diagnostics_and_login_step_markers_are_read_only_and_fail_closed(self):
        script = r'''
import assert from 'node:assert/strict';
import {sanitizedExerciseFailures, childFailure, observeLoginContract}
    from './TESTS/browser/message-reads-runner.mjs';
const actual = [{kind:'workflow-assertion',actor:'owner',action:'login',error_class:'AssertionError'},
    {kind:'pageerror',actor:'owner',action:'login',error_class:'ReferenceError'}];
const safe = childFailure('browser-exercise', new Error('TOKEN-SECRET'),
    {workflow_step:'login-navigation',failures:actual});
assert.equal(safe.type,'failure'); assert.equal(safe.code,'browser-exercise-failed');
assert.equal(safe.workflow_step,'login-navigation'); assert.deepEqual(safe.exercise_failures,actual);
for (const field of ['kind','actor','action','error_class','message','url','body','stack']) {
    const unsafe = [{...actual[0],[field]:'PASSWORD-SECRET'}];
    const rows = sanitizedExerciseFailures(unsafe);
    assert(!JSON.stringify(rows).includes('SECRET'));
    if (field !== 'error_class') assert.equal(rows[0].kind,'invalid-exercise-failure');
    else assert.equal(rows[0].error_class,'Other');
}
for (const value of [undefined,[]])
    assert.equal(sanitizedExerciseFailures(value)[0].kind,'invalid-exercise-failure');
const overflowing=actual.concat(Array(20).fill(actual[0]));
const overflow=childFailure('browser-exercise',new Error(),{workflow_step:'response-observation',failures:overflowing});
assert.deepEqual(overflow.exercise_failures,overflowing.slice(0,16));
assert.equal(overflow.failure_count,22);assert.equal(overflow.failures_truncated,true);
const mixed=sanitizedExerciseFailures([actual[0],{...actual[0],body:'TOKEN-SECRET'},actual[1]]);
assert.deepEqual(mixed[0],actual[0]);assert.deepEqual(mixed[2],actual[1]);
assert.equal(mixed[1].kind,'invalid-exercise-failure');assert(!JSON.stringify(mixed).includes('SECRET'));
const input = {actors:{owner:{user_id:'fixture-owner'}}};
const request = {url:()=> 'http://127.0.0.1:9/api/oauth2/login'};
for (const mode of ['wrong-id','onboarding','correct']) {
    const steps=[];
    const body={user_data:{user_id:mode==='wrong-id'?'TOKEN-SECRET':'fixture-owner',
        requires_welcome:mode==='onboarding'},access_token:'TOKEN-SECRET'};
    const response={json:async()=>body};
    const run=()=>observeLoginContract('owner',input,request,response,step=>steps.push(step));
    if (mode==='correct') {await run();assert.equal(steps.at(-1),'login-contract-complete');}
    else {await assert.rejects(run()); assert.equal(steps.at(-1),mode==='wrong-id'?'login-identity':'login-onboarding');}
    assert(!JSON.stringify(steps).includes('SECRET'));
    assert.equal(body.access_token,'TOKEN-SECRET','Read-only response observation');
}
const controlFailure = childFailure('browser-exercise', new Error('TOKEN-SECRET'),
    {workflow_step:'controls-comparison',failures:actual,controls_mismatches:['summary','pages']});
assert.deepEqual(controlFailure.controls_mismatches,['summary','pages']);
for (const fields of [null,['TOKEN-SECRET'],['summary','summary'],{body:'TOKEN-SECRET'},
    ['summary',{body:'TOKEN-SECRET'}],Array(14).fill('summary')]) {
    const rejected = childFailure('browser-exercise', new Error('TOKEN-SECRET'),
        {workflow_step:'controls-comparison',failures:actual,controls_mismatches:fields});
    assert.equal(rejected.controls_mismatches,null);
    assert(!JSON.stringify(rejected).includes('SECRET'));
}
assert.equal(childFailure('browser-exercise',new Error(),
    {workflow_step:'search',failures:actual,controls_mismatches:['summary']}).controls_mismatches,null);

'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=harness.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_exercise_forwarding_diagnostics_reach_python_failure_protocol(self):
        script = r'''
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {exercise} from './TESTS/browser/message-reads.mjs';
import {childFailure,forwardedLoginCookie} from './TESTS/browser/message-reads-runner.mjs';
let text='';for await(const chunk of process.stdin)text+=chunk;
const authored=JSON.parse(text),frames=[],unhandled=[];
process.on('unhandledRejection',error=>unhandled.push(error));
for(const mode of ['normal','fetch','fulfill','dispose','observe','abort','navigation-aborted']) {
    const input={...authored,version:'v2',origin:'http://127.0.0.1:9',artifact_sha256:{'index.html':'a'.repeat(64)}};
    const context=new EventEmitter(),page=new EventEmitter();let handler,snapshots=0,observed=0;
    context.routeWebSocket=async()=>{};context.route=async(pattern,callback)=>{handler=callback;};
    context.newPage=async()=>page;context.close=async()=>page.emit('close');
    page.setDefaultTimeout=page.setDefaultNavigationTimeout=()=>{};
    const frame={url:()=>input.origin+'/',parentFrame:()=>null};page.mainFrame=()=>frame;
    page.goto=page.waitForURL=page.waitForLoadState=async()=>{};page.evaluate=async()=>null;
    page.getByRole=()=>({isVisible:async()=>false});
    const dependency={url:()=>input.origin+'/api/profile/info',method:()=> 'GET',resourceType:()=> 'fetch',
        redirectedFrom:()=>null,isNavigationRequest:()=>false,failure:()=>({errorText:'net::ERR_ABORTED'})};
    page.locator=()=>({fill:async()=>{},click:async()=>{
        const login={url:()=>input.origin+'/api/oauth2/login',method:()=> 'POST',resourceType:()=> 'fetch',
            redirectedFrom:()=>null,isNavigationRequest:()=>false};
        async function send(request,bad) {
            const data=Buffer.from(JSON.stringify({user_data:{user_id:input.actors.owner.user_id,requires_welcome:false}}));
            const response={request:()=>request,url:request.url,status:()=>200,fromServiceWorker:()=>false,
                headerValue:async()=> 'go-v2',body:async()=>data,finished:async()=>null,json:async()=>JSON.parse(data),
                headersArray:()=>[{name:'Set-Cookie',value:'BKN_AUTH=TOKEN-SECRET.PAYLOAD.SIGNATURE; HttpOnly; Secure; Path=/; SameSite=Strict; Max-Age=3600'}],
                dispose:async()=>{if(bad&&mode==='dispose')throw new Error('apiResponse.dispose: Target page, context or browser has been closed');}};
            context.emit('request',request);
            await handler({request:()=>request,fetch:async options=>{
                assert.equal(options.maxRedirects,0);
                if(bad&&['fetch','abort'].includes(mode))throw new Error('route.fetch: socket hang up\nTOKEN-SECRET');
                return response;
            },fulfill:async options=>{
                assert.equal(options.response,response);
                if(bad&&mode==='fulfill')throw new Error('route.fulfill: Fetch response has been disposed');
                context.emit('response',response);page.emit('response',response);
            },abort:async()=>{
                context.emit('requestfailed',request);
                if(bad&&mode==='abort')throw new Error('route.abort: Route is already handled!');}});
        }
        await send(login,false);
        if(mode==='navigation-aborted') {
            context.emit('request',dependency);
            const document={...dependency,url:()=>input.origin+'/',resourceType:()=> 'document',
                isNavigationRequest:()=>true,frame:()=>frame};
            // This document receives a full valid response through the same route.
            await send(document,false);page.emit('framenavigated',frame);
            context.emit('requestfailed',dependency);
        } else await send(dependency,true);
    }});
    const expect=()=>({toBeVisible:async()=>{}});expect.poll=()=>({toBe:async()=>{}});
    const receipt=await exercise({browser:{newContext:async()=>context},expect,input,
        onForwardedResponse:async(actor,request,response)=>{
            observed++;
            if(request.url().endsWith('/api/profile/info')&&mode==='observe')throw new Error('TOKEN-SECRET');
            if(request.url().endsWith('/api/oauth2/login'))assert(await forwardedLoginCookie(actor,input,request,response));
        },checkpoint:async()=>{snapshots++;throw new Error('Stop after verified login baseline');}});
    assert.equal(receipt.success,false);
    const diagnostics=receipt.network_diagnostics;
    if(mode==='normal') {
        assert.equal(snapshots,1);assert.equal(observed,2);assert.equal(diagnostics.failure_count,0);
        for(const stage of ['fetch','observe','fulfill','dispose'])assert.equal(diagnostics.counts[stage].completed,2);
    } else {
        assert.equal(snapshots,0,'Failed dependency must prevent checkpoint');
        const expected=mode==='abort'?'fetch':mode==='navigation-aborted'?'requestfailed':mode;
        assert.equal(diagnostics.failures[0].stage,expected);
        assert.equal(diagnostics.failures[0].target,'/api/profile/info');
        if(mode==='abort')assert.deepEqual(diagnostics.failures.map(row=>row.stage),['fetch','requestfailed','abort']);
        if(mode==='navigation-aborted') {
            const row=diagnostics.failures[0];assert.equal(row.category,'browser-aborted');
            assert.equal(row.navigation_request_id,3);assert.equal(row.navigation_epoch,1);
            assert.equal(row.request_navigation_epoch,0);assert.equal(row.navigation_state,'root');
            assert(receipt.failures.some(row=>row.kind==='requestfailed'));
        }
    }
    const failure=childFailure('browser-exercise',new Error('TOKEN-SECRET'),{
        workflow_step:'login-network-idle',failures:receipt.failures,network_diagnostics:diagnostics});
    assert(failure.network_diagnostics);assert(!JSON.stringify(failure).includes('SECRET'));frames.push(failure);
}
await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(unhandled,[]);
process.stdout.write(JSON.stringify(frames));
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=harness.ROOT,
            input=json.dumps(harness.browser.authored_spec()), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = json.loads(result.stdout)
        self.assertEqual(len(frames), 7)
        for frame in frames:
            with self.subTest(stage=frame['network_diagnostics']['failures']):
                retained = harness.validate_child_failure(frame)
                self.assertEqual(retained['network_diagnostics'], frame['network_diagnostics'])
                public = harness.public_error('browser-v1', harness.HarnessError('browser-child-failed', retained))
                self.assertNotIn('SECRET', json.dumps(public))
        base = frames[1]
        row = base['network_diagnostics']['failures'][0]
        for key in row:
            bad = copy.deepcopy(base)
            bad['network_diagnostics']['failures'][0][key] = 'TOKEN-SECRET'
            with self.subTest(field=key), self.assertRaises(harness.HarnessError):
                harness.validate_child_failure(bad)
        for change in ('extra', 'unknown-stage', 'count', 'overflow', 'truncated', 'counts', 'order', 'null'):
            bad = copy.deepcopy(base)
            diagnostics = bad['network_diagnostics']
            if change == 'extra': diagnostics['failures'][0]['url'] = 'TOKEN-SECRET'
            if change == 'unknown-stage': diagnostics['failures'][0]['stage'] = 'TOKEN-SECRET'
            if change == 'count': diagnostics['failure_count'] = True
            if change == 'overflow': diagnostics['failures'] *= 17
            if change == 'truncated': diagnostics['failures_truncated'] = True
            if change == 'counts': diagnostics['counts']['fetch']['failed'] = 0
            if change == 'order': diagnostics['failures'][0]['event'] = 0
            if change == 'null': bad['network_diagnostics'] = None
            with self.subTest(change=change), self.assertRaises(harness.HarnessError):
                harness.validate_child_failure(bad)

    def test_actual_helper_response_error_path_retains_fixed_assertion_codes(self):
        script = r'''
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createHash} from 'node:crypto';
import {exercise} from './TESTS/browser/message-reads.mjs';
import {childFailure} from './TESTS/browser/message-reads-runner.mjs';
let data='';for await(const chunk of process.stdin)data+=chunk;
const authored=JSON.parse(data),unhandled=[];
process.on('unhandledRejection',error=>unhandled.push(error));
for (const mode of ['classification','service-worker','redirect','backend-header','static-status',
    'static-body','api-status','body-completion','body-throws','header-throws','overflow']) {
    const bytes=Buffer.from('exact-built-bytes');
    const input={...authored,version:'v2',origin:'http://127.0.0.1:9',
        artifact_sha256:{'index.html':createHash('sha256').update(bytes).digest('hex')}};
    const context=new EventEmitter(),page=new EventEmitter(),checks=[];
    context.routeWebSocket=context.route=async()=>{};
    context.newPage=async()=>page;context.close=async()=>{page.emit('close');};
    page.setDefaultTimeout=page.setDefaultNavigationTimeout=()=>{};
    page.goto=page.waitForURL=page.waitForLoadState=async()=>{};
    page.evaluate=async()=>null;
    page.getByRole=()=>({isVisible:async()=>false});
    function emit(path,method,bad=false) {
        const request={url:()=>input.origin+path,method:()=>method,
            redirectedFrom:()=>bad&&mode==='redirect'?{}:null};
        const response={request:()=>request,url:request.url,
            fromServiceWorker:()=>bad&&mode==='service-worker',
            status:()=>bad&&['static-status','api-status'].includes(mode)?500:200,
            headerValue:async()=>{if(bad&&mode==='header-throws')throw new Error('TOKEN-SECRET');
                return bad&&['backend-header','overflow'].includes(mode)?'wrong':path==='/'?'ui-static':'go-v2';},
            body:async()=>{if(bad&&mode==='body-throws')throw new Error('TOKEN-SECRET');
                return bad&&mode==='static-body'?Buffer.from('wrong bytes'):bytes;},
            finished:async()=>bad&&mode==='body-completion'?new Error('TOKEN-SECRET'):null,
            json:async()=>({user_data:{user_id:input.actors.owner.user_id,requires_welcome:false}})};
        context.emit('request',request);context.emit('response',response);page.emit('response',response);
    }
    page.locator=()=>({fill:async()=>{},click:async()=>{
        emit('/api/oauth2/login','POST');
        for(let i=0;i<(mode==='overflow'?24:1);i++)
            emit(mode==='classification'?'/unexpected':mode==='api-status'?'/api/profile/info':'/','GET',true);
    }});
    const expect=()=>({toBeVisible:async()=>{}});expect.poll=()=>({toBe:async()=>{}});
    let snapshots=0;
    const receipt=await exercise({browser:{newContext:async()=>context},expect,input,
        onResponseCheck:(actor,check)=>{assert.equal(actor,'owner');checks.push(check);},
        checkpoint:async()=>{snapshots++;return {success:false};}});
    assert.equal(receipt.success,false);assert.equal(snapshots,0);
    assert(receipt.failures.some(row=>row.kind==='response-or-route-validation'));
    const expected=mode==='body-throws'?'body-completion':['overflow','header-throws'].includes(mode)?'backend-header':mode;
    assert(checks.includes(expected),mode+' diagnostic lost');
    const frame=childFailure('browser-exercise',new Error(),{workflow_step:'response-observation',
        failures:receipt.failures,response_checks:checks.map(check=>({actor:'owner',check}))});
    assert(frame.response_checks.some(row=>row.check===expected));
    if(mode==='overflow') {
        assert.equal(frame.failure_count,receipt.failures.length);
        assert(frame.failure_count>16);assert.equal(frame.failures_truncated,true);
        assert.equal(frame.exercise_failures.length,16);
        assert(frame.exercise_failures.every(row=>row.kind==='response-or-route-validation'));
    }
    assert(!JSON.stringify(frame).includes('SECRET'));
}
await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(unhandled,[]);
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=harness.ROOT,
            input=json.dumps(harness.browser.authored_spec()), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
