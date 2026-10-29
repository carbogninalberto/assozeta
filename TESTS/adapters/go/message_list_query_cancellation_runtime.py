"""Parent-only packaged LIST workload plus query cancellation; never full acceptance.

Imports are inert and stdlib only. No builds, pulls or alternate service lifecycle.
Run with the production Python in a separately provisioned parent environment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
SOURCE_FILES = (
    'TESTS/adapters/go/message_list_spool_image.py',
    'TESTS/test_message_list_spool_image.py',
    'docs/architecture/go-migration/message_list_spool_image.md',
    'TESTS/adapters/go/message_list_query_cancellation.py',
    'TESTS/test_message_list_query_cancellation.py',
    'docs/architecture/go-migration/message_list_query_cancellation.md',
    'TESTS/adapters/go/local_docker_endpoint.py',
    'TESTS/test_local_docker_endpoint.py',
    'TESTS/adapters/go/message_list_query_cancellation_runtime.py',
    'TESTS/test_message_list_query_cancellation_runtime.py',
    'docs/architecture/go-migration/message_list_query_cancellation_runtime.md',
)
REMAINING = ['post-header-response-abandonment', 'spool-create-permission-denial',
             'spool-enospc', 'spool-write-failure', 'spool-read-failure',
             'new-storage-failure-during-final-copy']
HASH = re.compile(r'[0-9a-f]{64}\Z')
REPORT_KEYS = {'format', 'scope', 'success', 'complete_acceptance',
               'query_cancellation_accepted', 'phase1_accepted', 'incomplete',
               'image', 'revision', 'build_receipt_sha256', 'phase1_receipt_sha256',
               'source_before', 'source_after', 'source_stable', 'workload',
               'query_cancellation', 'query_cancellation_sha256', 'final_cleanup_verified', 'failure'}
FAILURES = {'runtime.incomplete', 'runtime.execution-failed', 'runtime.interrupted',
            'runtime.evidence-rejected'}
RECEIPT_LIMIT = 32768


def _modules():
    from TESTS.adapters.go import message_list_spool_image, message_list_query_cancellation
    return message_list_spool_image, message_list_query_cancellation


def _need(condition):
    if not condition:
        raise RuntimeError('runtime.check-failed')


def _closed(value, keys):
    return type(value) is dict and set(value) == keys


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def query_digest(evidence):
    """Canonical JSON link; originals of phase1/build are hashed as raw bytes."""
    return sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False).encode('ascii'))


def source_binding(root=ROOT):
    """Live application digest plus explicit harness/test/doc/endpoint hashes."""
    phase, _ = _modules()
    root = Path(root).resolve()
    files = {}
    for name in SOURCE_FILES:
        path = root / name
        _need(all(not parent.is_symlink() for parent in (path, *path.parents)))
        _need(path.is_file())
        files[name] = phase.digest_file(path)
    query_source = (root / 'BE_GO/internal/repository/message_reads.go').read_text()
    matches = re.findall(r'(?m)^const messageReadsListSQL = `([^`]+)`', query_source)
    _need(len(matches) == 1)
    return {'source_sha256': phase.source_digest(root), 'files': files,
            'query_sha256': sha256(matches[0].encode())}


def _source_valid(value):
    return (_closed(value, {'source_sha256', 'files', 'query_sha256'})
            and _hash(value['source_sha256']) and _hash(value['query_sha256'])
            and _closed(value['files'], set(SOURCE_FILES))
            and all(_hash(v) for v in value['files'].values()))


def _workload_valid(value):
    phase, _ = _modules()
    return (_closed(value, {'image', 'revision', 'owner', 'source', 'build_receipt_sha256'})
            and type(value['image']) is str and phase.IMAGE.fullmatch(value['image'])
            and type(value['revision']) is str and phase.REVISION.fullmatch(value['revision'])
            and type(value['owner']) is str and phase.OWNER.fullmatch(value['owner'])
            and _source_valid(value['source']) and _hash(value['build_receipt_sha256']))


def report_template(image, revision):
    return {'format': 1, 'scope': 'packaged-message-list-query-cancellation-runtime',
            'success': False, 'complete_acceptance': False,
            'query_cancellation_accepted': False, 'phase1_accepted': False,
            'incomplete': list(REMAINING), 'image': image, 'revision': revision,
            'build_receipt_sha256': None, 'phase1_receipt_sha256': None,
            'source_before': None, 'source_after': None, 'source_stable': False,
            'workload': None, 'query_cancellation': None, 'query_cancellation_sha256': None,
            'final_cleanup_verified': False, 'failure': 'runtime.incomplete'}


def _report_valid(report):
    """Allow only fixed diagnostics and strictly safe evidence, including RED."""
    phase, cancel = _modules()
    return (_closed(report, REPORT_KEYS) and type(report['format']) is int
            and report['format'] == 1
            and report['scope'] == 'packaged-message-list-query-cancellation-runtime'
            and report['success'] is False and report['complete_acceptance'] is False
            and report['incomplete'] == REMAINING and type(report['incomplete']) is list
            and type(report['image']) is str and phase.IMAGE.fullmatch(report['image'])
            and type(report['revision']) is str and phase.REVISION.fullmatch(report['revision'])
            and all(type(report[k]) is bool for k in ('query_cancellation_accepted',
                'phase1_accepted', 'source_stable', 'final_cleanup_verified'))
            and (report['failure'] is None or type(report['failure']) is str
                 and report['failure'] in FAILURES)
            and all(report[k] is None or _hash(report[k]) for k in
                ('build_receipt_sha256', 'phase1_receipt_sha256', 'query_cancellation_sha256'))
            and all(report[k] is None or _source_valid(report[k]) for k in
                ('source_before', 'source_after'))
            and (report['workload'] is None or _workload_valid(report['workload']))
            and (report['query_cancellation'] is None or cancel.validate_query_cancellation(report['query_cancellation'])))


def bounded_acceptance(report, *, phase1_bytes=None, build_bytes=None,
                       manifest=None, current_source=None):
    """Pure independent gate; no file reads, source fallback or old-receipt path.

    Caller must supply original receipt bytes and a freshly computed live source
    binding/Go manifest. Schema validity alone is never execution provenance.
    """
    try:
        phase, cancel = _modules()
        phase.require_assertions()
        _need(_report_valid(report) and report['failure'] is None)
        _need(all(report[k] is True for k in ('query_cancellation_accepted',
              'phase1_accepted', 'source_stable', 'final_cleanup_verified')))
        _need(type(phase1_bytes) is bytes and len(phase1_bytes) <= RECEIPT_LIMIT)
        _need(type(build_bytes) is bytes and len(build_bytes) <= 4 * 1024 * 1024)
        _need(sha256(phase1_bytes) == report['phase1_receipt_sha256']
              and sha256(build_bytes) == report['build_receipt_sha256'])
        _need(_source_valid(current_source) and report['source_before'] == current_source
              and report['source_after'] == current_source)
        original = phase.decode_json(phase1_bytes)
        _need(phase.phase1_acceptance(original, build_bytes=build_bytes, manifest=manifest,
              current_source=current_source['source_sha256']) is True)
        _need(original['phase1_accepted'] is True
              and original['image'] == report['image'] and original['revision'] == report['revision']
              and original['build_receipt_sha256'] == report['build_receipt_sha256'])
        _need(report['workload'] == {'image': report['image'], 'revision': report['revision'],
              'owner': original['owner'], 'source': current_source,
              'build_receipt_sha256': report['build_receipt_sha256']})
        evidence = report['query_cancellation']
        _need(cancel.validate_query_cancellation(evidence)
              and query_digest(evidence) == report['query_cancellation_sha256'])
        _need(evidence['query_sha256'] == current_source['query_sha256'])
        # Each complete original LIST is the oracle, including its exact size.
        recovery = {key: evidence['recovery_' + key] for key in
                    ('rows', 'bytes', 'wire_sha256', 'content_sha256', 'expected_sha256')}
        for row in original['lists']:
            _need(recovery == {key: value for key, value in row.items() if key != 'label'})
        return True
    except (RuntimeError, KeyError, TypeError, ValueError, IndexError,
            OverflowError, RecursionError):
        return False


def _publish(path, report):
    _need(_report_valid(report))
    raw = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n'
    _need(len(raw) <= RECEIPT_LIMIT)
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
    os.replace(temporary, path)


def _read_phase1(path):
    with path.open('rb') as stream:
        data = stream.read(RECEIPT_LIMIT + 1)
    _need(len(data) <= RECEIPT_LIMIT)
    return data


def run(args):
    phase, cancel = _modules()
    _need(type(args.image) is str and phase.IMAGE.fullmatch(args.image)
          and type(args.expected_revision) is str and phase.REVISION.fullmatch(args.expected_revision))
    requested_output = Path(args.output)
    _need(not requested_output.is_symlink())
    output = requested_output.resolve()
    _need(not any(output.is_relative_to(ROOT / name) for name in ('BE_GO', 'BE', 'TESTS', 'docs')))
    # Reserve a fresh directory atomically. Neither an existing receipt nor an
    # existing empty directory is adopted. phase.run gets its own fresh child.
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    receipt = output / 'query-cancellation.json'
    report = report_template(args.image, args.expected_revision)
    _publish(receipt, report)  # Remains RED even on hard interruption later.
    original = phase.run_lists
    try:
        phase.require_assertions()
        from TESTS.adapters.go.message_image_execution import bounded_seconds
        bounded_seconds(args.deadline, 3600)
        bounded_seconds(args.cleanup_grace, 300)
        from TESTS.adapters.django.production_runtime import require_production_python
        require_production_python()  # Before any phase.run/service/fixture work.
        build_bytes = phase.read_build(args.build_receipt)
        report['build_receipt_sha256'] = sha256(build_bytes)
        phase.verify_build_receipt(phase.decode_json(build_bytes), args.image,
                                  args.expected_revision, phase.go_source_hashes())
        source = source_binding()
        report['source_before'] = source
        invoked = False

        def workload(proof, base, token, association, phase_report):
            nonlocal invoked
            _need(not invoked)
            invoked = True
            def verify_binding():
                _need(source_binding() == source
                      and phase.read_build(args.build_receipt) == build_bytes)
                _need(proof.image_id == args.image and proof.revision == args.expected_revision
                      and proof.identifier == phase_report['owner']
                      and phase_report['source_sha256'] == source['source_sha256']
                      and phase_report['build_receipt_sha256'] == sha256(build_bytes))

            verify_binding()
            # Extend orchestration only. The original workload and both existing
            # acceptance functions are called unchanged.
            original(proof, base, token, association, phase_report)
            verify_binding()
            observed = cancel.run_query_cancellation(proof, base, token, association)
            _need(cancel.validate_query_cancellation(observed))
            verify_binding()
            report['workload'] = {'image': proof.image_id, 'revision': proof.revision,
                'owner': proof.identifier, 'source': source,
                'build_receipt_sha256': sha256(build_bytes)}
            report['query_cancellation'] = observed
            report['query_cancellation_sha256'] = query_digest(observed)

        phase_args = argparse.Namespace(**vars(args))
        phase_args.output = output / 'phase1'
        phase.run_lists = workload
        try:
            returned = phase.run(phase_args)
        finally:
            phase.run_lists = original
        _need(invoked)
        phase1_bytes = _read_phase1(phase_args.output / 'results.json')
        original_report = phase.decode_json(phase1_bytes)
        _need(returned == original_report)
        report['phase1_receipt_sha256'] = sha256(phase1_bytes)
        report['source_after'] = source_binding()
        report['source_stable'] = (source == report['source_after']
                                  and phase.read_build(args.build_receipt) == build_bytes)
        # Independently evaluate the original gate after all owned teardown.
        report['phase1_accepted'] = (original_report['phase1_accepted'] is True
            and phase.phase1_acceptance(original_report, build_bytes=build_bytes,
                manifest=phase.go_source_hashes(),
                current_source=report['source_after']['source_sha256']))
        report['final_cleanup_verified'] = report['phase1_accepted']
        report['failure'] = None
        report['query_cancellation_accepted'] = True  # Candidate, not yet published.
        report['query_cancellation_accepted'] = bounded_acceptance(report,
            phase1_bytes=phase1_bytes, build_bytes=phase.read_build(args.build_receipt),
            manifest=phase.go_source_hashes(), current_source=source_binding())
        if not report['query_cancellation_accepted']:
            report['failure'] = 'runtime.evidence-rejected'
    except BaseException as error:
        report['query_cancellation_accepted'] = False
        report['failure'] = ('runtime.interrupted' if not isinstance(error, Exception)
                             else 'runtime.execution-failed')
    finally:
        phase.run_lists = original
    _publish(receipt, report)
    return report


class PrivateArguments(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('runtime.arguments')


def main(argv=None):
    parser = PrivateArguments(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--docker-socket', help='Explicit existing local Unix socket')
    parser.add_argument('--postgres', default='postgres:16-alpine')
    parser.add_argument('--redis', default='redis:7-alpine')
    parser.add_argument('--deadline', type=float, default=1500)
    parser.add_argument('--cleanup-grace', type=float, default=180)
    try:
        report = run(parser.parse_args(argv))
        accepted = report['query_cancellation_accepted'] is True
        print(json.dumps({'check': 'message-list-query-cancellation.runtime', 'success': False,
                          'complete_acceptance': False, 'query_cancellation_accepted': accepted,
                          'incomplete': REMAINING}))
        return 2 if accepted else 1
    except SystemExit as error:
        if error.code == 0:
            return 0
    except BaseException:
        pass
    print('{"check":"message-list-query-cancellation.runtime","success":false,'
          '"complete_acceptance":false,"query_cancellation_accepted":false,'
          '"error":"runtime.startup-failed"}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
