"""Offline integration controls; mocked receipts never establish runtime evidence."""
import copy
from contextlib import contextmanager
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from TESTS.adapters.go import message_restore_image as restore
from TESTS.adapters.go import message_mutations_image as image
from TESTS.test_message_mutations_image import H, IID, cleanup


def helper_result(case):
    result = dict(name='message-mutations-restore-' + case, success=True, models=128,
        exact_effects=True, full_redis=True, backend='go', transport='packaged-actual-http',
        fixture_reset_owned_by_parent=True)
    if case.endswith('lease'):
        result.update(exact_owner_row=True, blocked_backend=456,
            shared_lease_while_blocked=True, exclusive_after_completed_effects=True)
    else:
        result.update(running_admission=True, running_row_removed=True)
    return result


def receipt():
    results = []
    for case in restore.CASE_NAMES:
        result = helper_result(case)
        if 'blocked_backend' in result:
            result['blocked_backend_sha256'] = image.contract.source_digest(result.pop('blocked_backend'))
        results.append(dict(case=case, result=result, fixture_cleanup=cleanup(image.FIXTURE_CLEANUP),
            restored=dict(state=True, cache=True, sequences=True),
            probes=dict(before=True, blocked=2 if case.endswith('lease') else 0, after=True)))
    return dict(verified=True, backend='go', transport='packaged-actual-http',
        process_sha256=H, identity_sha256=H, go_binary_sha256=H,
        client_ip=dict(verified=True, method='owned-container-tcp-peer', address_sha256=H, process_sha256=H),
        baseline=dict(state_sha256=H, cache_sha256=H, sequences_sha256=H), results=results)


class ReceiptControls(unittest.TestCase):
    def test_complete_receipt_binds_all_four_real_result_shapes(self):
        native = dict(process_sha256=H, go_binary_sha256=H,
            baseline=receipt()['baseline'], client_ip=receipt()['client_ip'])
        self.assertTrue(restore.receipt_valid(receipt(), native))
        changes = [lambda r: r.update(verified=False), lambda r: r['results'].pop(),
            lambda r: r['results'].append(copy.deepcopy(r['results'][0])),
            lambda r: r['results'].__setitem__(1, copy.deepcopy(r['results'][0])),
            lambda r: r.update(process_sha256='b'*64), lambda r: r.update(go_binary_sha256='b'*64),
            lambda r: r['client_ip'].update(address_sha256='b'*64),
            lambda r: r['baseline'].update(cache_sha256='b'*64),
            lambda r: r.update(identity_sha256='bad'), lambda r: r.update(results='mock')]
        for index in range(4):
            changes.extend([
                lambda r, i=index: r['results'][i]['result'].update(success=False),
                lambda r, i=index: r['results'][i]['result'].update(full_redis=False),
                lambda r, i=index: r['results'][i]['result'].update(models=True),
                lambda r, i=index: r['results'][i]['result'].update(transport='mock'),
                lambda r, i=index: r['results'][i]['result'].update(extra='private'),
                lambda r, i=index: r['results'][i]['result'].pop('exact_effects'),
                lambda r, i=index: r['results'][i]['fixture_cleanup']['results'].update(cache=False),
                lambda r, i=index: r['results'][i]['restored'].update(state=False),
                lambda r, i=index: r['results'][i]['probes'].update(before=False),
                lambda r, i=index: r['results'][i]['probes'].update(after=False),
                lambda r, i=index: r['results'][i]['probes'].update(blocked=True)])
        changes.extend([lambda r: r['results'][0]['probes'].update(blocked=1),
            lambda r: r['results'][2]['probes'].update(blocked=1),
            lambda r: r['results'][1]['result'].update(blocked_backend_sha256='bad'),
            lambda r: r['results'][2]['result'].update(running_row_removed=False)])
        for change in changes:
            candidate = receipt()
            change(candidate)
            with self.subTest(receipt=candidate):
                self.assertFalse(restore.receipt_valid(candidate, native))

    def test_private_helper_output_rejects_invented_or_weakened_results(self):
        for case in restore.CASE_NAMES:
            restore.validate_result(case, helper_result(case))
            for field in helper_result(case):
                broken = helper_result(case)
                broken.pop(field)
                with self.subTest(case=case, field=field), self.assertRaises(AssertionError):
                    restore.validate_result(case, broken)
            for broken in (None, True, dict(helper_result(case), models=True),
                           dict(helper_result(case), exact_effects=1),
                           dict(helper_result(case), mock=True)):
                with self.assertRaises(AssertionError):
                    restore.validate_result(case, broken)

    def test_probe_sequence_requires_idle_busy_and_post_completion(self):
        for case in restore.CASE_NAMES:
            valid = [True, False, False, True] if case.endswith('lease') else [True, True]
            self.assertTrue(restore.probe_receipt(case, valid)['after'])
            for values in ([], [False], [True], [True, False], [True, 0, 0, True],
                           [True, False, True] if case.endswith('lease') else [True, False, True]):
                with self.subTest(case=case, values=values), self.assertRaises(AssertionError):
                    restore.probe_receipt(case, values)


