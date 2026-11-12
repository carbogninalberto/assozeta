"""Actual Django/Go processes contend on one private manual-cache volume.

No database, production cache, external publisher or HTTP route is involved.
The publisher deliberately holds the first corpus response to make ownership
observable before the second runtime enters its synchronization function.
"""
import argparse
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'BE')]
OUTPUT = ROOT / 'quality-reports/go-migration/manual-refresh-lock'


def await_condition(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Private publisher/process checkpoint timed out')
        time.sleep(.01)


def django_child(path):
    from django.conf import settings
    manifest = json.loads(Path(path).read_text())
    settings.configure(**manifest['django_settings'])
    from application.manuale.sync import synchronize
    Path(manifest['ReadyPath']).write_text('ready')
    result = synchronize()
    assert result['status'] == 'updated'


def exercise(work, binary, log, first_runtime, cancel_go=False):
    from TESTS.adapters.django.manual_reader_http import PNG, Publisher, prepare

    class HoldingPublisher(Publisher):
        def do_GET(self):
            self.server.started_requests.append(self.path)
            if len(self.server.started_requests) == 1:
                self.server.entered.set()
                if not self.server.release.wait(8):
                    self.send_error(504)
                    return
            super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), HoldingPublisher)
    server.daemon_threads = True
    server.entered, server.release = threading.Event(), threading.Event()
    server.started_requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    processes = []
    name = first_runtime + ('-go-cancelled' if cancel_go else '-then-other')
    root = work / name
    try:
        settings, cache = prepare(root, {'mode': 'remote-success'}, f'http://127.0.0.1:{server.server_port}')
        fields = {'MANUAL_BUNDLE_PATH': 'BundlePath', 'MANUAL_BUNDLE_REQUIRED': 'BundleRequired',
                  'MANUAL_INDEX_PATH': 'IndexPath', 'MANUAL_ASSET_ROOT': 'AssetRoot',
                  'MANUAL_SOURCE_ROOT': 'SourceRoot', 'MANUAL_CORPUS_BASE_URL': 'CorpusBaseURL',
                  'MANUAL_APPLICATION_REVISION': 'ApplicationRevision', 'RUNNING_VERSION': 'Release',
                  'MANUAL_RUN_ID': 'RunID', 'MANUAL_DEVELOPMENT_ROOT': 'DevelopmentRoot',
                  'ASSOZETA_DEPLOYMENT_MODE': 'DeploymentMode'}

        def start(runtime):
            ready = root / (runtime + '-ready')
            manifest = root / (runtime + '.json')
            manifest.write_text(json.dumps({'django_settings': settings,
                'Settings': {fields[k]: v for k, v in settings.items()},
                'ReadyPath': str(ready), 'Cancel': cancel_go and runtime == 'go'}))
            command = ([sys.executable, str(Path(__file__).resolve()), '--django-child', str(manifest)]
                       if runtime == 'django' else [str(binary), '-test.run', '^TestManualRefreshCrossProcess$', '-test.v'])
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, 'ASSOZETA_MANUAL_LOCK_FIXTURE': str(manifest)})
            processes.append(process)
            await_condition(lambda: ready.exists() or process.poll() is not None)
            assert ready.exists() and process.poll() is None, runtime + ' failed before synchronization'
            return process

        first = start(first_runtime)
        assert server.entered.wait(5), 'first runtime did not enter its HTTP download'
        second = start('go' if first_runtime == 'django' else 'django')
        if cancel_go:
            # Go must exit on its own deadline without requesting any bytes.
            assert second.wait(timeout=3) == 0, 'Go did not cancel its file-lock wait'
        else:
            # The ready marker is emitted immediately before the actual sync
            # call. A full interval then proves it cannot pass the held lease.
            deadline = time.monotonic() + .35
            while time.monotonic() < deadline:
                assert second.poll() is None, 'competing publisher exited before lease release'
                assert len(server.started_requests) == 1, 'competing runtime bypassed shared file lock'
                time.sleep(.01)
        assert len(server.started_requests) == 1, 'download escaped the held lease'
        assert not (cache / 'index.json').exists(), 'corpus promoted before its download completed'
        server.release.set()
        assert first.wait(timeout=10) == 0, 'first runtime refresh failed'
        assert second.wait(timeout=10) == 0, 'second runtime refresh failed'
        assert json.loads((cache / 'index.json').read_bytes()) == json.loads(Publisher.payload)
        assert (cache / 'assets' / (hashlib.sha256(PNG).hexdigest() + '.png')).read_bytes() == PNG
        corpus_requests = sum(path.endswith('.json') for path in server.started_requests)
        assert corpus_requests == (1 if cancel_go else 2), 'unexpected corpus fetch count'
        assert server.started_requests.count('/image.png') == 1, 'second runtime did not reuse verified asset'
        leftovers = [p.name for p in cache.rglob('*') if p.name.startswith(('.manual-', '.tmp'))]
        assert not leftovers, 'temporary publication files escaped cleanup'
        return {'name': name, 'passed': True, 'corpus_requests': corpus_requests,
                'image_requests': 1, 'go_cancelled': cancel_go}
    finally:
        server.release.set()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--django-child')
    args = parser.parse_args()
    if args.django_child:
        django_child(args.django_child)
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'cases': [], 'scope': __doc__}
    try:
        with tempfile.TemporaryDirectory(prefix='az-manual-lock-', dir='/tmp') as directory, (OUTPUT / 'processes.log').open('w') as log:
            work = Path(directory)
            binary = work / 'source-test'
            subprocess.run(['go', 'test', '-race', '-c', '-tags', 'manualsourceintegration',
                            '-o', str(binary), './pkg/manual'], cwd=ROOT / 'BE_GO', check=True,
                           timeout=180, stdout=log, stderr=subprocess.STDOUT)
            for first, cancelled in [('django', True), ('django', False), ('go', False)]:
                report['cases'].append(exercise(work, binary, log, first, cancelled))
        report.update(passed=True, private_processes_stopped=True)
    finally:
        (OUTPUT / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: three actual Django/Go shared-lock scenarios')


if __name__ == '__main__':
    main()
