"""Parent-only bounded saved-message acceptance against an independently built image.

Import is inert. Never builds Go, installs dependencies, or substitutes a native
binary. Public receipts contain fixed errors and digests; raw evidence is private.
"""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django import message_mutations as adapter
from TESTS.adapters.django import message_mutations_runtime as runtime
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_session_lifecycle as lifecycle
from TESTS.adapters.go import message_read_scopes_image as packaged
from TESTS.adapters.go import message_image_build as builder
from TESTS.adapters.go import message_restore_image as restore_image
from TESTS.adapters.go.message_image_process import executing_server_guard
from TESTS.adapters.go.message_image_execution import (
    ExecutionDeadline, begin_cleanup, defer_interruption, fail_receipt,
)
# Published 703c helpers only; its CLI and native workload gate are never called.
from TESTS.adapters.django.message_mutations_acceptance import (
    PG_BOUNDS, configure_database_bounds, private_diagnostics, _owned, check_caddy_image,
    verify_observations, AcceptanceError,
)
from TESTS.contracts import test_message_mutations as contract

need = contract.require
HASH = re.compile(r'[0-9a-f]{64}\Z')
REVISION = re.compile(r'[0-9a-f]{40}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
ENDPOINTS = (('django-native', 'django'), ('go-packaged', 'go'),
             ('caddy-v1', 'django'), ('caddy-v2', 'go'))
LABELS = tuple(label for label, _ in ENDPOINTS)
IP_METHODS = dict(zip(LABELS, ('connected-host-socket', 'owned-container-tcp-peer',
                             'owned-caddy-health-peer', 'owned-caddy-health-peer'), strict=True))
CASE_CHECKS = ('response', 'state', 'audit', 'cache', 'attribution')
FIXTURE_CLEANUP = ('rows', 'cache', 'sequences', 'private_fixture')
SERVICES = packaged.SERVICES
RESOURCES = packaged.RESOURCES
PRIVATE_CHECKS = packaged.PRIVATE_CHECKS
SCHEMA = {'applied_migrations': 547, 'remaining_plan': 0, 'mode': 'full-migration-history'}
EXPECTED_CASES = 119
BUILD_KEYS = ('success', 'source_stable', 'tracked_context_exact', 'cleanup_verified',
              'private_directory_removed', 'source_revision', 'packaged_revision',
              'image_id', 'source_sha256', 'server_sha256', 'errors', 'cleanup_errors')

# Constant image-side protocol. Only the shell holds FD 9 while reading stdin;
# no background child can inherit it. Alpine ash's timed builtin also bounds a
# stranded exec if Docker fails to forward EOF after the client disappears.
LINUX_LOCK_SCRIPT = r'''set -eu
exec 9<> /operations/lifecycle.lock
/usr/bin/flock -x -n 9
printf 'MUTATION_LOCK_READY\n'
IFS= read -r -t 60 release || exit 77
[ "$release" = MUTATION_LOCK_RELEASE ] || exit 78
/usr/bin/flock -u 9
exec 9>&-
printf 'MUTATION_LOCK_RELEASED\n'
'''
LINUX_LOCK_PROBE = r'''set -eu
exec 9<> /operations/lifecycle.lock
/usr/bin/flock -x -n 9
/usr/bin/flock -u 9
exec 9>&-
printf 'MUTATION_LOCK_REACQUIRED\n'
'''
LOCK_READY = b'MUTATION_LOCK_READY\n'
LOCK_RELEASED = b'MUTATION_LOCK_RELEASED\n'
LOCK_START_TIMEOUT = 5
LOCK_EXIT_TIMEOUT = 5


def require_assertions():
    packaged.require_assertions()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hashed(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def source_hashes():
    """Bind the entire published oracle graph, application data, routes and recipe."""
    result = runtime.source_hashes()
    paths = {path for path in (ROOT / 'BE').rglob('*') if path.is_file()
             and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    paths.update(ROOT / name for name in (
        'TESTS/adapters/go/message_mutations_image.py', 'TESTS/test_message_mutations_image.py',
        'docs/architecture/go-migration/message_mutations_image.md',
        'TESTS/adapters/go/message_restore_image.py', 'TESTS/test_message_restore_image.py',
        'docs/architecture/go-migration/message_restore_image.md',
        'TESTS/adapters/go/message_mutation_restore.py', 'TESTS/test_message_mutation_restore.py',
        'docs/architecture/go-migration/message_mutation_restore.md',
        'TESTS/adapters/go/message_restore_lock_probe.py', 'TESTS/test_message_restore_lock_probe.py',
        'docs/architecture/go-migration/message_restore_lock_probe.md',
        'UI/Dockerfile', 'selfhost/caddy/Caddyfile'))
    result.update({path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(paths)})
    result.update(packaged.go_source_hashes(ROOT))
    return result


def verify_build_receipt(build, source, image_id, revision):
    """Do not trust the builder's top-level success flag alone."""
    require_assertions()
    need(type(build) is dict and all(build.get(key) is True for key in (
        'success', 'source_stable', 'tracked_context_exact', 'cleanup_verified',
        'private_directory_removed')) and build.get('errors') == []
        and build.get('cleanup_errors') == [], 'mutation-image.build-evidence')
    need(type(image_id) is str and IMAGE.fullmatch(image_id)
         and type(revision) is str and REVISION.fullmatch(revision), 'mutation-image.inputs')
    need(build.get('packaged_revision') == build.get('source_revision') == revision,
         'mutation-image.build-revision')
    return packaged.verify_build_receipt(build, source, image_id, revision)


def cleanup_valid(receipt, names):
    return (type(receipt) is dict and receipt.get('verified') is True
        and type(receipt.get('results')) is dict and set(receipt['results']) == set(names)
        and all(value is True for value in receipt['results'].values())
        and receipt.get('errors', []) == [])


def ip_valid(value, method, process):
    return (type(value) is dict and value.get('verified') is True
        and value.get('method') == method and hashed(value.get('address_sha256'))
        and value.get('process_sha256') == process and hashed(process))


def mutation_schema_valid(value):
    """Validate the actual FK columns and plain complete btree index key.

    Index/constraint names cannot substitute for the inspected SQL definition.
    The disposable schema uses ordinary PostgreSQL identifiers, optionally quoted.
    """
    identifier = r'(?:[a-z_][a-z0-9_]*|"[a-z_][a-z0-9_]*")'
    schema = rf'(?:{identifier}\.)?'
    delivery = r'"?communications_messagetransaction"?'
    message = r'"?communications_message"?'
    column = r'"?message_id"?'
    fk = (rf'FOREIGN KEY \({column}\) REFERENCES {schema}{message}\({column}\)'
          r'(?: ON UPDATE NO ACTION)?(?: ON DELETE NO ACTION)? DEFERRABLE INITIALLY DEFERRED')
    index = (rf'CREATE INDEX (?P<name>{identifier}) ON {schema}{delivery}'
             rf' USING btree \({column}\)')
    references = value['references']
    valid_index = False
    for row in value['indexes']:
        match = re.fullmatch(index, row['definition'])
        if (row['table'] == 'communications_messagetransaction' and match
                and match['name'].strip('"') == row['name']):
            valid_index = True
    return (value['orm_cascade'] == 'communications.MessageTransaction.message'
        and value['sql_cascade'] is False and value['message_audited'] is True
        and value['delivery_audited'] is False and len(references) == 1
        and references[0]['table'] == 'communications_messagetransaction'
        and type(references[0]['name']) is str and bool(references[0]['name'])
        and references[0]['delete_action'] == 'a'
        and references[0]['deferrable'] is True
        and references[0]['initially_deferred'] is True
        and re.fullmatch(fk, references[0]['definition']) is not None and valid_index)


def executing_evidence_valid(receipt, binary, process):
    """Require complete bracketed executable/listener evidence, bound to Go."""
    try:
        need(type(receipt) is dict and set(receipt) == {'verified', 'process_sha256', 'start', 'end'}
             and receipt['verified'] is True and receipt['process_sha256'] == process
             and hashed(process) and receipt['start'] == receipt['end'], 'receipt.executing-server')
        start = receipt['start']
        need(type(start) is dict and set(start) == {'pid', 'starttime_ticks', 'executable', 'port', 'listeners'}
             and type(start['pid']) is int and start['pid'] == 1
             and type(start['port']) is int and start['port'] == 8000
             and type(start['starttime_ticks']) is int and start['starttime_ticks'] > 0,
             'receipt.executing-server')
        executable = start['executable']
        need(type(executable) is dict and set(executable) == {'path', 'sha256', 'device', 'inode'}
             and executable['path'] == '/app/server' and executable['sha256'] == binary and hashed(binary)
             and type(executable['device']) is int and executable['device'] >= 0
             and type(executable['inode']) is int and executable['inode'] > 0, 'receipt.executing-server')
        listeners = start['listeners']
        need(type(listeners) is list and bool(listeners), 'receipt.executing-server')
        inodes = set()
        for row in listeners:
            need(type(row) is dict and set(row) == {'family', 'local_address_hex', 'local_port', 'inode', 'fds'}
                 and row['family'] in ('tcp', 'tcp6') and type(row['local_port']) is int and row['local_port'] == 8000
                 and type(row['inode']) is int and row['inode'] > 0 and row['inode'] not in inodes
                 and type(row['fds']) is list and bool(row['fds'])
                 and all(type(fd) is int and fd >= 0 for fd in row['fds'])
                 and row['fds'] == sorted(set(row['fds']))
                 and re.fullmatch(r'[0-9A-F]{' + ('8' if row['family'] == 'tcp' else '32') + '}',
                                  row['local_address_hex']) is not None, 'receipt.executing-server')
            inodes.add(row['inode'])
        return True
    except (AssertionError, KeyError, TypeError, ValueError):
        return False


def runtime_acceptance(report):
    """Independently require every authored case and each restoration/provenance gate."""
    try:
        require_assertions()
        need(report['runtime_executed'] is True and report['versioned'] is True
             and report['complete_message_family_parity'] is False
             and report['errors'] == [] and report['cleanup_errors'] == [], 'receipt.runtime')
        need(report['source_sha256'] and report['source_sha256'] == source_hashes()
             == report['source_sha256_at_server_start'] == report['source_sha256_after'], 'receipt.source')
        need(report['schema'] == SCHEMA and mutation_schema_valid(report['mutation_schema'])
             and report['mutation_schema_after'] == report['mutation_schema']
             and mutation_schema_valid(report['mutation_schema_after']), 'receipt.schema')
        need(report['audit_resolvers_after_migration'] is True, 'receipt.audit-resolver')
        need(report['python']['python'] == '3.13.16' and hashed(report['python']['sha256']), 'receipt.python')
        need(all(report[key] is True for key in ('cleanup_verified', 'private_directory_removed',
                                                'environment_restored')), 'receipt.cleanup')
        need(set(report['service_cleanup']) == set(SERVICES)
             and all(row == 'stopped' for row in report['service_cleanup'].values()), 'receipt.services')
        need(cleanup_valid(report['resource_cleanup'], RESOURCES)
             and cleanup_valid(report['sensitive_cleanup'], PRIVATE_CHECKS)
             and cleanup_valid(report['observations_cleanup'], LABELS + ('misroute',)), 'receipt.resources')
        source = contract.load_source()
        need(len(source['cases']) == EXPECTED_CASES, 'receipt.authored119')
        need(tuple((row['label'], row['backend']) for row in report['workloads']) == ENDPOINTS, 'receipt.endpoints')
        reference, baseline = None, None
        for workload in report['workloads']:
            need(workload['success'] is True and len(workload['cases']) == EXPECTED_CASES
                 and cleanup_valid(workload['fixture_cleanup'], FIXTURE_CLEANUP), 'receipt.workload')
            need(hashed(workload['process_sha256']) and ip_valid(workload['client_ip'],
                 IP_METHODS[workload['label']], workload['process_sha256']), 'receipt.client-ip')
            artifact = workload['go_binary_sha256']
            need(hashed(artifact) if workload['backend'] == 'go' else artifact is None, 'receipt.artifact')
            effect = workload['normalized_effects_sha256']
            need(hashed(effect) and type(workload['normalized_effects_count']) is int
                 and workload['normalized_effects_count'] == EXPECTED_CASES
                 and type(workload['observed_cases']) is int and workload['observed_cases'] == EXPECTED_CASES
                 and hashed(workload['observations_sha256'])
                 and workload['observations_verified'] is True, 'receipt.effect')
            reference = effect if reference is None else reference
            need(effect == reference, 'receipt.differential')
            current = workload['baseline']
            need(set(current) == {'state_sha256', 'cache_sha256', 'sequences_sha256'}
                 and all(hashed(value) for value in current.values()), 'receipt.baseline')
            baseline = current if baseline is None else baseline
            need(baseline == current, 'receipt.reset')
            for authored, observed in zip(source['cases'], workload['cases'], strict=True):
                need(observed['case'] == authored['name'] and type(observed['status']) is int
                     and observed['status'] == authored['status'] and type(observed['models']) is int
                     and observed['models'] == 128, 'receipt.case')
                need(set(observed['checks']) == set(CASE_CHECKS)
                     and all(value is True for value in observed['checks'].values())
                     and all(hashed(observed[key]) for key in (
                         'state_before_sha256', 'state_after_sha256', 'cache_sha256')), 'receipt.observation')
        native_go = report['workloads'][1]
        need(restore_image.receipt_valid(report['restore_controls'], native_go), 'receipt.restore-controls')
        need(executing_evidence_valid(report['executing_server'], native_go['go_binary_sha256'],
                                      native_go['process_sha256']), 'receipt.executing-server')
        control = report['misroute_control']
        need(control['rejected'] is True and type(control['actual_django_events']) is int
             and control['actual_django_events'] == 1 and type(control['status']) is int
             and control['status'] == 200 and control['state'] is True and control['cache'] is True
             and cleanup_valid(control['fixture_cleanup'], FIXTURE_CLEANUP)
             and ip_valid(control['client_ip'], 'owned-caddy-health-peer', control['process_sha256'])
             and hashed(report['caddy_image_sha256'])
             and report['caddy_image_sha256'] == report['preflight_caddy_image_sha256'], 'receipt.misroute')
        return True
    except (AssertionError, KeyError, TypeError, ValueError, RuntimeError, OSError, IndexError):
        return False


def acceptance(report):
    """Source, builder, package, real process, workload and cleanup all bind together."""
    try:
        require_assertions()
        need(report['packaged_checks_passed'] is True and runtime_acceptance(report['runtime'])
             and report['runtime']['success'] is True, 'mutation-image.runtime')
        need(all(report[key] is True for key in ('cleanup_verified', 'private_directory_removed',
             'source_stable', 'build_source_stable', 'revision_stable'))
             and report['errors'] == [] and report['cleanup_errors'] == [], 'mutation-image.cleanup')
        build, image = report['build_receipt'], report['image']
        preflight = report['preflight_images']
        need(set(preflight) == {'go', 'postgres', 'redis', 'caddy'}
             and all(IMAGE.fullmatch(value) for value in preflight.values())
             and preflight['go'] == report['requested_image']
             and preflight['postgres'] == image['postgres_image']
             and preflight['redis'] == image['redis_image']
             and preflight['caddy'] == 'sha256:' + report['runtime']['caddy_image_sha256'],
             'mutation-image.preflight')
        verify_build_receipt(build, ROOT, report['requested_image'], report['expected_revision'])
        need(hashed(report['build_receipt_sha256']) and report['build_context_sha256']
             == report['build_context_sha256_after'] == build['source_sha256'], 'mutation-image.source')
        need(report['runtime_revision_before'] == report['runtime_revision_after']
             == report['expected_revision'] == image['revision'] == build['packaged_revision']
             and image['immutable_id'] == report['requested_image'] == build['image_id'], 'mutation-image.revision')
        need(report['runtime_graph_sha256'] == report['runtime_graph_sha256_after']
             == contract.source_digest(report['runtime']['source_sha256']), 'mutation-image.runtime-graph')
        need(image['user'] == '10001:10001' and type(image['uid']) is int and image['uid'] == 10001
             and image['entrypoint'] == ['/app/server'] and image['readonly_root'] is True
             and image['mounted_executables'] is False and image['health_version_matches_package'] is True
             and hashed(image['version_sha256']) and image['platform'] in ('linux/amd64', 'linux/arm64'),
             'mutation-image.package')
        packaged.verify_binary_digest(image['server_sha256'], build['server_sha256'], build)
        need(all(row['go_binary_sha256'] == image['server_sha256'] for row in report['runtime']['workloads']
                 if row['backend'] == 'go'), 'mutation-image.binary')
        process = report['container']
        need(process['running_verified'] is True and process['stopped_verified'] is True
             and type(process['exit_code']) is int and process['exit_code'] == 0
             and type(process['restart_count']) is int and process['restart_count'] == 0
             and hashed(process['identity_sha256']) and hashed(process['process_sha256'])
             and process['process_sha256'] == report['runtime']['workloads'][1]['process_sha256'],
             'mutation-image.container')
        expected = (ROOT / 'BE_GO/THIRD_PARTY_NOTICES.md').read_bytes()
        need(report['third_party_license'] == packaged.license_evidence(expected, expected)
             and report['complete_message_family_parity'] is False, 'mutation-image.scope')
        return True
    except (AssertionError, KeyError, TypeError, ValueError, RuntimeError, OSError, IndexError):
        return False


def ip_receipt(address, method, guard):
    need(method in IP_METHODS.values(), 'mutation-image.ip-method')
    address = str(ipaddress.ip_address(address))
    process = lifecycle._process(guard)
    return {'verified': True, 'method': method, 'address_sha256': contract.source_digest(address),
            'process_sha256': contract.source_digest(process)}


def tcp_peers(content, port):
    """Linux /proc TCP peers, independent of application responses/audit data."""
    peers = set()
    for line in content.splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].endswith(':') or fields[3] != '01':
            continue
        local, remote = fields[1:3]
        if int(local.rsplit(':', 1)[1], 16) != port:
            continue
        address, peer_port = remote.split(':')
        need(len(address) in (8, 32), 'mutation-image.tcp-address')
        raw = bytes.fromhex(address)
        raw = b''.join(raw[index:index+4][::-1] for index in range(0, len(raw), 4))
        address = ipaddress.ip_address(raw)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        peers.add((str(address), int(peer_port, 16)))
    return peers


def measure_address(label, base, guard, *, proof=None):
    """Independent health/socket probe before and after each transport workload."""
    from TESTS.http_client import Client
    initial = lifecycle._process(guard)
    if label.startswith('caddy'):
        status, _, headers = Client(base.split('/api/')[0]).request('GET', '/healthz', include_headers=True)
        need(status == 200, 'mutation-image.caddy-probe')
        address = headers.get('X-Fixture-Client-IP')
    else:
        url = urlsplit(base)
        need(url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port, 'mutation-image.local-probe')
        def peers():
            name = proof.identifier + '-server'
            return tcp_peers(proof.docker('exec', name, 'cat', '/proc/net/tcp', '/proc/net/tcp6').stdout, 8000)
        before = peers() if label == 'go-packaged' else None
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
        try:
            connection.connect()
            address = connection.sock.getsockname()[0]
            if label == 'go-packaged':
                # Keep the accepted socket open while inspecting its server-side peer.
                # No audit/response value is used to infer this address.
                connection.request('GET', '/healthz')
                response = connection.getresponse()
                need(response.status == 200 and not response.will_close, 'mutation-image.keepalive-probe')
                response.read()
                added = peers() - before
                need(len(added) == 1, 'mutation-image.unique-peer')
                address = next(iter(added))[0]
        finally:
            connection.close()
    receipt = ip_receipt(address, IP_METHODS[label], guard)
    need(lifecycle._process(guard) == initial, 'mutation-image.probe-process')
    return str(ipaddress.ip_address(address)), receipt


class MutationImageFixture(packaged.MessageImageFixture):
    """Keep published image lifecycle; bound command groups and private logs."""
    def __init__(self, *args):
        super().__init__(*args)
        self.commands = builder.Commands(self.work)

    def docker(self, *args, check=True, timeout=90):
        # Commands enforces cancellation/group teardown and fixed diagnostics.
        # The inherited fixture needs nonzero results for absence/stop checks.
        import subprocess
        if check:
            output = self.commands(['docker', *map(str, args)], timeout=timeout).decode()
            return subprocess.CompletedProcess(args, 0, output, '')
        try:
            output = self.commands(['docker', *map(str, args)], timeout=timeout).decode()
            return subprocess.CompletedProcess(args, 0, output, '')
        except RuntimeError as error:
            if str(error) != 'build.command-failed':
                raise
            return subprocess.CompletedProcess(args, 1, '', '')

    @contextmanager
    def server(self, work, log, tools, address, *, versioned=False):
        from django.test.utils import override_settings
        with _owned(super().server(work, log, tools, address, versioned=versioned)) as server:
            # The inherited lifetime mounts this exact directory at /operations.
            # Its health probes do not acquire the admission lock. Create it as
            # the host owner before UID 10001's first mutation; only this empty
            # synthetic coordination file is writable by both participants.
            lock = Path(work) / 'image-operations/lifecycle.lock'
            need(not lock.is_symlink(), 'mutation-image.lock-file')
            lock.touch(mode=0o666, exist_ok=True)
            lock.chmod(0o666)
            with override_settings(DATA_RESTORE_LOCK_PATH=str(lock)):
                yield server

    def cleanup(self):
        try:
            super().cleanup()
        finally:
            if not self.commands.cleanup_verified:
                self.cleanup_verified = False
        need(self.cleanup_verified, 'mutation-image.command-cleanup')


def _linux_lock_identity(proof, guard, *, info=None):
    """Bind the real default-user server, configured mount and synthetic inode."""
    from django.conf import settings
    container = proof.identifier + '-server'
    if info is None:
        info = proof.inspect(container)
    identity = packaged._container_identity(info, proof.image_id, proof.identifier)
    operations = Path(proof.work).resolve() / 'image-operations'
    lock = operations / 'lifecycle.lock'
    need(operations.is_dir() and not operations.is_symlink()
         and lock.is_file() and not lock.is_symlink()
         and Path(settings.DATA_RESTORE_LOCK_PATH).resolve() == lock, 'mutation-image.linux-lock-path')
    mounts = [row for row in info['Mounts'] if row['Destination'] == '/operations']
    need(len(mounts) == 1 and mounts[0]['Type'] == 'bind' and mounts[0]['RW'] is True
         and Path(mounts[0]['Source']).resolve() == operations, 'mutation-image.linux-lock-mount')
    paths = [value for value in info['Config']['Env'] if value.startswith('DATA_RESTORE_LOCK_PATH=')]
    need(paths == ['DATA_RESTORE_LOCK_PATH=/operations/lifecycle.lock'],
         'mutation-image.linux-lock-config')
    stat = lock.stat()
    return identity, lifecycle._process(guard), (stat.st_dev, stat.st_ino)


@contextmanager
def linux_locked_admission(proof, guard):
    """Own one actual Linux lock; prove readiness before HTTP and release after.

    Docker exec output never reaches public FDs. A nonzero exit, expired lease,
    malformed protocol or failed same-container reacquisition cannot pass.
    """
    initial = _linux_lock_identity(proof, guard)
    container = initial[0]['id']  # Pin exec to this owned immutable container ID.
    process, acquired = None, False
    with ExitStack() as files:
        with defer_interruption():
            output = files.enter_context(tempfile.TemporaryFile(dir=proof.work))
            errors = files.enter_context(tempfile.TemporaryFile(dir=proof.work))
        try:
            with defer_interruption():
                process = subprocess.Popen(['docker', 'exec', '-i', container, 'sh', '-c', LINUX_LOCK_SCRIPT],
                    stdin=subprocess.PIPE, stdout=output, stderr=errors, start_new_session=True)
            until = time.monotonic() + LOCK_START_TIMEOUT
            while True:
                content = os.pread(output.fileno(), len(LOCK_READY) + 1, 0)
                need(process.poll() is None, 'mutation-image.linux-lock-start-exit')
                if content == LOCK_READY:
                    acquired = True
                    break
                need(LOCK_READY.startswith(content), 'mutation-image.linux-lock-readiness')
                need(time.monotonic() < until, 'mutation-image.linux-lock-start-timeout')
                time.sleep(0.02)
            need(_linux_lock_identity(proof, guard) == initial,
                 'mutation-image.linux-lock-identity')
            yield
        finally:
            # Cover startup failure/cancellation too. Do not declare terminal
            # runner cleanup for a per-case context that normally continues.
            with defer_interruption():
                cleanup_errors, protocol_error = [], False
                if process is not None:
                    try:
                        live = process.poll() is None
                        protocol_error = acquired and not live
                        if process.stdin is not None:
                            stream, process.stdin = process.stdin, None
                            try:
                                if live:
                                    stream.write(b'MUTATION_LOCK_RELEASE\n')
                                    stream.flush()
                            finally:
                                # EOF is also an emergency release path. Detach
                                # the closed stream before stop_group.communicate.
                                stream.close()
                        process.wait(timeout=LOCK_EXIT_TIMEOUT)
                        if acquired:
                            protocol_error |= (process.returncode != 0 or
                                os.pread(output.fileno(), len(LOCK_READY + LOCK_RELEASED) + 1, 0)
                                != LOCK_READY + LOCK_RELEASED)
                    except (OSError, subprocess.SubprocessError):
                        protocol_error = True
                        # A lost client may leave an exec not yet observable by
                        # a point probe. Do not certify that uncertain lifetime.
                        cleanup_errors.append('transport')
                    finally:
                        # Reaping Docker's client alone never proves its group
                        # or the container-side exec released the Linux lock.
                        try:
                            proof.commands.stop_group(process)
                        except BaseException:
                            cleanup_errors.append('group')
                try:
                    released = proof.commands(['docker', 'exec', container, 'sh', '-c', LINUX_LOCK_PROBE],
                        timeout=LOCK_EXIT_TIMEOUT)
                    need(released == b'MUTATION_LOCK_REACQUIRED\n', 'mutation-image.linux-lock-probe')
                except BaseException:
                    cleanup_errors.append('reacquire')
                try:
                    need(_linux_lock_identity(proof, guard) == initial,
                         'mutation-image.linux-lock-identity')
                except BaseException:
                    cleanup_errors.append('identity')
                if cleanup_errors:
                    proof.commands.cleanup_verified = False
                    need(False, 'mutation-image.linux-lock-cleanup-unverified')
                need(not protocol_error, 'mutation-image.linux-lock-child-exit')


def image_admission_controller(proof, guard):
    @contextmanager
    def control(case, fixture):
        if case.get('admission') == 'locked':
            with linux_locked_admission(proof, guard):
                yield
        else:
            with adapter.admission_control(case, fixture):
                yield
    return control


def baseline(apps, raw):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    state = scopes.snapshot_state(apps)
    need(len(state) == 128, 'mutation-image.models128')
    return {'state_sha256': adapter.shared.digest(state),
            'cache_sha256': snapshot_historical_read_cache(raw).digest,
            'sequences_sha256': adapter.shared.digest(adapter.sequence_snapshot())}


def run_endpoint(label, backend, base, guard, apps, raw, observer, work, report,
                 private_values, *, proof=None, binary=None):
    """Compose the published mutation oracle; retain no raw observations publicly."""
    current = baseline(apps, raw)
    address, address_proof = measure_address(label, base, guard, proof=proof)
    process = lifecycle._process(guard)
    cleanup = {}
    # The published verifier expects numbered observations. Endpoints execute
    # sequentially and remove this private file before the next transport.
    observation = work / 'observations-0.jsonl'
    workload = None
    started = time.time()
    admission = {}
    if backend == 'go':
        need(proof is not None, 'mutation-image.linux-lock-owner')
        admission['admission_controller'] = image_admission_controller(proof, guard)
    try:
        with adapter.fixture_context(apps, work / ('fixture-' + label), raw, cleanup_receipt=cleanup) as fixture:
            private_values.update(fixture['tokens'].values())
            private_values.update(fixture['sessions'].values())
            workload = adapter.run_workload(label, backend, base, fixture, apps, raw, observer,
                remote_addr=address, process_guard=guard, observations_path=observation, **admission)
            effects = workload.pop('normalized_effects')
            need(len(effects) == EXPECTED_CASES, 'mutation-image.effects119')
            for effect in effects:
                if 'audit' in effect:
                    effect['audit']['remote_addr'] = '$independently-measured-client-ip'
            workload.update(normalized_effects_sha256=adapter.shared.digest(effects),
                normalized_effects_count=len(effects),
                baseline=current, client_ip=address_proof, process_sha256=contract.source_digest(process),
                go_binary_sha256=binary if backend == 'go' else None, fixture_cleanup=cleanup)
            report['workloads'].append(workload)
        need(cleanup_valid(cleanup, FIXTURE_CLEANUP) and baseline(apps, raw) == current,
             'mutation-image.endpoint-reset')
        after_address, after_proof = measure_address(label, base, guard, proof=proof)
        need(after_address == address and after_proof == address_proof
             and lifecycle._process(guard) == process, 'mutation-image.endpoint-provenance')
        need(not observation.is_symlink() and observation.is_file()
             and observation.stat().st_mode & 0o777 == 0o600,
             'mutation-image.private-observations')
        observed = []
        with observation.open() as stream:
            for line in stream:
                row = json.loads(line)
                need(type(row['status']) is int, 'mutation-image.observed-status')
                observed.append((row['case'], row['status']))
        need(observed == [(case['name'], case['status']) for case in contract.load_source()['cases']]
             and len(observed) == EXPECTED_CASES, 'mutation-image.observations119')
        replay = verify_observations({'workloads': [workload]},
            [{'label': label, 'backend': backend, 'remote_addr': address}], work,
            started=started, finished=time.time())
        need(replay['verified'] is True, 'mutation-image.raw-evidence')
        workload.update(observed_cases=len(observed), observations_sha256=digest(observation),
                        observations_verified=True)
    finally:
        observation.unlink(missing_ok=True)
        report['observations_cleanup']['results'][label] = not observation.exists()
    return workload


def misroute_control(base, guard, apps, raw, observer, work, report, private_values):
    """Matching successful mutation routed to Django must fail Go attribution."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    import time
    current = baseline(apps, raw)
    address, address_proof = measure_address('caddy-v1', base, guard)
    process = lifecycle._process(guard)
    cleanup = {}
    observation = work / 'observations-misroute.jsonl'
    try:
        with adapter.fixture_context(apps, work / 'fixture-misroute', raw, cleanup_receipt=cleanup) as fixture:
            private_values.update(fixture['tokens'].values())
            private_values.update(fixture['sessions'].values())
            case = fixture['runtime']['cases'][0]
            need(case['status'] == 200 and case['effect']['kind'] == 'create', 'mutation-image.control-case')
            before, cache_before = scopes.snapshot_state(apps), snapshot_historical_read_cache(raw)
            cursor, started = observer.cursor(), time.time()
            status = contract.assert_case(case, base)
            finished = time.time()
            after, cache_after = scopes.snapshot_state(apps), snapshot_historical_read_cache(raw)
            observed = observer.delta(cursor)
            with observation.open('x') as stream:
                observation.chmod(0o600)
                json.dump({'before': before, 'after': after, 'observed': observed,
                    'cache_before': adapter.private_cache(cache_before),
                    'cache_after': adapter.private_cache(cache_after)}, stream)
            adapter.shared.assert_attribution('django', case['method'], case['path'], status, observed, case['name'])
            rejected = False
            try:
                adapter.shared.assert_attribution('go', case['method'], case['path'], status, observed, case['name'])
            except adapter.shared.AttributionError:
                rejected = True
            need(rejected, 'mutation-image.misroute-accepted')
            adapter.shared.assert_cache_unchanged(cache_before, cache_after, fixture['contrasts'])
            contract.assert_effect(case, before, after, started=started, finished=finished,
                                   remote_addr=address, cid=case['name'])
        need(cleanup_valid(cleanup, FIXTURE_CLEANUP) and baseline(apps, raw) == current
             and lifecycle._process(guard) == process, 'mutation-image.control-reset')
        need(measure_address('caddy-v1', base, guard)[1] == address_proof, 'mutation-image.control-ip')
        return {'rejected': True, 'actual_django_events': len(observed), 'status': status,
                'state': True, 'cache': True, 'fixture_cleanup': cleanup,
                'process_sha256': contract.source_digest(process), 'client_ip': address_proof}
    finally:
        observation.unlink(missing_ok=True)
        report['observations_cleanup']['results']['misroute'] = not observation.exists()


def execute_workloads(reuse, proof, work, log, tools, build, report, private_values):
    evidence = report['runtime']
    environment, generated = reuse.private_environment(work, proof.redis_port, tools)
    environment.update(PG_BOUNDS)
    environment.update(TESTS_DATABASE=packaged.DATABASE, DBNAME=packaged.DATABASE,
                       DBHOST='127.0.0.1', DBPORT=str(proof.postgres_port))
    private_values.update(generated)
    reuse.apply_private_environment(environment)
    raw, override = None, None
    evidence['resource_cleanup'] = {'verified': False, 'results': {}}

    def close_connections():
        # Even django.setup() can open a connection before it fails.
        from django.db import connections
        connections.close_all()

    try:
        apps, _ = reuse.initialize_django_apps()
        from django.db import connections
        from django.test.utils import override_settings
        from TESTS.http_client import poll
        import redis
        connections.close_all()
        configure_database_bounds(connections)
        override = override_settings(CACHES=reuse.cache_override(proof.redis_port))
        with defer_interruption():
            override.enable()
        evidence['schema'] = reuse.migrate_full_schema(log)
        need(evidence['schema'] == SCHEMA, 'mutation-image.migrations547')
        # AppConfig.ready ran before this fresh database had content types.
        from application.audit_resolvers import setup_audit_resolvers, get_resolver, direct_resolver
        from django.contrib.contenttypes.models import ContentType
        from communications.models import Message
        setup_audit_resolvers()
        need(get_resolver(ContentType.objects.get_for_model(Message)) is direct_resolver,
             'mutation-image.audit-resolver')
        evidence['audit_resolvers_after_migration'] = True
        evidence['mutation_schema'] = adapter.schema_evidence()
        need(mutation_schema_valid(evidence['mutation_schema']), 'mutation-image.schema-before')
        with defer_interruption():
            raw = redis.Redis(host='127.0.0.1', port=proof.redis_port, db=1,
                password='synthetic-identity-only', decode_responses=False,
                socket_timeout=5, socket_connect_timeout=5)
        poll(raw.ping, bool, timeout=15)
        django_guards = []
        with _owned(reuse.django_application(evidence, True, None,
                liveness_observer=django_guards.append)) as (django_base, observer, django_port):
            need(len(django_guards) == 1, 'mutation-image.django-guard')
            django_guard = django_guards[0]
            evidence['source_sha256_at_server_start'] = source_hashes()
            need(evidence['source_sha256_at_server_start'] == evidence['source_sha256'], 'mutation-image.source-start')
            run_endpoint('django-native', 'django', django_base, django_guard,
                apps, raw, observer, work, evidence, private_values)
            evidence['service_cleanup']['go'] = 'start_attempted'
            with _owned(proof.verified_server(work, log, tools, build, report)) as server:
                evidence['service_cleanup']['go'] = 'running'
                executing = {'verified': False, 'start': None, 'end': None, 'process_sha256': None}
                evidence['executing_server'] = executing
                go_guard = executing_server_guard(server[packaged.runtime.LIFECYCLE_GUARD],
                    docker=proof.docker, container=proof.identifier + '-server',
                    expected_server_sha256=build['server_sha256'])
                executing.update(start=go_guard.evidence,
                    process_sha256=contract.source_digest(lifecycle._process(go_guard)))
                run_endpoint('go-packaged', 'go', server['base_url'], go_guard,
                    apps, raw, observer, work, evidence, private_values, proof=proof,
                    binary=server['metadata']['server_sha256'])
                evidence['restore_controls'] = restore_image.run_controls(
                    apps=apps, database=connections['default'], raw=raw, base_url=server['base_url'],
                    process_guard=go_guard, proof=proof, work=work, private_values=private_values,
                    binary=server['metadata']['server_sha256'])
                django_upstream = f'host.docker.internal:{django_port}'
                with _owned(packaged.owned_image_caddy(evidence, log, django_upstream,
                        server['proxy_upstream'], network=proof.network, pull_policy='never')) as (gateway, _, caddy):
                    need(caddy['image_id'] == 'sha256:' + evidence['preflight_caddy_image_sha256'],
                         'mutation-image.caddy-image')
                    evidence['caddy_image_sha256'] = caddy['image_id'].removeprefix('sha256:')
                    for label, backend, version, backend_guard in (
                            ('caddy-v1', 'django', 'v1', django_guard),
                            ('caddy-v2', 'go', 'v2', go_guard)):
                        guard = packaged.gateway_liveness_guard(backend_guard, caddy['container_id'])
                        run_endpoint(label, backend, gateway + '/api/' + version, guard,
                            apps, raw, observer, work, evidence, private_values,
                            proof=proof if backend == 'go' else None,
                            binary=server['metadata']['server_sha256'])
                with _owned(reuse.owned_caddy(evidence, log, django_upstream, django_upstream,
                        service='caddy-misroute', pull_policy='never')) as (gateway, _, caddy):
                    need(caddy['image_id'] == 'sha256:' + evidence['preflight_caddy_image_sha256'],
                         'mutation-image.misroute-image')
                    guard = packaged.gateway_liveness_guard(django_guard, caddy['container_id'])
                    evidence['misroute_control'] = misroute_control(gateway + '/api/v2', guard,
                        apps, raw, observer, work, evidence, private_values)
                # Explicit end probe while Go still owns HTTP admission, after
                # both direct/versioned workloads and the deliberate misroute.
                end = go_guard.probe()
                need(end == executing['start'] and executing['process_sha256']
                     == contract.source_digest(lifecycle._process(go_guard)), 'mutation-image.executing-server')
                executing.update(end=end, verified=True)
            evidence['service_cleanup']['go'] = 'stopped'
        evidence['mutation_schema_after'] = adapter.schema_evidence()
        need(mutation_schema_valid(evidence['mutation_schema_after'])
             and evidence['mutation_schema_after'] == evidence['mutation_schema'], 'mutation-image.schema-after')
        evidence['runtime_executed'] = True
    finally:
        begin_cleanup()
        evidence['resource_cleanup'] = scopes.cleanup_steps((
            ('redis_client', lambda: raw.close() if raw is not None else True),
            ('db_connections', close_connections),
            ('cache_override', lambda: override.disable() if override is not None else True)))


def check_revision(commands, expected):
    """Bind every runtime graph byte/mode to Git blobs, bypassing index stat flags."""
    builder.clean_head(commands, ROOT, expected)
    manifest = source_hashes()
    tree = builder.git(commands, ROOT, 'ls-tree', '-r', '-z', '--full-tree', expected)
    entries = []
    for row in tree.split(b'\0'):
        if not row:
            continue
        metadata, name = row.split(b'\t', 1)
        name = name.decode('utf-8')
        if name in manifest:
            mode, kind, oid = metadata.decode('ascii').split()
            need(kind == 'blob' and mode in ('100644', '100755')
                 and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', oid), 'mutation-image.runtime-entry')
            entries.append((name, mode, oid))
    need({name for name, _, _ in entries} == set(manifest), 'mutation-image.runtime-tracked-graph')
    # The builder's private bounded command primitive writes full blobs to a
    # private file; read one blob at a time rather than exporting diagnostics.
    payload = commands.work / 'runtime-blobs'
    builder.git(commands, ROOT, 'cat-file', '--batch',
        data=b''.join(oid.encode() + b'\n' for _, _, oid in entries), target=payload)
    try:
        with payload.open('rb') as stream:
            for name, _, oid in entries:
                header = stream.readline(256).decode('ascii').strip().split()
                need(len(header) == 3 and header[:2] == [oid, 'blob'] and header[2].isdigit(),
                     'mutation-image.runtime-blob')
                remaining = int(header[2])
                algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
                object_hash = algorithm(b'blob ' + header[2].encode() + b'\0')
                content_hash = hashlib.sha256()
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    need(bool(chunk), 'mutation-image.runtime-truncated')
                    remaining -= len(chunk)
                    object_hash.update(chunk)
                    content_hash.update(chunk)
                need(stream.read(1) == b'\n' and object_hash.hexdigest() == oid
                     and content_hash.hexdigest() == manifest[name], 'mutation-image.runtime-content')
            need(not stream.read(1), 'mutation-image.runtime-trailing')
        builder.checkout_matches(ROOT, manifest, entries)
        builder.clean_head(commands, ROOT, expected)
        need(source_hashes() == manifest, 'mutation-image.runtime-graph-drift')
    finally:
        payload.unlink(missing_ok=True)
    return expected


def preflight_images(proof, postgres, redis, tools):
    """Inspect every local image before any network/container/service creation.

    Pin fixture tags to these immutable IDs for startup. Caddy's shared recipe
    resolves its tag again; both actual lifetimes must match this observation.
    """
    images = {name: proof.image(requested)['Id'] for name, requested in (
        ('go', proof.requested_image), ('postgres', postgres), ('redis', redis))}
    images['caddy'] = 'sha256:' + check_caddy_image(tools)
    need(all(type(value) is str and IMAGE.fullmatch(value) for value in images.values())
         and images['go'] == proof.requested_image, 'mutation-image.cached-images')
    return images


def execute_private(args, source, report, control, work, log, remove_work):
    """All helper/process calls, including preflight and final checks, stay private."""
    from TESTS.adapters.django import message_reads_runtime as reuse
    from TESTS.adapters.django.production_runtime import require_production_python
    evidence = report['runtime']
    original_environment = dict(os.environ)
    private_values = set()
    proof = commands = None
    try:
        require_assertions()
        require_production_python()
        evidence['python'] = {'python': '3.13.16', 'sha256': digest(sys.executable)}
        evidence['source_sha256'] = source_hashes()
        report['build_context_sha256'] = packaged.go_source_hashes(source)
        need(report['build_context_sha256'] == packaged.go_source_hashes(ROOT), 'mutation-image.oracle-source')
        build_bytes = args.build_receipt.read_bytes()
        build = json.loads(build_bytes)
        report['build_receipt_sha256'] = hashlib.sha256(build_bytes).hexdigest()
        verify_build_receipt(build, source, args.image, args.expected_revision)
        report['build_receipt'] = {key: build[key] for key in BUILD_KEYS}
        tools = packaged.runtime.owned_tool_environment()
        tools.update(PG_BOUNDS)
        reuse.apply_private_environment(tools)
        commands = builder.Commands(work)
        report['runtime_revision_before'] = check_revision(commands, args.expected_revision)
        report['runtime_graph_sha256'] = contract.source_digest(evidence['source_sha256'])
        # Register independent teardown before starting any owned service.
        with defer_interruption():
            proof = MutationImageFixture(args.image, args.expected_revision, work, work / 'private-logs', log)
            proof.restore_failure_receipt = args.output / 'results.json'
        report['_go_source'] = source
        report['preflight_images'] = preflight_images(proof, args.postgres, args.redis, tools)
        evidence['preflight_caddy_image_sha256'] = report['preflight_images']['caddy'].removeprefix('sha256:')
        evidence['service_cleanup'].update(postgres='start_attempted', redis='start_attempted')
        with defer_interruption():
            proof.start_services(report['preflight_images']['postgres'], report['preflight_images']['redis'])
            need(proof.image_id == args.image, 'mutation-image.resolved-id')
            need(all(proof.metadata[name + '_image'] == report['preflight_images'][name]
                     for name in ('postgres', 'redis')), 'mutation-image.fixture-images')
            evidence['service_cleanup'].update(postgres='running', redis='running')
        execute_workloads(reuse, proof, work, log, tools, build, report, private_values)
        report['packaged_checks_passed'] = True
    except BaseException:
        report['errors'].append('mutation-image.execution-failed')
        traceback.print_exc(file=log)
    finally:
        begin_cleanup()
        report.pop('_go_source', None)
        # No cleanup failure may suppress another independent resource finalizer.
        for code, operation in (
                ('cleanup.private-failed', lambda: reuse.remove_sensitive_files(work, evidence)),
                ('cleanup.image-failed', lambda: cleanup_image(proof, private_values))):
            try:
                operation()
            except BaseException:
                report['cleanup_errors'].append(code)
                traceback.print_exc(file=log)
        if 'sensitive_cleanup' in evidence:
            evidence['sensitive_cleanup']['errors'] = ['cleanup.private-failed'] * len(
                evidence['sensitive_cleanup']['errors'])
        if proof is not None:
            for service in ('postgres', 'redis', 'go'):
                if evidence['service_cleanup'][service] != 'not_started':
                    evidence['service_cleanup'][service] = 'stopped' if proof.cleanup_verified else 'cleanup_failed'
        try:
            if commands is not None:
                report['runtime_revision_after'] = check_revision(commands, args.expected_revision)
                report['runtime_graph_sha256_after'] = contract.source_digest(source_hashes())
                report['revision_stable'] = report.get('runtime_revision_before') == report['runtime_revision_after']
            evidence['source_sha256_after'] = source_hashes()
            report['source_stable'] = evidence.get('source_sha256') == evidence['source_sha256_after']
            report['build_context_sha256_after'] = packaged.go_source_hashes(source)
            report['build_source_stable'] = (report.get('build_context_sha256') == report['build_context_sha256_after']
                and digest(args.build_receipt) == report.get('build_receipt_sha256'))
        except BaseException:
            report['errors'].append('mutation-image.source-binding-failed')
            traceback.print_exc(file=log)
        os.environ.clear()
        os.environ.update(original_environment)
        evidence['environment_restored'] = dict(os.environ) == original_environment
        metadata = proof.metadata if proof else {}
        report['image'] = {key: metadata[key] for key in (
            'immutable_id', 'requested_image', 'platform', 'user', 'entrypoint', 'postgres_image',
            'redis_image', 'revision', 'server_sha256', 'readonly_root', 'uid',
            'mounted_executables', 'health_version_matches_package') if key in metadata}
        if type(metadata.get('version')) is str:
            report['image']['version_sha256'] = hashlib.sha256(metadata['version'].encode()).hexdigest()
        report['cleanup_verified'] = bool(proof and proof.cleanup_verified and not report['cleanup_errors']
            and commands and commands.cleanup_verified)
        # Remove private paths while the log's open FD still captures diagnostics.
        # ExitStack retains a second cleanup attempt if removal or publication fails.
        try:
            remove_work()
        except BaseException:
            report['cleanup_errors'].append('cleanup.directory-failed')
            report['cleanup_verified'] = False
        report['private_directory_removed'] = evidence['private_directory_removed'] = not work.exists()
        observations = evidence['observations_cleanup']
        observations['verified'] = (set(observations['results']) == set(LABELS + ('misroute',))
            and all(value is True for value in observations['results'].values()))
        evidence['cleanup_verified'] = bool(report['cleanup_verified'] and evidence['private_directory_removed']
            and evidence['environment_restored'] and cleanup_valid(evidence.get('resource_cleanup'), RESOURCES)
            and cleanup_valid(evidence.get('sensitive_cleanup'), PRIVATE_CHECKS)
            and cleanup_valid(observations, LABELS + ('misroute',)))
        report['cleanup_verified'] = evidence['cleanup_verified']
        evidence['cleanup_errors'] = ['cleanup.runtime-failed'] * len(evidence['cleanup_errors'])
        if control.interrupted:
            report['errors'].append('execution.interrupted')
            evidence['errors'].append('execution.interrupted')
            report['execution'] = {'interrupted': True, 'reason': control.reason, 'escalated': False}
        evidence['success'] = runtime_acceptance(evidence)
        report['success'] = acceptance(report)
        adapter.shared.write_publishable(args.output / 'results.json', report, private_values)


def cleanup_image(proof, private_values):
    if proof is not None:
        proof.private_values.update(private_values)
        proof.cleanup()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--go-source', type=Path, default=ROOT)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deadline', type=float, default=1500)
    parser.add_argument('--cleanup-grace', type=float, default=180)
    args = parser.parse_args(argv)
    if not IMAGE.fullmatch(args.image) or not REVISION.fullmatch(args.expected_revision):
        parser.error('immutable --image and full --expected-revision required')
    output, source = args.output.resolve(), args.go_source.resolve()
    args.output = output
    need(not output.is_relative_to(ROOT / 'BE_GO') and not output.is_relative_to(source / 'BE_GO'),
         'mutation-image.output-context')
    receipt = output / 'results.json'
    control = ExecutionDeadline(args.deadline, args.cleanup_grace,
        on_escalation=lambda state: fail_receipt(receipt, state, code='execution.cleanup-grace-expired'))
    output.mkdir(parents=True, exist_ok=False)
    report = {'success': False, 'packaged_checks_passed': False,
        'scope': 'bounded packaged saved-message CREATE/DELETE; 119 authored cases on four transports',
        'complete_message_family_parity': False, 'requested_image': args.image,
        'expected_revision': args.expected_revision, 'errors': [], 'cleanup_errors': [],
        'runtime': {'success': False, 'runtime_executed': False, 'versioned': True,
            'complete_message_family_parity': False, 'errors': [], 'cleanup_errors': [], 'workloads': [],
            'observations_cleanup': {'verified': False, 'results': {}, 'errors': []},
            'service_cleanup': dict.fromkeys(SERVICES, 'not_started')}}
    receipt.write_text(json.dumps(report, indent=2) + '\n')
    original_environment, original_umask = dict(os.environ), None
    try:
        with control:
            try:
                with _owned(ExitStack()) as lifetime:
                    # Establish owners before delivering setup cancellation. Both
                    # Python streams and inherited child FDs cover ALL helpers.
                    with defer_interruption():
                        original_umask = os.umask(0o077)
                        temporary = tempfile.TemporaryDirectory(prefix='az-message-mutations-image-', dir='/tmp')
                        lifetime.callback(temporary.cleanup)
                        work = Path(temporary.name)
                        private_output = work / 'private-logs'
                        private_output.mkdir(mode=0o700)
                        log = lifetime.enter_context((private_output / 'services.log').open('x'))
                        lifetime.enter_context(private_diagnostics(log))
                    execute_private(args, source, report, control, work, log, temporary.cleanup)
            finally:
                control.begin_cleanup()
                os.environ.clear()
                os.environ.update(original_environment)
                if original_umask is not None:
                    os.umask(original_umask)
    except BaseException:
        # Setup/FD restoration/receipt failure cannot establish verified cleanup.
        # Do not export the helper exception, even after the private log is gone.
        report = {'success': False, 'cleanup_verified': False, 'private_directory_removed': False,
                  'errors': ['execution.interrupted' if control.interrupted else 'mutation-image.envelope-failed']}
        try:
            receipt.write_text(json.dumps(report, indent=2) + '\n')
        except BaseException:
            pass
    if control.interrupted:
        try:
            fail_receipt(receipt, control)
            # A signal can arrive during FD restoration after the private gate
            # wrote its receipt. Invalidate its nested success at this fixed
            # public boundary too, preserving only already published evidence.
            published = json.loads(receipt.read_bytes())
            if type(published.get('runtime')) is dict:
                published['runtime']['success'] = False
                published['runtime'].setdefault('errors', []).append('execution.interrupted')
            receipt.write_text(json.dumps(published, indent=2) + '\n')
        except BaseException:
            pass
        report['success'] = False
    print(json.dumps({'success': report['success'], 'check': 'message-mutations.packaged-image'}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    from TESTS.adapters.go.message_mutations_image import main as packaged_main
    raise SystemExit(packaged_main())
