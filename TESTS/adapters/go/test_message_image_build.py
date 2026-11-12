"""Offline builder controls. Synthetic Docker responses prove no live acceptance."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import tarfile
import unittest
import zlib
from unittest.mock import patch

from TESTS.adapters.go import message_image_build as builder
from TESTS.adapters.go.message_image_execution import ExecutionInterrupted
from TESTS.adapters.go.message_read_scopes_image import verify_build_receipt

REVISION = 'c' * 40
IMAGE = 'sha256:' + 'a' * 64
CONTAINER = 'd' * 64
def elf_fixture(machine=62, kind=2, phoff=64, note=False, osabi=0):
    """Authored ELF layout: RX headers/code at 0..288; RW data/BSS at 512.

    Entry points to real ISA return instructions, never executed by this suite.
    The data LOAD includes 32 file bytes and 32 additional zero-fill bytes.
    """
    count = 3 if note else 2
    payload = bytearray(544)
    ident = b'\x7fELF\x02\x01\x01' + bytes([osabi]) + bytes(8)
    base = 0 if kind == 3 else 0x400000
    struct.pack_into('<16sHHIQQQIHHHHHH', payload, 0, ident, kind, machine, 1,
                     base + 256, phoff, 0, 0, 64, 56, count, 0, 0, 0)
    struct.pack_into('<IIQQQQQQ', payload, phoff, 1, 5, 0, base, base, 288, 288, 4096)
    struct.pack_into('<IIQQQQQQ', payload, phoff + 56, 1, 6, 512,
                     base + 0x1200, base + 0x1200, 32, 64, 4096)
    if note:
        struct.pack_into('<IIQQQQQQ', payload, phoff + 112, 4, 4, 528, 0, 0, 16, 0, 4)
    payload[256:264] = b'\x31\xc0\xc3' + bytes(5) if machine == 62 else b'\xc0\x03\x5f\xd6' + bytes(4)
    payload[512:544] = b'RW data followed by zero fill'.ljust(32, b'\0')
    return bytes(payload)


ELF = elf_fixture()
INPUTS = {'BE_GO/Dockerfile': b'FROM fixture\n', 'BE_GO/go.mod': b'module fixture\n',
          'BE_GO/go.sum': b'fixture sums\n', 'BE_GO/cmd/server/main.go': b'package main\n',
          'BE_GO/THIRD_PARTY_NOTICES.md': b'licences\n',
          'BE_GO/assets/payload.bin': b'\x00\xffasset',
          'BE_GO/third_party/dependency/LICENSE': b'transitive notice'}
EXPECTED = {name: hashlib.sha256(data).hexdigest() for name, data in INPUTS.items()}


class FakeCommands:
    """Exact external responses; never executes Git writes, Docker or services."""
    def __init__(self, checkout, fault=None):
        self.checkout, self.fault = checkout, fault
        self.calls, self.created, self.removed = [], False, False
        self.owner, self.name = None, None
        self.blobs = {hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest(): data
                      for data in INPUTS.values()}

    def __call__(self, args, *, timeout=60, data=None, target=None, capture=True):
        self.calls.append((list(args), timeout))
        if args[0] == 'git':
            op = args[args.index('-C') + 2]
            if op == 'rev-parse':
                return (('e' * 40 if self.fault == 'head' else REVISION) + '\n').encode()
            if op == 'status':
                return b' M BE_GO/go.sum\0' if self.fault == 'dirty' else b''
            if op == 'ls-tree':
                return b''.join(b'100644 blob ' + oid.encode() + b'\t' + name.encode() + b'\0'
                    for name, content in sorted(INPUTS.items())
                    for oid, blob in self.blobs.items() if blob == content)
            if op == 'cat-file':
                content = b''.join(oid + b' blob ' + str(len(self.blobs[oid.decode()])).encode()
                    + b'\n' + self.blobs[oid.decode()] + b'\n' for oid in data.splitlines())
                target.write_bytes(content)
                return b''
            raise AssertionError(args)
        if args[:2] == ['docker', 'build']:
            if self.fault in ('build', 'timeout'):
                if self.fault == 'timeout':
                    raise subprocess.TimeoutExpired('private-build-command', timeout)
                raise RuntimeError('private-token-build-failure')
            Path(args[args.index('--iidfile') + 1]).write_text(IMAGE + '\n')
            if self.fault == 'source':
                (Path(args[-1]) / 'go.sum').write_bytes(b'changed during build')
            if self.fault == 'source-extra':
                (Path(args[-1]) / 'untracked-input').write_bytes(b'new context input')
            if self.fault == 'source-mode':
                (Path(args[-1]) / 'go.sum').chmod(0o755)
            if self.fault == 'checkout':
                (self.checkout / 'BE_GO/go.sum').write_bytes(b'changed checkout')
            return b''
        if args[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': 'sha256:'+'f'*64 if self.fault == 'image' else IMAGE,
                'Os': 'linux', 'Architecture': 'amd64',
                'Config': {'User': '10001:10001', 'Entrypoint': ['/app/server'], 'Volumes': None}}]).encode()
        if args[:2] == ['docker', 'create']:
            self.created = True
            self.name = args[args.index('--name') + 1]
            self.owner = args[args.index('--label') + 1].split('=', 1)[1]
            if self.fault == 'create':
                raise subprocess.TimeoutExpired('private-create', timeout)
            return (CONTAINER + '\n').encode()
        if args[:3] == ['docker', 'container', 'inspect']:
            return json.dumps([{'Id': CONTAINER, 'Name': '/' + self.name, 'Image': IMAGE,
                'Config': {'Labels': {builder.LABEL: 'foreign' if self.fault == 'foreign' else self.owner}},
                'State': {'Running': False}}]).encode()
        if args[:2] == ['docker', 'cp']:
            if args[2].endswith('/CODE_REVISION'):
                Path(args[3]).write_bytes((('f'*40 if self.fault == 'revision' else REVISION) + '\n').encode())
            else:
                payload = b'wrong executable' if self.fault == 'binary' else ELF
                with tarfile.open(target, 'w') as tar:
                    info = tarfile.TarInfo('server')
                    info.mode = 0o700 if self.fault == 'owner-only' else 0o755
                    info.size = len(payload)
                    tar.addfile(info, io.BytesIO(payload))
            return b''
        if args[:3] == ['docker', 'container', 'ls']:
            if self.fault == 'cleanup-query': raise PermissionError('private-daemon-path')
            return ((CONTAINER + '\n').encode() if self.created and not self.removed else b'')
        if args[:2] == ['docker', 'rm']:
            if self.fault == 'cleanup': raise PermissionError('private-cleanup-path')
            self.removed = self.fault != 'cleanup-still-present'
            return (CONTAINER + '\n').encode()
        raise AssertionError(args)


class BuilderControls(unittest.TestCase):
    def setUp(self):
        self.guard = patch('socket.socket', side_effect=AssertionError('offline.socket-forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.checkout = self.root / 'checkout'
        for name, content in INPUTS.items():
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.checkout / 'BE_GO/.DS_Store').write_bytes(b'local metadata must never enter context')
        self.output = self.root / 'fresh'

    def run_build(self, fault=None):
        commands = FakeCommands(self.checkout, fault)
        result = builder.build(self.checkout, REVISION, self.output,
                               commands_factory=lambda work: commands)
        return result, commands

    def test_receipt_matches_independent_hashes_and_existing_consumer(self):
        report, commands = self.run_build()
        self.assertTrue(report['success'])
        self.assertEqual(report['source_sha256'], EXPECTED)
        self.assertEqual(report['server_sha256'], hashlib.sha256(ELF).hexdigest())
        self.assertEqual(report['packaged_revision'], REVISION)
        self.assertEqual(verify_build_receipt(report, self.output/'source', IMAGE, REVISION),
                         hashlib.sha256(ELF).hexdigest())
        self.assertTrue(report['cleanup_verified'])
        self.assertTrue(report['private_directory_removed'])
        self.assertEqual(set(p.name for p in self.output.iterdir()), {'source', 'results.json'})
        self.assertFalse((self.output/'source/BE_GO/.DS_Store').exists())
        build_args, deadline = next(c for c in commands.calls if c[0][:2] == ['docker', 'build'])
        self.assertIn('CODE_REVISION=' + REVISION, build_args)
        self.assertNotIn('-t', build_args)
        self.assertGreater(deadline, 60)
        self.assertLessEqual(deadline, 1800)
        self.assertTrue(all(0 < timeout <= 1800 for _, timeout in commands.calls))
        removed = [args for args, _ in commands.calls if args[:2] == ['docker', 'rm']]
        self.assertEqual(removed, [['docker', 'rm', CONTAINER]])

    def test_wrong_revision_image_binary_and_source_never_accept(self):
        for fault in ('revision', 'image', 'binary', 'source', 'source-extra', 'source-mode', 'checkout'):
            with self.subTest(fault=fault):
                self.output = self.root / ('output-' + fault)
                report, commands = self.run_build(fault)
                self.assertFalse(report['success'])
                self.assertTrue(report['errors'])
                self.assertTrue(report['private_directory_removed'])
                if commands.created:
                    self.assertTrue(commands.removed)

    def test_wrong_head_dirty_checkout_and_hidden_tracked_change_reject_before_docker(self):
        for fault in ('head', 'dirty', 'hidden-change'):
            with self.subTest(fault=fault):
                self.output = self.root / ('output-' + fault)
                if fault == 'hidden-change':
                    (self.checkout/'BE_GO/go.sum').write_bytes(b'assume-unchanged must not hide this')
                report, commands = self.run_build(fault)
                self.assertFalse(report['success'])
                self.assertFalse(any(args[0] == 'docker' for args, _ in commands.calls))

    def test_failures_and_timeouts_propagate_as_redacted_receipts(self):
        for fault in ('build', 'timeout', 'create', 'cleanup', 'cleanup-query', 'cleanup-still-present', 'foreign'):
            with self.subTest(fault=fault):
                self.output = self.root / ('output-' + fault)
                report, commands = self.run_build(fault)
                self.assertFalse(report['success'])
                self.assertEqual(report, json.loads((self.output/'results.json').read_text()))
                public = json.dumps(report)
                for forbidden in ('private-token', 'private-build', 'private-daemon', 'private-cleanup', str(self.root)):
                    self.assertNotIn(forbidden, public)
                if fault.startswith('cleanup') or fault == 'foreign':
                    self.assertFalse(report['cleanup_verified'])
                    self.assertTrue(report['cleanup_errors'])
                if fault == 'foreign':
                    self.assertFalse(any(args[:2] == ['docker', 'rm'] for args, _ in commands.calls))
                if fault == 'create':
                    self.assertTrue(commands.removed)  # Name registered before uncertain create.

    def test_stale_output_untouched_and_no_commands_run(self):
        self.output.mkdir()
        receipt = self.output / 'results.json'
        receipt.write_bytes(b'old-success-do-not-relabel')
        with patch.object(builder, 'Commands') as commands, self.assertRaises(FileExistsError):
            builder.build(self.checkout, REVISION, self.output)
        commands.assert_not_called()
        self.assertEqual(receipt.read_bytes(), b'old-success-do-not-relabel')

    def test_invalid_revision_and_optimized_python_rejected_before_output(self):
        for revision in ('HEAD', 'c'*39, 'C'*40):
            with self.assertRaises(ValueError):
                builder.build(self.checkout, revision, self.output)
            self.assertFalse(self.output.exists())
        result = subprocess.run([sys.executable, '-O', '-m', 'TESTS.adapters.go.message_image_build',
            '--expected-revision', REVISION, '--output', str(self.output)], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('optimized-python', result.stderr)
        self.assertFalse(self.output.exists())

    def test_production_interpreter_rejection_precedes_output(self):
        with patch.object(builder, 'require_production_python', side_effect=RuntimeError('wrong interpreter')), \
             self.assertRaises(RuntimeError):
            builder.build(self.checkout, REVISION, self.output)
        self.assertFalse(self.output.exists())

    def test_main_returns_nonzero_for_failed_build_and_no_private_exception(self):
        with patch.object(builder, 'ROOT', self.checkout), \
             patch.object(builder, 'Commands', side_effect=lambda work: FakeCommands(self.checkout, 'build')), \
             redirect_stdout(io.StringIO()) as public:
            code = builder.main(['--expected-revision', REVISION, '--output', str(self.output)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(public.getvalue()), {'success': False, 'check': 'message-image.build'})

    def test_command_interruption_starts_with_term_and_verifies_group_absence(self):
        process = unittest.mock.Mock(pid=9876, returncode=0)
        process.communicate.side_effect = [KeyboardInterrupt(), (None, None), (None, None)]
        def killpg(pid, sig):
            if sig == 0:
                raise ProcessLookupError()
        with patch.object(builder.subprocess, 'Popen', return_value=process), \
             patch.object(builder.os, 'killpg', side_effect=killpg) as kill, self.assertRaises(KeyboardInterrupt):
            builder.Commands(self.root)(['not-executed'])
        self.assertEqual(kill.call_args_list[0].args, (9876, signal.SIGTERM))
        self.assertIn(unittest.mock.call(9876, 0), kill.call_args_list)

    def test_signal_during_popen_assignment_still_cleans_owned_command_group(self):
        process = unittest.mock.Mock(pid=9876, returncode=-15)
        def spawn(*args, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return process
        with builder.ExecutionDeadline(20) as control:
            with patch.object(builder.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(builder.os, 'killpg') as kill, \
                 patch.object(builder.Commands, 'group_exists', return_value=False), \
                 self.assertRaises(ExecutionInterrupted):
                builder.Commands(self.root)(['not-executed'])
            kill.assert_called_once_with(9876, signal.SIGTERM)
            self.assertTrue(control.interrupted)

    def test_process_timeout_kills_group_and_uses_private_output(self):
        process = unittest.mock.Mock()
        process.pid = 9876
        process.communicate.side_effect = [subprocess.TimeoutExpired('private-command', 1), (None, None), (None, None)]
        with patch.object(builder.subprocess, 'Popen', return_value=process) as start, \
             patch.object(builder.os, 'killpg') as kill, \
             patch.object(builder.Commands, 'group_exists', return_value=False), \
             self.assertRaisesRegex(RuntimeError, '^build.command-timeout$'):
            builder.Commands(self.root)(['not-executed', 'private-command'], timeout=1)
        kill.assert_called_once_with(9876, signal.SIGTERM)
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        self.assertNotEqual(start.call_args.kwargs['stdout'], subprocess.PIPE)
        self.assertNotEqual(start.call_args.kwargs['stderr'], subprocess.PIPE)


    def test_process_interruption_kills_group_before_private_files_are_removed(self):
        process = unittest.mock.Mock()
        process.pid = 1234
        process.communicate.side_effect = [KeyboardInterrupt('private-interrupt'), (None, None), (None, None)]
        with patch.object(builder.subprocess, 'Popen', return_value=process), \
             patch.object(builder.os, 'killpg') as kill, \
             patch.object(builder.Commands, 'group_exists', return_value=False), self.assertRaises(KeyboardInterrupt):
            builder.Commands(self.root)(['not-executed'], timeout=1)
        kill.assert_called_once_with(1234, signal.SIGTERM)

    def test_builder_cancellation_at_create_copy_or_cleanup_removes_owned_helper_and_fails(self):
        for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
            for stage in ('create', 'cp', 'cleanup'):
                if sig == signal.SIGALRM and stage == 'cleanup':
                    continue  # In cleanup SIGALRM is the hard grace deadline.
                with self.subTest(sig=sig, stage=stage):
                    self.output = self.root / f'interrupt-{sig}-{stage}'
                    commands = FakeCommands(self.checkout)
                    fired = False
                    def interrupting(args, **kwargs):
                        nonlocal fired
                        result = commands(args, **kwargs)
                        trigger = (args[:2] == ['docker', stage] if stage != 'cleanup'
                                   else args[:3] == ['docker', 'container', 'ls'])
                        if trigger and not fired:
                            fired = True
                            signal.getsignal(sig)(sig, None)
                        return result
                    report = builder.build(self.checkout, REVISION, self.output,
                                           commands_factory=lambda work: interrupting)
                    self.assertTrue(fired)
                    self.assertFalse(report['success'])
                    self.assertTrue(commands.removed)
                    self.assertTrue(report['cleanup_verified'])
                    self.assertTrue(report['private_directory_removed'])
                    self.assertTrue(report['execution']['interrupted'])

    def test_descendant_cleanup_escalates_only_after_term_and_requires_absence(self):
        for stuck in (False, True):
            with self.subTest(stuck=stuck):
                process = unittest.mock.Mock(pid=4321, returncode=0)
                alive = True
                def killpg(pid, sig):
                    nonlocal alive
                    if sig == signal.SIGKILL and not stuck:
                        alive = False
                    if sig == 0 and not alive:
                        raise ProcessLookupError()
                commands = builder.Commands(self.root)
                with patch.object(builder.subprocess, 'Popen', return_value=process), \
                     patch.object(builder.os, 'killpg', side_effect=killpg) as kill, \
                     patch.object(builder.time, 'monotonic', side_effect=range(0, 1000, 11)), \
                     self.assertRaisesRegex(RuntimeError, 'build.command-' + ('cleanup-unverified' if stuck else 'descendants')):
                    commands(['not-executed'])
                calls = [call.args[1] for call in kill.call_args_list]
                self.assertLess(calls.index(signal.SIGTERM), calls.index(signal.SIGKILL))
                self.assertEqual(calls[-1], 0)
                self.assertEqual(commands.cleanup_verified, not stuck)

    def test_all_provenance_git_calls_disable_replacement_objects(self):
        report, commands = self.run_build()
        self.assertTrue(report['success'])
        calls = [args for args, _ in commands.calls if args[0] == 'git']
        self.assertTrue(calls)
        self.assertTrue(all('--no-replace-objects' in args for args in calls))
        self.assertGreaterEqual(sum('rev-parse' in args for args in calls), 3)

    def test_isolated_replacement_commit_cannot_bind_replacement_tree_to_original_revision(self):
        # Independently authored temporary repository storage. No git write commands,
        # config changes, or changes to the real repository's refs/objects/index.
        checkout = self.root / 'replacement-fixture'
        metadata = checkout / '.git'
        (metadata / 'objects').mkdir(parents=True)
        (metadata / 'refs/heads').mkdir(parents=True)
        (metadata / 'refs/replace').mkdir(parents=True)
        def obj(kind, content):
            raw = kind.encode() + b' ' + str(len(content)).encode() + b'\0' + content
            oid = hashlib.sha1(raw).hexdigest()
            path = metadata / 'objects' / oid[:2] / oid[2:]
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(zlib.compress(raw))
            return oid
        def tree(files):
            rows = []
            for name in sorted({key.split('/')[0] for key in files}):
                if name in files:
                    mode, oid = b'100644', obj('blob', files[name])
                else:
                    mode = b'40000'
                    oid = tree({key[len(name)+1:]: value for key, value in files.items()
                                if key.startswith(name + '/')})
                rows.append(mode + b' ' + name.encode() + b'\0' + bytes.fromhex(oid))
            return obj('tree', b''.join(rows))
        def commit(files):
            return obj('commit', ('tree ' + tree(files) + '\nauthor Fixture <fixture@example.invalid> 0 +0000\n'
                       'committer Fixture <fixture@example.invalid> 0 +0000\n\nfixture\n').encode())
        original = commit({**INPUTS, 'BE_GO/go.sum': b'original revision sums\n'})
        replacement = commit(INPUTS)
        (metadata / 'HEAD').write_text('ref: refs/heads/fixture\n')
        (metadata / 'refs/heads/fixture').write_text(original + '\n')
        (metadata / 'refs/replace' / original).write_text(replacement + '\n')
        for name, content in INPUTS.items():
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        # An authored v2 index matches the replacement checkout. This allows
        # real Git status in both the vulnerable and protected paths, no mock.
        index_rows = []
        for name, content in sorted(INPUTS.items()):
            stat = (checkout / name).stat()
            fields = (stat.st_ctime_ns // 10**9, stat.st_ctime_ns % 10**9,
                      stat.st_mtime_ns // 10**9, stat.st_mtime_ns % 10**9,
                      stat.st_dev, stat.st_ino, 0o100644, stat.st_uid, stat.st_gid, len(content))
            raw_name = name.encode()
            row = struct.pack('>10I', *(value & 0xffffffff for value in fields))
            row += bytes.fromhex(obj('blob', content)) + struct.pack('>H', len(raw_name)) + raw_name + b'\0'
            index_rows.append(row + bytes(-len(row) % 8))
        index = b'DIRC' + struct.pack('>II', 2, len(index_rows)) + b''.join(index_rows)
        (metadata / 'index').write_bytes(index + hashlib.sha1(index).digest())
        commands = builder.Commands(self.root)
        # Prove the attack preconditions with read-only Git, not command mocks.
        original_blob = commands(['git', '--no-replace-objects', '-C', str(checkout),
                                  'show', original + ':BE_GO/go.sum'])
        replaced_blob = commands(['git', '-C', str(checkout), 'show', original + ':BE_GO/go.sum'])
        self.assertEqual(original_blob, b'original revision sums\n')
        self.assertEqual(replaced_blob, INPUTS['BE_GO/go.sum'])
        self.assertEqual(commands(['git', '--no-optional-locks', '-C', str(checkout),
                                   'status', '--porcelain=v1', '--untracked-files=no']), b'')
        with self.assertRaisesRegex(RuntimeError, '^build.checkout-dirty$'):
            builder.freeze_context(commands, checkout, original,
                                   self.root / 'replacement-export', self.root)

    def test_root_owned_owner_only_server_is_not_executable_by_image_user(self):
        report, _ = self.run_build('owner-only')
        self.assertFalse(report['success'])

    def test_packaged_permissions_apply_to_runtime_uid_and_tar_is_not_extracted(self):
        for uid, gid, mode, accepted in ((0, 0, 0o755, True), (0, 0, 0o700, False),
                (10001, 0, 0o700, True), (0, 10001, 0o710, True),
                (10001, 0, 0o007, False), (0, 10001, 0o701, False), (0, 0, 0o644, False)):
            with self.subTest(uid=uid, gid=gid, mode=oct(mode)):
                archive = self.root / 'packaged.tar'
                with tarfile.open(archive, 'w') as tar:
                    info = tarfile.TarInfo('server')
                    info.uid, info.gid, info.mode, info.size = uid, gid, mode, len(ELF)
                    tar.addfile(info, io.BytesIO(ELF))
                def cp(args, **kwargs):
                    self.assertEqual(args, ['docker', 'cp', CONTAINER + ':/app/server', '-'])
                    Path(kwargs['target']).write_bytes(archive.read_bytes())
                path = self.root / 'server'
                if path.exists():
                    path.unlink()
                if accepted:
                    metadata = builder.copy_server(cp, CONTAINER, self.root)
                    self.assertEqual(metadata, {'server_mode': mode, 'server_uid': uid, 'server_gid': gid})
                    self.assertEqual(path.read_bytes(), ELF)
                    self.assertEqual(path.stat().st_mode & 0o777, mode)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'build.server-permissions'):
                        builder.copy_server(cp, CONTAINER, self.root)
                    self.assertFalse(path.exists())
        for name, kind in (('../escape', tarfile.REGTYPE), ('server', tarfile.SYMTYPE)):
            with self.subTest(name=name, kind=kind):
                with tarfile.open(archive, 'w') as tar:
                    info = tarfile.TarInfo(name)
                    info.type, info.linkname = kind, '/outside'
                    tar.addfile(info)
                with self.assertRaisesRegex(RuntimeError, 'build.server-archive'):
                    builder.copy_server(cp, CONTAINER, self.root)
                self.assertFalse((self.root.parent / 'escape').exists())

    def test_binary_rejects_nonexecutable_old_fixture_and_structural_corruption(self):
        path = self.root / 'elf-server'
        old = bytearray(110)
        old[:7] = b'\x7fELF\x02\x01\x01'
        struct.pack_into('<HHI', old, 16, 2, 62, 1)
        struct.pack_into('<H', old, 52, 64)
        cases = {'old-header-only': old, 'truncated-table': ELF[:100]}
        def changed(name, offset, fmt, value):
            data = bytearray(ELF)
            struct.pack_into(fmt, data, offset, value)
            cases[name] = data
        changed('zero-program-count', 56, '<H', 0)
        changed('table-outside-file', 32, '<Q', 10000)
        changed('short-program-entry', 54, '<H', 48)
        changed('load-filesz-past-eof', 64 + 32, '<Q', 10000)
        changed('load-filesz-over-memsz', 64 + 40, '<Q', 100)
        changed('virtual-range-overflow', 64 + 16, '<Q', 2**64 - 128)
        changed('entry-in-data', 24, '<Q', 0x401200)
        changed('entry-in-zero-fill', 24, '<Q', 0x401228)
        changed('entry-outside-load', 24, '<Q', 0x700000)
        changed('no-executable-load', 64 + 4, '<I', 4)
        changed('no-load-segments', 64, '<I', 4)
        # Second segment cannot cover the entry either.
        changed('misaligned-load', 64 + 48, '<Q', 3)
        changed('incongruent-load', 64 + 16, '<Q', 0x400001)
        changed('oversized-program-table', 56, '<H', 0xffff)
        for name, data in cases.items():
            with self.subTest(name=name):
                path.write_bytes(data)
                path.chmod(0o755)
                with self.assertRaises(RuntimeError):
                    builder.binary_digest(path, 'amd64')
        path.write_bytes(ELF)
        path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, '^build.server-file$'):
            builder.binary_digest(path, 'amd64')

    def test_binary_accepts_structural_amd64_arm64_pie_and_header_variants(self):
        path = self.root / 'elf-server'
        for machine, architecture in ((62, 'amd64'), (183, 'arm64')):
            for options in ({}, {'kind': 3}, {'phoff': 80, 'note': True}, {'osabi': 3}):
                with self.subTest(machine=machine, options=options):
                    data = elf_fixture(machine=machine, **options)
                    path.write_bytes(data)
                    path.chmod(0o755)
                    self.assertEqual(builder.binary_digest(path, architecture), hashlib.sha256(data).hexdigest())

    def test_real_read_only_git_export_covers_every_tracked_context_input(self):
        root = Path(__file__).resolve().parents[3]
        revision = subprocess.check_output(['git', '--no-replace-objects', '--no-optional-locks', '-C', str(root),
                                            'rev-parse', 'HEAD'], text=True, timeout=30).strip()
        names = subprocess.check_output(['git', '--no-replace-objects', '--no-optional-locks', '-C', str(root),
                                          'ls-files', '-z', '--', 'BE_GO'], timeout=30).split(b'\0')
        expected = {name.decode(): hashlib.sha256((root / name.decode()).read_bytes()).hexdigest()
                    for name in names if name}
        source = self.root.resolve() / 'export'
        commands = builder.Commands(self.root)
        def only_git(args, **kwargs):
            self.assertEqual(args[0], 'git')
            self.assertIn('--no-optional-locks', args)
            self.assertIn('--no-replace-objects', args)
            return commands(args, **kwargs)
        manifest, entries = builder.freeze_context(only_git, root, revision, source, self.root)
        self.assertEqual(manifest, expected)
        self.assertEqual({name for name, _, _ in entries}, set(expected))
        self.assertEqual({path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file()},
                         set(expected))

    def test_binary_rejects_empty_header_only_and_foreign_architecture(self):
        path = self.root / 'server'
        for data in (b'', ELF[:64], ELF.replace(b'\x3e\x00', b'\xb7\x00', 1)):
            with self.subTest(data=data[:20]), self.assertRaises(RuntimeError):
                path.write_bytes(data)
                path.chmod(0o755)
                builder.binary_digest(path, 'amd64')


class HostedWorkflowControls(unittest.TestCase):
    def test_separate_required_packaged_gate_uses_checkout_head_and_receipts_only(self):
        text = (Path(__file__).resolve().parents[3] / '.github/workflows/go-message-reads.yml').read_text()
        self.assertIn('  message-image:\n', text)
        native, image = text.split('  message-image:\n', 1)
        for gate in ('Compare native and versioned messages, query budgets and concurrent reads',
                     'Verify real repository plans, hydration and blocked cancellation',
                     'Verify unchanged saved-message browser workflow on v1 and v2',
                     'Verify authorization and warm-session invalidation on native and versioned backends'):
            self.assertIn(gate, native)
        self.assertIn('timeout-minutes: 95', native)
        self.assertIn('timeout-minutes: 90', image)
        self.assertIn('persist-credentials: false', image)
        self.assertIn('contents: read', text)
        self.assertNotIn('continue-on-error:', image)
        self.assertNotIn('pull_request.head.sha', image)
        self.assertIn('git --no-replace-objects rev-parse HEAD', image)
        self.assertIn('-m TESTS.adapters.go.message_image_build', image)
        self.assertIn('TESTS.adapters.go.test_message_image_build', image)
        self.assertIn('TESTS.adapters.go.test_message_read_scopes_image', image)
        self.assertIn("'--build-receipt'", image)
        self.assertIn("'--go-source'", image)
        self.assertIn("'--image', build['image_id']", image)
        self.assertIn("build.get('source_revision') != revision", image)
        self.assertNotIn('timeout=1500', image)
        self.assertIn('run_packaged(', image)
        self.assertIn('deadline=1500', image)
        self.assertIn('--no-replace-objects', image)
        self.assertIn('postgres:16-alpine', image)
        self.assertIn('redis:7-alpine', image)
        self.assertIn("r'^FROM (caddy:", image)
        paths = [line.strip()[6:] for line in image.splitlines() if line.strip().startswith('path: ')]
        self.assertEqual(paths, ['quality-reports/go-migration/message-image-build/results.json',
                                 'quality-reports/go-migration/message-read-scopes-image/results.json'])

    def test_mutation_browser_gate_reuses_inputs_and_cannot_be_hidden_by_failed_reads(self):
        import shlex

        text = (Path(__file__).resolve().parents[3] / '.github/workflows/go-message-reads.yml').read_text()
        native, _ = text.split('  message-image:\n', 1)
        steps = native.split('      - ')
        gates = [step for step in steps if 'id: message-mutations-browser\n' in step]
        self.assertEqual(len(gates), 1, 'Require exactly one actual mutation browser gate')
        gate, = gates
        condition = next(line.strip() for line in gate.splitlines() if line.strip().startswith('if:'))
        self.assertEqual(condition, "if: ${{ !cancelled() && steps.message-controls.outcome == 'success' && steps.message-browser-build.outcome == 'success' }}")
        self.assertIn('timeout-minutes: 30\n', gate)
        self.assertNotIn('continue-on-error:', native)
        self.assertNotIn('concurrency:', text)
        command = gate.split('run: |\n', 1)[1].replace('\\\n', ' ')
        build = '$GITHUB_WORKSPACE/quality-reports/go-migration/message-browser-build'
        self.assertEqual(shlex.split(command), [
            'python', '-m', 'TESTS.adapters.django.message_mutations_browser_acceptance',
            '--ui-dir', build + '/artifact',
            '--build-observation', build + '/build-observation.json',
            '--playwright-module', '$GITHUB_WORKSPACE/selfhost/tests/browser/node_modules/@playwright/test',
            '--go-binary', build + '/server', '--image-codec', build + '/image-codec',
            '--go-build-record', build + '/go-build-record.json',
            '--output', '$GITHUB_WORKSPACE/quality-reports/go-migration/message-mutations-browser',
            '--timeout', '360', '--deadline', '1500', '--cleanup-grace', '180',
        ])
        controls, = [step for step in steps if 'id: message-controls\n' in step]
        self.assertIn('          python -m unittest -v TESTS.test_message_mutations_browser_acceptance\n', controls)
        self.assertLess(native.index('id: message-controls\n'), native.index('id: message-browser-build\n'))
        self.assertLess(native.index('id: message-browser-build\n'), native.index('id: message-mutations-browser\n'))
        self.assertLess(native.index('Verify unchanged saved-message browser workflow'), native.index('id: message-mutations-browser\n'))
        uploads = [step for step in steps if step.startswith('uses: actions/upload-artifact@')]
        paths = [line.strip()[6:] for step in uploads for line in step.splitlines()
                 if line.strip().startswith('path: ')]
        self.assertEqual(paths, [
            'quality-reports/go-migration/message-reads-http/results.json',
            'quality-reports/go-migration/message-reads-repository/results.json',
            'quality-reports/go-migration/message-reads-browser/results.json',
            'quality-reports/go-migration/message-mutations-browser/results.json',
            'quality-reports/go-migration/message-read-scopes/results.json',
        ])
        upload, = [step for step in uploads if 'name: go-message-mutations-browser\n' in step]
        self.assertIn('if: always()\n', upload)
        self.assertIn('timeout-minutes: 3\n', upload)
        self.assertIn('if-no-files-found: ignore\n', upload)


if __name__ == '__main__':
    unittest.main()