class IdentityControls(unittest.TestCase):
    def test_inspection_is_budgeted_and_binds_mount_process_config_uid_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            operations = Path(directory) / 'image-operations'
            operations.mkdir()
            lock = operations / 'lifecycle.lock'
            lock.touch()
            info = {'Id': H, 'Image': IID, 'RestartCount': 0,
                'State': {'Running': True, 'Pid': 42, 'StartedAt': '2026-10-26T12:00:00Z'},
                'Config': {'User': '10001:10001', 'Labels': {image.packaged.LABEL: 'owner'},
                           'Env': ['DATA_RESTORE_LOCK_PATH=/operations/lifecycle.lock']},
                'Mounts': [{'Destination': '/operations', 'Source': str(operations), 'RW': True, 'Type': 'bind'}]}
            identity = image.packaged._container_identity(info, IID, 'owner')
            process = {'pid': 42, 'instance': int(image.contract.source_digest(identity), 16), 'running': True}
            commands = Mock(return_value=json.dumps([info]).encode())
            commands.cleanup_verified = True
            proof = SimpleNamespace(identifier='owner', image_id=IID, work=Path(directory), commands=commands,
                                    inspect=Mock(side_effect=AssertionError('unbounded inspect')))
            with patch('django.conf.settings', SimpleNamespace(DATA_RESTORE_LOCK_PATH=str(lock))):
                observe = restore.make_identity_guard(proof, process)
                initial = observe(timeout=.5)
                self.assertEqual(initial, (identity, process, (lock.stat().st_dev, lock.stat().st_ino)))
                call = commands.call_args
                self.assertEqual(call.args[0], ['docker', 'inspect', 'owner-server'])
                self.assertGreater(call.kwargs['timeout'], 0)
                self.assertLessEqual(call.kwargs['timeout'], .5)
                observe(timeout=.5)
                self.assertEqual(commands.call_args.args[0][-1], H)
                for mutation in ('uid', 'mount', 'config', 'pid', 'image', 'restart', 'inode'):
                    changed = copy.deepcopy(info)
                    if mutation == 'uid': changed['Config']['User'] = '0'
                    if mutation == 'mount': changed['Mounts'][0]['RW'] = False
                    if mutation == 'config': changed['Config']['Env'] = []
                    if mutation == 'pid': changed['State']['Pid'] += 1
                    if mutation == 'image': changed['Image'] = 'sha256:' + 'b'*64
                    if mutation == 'restart': changed['RestartCount'] = 1
                    if mutation == 'inode': lock.rename(operations / 'old'); lock.touch()
                    commands.return_value = json.dumps([changed]).encode()
                    with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                        observe(timeout=.5)
                    commands.return_value = json.dumps([info]).encode()
                proof.inspect.assert_not_called()


