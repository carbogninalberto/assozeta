"""Qualify an immutable self-host release candidate and promote its image manifests."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from TESTS.inventory.release_gate import ReleaseGateError, check_release_inventory, load_ledger

IMAGES = ('backend', 'backend-go', 'web', 'renderer', 'updater')
TREES = ('TESTS/handoff', 'selfhost/tests')
BINDINGS = ('commit', 'images', 'bundle_sha256', 'fixtures')
SHARED = {'postgres', 'redis', 'minio', 'renderer', 'web'}
APP = {'django': {'api', 'worker', 'beat', 'realtime-relay'},
       'go': {'api-go', 'worker-go', 'scheduler-go', 'job-worker-go', 'realtime-relay-go'}}
OUTCOMES = ('passed', 'failed', 'skipped', 'error', 'expected_red')
OBS_FIELDS = {'services': ('running',), 'work': ('items',), 'renders': ('renders',),
              'inactive_runtime': ('started', 'running', 'requests'), 'provider_effects': ('effects',)}


def require(condition):
    if not condition:
        raise ValueError('Invalid release evidence')


def fields(obj, *names):
    require(isinstance(obj, dict) and set(obj) == set(names))


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def text(value):
    return isinstance(value, str) and bool(value.strip())


def binding(obj):
    require(type(obj['format']) is int and obj['format'] == 1)
    require(matches('[0-9a-f]{40}', obj['commit']) and matches('[0-9a-f]{64}', obj['bundle_sha256']))
    fields(obj['images'], *IMAGES)
    for ref in obj['images'].values():
        require(matches(r'(?:[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/)?(?:[a-z0-9][a-z0-9._-]*/)*'
                        r'[a-z0-9][a-z0-9._-]*@sha256:[0-9a-f]{64}', ref))
    fields(obj['fixtures'], *TREES)
    require(all(matches('[0-9a-f]{40}', tree) for tree in obj['fixtures'].values()))


def validate_candidate(candidate):
    fields(candidate, 'format', 'version', *BINDINGS)
    binding(candidate)
    require(candidate['version'] is None or text(candidate['version']))


def validate_receipt(receipt):
    fields(receipt, 'format', 'lane', 'run', 'started_at', 'finished_at', 'tests', 'observations', *BINDINGS)
    binding(receipt)
    require(text(receipt['lane']) and matches(r'[a-zA-Z0-9_.-]+', receipt['run']))
    for key in ('started_at', 'finished_at'):
        stamp = receipt[key]
        require(matches(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', stamp))
        datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')
    require(receipt['started_at'] <= receipt['finished_at'])
    require(isinstance(receipt['tests'], dict) and all(text(name) and state in OUTCOMES
                                                       for name, state in receipt['tests'].items()))
    require(isinstance(receipt['observations'], list))


def observation_failures(obs):
    kind, runtime = obs['kind'], obs['runtime']
    fields(obs, 'kind', 'runtime', *OBS_FIELDS[kind])
    require(runtime in APP)
    for key in OBS_FIELDS[kind]:
        if key == 'requests':
            require(type(obs[key]) is int and obs[key] >= 0)
        else:
            require(isinstance(obs[key], list))
        if key in ('running', 'started'):
            require(all(text(service) for service in obs[key]))
    if kind == 'services':
        if (SHARED | APP[runtime]).difference(obs['running']):
            yield 'missing_worker'
    elif kind == 'inactive_runtime':
        if obs['running'] or obs['started'] or obs['requests']:
            yield 'inactive_runtime_call'
    elif kind == 'work':
        if not obs['items']:
            yield 'observation_missing'
        for item in obs['items']:
            fields(item, 'id', 'state', 'owner', 'eta_future')
            require(text(item['id']) and text(item['state']) and isinstance(item['owner'], str)
                    and type(item['eta_future']) is bool)
            future = item['state'] in ('held', 'pending') and item['eta_future']
            if item['owner'] != runtime or not (item['state'] == 'completed' or future):
                yield 'unresolved_work'
    elif kind == 'renders':
        if not obs['renders']:
            yield 'observation_missing'
        for render in obs['renders']:
            fields(render, 'document', 'status', 'reason')
            require(text(render['document']) and render['status'] in ('ok', 'failed')
                    and (render['reason'] is None or isinstance(render['reason'], str)))
            if render['status'] == 'failed':
                yield 'pdf_render_failed'
    elif kind == 'provider_effects':
        seen = set()
        for effect in obs['effects']:
            fields(effect, 'provider', 'item', 'count')
            require(text(effect['provider']) and text(effect['item'])
                    and type(effect['count']) is int and effect['count'] >= 1)
            key = (effect['provider'], effect['item'])
            if key in seen or effect['count'] > 1:
                yield 'duplicate_effect'
            seen.add(key)


def reason(code, lane, detail):
    return {'code': code, 'lane': lane, 'detail': detail}


def lane_reasons(receipt, spec):
    lane = receipt['lane']
    seen = set()
    for obs in receipt['observations']:
        for code in sorted(set(observation_failures(obs))):
            yield reason(code, lane, f"{obs['kind']} ({obs['runtime']})")
        seen.add((obs['kind'], obs['runtime']))
    for required in spec['observations']:
        if (required['kind'], required['runtime']) not in seen:
            yield reason('observation_missing', lane, f"{required['kind']} ({required['runtime']})")
    tests = receipt['tests']
    for entry in spec['tests']:
        found = any(name.startswith(entry[:-1]) for name in tests) if entry.endswith('*') else entry in tests
        if not found:
            yield reason('test_missing', lane, entry)
    for name, outcome in tests.items():
        if outcome != 'passed':
            yield reason('test_not_passed', lane, name)


def evaluate(candidate, receipts, *, matrix, expected_red, ledger, root):
    reasons, hashes, seen = [], {}, set()
    try:
        validate_candidate(candidate)
    except ValueError:
        reasons.append(reason('candidate_invalid', None, 'Invalid candidate schema'))
    require(matrix.get('format') == 1 and isinstance(matrix.get('lanes'), dict) and matrix['lanes'])
    require(expected_red.get('format') == 1 and isinstance(expected_red.get('tests'), dict))
    lanes = matrix['lanes']
    for receipt in receipts:
        name = receipt.get('lane') if isinstance(receipt, dict) else None
        if not isinstance(name, str) or name not in lanes or name in seen:
            reasons.append(reason('receipt_invalid', None, 'Unknown or duplicate lane'))
            continue
        seen.add(name)
        try:
            validate_receipt(receipt)
            if not isinstance(candidate, dict) or any(receipt[k] != candidate.get(k) for k in BINDINGS):
                reasons.append(reason('receipt_mismatch', name, 'Receipt binding differs from the candidate'))
                continue
            failures = list(lane_reasons(receipt, lanes[name]))
        except (ValueError, TypeError, KeyError):
            reasons.append(reason('receipt_invalid', name, 'Invalid receipt or observation schema'))
            continue
        reasons.extend(failures)
        encoded = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode()
        hashes[name] = hashlib.sha256(encoded).hexdigest()
    for name, spec in lanes.items():
        if spec.get('open'):
            reasons.append(reason('lane_open', name, 'Release lane is open'))
        elif name not in seen:
            reasons.append(reason('lane_missing', name, 'No receipt for required lane'))
    if expected_red['tests']:
        reasons.append(reason('expected_red_open', None, 'Expected-red manifest has entries'))
    try:
        check_release_inventory(load_ledger(ledger), root)
    except ReleaseGateError as exc:
        reasons.append(reason('ledger_open', None, str(exc)))
    return {'format': 1, 'candidate': candidate, 'qualified': not reasons, 'reasons': reasons, 'receipts': hashes}


def published_mismatches(qualification, published):
    return sorted(name for name, ref in qualification['candidate']['images'].items() if published.get(name) != ref)


def checkout(root):
    def git(*args):
        return subprocess.check_output(['git', '-C', root, *args], text=True).strip()
    require(not git('status', '--porcelain', '--untracked-files=all'))
    return git('rev-parse', 'HEAD'), {tree: git('rev-parse', 'HEAD:' + tree) for tree in TREES}


def read(path):
    def unique_keys(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs))
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_keys)


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2) + '\n')


def bundle_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def candidate_command(args):
    commit, fixtures = checkout(args.root)
    images = {name: (args.images / name).read_text().strip() for name in IMAGES}
    candidate = dict(format=1, commit=commit, fixtures=fixtures, version=args.version, images=images,
                     bundle_sha256=bundle_sha(args.bundle))
    validate_candidate(candidate)
    write(args.output, candidate)


def receipt_command(args):
    candidate = read(args.candidate)
    validate_candidate(candidate)
    tests = {}
    for path in args.report:
        report = read(path)
        require(report['revision'] == candidate['commit'] and report['tests'])
        for name, item in report['tests'].items():
            require(name not in tests)
            state = item['state']
            if state.startswith('violation:'):
                tests[name] = 'failed'
            else:
                tests[name] = {'pass': 'passed', 'expected-red': 'expected_red'}[state]
    observations = []
    for path in args.observations:
        document = read(path)
        require(document['lane'] == args.lane)
        require(isinstance(document['observations'], list))
        observations.extend(document['observations'])
    receipt = {key: candidate[key] for key in BINDINGS}
    receipt.update(format=1, lane=args.lane, run=args.run, started_at=args.started_at, finished_at=args.finished_at,
                   tests=tests, observations=observations)
    validate_receipt(receipt)
    write(args.output, receipt)


def check_command(args):
    candidate = read(args.candidate)
    receipts = [read(path) for path in sorted(args.receipts.glob('*.json'))]
    result = evaluate(candidate, receipts, matrix=read(args.matrix), expected_red=read(args.expected_red),
                      ledger=args.ledger, root=args.root)
    try:
        require(checkout(args.root) == (candidate['commit'], candidate['fixtures']))
    except (ValueError, TypeError, KeyError, subprocess.CalledProcessError):
        result['reasons'].append(reason('candidate_mismatch', None, 'Candidate does not match a clean checkout'))
        result['qualified'] = False
    write(args.output, result)
    for failure in result['reasons']:
        print(f"{failure['lane'] or 'release'}: {failure['code']}: {failure['detail']}", file=sys.stderr)
    return int(not result['qualified'])


def publication_command(args):
    qualification = read(args.qualification)
    require(qualification['qualified'] is True and qualification['reasons'] == [] and qualification['receipts'])
    candidate = qualification['candidate']
    validate_candidate(candidate)
    require(text(candidate['version']))
    version = candidate['version'].removeprefix('v')
    require(matches(r'[0-9][A-Za-z0-9._-]{0,127}', version))
    images = candidate['images']
    buildx = ['docker', 'buildx', 'imagetools']
    if args.command == 'promote':
        source = images[args.image]
        repo = source.split('@')[0]
        tags = args.tags.splitlines()
        require(f'{repo}:{version}' in tags)
        options = []
        for tag in tags:
            require(tag.startswith(repo + ':') and matches(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,127}', tag[len(repo)+1:]))
            options.extend(['--tag', tag])
        subprocess.run([*buildx, 'create', '--prefer-index=false', *options, source], check=True)
    else:
        published = {}
        for name, ref in images.items():
            repo = ref.split('@')[0]
            digest = subprocess.check_output([*buildx, 'inspect', f'{repo}:{version}',
                                              '--format', '{{json .Manifest.Digest}}'], text=True)
            published[name] = f'{repo}@{json.loads(digest)}'
        different = published_mismatches(qualification, published)
        if different:
            raise ValueError('Published image mismatch: ' + ', '.join(different))
        if args.bundle:
            require(bundle_sha(args.bundle) == candidate['bundle_sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    candidate = commands.add_parser('candidate')
    receipt = commands.add_parser('receipt')
    check = commands.add_parser('check')
    promote = commands.add_parser('promote')
    verify = commands.add_parser('verify-published')
    for cmd in (candidate, check):
        cmd.add_argument('--root', type=Path, default=ROOT)
    for cmd in (candidate, receipt, check):
        cmd.add_argument('--output', type=Path, required=True)
    for cmd in (receipt, check):
        cmd.add_argument('--candidate', type=Path, required=True)
    for cmd in (promote, verify):
        cmd.add_argument('--qualification', type=Path, required=True)
    candidate.add_argument('--images', type=Path, required=True, help='directory with one digest reference per image')
    candidate.add_argument('--bundle', type=Path, required=True)
    candidate.add_argument('--version', required=True)
    receipt.add_argument('--lane', required=True)
    receipt.add_argument('--run', required=True)
    receipt.add_argument('--started-at', required=True)
    receipt.add_argument('--finished-at', required=True)
    receipt.add_argument('--report', type=Path, nargs='+', required=True)
    receipt.add_argument('--observations', type=Path, action='append', default=[])
    check.add_argument('--receipts', type=Path, required=True)
    check.add_argument('--matrix', type=Path, default=ROOT / 'TESTS/handoff/release_matrix.json')
    check.add_argument('--expected-red', type=Path, default=ROOT / 'TESTS/handoff/expected_red.json')
    check.add_argument('--ledger', type=Path, default=ROOT / 'docs/architecture/go-migration/ledger.json')
    promote.add_argument('--image', choices=IMAGES, required=True)
    promote.add_argument('--tags', required=True, help='newline-separated fully qualified release tags')
    verify.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    handlers = {'candidate': candidate_command, 'receipt': receipt_command, 'check': check_command,
                'promote': publication_command, 'verify-published': publication_command}
    try:
        return handlers[args.command](args)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Release evidence check failed: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
