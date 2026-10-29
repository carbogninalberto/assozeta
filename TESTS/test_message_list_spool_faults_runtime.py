"""Offline controls only: positive dictionaries are never runtime evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from TESTS.adapters.go import message_list_spool_faults_runtime as driver
from TESTS.adapters.go import message_list_spool_faults as faults
from TESTS.adapters.go import message_list_spool_image as phase
from TESTS.test_message_list_spool_image import build_control, evidence_control, IMAGE, REVISION, HASH
from TESTS.test_message_list_spool_faults import Boundary, Proof

RUN_FAULTS = faults.run_faults


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def fault_control():
    proof = Proof()
    boundary = Boundary(proof)
    with patch.object(faults, '_phase1', return_value=boundary), patch.object(
            faults.http.client, 'HTTPConnection', boundary.connection):
        return RUN_FAULTS(proof, 'http://127.0.0.1:12345', 'synthetic', 'synthetic')


def source_control():
    return {'source_sha256': HASH, 'files': dict.fromkeys(driver.SOURCE_FILES, HASH)}


def phase_control():
    value = evidence_control()
    recovery = fault_control()['cancellation']['recovery']
    value['lists'] = [dict(label=label, **recovery)
                      for label in ('single', 'concurrent-a', 'concurrent-b')]
    value['spool']['staged_bytes'] = [recovery['bytes']] * 3
    value['phase1_accepted'] = True
    value['build_receipt_sha256'] = driver.sha256(encoded(build_control()))
    return value


class AcceptanceControls(unittest.TestCase):
    def setUp(self):
        self.phase_bytes = encoded(phase_control())
        self.build_bytes = encoded(build_control())
        self.source = source_control()
        self.report = driver.report_template(IMAGE, REVISION)
        self.report.update(
            bounded_faults_accepted=True, phase1_accepted=True, failure=None,
            build_receipt_sha256=driver.sha256(self.build_bytes),
            phase1_receipt_sha256=driver.sha256(self.phase_bytes),
            source_before=deepcopy(self.source), source_after=deepcopy(self.source),
            source_stable=True, final_cleanup_verified=True,
            workload={'image': IMAGE, 'revision': REVISION,
                      'owner': phase_control()['owner'], 'source': deepcopy(self.source),
                      'build_receipt_sha256': driver.sha256(self.build_bytes)},
            faults=fault_control())
        self.report['faults_sha256'] = driver.fault_digest(self.report['faults'])

    def accept(self, report=None, **overrides):
        arguments = dict(phase1_bytes=self.phase_bytes, build_bytes=self.build_bytes,
                         manifest=build_control()['source_sha256'], current_source=self.source)
        arguments.update(overrides)
        return driver.bounded_acceptance(self.report if report is None else report, **arguments)

    def test_positive_control_preserves_overall_incomplete(self):
        self.assertTrue(self.accept())
        self.assertFalse(self.report['success'])
        self.assertFalse(self.report['complete_acceptance'])
        self.assertEqual(self.report['incomplete'], list(faults.REMAINING[:-1]))

    def test_every_missing_field_or_binding_is_red(self):
        for key in self.report:
            bad = deepcopy(self.report)
            del bad[key]
            self.assertFalse(self.accept(bad), key)
        for key in ('phase1_bytes', 'build_bytes', 'manifest', 'current_source'):
            self.assertFalse(self.accept(**{key: None}), key)
        self.assertFalse(driver.bounded_acceptance(self.report))
        self.assertFalse(self.accept({'bounded_faults_accepted': True}))

    def test_stale_edited_original_bytes_and_current_sources_are_red(self):
        for key, value in (('phase1_bytes', self.phase_bytes + b' '),
                           ('build_bytes', self.build_bytes + b' '),
                           ('phase1_bytes', b'{}'), ('phase1_bytes', b'{"x":1,"x":2}'),
                           ('manifest', {'BE_GO/Dockerfile': 'f' * 64})):
            self.assertFalse(self.accept(**{key: value}), key)
        for filename in driver.SOURCE_FILES:
            changed = deepcopy(self.source)
            changed['files'][filename] = 'f' * 64
            self.assertFalse(self.accept(current_source=changed), filename)
        changed = deepcopy(self.source)
        changed['source_sha256'] = 'f' * 64
        self.assertFalse(self.accept(current_source=changed))

    def test_failed_cleanup_never_accepts_even_with_updated_receipt_hash(self):
        for key in phase.CLEANUP_KEYS:
            bad = json.loads(self.phase_bytes)
            bad['cleanup'][key] = False
            bad['cleanup_status'][key] = 'failed'
            raw = encoded(bad)
            report = deepcopy(self.report)
            report['phase1_receipt_sha256'] = driver.sha256(raw)
            self.assertFalse(self.accept(report, phase1_bytes=raw), key)
        report = deepcopy(self.report)
        report['final_cleanup_verified'] = False
        self.assertFalse(self.accept(report))

    def test_nested_schema_and_cross_phase_hash_links_are_required(self):
        for mutate in (
                lambda r: r['faults']['runtime'].update(extra='private'),
                lambda r: r['faults']['storage'].update(restored=False),
                lambda r: r['faults']['cancellation'].update(cleanup_seconds=float('nan')),
                lambda r: r['faults'].update(format=True),
                lambda r: r['workload'].update(owner='az-category-image-' + 'b' * 32),
                lambda r: r.update(source_stable=False),
                lambda r: r.update(complete_acceptance=True),
                lambda r: r.update(bounded_faults_accepted=1)):
            report = deepcopy(self.report)
            mutate(report)
            self.assertFalse(self.accept(report))
        report = deepcopy(self.report)
        for key in ('cancellation', 'storage'):
            report['faults'][key]['recovery']['wire_sha256'] = 'f' * 64
        report['faults_sha256'] = driver.fault_digest(report['faults'])
        self.assertTrue(faults.validate_faults(report['faults']))
        self.assertFalse(self.accept(report))
        report = deepcopy(self.report)
        report['faults']['cancellation']['cleanup_seconds'] = 1.5
        self.assertFalse(self.accept(report))  # Valid observation, stale exact hash.

    def test_missing_fault_fields_and_edited_phase1_with_new_hash_are_red(self):
        for section in (None, 'runtime', 'cancellation', 'storage'):
            value = self.report['faults'] if section is None else self.report['faults'][section]
            for key in value:
                report = deepcopy(self.report)
                target = report['faults'] if section is None else report['faults'][section]
                del target[key]
                report['faults_sha256'] = driver.fault_digest(report['faults'])
                self.assertFalse(self.accept(report), (section, key))
        for field, value in (('phase1_accepted', False), ('runtime_executed', False),
                              ('owner', 'az-category-image-' + 'b' * 32)):
            original = json.loads(self.phase_bytes)
            original[field] = value
            raw = encoded(original)
            report = deepcopy(self.report)
            report['phase1_receipt_sha256'] = driver.sha256(raw)
            self.assertFalse(self.accept(report, phase1_bytes=raw), field)

    def test_old_prototype_shape_and_unbound_fault_receipt_are_red(self):
        prototype = {'scope': 'parent-combined-packaged-spool-bounded-faults',
            'complete_acceptance': False, 'bounded_faults_accepted': True,
            'phase1_accepted': True, 'phase1_receipt_sha256': driver.sha256(self.phase_bytes),
            'driver_sha256': HASH, 'driver_stable': True,
            'source_sha256': HASH, 'faults': fault_control()}
        self.assertFalse(self.accept(prototype))
        self.assertFalse(self.accept(fault_control()))

    def test_live_source_binding_includes_driver_docs_and_fault_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = set(driver.SOURCE_FILES) | {'BE_GO/' + name for name in (
                'Dockerfile', 'go.mod', 'go.sum', 'cmd/server/main.go', 'THIRD_PARTY_NOTICES.md')}
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'offline source')
            before = driver.source_binding(root)
            for name in driver.SOURCE_FILES:
                path = root / name
                path.write_bytes(b'changed offline source')
                self.assertNotEqual(before, driver.source_binding(root), name)
                path.write_bytes(b'offline source')
            path = root / driver.SOURCE_FILES[0]
            path.unlink()
            with self.assertRaises((OSError, RuntimeError)):
                driver.source_binding(root)
            path.symlink_to(root / 'BE_GO/go.mod')
            with self.assertRaises(RuntimeError):
                driver.source_binding(root)


class OrchestrationControls(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.build = self.root / 'build.json'
        self.build.write_bytes(encoded(build_control()))
        self.args = SimpleNamespace(image=IMAGE, expected_revision=REVISION,
            build_receipt=self.build, output=self.root / 'fresh', docker_socket='/synthetic/socket',
            postgres='postgres:16-alpine', redis='redis:7-alpine', deadline=123, cleanup_grace=45)
        self.events = []
        self.enterContext(patch.object(driver, 'source_binding', return_value=source_control()))
        self.enterContext(patch.object(phase, 'go_source_hashes', return_value=build_control()['source_sha256']))
        from TESTS.adapters.django import production_runtime
        self.production = self.enterContext(patch.object(production_runtime, 'require_production_python',
            side_effect=lambda: self.events.append('production')))
        self.original = self.enterContext(patch.object(phase, 'run_lists',
            side_effect=lambda *args: self.events.append('lists')))
        self.fault = self.enterContext(patch.object(faults, 'run_faults',
            side_effect=lambda *args: (self.events.append('faults'), fault_control())[1]))
        self.phase_run = self.enterContext(patch.object(phase, 'run', side_effect=self.fake_phase_run))

    def fake_phase_run(self, args):
        self.events.append('phase-run')
        self.assertEqual(args.docker_socket, self.args.docker_socket)
        self.assertEqual((args.deadline, args.cleanup_grace), (123, 45))
        args.output.mkdir(mode=0o700)
        report = phase_control()
        try:
            proof = SimpleNamespace(image_id=IMAGE, revision=REVISION, identifier=report['owner'])
            phase.run_lists(proof, 'http://127.0.0.1:12345', 'synthetic', 'synthetic', report)
        except BaseException:
            report['phase1_accepted'] = False
            report['failure'] = {'stage': 'list-workload', 'category': 'check-failed'}
            report['errors'] = ['spool.execution-failed']
        self.events.append('cleanup')
        phase.publish(args.output / 'results.json', report)
        return report

    def test_same_lifetime_original_then_faults_then_cleanup_and_restoration(self):
        result = driver.run(self.args)
        self.assertTrue(result['bounded_faults_accepted'])
        self.assertIs(phase.run_lists, self.original)
        self.assertEqual(self.events, ['production', 'phase-run', 'lists', 'faults', 'cleanup'])
        self.assertEqual(json.loads((self.args.output / 'faults.json').read_bytes()), result)
        self.assertEqual(os.stat(self.args.output / 'faults.json').st_mode & 0o777, 0o600)
        self.assertTrue((self.args.output / 'phase1/results.json').is_file())

    def test_fault_failure_and_interrupt_leave_red_receipt_and_restore_pointer(self):
        for index, error in enumerate((RuntimeError('private-canary /private/path'),
                                       KeyboardInterrupt('private-canary'))):
            self.args.output = self.root / f'failed-{index}'
            self.fault.side_effect = error
            result = driver.run(self.args)
            self.assertFalse(result['bounded_faults_accepted'])
            self.assertIsNone(result['faults'])
            self.assertIs(phase.run_lists, self.original)
            raw = (self.args.output / 'faults.json').read_bytes()
            self.assertNotIn(b'private-canary', raw)
            self.assertNotIn(b'/private/path', raw)
            self.assertIn('cleanup', self.events)

    def test_phase_run_raises_after_faults_no_partial_green(self):
        def broken(args):
            self.fake_phase_run(args)
            raise OSError('private-canary')
        self.phase_run.side_effect = broken
        result = driver.run(self.args)
        self.assertFalse(result['bounded_faults_accepted'])
        self.assertIs(phase.run_lists, self.original)
        self.assertNotIn('private-canary', json.dumps(result))

    def test_production_python_failure_precedes_phase_and_is_redacted(self):
        self.production.side_effect = RuntimeError('private-canary /private/python')
        result = driver.run(self.args)
        self.phase_run.assert_not_called()
        self.assertFalse(result['bounded_faults_accepted'])
        self.assertNotIn('private-canary', (self.args.output / 'faults.json').read_text())

    def test_fresh_output_protects_existing_files_and_symlinks(self):
        self.args.output.mkdir()
        sentinel = self.args.output / 'faults.json'
        sentinel.write_bytes(b'existing private sentinel')
        with self.assertRaises((RuntimeError, FileExistsError)):
            driver.run(self.args)
        self.assertEqual(sentinel.read_bytes(), b'existing private sentinel')
        self.phase_run.assert_not_called()
        link = self.root / 'link'
        link.symlink_to(self.args.output, target_is_directory=True)
        self.args.output = link
        with self.assertRaises((RuntimeError, FileExistsError)):
            driver.run(self.args)
        self.assertEqual(sentinel.read_bytes(), b'existing private sentinel')

    def test_changed_build_at_exit_rejects(self):
        def changed(args):
            value = self.fake_phase_run(args)
            self.build.write_bytes(self.build.read_bytes() + b' ')
            return value
        self.phase_run.side_effect = changed
        self.assertFalse(driver.run(self.args)['bounded_faults_accepted'])

    def test_failed_final_cleanup_and_missing_or_edited_phase_receipt_reject(self):
        for index, mode in enumerate(('cleanup', 'missing', 'edited')):
            self.args.output = self.root / f'phase-failure-{index}'

            def changed(args):
                value = self.fake_phase_run(args)
                path = args.output / 'results.json'
                if mode == 'cleanup':
                    value['cleanup']['volume'] = False
                    value['cleanup_status']['volume'] = 'failed'
                    phase.publish(path, value)
                elif mode == 'missing':
                    path.unlink()
                else:
                    altered = deepcopy(value)
                    altered['memory']['samples'][0] += 1
                    phase.publish(path, altered)
                return value

            self.phase_run.side_effect = changed
            self.assertFalse(driver.run(self.args)['bounded_faults_accepted'], mode)
            self.assertIs(phase.run_lists, self.original)

    def test_source_edit_during_faults_or_cleanup_rejects(self):
        for index, event in enumerate(('faults', 'cleanup')):
            self.args.output = self.root / f'changed-source-{index}'
            live = source_control()

            def current():
                value = deepcopy(live)
                if event in self.events:
                    value['files'][driver.SOURCE_FILES[-1]] = 'f' * 64
                return value

            self.events.clear()
            with patch.object(driver, 'source_binding', side_effect=current):
                result = driver.run(self.args)
            self.assertFalse(result['bounded_faults_accepted'], event)
            self.assertIs(phase.run_lists, self.original)

    def test_original_list_failure_never_calls_faults(self):
        self.original.side_effect = RuntimeError('private-canary')
        result = driver.run(self.args)
        self.assertFalse(result['bounded_faults_accepted'])
        self.fault.assert_not_called()
        self.assertIs(phase.run_lists, self.original)

    def test_dangling_output_symlink_is_not_adopted(self):
        target = self.root / 'not-created'
        self.args.output.symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            driver.run(self.args)
        self.assertFalse(target.exists())
        self.phase_run.assert_not_called()

    def test_invalid_deadlines_fail_before_services(self):
        for index, value in enumerate((0, -1, float('nan'), float('inf'), 3601)):
            self.args.output = self.root / f'deadline-{index}'
            self.args.deadline = value
            result = driver.run(self.args)
            self.assertFalse(result['bounded_faults_accepted'])
        self.phase_run.assert_not_called()


class StartupControls(unittest.TestCase):
    def test_imports_are_inert_and_stdlib_only(self):
        script = '''
import builtins, sys
before = set(sys.modules)
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'django', 'redis', 'socket', 'subprocess'}:
        raise RuntimeError('forbidden import')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from TESTS.adapters.go import message_list_spool_faults_runtime
added = set(sys.modules) - before
assert all(name.startswith('TESTS') or name.split('.')[0] in sys.stdlib_module_names for name in added)
assert 'TESTS.adapters.go.message_list_spool_image' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_optimized_python_red_receipt_no_services_and_redacted_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'fresh'
            command = [sys.executable, '-O', '-m',
                       'TESTS.adapters.go.message_list_spool_faults_runtime',
                       '--image', IMAGE, '--expected-revision', REVISION,
                       '--build-receipt', '/private/canary', '--output', str(output)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('/private/canary', result.stdout + result.stderr)
            self.assertNotIn('Traceback', result.stdout + result.stderr)
            self.assertFalse(json.loads((output / 'faults.json').read_bytes())['bounded_faults_accepted'])

    def test_cli_exit_contract_help_and_startup_redaction(self):
        with patch.object(driver, 'run', return_value={'bounded_faults_accepted': True}), redirect_stdout(io.StringIO()):
            self.assertEqual(driver.main(['--image', IMAGE, '--expected-revision', REVISION,
                '--build-receipt', 'synthetic', '--output', 'synthetic']), 2)
        for argv in (['--private-canary'], ['--deadline', 'private-canary']):
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(driver.main(argv), 1)
            self.assertNotIn('private-canary', stream.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(driver.main(['--help']), 0)


if __name__ == '__main__':
    unittest.main()
