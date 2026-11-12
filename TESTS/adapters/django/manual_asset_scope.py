"""Offline differential for installed manual asset source scoping.

The expected bytes and denials live in TESTS/fixtures/manual-asset-scope.json
and are checked before either backend observation is accepted. This adapter
uses the actual installed-bundle loader and production manual_asset core in
Django, then runs the actual Go Loader.ReadAsset against the same private
fixture. It intentionally excludes HTTP authentication, preview, providers,
databases, containers and network clients.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'BE')]
from TESTS.adapters.django.production_runtime import require_production_python


FIXTURE = ROOT / 'TESTS/fixtures/manual-asset-scope.json'
ADAPTER = Path(__file__).resolve()
GO_TEST = ROOT / 'BE_GO/pkg/manual/asset_scope_oracle_integration_test.go'
HELPER = ROOT / 'TESTS/adapters/django/manual_reader_library.py'
ARTIFACT_NAMES = ('shared', 'target', 'feature', 'private', 'draft', 'unrelated')


def digest(value):
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def write(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def file_hashes(root, files=None):
    root = Path(root)
    paths = files if files is not None else sorted(path for path in root.rglob('*') if path.is_file())
    return {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in paths}


def source_payload(name):
    # The production reader validates the .png suffix and capture digest. This
    # deterministic header-valid payload keeps the asset identities distinct.
    return b'\x89PNG\r\n\x1a\n' + (13).to_bytes(4, 'big') + b'IHDR' + (1).to_bytes(4, 'big') * 2 + name.encode()


def prepare_runtime_python():
    require_production_python()
    from django.conf import settings
    settings.configure()


def install_value(bundle, value, seal):
    value = dict(value)
    value.pop('identity', None)
    value['identity'] = seal(value)
    index = bundle / 'index.json'
    write(index, canonical(value))
    receipt = {
        'format': 1,
        'distribution': 'immutable-backend-image',
        'index_sha256': digest(index.read_bytes()),
        'corpus_identity': value['identity'],
        'application_revision': value['metadata']['application_revision'],
        'release': value['metadata']['release'],
        'manual_revision': value['metadata']['manual_revision'],
        'verified_corpus_identity': value['metadata']['verification']['corpus_identity'],
    }
    write(bundle / 'bundle.json', canonical(receipt))
    return value, receipt


def build_fixture(directory, spec):
    from TESTS.adapters.django.manual_reader_library import prepare
    from application.manuale.index import canonical, digest

    def seal(value):
        return digest(canonical(value))

    root = directory / 'fixture'
    base = root / 'base'
    root.mkdir()
    prepare(base, {
        'name': 'base',
        'mutation': '',
        'features': [],
        'source': True,
        'asset_path': 'images/z.png',
        'expected': {'asset': 'base'},
    })
    bundle, source = base / 'bundle', base / 'source'
    value = json.loads((bundle / 'index.json').read_text())
    write(source / 'BE/unrelated.py', b'original unrelated source')

    artifacts = {name: source_payload(name) for name in ARTIFACT_NAMES}
    assert {name: digest(value) for name, value in artifacts.items()} == {
        name: specification['sha256'] for name, specification in spec['artifacts'].items()
    }, 'generated asset bytes differ from authored fixture'
    image_root = bundle / 'images'
    for name in ('shared', 'target', 'feature', 'private', 'draft', 'unrelated'):
        write(image_root / f'{name}.png', artifacts[name])
    write(image_root / 'tampered.png', b'tampered capture bytes')

    def evidence(relative):
        return [{'path': relative, 'sha256': digest((source / relative).read_bytes())}]

    def capture(name):
        return {'path': f'images/{name}.png', 'sha256': digest(artifacts[name])}

    def chunk(identifier, audience, status, features, source_path, screenshots):
        return {
            'id': identifier,
            'audience': audience,
            'status': status,
            'features': features,
            'evidence': evidence(source_path),
            'runtime_dependencies': {},
            'screenshots': screenshots,
        }

    def invalid_source_chunk(identifier, audience, source_path, screenshots):
        return {
            'id': identifier,
            'audience': audience,
            'status': 'verified',
            'features': [],
            'evidence': [{'path': source_path, 'sha256': '0' * 64}],
            'runtime_dependencies': {},
            'screenshots': screenshots,
        }

    value['dependencies'] = {
        'BE/core/authentication.py': digest((source / 'BE/core/authentication.py').read_bytes()),
        'BE/unrelated.py': digest((source / 'BE/unrelated.py').read_bytes()),
    }
    value['chunks'] = [
        chunk('public-target', 'public', 'verified', [], 'BE/z.py', [capture('target')]),
        chunk('public-shared-a', 'public', 'verified', [], 'BE/a.py', [capture('shared')]),
        chunk('public-shared-b', 'public', 'verified', [], 'BE/runtime.py', [capture('shared')]),
        chunk('public-feature', 'public', 'verified', ['self_hosted'], 'BE/host.py', [capture('feature')]),
        chunk('maintainer-private', 'maintainer', 'verified', [], 'BE/private.py', [capture('private')]),
        chunk('public-draft', 'public', 'pending', [], 'BE/draft.py', [capture('draft')]),
        chunk('public-unrelated', 'public', 'verified', [], 'BE/unrelated.py', [capture('unrelated')]),
        chunk('public-missing', 'public', 'verified', [], 'BE/z.py', [{'path': 'images/missing.png', 'sha256': '0' * 64}]),
        chunk('public-tampered', 'public', 'verified', [], 'BE/z.py', [{'path': 'images/tampered.png', 'sha256': digest(artifacts['target'])}]),
        chunk('public-no-hash', 'public', 'verified', [], 'BE/z.py', [{'path': 'images/no-hash.png'}]),
    ]
    value, _ = install_value(bundle, value, seal)

    variant_root = root / 'variants'

    def variant(name, edit, invalid_seal=False, bad_receipt=False):
        target = variant_root / name
        shutil.copytree(bundle, target)
        index_path, receipt_path = target / 'index.json', target / 'bundle.json'
        candidate = json.loads(index_path.read_text())
        receipt = json.loads(receipt_path.read_text())
        edit(candidate, receipt)
        if invalid_seal:
            candidate['unexpected'] = True
        else:
            candidate = dict(candidate)
            candidate.pop('identity', None)
            candidate['identity'] = seal(candidate)
        write(index_path, canonical(candidate))
        receipt['index_sha256'] = digest(index_path.read_bytes())
        receipt['corpus_identity'] = candidate['identity']
        if bad_receipt:
            receipt['corpus_identity'] = '0' * 64
        write(receipt_path, canonical(receipt))

    def inaccessible_bad(candidate, _):
        candidate['chunks'].append(invalid_source_chunk(
            'inaccessible-bad-source', 'maintainer', '../inaccessible-escape.py',
            [capture('target')],
        ))

    def unrelated_bad(candidate, _):
        candidate['chunks'].append(invalid_source_chunk(
            'unrelated-bad-source', 'public', '../unrelated-escape.py',
            [capture('unrelated')],
        ))

    def malformed_image(candidate, _):
        candidate['chunks'].append(chunk(
            'malformed-image-record', 'public', 'verified', [], 'BE/unrelated.py', [42],
        ))

    def invalid_seal(candidate, _):
        candidate['unexpected'] = True

    def invalid_receipt(_, receipt):
        receipt['verified_corpus_identity'] = '0' * 64

    variant('inaccessible-bad-source', inaccessible_bad)
    variant('unrelated-bad-source', unrelated_bad)
    variant('malformed-image-structure', malformed_image)
    variant('invalid-index-seal', invalid_seal, invalid_seal=True)
    variant('bad-receipt', invalid_receipt, bad_receipt=True)
    variant('strict', lambda *_: None)
    return root, base, source, bundle, value, artifacts


def runtime(bundle, source, revision, release, features):
    return {
        'Settings': {
            'BundlePath': str(bundle),
            'BundleRequired': True,
            'IndexPath': '',
            'AssetRoot': '',
            'CorpusBaseURL': '',
            'DeploymentMode': 'production',
            'ApplicationRevision': revision,
            'Release': release,
            'RunID': '',
            'SourceRoot': str(source),
            'DevelopmentRoot': '',
        },
        'Features': list(features),
    }


def files_changed(before, after):
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def observe_django(namespace, case):
    from django.http import Http404
    from application.manuale.bundle import load_bundle

    settings = case['Settings']
    context = {
        'revision': settings['ApplicationRevision'],
        'release': settings['Release'],
        'features': case['Features'],
        'maintainer': False,
        'code_root': settings['SourceRoot'] or None,
    }

    def load(_):
        index = load_bundle(
            settings['BundlePath'],
            revision=context['revision'],
            release=context['release'],
            required=True,
        )
        return index, context

    namespace['_load'] = load
    try:
        response = namespace['manual_asset'](None, case['AssetPath'])
        contents = b''.join(response.streaming_content)
        response.close()
        assert response['Content-Type'] == 'image/png'
        assert response['Cache-Control'] == 'private, no-store'
        assert response['X-Content-Type-Options'] == 'nosniff'
        artifact = next((name for name, expected in namespace['_artifacts'].items() if expected == contents), None)
        assert artifact is not None, 'asset returned unreviewed bytes'
        return {'access': 'bytes', 'artifact': artifact}
    except Http404:
        return {'access': 'denied'}


def original_source(source, source_bytes):
    return {relative: base64.b64decode(encoded) for relative, encoded in source_bytes.items()}


def write_contents(source, writes, originals):
    contents = {}
    for relative, operation in writes.items():
        contents[relative] = originals[relative] if operation == 'original' else f'changed {relative}\n'.encode()
    return contents


def run_django_sequence(namespace, root, source, case, sequence, originals):
    before = file_hashes(root)
    outcomes = {}
    for stage in sequence['stages']:
        contents = write_contents(source, stage['writes'], originals)
        prewrite = file_hashes(root)
        for relative, data in contents.items():
            write(source / relative, data)
        after_write = file_hashes(root)
        expected_changes = sorted(
            f'{source.relative_to(root).as_posix()}/{relative}' for relative in contents
        )
        assert files_changed(prewrite, after_write) == expected_changes, (
            'undeclared fixture mutation', stage['name'], files_changed(prewrite, after_write), expected_changes
        )
        for check in stage['checks']:
            observed = observe_django(namespace, {**case, 'AssetPath': check['asset_path']})
            assert observed == check['expected'], (sequence['name'], stage['name'], observed, check['expected'])
            outcomes[f"sequence/{sequence['name']}/{stage['name']}/{check['asset_path']}"] = observed
        assert file_hashes(root) == after_write, ('asset read changed files', sequence['name'], stage['name'])
    assert file_hashes(root) == before, ('mutation sequence did not restore fixture', sequence['name'])
    return outcomes


def source_receipt(go_module_root):
    repo_files = [
        FIXTURE,
        ADAPTER,
        GO_TEST,
        HELPER,
        *sorted((ROOT / 'BE/application/manuale').glob('*.py')),
    ]
    go_files = [go_module_root / 'go.mod', go_module_root / 'go.sum']
    go_files.extend(sorted((go_module_root / 'pkg/manual').glob('*.go')))
    return {
        'repository_root': str(ROOT),
        'repository_files': {
            path.relative_to(ROOT).as_posix(): digest(path.read_bytes()) for path in repo_files
        },
        'go_module_root': str(go_module_root),
        'go_files': {
            path.relative_to(go_module_root).as_posix(): digest(path.read_bytes()) for path in go_files
        },
    }


def compare_source_receipt(receipt):
    for root, files in [
        (Path(receipt['repository_root']), receipt['repository_files']),
        (Path(receipt['go_module_root']), receipt['go_files']),
    ]:
        for relative, expected in files.items():
            assert digest((root / relative).read_bytes()) == expected, f'source changed during proof: {relative}'


def make_manifest(root, base, source, bundle, metadata, spec, artifacts, go_module_root):
    source_bytes = {
        path.relative_to(source).as_posix(): base64.b64encode(path.read_bytes()).decode()
        for path in sorted(source.rglob('*')) if path.is_file()
    }
    cases = []
    for case in spec['cases']:
        name = case.get('bundle_variant')
        case_bundle = root / 'variants' / name if name else bundle
        cases.append({
            'Name': case['name'],
            'AssetPath': case['asset_path'],
            'Expected': case['expected'],
            **runtime(
                case_bundle,
                source,
                case.get('revision', metadata['application_revision']),
                case.get('release', metadata['release']),
                case['features'],
            ),
        })
    sequences = []
    originals = original_source(source, source_bytes)
    for sequence in spec['sequences']:
        stages = []
        for stage in sequence['stages']:
            writes = {
                relative: base64.b64encode(data).decode()
                for relative, data in write_contents(source, stage['writes'], originals).items()
            }
            stages.append({
                'Name': stage['name'],
                'Writes': writes,
                'Checks': [
                    {'AssetPath': check['asset_path'], 'Expected': check['expected']}
                    for check in stage['checks']
                ],
            })
        sequences.append({
            'Name': sequence['name'],
            'Expected': {'access': 'bytes'},
            **runtime(bundle, source, metadata['application_revision'], metadata['release'], sequence['features']),
            'Stages': stages,
        })

    fixture_files = file_hashes(root)
    artifact_hashes = {name: digest(value) for name, value in artifacts.items()}
    receipt = {
        'format': 1,
        'fixture_spec_sha256': digest(FIXTURE.read_bytes()),
        'case_definition_sha256': digest(canonical({'cases': spec['cases'], 'sequences': spec['sequences']})),
        'fixture_files': fixture_files,
        'source_files': {path: digest(value) for path, value in originals.items()},
        'artifact_sha256': artifact_hashes,
        **source_receipt(go_module_root),
    }
    return {
        'FixtureRoot': str(root),
        'SourceRoot': str(source),
        'SourceBytes': source_bytes,
        'Artifacts': {name: base64.b64encode(value).decode() for name, value in artifacts.items()},
        'InitialFiles': fixture_files,
        'InputReceipt': receipt,
        'InputReceiptCanonical': canonical(receipt),
        'InputReceiptSha256': digest(canonical(receipt)),
        'Cases': cases,
        'Sequences': sequences,
    }


def run_go(directory, manifest, go_module_root):
    module_copy = None
    if go_module_root == (ROOT / 'BE_GO').resolve():
        module = go_module_root
    else:
        module_copy = directory / 'go-module'
        shutil.copytree(go_module_root, module_copy, symlinks=True)
        shutil.copy2(GO_TEST, module_copy / 'pkg/manual/asset_scope_oracle_integration_test.go')
        module = module_copy
    manifest_path = directory / 'manifest.json'
    result_path = directory / 'go-results.json'
    write(manifest_path, json.dumps(manifest, ensure_ascii=False))
    environment = {
        **os.environ,
        'GOCACHE': os.environ.get('GOCACHE', str(directory / 'go-cache')),
        'GOTMPDIR': str(directory),
        'ASSOZETA_MANUAL_ASSET_SCOPE_FIXTURE': str(manifest_path),
        'ASSOZETA_MANUAL_ASSET_SCOPE_RESULT': str(result_path),
    }
    command = [
        'go', 'test', '-race', '-count=1', '-tags', 'manualassetscopeintegration',
        './pkg/manual', '-run', '^TestManualAssetScopeDifferential$', '-json',
    ]
    completed = subprocess.run(command, cwd=module, env=environment, capture_output=True, text=True, timeout=240)
    if completed.returncode != 0:
        print(completed.stdout, completed.stderr, file=sys.stderr)
    completed.check_returncode()
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert not any(event.get('Action') == 'skip' for event in events), 'differential test skipped'
    prefix = 'TestManualAssetScopeDifferential/'
    tested = {
        event['Test'][len(prefix):] for event in events
        if event.get('Action') == 'pass' and event.get('Test', '').startswith(prefix)
    }
    expected = {
        *(f"case/{case['Name']}" for case in manifest['Cases']),
        *(f"sequence/{sequence['Name']}" for sequence in manifest['Sequences']),
    }
    assert expected.issubset(tested), ('missing differential subtests', sorted(expected - tested))
    return json.loads(result_path.read_text()), module_copy is not None


def validate_global_controls(namespace, root, base, source, metadata, originals):
    from application.manuale.bundle import load_bundle
    from application.manuale.index import EvidenceError

    strict_bundle = root / 'variants/strict'
    case = {
        **runtime(strict_bundle, source, metadata['application_revision'], metadata['release'], []),
        'AssetPath': 'images/target.png',
    }
    assert observe_django(namespace, case) == {'access': 'bytes', 'artifact': 'target'}
    initial = file_hashes(root)
    write(source / 'BE/unrelated.py', b'changed unrelated source')
    assert observe_django(namespace, case) == {'access': 'bytes', 'artifact': 'target'}
    strict_index = load_bundle(
        strict_bundle,
        revision=metadata['application_revision'],
        release=metadata['release'],
        required=True,
    )
    try:
        strict_index.verify_unchanged_dependencies(source)
    except EvidenceError:
        strict_source_rejected = True
    else:
        strict_source_rejected = False
    assert strict_source_rejected, 'global source validation was weakened'
    write(source / 'BE/unrelated.py', originals['BE/unrelated.py'])
    assert file_hashes(root) == initial

    try:
        load_bundle(
            base / 'bundle',
            revision=metadata['application_revision'],
            release=metadata['release'],
            required=True,
            verify_assets=True,
        )
    except (OSError, ValueError, KeyError, TypeError):
        strict_assets_rejected = True
    else:
        strict_assets_rejected = False
    assert strict_assets_rejected, 'global bundle asset validation was weakened'
    return {
        'global_source_validation_rejected': strict_source_rejected,
        'global_bundle_asset_validation_rejected': strict_assets_rejected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go-module-root', type=Path, default=ROOT / 'BE_GO')
    options = parser.parse_args()
    initial_sources = source_receipt(options.go_module_root.resolve())
    prepare_runtime_python()
    from TESTS.adapters.django.manual_reader_library import asset_core

    spec = json.loads(FIXTURE.read_text())
    go_module_root = options.go_module_root.resolve()
    assert (go_module_root / 'go.mod').is_file(), 'Go module root required'
    with tempfile.TemporaryDirectory(prefix='az-manual-asset-scope-') as directory_name:
        directory = Path(directory_name)
        root, base, source, bundle, value, artifacts = build_fixture(directory, spec)
        metadata = value['metadata']
        source_bytes = {
            path.relative_to(source).as_posix(): base64.b64encode(path.read_bytes()).decode()
            for path in sorted(source.rglob('*')) if path.is_file()
        }
        originals = original_source(source, source_bytes)
        initial = file_hashes(root)
        namespace = asset_core()
        namespace['_artifacts'] = artifacts
        django_observed = {}
        with patch.object(socket, 'socket', side_effect=AssertionError('Django asset oracle attempted network')):
            for case in spec['cases']:
                name = case.get('bundle_variant')
                case_bundle = root / 'variants' / name if name else bundle
                runtime_case = {
                    **runtime(
                        case_bundle,
                        source,
                        case.get('revision', metadata['application_revision']),
                        case.get('release', metadata['release']),
                        case['features'],
                    ),
                    'AssetPath': case['asset_path'],
                }
                before = file_hashes(root)
                observed = observe_django(namespace, runtime_case)
                assert observed == case['expected'], (case['name'], observed, case['expected'])
                assert file_hashes(root) == before, ('asset read changed files', case['name'])
                django_observed[f"case/{case['name']}"] = observed
            for sequence in spec['sequences']:
                sequence_case = runtime(
                    bundle,
                    source,
                    metadata['application_revision'],
                    metadata['release'],
                    sequence['features'],
                )
                django_observed.update(run_django_sequence(
                    namespace, root, source, sequence_case, sequence, originals,
                ))
        assert file_hashes(root) == initial, 'Django observations did not restore exact fixture'
        controls = validate_global_controls(namespace, root, base, source, metadata, originals)
        manifest = make_manifest(root, base, source, bundle, metadata, spec, artifacts, go_module_root)
        assert file_hashes(root) == manifest['InitialFiles']
        go_observed, used_copy = run_go(directory, manifest, go_module_root)
        differing = sorted(
            key for key in set(django_observed) | set(go_observed)
            if django_observed.get(key) != go_observed.get(key)
        )
        assert not differing, ('Django and Go asset observations differ', differing)
        assert file_hashes(root) == initial, 'Go observations changed fixture files'
        compare_source_receipt(manifest['InputReceipt'])
        assert source_receipt(go_module_root) == initial_sources, 'Proof source set changed during observation'
        cases = len(spec['cases']) + sum(
            len(stage['checks']) for sequence in spec['sequences'] for stage in sequence['stages']
        )
        report = {
            'success': True,
            'cases': cases,
            'fixture_cases': len(spec['cases']),
            'mutation_sequences': len(spec['sequences']),
            'backend': 'actual Django manual_asset core versus actual Go Loader.ReadAsset',
            'same_fixture': True,
            'explicit_expectations': True,
            'django_and_go_equal': go_observed == django_observed,
            'file_effects': 'unchanged except declared reversible source mutations',
            'network': 'Django denied; Go library has no network client',
            **controls,
            'input_receipt_sha256': manifest['InputReceiptSha256'],
            'source_sha256': manifest['InputReceipt']['repository_files'],
            'go_source_sha256': manifest['InputReceipt']['go_files'],
            'go_used_private_module_copy': used_copy,
        }
    report['cleanup_verified'] = not Path(directory_name).exists()
    assert report['cleanup_verified']
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