class CompositionControls(unittest.TestCase):
    def exercise(self, failure=None):
        from TESTS.adapters.go import message_mutation_restore as helper
        events, fixtures, paths = [], [], []
        identity = ({'id': H, 'image': IID}, {'pid': 42, 'instance': 123, 'running': True}, (1, 2))
        guard = lambda: identity[1].copy()
        address = image.ip_receipt('192.0.2.5', 'owned-container-tcp-peer', guard)
        initial = dict(state_sha256=H, cache_sha256=H, sequences_sha256=H)
        private = set()
        commands = Mock()
        commands.cleanup_verified = True
        proof = SimpleNamespace(commands=commands, image_id=IID)
        probe = Mock(return_value=True)

        @contextmanager
        def fixture_context(apps, work, raw, *, cleanup_receipt):
            self.assertEqual((apps, raw), ('apps', 'raw'))
            paths.append(work)
            fixture = dict(tokens={'owner': 'private-token'}, sessions={'owner': 'private-session'})
            fixtures.append(fixture)
            events.append('fixture-enter')
            try:
                yield fixture
            finally:
                events.append('fixture-reset')
                cleanup_receipt.update(cleanup(image.FIXTURE_CLEANUP))
                if failure == 'fixture-cleanup':
                    cleanup_receipt['verified'] = False

        def run(**kwargs):
            case = kwargs['case']
            events.append(case)
            self.assertIs(kwargs['fixture'], fixtures[-1])
            self.assertEqual(kwargs['base_url'], 'http://127.0.0.1:8002')
            self.assertEqual(kwargs['client_ip'], '192.0.2.5')
            self.assertEqual(kwargs['database'], 'default-database')
            self.assertEqual(kwargs['raw'], 'raw')
            self.assertIsInstance(kwargs['portable'], helper.PortableAssertions)
            self.assertEqual(kwargs['portable'].validate_runtime, image.contract.validate_runtime)
            self.assertEqual(kwargs['bounds'], helper.Bounds(io=5, probe=5))
            if failure == 'helper':
                raise RuntimeError('private helper failure including token')
            if failure == 'interrupt':
                raise KeyboardInterrupt()
            values = [True, False, False, True] if case.endswith('lease') else [True, True]
            if failure == 'probe':
                values = [False]
            for value in values:
                probe.return_value = value
                self.assertIs(kwargs['exclusive_probe'](timeout=1), value)
            if failure == 'commands': commands.cleanup_verified = False
            if failure == 'guard': identity[1]['instance'] += 1
            result = helper_result(case)
            if failure == 'result': result['exact_effects'] = False
            return result

        module = SimpleNamespace(make_exclusive_probe=Mock(return_value=probe))
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict('sys.modules', {'TESTS.adapters.go.message_restore_lock_probe': module}), \
             patch.object(restore, 'make_identity_guard', return_value=Mock(return_value=identity)), \
             patch.object(restore, 'quiesce', side_effect=lambda *a: events.append('quiesce')) as stop, \
             patch.object(helper, 'run', side_effect=run) as called, \
             patch.object(image.adapter, 'fixture_context', fixture_context), \
             patch.object(image, 'baseline', side_effect=lambda *a: initial.copy()), \
             patch.object(image, 'measure_address', return_value=('192.0.2.5', address)):
            arguments = dict(apps='apps', database='default-database', raw='raw',
                base_url='http://127.0.0.1:8002', process_guard=guard, proof=proof,
                work=Path(directory), private_values=private, binary=H)
            if failure:
                with self.assertRaises(BaseException):
                    restore.run_controls(**arguments)
            else:
                result = restore.run_controls(**arguments)
                self.assertTrue(restore.receipt_valid(result, dict(process_sha256=result['process_sha256'],
                    go_binary_sha256=H, baseline=initial, client_ip=address)))
                self.assertEqual(called.call_count, 4)
                self.assertEqual(len({id(f) for f in fixtures}), 4)
                self.assertEqual(len(set(paths)), 4)
                stop.assert_not_called()
                serialized = json.dumps(result)
                self.assertNotIn('private-token', serialized)
                self.assertNotIn('blocked_backend"', serialized)
            module.make_exclusive_probe.assert_called_once()
            self.assertEqual(module.make_exclusive_probe.call_args.kwargs['expected_image'], IID)
            self.assertEqual(module.make_exclusive_probe.call_args.kwargs['container'], H)
        self.assertEqual(private, {'private-token', 'private-session'})
        return events

    def test_four_fresh_fixtures_use_frozen_helper_and_canonical_oracle(self):
        events = self.exercise()
        self.assertEqual(events, [entry for case in restore.CASE_NAMES
            for entry in ('fixture-enter', case, 'fixture-reset')])

    def test_every_failure_quiesces_before_fixture_reset(self):
        for failure in ('helper', 'interrupt', 'probe', 'result', 'commands', 'guard'):
            with self.subTest(failure=failure):
                events = self.exercise(failure)
                self.assertEqual(events, ['fixture-enter', 'create-lease', 'quiesce', 'fixture-reset'])

    def test_failed_fixture_cleanup_cannot_pass(self):
        self.exercise('fixture-cleanup')


