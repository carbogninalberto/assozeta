#!/usr/bin/env python3
"""Export and import integrity-checked portable manual verification baselines.

The archive is capture evidence, not an installable manual or release proof.
GitHub producer authenticity is deliberately outside this portable boundary.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FORMAT = 2
PURPOSE = 'capture-cache-not-release'
PUBLIC_STATE = ('format', 'run_id', 'capture_id', 'status', 'release', 'reference_date',
                'application_input', 'manual_input', 'tooling_hashes', 'selected_recipes',
                'evidence_reuse', 'render_reuse')
PUBLIC_STATE_KEYS = set(PUBLIC_STATE) | {'application', 'manual', 'run'}
PUBLIC_STATE_REQUIRED = PUBLIC_STATE_KEYS - {'evidence_reuse', 'render_reuse', 'capture_id'}
PUBLIC_INPUT_KEYS = {'revision', 'state', 'snapshot_kind', 'checkout_kind', 'uncommitted_files'}
MAX_MEMBERS = 100_000
MAX_MEMBER_BYTES = 2 * 1024 ** 3
MAX_EXPANDED_BYTES = 6 * 1024 ** 3
MAX_ARCHIVE_BYTES = 4 * 1024 ** 3
MAX_JSON_BYTES = 128 * 1024 ** 2
HASH = re.compile(r'[a-f0-9]{64}\Z')
REVISION = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\Z')
PRIVATE_NAMES = {'browser-input.json', 'execution.log', 'preview.log', 'diagnostics.json',
                 '.npmrc', '.pypirc', '.netrc', '.pgpass', 'auth.json', 'credentials',
                 'credentials.json', 'service-account.json', 'kubeconfig',
                 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'}
PRIVATE_SUFFIXES = ('.pem', '.key', '.p12', '.pfx')
PUBLIC_FRONTEND_COMMAND = ['npm', 'exec', 'vite', 'preview', '--', '--host', '0.0.0.0',
                           '--port', '5001', '--strictPort']
PUBLIC_FRONTEND_ENVIRONMENT = {'DEPLOY_ENV': 'development', 'OEM_ENV': 'assozeta',
                               'ASSOZETA_MANUAL_BUILD': '1'}


def _digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _digest_file(path, *, maximum=MAX_MEMBER_BYTES):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise ValueError('Cache member is unreasonably large: ' + str(path))
            digest.update(chunk)
    return digest.hexdigest(), size


def _private_path(relative):
    parts = PurePosixPath(relative).parts
    for part in parts:
        if part == '.git' or part in PRIVATE_NAMES:
            return True
        if part.startswith('.env') and not part.endswith(('.example', '.sample')):
            return True
        if part.lower().endswith(PRIVATE_SUFFIXES):
            return True
    return False


def owned(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or '\0' in relative:
        raise ValueError('Unowned cache path: ' + repr(relative))
    relative_path = PurePosixPath(relative)
    if (relative_path.is_absolute() or str(relative_path) != relative
            or any(part in ('', '.', '..') for part in relative_path.parts)):
        raise ValueError('Unowned cache path: ' + repr(relative))
    if _private_path(relative):
        raise ValueError('Private configuration cannot enter a capture cache: ' + relative)
    target = Path(root).resolve()
    for part in relative_path.parts:
        target /= part
        if target.is_symlink():
            raise ValueError('Symlink in capture cache: ' + relative)
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Unowned cache path: ' + repr(relative))
    return target


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key: ' + key)
        value[key] = item
    return value


def _read_json(path, label, *, maximum=MAX_JSON_BYTES):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing cache JSON: ' + label)
    if path.stat().st_size > maximum:
        raise ValueError('Cache JSON is unreasonably large: ' + label)
    try:
        return json.loads(path.read_text(), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid cache JSON: ' + label) from exc


def _write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def _git_environment():
    return {
        **os.environ,
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_OPTIONAL_LOCKS': '0',
        'GIT_TERMINAL_PROMPT': '0',
    }


def _git(repo, *arguments, text=False):
    result = subprocess.run(
        ['git', '-C', str(repo), *arguments],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_git_environment(), text=text,
    )
    return result.stdout.strip() if text else result.stdout


def validate_application_sources(application):
    """Validate portable source before capture; return copied overlay members.

    The repository's public npm install flag is restored only from the exact
    Git bundle. Never accept arbitrary npm configuration or an archive overlay
    for a credential-sensitive filename.
    """
    application = Path(application).resolve()
    sources = {}
    for relative in sorted(filter(None, _git(application, 'ls-files', '-z').decode().split('\0'))):
        if relative == 'UI/.npmrc':
            path = owned(application, 'UI') / '.npmrc'
            if (path.is_symlink() or not path.is_file() or path.stat().st_size > 64
                    or not re.fullmatch(rb'legacy-peer-deps=(?:true|false)(?:\r?\n)?', path.read_bytes())):
                raise ValueError('Tracked npm configuration must contain only the public legacy-peer-deps flag')
            continue
        path = owned(application, relative)
        if path.is_file():
            sources[relative] = path
    return sources


def _check_clean_repository(repo, revision, label):
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ValueError(f'{label} repository is unavailable: {repo}')
    try:
        top = Path(_git(repo, 'rev-parse', '--show-toplevel', text=True)).resolve()
        head = _git(repo, 'rev-parse', 'HEAD', text=True)
        dirty = _git(repo, 'status', '--porcelain=v1', '--untracked-files=all', text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f'{label} must be an available committed Git repository') from exc
    if top != repo or head != revision or dirty:
        raise ValueError(f'Baseline export requires the clean committed {label} revision recorded by the run')
    return head


def _trusted_index():
    path = ROOT / 'BE/application/manuale/index.py'
    spec = importlib.util.spec_from_file_location('manual_cache_trusted_index', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trusted_reuse():
    path = ROOT / 'BE/application/manuale/reuse.py'
    spec = importlib.util.spec_from_file_location('manual_cache_trusted_reuse', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_revision(value, label):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise ValueError('Invalid committed revision for ' + label)
    return value


def _safe_tooling(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid tooling hash inventory')
    result = {}
    for relative, expected in value.items():
        owned(ROOT, relative)
        if not isinstance(expected, str) or not HASH.fullmatch(expected):
            raise ValueError('Invalid tooling hash: ' + relative)
        result[relative] = expected
    return result


def _public_input(value, kind):
    if not isinstance(value, dict) or set(value) != PUBLIC_INPUT_KEYS:
        raise ValueError('Portable baseline has an invalid public ' + kind + ' input')
    result = {key: value[key] for key in PUBLIC_INPUT_KEYS}
    if result.get('snapshot_kind') != kind:
        raise ValueError('Portable baseline has a mismatched ' + kind + ' snapshot kind')
    return result


def _validate_public_state(state):
    if (not isinstance(state, dict) or not PUBLIC_STATE_REQUIRED <= set(state)
            or set(state) - PUBLIC_STATE_KEYS):
        raise ValueError('Portable baseline has an invalid public run-state structure')
    if (state.get('application') != 'application' or state.get('manual') != 'manual'
            or state.get('run') != '.'):
        raise ValueError('Portable baseline run paths must remain relative')
    state['application_input'] = _public_input(state['application_input'], 'application')
    state['manual_input'] = _public_input(state['manual_input'], 'manual')
    evidence = state.get('evidence_reuse')
    if evidence is not None:
        if (not isinstance(evidence, dict)
                or set(evidence) != {'path', 'sha256', 'reused_recipes'}):
            raise ValueError('Portable baseline has invalid retained-evidence state')
    return state


def _validate_state(state, *, run=None, require_source_repositories=False):
    if not isinstance(state, dict) or state.get('format') != 1 or state.get('status') != 'evaluated':
        raise ValueError('Only a fully evaluated run may be exported or imported as a baseline')
    run_id = state.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-z0-9]{8,32}', run_id):
        raise ValueError('Evaluated run is missing a valid run identifier')
    release = state.get('release')
    if not isinstance(release, str) or not release.strip():
        raise ValueError('Evaluated run is missing its exact release')
    if state.get('selected_recipes') and (not isinstance(state.get('capture_id'), str) or not state['capture_id'].strip()):
        raise ValueError('Evaluated run is missing its capture identifier')
    reference_date = state.get('reference_date')
    if not isinstance(reference_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', reference_date):
        raise ValueError('Evaluated run is missing its reference date')
    application = state.get('application_input')
    manual = state.get('manual_input')
    if (not isinstance(application, dict) or not isinstance(manual, dict)
            or application.get('state') != 'committed' or manual.get('state') != 'committed'
            or application.get('uncommitted_files') or manual.get('uncommitted_files')):
        raise ValueError('Baseline export requires clean committed application and manual inputs')
    application_revision = _safe_revision(application.get('revision'), 'application input')
    manual_revision = _safe_revision(manual.get('revision'), 'manual input')
    _safe_tooling(state.get('tooling_hashes'))
    if not isinstance(state.get('selected_recipes'), list) or any(
            not isinstance(item, str) or not item for item in state['selected_recipes']):
        raise ValueError('Evaluated run has an invalid recipe selection')
    if require_source_repositories:
        _check_clean_repository(state.get('application'), application_revision, 'application')
        _check_clean_repository(state.get('manual_repository'), manual_revision, 'manual')
    return application_revision, manual_revision


def _scenario_records(run, state, manifest, registry, reuse):
    if not isinstance(registry, dict) or registry.get('format') != 1 or not isinstance(
            registry.get('recipes'), list) or not registry['recipes']:
        raise ValueError('Invalid manual recipe registry')
    recipes = {recipe.get('id'): recipe for recipe in registry['recipes'] if isinstance(recipe, dict)}
    if len(recipes) != len(registry['recipes']) or None in recipes:
        raise ValueError('Duplicate or invalid manual recipe identifier')
    if (not isinstance(manifest, dict) or manifest.get('format') != 1
            or not isinstance(manifest.get('scenarios'), list)
            or not isinstance(manifest.get('pages'), list)
            or not isinstance(manifest.get('metadata'), dict)):
        raise ValueError('Invalid evaluated capture manifest')
    scenarios = {}
    for scenario in manifest['scenarios']:
        if not isinstance(scenario, dict) or not isinstance(scenario.get('id'), str):
            raise ValueError('Invalid scenario binding in capture manifest')
        if scenario['id'] in scenarios:
            raise ValueError('Duplicate scenario binding: ' + scenario['id'])
        scenarios[scenario['id']] = scenario
    if set(scenarios) != set(recipes):
        missing = sorted(set(recipes) - set(scenarios))
        extra = sorted(set(scenarios) - set(recipes))
        raise ValueError('Only a complete passed capture baseline can be exported; '
                         f'missing={missing}, extra={extra}')
    reports, proofs, captures = {}, set(), {}
    source_paths = set()
    reused_scenarios = set()
    for identifier, recipe in recipes.items():
        scenario = scenarios[identifier]
        expected_path = identifier + '.json'
        if (scenario.get('status') != 'passed' or scenario.get('report_path') != expected_path
                or not isinstance(scenario.get('report_sha256'), str)
                or not HASH.fullmatch(scenario['report_sha256'])):
            raise ValueError('Scenario is not a complete passed capture baseline: ' + identifier)
        report_path = owned(run, expected_path)
        report = _read_json(report_path, 'scenario report ' + identifier)
        if _digest_file(report_path)[0] != scenario['report_sha256']:
            raise ValueError('Scenario report bytes differ from manifest: ' + identifier)
        if (report.get('id') != identifier or report.get('status') != 'passed'
                or report.get('backend') != 'real'
                or scenario.get('application_revision') != report.get('application_revision')
                or scenario.get('capture_id') != report.get('capture_id')):
            raise ValueError('Scenario report is failed or has invalid provenance: ' + identifier)
        origin = {'application_revision': report.get('application_revision'),
                  'capture_id': report.get('capture_id')}
        try:
            reuse.check_report(state, recipe, report, origin)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('Original scenario evidence is invalid: ' + identifier + ': ' + str(exc)) from exc
        try:
            reuse.origin_sections(
                state, manifest, identifier, scenario['report_sha256'], report,
                allow_empty=recipe.get('publication_sections') == [],
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('Original editorial evidence is invalid: ' + identifier + ': ' + str(exc)) from exc
        if scenario.get('source_hashes') != report.get('source_hashes'):
            raise ValueError('Scenario source binding differs from its report: ' + identifier)
        if 'reuse' not in scenario and report.get('tooling_hashes') != state['tooling_hashes']:
            raise ValueError('Scenario tooling differs from the run: ' + identifier)
        if 'reuse' in scenario:
            reused_scenarios.add(identifier)
        source_paths.update(report.get('source_hashes', {}))
        for screenshot in report['screenshots']:
            relative = 'captures/' + screenshot['path']
            proofs.add(relative)
            captures[relative] = screenshot
            proofs.add(screenshot['master']['path'])
            captures[screenshot['master']['path']] = screenshot['master']
            if screenshot.get('stability'):
                proofs.add(screenshot['stability']['path'])
        proofs.add(expected_path)
        reports[identifier] = report
    selected = state['selected_recipes']
    if len(selected) != len(set(selected)) or set(selected) != set(recipes) - reused_scenarios:
        raise ValueError('Fresh workflow selection differs from original and retained scenario evidence')
    evidence = state.get('evidence_reuse')
    if evidence is not None:
        if (not isinstance(evidence, dict) or evidence.get('path') != 'evidence-reuse.json'
                or not isinstance(evidence.get('sha256'), str)
                or not HASH.fullmatch(evidence['sha256'])):
            raise ValueError('Evaluated run has invalid retained-evidence provenance')
        if _digest_file(owned(run, 'evidence-reuse.json'))[0] != evidence['sha256']:
            raise ValueError('Retained-evidence plan changed after evaluation')
        try:
            plan = reuse.validate_reuse_plan(state)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('Original retained evidence is invalid: ' + str(exc)) from exc
        if evidence.get('reused_recipes') != plan.get('reused_recipes'):
            raise ValueError('Retained-evidence selection differs from its validated plan')
        if reused_scenarios != set(plan['recipes']):
            raise ValueError('Retained-evidence scenarios differ from their validated origin manifests')
        proofs.add('evidence-reuse.json')
        for entry in plan['recipes'].values():
            origin = entry.get('origin_manifest', {})
            relative = origin.get('path', '')
            owned(run, relative)
            if (_digest_file(owned(run, relative))[0] != origin.get('sha256')
                    or not relative.startswith('evidence-origin/') or not relative.endswith('.json')):
                raise ValueError('Invalid original evidence manifest: ' + relative)
            proofs.add(relative)
    elif reused_scenarios:
        raise ValueError('Reused scenarios require their origin manifests and validated evidence plan')
    return recipes, scenarios, reports, proofs, captures, source_paths


def _chunk_screenshot_paths(chunk):
    paths = set()
    for screenshot in chunk.get('screenshots', []):
        if isinstance(screenshot, dict) and isinstance(screenshot.get('path'), str):
            paths.add(screenshot['path'])
    for block in chunk.get('reader', []):
        for relative in block.get('screenshots', []):
            if isinstance(relative, str):
                paths.add(relative)
    return paths


def _validate_render_evidence(run, state, manifest, compiled, render, verified_pages):
    """Check actual rendered sections and displayed images, retaining all capture proof."""
    sys.path.insert(0, str(ROOT / 'BE'))
    from application.manuale.render_reuse import _page_facts, _inspect_page_record, RenderReuseError
    if render.get('status') != 'passed' or not isinstance(render.get('pages'), list):
        raise ValueError('Render evidence is not a successful complete evaluation')
    rendered = {}
    for page in render['pages']:
        if (not isinstance(page, dict) or not isinstance(page.get('page'), str)
                or page['page'] in rendered):
            raise ValueError('Render evidence contains a duplicate or invalid page')
        rendered[page['page']] = page
    if set(rendered) != verified_pages:
        raise ValueError('Render evidence does not cover exactly the verified corpus pages')
    manifest_pages = {page['path']: page for page in manifest['pages']}
    layout_proofs = set()
    for relative in sorted(verified_pages):
        manual_page = relative + '.mdx'
        try:
            facts = _page_facts(manifest_pages[manual_page], state, compiled[manual_page])
            layouts = _inspect_page_record(rendered[relative], facts, run)
        except RenderReuseError as exc:
            raise ValueError('Render evidence differs from generated content: ' + relative + ': ' + str(exc)) from exc
        if layout_proofs.intersection(layouts):
            raise ValueError('Render evidence reuses a layout path across different pages')
        layout_proofs.update(layouts)
    return layout_proofs


def _validate_evaluated_evidence(run, state, *, require_source_repositories=False):
    application_revision, manual_revision = _validate_state(
        state, run=run if require_source_repositories else None,
        require_source_repositories=require_source_repositories,
    )
    run = Path(run).resolve()
    application = Path(state['application']).resolve()
    manual = Path(state['manual']).resolve()
    manifest = _read_json(owned(run, 'manifest.json'), 'manifest.json')
    registry = _read_json(owned(application, 'docs/manuale/recipes.json'), 'manual recipe registry')
    index = _trusted_index()
    reuse = _trusted_reuse()
    recipes, scenarios, reports, scenario_proofs, captures, source_paths = _scenario_records(
        run, state, manifest, registry, reuse)
    metadata = manifest['metadata']
    if (metadata.get('application_revision') != application_revision
            or metadata.get('manual_revision') != manual_revision
            or metadata.get('release') != state['release']
            or metadata.get('reference_date') != state['reference_date']
            or metadata.get('code_state') != 'committed'
            or metadata.get('uncommitted_code')
            or metadata.get('manual_input_state') != 'committed'
            or metadata.get('uncommitted_manual')
            or metadata.get('tooling_hashes') != state['tooling_hashes']
            or metadata.get('evidence_reuse') != state.get('evidence_reuse')):
        raise ValueError('Manifest is not bound to the exact committed evaluated input revision/release')

    try:
        evaluated_index = index.ManualIndex.load(owned(run, 'index.json'))
        corpus = evaluated_index.value
        evaluated_index.verify_unchanged_dependencies(application)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('Sealed evaluated index is invalid or differs from its application input: ' + str(exc)) from exc
    if corpus.get('metadata') != metadata:
        raise ValueError('Evaluated index does not belong to the capture manifest input/release')
    rebuilt = index.build_index(manual, application, manifest, artifact_root=run)
    if not index.reproduces(rebuilt, corpus):
        raise ValueError('Evaluated index cannot be reproduced from retained content and evidence')
    chunks = corpus.get('chunks')
    if (not isinstance(chunks, list) or not chunks
            or any(not isinstance(chunk, dict) or not isinstance(chunk.get('page'), str)
                   or not isinstance(chunk.get('section'), str) for chunk in chunks)):
        raise ValueError('Evaluated baseline has no verified corpus sections')
    verified_pages = {chunk.get('page') for chunk in chunks}
    manifest_pages = {page.get('path') for page in manifest['pages'] if isinstance(page, dict)}
    if None in verified_pages:
        raise ValueError('Evaluated index has a verified section without an owned page')
    page_files = {page: page if str(page).endswith('.mdx') else str(page) + '.mdx'
                  for page in verified_pages}
    if not set(page_files.values()) <= manifest_pages:
        raise ValueError('Evaluated index references pages outside the complete manifest')
    manual_files = set()
    for relative in manifest_pages:
        owned(manual, relative)
        if not isinstance(relative, str) or not relative.endswith('.mdx'):
            raise ValueError('Invalid generated manual page path')
        manual_files.add(relative)
    manual_files.add('.manuale-evidence.json')
    for config in ('mint.json', 'docs.json', 'SCREENSHOTS-NEEDED.md'):
        path = manual / config
        if path.exists():
            manual_files.add(config)
    if not (manual / 'mint.json').is_file():
        raise ValueError('Evaluated generated manual is missing public mint.json configuration')
    for chunk in chunks:
        for relative in _chunk_screenshot_paths(chunk):
            path = owned(manual, relative)
            if not path.is_file():
                raise ValueError('Generated manual is missing verified screenshot: ' + relative)
            manual_files.add(relative)

    catalog = None
    catalog_binding = metadata.get('source_catalog')
    if not catalog_binding:
        raise ValueError('Verified baselines require a complete validated source catalog')
    if catalog_binding is not None:
        try:
            catalog = index.load_source_catalog(manual, application, manifest, run)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('Original source catalog is invalid: ' + str(exc)) from exc
        manual_files.update(catalog.get('manual_files', {}))
    source_catalog = run / 'source-catalog.json'
    if catalog_binding is not None and not source_catalog.is_file():
        raise ValueError('Evaluated run is missing its bound source catalog')
    if source_catalog.is_file():
        _read_json(source_catalog, 'source-catalog.json')

    try:
        reuse.verify_snapshot(application, state['application_input'],
                              set(corpus.get('dependencies', {})) | source_paths)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('Application evidence does not match its committed snapshot: ' + str(exc)) from exc
    if require_source_repositories:
        _check_clean_repository(application, application_revision, 'application')

    evaluation = _read_json(owned(run, 'evaluation.json'), 'evaluation.json')
    embedded = _read_json(owned(run, 'embedded-manual.json'), 'embedded-manual.json')
    render = _read_json(owned(run, 'render.json'), 'render.json')
    compile_report = _read_json(owned(run, 'compile.json'), 'compile.json')
    identity = corpus.get('identity')
    if (evaluation.get('status') != 'passed'
            or evaluation.get('corpus_identity') != identity
            or evaluation.get('application_revision') != application_revision
            or evaluation.get('version') != state['release']):
        raise ValueError('Evaluation evidence is not a successful result for this exact corpus/release')
    if (embedded.get('status') != 'passed' or embedded.get('backend') != 'real'
            or embedded.get('corpus_identity') != identity
            or embedded.get('application_revision') != application_revision
            or embedded.get('version') != state['release']):
        raise ValueError('Embedded-reader evidence is not a successful result for this exact corpus/release')
    if (compile_report.get('format') != 1 or compile_report.get('status') != 'passed'
            or not isinstance(compile_report.get('compiler'), str) or not compile_report['compiler']):
        raise ValueError('Compile evidence is not a successful complete evaluation')
    compiled_pages = compile_report.get('pages')
    if not isinstance(compiled_pages, list) or len(compiled_pages) != len(manifest_pages):
        raise ValueError('Compile evidence does not cover the complete generated manual')
    compiled = {}
    for page in compiled_pages:
        identifier = page.get('page') if isinstance(page, dict) else None
        if not isinstance(identifier, str) or identifier + '.mdx' in compiled:
            raise ValueError('Compile evidence contains a duplicate or invalid page')
        compiled[identifier + '.mdx'] = page
    if set(compiled) != manifest_pages:
        raise ValueError('Compile evidence does not cover exactly the generated manual')
    for relative, page in compiled.items():
        actual = index.file_digest(owned(manual, relative))
        if (page.get('source_sha256') != actual
                or not isinstance(page.get('compiled_sha256'), str)
                or not HASH.fullmatch(page['compiled_sha256'])):
            raise ValueError('Compile evidence differs from generated manual bytes: ' + relative)
        assets = page.get('assets', {})
        if not isinstance(assets, dict):
            raise ValueError('Compile evidence has invalid local assets')
        for asset, expected in assets.items():
            if not isinstance(expected, str) or not HASH.fullmatch(expected) or index.file_digest(owned(manual, asset)) != expected:
                raise ValueError('Compiled local asset changed: ' + asset)
            manual_files.add(asset)

    layout_proofs = _validate_render_evidence(run, state, manifest, compiled, render, verified_pages)

    proof_files = {'manifest.json', 'index.json', 'evaluation.json', 'embedded-manual.json',
                   'render.json', 'compile.json'} | scenario_proofs | layout_proofs
    if state.get('render_reuse') or (run / 'render-reuse.json').exists():
        sys.path.insert(0, str(ROOT / 'BE'))
        from application.manuale.render_reuse import validate_render_reuse
        render_plan = validate_render_reuse(state)
        proof_files.update(('render-reuse.json', 'render-inputs.json'))
        manual_files.update(path.removeprefix('manual/') for path in render_plan['render_inputs']
                            if path.startswith('manual/'))
        for page in render_plan['pages'].values():
            proof_files.update((page['original_report']['path'], page['origin_record']['path']))
    rehearsal = run / 'release-verification.json'
    if rehearsal.exists():
        if _read_json(rehearsal, 'release verification').get('status') != 'passed':
            raise ValueError('Release verification did not pass')
        proof_files.add('release-verification.json')
    if (run / 'authoring-preflight.json').is_file():
        proof_files.add('authoring-preflight.json')
    if source_catalog.is_file():
        proof_files.add('source-catalog.json')
    if (run / 'timings.json').is_file():
        _read_json(run / 'timings.json', 'timings.json')
        proof_files.add('timings.json')
    frontend = False
    frontend_artifacts = set()
    frontend_build = run / 'frontend-build.json'
    if frontend_build.is_file() and state.get('override'):
        build = _read_json(frontend_build, 'frontend-build.json')
        artifacts = build.get('artifacts') if isinstance(build, dict) else None
        if not isinstance(artifacts, dict) or not artifacts:
            raise ValueError('Frontend build evidence is incomplete')
        override = _read_json(state['override'], 'frontend compose override')
        ui = override.get('services', {}).get('ui', {}) if isinstance(override, dict) else {}
        if (not isinstance(ui.get('command'), list) or not ui['command']
                or not isinstance(ui.get('environment'), dict)):
            raise ValueError('Frontend reuse configuration is incomplete')
        source = application / 'UI/dist/public'
        for relative, expected in artifacts.items():
            owned(source, relative)
            if _digest_file(source / relative)[0] != expected:
                raise ValueError('Frontend artifact differs from its recorded build: ' + relative)
            frontend_artifacts.add('application/UI/dist/public/' + relative)
        frontend = True
    return {
        'application_revision': application_revision,
        'manual_revision': manual_revision,
        'manifest': manifest,
        'registry': registry,
        'corpus': corpus,
        'proof_files': proof_files,
        'manual_files': manual_files,
        'captures': captures,
        'frontend': frontend,
        'frontend_artifacts': frontend_artifacts,
    }


def _sanitize_state(state):
    public = {key: state[key] for key in PUBLIC_STATE if key in state}
    if public.get('format') != 1 or public.get('status') != 'evaluated':
        raise ValueError('Only evaluated public state can enter a portable baseline')
    evidence = public.get('evidence_reuse')
    if evidence is not None:
        if not isinstance(evidence, dict):
            raise ValueError('Invalid public retained-evidence state')
        public['evidence_reuse'] = {
            key: evidence[key] for key in ('path', 'sha256', 'reused_recipes') if key in evidence
        }
    public['application_input'] = _public_input(state['application_input'], 'application')
    public['manual_input'] = _public_input(state['manual_input'], 'manual')
    public.update(application='application', manual='manual', run='.')
    public = _validate_public_state(public)
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)
    if str(ROOT) in serialized or str(Path.home()) in serialized:
        raise ValueError('Refusing to export private absolute paths')
    return public


def _frontend_members(run, state, members):
    recorded = run / 'frontend-build.json'
    if not recorded.is_file():
        return
    build = _read_json(recorded, 'frontend-build.json')
    artifacts = build.get('artifacts') if isinstance(build, dict) else None
    if not isinstance(artifacts, dict) or not artifacts:
        return
    override_path = state.get('override')
    if not isinstance(override_path, str):
        return
    override = _read_json(override_path, 'frontend compose override')
    ui = override.get('services', {}).get('ui', {}) if isinstance(override, dict) else {}
    command = ui.get('command')
    environment = ui.get('environment')
    if (not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command)
            or not isinstance(environment, dict)):
        return
    public_environment = {key: value for key, value in environment.items()
                          if key in ('DEPLOY_ENV', 'OEM_ENV', 'ASSOZETA_MANUAL_BUILD', 'NODE_OPTIONS')
                          and isinstance(value, str)}
    public_override = _validate_public_override(
        {'services': {'ui': {'command': command, 'environment': public_environment}}})
    staged = {}
    source = Path(state['application']).resolve() / 'UI/dist/public'
    for relative, expected in artifacts.items():
        owned(source, relative)
        path = source / relative
        if not path.is_file() or _digest_file(path)[0] != expected:
            raise ValueError('Frontend artifact changed after its recorded build: ' + relative)
        staged['application/UI/dist/public/' + relative] = (path, path.stat().st_mode & 0o777)
    staged['proof/frontend-build.json'] = (recorded, 0o644)
    staged['proof/compose.override.json'] = (public_override, 0o644)
    for name, (source_value, mode) in staged.items():
        if name in members:
            raise ValueError('Duplicate frontend cache path: ' + name)
        members[name] = (source_value, mode)


def _validate_public_override(value):
    services = value.get('services') if isinstance(value, dict) else None
    ui = services.get('ui') if isinstance(services, dict) else None
    if (not isinstance(services, dict) or set(services) != {'ui'}
            or not isinstance(ui, dict) or set(ui) != {'command', 'environment'}):
        raise ValueError('Portable frontend configuration contains unexpected runtime fields')
    command, environment = ui.get('command'), ui.get('environment')
    if (not isinstance(command, list) or not command
            or not all(isinstance(item, str) for item in command)
            or not isinstance(environment, dict)
            or set(environment) - {'DEPLOY_ENV', 'OEM_ENV', 'ASSOZETA_MANUAL_BUILD', 'NODE_OPTIONS'}
            or not all(isinstance(item, str) for item in environment.values())):
        raise ValueError('Portable frontend configuration is invalid')
    if command != PUBLIC_FRONTEND_COMMAND:
        raise ValueError('Portable frontend command must match the fixed manual preview command')
    for key, item in environment.items():
        if key == 'NODE_OPTIONS':
            if not re.fullmatch(r'--max-old-space-size=[1-9][0-9]{2,5}', item):
                raise ValueError('Portable frontend NODE_OPTIONS only supports a bounded heap size')
        elif item != PUBLIC_FRONTEND_ENVIRONMENT[key]:
            raise ValueError('Portable frontend environment must match the public manual fixture')
    return value


def _stage_member(stage, name, source, mode, totals):
    destination = owned(stage, name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, (dict, list)):
        destination.write_text(json.dumps(source, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        destination.chmod(0o644)
    else:
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError('Cache input is not a regular file: ' + str(source))
        shutil.copyfile(source, destination)
        destination.chmod(mode & 0o777)
    digest, size = _digest_file(destination)
    totals[0] += 1
    totals[1] += size
    if totals[0] > MAX_MEMBERS or totals[1] > MAX_EXPANDED_BYTES:
        raise ValueError('Capture cache exceeds the supported member count or expanded size')
    return {'path': name, 'sha256': digest, 'size': size, 'mode': destination.stat().st_mode & 0o777}


def export_cache(run, output):
    run = Path(run).resolve()
    output = Path(output).resolve()
    state = _read_json(owned(run, 'run.json'), 'run.json')
    application = Path(state['application']).resolve()
    sources = validate_application_sources(application)
    validated = _validate_evaluated_evidence(run, state, require_source_repositories=True)
    proof_files = validated['proof_files']
    manual_files = validated['manual_files']
    if _read_json(run / 'manifest.json', 'manifest.json') != validated['manifest']:
        raise ValueError('Manifest changed while the baseline was being validated')
    public_state = _sanitize_state(state)
    members = {}
    for relative in ('manifest.json', *sorted(proof_files - {'manifest.json'})):
        path = owned(run, relative)
        if not path.is_file():
            raise ValueError('Evidence file disappeared after validation: ' + relative)
        members['proof/' + relative] = (path, path.stat().st_mode & 0o777)
    manual_root = Path(state['manual']).resolve()
    for relative in sorted(manual_files):
        path = owned(manual_root, relative)
        if not path.is_file():
            raise ValueError('Generated manual file disappeared after validation: ' + relative)
        members['manual/' + relative] = (path, path.stat().st_mode & 0o777)
    for relative, path in sources.items():
        members['application/' + relative] = (path, path.stat().st_mode & 0o777)
    _frontend_members(run, state, members)
    if len(members) + 2 > MAX_MEMBERS:
        raise ValueError('Capture cache exceeds the supported member count')

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.manuale-cache-') as temporary:
        temporary = Path(temporary)
        stage = temporary / 'bundle'
        stage.mkdir()
        totals = [0, 0]
        inventory = []
        for name, (source, mode) in sorted(members.items()):
            inventory.append(_stage_member(stage, name, source, mode, totals))
        bundle = stage / 'application.bundle'
        try:
            _git(application, 'bundle', 'create', str(bundle), 'HEAD')
        except subprocess.CalledProcessError as exc:
            raise ValueError('Could not create the committed application Git bundle') from exc
        bundle_digest, bundle_size = _digest_file(bundle)
        inventory.append({
            'path': 'application.bundle',
            'sha256': bundle_digest,
            'size': bundle_size,
            'mode': bundle.stat().st_mode & 0o777,
        })
        totals[0] += 1
        totals[1] += bundle_size
        if totals[0] > MAX_MEMBERS or totals[1] > MAX_EXPANDED_BYTES:
            raise ValueError('Capture cache exceeds the supported member count or expanded size')
        payload = {
            'format': FORMAT,
            'purpose': PURPOSE,
            'state': public_state,
            'revision': validated['application_revision'],
            'members': inventory,
        }
        cache_json = stage / 'cache.json'
        cache_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        cache_json.chmod(0o644)
        inventory_paths = {item['path'] for item in inventory}
        actual_paths = {path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file()}
        if actual_paths != inventory_paths | {'cache.json'}:
            raise ValueError('Portable cache stage contains missing or unexpected files')
        archive_path = temporary / 'cache.tar.gz'
        with tarfile.open(archive_path, 'w:gz', compresslevel=1) as archive:
            for path in sorted(stage.rglob('*')):
                if not path.is_file():
                    continue
                relative = path.relative_to(stage).as_posix()
                info = tarfile.TarInfo(relative)
                info.size = path.stat().st_size
                info.mode = path.stat().st_mode & 0o777
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                with path.open('rb') as stream:
                    archive.addfile(info, stream)
        archive_path.replace(output)
    return {'path': str(output), 'workflows': len(validated['registry']['recipes']),
            'format': FORMAT, 'purpose': PURPOSE}


def _parse_cache_manifest(path):
    payload = _read_json(path, 'cache.json', maximum=32 * 1024 ** 2)
    if not isinstance(payload, dict) or set(payload) != {
            'format', 'purpose', 'state', 'revision', 'members'}:
        raise ValueError('Invalid capture-cache manifest')
    if payload.get('format') == 1:
        raise ValueError('Capture cache format 1 is untrusted and unsupported; perform a cold full '
                         'verification and export a new format 2 baseline')
    if payload.get('format') != FORMAT or payload.get('purpose') != PURPOSE:
        raise ValueError('Not a supported format 2 capture cache')
    state = payload.get('state')
    state = _validate_public_state(state)
    _validate_state(state)
    revision = payload.get('revision')
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise ValueError('Capture cache has an invalid application revision')
    members = payload.get('members')
    if not isinstance(members, list) or not 2 <= len(members) <= MAX_MEMBERS - 1:
        raise ValueError('Capture cache has an invalid or unreasonable member inventory')
    inventory = {}
    total = 0
    for item in members:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'size', 'mode'}:
            raise ValueError('Capture cache has an invalid member inventory entry')
        relative = item['path']
        if (not isinstance(relative, str) or relative == 'cache.json'
                or (relative != 'application.bundle'
                    and not relative.startswith(('application/', 'manual/', 'proof/')))):
            raise ValueError('Capture cache has an unowned member: ' + repr(relative))
        parts = PurePosixPath(relative)
        if (parts.is_absolute() or str(parts) != relative
                or any(part in ('', '.', '..') for part in parts.parts)
                or _private_path(relative)):
            raise ValueError('Capture cache has an unsafe member: ' + repr(relative))
        if relative in inventory:
            raise ValueError('Capture cache has a duplicate inventory member: ' + relative)
        if (not isinstance(item['sha256'], str) or not HASH.fullmatch(item['sha256'])
                or type(item['size']) is not int or not 0 <= item['size'] <= MAX_MEMBER_BYTES
                or type(item['mode']) is not int or not 0 <= item['mode'] <= 0o777):
            raise ValueError('Capture cache has invalid integrity metadata for ' + relative)
        total += item['size']
        if total > MAX_EXPANDED_BYTES:
            raise ValueError('Capture cache expanded size is unreasonable')
        inventory[relative] = item
    if 'application.bundle' not in inventory or 'proof/manifest.json' not in inventory:
        raise ValueError('Capture cache is missing its Git bundle or manifest')
    return payload, inventory


def _extract_archive(archive_path, destination):
    names = set()
    extracted = {}
    total = 0
    with tarfile.open(archive_path, 'r:gz') as archive:
        for count, member in enumerate(archive, start=1):
            if count > MAX_MEMBERS:
                raise ValueError('Capture cache has an unreasonable member count')
            if (not member.isfile() or member.issym() or member.islnk() or member.issparse()
                    or member.size < 0):
                raise ValueError('Unexpected non-regular capture-cache member: ' + member.name)
            if member.name in names:
                raise ValueError('Duplicate capture-cache member: ' + member.name)
            if member.size > MAX_MEMBER_BYTES:
                raise ValueError('Capture cache member is unreasonably large: ' + member.name)
            total += member.size
            if total > MAX_EXPANDED_BYTES:
                raise ValueError('Capture cache expanded size is unreasonable')
            if (member.name not in ('cache.json', 'application.bundle')
                    and not member.name.startswith(('application/', 'manual/', 'proof/'))):
                raise ValueError('Unexpected capture-cache member: ' + member.name)
            target = owned(destination, member.name)
            names.add(member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError('Capture cache member cannot be read: ' + member.name)
            digest = hashlib.sha256()
            size = 0
            with source, target.open('wb') as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > member.size or size > MAX_MEMBER_BYTES:
                        raise ValueError('Capture cache member exceeds its declared size: ' + member.name)
                    digest.update(chunk)
                    output.write(chunk)
            if size != member.size:
                raise ValueError('Capture cache member is truncated: ' + member.name)
            target.chmod(member.mode & 0o777)
            extracted[member.name] = {
                'path': member.name, 'sha256': digest.hexdigest(), 'size': size,
                'mode': member.mode & 0o777,
            }
    if 'cache.json' not in names:
        raise ValueError('Capture cache is missing cache.json')
    return names, extracted


def _verify_inventory(names, extracted, inventory):
    expected = set(inventory) | {'cache.json'}
    if names != expected:
        missing = sorted(expected - names)
        extra = sorted(names - expected)
        raise ValueError(f'Capture cache member inventory differs from its archive; missing={missing}, extra={extra}')
    for relative, expected_member in inventory.items():
        actual = extracted.get(relative)
        if actual != expected_member:
            raise ValueError('Capture cache member digest, size or mode differs from cache.json: ' + relative)
    if len(inventory) != len(expected) - 1:
        raise ValueError('Capture cache inventory contains duplicate paths')


def _restore_bundle_and_payload(unpacked, result, payload):
    bundle = owned(unpacked, 'application.bundle')
    application = result / 'application'
    try:
        subprocess.run(
            ['git', 'clone', '--quiet', '--no-local', str(bundle), str(application)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_git_environment(),
        )
        head = _git(application, 'rev-parse', 'HEAD', text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError('Capture cache Git bundle cannot be cloned as the recorded revision') from exc
    if head != payload['revision']:
        raise ValueError('Capture cache Git bundle revision differs from its evaluated input')
    shutil.copytree(unpacked / 'application', application, dirs_exist_ok=True)
    shutil.copytree(unpacked / 'manual', result / 'manual')
    shutil.copytree(unpacked / 'proof', result, dirs_exist_ok=True)
    state = dict(payload['state'])
    state.update(
        run=str(result), application=str(application), manual=str(result / 'manual'),
        services_removed=True,
    )
    state.pop('env_file', None)
    override = result / 'compose.override.json'
    if override.is_file():
        _validate_public_override(_read_json(override, 'frontend compose override'))
        state['override'] = str(override)
    else:
        state.pop('override', None)
    _write_json(result / 'run.json', state)
    return state


def _verify_git_overlay(application, revision):
    if _git(application, 'rev-parse', 'HEAD', text=True) != revision:
        raise ValueError('Imported application Git revision changed')
    dirty = _git(application, 'status', '--porcelain=v1', '--untracked-files=all', text=True)
    relevant = [line for line in dirty.splitlines()
                if not line[3:].startswith('quality-reports/')]
    if relevant:
        raise ValueError('Imported application files differ from the hash-validated committed bundle')
    validate_application_sources(application)


def _verify_member_scope(result, state, inventory, validated):
    expected = {'application.bundle'}
    expected.update('proof/' + relative for relative in validated['proof_files'])
    expected.update('manual/' + relative for relative in validated['manual_files'])
    application = result / 'application'
    expected.update('application/' + relative for relative in validate_application_sources(application))
    if validated['frontend']:
        expected.update(validated['frontend_artifacts'])
        expected.update({'proof/frontend-build.json', 'proof/compose.override.json'})
        if not state.get('override'):
            raise ValueError('Imported frontend artifacts require their public build configuration')
    if set(inventory) != expected:
        missing = sorted(expected - set(inventory))
        extra = sorted(set(inventory) - expected)
        raise ValueError(f'Capture cache contains undeclared or missing evidence; missing={missing}, extra={extra}')


def _register_import(state):
    spec = importlib.util.spec_from_file_location('manual_cache_runner', ROOT / 'docs/scripts/manuale.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.remember_run(state)


def _remove_import_registration(state):
    registry = ROOT / 'quality-reports/manuale/cache.json'
    if not registry.is_file() or registry.is_symlink():
        return
    try:
        payload = _read_json(registry, 'manual run registry')
    except ValueError:
        return
    if not isinstance(payload, dict) or payload.get('format') != 1 or not isinstance(
            payload.get('runs'), list):
        return
    run = Path(state['run']).resolve()
    runs = []
    for entry in payload['runs']:
        entry_run = entry.get('run') if isinstance(entry, dict) else None
        if isinstance(entry_run, str) and Path(entry_run).resolve() == run:
            continue
        runs.append(entry)
    if runs == payload['runs']:
        return
    _write_json(registry, {'format': 1, 'runs': runs})


def import_cache(archive_path, output, manual_repository, *, register=True):
    archive_path = Path(archive_path).resolve()
    output = Path(output).resolve()
    if output.exists() or output.is_symlink():
        raise ValueError('Cache import needs a new owned output directory')
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError('Capture cache archive is missing')
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('Capture cache archive is unreasonably large')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.manuale-import-') as temporary:
        temporary = Path(temporary)
        unpacked = temporary / 'unpacked'
        unpacked.mkdir()
        try:
            names, extracted = _extract_archive(archive_path, unpacked)
            payload, inventory = _parse_cache_manifest(unpacked / 'cache.json')
            _verify_inventory(names, extracted, inventory)
            result = temporary / 'result'
            result.mkdir()
            state = _restore_bundle_and_payload(unpacked, result, payload)
            state['manual_repository'] = str(Path(manual_repository).resolve())
            _write_json(result / 'run.json', state)
            _verify_git_overlay(result / 'application', payload['revision'])
            validated = _validate_evaluated_evidence(result, state)
            _verify_member_scope(result, state, inventory, validated)
            _check_clean_repository(result / 'application', payload['revision'], 'application')
            if state['application_input']['revision'] != payload['revision']:
                raise ValueError('Imported evaluated state differs from its Git bundle revision')
            state.update(
                run=str(output), application=str(output / 'application'),
                manual=str(output / 'manual'),
            )
            if (result / 'compose.override.json').is_file():
                state['override'] = str(output / 'compose.override.json')
            _write_json(result / 'run.json', state)
            result.replace(output)
        except Exception:
            if output.exists():
                shutil.rmtree(output)
            raise
    if register:
        try:
            _register_import(state)
        except Exception:
            try:
                _remove_import_registration(state)
            finally:
                if output.exists():
                    shutil.rmtree(output)
            raise
    return {'run': str(output), 'format': FORMAT, 'purpose': PURPOSE}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('export', 'import'))
    parser.add_argument('--run', type=Path)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manual-repo', type=Path)
    args = parser.parse_args()
    if args.command == 'export':
        if not args.run:
            parser.error('--run is required')
        result = export_cache(args.run, args.archive)
    else:
        if not args.output or not args.manual_repo:
            parser.error('--output and --manual-repo are required')
        result = import_cache(args.archive, args.output, args.manual_repo)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
