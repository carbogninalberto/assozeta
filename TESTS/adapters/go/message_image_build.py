"""Parent-only bounded exact-HEAD Go image build; no application acceptance.

Import is inert. Docker runs only through main()/build() in a provisioned parent
process. Public results contain provenance and fixed errors; diagnostics and
copied executable bytes stay in a private temporary directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import struct
import subprocess
import sys
import tempfile
import tarfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.message_read_scopes_image import go_source_hashes
from TESTS.adapters.go.message_image_execution import ExecutionDeadline, defer_interruption, fail_receipt

LABEL = 'org.assozeta.message-image-build'
REVISION = re.compile(r'[0-9a-f]{40}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
CONTAINER = re.compile(r'[0-9a-f]{64}\Z')
MAX_METADATA = 4 * 1024 * 1024
BUILD_TIMEOUT = 1800
EXECUTION_TIMEOUT = 2100
COMMAND_GRACE = 10


def require_assertions():
    if sys.flags.optimize:
        raise RuntimeError('build.optimized-python')


def need(condition, code):
    if not condition:
        raise RuntimeError(code)


class Commands:
    """Bound subprocess groups; keep stdout/stderr out of public CI logs."""
    def __init__(self, work):
        self.work = Path(work)
        self.log = self.work / 'commands.log'
        self.cleanup_verified = True

    @staticmethod
    def group_exists(pid):
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def stop_group(self, process):
        with defer_interruption():
            try:
                self._stop_group(process)
            except BaseException:
                self.cleanup_verified = False
                raise

    def _stop_group(self, process):
        """TERM the owned session first; KILL only after a bounded grace.

        Reaping the leader alone does not prove its descendants stopped. Group
        absence is checked independently; inability to prove it is a failure.
        """
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        until = time.monotonic() + COMMAND_GRACE
        try:
            process.communicate(timeout=COMMAND_GRACE)
        except subprocess.TimeoutExpired:
            pass
        while self.group_exists(process.pid) and time.monotonic() < until:
            time.sleep(0.05)
        if self.group_exists(process.pid):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=COMMAND_GRACE)
        until = time.monotonic() + COMMAND_GRACE
        while self.group_exists(process.pid) and time.monotonic() < until:
            time.sleep(0.05)
        need(not self.group_exists(process.pid), 'build.command-cleanup-unverified')

    def __call__(self, args, *, timeout=60, data=None, target=None, capture=True):
        need(0 < timeout <= BUILD_TIMEOUT, 'build.command-deadline')
        destination = Path(target) if target is not None else self.work / 'command-output'
        # Keep arguments, diagnostics and captured bytes outside public CI logs.
        with destination.open('wb') as output, self.log.open('ab') as log:
            process = None
            try:
                try:
                    # Deliver a signal only after the owned Popen is assigned;
                    # unlike a blocked signal mask, children keep normal signals.
                    with defer_interruption():
                        process = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                            stdout=output if capture or target is not None else log,
                            stderr=log, start_new_session=True)
                    process.communicate(input=data, timeout=timeout)
                    need(not self.group_exists(process.pid), 'build.command-descendants')
                    need(process.returncode == 0, 'build.command-failed')
                except BaseException as error:
                    if process is not None:
                        self.stop_group(process)
                    if isinstance(error, subprocess.TimeoutExpired):
                        raise RuntimeError('build.command-timeout') from None
                    raise
            except OSError:
                raise RuntimeError('build.command-unavailable') from None
        if target is not None or not capture:
            return b''
        need(destination.stat().st_size <= MAX_METADATA, 'build.command-output-limit')
        return destination.read_bytes()


def git(commands, checkout, *args, **kwargs):
    return commands(['git', '--no-replace-objects', '--no-optional-locks', '-C', str(checkout), *args], **kwargs)


def clean_head(commands, checkout, revision):
    need(git(commands, checkout, 'rev-parse', '--verify', 'HEAD').decode().strip() == revision,
         'build.checkout-revision')
    need(not git(commands, checkout, 'status', '--porcelain=v1', '-z',
                 '--untracked-files=no', '--', 'BE_GO'), 'build.checkout-dirty')


def tracked_entries(commands, checkout, revision):
    raw = git(commands, checkout, 'ls-tree', '-r', '-z', '--full-tree', revision, '--', 'BE_GO')
    entries = []
    for row in raw.split(b'\0'):
        if not row:
            continue
        metadata, name = row.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split(' ')
        name = name.decode('utf-8')
        path = PurePosixPath(name)
        need(kind == 'blob' and mode in ('100644', '100755')
             and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', oid) and name == path.as_posix()
             and path.parts[0] == 'BE_GO' and len(path.parts) > 1
             and not any(part in ('.', '..', '.git', '.DS_Store') for part in path.parts),
             'build.context-entry')
        entries.append((name, mode, oid))
    need(entries and len({name for name, _, _ in entries}) == len(entries), 'build.context-empty')
    return entries


def regular_file(root, name):
    path = root / name
    need(not any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent)
         and path.is_file(), 'build.context-file')
    return path


def context_modes(source, entries):
    return {name: regular_file(source, name).stat().st_mode & 0o777 for name, _, _ in entries}


def checkout_matches(checkout, manifest, entries):
    for name, mode, _ in entries:
        path = regular_file(checkout, name)
        need(hashlib.sha256(path.read_bytes()).hexdigest() == manifest[name], 'build.checkout-content')
        need(bool(path.stat().st_mode & 0o111) == (mode == '100755'), 'build.checkout-mode')


def freeze_context(commands, checkout, revision, source, work):
    """Export every tracked blob without archive export-ignore/subst semantics."""
    clean_head(commands, checkout, revision)
    entries = tracked_entries(commands, checkout, revision)
    payload = work / 'git-blobs'
    git(commands, checkout, 'cat-file', '--batch',
        data=b''.join(oid.encode() + b'\n' for _, _, oid in entries), target=payload)
    with payload.open('rb') as stream:
        for name, mode, oid in entries:
            header = stream.readline(256).decode('ascii').strip().split(' ')
            need(len(header) == 3 and header[:2] == [oid, 'blob'] and header[2].isdigit(),
                 'build.context-blob')
            size = int(header[2])
            content = stream.read(size)
            need(len(content) == size and stream.read(1) == b'\n', 'build.context-truncated')
            # Git object identity and exported bytes must agree, independently of
            # the checkout's stat cache (assume-unchanged/skip-worktree included).
            algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
            need(algorithm(b'blob ' + str(size).encode() + b'\0' + content).hexdigest() == oid,
                 'build.context-object')
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(int(mode[-3:], 8))
        need(not stream.read(1), 'build.context-trailing')
    manifest = go_source_hashes(source)
    need(set(manifest) == {name for name, _, _ in entries}, 'build.context-exact')
    checkout_matches(checkout, manifest, entries)
    clean_head(commands, checkout, revision)
    return manifest, entries


def inspect_one(commands, *args):
    value = json.loads(commands(['docker', *args]))
    need(type(value) is list and len(value) == 1 and type(value[0]) is dict, 'build.inspect-shape')
    return value[0]


def owned_container(commands, name, image, owner, identifier=None):
    value = inspect_one(commands, 'container', 'inspect', identifier or name)
    need(CONTAINER.fullmatch(value['Id']) and (identifier is None or value['Id'] == identifier)
         and value['Name'] == '/' + name and value['Image'] == image
         and value['Config']['Labels'].get(LABEL) == owner
         and value['State']['Running'] is False, 'build.container-ownership')
    return value['Id']


def cleanup_container(commands, name, image, owner, identifier):
    # A successful list operation distinguishes absence from inspect permission
    # errors. After uncertain create, recover only the unique owned name.
    def listed():
        return commands(['docker', 'container', 'ls', '--all', '--no-trunc',
                         '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}']).decode().splitlines()
    found = listed()
    if not found:
        need(identifier is None, 'build.container-disappeared')
        return
    need(len(found) == 1 and CONTAINER.fullmatch(found[0]), 'build.cleanup-list')
    need(identifier is None or identifier == found[0], 'build.cleanup-replacement')
    identifier = owned_container(commands, name, image, owner, found[0])
    commands(['docker', 'rm', identifier])
    need(not listed(), 'build.cleanup-present')


def copy_server(commands, identifier, work):
    """Read Docker's file tar without extraction; preserve original UID/GID/mode.

    A normal docker cp to a local path rewrites ownership to the caller. Inspect
    the archive metadata to check execution by image UID/GID 10001 instead.
    """
    archive, path = work / 'server.tar', work / 'server'
    commands(['docker', 'cp', identifier + ':/app/server', '-'], target=archive)
    with tarfile.open(archive, 'r:') as tar:
        info = tar.next()
        need(info is not None and info.name == 'server' and info.isreg()
             and info.size >= 64 and info.offset_data + info.size <= archive.stat().st_size,
             'build.server-archive')
        execute_bit = 0o100 if info.uid == 10001 else 0o010 if info.gid == 10001 else 0o001
        need(info.mode & execute_bit, 'build.server-permissions')
        with tar.extractfile(info) as content, path.open('xb') as output:
            remaining = info.size
            while remaining:
                chunk = content.read(min(remaining, 1024 * 1024))
                need(chunk, 'build.server-archive')
                output.write(chunk)
                remaining -= len(chunk)
        need(tar.next() is None, 'build.server-archive')
        path.chmod(info.mode & 0o777)
        return {'server_mode': info.mode & 0o777, 'server_uid': info.uid, 'server_gid': info.gid}


def binary_digest(path, architecture):
    """Validate ELF64 load structure without executing copied package bytes."""
    need(path.is_file() and not path.is_symlink() and path.stat().st_size >= 64
         and path.stat().st_mode & 0o111, 'build.server-file')
    size = path.stat().st_size
    with path.open('rb') as stream:
        header = stream.read(64)
        need(len(header) == 64 and header[:7] == b'\x7fELF\x02\x01\x01'
             and struct.unpack_from('<H', header, 16)[0] in (2, 3)
             and struct.unpack_from('<H', header, 18)[0] == {'amd64': 62, 'arm64': 183}[architecture]
             and struct.unpack_from('<I', header, 20)[0] == 1
             and struct.unpack_from('<H', header, 52)[0] == 64, 'build.server-elf')
        entry, phoff = struct.unpack_from('<QQ', header, 24)
        phsize, phnum = struct.unpack_from('<HH', header, 54)
        # Linux ELF loaders bound the program-header table to 64 KiB and use
        # native Elf64_Phdr entries. No fixed Go phoff/count/section layout,
        # virtual base, OSABI or page-alignment value is assumed.
        need(phsize == 56 and phnum > 0 and phsize * phnum <= 65536
             and phoff >= 64 and phoff + phsize * phnum <= size, 'build.server-programs')
        executable_entry = False
        for index in range(phnum):
            stream.seek(phoff + index * phsize)
            kind, flags, offset, address, _, filesz, memsz, alignment = struct.unpack('<IIQQQQQQ', stream.read(56))
            if kind == 0:  # PT_NULL is ignored by ELF loaders.
                continue
            need(offset <= size and filesz <= size - offset, 'build.server-segment-file')
            if kind != 1:  # Only PT_LOAD contributes mapped entry-point bytes.
                continue
            need(filesz <= memsz and address + memsz <= 2**64
                 and (alignment in (0, 1) or (alignment & (alignment - 1) == 0
                      and address % alignment == offset % alignment)), 'build.server-load')
            if flags & 1 and address <= entry < address + filesz:
                executable_entry = True
        need(executable_entry, 'build.server-entry')
        stream.seek(0)
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()


def build(checkout, expected_revision, output, *, commands_factory=None,
          deadline=EXECUTION_TIMEOUT, cleanup_grace=180):
    require_assertions()
    require_production_python()
    if not isinstance(expected_revision, str) or not REVISION.fullmatch(expected_revision):
        raise ValueError('build.expected-revision')
    checkout, output = Path(checkout).resolve(), Path(output).resolve()
    # Output within the context could itself alter the next build's inputs.
    need(not output.is_relative_to(checkout / 'BE_GO'), 'build.output-context')
    control = ExecutionDeadline(deadline, cleanup_grace,
        on_escalation=lambda state: fail_receipt(output / 'results.json', state,
                                               code='execution.cleanup-grace-expired'))
    output.mkdir(parents=True, exist_ok=False)
    source, receipt = output / 'source', output / 'results.json'
    report = {'success': False, 'source_stable': False, 'source_revision': expected_revision,
        'tracked_context_exact': False, 'cleanup_verified': False,
        'private_directory_removed': False, 'errors': [], 'cleanup_errors': [],
        'scope': 'bounded exact-HEAD image build; no installed activation or release acceptance'}
    receipt.write_text(json.dumps(report, indent=2) + '\n')
    work, commands, entries, modes = None, None, None, None
    name = 'az-message-build-' + uuid4().hex
    owner = uuid4().hex
    identifier, attempted = None, False
    with control:
        try:
            with tempfile.TemporaryDirectory(prefix='az-message-build-') as directory:
                work = Path(directory)
                try:
                    commands = (commands_factory or Commands)(work)
                    manifest, entries = freeze_context(commands, checkout, expected_revision, source, work)
                    report['source_sha256'] = manifest
                    report['tracked_context_exact'] = True
                    modes = context_modes(source, entries)
                    iid = work / 'image-id'
                    commands(['docker', 'build', '--build-arg', 'CODE_REVISION=' + expected_revision,
                              '--iidfile', str(iid), str(source/'BE_GO')],
                             timeout=BUILD_TIMEOUT, capture=False)
                    image = iid.read_text().strip()
                    need(IMAGE.fullmatch(image), 'build.image-id')
                    report['image_id'] = image
                    metadata = inspect_one(commands, 'image', 'inspect', image)
                    config = metadata['Config']
                    need(metadata['Id'] == image and metadata['Os'] == 'linux'
                         and metadata['Architecture'] in ('amd64', 'arm64')
                         and config['User'] == '10001:10001'
                         and config['Entrypoint'] == ['/app/server'] and not config.get('Volumes'),
                         'build.image-metadata')
                    attempted = True  # Before create, including failure/timeout after daemon creation.
                    created = commands(['docker', 'create', '--pull', 'never', '--network', 'none',
                        '--name', name, '--label', LABEL + '=' + owner, image]).decode().strip()
                    need(CONTAINER.fullmatch(created), 'build.container-id')
                    identifier = created
                    owned_container(commands, name, image, owner, identifier)
                    commands(['docker', 'cp', identifier + ':/app/CODE_REVISION', str(work/'CODE_REVISION')])
                    report.update(copy_server(commands, identifier, work))
                    packaged_revision = (work/'CODE_REVISION').read_bytes()
                    need(packaged_revision == (expected_revision + '\n').encode(), 'build.packaged-revision')
                    report['packaged_revision'] = expected_revision
                    report['server_sha256'] = binary_digest(work/'server', metadata['Architecture'])
                finally:
                    control.begin_cleanup()
                    if attempted:
                        try:
                            cleanup_container(commands, name, report['image_id'], owner, identifier)
                            report['cleanup_verified'] = True
                        except BaseException:
                            report['cleanup_errors'].append('build.helper-cleanup-failed')
                    else:
                        report['cleanup_verified'] = True  # No helper creation was attempted.
                    if entries is not None:
                        try:
                            clean_head(commands, checkout, expected_revision)
                            checkout_matches(checkout, report['source_sha256'], entries)
                            need(go_source_hashes(source) == report['source_sha256']
                                 and context_modes(source, entries) == modes, 'build.source-drift')
                            report['source_stable'] = True
                        except BaseException:
                            report['errors'].append('build.source-stability-failed')
        except BaseException:
            report['errors'].append('build.execution-failed')
        finally:
            if commands is not None and getattr(commands, 'cleanup_verified', True) is not True:
                report['cleanup_verified'] = False
                report['cleanup_errors'].append('build.command-cleanup-unverified')
            report['private_directory_removed'] = bool(work is not None and not work.exists())
            if control.interrupted:
                report['errors'].append('build.interrupted')
                report['execution'] = {'interrupted': True, 'reason': control.reason, 'escalated': False}
            report['success'] = (report['tracked_context_exact'] is True
                and report['source_stable'] is True and report['cleanup_verified'] is True
                and report['private_directory_removed'] is True and not report['errors']
                and not report['cleanup_errors'] and report.get('packaged_revision') == expected_revision
                and bool(IMAGE.fullmatch(report.get('image_id', '')))
                and bool(re.fullmatch(r'[0-9a-f]{64}', report.get('server_sha256', ''))))
            receipt.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    if control.interrupted and 'execution' not in report:
        fail_receipt(receipt, control, code='build.interrupted')
        report = json.loads(receipt.read_bytes())
    return report


def main(argv=None):
    require_assertions()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deadline', type=float, default=EXECUTION_TIMEOUT)
    parser.add_argument('--cleanup-grace', type=float, default=180)
    args = parser.parse_args(argv)
    report = build(ROOT, args.expected_revision, args.output,
                   deadline=args.deadline, cleanup_grace=args.cleanup_grace)
    print(json.dumps({'success': report['success'], 'check': 'message-image.build'}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
