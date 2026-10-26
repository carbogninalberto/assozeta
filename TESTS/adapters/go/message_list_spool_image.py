"""Parent-only, source-bound packaged LIST spool proof (phase one).

Import uses only stdlib and has no side effects. This version deliberately never
accepts the entire objective: request cancellation and storage faults remain RED.
Run only in the independently provisioned parent; never builds or pulls images.
"""
import argparse
import base64
import codecs
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import html
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
SPOOL = '/var/cache/assozeta-message-list'
ROUTE = '/communications/messages/list'
ROWS = 1152
BODY_BYTES = 65536
CHUNK = 65536
MAX_ROW = 256 * 1024
MAX_RESPONSE = 128 * 1024 * 1024
MIN_RESPONSE = 64 * 1024 * 1024
HASH = re.compile(r'[0-9a-f]{64}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
REVISION = re.compile(r'[0-9a-f]{40}\Z')
OWNER = re.compile(r'az-category-image-[0-9a-f]{32}\Z')
SCHEMA = {'applied_migrations': 547, 'remaining_plan': 0, 'mode': 'full-migration-history'}
REMAINING = ['request-cancellation', 'storage-failure']
OWNED = ('TESTS/adapters/go/message_list_spool_image.py',
         'TESTS/test_message_list_spool_image.py',
         'docs/architecture/go-migration/message_list_spool_image.md')
BUILD_KEYS = {'success', 'source_stable', 'source_revision', 'tracked_context_exact',
              'cleanup_verified', 'private_directory_removed', 'errors', 'cleanup_errors',
              'scope', 'source_sha256', 'image_id', 'server_mode', 'server_uid',
              'server_gid', 'packaged_revision', 'server_sha256'}
CLEANUP_KEYS = {'containers', 'network', 'volume', 'fixture', 'cache', 'connections',
                'settings', 'environment', 'private_directory', 'commands'}
REPORT_KEYS = {'format', 'scope', 'owner', 'success', 'phase1_accepted', 'complete_acceptance',
               'incomplete', 'image', 'revision', 'build_receipt_sha256', 'source_sha256',
               'source_sha256_after', 'source_stable', 'runtime_executed', 'schema',
               'package', 'spool', 'lists', 'cleanup', 'cleanup_status', 'failure', 'errors', 'memory'}
PACKAGE_KEYS = {'image', 'revision', 'server_sha256', 'version_sha256', 'user', 'uid',
                'entrypoint', 'read_only', 'tmpfs', 'mounts_verified', 'same_process'}
SPOOL_KEYS = {'driver', 'options', 'ownership', 'filesystem', 'mount_verified',
              'staged_bytes', 'overlap', 'empty_after'}
LIST_KEYS = {'label', 'rows', 'bytes', 'wire_sha256', 'content_sha256', 'expected_sha256'}
FAILURE_STAGES = frozenset({
    'execution-control', 'production-python', 'build-receipt', 'source-binding',
    'private-environment', 'services-startup', 'dependency-postgres-image',
    'dependency-postgres-volumes', 'dependency-redis-image', 'dependency-redis-volumes',
    'packaged-image', 'dependency-images', 'network-create', 'postgres-create',
    'postgres-inspect', 'postgres-readiness', 'redis-create', 'redis-inspect',
    'redis-readiness', 'dependency-mounts', 'runtime-initialization', 'django-setup',
    'settings-override', 'schema-migration', 'fixture-snapshot', 'cache-snapshot',
    'fixture-seed', 'packaged-server', 'request-source-binding', 'list-workload',
    'source-recheck', 'resource-cleanup', 'cleanup-grace',
})
FAILURE_CATEGORIES = frozenset({
    'command-not-found', 'permission-denied', 'command-timeout', 'command-failed',
    'command-output-limit', 'command-descendants', 'metadata-invalid',
    'assertion-failed', 'check-failed', 'interrupted', 'unexpected-error',
})
CLEANUP_STATES = frozenset({'not-attempted', 'verified', 'failed', 'unverified'})


class OperationFailure(RuntimeError):
    """Locally assigned category; never copy subprocess/exception text."""
    def __init__(self, category):
        need(category in FAILURE_CATEGORIES)
        self.category = category
        super().__init__('spool.docker-operation')


def failure_detail(stage, error):
    need(stage in FAILURE_STAGES)
    if isinstance(error, OperationFailure):
        category = error.category
    elif isinstance(error, FileNotFoundError):
        category = 'command-not-found'
    elif isinstance(error, PermissionError):
        category = 'permission-denied'
    elif isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
        category = 'command-timeout'
    elif isinstance(error, subprocess.CalledProcessError):
        category = 'command-failed'
    elif isinstance(error, (KeyError, IndexError, TypeError, ValueError)):
        category = 'metadata-invalid'
    elif isinstance(error, AssertionError):
        category = 'assertion-failed'
    elif isinstance(error, RuntimeError):
        category = 'check-failed'
    elif not isinstance(error, Exception):
        category = 'interrupted'
    else:
        category = 'unexpected-error'
    return {'stage': stage, 'category': category}


def valid_diagnostics(report):
    failure = report['failure']
    return ((failure is None or closed(failure, {'stage', 'category'})
             and type(failure['stage']) is str and failure['stage'] in FAILURE_STAGES
             and type(failure['category']) is str and failure['category'] in FAILURE_CATEGORIES)
            and closed(report['cleanup_status'], CLEANUP_KEYS)
            and all(type(value) is str and value in CLEANUP_STATES
                    for value in report['cleanup_status'].values())
            and all((report['cleanup_status'][key] == 'verified') is report['cleanup'][key]
                    for key in CLEANUP_KEYS))


def cleanup_result(report, key, verified):
    report['cleanup'][key] = verified is True
    report['cleanup_status'][key] = 'verified' if verified is True else 'failed'


def need(condition, code='spool.check'):
    if not condition:
        raise RuntimeError(code)


def require_assertions():
    need(__debug__ and not sys.flags.optimize, 'spool.optimized-python')


def closed(value, keys):
    return type(value) is dict and set(value) == keys


def digest_file(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(CHUNK):
            result.update(chunk)
    return result.hexdigest()


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'spool.duplicate-json-key')
        result[key] = value
    return result


def decode_json(data):
    return json.loads(data, object_pairs_hook=object_pairs,
                      parse_constant=lambda _: need(False, 'spool.json-constant'))


def read_build(path):
    with Path(path).open('rb') as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    need(len(data) <= 4 * 1024 * 1024, 'spool.build-receipt-limit')
    return data


def go_source_hashes(root=ROOT):
    root = Path(root)
    context = root / 'BE_GO'
    need(context.is_dir() and not context.is_symlink(), 'spool.source-root')
    result = {}
    for path in sorted(context.rglob('*')):
        need(not path.is_symlink(), 'spool.source-symlink')
        if path.is_file() and '.git' not in path.relative_to(context).parts:
            result[path.relative_to(root).as_posix()] = digest_file(path)
    need(all('BE_GO/' + name in result for name in (
        'Dockerfile', 'go.mod', 'go.sum', 'cmd/server/main.go', 'THIRD_PARTY_NOTICES.md')),
        'spool.source-inputs')
    return result


def source_digest(root=ROOT):
    """Bind the whole Go context and transitive fixture/runtime inputs, not outputs."""
    root = Path(root)
    manifest = go_source_hashes(root)
    paths = set((root / 'BE').rglob('*'))
    paths.update((root / 'TESTS').rglob('*.py'))
    paths.update((root / 'TESTS/fixtures').rglob('*'))
    paths.update(root / name for name in (*OWNED, 'selfhost/compose.yml',
                                         'selfhost/.env.example'))
    for path in sorted(paths):
        need(not path.is_symlink(), 'spool.source-symlink')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            manifest[path.relative_to(root).as_posix()] = digest_file(path)
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def verify_build_receipt(build, image, revision, manifest):
    require_assertions()
    need(type(image) is str and IMAGE.fullmatch(image), 'spool.immutable-image')
    need(type(revision) is str and REVISION.fullmatch(revision), 'spool.revision')
    need(closed(build, BUILD_KEYS), 'spool.build-schema')
    need(all(build[key] is True for key in ('success', 'source_stable', 'tracked_context_exact',
         'cleanup_verified', 'private_directory_removed')), 'spool.build-incomplete')
    need(build['errors'] == [] and build['cleanup_errors'] == [], 'spool.build-errors')
    need(build['image_id'] == image and build['source_revision'] == revision
         and build['packaged_revision'] == revision, 'spool.build-identity')
    need(type(manifest) is dict and bool(manifest)
         and all(type(k) is str and type(v) is str and HASH.fullmatch(v)
                 for k, v in manifest.items())
         and build['source_sha256'] == manifest, 'spool.build-source')
    need(type(build['server_sha256']) is str and HASH.fullmatch(build['server_sha256'])
         and type(build['scope']) is str and len(build['scope']) < 256,
         'spool.build-binary')
    need(all(type(build[k]) is int for k in ('server_mode', 'server_uid', 'server_gid'))
         and 0 <= build['server_mode'] <= 0o777 and build['server_uid'] >= 0
         and build['server_gid'] >= 0, 'spool.build-mode')
    execute_bit = 0o100 if build['server_uid'] == 10001 else (
        0o010 if build['server_gid'] == 10001 else 0o001)
    need(build['server_mode'] & execute_bit, 'spool.build-executable')
    return build['server_sha256']


def row_id(index):
    return f'a2000000-0000-4000-8000-{900000 + index:012d}'


def body(index):
    prefix = f'owned-spool-row-{index:06d}|'
    trailer = '|<&>"\'|è雪\\\n\t|end'
    fill = BODY_BYTES - len((prefix + trailer).encode('utf-8'))
    return prefix + chr(65 + index % 26) * fill + trailer


def created(index):
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)


