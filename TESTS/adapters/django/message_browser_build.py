"""Offline, fresh-output builds for the unchanged message-browser acceptance.

Run with ``python -m TESTS.adapters.django.message_browser_build --output PATH``.
Install UI dependencies and populate the Go module cache separately. This command
never installs packages, fetches modules, starts services, or edits UI sources.
Build trees and logs are private and removed; only completed, stable builds get
an observation. Build records and artifacts are private inputs, not CI uploads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from TESTS.adapters.django import message_reads_browser as browser
from TESTS.adapters.django import message_reads_browser_runtime as runtime
from TESTS.adapters.go import server as go_server

ROOT = Path(__file__).resolve().parents[3]
UI_SUFFIXES = {'.js', '.mjs', '.cjs', '.json', '.ts', '.html', '.txt'}
GO_ENV = {'GOFLAGS': '-p=1', 'GOWORK': 'off', 'GOENV': 'off',
          'GOTOOLCHAIN': 'local', 'GOPROXY': 'off', 'GOSUMDB': 'off'}
UI_ENV = {'DEPLOY_ENV': 'production', 'OEM_ENV': 'assozeta', 'VERSION': 'unstable',
          'VITE_DEV_HTTPS': 'false', 'NODE_OPTIONS': '--max-old-space-size=4096',
          'NODE_ENV': 'production'}
GO_GRAPH_COMMAND = ['go', 'list', '-mod=readonly', '-m', '-json', 'all']
NATIVE_BUILD_COMMAND = """import os
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from TESTS.adapters.go.server import build_server
build_server(Path(sys.argv[2]), sys.stdout, dict(os.environ))
"""


def need(condition, code):
    if not condition:
        raise RuntimeError(code)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def walk_error(error):
    # os.walk otherwise silently drops unreadable directories from inventories.
    raise error


def tree(directory, *, dependency_links=False):
    """Hash the whole tree, rejecting special files and escaping symlinks.

    npm's internal .bin file links are allowed only in installed dependencies;
    both their link text and target bytes are bound. Directory links are refused.
    """
    directory = Path(directory)
    need(directory.is_dir() and not directory.is_symlink(), 'tree-required')
    result = {}
    for base, directories, files in os.walk(directory, followlinks=False, onerror=walk_error):
        for name in sorted(directories + files):
            path = Path(base) / name
            relative = path.relative_to(directory).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                need(dependency_links, 'symlink-input')
                target = path.resolve(strict=True)
                need(target.is_relative_to(directory) and target.is_file(), 'dependency-link-escape')
                result['@link/' + relative] = hashlib.sha256(os.readlink(path).encode()).hexdigest()
                result[relative] = sha(target)
            elif stat.S_ISREG(mode):
                result[relative] = sha(path)
            else:
                need(stat.S_ISDIR(mode), 'special-file-input')
    return dict(sorted(result.items()))


def ui_inputs(root):
    root = Path(root)
    ui = root / 'UI'
    need(ui.is_dir() and not ui.is_symlink(), 'ui-required')
    result = {}
    for folder in ('src', 'public', 'scripts'):
        for name, digest in tree(ui / folder).items():
            result[f'UI/{folder}/{name}'] = digest
    for path in sorted(ui.iterdir()):
        if path.suffix in UI_SUFFIXES or path.name == 'Dockerfile':
            need(path.is_file() and not path.is_symlink(), 'ui-root-input')
            result['UI/' + path.name] = sha(path)
    required = {name for name in browser.required_source_paths() if name.startswith('UI/')}
    required.add('UI/package-lock.json')
    need(required <= result.keys(), 'missing-ui-build-input')
    return dict(sorted(result.items()))


def native_inputs(root):
    # Check directories too: rglob alone can silently omit a linked subtree.
    need(not (root / 'TESTS').is_symlink(), 'native-source-link')
    for directory in (root / 'BE_GO', root / 'TESTS/adapters'):
        need(not directory.is_symlink(), 'native-source-link')
        for base, directories, files in os.walk(directory, followlinks=False, onerror=walk_error):
            need(not any((Path(base) / name).is_symlink() for name in directories + files),
                 'native-source-link')
    result = runtime.native_source_hashes(root)
    need(result, 'native-inputs-required')
    return result


def tool_environment(environment, work):
    """Own HOME/config and flags; retain only provisioned tool/cache paths."""
    home = Path(environment.get('HOME', str(Path.home())))
    default_cache = home / ('Library/Caches/go-build' if sys.platform == 'darwin' else '.cache/go-build')
    cache_path = Path(environment.get('GOCACHE', str(default_cache)))
    module_path = Path(environment.get('GOMODCACHE', str(home / 'go/pkg/mod')))
    need(cache_path.is_absolute() and module_path.is_absolute(), 'absolute-go-cache-path-required')
    cache = cache_path.resolve()
    modules = module_path.resolve(strict=True)
    need(modules.is_dir(), 'offline-go-cache-required')
    owned_home, temporary = work / 'home', work / 'tmp'
    owned_home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    return {'PATH': environment.get('PATH', os.defpath), 'HOME': str(owned_home),
            'TMPDIR': str(temporary), 'GOCACHE': str(cache), 'GOMODCACHE': str(modules),
            'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC', **GO_ENV}


def go_dependencies(root, tools, log_path):
    """Bind only BE_GO's offline module graph, never the shared cache tree.

    Go reports a sequence of JSON objects. Bind declared and effective versions,
    actual directories, graph go.mod bytes, and each selected dependency tree.
    Local replacements must stay in this checkout or the provisioned cache.
    """
    with log_path.open('a') as log:
        result = subprocess.run(GO_GRAPH_COMMAND, cwd=root / 'BE_GO', env=tools,
                                check=True, timeout=60, text=True,
                                stdout=subprocess.PIPE, stderr=log)
    need(result.returncode == 0 and isinstance(result.stdout, str), 'go-module-graph-command')
    decoder = json.JSONDecoder(object_pairs_hook=runtime.unique_object)
    graph, hashes, position, main_count = {}, {}, 0, 0
    cache = Path(tools['GOMODCACHE'])

    def input_path(value, *, directory=False):
        need(isinstance(value, str) and value, 'go-module-input-path')
        path = Path(value)
        need(path.is_absolute() and path == path.resolve(strict=True) and
             (path.is_relative_to(root) or path.is_relative_to(cache)), 'go-module-input-escape')
        need(path.is_dir() if directory else path.is_file(), 'go-module-input-missing')
        return path

    while position < len(result.stdout):
        if result.stdout[position].isspace():
            position += 1
            continue
        module, position = decoder.raw_decode(result.stdout, position)
        need(isinstance(module, dict) and not module.get('Error'), 'go-module-graph-entry')
        name = module.get('Path')
        need(isinstance(name, str) and name and name not in graph, 'go-module-graph-duplicate')
        effective = module.get('Replace', module)
        need(isinstance(effective, dict) and not effective.get('Error'), 'go-module-replacement')
        directory = input_path(effective.get('Dir'), directory=True)
        go_mod = input_path(effective.get('GoMod'))
        main = module.get('Main', False)
        need(type(main) is bool, 'go-module-main')
        if main:
            need(directory == root / 'BE_GO' and effective is module, 'go-module-main-directory')
            main_count += 1
        else:
            need(isinstance(module.get('Version'), str) and module['Version'], 'go-module-version')
            hashes[name] = tree(directory)
        graph[name] = {'version': module.get('Version', ''), 'main': main,
            'effective_path': effective.get('Path'), 'effective_version': effective.get('Version', ''),
            'directory': str(directory), 'go_mod': str(go_mod), 'go_mod_sha256': sha(go_mod)}
    need(main_count == 1, 'go-module-main-required')
    return dict(sorted(graph.items())), dict(sorted(hashes.items()))


def run_ui(invocation, *, cwd, env, log, timeout):
    """Bound the actual process, including its child group on interruption."""
    process = subprocess.Popen(invocation, cwd=cwd, env=env, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)
        raise


class NativeCleanupError(RuntimeError):
    """Native ownership is unresolved; preserve private outputs for inspection."""


def stop_native(process):
    """Kill the owned session's group and reap its helper before any deletion.

    Also kill after a normal helper exit: an inner compiler timeout can reap
    only its direct child while leaving descendants in this group. Grandchildren
    are reaped by the OS after termination; only the helper is our waitable child.
    """
    try:
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                process.wait(timeout=10)
            # SIGKILL delivery and orphan reaping need not finish with the
            # helper. Do not remove outputs while any group member remains.
            deadline = time.monotonic() + 10
            while True:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                need(time.monotonic() < deadline, 'native-group-still-present')
                time.sleep(0.01)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    except BaseException:
        raise NativeCleanupError('native-process-cleanup-unverified') from None


def run_native(output, log, environment, *, root=ROOT):
    """Run the unchanged native helper within a bounded, owned process group."""
    invocation = [sys.executable, '-B', '-c', NATIVE_BUILD_COMMAND, str(root), str(output)]
    process = subprocess.Popen(invocation, cwd=root, env=environment, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        code = process.wait(timeout=240)
    finally:
        stop_native(process)
    if code != 0:
        raise subprocess.CalledProcessError(code, invocation)


def write_json(path, value):
    runtime.private_write(path, json.dumps(value, indent=2, sort_keys=True) + '\n')


def remove_owned(path):
    """Only called for this invocation's exact private tree/artifact names."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def build(output, *, root=ROOT, node=None, environment=None):
    root = Path(root).resolve(strict=True)
    # A symlink destination, including a dangling link, must never be reused.
    output = Path(output).absolute()
    need(not output.exists() and not output.is_symlink(), 'output-must-be-fresh')
    need(output.parent.is_dir(), 'output-parent-required')
    need(output.parent == output.parent.resolve(strict=True), 'output-parent-symlink')
    need(not output.is_relative_to(root / 'UI') and
         not output.is_relative_to(root / 'BE_GO') and
         not output.is_relative_to(root / 'TESTS'), 'output-overlaps-input')
    output.mkdir(mode=0o700)
    identity = output.stat()

    def check_owner():
        current = output.lstat()
        need(stat.S_ISDIR(current.st_mode) and
             (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), 'output-replaced')

    record = {'success': False, 'exit_code': None, 'source_unchanged': False,
              'build_inputs_unchanged': False, 'invocation': [], 'started': time.time(),
              'stage': 'preflight', 'native_build_completed': False,
              'scope': 'build inputs only; no browser or message-family acceptance',
              'environment': UI_ENV, 'go_environment': GO_ENV,
              'dependency_binding_limits': [
                  'Installed UI tree and BE_GO offline module graph/dependency trees hashed before/after.',
                  'Unrelated Go cache modules are excluded; local replacements are confined to '
                  'the checkout or provisioned module cache.',
                  'Go build cache, toolchain/standard library, cc, pkg-config and system libraries '
                  'are not content-bound by these dependency maps.',
                  'Before/after inventories detect persistent drift, not transient adversarial writes.',
              ]}
    for key in ('source_sha256', 'native_source_sha256', 'dependency_sha256',
                'node_sha256', 'copied_input_sha256'):
        record[key + '_before'] = None
        record[key + '_after'] = None
    record['artifact_sha256'] = {}
    record['go_module_graph_before'] = None
    record['go_module_graph_after'] = None
    record['go_module_graph_invocation'] = GO_GRAPH_COMMAND
    artifact = output / 'artifact'
    executable, codec = output / 'server', output / 'image-codec'
    work = None
    tools = None
    old_umask = os.umask(0o077)
    completed = False
    native_cleanup_verified = True
    try:
        need(not sys.flags.optimize, 'assert-enabled-python-required')
        environment = dict(os.environ if environment is None else environment)
        node = Path(node or shutil.which('node', path=environment.get('PATH', os.defpath)) or
                    '/missing-node').resolve(strict=True)
        need(node.is_file() and os.access(node, os.X_OK), 'node-executable-required')
        dependencies = (root / 'UI/node_modules').resolve(strict=True)
        need(dependencies.is_dir(), 'installed-ui-dependencies-required')
        vite = dependencies / 'vite/bin/vite.js'
        need(vite.is_file() and not vite.is_symlink(), 'installed-vite-required')
        work = Path(tempfile.mkdtemp(prefix='.work-', dir=output))
        tools = tool_environment(environment, work)
        modules = Path(tools['GOMODCACHE'])
        need(not output.is_relative_to(modules) and not output.is_relative_to(dependencies),
             'output-overlaps-dependencies')
        log_path = work / 'build.log'
        log_path.touch(mode=0o600, exist_ok=False)

        def snapshot(suffix):
            record['source_sha256_' + suffix] = ui_inputs(root)
            record['native_source_sha256_' + suffix] = native_inputs(root)
            graph, go_hashes = go_dependencies(root, tools, log_path)
            record['go_module_graph_' + suffix] = graph
            record['dependency_sha256_' + suffix] = {
                'ui': tree(dependencies, dependency_links=True), 'go_modules': go_hashes}
            record['node_sha256_' + suffix] = sha(node)

        record['stage'] = 'input-inventory'
        snapshot('before')
        record['source_sha256'] = record['source_sha256_before']
        record['dependency_paths'] = {'ui': str(dependencies), 'go_modules': str(modules)}
        record['stage'] = 'copy'
        for name in record['source_sha256']:
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
        record['copied_input_sha256_before'] = ui_inputs(work)
        need(record['source_sha256'] == record['copied_input_sha256_before'], 'copy-drift')
        (work / 'UI/node_modules').symlink_to(dependencies, target_is_directory=True)
        invocation = [str(node), str(vite), 'build', '--sourcemap', '--outDir', str(artifact)]
        record['invocation'] = invocation
        record['working_directory'] = str(work / 'UI')
        record['dependency_link'] = 'private UI/node_modules -> dependency_paths.ui'
        with log_path.open('a') as log:
            record['stage'] = 'ui-command'
            record['exit_code'] = run_ui(invocation, cwd=work / 'UI', env={**tools, **UI_ENV},
                                         log=log, timeout=600)
            need(type(record['exit_code']) is int and record['exit_code'] == 0, 'ui-command-failed')
            # Validate maps from the actual just-completed Vite output, not an
            # existing artifact supplied by the caller.
            record['stage'] = 'ui-provenance'
            need((artifact / 'index.html').is_file() and (artifact / 'index.html').stat().st_size > 0,
                 'ui-entry-required')
            ui_proof = browser.capture_provenance(artifact, root=root)
            record['artifact_sha256'] = tree(artifact)
            need(record['artifact_sha256'] == ui_proof['artifact_sha256'], 'ui-artifact-binding')
            snapshot('after_ui')
            for key in ('source_sha256', 'native_source_sha256', 'dependency_sha256',
                        'node_sha256', 'go_module_graph'):
                need(record[key + '_before'] == record[key + '_after_ui'], 'input-drift')
            need(record['source_sha256'] == ui_inputs(work), 'copied-input-drift')
            # The child invokes the actual shared helper with this exact owned
            # environment/log; successful return alone does not prove outputs.
            record['stage'] = 'native-command'
            run_native(executable, log, tools, root=root)
            record['native_build_completed'] = True
        record['stage'] = 'post-build-binding'
        for path in (executable, codec):
            need(path.is_file() and not path.is_symlink() and path.stat().st_size > 0
                 and os.access(path, os.X_OK), 'native-output-required')
        native_artifacts = {'server': sha(executable), 'image-codec': sha(codec)}
        snapshot('after')
        record['copied_input_sha256_after'] = ui_inputs(work)
        record['source_unchanged'] = record['source_sha256_before'] == record['source_sha256_after']
        record['build_inputs_unchanged'] = (record['source_sha256'] ==
            record['copied_input_sha256_before'] == record['copied_input_sha256_after'])
        need(record['source_unchanged'] and record['build_inputs_unchanged'], 'ui-input-drift')
        for key in ('native_source_sha256', 'dependency_sha256', 'node_sha256', 'go_module_graph'):
            need(record[key + '_before'] == record[key + '_after'], 'native-or-dependency-drift')
        need(tree(artifact) == record['artifact_sha256'], 'artifact-drift')
        need(browser.capture_provenance(artifact, root=root) == ui_proof, 'provenance-drift')
        check_owner()
        completed = True
    except NativeCleanupError:
        native_cleanup_verified = False
        record['error'] = 'build.cleanup-failed'
    except BaseException:
        # No private subprocess output, environment or exception strings escape.
        record['error'] = 'build.failed'
    finally:
        try:
            check_owner()
            need(native_cleanup_verified, 'native-process-cleanup-unverified')
            if work is not None:
                # Preserve bounded failure evidence as well. A missing input
                # yields null, never the original map silently reused as "after".
                if not completed:
                    readers = {'source_sha256': lambda: ui_inputs(root),
                        'native_source_sha256': lambda: native_inputs(root),
                        'copied_input_sha256': lambda: ui_inputs(work),
                        'node_sha256': lambda: sha(node)}
                    if tools is not None:
                        try:
                            graph, go_hashes = go_dependencies(root, tools, work / 'build.log')
                            record['go_module_graph_after'] = graph
                            record['dependency_sha256_after'] = {
                                'ui': tree(dependencies, dependency_links=True), 'go_modules': go_hashes}
                        except BaseException:
                            record['go_module_graph_after'] = None
                            record['dependency_sha256_after'] = None
                    for key, reader in readers.items():
                        try:
                            record[key + '_after'] = reader()
                        except BaseException:
                            record[key + '_after'] = None
                    try:
                        record['artifact_sha256'] = tree(artifact)
                    except BaseException:
                        record['artifact_sha256'] = {}
                record['source_unchanged'] = bool(record['source_sha256_before']) and (
                    record['source_sha256_before'] == record['source_sha256_after'])
                record['build_inputs_unchanged'] = bool(record['copied_input_sha256_before']) and (
                    record['source_sha256_before'] == record['copied_input_sha256_before'] ==
                    record['copied_input_sha256_after'])
                remove_owned(work)
            record['private_cleanup_verified'] = work is None or not work.exists()
        except BaseException:
            record['private_cleanup_verified'] = False
            record['error'] = 'build.cleanup-failed'
            completed = False
        os.umask(old_umask)

    check_owner()
    record['success'] = completed and record['private_cleanup_verified']
    record['finished'] = time.time()
    if record['success']:
        record['stage'] = 'records'
        native_record = {'success': True, 'source_stable': True,
                         'source_sha256': record['native_source_sha256_before'],
                         'artifacts': native_artifacts, 'builder': 'TESTS.adapters.go.server.build_server'}
        try:
            write_json(output / 'go-build-record.json', native_record)
            runtime.validate_go_build_record(output / 'go-build-record.json', executable, codec, root=root)
            write_json(output / 'build-record.json', record)
            observation = {key: record[key] for key in ('invocation', 'source_sha256', 'artifact_sha256')}
            observation['record_sha256'] = sha(output / 'build-record.json')
            write_json(output / 'build-observation.json', observation)
            runtime.validate_build_record(output / 'build-observation.json',
                                          browser.capture_provenance(artifact, root=root))
            return 0
        except BaseException:
            record['success'] = False
            record['error'] = 'build.record-validation-failed'
    # Once native termination is verified, retain only a false record on failure.
    # Otherwise preserve the private tree/products rather than race a writer.
    # Never clear the destination or any parent/cache/dependency tree.
    if native_cleanup_verified:
        for name in ('artifact', 'server', 'image-codec', 'build-observation.json',
                     'go-build-record.json', 'build-record.json'):
            remove_owned(output / name)
    write_json(output / 'build-record.json', record)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='Fresh path with an existing parent; parent must have no symlink components')
    parser.add_argument('--node', type=Path, help='Existing Node 22 executable (defaults to PATH)')
    args = parser.parse_args(argv)

    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        result = build(args.output, node=args.node)
    except BaseException:
        result = 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps({'success': result == 0, 'check': 'message-browser.build-only'}))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
