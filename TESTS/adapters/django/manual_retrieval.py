"""Offline actual Django sealed-model retrieval and ordinary reader projection.

The fixture stores the already-trained model; neither runtime retrains it. This
oracle directly calls ManualIndex.search/applicable and tools._public, with an
explicit public context. It does not execute authentication, HTTP, source-mode
selection, MCP, an assistant or any filesystem/remote refresh.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT/'BE')]
from TESTS.adapters.django.production_runtime import require_production_python


def main():
    require_production_python()
    import numpy
    expected_numpy = next(line.split('==', 1)[1] for line in
                          (ROOT/'docs/manuale/requirements-offline.txt').read_text().splitlines()
                          if line.startswith('numpy=='))
    if numpy.__version__ != expected_numpy:
        raise RuntimeError('Use the pinned offline NumPy runtime for retrieval evidence')
    from application.manuale import index as implementation
    from application.manuale.tools import _public
    fixture_path = ROOT/'TESTS/fixtures/manual-reader-retrieval.json'
    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    output = ROOT/'quality-reports/go-migration/manual-retrieval'
    output.mkdir(parents=True, exist_ok=True)
    (output/'results.json').write_text(json.dumps({'success': False, 'scope': __doc__}))
    cases = []
    with patch.object(socket, 'socket', side_effect=AssertionError('offline retrieval attempted network')), \
            patch.object(implementation, 'fit_semantics', side_effect=AssertionError('runtime retrained sealed model')):
        for tc in fixture['tokens']:
            assert implementation.tokens(tc['text']) == tc['expected'], tc
            assert sorted(implementation.operations(tc['text'])) == tc['operations'], tc
        for spec in fixture['cases']:
            value = copy.deepcopy(spec.get('index', fixture['index']))
            before = copy.deepcopy(value)
            index = implementation.ManualIndex(value)
            index.asset_root = '/unused/read-only' if spec['asset_urls'] else None
            context = dict(revision='fixture-r1', release='fixture-v1', features=spec['features'], maintainer=False)
            applicable = index.applicable(**context)
            failed = False
            try:
                hits = index.search(spec['query'], limit=10, **context) if spec['query'] else applicable
                results = []
                for chunk in hits:
                    public = _public(chunk, index)
                    public['url'] = public['embedded_url']
                    assert not {'secret', 'capture_provenance', 'evidence', 'runtime_dependencies', 'lexical', 'semantic'} & public.keys()
                    results.append(public)
                assert [c['id'] for c in hits] == spec['expected_ids'], spec['name']
                assert [c.get('score') for c in hits] == spec['expected_scores'], spec['name']
                response = dict(status='verified' if results else 'no_evidence', results=results,
                                message='' if results else 'Non ho trovato istruzioni verificate per questa domanda.')
            except (ValueError, KeyError, TypeError):
                failed = True
                response = None
            assert failed == spec.get('expected_error', False), spec['name']
            assert value == before, 'Django changed sealed input'
            cases.append(dict(name=spec['name'], index=value, applicable=applicable,
                              asset_urls=spec['asset_urls'], query=spec['query'], expected=response, expected_error=failed))
    assert fixture_path.read_bytes() == raw
    (output/'django.json').write_text(json.dumps(dict(success=True, cases=cases, tokens=fixture['tokens']), ensure_ascii=False, indent=2)+'\n')
    with tempfile.TemporaryDirectory(prefix='az-manual-retrieval-') as directory:
        manifest = Path(directory)/'oracle.json'
        manifest.write_text(json.dumps(dict(cases=cases, tokens=fixture['tokens']), ensure_ascii=False))
        before = manifest.read_bytes()
        result = subprocess.run(['go', 'test', '-race', '-tags', 'manualretrievalintegration', './pkg/manual',
                                 '-run', '^TestManualRetrieval', '-count=1', '-v'], cwd=ROOT/'BE_GO',
                                env={**os.environ, 'ASSOZETA_MANUAL_RETRIEVAL_FIXTURE': str(manifest)},
                                capture_output=True, text=True, timeout=180)
        (output/'go.log').write_text(result.stdout+result.stderr)
        print(result.stdout, result.stderr)
        result.check_returncode()
        assert manifest.read_bytes() == before and fixture_path.read_bytes() == raw
    import django
    import numpy
    report = dict(success=True, scope=__doc__, cases=len(cases), token_cases=len(fixture['tokens']),
                  runtime=dict(python=sys.version.split()[0], django=django.get_version(), numpy=numpy.__version__),
                  private_directory_removed=not Path(directory).exists(), file_effects='unchanged',
                  network='Django denied; Go pure computation only', source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in [fixture_path, Path(__file__), ROOT/'BE/application/manuale/index.py', ROOT/'BE/application/manuale/tools.py']})
    (output/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
