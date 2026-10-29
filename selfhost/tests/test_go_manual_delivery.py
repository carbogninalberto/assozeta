"""Manual image wiring without Docker; real validator behavior has its CLI oracle.

Execute the Dockerfile's metadata/permissions and gate command fragments in private
host directories. This is argument/exit-code and file-mode evidence, not an image
build, a container UID test or a substitute for the actual packaged-image gate.
"""
import ast
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def instructions(path):
    result = []
    pending = ''
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        pending += line.strip()
        if pending.endswith('\\'):
            pending = pending[:-1] + ' '
            continue
        operation, _, argument = pending.partition(' ')
        result.append((operation.upper(), argument.strip()))
        pending = ''
    if pending:
        raise AssertionError('Incomplete Docker instruction')
    return result


def github_condition(expression, name, event):
    expression = expression.removeprefix('${{').removesuffix('}}').strip()
    expression = expression.replace('matrix.name', repr(name)).replace('github.event_name', repr(event))
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    parsed = ast.parse(expression.strip(), mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq, ast.Constant)
    if any(not isinstance(node, allowed) for node in ast.walk(parsed)):
        raise AssertionError('Review changed manual artifact condition semantics')
    return eval(compile(parsed, '<workflow-condition>', 'eval'), {'__builtins__': {}})


class GoManualDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.docker = instructions(ROOT/'BE_GO/Dockerfile')
        self.workflow = (ROOT/'.github/workflows/publish-images.yml').read_text()

    def test_release_artifact_reaches_both_backend_contexts_only(self):
        start = self.workflow.index('      - name: Download the verified release manual')
        stop = self.workflow.index('\n      - name:', start + 1)
        step = self.workflow[start:stop]
        condition = re.search(r'^        if: (.+)$', step, re.M).group(1)
        destination = re.search(r'^          path: (.+)$', step, re.M).group(1)
        self.assertIn('uses: actions/download-artifact@v4', step)
        self.assertIn('name: manuale-release-runtime', step)
        matrix = self.workflow.split('        include:', 1)[1].split('    steps:', 1)[0]
        contexts = dict(re.findall(r'- name: ([^\n]+).*?\n            context: ([^\n]+)', matrix, re.S))
        self.assertEqual(set(contexts), {'backend', 'backend-go', 'web', 'renderer', 'updater'})
        for name in contexts:
            for event in ('push', 'release', 'workflow_dispatch'):
                with self.subTest(name=name, event=event):
                    expected = name in ('backend', 'backend-go') and event != 'push'
                    self.assertEqual(github_condition(condition, name, event), expected)
                    if expected:
                        context = contexts[name]
                        self.assertEqual(context, './BE' if name == 'backend' else './BE_GO')
                        path = destination.replace('${{ matrix.context }}', context)
                        self.assertEqual((ROOT/path).resolve(), (ROOT/context/'release-manual').resolve())
        self.assertLess(start, self.workflow.index('      - name: Build and publish'))
        self.assertIn("needs.manual-release.result == 'success'", self.workflow)
        self.assertIn("REQUIRE_MANUAL=${{ github.event_name != 'push' }}", self.workflow)

    def test_runtime_preserves_all_binaries_codec_licenses_and_server_health(self):
        for name in ('server', 'worker', 'scheduler', 'migrate', 'check-manual'):
            self.assertTrue(any(op == 'RUN' and f'-o /{name} ./cmd/{name}' in arg for op, arg in self.docker), name)
            self.assertIn(('COPY', f'--from=builder /{name} /app/{name}'), self.docker)
        for destination in ('image-codec', 'licenses/libjpeg-turbo-LICENSE.md', 'licenses/zlib-LICENSE',
                            'licenses/libwebp-LICENSE', 'licenses/Pillow-LICENSE', 'licenses/go-qrcode-LICENSE',
                            'licenses/loginimage-NOTICE', 'migrations'):
            self.assertTrue(any(op == 'COPY' and arg.endswith('/app/'+destination) for op, arg in self.docker), destination)
        self.assertEqual([arg for op, arg in self.docker if op == 'ENTRYPOINT'], ['["/app/server"]'])
        self.assertTrue(any(op == 'HEALTHCHECK' and 'http://127.0.0.1:8000/readyz' in arg for op, arg in self.docker))
        self.assertEqual([arg for op, arg in self.docker if op == 'USER'], ['10001:10001'])

    def test_required_marker_and_atomic_package_modes_execute_as_declared(self):
        runs = [arg for op, arg in self.docker if op == 'RUN' and ('> /app/MANUAL_REQUIRED' in arg or 'chmod' in arg and '/app/release-manual' in arg)]
        self.assertTrue(any('> /app/MANUAL_REQUIRED' in command for command in runs), 'missing image required marker')
        self.assertTrue(any('chmod' in command for command in runs), 'missing package readability/immutability normalization')
        self.assertIn(('ARG', 'REQUIRE_MANUAL=false'), self.docker)
        self.assertIn(('COPY', '--chown=0:0 release-manual/ /app/release-manual/'), self.docker)
        for required in ('true', 'false'):
            with self.subTest(required=required), tempfile.TemporaryDirectory(prefix='manual-delivery-') as directory:
                app = Path(directory)/'app';package = app/'release-manual';package.mkdir(parents=True)
                package.chmod(0o700);image=package/'index.json';image.write_bytes(b'fixture');image.chmod(0o600)
                for command in runs:
                    command = command.replace('/app', shlex.quote(str(app)))
                    subprocess.run(['sh', '-ec', command], check=True, env={**os.environ, 'VERSION':'release-fixture', 'CODE_REVISION':'revision-fixture', 'REQUIRE_MANUAL':required}, capture_output=True, text=True)
                self.assertEqual((app/'MANUAL_REQUIRED').read_text(), required+'\n')
                self.assertEqual((app/'VERSION').read_text(), 'release-fixture\n')
                self.assertEqual((app/'CODE_REVISION').read_text(), 'revision-fixture\n')
                for path in (package, image):
                    mode=stat.S_IMODE(path.stat().st_mode)
                    self.assertEqual(mode & 0o222, 0)
                    self.assertEqual(mode & 0o444, 0o444)
                self.assertEqual(stat.S_IMODE(package.stat().st_mode) & 0o111, 0o111)
                # Host cleanup remains possible even though the artifact directory
                # intentionally became immutable for its owner.
                package.chmod(0o700)

    def test_gate_runs_after_runtime_user_and_propagates_failure(self):
        gates = [(i,arg) for i,(op,arg) in enumerate(self.docker) if op == 'RUN' and '/app/check-manual ' in arg]
        self.assertEqual(len(gates), 1, 'one explicit immutable bundle gate required')
        index,command=gates[0]
        self.assertIn(('COPY', '--chown=0:0 release-manual/ /app/release-manual/'), self.docker[:index])
        self.assertTrue(any(op == 'RUN' and 'chmod' in arg and '/app/release-manual' in arg for op,arg in self.docker[:index]))
        self.assertEqual([arg for op,arg in self.docker[:index] if op == 'USER'][-1], '10001:10001')
        with tempfile.TemporaryDirectory(prefix='manual-delivery-gate-') as directory:
            root=Path(directory);probe=root/'check-manual';receipt=root/'arguments.json'
            probe.write_text(f'#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["PROBE_RECEIPT"]).write_text(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ["PROBE_EXIT"]))\n');probe.chmod(0o700)
            for required in ('true','false'):
                for exit_code in (0,1):
                    process=subprocess.run(['sh','-ec',command.replace('/app/check-manual',shlex.quote(str(probe)))],
                        env={**os.environ,'PROBE_RECEIPT':str(receipt),'PROBE_EXIT':str(exit_code),'CODE_REVISION':'revision with spaces','VERSION':'release with spaces','REQUIRE_MANUAL':required},capture_output=True,text=True)
                    self.assertEqual(process.returncode,exit_code)
                    self.assertEqual(json.loads(receipt.read_text()),['--root','/app/release-manual','--revision','revision with spaces','--release','release with spaces','--required',required])

    def test_placeholder_is_shipped_and_django_gate_is_retained(self):
        self.assertEqual((ROOT/'BE_GO/release-manual/.gitkeep').read_bytes(),(ROOT/'BE/release-manual/.gitkeep').read_bytes())
        ignored=(ROOT/'BE_GO/.dockerignore').read_text().splitlines()
        self.assertNotIn('release-manual',ignored);self.assertNotIn('release-manual/',ignored)
        django=instructions(ROOT/'BE/Dockerfile')
        gate=next(i for i,(op,arg) in enumerate(django) if op=='RUN' and 'python -m application.manuale.bundle' in arg)
        self.assertEqual([arg for op,arg in django[:gate] if op=='USER'][-1],'assozeta')
        self.assertIn('--required "$REQUIRE_MANUAL"',django[gate][1])


if __name__ == '__main__':
    unittest.main()
