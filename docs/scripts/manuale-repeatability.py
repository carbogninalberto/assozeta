#!/usr/bin/env python3
"""Compare completed real runs without erasing their original provenance.

Only run/capture nonces, preview locations, rotated fixture credentials and
screenshot bytes are excluded from semantic fingerprints. Screenshot bytes
are independently hash-checked and their pixels measured at the 0.1% limit.
This checks the seed's public fixture contract, not a complete database dump.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('manual_repeatability_index', ROOT / 'BE/application/manuale/index.py')
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)


def read(root, relative):
    return json.loads(index.owned_path(root, relative).read_text())


def fingerprint(value):
    return index.digest(index.canonical(value))


def snapshot_input(value):
    return {key: value.get(key) for key in ('revision', 'state', 'uncommitted_files')}


def fixture_contract(value):
    value = copy.deepcopy(value)
    for key in ('origin', 'login_password', 'token', 'refresh_token'):
        value.pop(key, None)
    for identity in value.get('identities', {}).values():
        for key in ('token', 'refresh_token'):
            identity.pop(key, None)
    return value


def fixture_identity(root, state):
    seed = 'BE/application/management/commands/seed_manuale.py'
    source_hash = state['tooling_hashes'][seed]
    private = index.owned_path(root, 'browser-input.json')
    if private.exists():
        value = fixture_contract(read(root, 'browser-input.json'))
        if value['reference_date'] != state['reference_date']:
            raise ValueError('Fixture date differs from recorded inputs')
        return {'format': 1, 'scope': 'seed-public-contract', 'seed_sha256': source_hash,
                'fixture_version': value['fixture_version'], 'reference_date': value['reference_date'],
                'identity': fingerprint(value)}
    recorded = read(root, 'fixture-identity.json')
    if (recorded.get('format') != 1 or recorded.get('scope') != 'seed-public-contract'
            or recorded.get('seed_sha256') != source_hash
            or recorded.get('reference_date') != state['reference_date']):
        raise ValueError('Retained fixture identity differs from recorded inputs')
    return recorded


def capture_semantics(capture):
    value = copy.deepcopy(capture)
    value.pop('sha256', None)
    value.pop('url', None)
    if 'master' in value:
        value['master'].pop('sha256', None)
    if 'stability' in value:
        value['stability'].pop('sha256', None)
    return value


def report_semantics(report):
    value = copy.deepcopy(report)
    value.pop('capture_id', None)
    value['screenshots'] = [capture_semantics(item) for item in value['screenshots']]
    return value


def corpus_semantics(value):
    chunks = []
    for chunk in value['chunks']:
        chunk = copy.deepcopy(chunk)
        chunk.pop('url', None)
        for origin in chunk.get('capture_provenance', []):
            origin.pop('capture_id', None)
            origin.pop('report_sha256', None)
        chunk['screenshots'] = [capture_semantics(item) for item in chunk['screenshots']]
        chunks.append(chunk)
    metadata = value['metadata']
    return {'format': value['format'], 'embedding': value['embedding'],
            'application_revision': metadata['application_revision'],
            'manual_revision': metadata['manual_revision'], 'release': metadata['release'],
            'dependencies': value['dependencies'], 'chunks': chunks,
            'gaps': value['gaps'], 'semantics': value['semantics']}


def load_run(root):
    root = Path(root).resolve()
    state = read(root, 'run.json')
    if state.get('status') != 'evaluated':
        raise ValueError('Repeatability requires a completed evaluated run')
    corpus = index.ManualIndex.load(index.owned_path(root, 'index.json')).value
    metadata = corpus['metadata']
    if (metadata['application_revision'] != state['application_input']['revision']
            or metadata['manual_revision'] != state['manual_input']['revision']
            or metadata['release'] != state['release']):
        raise ValueError('Corpus differs from recorded source inputs')
    for name in ('render', 'evaluation', 'embedded-manual'):
        report = read(root, name + '.json')
        if report.get('status') != 'passed':
            raise ValueError('Repeatability requires passed ' + name)
        if name == 'evaluation' and report.get('corpus_identity') != corpus['identity']:
            raise ValueError('Evaluation belongs to another corpus')
        if name == 'embedded-manual' and report.get('backend') != 'real':
            raise ValueError('Reader was not verified against the real backend')
    manifest = read(root, 'manifest.json')
    if manifest['metadata'] != corpus['metadata']:
        raise ValueError('Manifest and corpus inputs differ')
    scenarios, captures, originals = {}, {}, {}
    for scenario in manifest['scenarios']:
        path = index.owned_path(root, scenario['report_path'])
        if index.file_digest(path) != scenario['report_sha256']:
            raise ValueError('Scenario report bytes differ from manifest')
        report = json.loads(path.read_text())
        if (report.get('status') != 'passed' or report.get('backend') != 'real'
                or report['id'] != scenario['id']):
            raise ValueError('Repeatability requires passed real scenarios')
        if report['id'] in scenarios:
            raise ValueError('Duplicate scenario')
        scenarios[report['id']] = report_semantics(report)
        originals[report['id']] = {'capture_id': report.get('capture_id'),
                                  'report_sha256': scenario['report_sha256']}
        for capture in report['screenshots']:
            index.verify_capture_frame(root, capture)
            for relative, record in [('captures/' + capture['path'], capture),
                                     (capture['master']['path'], capture['master'])]:
                key = report['id'] + ':' + relative
                if key in captures:
                    raise ValueError('Duplicate screenshot checkpoint')
                captures[key] = (relative, record)
    if not scenarios or not captures or not corpus['chunks']:
        raise ValueError('Repeatability requires nonempty workflow, screenshot and corpus evidence')
    for chunk in corpus['chunks']:
        for image in chunk['screenshots']:
            if not any(relative == 'captures/' + image['path'] and record['sha256'] == image['sha256']
                       for relative, record in captures.values()):
                raise ValueError('Corpus screenshot differs from retained captures')
        for origin in chunk.get('capture_provenance', []):
            if origin['scenario_id'] not in originals or any(origin.get(key) !=
                    originals[origin['scenario_id']][key] for key in ('capture_id', 'report_sha256')):
                raise ValueError('Corpus evidence differs from retained scenario provenance')
    inputs = {'application': snapshot_input(state['application_input']),
              'manual': snapshot_input(state['manual_input']), 'release': state['release'],
              'reference_date': state['reference_date'], 'tooling_hashes': state['tooling_hashes'],
              'selected_recipes': state['selected_recipes'], 'manual_url': state['manual_url']}
    identities = {'inputs': fingerprint(inputs), 'fixture': fingerprint(fixture_identity(root, state)),
                  'semantic_corpus': fingerprint(corpus_semantics(corpus)),
                  'semantic_evidence': fingerprint(scenarios)}
    provenance = {'run_id': state['run_id'], 'corpus_identity': corpus['identity'],
                  'content_identity': corpus['content_identity'], 'scenarios': originals,
                  'artifact_sha256': {key: record['sha256'] for key, (_, record) in captures.items()}}
    return root, identities, provenance, captures


def compare(before, after):
    import numpy as np
    from PIL import Image
    left, right = load_run(before), load_run(after)
    checks = {key: left[1][key] == right[1][key] for key in left[1]}
    pixels = []
    for key in sorted(set(left[3]) | set(right[3])):
        entry = {'checkpoint': key}
        if key not in left[3] or key not in right[3]:
            entry.update(status='checkpoint-changed')
        else:
            arrays = []
            for root, _, _, captures in (left, right):
                with Image.open(index.owned_path(root, captures[key][0])) as image:
                    arrays.append(np.asarray(image.convert('RGBA')))
            if arrays[0].shape != arrays[1].shape:
                entry.update(status='dimensions-changed')
            else:
                fraction = float(np.any(arrays[0] != arrays[1], axis=2).mean())
                entry.update(status='stable' if fraction <= .001 else 'changed',
                             changed_pixel_fraction=round(fraction, 10),
                             identical_bytes=left[3][key][1]['sha256'] == right[3][key][1]['sha256'])
        pixels.append(entry)
    passed = all(checks.values()) and all(item['status'] == 'stable' for item in pixels)
    return {'format': 1, 'status': 'passed' if passed else 'failed', 'checks': checks,
            'normalized_identities': {'before': left[1], 'after': right[1]},
            'original_provenance': {'before': left[2], 'after': right[2]},
            'maximum_changed_pixel_fraction': .001, 'screenshots': pixels,
            'fixture_scope': 'Public seed contract and seed implementation; no complete database digest is claimed.',
            'normalization': ['Run/capture nonces and preview URLs remain in original provenance.',
                              'Fixture password/JWT credentials rotate and are excluded.',
                              'Capture hashes remain in original provenance; pixels are separately checked.',
                              'All other scenario outcomes, download hashes, code evidence and corpus content remain exact.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    fixture = subparsers.add_parser('fixture', help='Record a public fixture fingerprint before cleanup removes credentials')
    fixture.add_argument('--run', required=True)
    comparison = subparsers.add_parser('compare', help='Compare two complete runs on identical source inputs')
    comparison.add_argument('--before', required=True)
    comparison.add_argument('--after', required=True)
    comparison.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'fixture':
        root = Path(args.run).resolve()
        result = fixture_identity(root, read(root, 'run.json'))
        index.promote(result, root / 'fixture-identity.json')
    else:
        result = compare(args.before, args.after)
        index.promote(result, args.output)
    print(json.dumps({'status': result.get('status', 'recorded'), 'format': result['format']}))
    return 0 if result.get('status', 'passed') == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
