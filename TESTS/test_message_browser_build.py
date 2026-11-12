"""Offline build-boundary controls. No compiler, browser, service or install runs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from TESTS.adapters.django import message_browser_build as builder
from TESTS.adapters.django import message_reads_browser as browser
from TESTS.adapters.django import message_reads_browser_runtime as runtime


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / 'repo'
        self.root.mkdir()
        originals = Path(__file__).resolve().parents[1]
        for name in browser.required_source_paths():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((originals / name).read_bytes())
        for name, content in {
            'UI/public/offline.html': 'offline?v=0',
            'UI/package-lock.json': '{"lockfileVersion":3}',
            'UI/scripts/extra.cjs': 'copied root and nested cjs',
            'BE_GO/go.mod': 'module fixture', 'BE_GO/go.sum': '',
            'BE_GO/cmd/server/main.go': 'package main',
            'TESTS/adapters/go/server.py': 'server builder fixture',
            'TESTS/adapters/django/login_jpeg.py': 'codec builder fixture',
            'UI/node_modules/vite/bin/vite.js': 'installed vite',
            'UI/node_modules/postcss-import/index.js': 'installed postcss',
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        bins = self.root / 'UI/node_modules/.bin'
        bins.mkdir()
        (bins / 'vite').symlink_to('../vite/bin/vite.js')
        self.node = self.directory / 'node'
        self.node.write_bytes(b'installed node executable')
        self.node.chmod(0o700)
        self.cache = self.directory / 'module-cache'
        self.cache.mkdir()
        self.module_dir = self.cache / 'example.test/dependency@v1.2.3'
        self.module_dir.mkdir(parents=True)
        (self.module_dir / 'dependency.go').write_text('module source')
        (self.module_dir / 'go.mod').write_text('module example.test/dependency')
        self.graph = [
            {'Path': 'fixture', 'Main': True, 'Dir': str(self.root / 'BE_GO'),
             'GoMod': str(self.root / 'BE_GO/go.mod')},
            {'Path': 'example.test/dependency', 'Version': 'v1.2.3',
             'Dir': str(self.module_dir), 'GoMod': str(self.module_dir / 'go.mod')},
        ]
        self.environment = {'PATH': '/usr/bin:/bin', 'HOME': str(self.directory),
                            'GOMODCACHE': str(self.cache), 'GOCACHE': str(self.directory / 'go-cache')}
        self.output = self.directory / 'build'
        self.ui_calls = []
        self.go_calls = []
        for name in ('subprocess.Popen', 'subprocess.run', 'subprocess.check_output',
                     'socket.socket', 'socket.create_connection'):
            guard = patch(name, side_effect=AssertionError('real process/socket forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        graph_guard = patch('subprocess.run', side_effect=self.fake_graph_command)
        graph_guard.start()
        self.addCleanup(graph_guard.stop)

    def fake_graph_command(self, command, **kwargs):
        self.assertEqual(command, builder.GO_GRAPH_COMMAND, 'any other real process is forbidden')
        self.assertEqual(kwargs['cwd'], self.root / 'BE_GO')
        for key, value in builder.GO_ENV.items(): self.assertEqual(kwargs['env'][key], value)
        return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(row) for row in self.graph))

    def fake_ui(self, invocation, *, cwd, env, log, timeout):
        self.ui_calls.append((invocation, cwd, env))
        self.assertEqual((cwd / 'postcss.config.cjs').read_bytes(),
                         (self.root / 'UI/postcss.config.cjs').read_bytes())
        self.assertTrue((cwd / 'node_modules').is_symlink())
        self.assertEqual((cwd / 'node_modules').resolve(), self.root / 'UI/node_modules')
        artifact = Path(invocation[invocation.index('--outDir') + 1])
        artifact.mkdir()
        (artifact / 'index.html').write_text('built index')
        (artifact / 'app.js').write_text('compiled;\n//# sourceMappingURL=app.js.map\n')
        (artifact / 'app.js.map').write_text(json.dumps({
            'version': 3, 'file': 'app.js',
            'sources': list(browser.WORKFLOW_SOURCES),
            'sourcesContent': [(cwd / name).read_text() for name in browser.WORKFLOW_SOURCES],
        }))
        log.write('private diagnostic must be removed\n')
        return 0

    def fake_go(self, output, log, environment):
        self.go_calls.append(environment)
        for path in (output, output.with_name('image-codec')):
            path.write_bytes(b'completed executable: ' + path.name.encode())
            path.chmod(0o700)

    def build(self, ui=None, native=None):
        with patch.object(builder, 'run_ui', side_effect=ui or self.fake_ui), \
             patch.object(builder, 'run_native', side_effect=lambda output, log, environment, **options:
                          (native or self.fake_go)(output, log, environment)):
            return builder.build(self.output, root=self.root, node=self.node,
                                 environment=self.environment)

    def assert_failed(self, ui=None, native=None):
        self.assertEqual(self.build(ui, native), 1)
        self.assertFalse((self.output / 'build-observation.json').exists())
        self.assertFalse(list(self.output.glob('.work-*')))
        for name in ('artifact', 'server', 'image-codec'):
            self.assertFalse((self.output / name).exists(), name)
        record = json.loads((self.output / 'build-record.json').read_text())
        self.assertIs(record['success'], False)
        self.assertNotIn('private diagnostic', json.dumps(record))
        return record

    def test_completed_build_records_validate_with_real_runtime_and_provenance(self):
        self.assertEqual(self.build(), 0)
        provenance = browser.capture_provenance(self.output / 'artifact', root=self.root)
        observed = runtime.validate_build_record(self.output / 'build-observation.json', provenance)
        native = runtime.validate_go_build_record(self.output / 'go-build-record.json',
            self.output / 'server', self.output / 'image-codec', root=self.root)
        raw = json.loads((self.output / 'build-record.json').read_text())
        self.assertEqual(raw['artifact_sha256'], observed['artifact_sha256'])
        self.assertEqual(raw['native_source_sha256_before'], native['source_sha256'])
        self.assertEqual(raw['node_sha256_before'], runtime.sha(self.node))
        self.assertEqual(raw['source_sha256_before'], raw['source_sha256_after'])
        self.assertEqual(raw['copied_input_sha256_before'], raw['copied_input_sha256_after'])
        self.assertEqual(raw['dependency_sha256_before'], raw['dependency_sha256_after'])
        self.assertEqual(raw['go_module_graph_before'], raw['go_module_graph_after'])
        self.assertEqual(set(raw['dependency_sha256_before']['go_modules']), {'example.test/dependency'})
        self.assertEqual(raw['exit_code'], 0)
        self.assertFalse(list(self.output.glob('.work-*')))
        self.assertFalse(list(self.output.glob('*.log')))

    def test_build_failure_even_with_complete_artifact_cannot_issue_success(self):
        def fail(*args, **kwargs):
            self.fake_ui(*args, **kwargs)
            return 7
        record = self.assert_failed(ui=fail)
        self.assertEqual(record['exit_code'], 7)
        self.assertEqual(self.go_calls, [])
        self.assertEqual(record['source_sha256_before'], record['source_sha256_after'])

    def test_zero_exit_without_complete_sourcemapped_output_is_rejected(self):
        for mode in ('missing', 'empty', 'empty-entry', 'detached-map', 'wrong-source'):
            self.output = self.directory / mode
            def partial(*args, **kwargs):
                if mode == 'missing': return 0
                result = self.fake_ui(*args, **kwargs)
                if mode == 'empty': (self.output / 'artifact/app.js.map').write_text('{}')
                if mode == 'empty-entry': (self.output / 'artifact/index.html').write_text('')
                if mode == 'detached-map': (self.output / 'artifact/app.js').write_text('no map link')
                if mode == 'wrong-source':
                    path = self.output / 'artifact/app.js.map'
                    data = json.loads(path.read_text())
                    data['sourcesContent'][0] = 'changed UI'
                    path.write_text(json.dumps(data))
                return result
            with self.subTest(mode=mode):
                self.assert_failed(ui=partial)

    def test_timeout_and_launch_error_have_no_receipt_or_private_exception(self):
        for error in (subprocess.TimeoutExpired('private command', 1), OSError('PRIVATE_TOKEN')):
            with self.subTest(error=type(error).__name__):
                self.output = self.directory / type(error).__name__
                def fail(*args, **kwargs):
                    raise error
                record = self.assert_failed(ui=fail)
                self.assertIsNone(record['exit_code'])
                self.assertNotIn('PRIVATE_TOKEN', json.dumps(record))

    def test_fresh_output_existing_directory_file_and_dangling_link_are_preserved(self):
        for kind in ('directory', 'file', 'link'):
            self.output = self.directory / kind
            if kind == 'directory':
                self.output.mkdir()
                (self.output / 'other-work').write_text('keep')
            elif kind == 'file':
                self.output.write_text('keep')
            else:
                self.output.symlink_to(self.directory / 'absent')
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                self.build()
        self.assertEqual((self.directory / 'directory/other-work').read_text(), 'keep')
        self.assertEqual((self.directory / 'file').read_text(), 'keep')
        self.assertTrue((self.directory / 'link').is_symlink())
        self.assertEqual(self.ui_calls, [])

    def test_symlink_parent_and_output_within_build_inputs_are_rejected(self):
        other = self.directory / 'other'
        other.mkdir()
        link = self.directory / 'linked-parent'
        link.symlink_to(other, target_is_directory=True)
        for path in (link / 'build', self.root / 'UI/new-output', self.root / 'BE_GO/new-output'):
            self.output = path
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.build()
            self.assertFalse(path.exists())
        self.assertEqual(list(other.iterdir()), [])

    def test_replaced_output_cannot_redirect_cleanup_to_unowned_directory(self):
        other = self.directory / 'unowned'
        other.mkdir()
        (other / 'keep').write_text('keep')
        moved = self.directory / 'moved-build'
        def replace(*args, **kwargs):
            code = self.fake_ui(*args, **kwargs)
            self.output.rename(moved)
            self.output.symlink_to(other, target_is_directory=True)
            return code
        with self.assertRaises(RuntimeError):
            self.build(ui=replace)
        self.assertEqual([path.name for path in other.iterdir()], ['keep'])
        self.assertEqual((other / 'keep').read_text(), 'keep')
        self.assertFalse((moved / 'build-observation.json').exists())

    def test_original_and_copied_input_mutations_additions_and_deletions_fail(self):
        for target in ('original', 'copy'):
            for mutation in ('change', 'addition', 'deletion'):
                with self.subTest(target=target, mutation=mutation):
                    self.output = self.directory / f'{target}-{mutation}'
                    original = self.root / 'UI/scripts/extra.cjs'
                    original.write_text('copied root and nested cjs')
                    def mutate(*args, **kwargs):
                        code = self.fake_ui(*args, **kwargs)
                        ui = self.root / 'UI' if target == 'original' else kwargs['cwd']
                        path = ui / 'scripts/extra.cjs'
                        if mutation == 'change': path.write_text('changed')
                        elif mutation == 'deletion': path.unlink()
                        else: (ui / 'scripts/new.cjs').write_text('added')
                        return code
                    self.assert_failed(ui=mutate)
                    (self.root / 'UI/scripts/new.cjs').unlink(missing_ok=True)

    def test_missing_postcss_is_rejected_before_execution(self):
        (self.root / 'UI/postcss.config.cjs').unlink()
        self.assert_failed()
        self.assertEqual(self.ui_calls, [])

    def test_inventory_walk_error_cannot_silently_drop_dependency_subtree(self):
        def unreadable(*args, **kwargs):
            kwargs['onerror'](PermissionError('private unreadable path'))
            return iter(())
        with patch.object(builder.os, 'walk', side_effect=unreadable):
            self.assert_failed()
        self.assertEqual(self.ui_calls, [])

    def test_relative_cache_paths_cannot_redirect_compiler_writes_into_checkout(self):
        for key in ('GOCACHE', 'GOMODCACHE'):
            self.output = self.directory / key
            original = self.environment[key]
            self.environment[key] = 'relative-cache'
            with self.subTest(key=key):
                self.assert_failed()
            self.environment[key] = original
        self.assertEqual(self.ui_calls, [])

    def test_dependency_node_and_native_input_drift_fail(self):
        for path in (self.node, self.module_dir / 'dependency.go',
                     self.root / 'UI/node_modules/postcss-import/index.js',
                     self.root / 'BE_GO/cmd/server/main.go'):
            with self.subTest(path=path.name):
                self.output = self.directory / ('drift-' + str(len(self.ui_calls)))
                def drift(*args, **kwargs):
                    code = self.fake_ui(*args, **kwargs)
                    path.write_text(path.read_text() + 'changed')
                    return code
                self.assert_failed(ui=drift)

    def test_dependency_addition_and_deletion_fail(self):
        dependency = self.root / 'UI/node_modules/new.js'
        for mode in ('addition', 'deletion'):
            self.output = self.directory / mode
            if mode == 'deletion': dependency.write_text('existing')
            def mutate(*args, **kwargs):
                result = self.fake_ui(*args, **kwargs)
                if mode == 'addition': dependency.write_text('new')
                else: dependency.unlink()
                return result
            self.assert_failed(ui=mutate)

    def test_unrelated_shared_module_cache_changes_do_not_invalidate_build(self):
        unrelated = self.cache / 'unrelated-project'
        unrelated.mkdir()
        (unrelated / 'unreadable').symlink_to('/outside/unrelated-cache-entry')
        def mutate(*args, **kwargs):
            result = self.fake_ui(*args, **kwargs)
            (unrelated / 'new-module.go').write_text('other project changed concurrently')
            return result
        self.assertEqual(self.build(ui=mutate), 0)

    def test_graph_version_change_cannot_reuse_identical_dependency_bytes(self):
        def mutate(*args, **kwargs):
            result = self.fake_ui(*args, **kwargs)
            self.graph[1]['Version'] = 'v9.9.9'
            return result
        self.assert_failed(ui=mutate)

    def test_graph_errors_missing_modules_and_escaping_replacements_fail_before_build(self):
        initial = json.dumps(self.graph)
        outside = self.directory / 'outside-module'
        outside.mkdir()
        (outside / 'go.mod').write_text('module outside')
        for mode in ('error', 'missing', 'duplicate', 'external-replace', 'symlink-replace'):
            self.graph = json.loads(initial)
            self.output = self.directory / mode
            if mode == 'error': self.graph[1]['Error'] = {'Err': 'private graph failure'}
            if mode == 'missing': self.graph[1].pop('Dir')
            if mode == 'duplicate': self.graph.append(self.graph[1])
            if mode == 'external-replace':
                self.graph[1]['Replace'] = {'Path': '../outside-module', 'Dir': str(outside),
                                           'GoMod': str(outside / 'go.mod')}
            if mode == 'symlink-replace':
                link = self.root / 'BE_GO/linked-module'
                link.symlink_to(outside, target_is_directory=True)
                self.graph[1]['Replace'] = {'Path': './linked-module', 'Dir': str(link),
                                           'GoMod': str(link / 'go.mod')}
            with self.subTest(mode=mode):
                self.assert_failed()
            (self.root / 'BE_GO/linked-module').unlink(missing_ok=True)
        self.assertEqual(self.ui_calls, [])

    def test_local_replacement_is_hashed_and_graph_command_never_downloads(self):
        local = self.root / 'BE_GO/third_party/local'
        local.mkdir(parents=True)
        (local / 'go.mod').write_text('module example.test/dependency')
        (local / 'dependency.go').write_text('local dependency')
        self.graph[1]['Replace'] = {'Path': './third_party/local', 'Dir': str(local),
                                   'GoMod': str(local / 'go.mod')}
        self.assertEqual(self.build(), 0)
        raw = json.loads((self.output / 'build-record.json').read_text())
        self.assertEqual(raw['go_module_graph_before']['example.test/dependency']['effective_path'],
                         './third_party/local')
        self.assertEqual(raw['dependency_sha256_before']['go_modules']['example.test/dependency']
                         ['dependency.go'], builder.sha(local / 'dependency.go'))

    def test_malformed_duplicate_json_and_failed_go_graph_have_no_observation(self):
        for mode in ('malformed', 'duplicate-json', 'failed'):
            self.output = self.directory / mode
            def bad_graph(*args, **kwargs):
                if mode == 'failed': raise subprocess.CalledProcessError(1, args[0], stderr='PRIVATE')
                return SimpleNamespace(returncode=0, stdout='{' if mode == 'malformed' else
                    '{"Path":"fixture","Path":"duplicate"}')
            with self.subTest(mode=mode), patch('subprocess.run', side_effect=bad_graph):
                self.assert_failed()
        self.assertEqual(self.ui_calls, [])

    def test_source_dependency_artifact_and_output_symlinks_cannot_escape(self):
        outside = self.directory / 'unowned'
        outside.mkdir()
        sentinel = outside / 'keep'
        sentinel.write_text('keep')
        for location in ('source', 'dependency', 'artifact'):
            self.output = self.directory / location
            link = self.root / ('UI/src/escape' if location == 'source' else 'UI/node_modules/escape')
            def escape(*args, **kwargs):
                code = self.fake_ui(*args, **kwargs)
                (self.output / 'artifact/escape').symlink_to(outside, target_is_directory=True)
                return code
            if location != 'artifact': link.symlink_to(outside, target_is_directory=True)
            self.assert_failed(ui=escape if location == 'artifact' else None)
            if link.is_symlink(): link.unlink()
            self.assertEqual(sentinel.read_text(), 'keep')

    def test_native_failure_or_missing_codec_discards_all_partial_outputs(self):
        for mode in ('failure', 'missing', 'empty'):
            self.output = self.directory / mode
            def fail(output, log, environment):
                self.fake_go(output, log, environment)
                if mode == 'failure': raise subprocess.CalledProcessError(1, ['cc', 'private'])
                if mode == 'missing': output.with_name('image-codec').unlink()
                if mode == 'empty': output.write_bytes(b'')
            self.assert_failed(native=fail)

    def test_native_source_addition_deletion_and_artifact_link_fail(self):
        original = self.root / 'BE_GO/cmd/server/main.go'
        outside = self.directory / 'external-executable'
        outside.write_bytes(b'keep')
        outside.chmod(0o700)
        for mode in ('addition', 'deletion', 'symlink'):
            self.output = self.directory / mode
            original.write_text('package main')
            def mutate(output, log, environment):
                self.fake_go(output, log, environment)
                if mode == 'addition': (self.root / 'BE_GO/added.go').write_text('package added')
                elif mode == 'deletion': original.unlink()
                else:
                    output.unlink()
                    output.symlink_to(outside)
            with self.subTest(mode=mode):
                self.assert_failed(native=mutate)
            (self.root / 'BE_GO/added.go').unlink(missing_ok=True)
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_shared_native_builder_receives_owned_environment_at_each_actual_boundary(self):
        from TESTS.adapters.django import login_jpeg
        calls = []
        child = Mock(pid=987654)
        child.wait.return_value = 0

        def launch_helper(command, **kwargs):
            self.assertEqual(command[:3], [builder.sys.executable, '-B', '-c'])
            self.assertEqual(command[4:], [str(self.root), str(self.output / 'server')])
            self.assertEqual(kwargs['cwd'], self.root)
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
            # Execute the actual child entry point, replacing only process
            # boundaries. The real shared helper must invoke pkg-config/cc/go.
            with patch.object(builder.sys, 'argv', ['-c', *command[4:]]), \
                 patch.object(builder.sys, 'path', list(builder.sys.path)), \
                 patch.object(builder.sys, 'stdout', kwargs['stdout']), \
                 patch.dict(os.environ, kwargs['env'], clear=True):
                exec(compile(command[3], '<native-child>', 'exec'), {'__name__': '__main__'})
            return child

        def flags(command, **kwargs):
            self.assertEqual(command[0], 'pkg-config')
            calls.append((command, kwargs['env']))
            return '-ljpeg -lwebpdemux -lwebp'
        def compile_command(command, **kwargs):
            if command == builder.GO_GRAPH_COMMAND:
                return self.fake_graph_command(command, **kwargs)
            calls.append((command, kwargs['env']))
            self.assertIs(kwargs['stdout'], launch.call_args.kwargs['stdout'])
            self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
            destination = Path(command[command.index('-o') + 1])
            destination.write_bytes(b'completed ' + command[0].encode())
            destination.chmod(0o700)
            return SimpleNamespace(returncode=0)
        with patch.object(builder, 'run_ui', side_effect=self.fake_ui), \
             patch.object(builder.go_server, 'ROOT', self.root), \
             patch.object(login_jpeg, 'NATIVE', self.root / 'BE_GO/codec/jpeg_encode.c'), \
             patch('subprocess.check_output', side_effect=flags), \
             patch('subprocess.run', side_effect=compile_command), \
             patch('subprocess.Popen', side_effect=launch_helper) as launch, \
             patch('os.killpg', side_effect=[None, ProcessLookupError()]) as kill:
            self.assertEqual(builder.build(self.output, root=self.root, node=self.node,
                                           environment=self.environment), 0)
        self.assertEqual(kill.call_args_list, [unittest.mock.call(child.pid, builder.signal.SIGKILL),
                                              unittest.mock.call(child.pid, 0)])
        self.assertEqual(child.wait.call_args_list, [unittest.mock.call(timeout=240),
                                                   unittest.mock.call(timeout=10)])
        self.assertEqual([call[0][0] for call in calls], ['pkg-config', 'cc', 'go'])
        for command, environment in calls:
            self.assertEqual(environment, launch.call_args.kwargs['env'])
            for key, value in builder.GO_ENV.items(): self.assertEqual(environment[key], value)
        self.assertEqual(calls[-1][0][-1], './cmd/server')

    def test_native_timeout_interruption_and_inner_failure_kill_and_reap_before_cleanup(self):
        for error in (subprocess.TimeoutExpired('native', 240), KeyboardInterrupt(),
                      SystemExit(1), None):
            self.output = self.directory / ('native-failure-' + str(len(self.ui_calls)))
            events = []
            child = Mock(pid=987654)

            def wait(*, timeout):
                events.append(('wait', timeout))
                if timeout == 240 and error is not None:
                    raise error
                return 1 if timeout == 240 else -9

            def launch(*args, **kwargs):
                self.assertTrue(kwargs['start_new_session'])
                self.fake_go(self.output / 'server', kwargs['stdout'], kwargs['env'])
                return child

            remove = builder.remove_owned

            def cleanup(path):
                self.assertEqual(events, [('wait', 240), ('kill', child.pid), ('wait', 10),
                                          ('group-check', child.pid)])
                return remove(path)

            def kill_group(pid, sig):
                events.append(('group-check' if sig == 0 else 'kill', pid))
                if sig == 0:
                    raise ProcessLookupError

            child.wait.side_effect = wait
            with self.subTest(error=type(error).__name__), \
                 patch.object(builder, 'run_ui', side_effect=self.fake_ui), \
                 patch('subprocess.Popen', side_effect=launch), \
                 patch('os.killpg', side_effect=kill_group) as kill, \
                 patch.object(builder, 'remove_owned', side_effect=cleanup):
                self.assertEqual(builder.build(self.output, root=self.root, node=self.node,
                                               environment=self.environment), 1)
            self.assertEqual(kill.call_args_list, [unittest.mock.call(child.pid, builder.signal.SIGKILL),
                                                  unittest.mock.call(child.pid, 0)])
            record = json.loads((self.output / 'build-record.json').read_text())
            self.assertFalse(record['native_build_completed'])
            self.assertEqual(record['stage'], 'native-command')
            self.assertFalse(record['success'])
            self.assertFalse(list(self.output.glob('.work-*')))
            for name in ('artifact', 'server', 'image-codec', 'build-observation.json',
                         'go-build-record.json'):
                self.assertFalse((self.output / name).exists(), name)

    def test_native_cleanup_failure_retains_private_outputs_without_success(self):
        for failure in ('kill', 'reap', 'group'):
            self.output = self.directory / ('cleanup-' + failure)
            child = Mock(pid=987654)
            child.wait.side_effect = [subprocess.TimeoutExpired('native', 240),
                                     subprocess.TimeoutExpired('native', 10) if failure == 'reap' else -9]

            def launch(*args, **kwargs):
                self.fake_go(self.output / 'server', kwargs['stdout'], kwargs['env'])
                return child

            with self.subTest(failure=failure), \
                 patch.object(builder, 'run_ui', side_effect=self.fake_ui), \
                 patch('subprocess.Popen', side_effect=launch), \
                 patch('os.killpg', side_effect=PermissionError('private') if failure == 'kill' else None), \
                 patch.object(builder.time, 'monotonic', side_effect=[0, 11]), \
                 patch.object(builder.signal, 'pthread_sigmask', return_value=set()) as mask, \
                 patch.object(builder, 'remove_owned') as remove:
                self.assertEqual(builder.build(self.output, root=self.root, node=self.node,
                                               environment=self.environment), 1)
            remove.assert_not_called()
            self.assertEqual(child.wait.call_args_list, [unittest.mock.call(timeout=240),
                                                       unittest.mock.call(timeout=10)])
            self.assertEqual(mask.call_args_list, [unittest.mock.call(builder.signal.SIG_BLOCK,
                                                    {builder.signal.SIGINT, builder.signal.SIGTERM}),
                                                  unittest.mock.call(builder.signal.SIG_SETMASK, set())])
            record = json.loads((self.output / 'build-record.json').read_text())
            self.assertFalse(record['success'])
            self.assertFalse(record['private_cleanup_verified'])
            self.assertEqual(record['error'], 'build.cleanup-failed')
            self.assertTrue((self.output / 'server').exists())
            self.assertTrue(list(self.output.glob('.work-*')))
            self.assertFalse((self.output / 'build-observation.json').exists())

    def test_native_exited_group_and_launch_failure_do_not_signal_unowned_processes(self):
        child = Mock(pid=987654)
        child.wait.return_value = 0
        with (self.directory / 'native.log').open('w') as log:
            options = {'root': self.root}
            with patch('subprocess.Popen', return_value=child), \
                 patch('os.killpg', side_effect=ProcessLookupError) as kill:
                builder.run_native(self.output, log, self.environment, **options)
            self.assertEqual(kill.call_args_list, [unittest.mock.call(child.pid, builder.signal.SIGKILL),
                                                  unittest.mock.call(child.pid, 0)])
            child.wait.assert_called_with(timeout=10)
            with patch('subprocess.Popen', side_effect=OSError('private')), \
                 patch('os.killpg') as kill, self.assertRaises(OSError):
                builder.run_native(self.output, log, self.environment, **options)
            kill.assert_not_called()

    def test_native_launch_error_cleans_partial_ui_without_success_or_group_signal(self):
        with patch.object(builder, 'run_ui', side_effect=self.fake_ui), \
             patch('subprocess.Popen', side_effect=OSError('PRIVATE_TOKEN')), \
             patch('os.killpg') as kill:
            self.assertEqual(builder.build(self.output, root=self.root, node=self.node,
                                           environment=self.environment), 1)
        kill.assert_not_called()
        record = json.loads((self.output / 'build-record.json').read_text())
        self.assertFalse(record['success'])
        self.assertFalse(record['native_build_completed'])
        self.assertTrue(record['private_cleanup_verified'])
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(record))
        for name in ('artifact', 'server', 'image-codec', 'build-observation.json'):
            self.assertFalse((self.output / name).exists(), name)
        self.assertFalse(list(self.output.glob('.work-*')))

    def test_native_waits_for_descendant_group_with_interruptions_masked(self):
        child = Mock(pid=987654)
        child.wait.return_value = 0
        previous = {builder.signal.SIGUSR1}
        with (self.directory / 'native.log').open('w') as log, \
             patch('subprocess.Popen', return_value=child), \
             patch('os.killpg', side_effect=[None, None, None, ProcessLookupError()]) as kill, \
             patch.object(builder.time, 'monotonic', return_value=0), \
             patch.object(builder.time, 'sleep') as sleep, \
             patch.object(builder.signal, 'pthread_sigmask', return_value=previous) as mask:
            builder.run_native(self.output, log, self.environment, root=self.root)
        self.assertEqual(kill.call_args_list, [unittest.mock.call(child.pid, builder.signal.SIGKILL),
                                              *[unittest.mock.call(child.pid, 0)] * 3])
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(0.01)] * 2)
        self.assertEqual(mask.call_args_list, [unittest.mock.call(builder.signal.SIG_BLOCK,
                                                {builder.signal.SIGINT, builder.signal.SIGTERM}),
                                              unittest.mock.call(builder.signal.SIG_SETMASK, previous)])

    def test_ui_process_boundary_returns_actual_code_and_kills_group_on_timeout(self):
        child = Mock(pid=987654)
        child.wait.return_value = 9
        options = {'cwd': self.root, 'env': {'PATH': '/bin'}, 'log': Mock(), 'timeout': 12}
        with patch('subprocess.Popen', return_value=child) as launch:
            self.assertEqual(builder.run_ui(['node', 'vite', 'build'], **options), 9)
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertEqual(launch.call_args.kwargs['env'], options['env'])
        child.wait.side_effect = [subprocess.TimeoutExpired('node', 12), -9]
        with patch('subprocess.Popen', return_value=child), patch('os.killpg') as kill, \
             self.assertRaises(subprocess.TimeoutExpired):
            builder.run_ui(['node', 'vite', 'build'], **options)
        kill.assert_called_once_with(child.pid, builder.signal.SIGKILL)
        child.wait.assert_called_with(timeout=10)

    def test_cleanup_failure_cannot_produce_success_and_preserves_unrelated_work(self):
        sentinel = self.directory / 'other-work'
        sentinel.write_text('keep')
        remove = builder.remove_owned
        def fail_private_cleanup(path):
            if path.name.startswith('.work-'):
                raise OSError('private cleanup diagnostic')
            remove(path)
        with patch.object(builder, 'remove_owned', side_effect=fail_private_cleanup):
            self.assertEqual(self.build(), 1)
        record = json.loads((self.output / 'build-record.json').read_text())
        self.assertIs(record['private_cleanup_verified'], False)
        self.assertIs(record['success'], False)
        self.assertFalse((self.output / 'build-observation.json').exists())
        self.assertFalse((self.output / 'artifact').exists())
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_later_native_work_cannot_change_ui_artifact(self):
        def drift(*args):
            self.fake_go(*args)
            (self.output / 'artifact/app.js').write_text('drifted after UI build')
        self.assert_failed(native=drift)

    def test_go_overrides_private_env_and_node_hooks_never_reach_builds(self):
        private = {'GOFLAGS': '-overlay=/private -toolexec=evil', 'GOWORK': '/private',
                   'GOENV': '/private', 'GOTOOLCHAIN': 'auto', 'GOPROXY': 'https://private',
                   'GOSUMDB': 'private', 'CC': 'evil', 'CXX': 'evil', 'CGO_CFLAGS': '-include private',
                   'CGO_ENABLED': '0', 'GOPATH': '/private', 'GOROOT': '/private',
                   'PKG_CONFIG': 'evil', 'CPATH': '/private', 'NODE_OPTIONS': '--require=private',
                   'VITE_PRIVATE': 'secret', 'AWS_SECRET_ACCESS_KEY': 'secret',
                   'DATABASE_URL': 'secret', 'LD_PRELOAD': '/private', 'DYLD_INSERT_LIBRARIES': '/private'}
        self.environment.update(private)
        before = dict(os.environ)
        self.assertEqual(self.build(), 0)
        self.assertEqual(dict(os.environ), before)
        go = self.go_calls[0]
        for key, value in {'GOFLAGS': '-p=1', 'GOWORK': 'off', 'GOENV': 'off',
                           'GOTOOLCHAIN': 'local', 'GOPROXY': 'off', 'GOSUMDB': 'off'}.items():
            self.assertEqual(go[key], value)
        ui = self.ui_calls[0][2]
        for key in private:
            if key not in {'GOFLAGS', 'GOWORK', 'GOENV', 'GOTOOLCHAIN', 'GOPROXY', 'GOSUMDB', 'NODE_OPTIONS'}:
                self.assertNotIn(key, go)
                self.assertNotIn(key, ui)
        self.assertEqual(ui['NODE_OPTIONS'], '--max-old-space-size=4096')
        self.assertEqual(ui['DEPLOY_ENV'], 'production')
        self.assertEqual(ui['OEM_ENV'], 'assozeta')
        self.assertEqual(ui['VERSION'], 'unstable')
        raw = (self.output / 'build-record.json').read_text()
        self.assertNotIn('AWS_SECRET', raw)
        self.assertNotIn('DATABASE_URL', raw)


if __name__ == '__main__':
    unittest.main()