class ShutdownControls(unittest.TestCase):
    def stopped(self):
        return dict(Id=H, Image=IID, Config={'Labels': {image.packaged.LABEL: 'owner'}},
            State={'Running': False, 'Pid': 0})

    def test_stop_is_pinned_and_independently_verified_even_after_command_error(self):
        for failed_stop in (False, True):
            commands = Mock(side_effect=[RuntimeError('private stop failure') if failed_stop else b'',
                                         json.dumps([self.stopped()]).encode()])
            commands.cleanup_verified = False
            proof = SimpleNamespace(commands=commands, identifier='owner', image_id=IID)
            restore.quiesce(proof, ({'id': H}, {}, (1, 2)))
            self.assertEqual(commands.call_args_list[0].args[0], ['docker', 'stop', '--time', '5', H])
            self.assertEqual(commands.call_args_list[1].args[0], ['docker', 'inspect', H])
            self.assertIs(commands.cleanup_verified, False)
            self.assertEqual([call.kwargs['timeout'] for call in commands.call_args_list], [10, 5])

    def test_live_or_wrong_container_requires_kill_and_matching_stopped_proof(self):
        for field, value in [('Id', 'b'*64), ('Image', 'sha256:' + 'b'*64),
                             ('State', {'Running': True, 'Pid': 42}),
                             ('State', {'Running': False, 'Pid': 42}),
                             ('Config', {'Labels': {image.packaged.LABEL: 'someone-else'}})]:
            wrong = self.stopped()
            wrong[field] = value
            commands = Mock(side_effect=[b'', json.dumps([wrong]).encode(),
                                         b'', json.dumps([self.stopped()]).encode()])
            commands.cleanup_verified = True
            proof = SimpleNamespace(commands=commands, identifier='owner', image_id=IID)
            restore.quiesce(proof, ({'id': H}, {}, (1, 2)))
            self.assertEqual(commands.call_args_list[2].args[0], ['docker', 'kill', '--signal', 'KILL', H])
            self.assertEqual(commands.call_count, 4)

    def test_unprovable_shutdown_hard_exits_before_any_fixture_reset(self):
        program = '''
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
from TESTS.adapters.go import message_restore_image as restore
@contextmanager
def fixture():
    try:
        yield
    finally:
        Path(sys.argv[1]).write_text('UNSAFE RESET')
commands = Mock(side_effect=RuntimeError('PRIVATE TOKEN'))
commands.cleanup_verified = True
proof = SimpleNamespace(commands=commands, identifier='owner', image_id='sha256:'+'a'*64,
                        restore_failure_receipt=Path(sys.argv[2]))
with fixture():
    try:
        raise RuntimeError('pending HTTP')
    except BaseException:
        restore.quiesce(proof, ({'id':'a'*64}, {}, (1,2)))
'''
        with tempfile.TemporaryDirectory() as directory:
            reset = Path(directory) / 'reset'
            recovery = Path(directory) / 'results.json'
            recovery.write_text('{"success": false}')
            child = subprocess.run([sys.executable, '-B', '-c', program, str(reset), str(recovery)],
                                   capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 124)
            self.assertFalse(reset.exists())
            self.assertEqual(child.stdout, b'')
            self.assertEqual(child.stderr,
                b'mutation-image.restore-quiescence-unverified; fixture reset forbidden\n')
            public = json.loads(recovery.read_text())
            self.assertIs(public['success'], False)
            self.assertIs(public['cleanup_verified'], False)
            self.assertEqual(public['errors'], ['mutation-image.restore-quiescence-unverified'])
            self.assertEqual(public['recovery'], {'container_id': H})
            self.assertNotIn('PRIVATE TOKEN', recovery.read_text())

    def test_uncertain_shutdown_marks_cleanup_false(self):
        commands = Mock(side_effect=RuntimeError('private daemon error'))
        commands.cleanup_verified = True
        proof = SimpleNamespace(commands=commands, identifier='owner', image_id=IID)
        with patch.object(restore, '_abort_unquiesced', side_effect=SystemExit(124)) as abort:
            with self.assertRaises(SystemExit):
                restore.quiesce(proof, ({'id': H}, {}, (1, 2)))
        abort.assert_called_once()
        self.assertIs(commands.cleanup_verified, False)

    def test_broken_diagnostic_stream_cannot_unwind_into_fixture_reset(self):
        with patch.object(restore.os, 'write', side_effect=OSError('closed private FD')), \
             patch.object(restore.os, '_exit', side_effect=SystemExit(124)) as abort:
            with self.assertRaises(SystemExit):
                restore._abort_unquiesced()
        abort.assert_called_once_with(124)

    def test_failed_recovery_receipt_write_still_cannot_unwind(self):
        with patch.object(restore.Path, 'write_text', side_effect=OSError('private disk failure')), \
             patch.object(restore.os, 'write'), \
             patch.object(restore.os, '_exit', side_effect=SystemExit(124)) as abort:
            with self.assertRaises(SystemExit):
                restore._abort_unquiesced(receipt='results.json', container=H)
        abort.assert_called_once_with(124)