def expected_row(index, association):
    return {'message_id': row_id(index), 'sport_association': association,
            'type': 'EMAIL' if index % 2 else 'INSIDE_APP',
            'message': html.escape(body(index), quote=True),
            'subject': html.escape('None' if index % 3 == 0 else f'subject <&> {index}', quote=True),
            'created_at': created(index).isoformat().replace('+00:00', 'Z')}


def canonical(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'


def stream_array(response, *, maximum=MAX_RESPONSE, row_limit=MAX_ROW):
    """Incrementally decode one object; never read/retain an entire LIST.

    Strict array grammar, duplicate-key rejection and byte/row limits also apply
    to controls. UTF-8 may split anywhere, including inside a multibyte character.
    """
    decoder = json.JSONDecoder(object_pairs_hook=object_pairs,
                              parse_constant=lambda _: need(False, 'spool.json-constant'))
    utf8 = codecs.getincrementaldecoder('utf-8')('strict')
    buffer, eof, total = '', False, 0
    wire = hashlib.sha256()
    deadline = time.monotonic() + 180

    def more():
        nonlocal buffer, eof, total
        need(not eof and time.monotonic() < deadline, 'spool.response-deadline')
        chunk = response.read(CHUNK)
        need(type(chunk) is bytes and len(chunk) <= CHUNK, 'spool.response-chunk')
        total += len(chunk)
        need(total <= maximum, 'spool.response-limit')
        wire.update(chunk)
        eof = not chunk
        buffer += utf8.decode(chunk, final=eof)
        need(len(buffer) <= row_limit + CHUNK, 'spool.row-limit')

    def token():
        nonlocal buffer
        buffer = buffer.lstrip(' \t\r\n')
        while not buffer and not eof:
            more()
            buffer = buffer.lstrip(' \t\r\n')
        need(buffer, 'spool.truncated-array')
        return buffer[0]

    need(token() == '[', 'spool.array-required')
    buffer = buffer[1:]
    first = True
    while True:
        if token() == ']':
            buffer = buffer[1:]
            break
        if not first:
            need(token() == ',', 'spool.array-separator')
            buffer = buffer[1:]
            need(token() == '{', 'spool.object-required')
        else:
            need(token() == '{', 'spool.object-required')
        while True:
            try:
                row, end = decoder.raw_decode(buffer)
                need(end <= row_limit and type(row) is dict, 'spool.row-limit')
                buffer = buffer[end:]
                yield row
                break
            except json.JSONDecodeError:
                need(not eof and len(buffer) <= row_limit, 'spool.invalid-row')
                more()
        first = False
    while not eof:
        need(not buffer.strip(' \t\r\n'), 'spool.trailing-data')
        more()
    need(not buffer.strip(' \t\r\n'), 'spool.trailing-data')
    return {'bytes': total, 'wire_sha256': wire.hexdigest()}


def verify_list(response, association, *, count=ROWS, minimum=MIN_RESPONSE):
    need(response.status == 200, 'spool.http-status')
    need(response.getheader('Content-Type', '').split(';')[0] == 'application/json'
         and response.getheader('Content-Encoding') in (None, 'identity'), 'spool.http-type')
    observed, expected = hashlib.sha256(), hashlib.sha256()
    stream = stream_array(response)
    position = 0
    while True:
        try:
            row = next(stream)
        except StopIteration as done:
            evidence = done.value
            break
        need(position < count, 'spool.extra-row')
        authored = expected_row(count - 1 - position, association)
        need(row == authored, 'spool.row-content-order')
        observed.update(canonical(row))
        expected.update(canonical(authored))
        position += 1
    need(position == count and evidence['bytes'] > minimum, 'spool.incomplete-list')
    return {'rows': position, **evidence, 'content_sha256': observed.hexdigest(),
            'expected_sha256': expected.hexdigest()}


def report_template(image, revision):
    return {'format': 1, 'scope': 'packaged-message-list-spool-phase1',
            'owner': None,
            'success': False, 'phase1_accepted': False, 'complete_acceptance': False,
            'incomplete': list(REMAINING), 'image': image, 'revision': revision,
            'build_receipt_sha256': None, 'source_sha256': None, 'source_sha256_after': None,
            'source_stable': False, 'runtime_executed': False, 'schema': None,
            'package': None, 'spool': None, 'lists': [], 'errors': [],
            'cleanup': dict.fromkeys(CLEANUP_KEYS, False),
            'cleanup_status': dict.fromkeys(CLEANUP_KEYS, 'not-attempted'), 'failure': None,
            'memory': {'kind': 'proc1-vmrss-bytes', 'samples': [], 'go_heap': None,
                       'container_total_including_page_cache': None}}


def phase1_acceptance(report, *, build_bytes=None, manifest=None, current_source=None):
    """Closed evidence gate. No default-true fields, coercion or source fallback."""
    try:
        require_assertions()
        need(closed(report, REPORT_KEYS) and type(report['format']) is int and report['format'] == 1)
        need(valid_diagnostics(report) and report['failure'] is None
             and all(value == 'verified' for value in report['cleanup_status'].values()))
        need(type(build_bytes) is bytes and len(build_bytes) <= 4 * 1024 * 1024)
        build = decode_json(build_bytes)
        verify_build_receipt(build, report['image'], report['revision'], manifest)
        need(hashlib.sha256(build_bytes).hexdigest() == report['build_receipt_sha256']
             and current_source == report['source_sha256_after'])
        need(report['scope'] == 'packaged-message-list-spool-phase1'
             and type(report['owner']) is str and OWNER.fullmatch(report['owner'])
             and report['success'] is False and report['complete_acceptance'] is False
             and type(report['phase1_accepted']) is bool and report['incomplete'] == REMAINING)
        need(type(report['image']) is str and IMAGE.fullmatch(report['image'])
             and type(report['revision']) is str and REVISION.fullmatch(report['revision']))
        need(report['runtime_executed'] is True and report['source_stable'] is True
             and report['errors'] == [] and report['source_sha256'] == report['source_sha256_after'])
        need(all(type(report[k]) is str and HASH.fullmatch(report[k])
                 for k in ('source_sha256', 'source_sha256_after', 'build_receipt_sha256')))
        need(closed(report['schema'], set(SCHEMA)) and report['schema'] == SCHEMA
             and type(report['schema']['applied_migrations']) is int
             and type(report['schema']['remaining_plan']) is int)
        need(closed(report['cleanup'], CLEANUP_KEYS)
             and all(value is True for value in report['cleanup'].values()))
        package = report['package']
        need(closed(package, PACKAGE_KEYS) and package['image'] == report['image']
             and package['revision'] == report['revision'] and package['user'] == '10001:10001'
             and package['server_sha256'] == build['server_sha256']
             and type(package['uid']) is int and package['uid'] == 10001
             and package['entrypoint'] == ['/app/server'] and package['read_only'] is True
             and package['tmpfs'] == {'/tmp': 'rw,noexec,nosuid,size=64m'}
             and package['mounts_verified'] is True and package['same_process'] is True
             and all(type(package[k]) is str and HASH.fullmatch(package[k])
                     for k in ('server_sha256', 'version_sha256')))
        spool = report['spool']
        need(closed(spool, SPOOL_KEYS) and spool['driver'] == 'local' and spool['options'] == {}
             and spool['ownership'] == '10001:10001:700' and spool['mount_verified'] is True
             and spool['filesystem'] in ('ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs')
             and type(spool['overlap']) is int and spool['overlap'] == 2 and spool['empty_after'] is True)
        need(type(spool['staged_bytes']) is list and len(spool['staged_bytes']) == 3
             and all(type(n) is int and MIN_RESPONSE < n <= MAX_RESPONSE for n in spool['staged_bytes']))
        lists = report['lists']
        need(type(lists) is list and len(lists) == 3)
        for position, (row, label) in enumerate(zip(lists, ('single', 'concurrent-a', 'concurrent-b'))):
            need(closed(row, LIST_KEYS) and row['label'] == label
                 and type(row['rows']) is int and row['rows'] == ROWS
                 and type(row['bytes']) is int and row['bytes'] == spool['staged_bytes'][position]
                 and MIN_RESPONSE < row['bytes'] <= MAX_RESPONSE)
            need(all(type(row[k]) is str and HASH.fullmatch(row[k])
                     for k in ('wire_sha256', 'content_sha256', 'expected_sha256'))
                 and row['content_sha256'] == row['expected_sha256'])
        need(len({row['wire_sha256'] for row in lists}) == 1
             and len({row['content_sha256'] for row in lists}) == 1)
        memory = report['memory']
        need(closed(memory, {'kind', 'samples', 'go_heap', 'container_total_including_page_cache'})
             and memory['kind'] == 'proc1-vmrss-bytes' and memory['go_heap'] is None
             and memory['container_total_including_page_cache'] is None
             and type(memory['samples']) is list and len(memory['samples']) == 3
             and all(type(n) is int and n > 0 for n in memory['samples']))
        return True
    except (RuntimeError, KeyError, TypeError, ValueError, IndexError):
        return False


def acceptance(report):
    # This version has no cancellation/storage-failure implementation. Relabeling
    # a phase-one receipt cannot make those missing observations exist.
    return False


def publish(path, report):
    """No untrusted strings in a public receipt, even on execution failures."""
    need(closed(report, REPORT_KEYS), 'spool.public-schema')
    need(type(report['image']) is str and IMAGE.fullmatch(report['image'])
         and type(report['revision']) is str and REVISION.fullmatch(report['revision']))
    need(type(report['format']) is int and report['format'] == 1
         and report['scope'] == 'packaged-message-list-spool-phase1'
         and report['incomplete'] == REMAINING and report['success'] is False
         and report['complete_acceptance'] is False)
    need(report['owner'] is None or type(report['owner']) is str and OWNER.fullmatch(report['owner']))
    need(all(type(report[k]) is bool for k in
             ('phase1_accepted', 'source_stable', 'runtime_executed')))
    for key in ('source_sha256', 'source_sha256_after', 'build_receipt_sha256'):
        need(report[key] is None or type(report[key]) is str and HASH.fullmatch(report[key]))
    need(closed(report['cleanup'], CLEANUP_KEYS)
         and all(type(v) is bool for v in report['cleanup'].values()))
    need(valid_diagnostics(report), 'spool.public-diagnostics')
    need(type(report['errors']) is list and len(report['errors']) <= 8
         and all(v in ('spool.execution-failed', 'spool.source-check-failed',
                       'spool.execution-control-failed', 'spool.interrupted',
                       'spool.cleanup-grace-expired') for v in report['errors']))
    need(report['schema'] is None or report['schema'] == SCHEMA)
    package = report['package']
    if package is not None:
        need(closed(package, PACKAGE_KEYS) and package['image'] == report['image']
             and package['revision'] == report['revision'] and package['user'] == '10001:10001'
             and type(package['uid']) is int and package['uid'] == 10001
             and package['entrypoint'] == ['/app/server'] and package['read_only'] is True
             and package['tmpfs'] == {'/tmp': 'rw,noexec,nosuid,size=64m'}
             and package['mounts_verified'] is True and type(package['same_process']) is bool
             and all(type(package[k]) is str and HASH.fullmatch(package[k])
                     for k in ('server_sha256', 'version_sha256')))
    spool = report['spool']
    if spool is not None:
        need(closed(spool, SPOOL_KEYS) and spool['driver'] == 'local' and spool['options'] == {}
             and spool['ownership'] == '10001:10001:700' and spool['mount_verified'] is True
             and spool['filesystem'] in ('ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs')
             and type(spool['overlap']) is int and spool['overlap'] in (0, 2)
             and type(spool['empty_after']) is bool and type(spool['staged_bytes']) is list
             and len(spool['staged_bytes']) <= 3
             and all(type(v) is int and MIN_RESPONSE < v <= MAX_RESPONSE for v in spool['staged_bytes']))
    need(type(report['lists']) is list and len(report['lists']) <= 3)
    for row in report['lists']:
        need(closed(row, LIST_KEYS) and row['label'] in ('single', 'concurrent-a', 'concurrent-b')
             and type(row['rows']) is int and row['rows'] == ROWS
             and type(row['bytes']) is int and MIN_RESPONSE < row['bytes'] <= MAX_RESPONSE
             and all(type(row[k]) is str and HASH.fullmatch(row[k])
                     for k in ('wire_sha256', 'content_sha256', 'expected_sha256')))
    memory = report['memory']
    need(closed(memory, {'kind', 'samples', 'go_heap', 'container_total_including_page_cache'})
         and memory['kind'] == 'proc1-vmrss-bytes' and memory['go_heap'] is None
         and memory['container_total_including_page_cache'] is None
         and type(memory['samples']) is list and len(memory['samples']) <= 3
         and all(type(v) is int and v > 0 for v in memory['samples']))
    raw = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n'
    need(len(raw) <= 32768, 'spool.public-limit')
    # All producer fields are allowlisted and observations are typed; source/build
    # manifests, DB rows, credentials, diagnostics and resource names stay private.
    temporary = path.with_suffix('.tmp')
    with temporary.open('xb') as stream:
        os.chmod(temporary, 0o600)
        stream.write(raw)
    os.replace(temporary, path)


def fixture_class():
    """Lazy imports preserve stdlib-only offline controls and inert imports."""
    from TESTS.adapters.go.payment_category_image import ImageFixture, LABEL
    from TESTS.adapters.go.message_image_execution import defer_interruption

    class SpoolFixture(ImageFixture):
        def __init__(self, *args):
            super().__init__(*args)
            self.volume = self.identifier + '-spool'
            self.volume_attempted = False
            self.command_cleanup = True
            self.owned_ids = {}
            self.network_attempted = False
            self.starting = False
            self.failure = None

        def start_services(self, postgres, redis):
            # The shared helper covers only these data paths with tmpfs. Reject
            # every other image VOLUME before network/container creation, then
            # pass immutable IDs so a mutable local tag cannot bypass preflight.
            self.failure, self.starting = None, True
            try:
                dependencies = []
                for role, requested, path in (('postgres', postgres, '/var/lib/postgresql/data'),
                                               ('redis', redis, '/data')):
                    self.failure_stage = 'dependency-' + role + '-image'
                    image = self.image(requested)
                    need(IMAGE.fullmatch(image['Id']), 'spool.dependency-image')
                    self.failure_stage = 'dependency-' + role + '-volumes'
                    volumes = image['Config'].get('Volumes') or {}
                    need(type(volumes) is dict and set(volumes) <= {path},
                         'spool.dependency-image-volumes')
                    dependencies.append(image['Id'])
                self.failure_stage = 'packaged-image'
                super().start_services(*dependencies)
                self.failure_stage = 'dependency-mounts'
                for name in (self.postgres, self.redis):
                    self.verify_mounts(name, self.owned(name))
            except BaseException as error:
                self.failure = failure_detail(self.failure_stage, error)
                raise
            finally:
                self.starting = False

        def image(self, requested):
            # Inherited startup re-inspects the pinned dependencies after checking
            # the packaged image. Keep that boundary separate from its assertions.
            if (getattr(self, 'starting', False) and self.failure_stage == 'packaged-image'
                    and requested != self.requested_image):
                self.failure_stage = 'dependency-images'
            return super().image(requested)

        def verify_mounts(self, name, value):
            """Forbid implicit storage, including on interrupted create paths.

            No anonymous volume is authorized. Unexpected mounts retain their
            container association for parent recovery; rm -v could delete storage
            whose ownership cannot be established from a container label alone.
            """
            paths = {self.identifier + '-postgres': '/var/lib/postgresql/data',
                     self.identifier + '-redis': '/data'}
            config, host, mounts = value['Config'], value['HostConfig'], value['Mounts']
            need(type(mounts) is list and not host.get('Binds') and not host.get('VolumesFrom'),
                 'spool.unexpected-storage')
            if name in paths:
                path = paths[name]
                volumes = config.get('Volumes') or {}
                need(type(volumes) is dict and set(volumes) <= {path}
                     and set(host['Tmpfs']) == {path} and not host.get('Mounts')
                     and all(m['Type'] == 'tmpfs' and m['Destination'] == path for m in mounts)
                     and len(mounts) <= 1, 'spool.dependency-mounts')
            else:
                need(name == self.identifier + '-server' and not config.get('Volumes')
                     and set(host['Tmpfs']) == {'/tmp'}, 'spool.server-mounts')
                disk = [m for m in mounts if m['Destination'] == SPOOL]
                need(len(disk) == 1 and disk[0]['Type'] == 'volume'
                     and disk[0]['Name'] == self.volume
                     and all(m['Destination'] == SPOOL or
                             m['Destination'] == '/tmp' and m['Type'] == 'tmpfs' for m in mounts)
                     and len(mounts) <= 2, 'spool.server-mounts')
                self.volume_info()

        def docker(self, *args, check=True, timeout=90):
            if args[:2] == ('network', 'create'):
                # Record before daemon mutation, including client failure.
                self.network_attempted = True
            if getattr(self, 'starting', False):
                if args[:2] == ('network', 'create'):
                    self.failure_stage = 'network-create'
                for role in ('postgres', 'redis'):
                    name = self.identifier + '-' + role
                    if args[:1] == ('run',) and '--name' in args and args[args.index('--name') + 1] == name:
                        self.failure_stage = role + '-create'
                    elif args[:2] == ('inspect', name):
                        # Post-start mount verification is one independent gate.
                        if self.failure_stage != 'dependency-mounts':
                            self.failure_stage = role + '-inspect'
                    elif args[:2] == ('exec', name):
                        self.failure_stage = role + '-readiness'
            return self._docker(*args, check=check, timeout=timeout)

        def _docker(self, *args, check=True, timeout=90):
            # The inherited private service lifecycle calls this bounded operation.
            # Never collect docker logs, forward diagnostics or expose command args.
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                process = None
                try:
                    with defer_interruption():
                        process = subprocess.Popen(['docker', *map(str, args)],
                            stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                            start_new_session=True)
                    process.wait(timeout=min(timeout, 90))
                    from TESTS.adapters.go.message_image_build import Commands
                    if Commands.group_exists(process.pid):
                        raise OperationFailure('command-descendants')
                except BaseException as error:
                    if process is not None:
                        from TESTS.adapters.go.message_image_build import Commands
                        try:
                            Commands(self.work).stop_group(process)
                        except BaseException:
                            self.command_cleanup = False
                    raise OperationFailure(failure_detail('services-startup', error)['category']) from None
                if output.tell() > 2 * 1024 * 1024 or errors.tell() > 2 * 1024 * 1024:
                    raise OperationFailure('command-output-limit')
                output.seek(0)
                result = subprocess.CompletedProcess([], process.returncode,
                    output.read().decode('utf-8', 'strict'), '')
                if check and result.returncode != 0:
                    raise OperationFailure('command-failed')
                return result

        def run_container(self, suffix, image, *args):
            # Track attempts before the daemon can create anything. Names are
            # random and ownership is rechecked before every destructive action.
            with defer_interruption():
                name = super().run_container(suffix, image, *args)
                self.owned_ids[name] = self.inspect(name)['Id']
            return name

        def owned(self, name):
            value = self.inspect(name)
            need(value['Config']['Labels'].get(LABEL) == self.identifier
                 and value['Name'] == '/' + name and HASH.fullmatch(value['Id'])
                 and (name not in self.owned_ids or self.owned_ids[name] == value['Id']),
                 'spool.resource-ownership')
            return value

        def exec(self, *args):
            self.owned(self.server_name)
            return self.docker('exec', self.server_name, *args).stdout.strip()

        def volume_info(self):
            value = decode_json(self.docker('volume', 'inspect', self.volume).stdout)[0]
            need(value['Name'] == self.volume and value['Labels'].get(LABEL) == self.identifier
                 and value['Driver'] == 'local' and (value.get('Options') or {}) == {},
                 'spool.volume-ownership')
            return value

        @contextmanager
        def spool_server(self, settings, build, report):
            values = {
                'DBTYPE': 'POSTGRES', 'DBHOST': 'postgres', 'DBPORT': '5432',
                'DBNAME': 'assozeta_contract_category_image', 'DBUSER': 'contracts',
                'DBPASSWORD': 'synthetic-only', 'REDIS_HOST': 'redis', 'REDIS_PORT': '6379',
                'REDIS_USERNAME': 'default', 'REDIS_PASSWORD': 'synthetic-identity-only',
                'GO_LISTEN_ADDR': ':8000', 'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False',
                'DATA_RESTORE_LOCK_PATH': '/tmp/lifecycle.lock', 'GO_MESSAGE_LIST_SPOOL_DIR': SPOOL,
                **{key: getattr(settings, key) for key in (
                    'SECRET_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                    'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL',
                    'AWS_LOCATION', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')},
                **{key + '_B64': base64.b64encode(getattr(settings, key).encode()).decode()
                   for key in ('JWT_SECRET_KEY', 'JWT_PUBLIC_KEY')},
            }
            need(all('\n' not in str(v) and '\r' not in str(v) for v in values.values()))
            envfile = self.work / 'spool.env'
            envfile.touch(mode=0o600, exist_ok=False)
            envfile.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
            self.volume_attempted = True
            self.docker('volume', 'create', '--label', LABEL + '=' + self.identifier, self.volume)
            self.volume_info()
            self.server_name = self.run_container('server', self.image_id,
                '--publish', '127.0.0.1::8000', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
                '--env-file', envfile, '--mount', f'type=volume,source={self.volume},target={SPOOL}')
            try:
                first = self.owned(self.server_name)
                package, spool = inspect_security(first, self.image_id, self.identifier,
                    self.volume, LABEL, self.exec)
                need(package['revision'] == self.revision
                     and package['server_sha256'] == build['server_sha256'], 'spool.package-binding')
                report['package'], report['spool'] = package, spool
                from TESTS.http_client import Client, poll
                base = f'http://127.0.0.1:{self.published_port(self.server_name, 8000)}'

                def ready():
                    need(self.owned(self.server_name)['State']['Running'], 'spool.server-dead')
                    try:
                        return Client(base, timeout=2).request('GET', '/readyz')[0] == 200
                    except OSError:
                        return False

                poll(ready, bool, timeout=30)
                status, health = Client(base).request('GET', '/healthz')
                need(status == 200 and type(health) is dict and set(health) == {'status', 'version'}
                     and health['status'] == 'ok' and hashlib.sha256(health['version'].encode()).hexdigest()
                     == package['version_sha256'], 'spool.health-package')
                yield base
                after = self.owned(self.server_name)
                need(after['State']['Running'] is True and after['RestartCount'] == 0
                     and after['State']['StartedAt'] == first['State']['StartedAt']
                     and after['State']['Pid'] == first['State']['Pid'], 'spool.server-restarted')
                package['same_process'] = True
            finally:
                self.owned(self.server_name)
                self.docker('stop', '--time', '20', self.server_name, timeout=30)
                stopped = self.owned(self.server_name)
                need(stopped['State']['Running'] is False and stopped['State']['ExitCode'] == 0
                     and stopped['RestartCount'] == 0, 'spool.server-stop')

        def cleanup_owned(self, report):
            """No prune, global flush, blanket rm or deletion of an unowned object."""
            clean = report['cleanup']
            failures = False
            for name in reversed(self.containers):
                try:
                    # Successful listing distinguishes absence from inspect failure.
                    found = self.docker('ps', '-a', '--no-trunc', '--quiet', '--filter',
                                        'name=^/' + name + '$').stdout.split()
                    if found:
                        need(len(found) == 1, 'spool.cleanup-ambiguous')
                        value = self.owned(name)
                        need(found == [value['Id']], 'spool.cleanup-replaced')
                        self.verify_mounts(name, value)
                        self.docker('rm', '--force', value['Id'])
                    need(not self.docker('ps', '-a', '--quiet', '--filter',
                                         'name=^/' + name + '$').stdout.strip())
                except BaseException:
                    failures = True
            clean['containers'] = bool(self.containers) and not failures
            try:
                # Also catch a partially created inherited Redis run.
                if self.containers:
                    need(not self.docker('ps', '-a', '--quiet', '--filter',
                                         'label=' + LABEL + '=' + self.identifier).stdout.strip())
            except BaseException:
                clean['containers'] = False
            if self.containers:
                cleanup_result(report, 'containers', clean['containers'])
            try:
                if self.volume_attempted:
                    cleanup_result(report, 'volume', False)
                    found = self.docker('volume', 'ls', '--quiet', '--filter',
                                         'name=^' + self.volume + '$').stdout.split()
                    if found:
                        need(found == [self.volume])
                        self.volume_info()
                        self.docker('volume', 'rm', self.volume)
                    need(not self.docker('volume', 'ls', '--quiet', '--filter',
                                         'name=^' + self.volume + '$').stdout.strip())
                    # Inspect/ownership/mount failures can hide other storage. The
                    # named spool's absence alone cannot establish volume cleanup.
                    cleanup_result(report, 'volume', clean['containers'])
                elif self.containers and not clean['containers']:
                    # A retained dependency may have unverified extra storage.
                    cleanup_result(report, 'volume', False)
            except BaseException:
                pass
            network_attempted = (getattr(self, 'network_attempted', False)
                                 or getattr(self, 'network_created', False) or bool(self.containers))
            try:
                if not network_attempted:
                    return
                cleanup_result(report, 'network', False)
                found = self.docker('network', 'ls', '--quiet', '--no-trunc', '--filter',
                                    'name=^' + self.network + '$').stdout.split()
                if found:
                    need(len(found) == 1, 'spool.cleanup-network-ambiguous')
                    value = decode_json(self.docker('network', 'inspect', self.network).stdout)[0]
                    need(value['Name'] == self.network and value['Labels'].get(LABEL) == self.identifier)
                    need(found == [value['Id']], 'spool.cleanup-network-replaced')
                    self.docker('network', 'rm', self.network)
                # Covers a create interrupted after daemon success but before return.
                remaining = self.docker('network', 'ls', '--quiet', '--filter',
                    'label=' + LABEL + '=' + self.identifier).stdout.strip()
                need(not remaining)
                cleanup_result(report, 'network', True)
            except BaseException:
                pass
            finally:
                cleanup_result(report, 'commands', self.command_cleanup)

    return SpoolFixture


def mount_row(text, destination):
    rows = []
    for line in text.splitlines():
        left, right = line.split(' - ', 1)
        fields, filesystem = left.split(), right.split()
        if fields[4] == destination:
            rows.append({'id': int(fields[0]), 'device': fields[2],
                         'options': fields[5].split(','), 'filesystem': filesystem[0],
                         'super_options': filesystem[2].split(',')})
    need(len(rows) == 1, 'spool.actual-mount')
    return rows[0]


def inspect_security(info, image, owner, volume, label, execute):
    config, host, state = info['Config'], info['HostConfig'], info['State']
    need(info['Image'] == image and config['User'] == '10001:10001'
         and config['Entrypoint'] == ['/app/server'] and not config.get('Cmd')
         and config['Labels'].get(label) == owner and not config.get('Volumes')
         and state['Running'] is True and type(state['Pid']) is int and state['Pid'] > 0
         and info['RestartCount'] == 0, 'spool.container-config')
    need(host['ReadonlyRootfs'] is True and host['Privileged'] is False
         and host['CapDrop'] == ['ALL'] and not host.get('CapAdd')
         and any(v in ('no-new-privileges', 'no-new-privileges:true') for v in host['SecurityOpt'])
         and host['Tmpfs'] == {'/tmp': 'rw,noexec,nosuid,size=64m'}, 'spool.container-security')
    environment = config['Env']
    need(sum(v.startswith('GO_MESSAGE_LIST_SPOOL_DIR=') for v in environment) == 1
         and 'GO_MESSAGE_LIST_SPOOL_DIR=' + SPOOL in environment
         and not any(v.startswith('TMPDIR=') for v in environment), 'spool.container-spool-env')
    mounts = info['Mounts']
    disk = [m for m in mounts if m['Destination'] == SPOOL]
    need(len(disk) == 1 and disk[0]['Type'] == 'volume' and disk[0]['Name'] == volume
         and disk[0]['RW'] is True
         and all(m['Destination'] in (SPOOL, '/tmp') and (m['Type'] == 'tmpfs'
                 if m['Destination'] == '/tmp' else True) for m in mounts), 'spool.volume-mount')
    need(execute('id', '-u') == '10001' and execute('readlink', '/proc/1/exe') == '/app/server',
         'spool.actual-process')
    ownership = execute('stat', '-c', '%u:%g:%a', SPOOL)
    need(ownership == '10001:10001:700', 'spool.directory-ownership')
    actual = execute('cat', '/proc/1/mountinfo')
    disk_mount = mount_row(actual, SPOOL)
    tmp = mount_row(actual, '/tmp')
    root_mount = mount_row(actual, '/')
    need(disk_mount['filesystem'] in ('ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs')
         and 'rw' in disk_mount['options'] and tmp['filesystem'] == 'tmpfs'
         and {'rw', 'nosuid', 'noexec'} <= set(tmp['options'])
         and 'size=65536k' in tmp['super_options'] and 'ro' in root_mount['options'],
         'spool.disk-filesystem')
    revision = execute('cat', '/app/CODE_REVISION')
    binary = execute('sha256sum', '/app/server').split()[0]
    need(REVISION.fullmatch(revision) and HASH.fullmatch(binary), 'spool.package-metadata')
    version = execute('cat', '/app/VERSION')
    need(0 < len(version) < 256, 'spool.package-version')
    package = {'image': image, 'revision': revision, 'server_sha256': binary,
               'version_sha256': hashlib.sha256(version.encode()).hexdigest(),
               'user': config['User'], 'uid': 10001, 'entrypoint': config['Entrypoint'],
               'read_only': True, 'tmpfs': host['Tmpfs'], 'mounts_verified': True, 'same_process': False}
    spool = {'driver': 'local', 'options': {}, 'ownership': ownership,
             'filesystem': disk_mount['filesystem'], 'mount_verified': True,
             'staged_bytes': [], 'overlap': 0, 'empty_after': False}
    return package, spool


FD_SCRIPT = '''for spool_fd in /proc/1/fd/*; do
case "$(readlink "$spool_fd")" in
/var/cache/assozeta-message-list/assozeta-message-list-*" (deleted)")
stat -Lc '%s %i %d %a %u %g' "$spool_fd" || exit 1
sed -n 's/^mnt_id:[[:space:]]*//p' "/proc/1/fdinfo/${spool_fd##*/}" || exit 1
;;
esac
done'''


def observe_spools(proof, expected_count):
    mount = mount_row(proof.exec('cat', '/proc/1/mountinfo'), SPOOL)
    lines = proof.exec('/bin/sh', '-c', FD_SCRIPT).splitlines()
    need(len(lines) == expected_count * 2, 'spool.fd-count')
    observations = []
    for position in range(0, len(lines), 2):
        size, inode, device, mode, uid, gid = map(int, lines[position].split())
        mount_id = int(lines[position + 1])
        need(MIN_RESPONSE < size <= MAX_RESPONSE and mode == 600 and uid == gid == 10001
             and mount_id == mount['id'], 'spool.fd-disk')
        observations.append((size, inode, device))
    need(len({(row[1], row[2]) for row in observations}) == expected_count, 'spool.fd-isolation')
    # A private unlinked spool must never expose a directory entry.
    need(proof.exec('/bin/sh', '-c', 'ls -A ' + SPOOL + ' | wc -l') == '0', 'spool.named-file')
    return observations


def sample_rss(proof, report):
    status = proof.exec('cat', '/proc/1/status')
    match = re.search(r'^VmRSS:\s+([0-9]+) kB$', status, re.M)
    need(match and int(match[1]) > 0, 'spool.rss-unavailable')
    report['memory']['samples'].append(int(match[1]) * 1024)


def wait_empty(proof):
    until = time.monotonic() + 15
    while True:
        lines = proof.exec('/bin/sh', '-c', FD_SCRIPT).strip()
        if not lines:
            observe_spools(proof, 0)
            return
        need(time.monotonic() < until, 'spool.fd-leak')
        time.sleep(0.1)


@contextmanager
def held_list(base, token):
    # Only loopback endpoints published by this owned fixture are accepted.
    need(re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', base), 'spool.endpoint')
    connection = http.client.HTTPConnection('127.0.0.1', int(base.rsplit(':', 1)[1]), timeout=45)
    response = None
    try:
        connection.request('GET', ROUTE, headers={'Authorization': 'Bearer ' + token,
            'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Connection': 'close'})
        response = connection.getresponse()
        need(response.status == 200, 'spool.http-status')
        yield response
    finally:
        if response is not None:
            response.close()
        connection.close()


def run_lists(proof, base, token, association, report):
    with held_list(base, token) as response:
        held = observe_spools(proof, 1)
        report['spool']['staged_bytes'].append(held[0][0])
        sample_rss(proof, report)
        report['lists'].append({'label': 'single', **verify_list(response, association)})
    wait_empty(proof)
    sample_rss(proof, report)
    # Hold both complete staged responses before consuming either. This proves
    # overlapping live descriptors, rather than assuming thread scheduling did so.
    with ExitStack() as stack:
        first = stack.enter_context(held_list(base, token))
        second = stack.enter_context(held_list(base, token))
        held = observe_spools(proof, 2)
        need(len({row[0] for row in held}) == 1, 'spool.concurrent-size')
        report['spool']['staged_bytes'].extend(row[0] for row in held)
        report['spool']['overlap'] = len(held)
        sample_rss(proof, report)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(verify_list, response, association) for response in (first, second)]
            for label, future in zip(('concurrent-a', 'concurrent-b'), futures):
                report['lists'].append({'label': label, **future.result(timeout=200)})
    wait_empty(proof)
    report['spool']['empty_after'] = True


def seed_large(shared, fixture):
    from application.models import SportAssociation
    from communications.models import Message
    association = SportAssociation._base_manager.get(pk=shared._uid(701001))
    for start in range(0, ROWS + 1, 64):
        need(not Message._base_manager.filter(pk__in=[row_id(i) for i in
             range(start, min(start + 64, ROWS + 1))]).exists(), 'spool.seed-collision')
    Message._base_manager.filter(sport_association=association).delete()
    # Deliberately excluded own type plus the existing foreign-owner fixtures
    # distinguish complete authorized LIST from a table dump or leaked tenant.
    Message._base_manager.bulk_create([Message(pk=row_id(ROWS), sport_association=association,
                                              type='SMS', message='excluded own type')])
    for start in range(0, ROWS, 16):
        indices = range(start, min(start + 16, ROWS))
        batch = [Message(pk=row_id(i), sport_association=association,
                         type='EMAIL' if i % 2 else 'INSIDE_APP', message=body(i),
                         subject=None if i % 3 == 0 else f'subject <&> {i}') for i in indices]
        Message._base_manager.bulk_create(batch)
        for i, row in zip(indices, batch):
            row.created_at = created(i)
        Message._base_manager.bulk_update(batch, ['created_at'])
    need(Message._base_manager.filter(sport_association=association,
                                      type__in=('EMAIL', 'INSIDE_APP')).count() == ROWS,
         'spool.seed-count')
    # A DB cursor verifies independent fixture content/order without materializing
    # the response population in this Python process either.
    query = Message._base_manager.filter(sport_association=association,
        type__in=('EMAIL', 'INSIDE_APP')).order_by('-created_at')
    count = 0
    for count, row in enumerate(query.iterator(chunk_size=8), 1):
        i = ROWS - count
        need(str(row.pk) == row_id(i) and row.message == body(i)
             and row.created_at == created(i) and row.type == expected_row(i, str(association.pk))['type']
             and row.subject == (None if i % 3 == 0 else f'subject <&> {i}'), 'spool.seed-content')
    need(count == ROWS, 'spool.seed-count')
    return str(association.pk)


def execute_runtime(proof, work, log, report, build, tools):
    apps = connections = override = None
    raw = fixture = original_state = original_cache = None
    growth_owned = False
    stage = 'runtime-initialization'
    try:
        from TESTS.adapters.django import message_reads_runtime as reuse
        from TESTS.adapters.django import message_reads as shared
        from TESTS.adapters.django import message_read_scopes as scopes
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        environment, _ = reuse.private_environment(work, proof.redis_port, tools)
        environment.update(TESTS_DATABASE='assozeta_contract_category_image', DBNAME='assozeta_contract_category_image',
                           DBHOST='127.0.0.1', DBPORT=str(proof.postgres_port))
        reuse.apply_private_environment(environment)
        # Acquire the lazy DB registry before django.setup: app initialization
        # can itself open connections and fail before returning apps/settings.
        from django.db import connections, transaction
        stage = 'django-setup'
        apps, settings = reuse.initialize_django_apps()
        from django.test.utils import override_settings
        from auditlog.context import disable_auditlog
        import redis
        stage = 'settings-override'
        override = override_settings(CACHES=reuse.cache_override(proof.redis_port))
        override.enable()
        connections.close_all()
        stage = 'schema-migration'
        schema = reuse.migrate_full_schema(log)
        need(schema == SCHEMA, 'spool.migration-schema')
        report['schema'] = dict(SCHEMA)
        stage = 'fixture-snapshot'
        original_state = shared.snapshot_models(apps)
        stage = 'cache-snapshot'
        raw = redis.Redis(host='127.0.0.1', port=proof.redis_port, db=1,
            password='synthetic-identity-only', socket_timeout=5, socket_connect_timeout=5)
        original_cache = snapshot_historical_read_cache(raw)
        stage = 'fixture-seed'
        # Any partial fixture/growth seeding rolls back database writes together.
        with disable_auditlog(), transaction.atomic():
            fixture = shared.seed_fixture(apps, work / 'private-fixture', raw)
            association = seed_large(shared, fixture)
            growth_owned = True
        stage = 'packaged-server'
        with proof.spool_server(settings, build, report) as base:
            # Source recheck immediately before admission, as well as at exit.
            stage = 'request-source-binding'
            need(source_digest() == report['source_sha256'], 'spool.source-before-requests')
            verify_build_receipt(build, proof.image_id, proof.revision, go_source_hashes())
            stage = 'list-workload'
            run_lists(proof, base, fixture['tokens']['owner'], association, report)
            stage = 'packaged-server'
        report['runtime_executed'] = True
    except BaseException as error:
        if report['failure'] is None:
            report['failure'] = failure_detail(stage, error)
        raise
    finally:
        from TESTS.adapters.go.message_image_execution import begin_cleanup
        begin_cleanup()
        if original_state is not None:
            cleanup_result(report, 'fixture', False)
        try:
            if original_state is not None:
                from communications.models import Message
                with disable_auditlog():
                    # Only this adapter's explicit UUID range, in this private DB.
                    if growth_owned:
                        for start in range(0, ROWS + 1, 64):
                            Message._base_manager.filter(pk__in=[row_id(i) for i in
                                range(start, min(start + 64, ROWS + 1))]).delete()
                    if fixture is not None:
                        shared.delete_created(fixture)
                cleanup_result(report, 'fixture', shared.snapshot_models(apps) == original_state)
        except BaseException:
            pass
        if raw is not None:
            cleanup_result(report, 'cache', False)
        try:
            if raw is not None and original_cache is not None:
                cleanup_result(report, 'cache', scopes.restore_cache(raw, original_cache))
        except BaseException:
            pass
        redis_closed, database_closed = raw is None, connections is None
        try:
            if raw is not None:
                raw.close()
                redis_closed = True
        except BaseException:
            pass
        try:
            if connections is not None:
                connections.close_all()
                database_closed = True
        except BaseException:
            pass
        if raw is not None or connections is not None:
            cleanup_result(report, 'connections', redis_closed and database_closed)
        if override is not None:
            cleanup_result(report, 'settings', False)
        try:
            if override is not None:
                # Also attempt disable after a partially failing enable().
                override.disable()
                cleanup_result(report, 'settings', True)
        except BaseException:
            pass


def run(args):
    require_assertions()
    need(not any(name == 'django' or name.startswith('django.') for name in sys.modules),
         'spool.preinitialized-django')
    need(IMAGE.fullmatch(args.image) and REVISION.fullmatch(args.expected_revision), 'spool.arguments')
    output = args.output.resolve()
    need(not any(output.is_relative_to(ROOT / name) for name in ('BE_GO', 'BE', 'TESTS')),
         'spool.output-in-source')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    receipt = output / 'results.json'
    report = report_template(args.image, args.expected_revision)
    publish(receipt, report)  # Fresh failure receipt exists before any service attempt.
    # Imports below are still inert; no Docker call occurs until receipt/source
    # verification and production-interpreter checks have all succeeded.
    from TESTS.adapters.go.message_image_execution import ExecutionDeadline, begin_cleanup
    original_environment = dict(os.environ)
    proof = work = control = None
    build_bytes = None
    stage = 'execution-control'

    def escalate(_):
        report['errors'] = ['spool.cleanup-grace-expired']
        report['phase1_accepted'] = False
        report['cleanup'] = dict.fromkeys(CLEANUP_KEYS, False)
        # Hard grace expiry can interrupt before acquisition bookkeeping or
        # verification finishes. Do not invent attempted or successful cleanup.
        report['cleanup_status'] = dict.fromkeys(CLEANUP_KEYS, 'unverified')
        report['failure'] = report['failure'] or {'stage': 'cleanup-grace', 'category': 'interrupted'}
        publish(receipt, report)

    try:
        control = ExecutionDeadline(args.deadline, args.cleanup_grace, on_escalation=escalate)
        with control:
            try:
                stage = 'production-python'
                from TESTS.adapters.django.production_runtime import require_production_python
                require_production_python()
                stage = 'build-receipt'
                build_bytes = read_build(args.build_receipt)
                report['build_receipt_sha256'] = hashlib.sha256(build_bytes).hexdigest()
                build = decode_json(build_bytes)
                verify_build_receipt(build, args.image, args.expected_revision, go_source_hashes())
                stage = 'source-binding'
                report['source_sha256'] = source_digest()
                verify_build_receipt(build, args.image, args.expected_revision, go_source_hashes())
                stage = 'private-environment'
                with tempfile.TemporaryDirectory(prefix='az-list-spool-', dir='/tmp') as directory:
                    work = Path(directory)
                    work.chmod(0o700)
                    with (work / 'private.log').open('w') as log:
                        with redirect_stdout(log), redirect_stderr(log):
                            from TESTS.adapters.django.message_read_scopes_runtime import owned_tool_environment
                            from TESTS.adapters.django.message_reads_runtime import apply_private_environment
                            tools = owned_tool_environment(original_environment)
                            # Disable Docker client config/plugin/context injection;
                            # parent must provision the default local daemon/socket.
                            docker_home = work / 'docker-config'
                            docker_home.mkdir(mode=0o700)
                            tools['DOCKER_CONFIG'] = str(docker_home)
                            from TESTS.adapters.go.local_docker_endpoint import local_docker_environment
                            tools = local_docker_environment(tools, getattr(args, 'docker_socket', None))
                            apply_private_environment(tools)
                            proof = fixture_class()(args.image, args.expected_revision, work, work, log)
                            report['owner'] = proof.identifier
                            # Retain the opaque ownership label for exact parent
                            # recovery after SIGKILL, before any daemon mutation.
                            publish(receipt, report)
                            try:
                                stage = 'services-startup'
                                proof.start_services(args.postgres, args.redis)
                                need(proof.image_id == args.image, 'spool.resolved-image')
                                stage = 'runtime-initialization'
                                execute_runtime(proof, work, log, report, build, tools)
                            except BaseException as error:
                                report['failure'] = (report['failure'] or proof.failure
                                                     or failure_detail(stage, error))
                                raise
                            finally:
                                begin_cleanup()
                                try:
                                    proof.cleanup_owned(report)
                                except BaseException as error:
                                    report['failure'] = report['failure'] or failure_detail('resource-cleanup', error)
                                    raise
            except BaseException as error:
                report['failure'] = (report['failure'] or getattr(proof, 'failure', None)
                                     or failure_detail(stage, error))
                report['errors'].append('spool.execution-failed')
            finally:
                begin_cleanup()
                os.environ.clear()
                os.environ.update(original_environment)
                cleanup_result(report, 'environment', dict(os.environ) == original_environment)
                if work is not None:
                    cleanup_result(report, 'private_directory', not work.exists())
                try:
                    report['source_sha256_after'] = source_digest()
                    report['source_stable'] = (report['source_sha256_after'] == report['source_sha256']
                        and build_bytes is not None and read_build(args.build_receipt) == build_bytes
                        and go_source_hashes() == build['source_sha256'])
                except BaseException as error:
                    report['failure'] = report['failure'] or failure_detail('source-recheck', error)
                    report['errors'].append('spool.source-check-failed')
    except BaseException as error:
        report['failure'] = report['failure'] or failure_detail('execution-control', error)
        report['errors'].append('spool.execution-control-failed')
    if control is not None and control.interrupted:
        report['failure'] = report['failure'] or {'stage': stage, 'category': 'interrupted'}
        report['errors'].append('spool.interrupted')
    try:
        report['phase1_accepted'] = phase1_acceptance(report,
            build_bytes=read_build(args.build_receipt), manifest=go_source_hashes(),
            current_source=source_digest())
    except BaseException as error:
        report['failure'] = report['failure'] or failure_detail('source-recheck', error)
        report['errors'].append('spool.source-check-failed')
    report['success'] = acceptance(report)
    publish(receipt, report)
    return report


class PrivateArguments(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes arbitrary user-supplied strings.
        raise ValueError('spool.arguments')


def main(argv=None):
    parser = PrivateArguments(description=__doc__)
    parser.add_argument('--docker-socket', help='Explicit existing local Unix socket; excludes ambient contexts')
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deadline', type=float, default=1500)
    parser.add_argument('--cleanup-grace', type=float, default=180)
    try:
        require_assertions()
        args = parser.parse_args(argv)
        report = run(args)
        print(json.dumps({'check': 'message-list-spool.phase1', 'success': False,
                          'phase1_accepted': report['phase1_accepted'], 'incomplete': REMAINING}))
        return 2 if report['phase1_accepted'] else 1
    except SystemExit as error:
        if error.code == 0:  # --help is inert and has no acceptance claim.
            return 0
        print('{"check":"message-list-spool.phase1","success":false,"error":"spool.startup-failed"}')
        return 1
    except BaseException:
        print('{"check":"message-list-spool.phase1","success":false,"error":"spool.startup-failed"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