class AdditionalGateControls(unittest.TestCase):
    def test_runtime_and_public_parent_gate_both_require_complete_restore_results(self):
        from TESTS.test_message_mutations_image import runtime_receipt, parent_receipt
        with patch.object(image, 'source_hashes', return_value={'source': H}), \
             patch.object(image.packaged, 'go_source_hashes', return_value={'BE_GO/example': H}):
            self.assertTrue(image.acceptance(parent_receipt()))
            for malformed in (None, {}, True, 'mock', dict(receipt(), verified=False),
                              dict(receipt(), results=receipt()['results'][:3])):
                runtime = runtime_receipt()
                runtime['restore_controls'] = malformed
                self.assertFalse(image.runtime_acceptance(runtime))
                parent = parent_receipt()
                parent['runtime']['restore_controls'] = malformed
                self.assertFalse(image.acceptance(parent))

    def test_identity_rejects_invalid_budget_or_success_after_deadline(self):
        proof = SimpleNamespace(commands=Mock())
        observe = restore.make_identity_guard(proof, {'pid': 42, 'instance': 123, 'running': True})
        for timeout in (True, False, None, 0, -1, float('inf'), float('nan'), 21, '5'):
            with self.subTest(timeout=timeout), self.assertRaises(AssertionError):
                observe(timeout=timeout)
        proof.commands.assert_not_called()
        proof.identifier = 'owner'
        proof.commands.return_value = b'private malformed inspect output'
        with patch.object(restore.time, 'monotonic', side_effect=[0, .1, 1]), self.assertRaises(AssertionError):
            observe(timeout=.5)
        self.assertAlmostEqual(proof.commands.call_args.kwargs['timeout'], .4)


if __name__ == '__main__':
    unittest.main()
