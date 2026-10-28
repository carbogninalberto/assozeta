#!/usr/bin/env python3
"""Build, capture and verify the Italian manual.

build: compile reviewed MDX and compatible evidence without Docker or browsers.
capture: run selected or affected workflows in an owned demonstration environment.
verify-release: verify the complete target, package it and rehearse appliance updates.

Compatible evidence and frontend builds are discovered automatically. The other
commands expose individual phases for diagnosis and recovery; see docs/manuale/README.md.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def runtime_check(root=None):
    """Bounded capability probes before creating worktrees or runtime state.

    Command diagnostics may contain daemon endpoints or credentials. Retain
    fixed reasons and version numbers here, never raw subprocess output.
    """
    root = Path(root or ROOT)
    checks = []

    def command(identifier, argv, hint, *, timeout=5, cwd=None):
        record = {'id': identifier, 'status': 'failed', 'hint': hint}
        try:
            result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                record['status'] = 'passed'
                version = re.search(r'\bv?\d+(?:\.\d+){1,3}\b', result.stdout)
                if version:
                    record['version'] = version.group()
                record.pop('hint')
            else:
                record['reason'] = 'command-unavailable'
        except FileNotFoundError:
            record['reason'] = 'not-installed'
        except subprocess.TimeoutExpired:
            record['reason'] = 'timeout'
        except OSError:
            record['reason'] = 'permission-or-runtime-error'
        checks.append(record)
        return record

    command('git', ['git', '--version'], 'Install Git.')
    command('python-index', [sys.executable, '-c', 'import numpy; print(numpy.__version__)'],
            'Run from a Python environment with docs/manuale/requirements-offline.txt installed.')
    node = command('node', ['node', '--version'], 'Install Node and the browser project dependencies.')
    command('compose', ['docker', 'compose', 'version', '--short'], 'Install Docker Compose v2.')
    command('docker-engine', ['docker', 'info', '--format', '{{.ServerVersion}}'],
            'Start Docker and allow this session to access its daemon.')
    try:
        with socket.socket() as connection:
            connection.bind(('127.0.0.1', 0))
            connection.listen(1)
        checks.append({'id': 'host-ports', 'status': 'passed'})
    except OSError:
        checks.append({'id': 'host-ports', 'status': 'failed', 'reason': 'cannot-listen',
                       'hint': 'Use an execution environment that allows local application servers.'})
    browser_root = root / 'selfhost/tests/browser'
    if node['status'] == 'passed' and browser_root.is_dir():
        # Use precisely the installed project browser. No downloads, alternate
        # executable, application fixtures or API response mocks are involved.
        probe = '''
import {chromium} from '@playwright/test';
let browser;
try { browser = await chromium.launch({timeout: 12000}); }
catch { process.exitCode = 1; }
finally { if (browser) await browser.close(); }
'''
        command('chromium', ['node', '--input-type=module', '-e', probe],
                'Install browser dependencies and Chromium, then allow this session to launch the browser.',
                cwd=browser_root, timeout=15)
    else:
        checks.append({'id': 'chromium', 'status': 'failed', 'reason': 'prerequisite-missing',
                       'hint': 'Install Node and run npm --prefix selfhost/tests/browser ci, then playwright install chromium.'})
    return {'format': 1, 'purpose': 'runtime-prerequisites-not-workflow-evidence',
            'status': 'passed' if all(item['status'] == 'passed' for item in checks) else 'failed',
            'checks': checks}


def print_runtime_check(report):
    for check in report['checks']:
        detail = check.get('version') or check.get('hint', '')
        print(f"{check['id']}: {check['status']}" + (f' — {detail}' if detail else ''), flush=True)
    if report['status'] != 'passed':
        print('Runtime unavailable. No worktrees, seed, captures or corpus changes were started.', file=sys.stderr)


def recipe_registry(root=ROOT):
    registry = json.loads((root / 'docs/manuale/recipes.json').read_text())
    if registry['format'] != 1 or len({recipe['id'] for recipe in registry['recipes']}) != len(registry['recipes']):
        raise ValueError('Invalid manual recipe registry')
    return registry


def recipe_seed_options(recipe):
    """Select an explicitly implemented fixture profile, never arbitrary flags."""
    profile = recipe.get('fixture', 'baseline')
    if profile not in ('baseline', 'receipts-edit-delete', 'collaborator-removal', 'member-transfer'):
        raise ValueError('Unknown manual fixture profile: ' + str(profile))
    return ['--scenario', profile]


def cached_runs(manual_repository):
    """Local discovery only. Every reused input still passes its compatibility gate."""
    path = ROOT / 'quality-reports/manuale/cache.json'
    try:
        cache = json.loads(path.read_text())
        if not isinstance(cache, dict) or cache.get('format') != 1:
            return []
        repository = str(Path(manual_repository).resolve())
        return [entry for entry in cache['runs'] if isinstance(entry, dict) and entry.get('manual_repository') == repository
                and (Path(entry['run']) / 'run.json').is_file()]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def remember_run(state):
    if not state.get('manual_repository'):
        return
    run = Path(state['run']).resolve()
    manifest = run / 'manifest.json'
    expected = {recipe['id'] for recipe in recipe_registry(Path(state['application']))['recipes']}
    captured = {item['id'] for item in json.loads(manifest.read_text()).get('scenarios', [])
                if item.get('status') == 'passed'} if manifest.is_file() else set()
    frontend = (run / 'frontend-build.json').is_file()
    if not captured and not frontend:
        return
    entries = cached_runs(state['manual_repository'])
    entry = {'run': str(run), 'manual_repository': str(Path(state['manual_repository']).resolve()),
             'evidence': bool(captured), 'complete': captured == expected, 'frontend': frontend}
    path = ROOT / 'quality-reports/manuale/cache.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    write_json(temporary, {'format': 1, 'runs': [entry, *[item for item in entries if item['run'] != str(run)][:7]]})
    temporary.replace(path)


def capture_input_hashes(application):
    """Compare actual source snapshots, including dirty, added and deleted inputs."""
    application = Path(application)
    names = git(application, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
    return {relative: sha(application / relative) if (application / relative).is_file() else None
            for relative in sorted(set(filter(None, names)))}


def reuse_inputs(state, args):
    """Reuse by default, with explicit cold runs and recorded rejection reasons."""
    cold = getattr(args, 'no_cache', False)
    state.setdefault('selection', {})
    explicit = getattr(args, 'reuse_evidence', None)
    candidates = ([{'run': explicit, 'evidence': True,
                    'frontend': (Path(explicit) / 'frontend-build.json').is_file()}] if explicit else
                  [] if cold else cached_runs(state['manual_repository']))
    state['cache'] = {'mode': 'disabled' if cold else 'explicit' if explicit else 'automatic', 'decisions': []}
    helper = None
    chosen = None
    target_hashes = None
    force_selected = bool(args.changed_since or args.manual_changed_since or getattr(args, 'workflow', None))
    maximum_retained = len(recipe_registry(Path(state['application']))['recipes']) - (
        len(state['selected_recipes']) if force_selected else 0)
    for candidate in candidates:
        if not candidate.get('evidence'):
            continue
        try:
            helper = helper or retained_evidence_helper(state)
            baseline = str(Path(candidate['run']).resolve())
            previous = json.loads((Path(baseline) / 'run.json').read_text())
            target_hashes = target_hashes or capture_input_hashes(state['application'])
            previous_hashes = capture_input_hashes(previous['application'])
            changed = sorted(relative for relative in previous_hashes.keys() | target_hashes.keys()
                             if previous_hashes.get(relative) != target_hashes.get(relative))
            manual_changed = []
            old_manual = previous.get('manual_input', {}).get('revision')
            new_manual = state.get('manual_input', {}).get('revision')
            if old_manual and new_manual and old_manual != new_manual:
                try:
                    manual_changed = git(state['manual_repository'], 'diff', '--name-only', old_manual, new_manual).splitlines()
                except subprocess.CalledProcessError:
                    manual_changed = ['unknown-authoring-revision']
            # Per-section editorial bindings decide whether MDX changes need a
            # capture. This preserves the reviewed wording-only reuse path.
            shared_manual = [path for path in manual_changed if not path.endswith('.mdx')
                             and not path.startswith('images/')]
            input_decisions = []
            current_registry = recipe_registry(Path(state['application']))
            selected_changes = list(changed)
            registry_affected = []
            if 'docs/manuale/recipes.json' in selected_changes:
                previous_registry = recipe_registry(Path(previous['application']))
                shared_keys = ('format', 'shared_dependencies', 'non_capture_dependencies')
                if all(previous_registry.get(key) == current_registry.get(key) for key in shared_keys):
                    previous_recipes = {recipe['id']: recipe for recipe in previous_registry['recipes']}
                    registry_affected = [recipe['id'] for recipe in current_registry['recipes']
                                         if previous_recipes.get(recipe['id']) != recipe]
                    selected_changes.remove('docs/manuale/recipes.json')
                    input_decisions.append({'path': 'docs/manuale/recipes.json', 'scope': 'application',
                        'reason': 'generated-registry; compare individual recipe contracts', 'recipes': registry_affected})
            impact = {}
            affected = select_affected(current_registry, selected_changes, shared_manual,
                                       root=Path(state['application']), decisions=input_decisions,
                                       base_root=previous['application'], report=impact)
            affected = sorted(set(affected) | set(registry_affected))
            fresh = sorted(set(affected) | set(state['selected_recipes'] if force_selected else []))
            probe = helper.build_reuse_plan({**state, 'selected_recipes': fresh},
                                           baseline, stage=False)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            if explicit and not getattr(args, 'baseline_optional', False):
                raise
            state['cache']['decisions'].append({'run': candidate['run'], 'result': 'unavailable', 'reason': str(exc)})
            continue
        if chosen is None or len(probe['reused_recipes']) > len(chosen[1]['reused_recipes']):
            chosen = baseline, probe
        state['cache']['decisions'].append({'run': baseline, 'result': 'checked',
                                           'retained': len(probe['reused_recipes']), 'rejected': probe.get('rejected', []),
                                           'changed_inputs': changed, 'affected_recipes': affected})
        state['cache']['decisions'][-1].update(input_decisions=input_decisions, changed_manual=manual_changed)
        if impact.get('format'):
            state['cache']['decisions'][-1]['impact'] = {key: impact[key] for key in (
                'mode', 'full_verification_required', 'affected_workflows', 'checks', 'reasons')}
        if explicit or len(probe['reused_recipes']) == maximum_retained:
            break
    complete = getattr(args, 'command', '') in ('run', 'build', 'verify-release')
    if complete or (chosen and not force_selected):
        retained = set(chosen[1]['reused_recipes'] if chosen else [])
        state['selected_recipes'] = [recipe['id'] for recipe in recipe_registry(Path(state['application']))['recipes']
                                     if recipe['id'] not in retained]
    if chosen:
        baseline, probe = chosen
        for decision in state['cache']['decisions']:
            decision['selected'] = decision.get('run') == baseline
        # Stage only after the read-only compatibility probe; a staging integrity
        # failure must stop this run, never silently fall through to another cache.
        plan = helper.build_reuse_plan(state, baseline, stage=True, eligible_recipes=probe['reused_recipes'])
        file = Path(state['run']) / 'evidence-reuse.json'
        state['reuse_evidence'] = baseline
        state['evidence_reuse'] = {'path': file.name, 'sha256': sha(file), 'reused_recipes': plan['reused_recipes']}
        state['selection'].update(retained_recipes=plan['reused_recipes'], reuse_rejections=probe.get('rejected', []))
        if not force_selected:
            state['selection']['mode'] = 'full-compatible-reuse'
        validate_retained_evidence(state)
    if not cold and not getattr(args, 'reuse_frontend', None):
        state['frontend_candidates'] = [entry['run'] for entry in candidates if entry.get('frontend')]


TOOL_MODULE_ROOTS = ('docs/manuale/', 'selfhost/tests/browser/manuale/')
TOOL_MODULE_SUFFIXES = ('.mjs', '.js', '.cjs')
SHARED_TOOL_MODULES = {
    'selfhost/tests/browser/manuale/scenario.mjs',
    'selfhost/tests/browser/manuale/frame.mjs',
    'selfhost/tests/browser/manuale/redaction.mjs',
    'selfhost/tests/browser/manuale/reader-plan.mjs',
}


def recipe_tooling_dependencies(registry, root=ROOT):
    """Follow literal relative imports from registered recipe-local tooling.

    The generator entry point itself remains shared. Its individual recipe
    modules must be registered as dependencies, rather than inferred from file
    names. Computed imports and unregistered modules therefore stay conservative.
    """
    root = Path(root).resolve()
    import_pattern = re.compile(
        r'''(?:\b(?:import|export)\s+(?:[^;]*?\s+from\s+)?|\b(?:import|require)\s*\(\s*)["'](\.[^"']+)["']''')
    cache = {}

    def imports(relative):
        if relative in cache:
            return cache[relative]
        result = set()
        file = root / relative
        if file.is_file():
            for specifier in import_pattern.findall(file.read_text()):
                candidate = (file.parent / specifier).resolve()
                if not candidate.is_relative_to(root):
                    continue
                if not candidate.suffix:
                    alternatives = [Path(str(candidate) + suffix) for suffix in TOOL_MODULE_SUFFIXES]
                    alternatives += [candidate / ('index' + suffix) for suffix in TOOL_MODULE_SUFFIXES]
                    candidate = next((path for path in alternatives if path.is_file()), candidate)
                if candidate.suffix in TOOL_MODULE_SUFFIXES:
                    result.add(candidate.relative_to(root).as_posix())
        cache[relative] = result
        return result

    dependencies = {}
    for recipe in registry['recipes']:
        pending = [recipe['script']]
        for relative in recipe['dependencies']:
            if not relative.startswith(TOOL_MODULE_ROOTS):
                continue
            if relative.endswith(TOOL_MODULE_SUFFIXES):
                pending.append(relative)
            elif relative.endswith('/') and (root / relative).is_dir():
                pending.extend(path.relative_to(root).as_posix() for path in (root / relative).rglob('*')
                               if path.is_file() and path.suffix in TOOL_MODULE_SUFFIXES)
        found = set()
        while pending:
            relative = pending.pop()
            if relative in found:
                continue
            found.add(relative)
            # Imports into application code do not weaken production dependency
            # handling: only capture/generator modules get this narrow scope.
            if relative.startswith(TOOL_MODULE_ROOTS):
                pending.extend(imports(relative) - found)
        dependencies[recipe['id']] = sorted(found)
    return dependencies


def affected_recipes(registry, changed, manual_changed=(), *, root=ROOT, decisions=None):
    """Select registered consumers; unknown production/tooling still selects all."""
    all_ids = [recipe['id'] for recipe in registry['recipes']]
    tooling = recipe_tooling_dependencies(registry, root)
    selected = set()

    def record(relative, scope, reason, matches):
        if decisions is not None:
            decisions.append({'path': relative, 'scope': scope, 'reason': reason, 'recipes': matches})
        selected.update(matches)

    for relative in changed:
        if relative == 'docs/manuale/source.json' or relative.startswith('manuale/'):
            # Vendored manual content is materialized as its own immutable
            # authoring revision and checked independently.
            record(relative, 'application', 'immutable-authoring-pin; content checked independently', [])
            continue
        if relative.startswith('docs/scripts/tests/') or (relative.startswith('BE/') and '/tests/' in relative) or (
                relative.startswith('UI/') and re.search(r'\.(?:test|spec)\.[cm]?[jt]sx?$', relative)):
            record(relative, 'application', 'test-only', [])
            continue
        if (relative.startswith('selfhost/tests/browser/')
                and re.search(r'\.(?:test|spec)\.[cm]?[jt]sx?$', relative)
                and not any(relative in dependencies for dependencies in tooling.values())):
            record(relative, 'application', 'test-only', [])
            continue
        if (relative.startswith('selfhost/tests/')
                and not relative.startswith(('selfhost/tests/browser/manuale/', 'selfhost/tests/manual-release-smoke'))
                and relative not in ('selfhost/tests/browser/package.json', 'selfhost/tests/browser/package-lock.json',
                                     'selfhost/tests/browser/playwright.manual.config.mjs')
                and not any(relative in dependencies for dependencies in tooling.values())):
            record(relative, 'application', 'independent-application-test; ordinary quality checks retained', [])
            continue
        if relative.startswith(('docs/manuale/', 'selfhost/')) and Path(relative).suffix.lower() in ('.md', '.mdx', '.txt'):
            matches = [recipe['id'] for recipe in registry['recipes']
                       if any(relative.startswith(prefix) for prefix in recipe['dependencies'])]
            record(relative, 'application', 'registered-dependency' if matches else 'documentation-only', matches)
            continue
        local_tool = relative.startswith(TOOL_MODULE_ROOTS) and relative.endswith(TOOL_MODULE_SUFFIXES)
        if local_tool and relative not in SHARED_TOOL_MODULES:
            matches = [identifier for identifier in all_ids if relative in tooling[identifier]]
            if not matches and relative in registry.get('reference_modules', []):
                record(relative, 'application', 'reviewed-reference; regenerate prose contracts', [])
                continue
            reason = ('shared-dependency' if set(matches) == set(all_ids) else
                      'registered-recipe-tooling' if matches else 'unknown-tooling')
            record(relative, 'application', reason, matches or all_ids)
            continue
        matches = [recipe['id'] for recipe in registry['recipes']
                   if any(relative.startswith(prefix) for prefix in recipe['dependencies'])]
        if not matches and any(relative.startswith(prefix) for prefix in registry.get('non_capture_dependencies', [])):
            record(relative, 'application', 'declared-reference-only; runtime checks still required', [])
            continue
        if relative in SHARED_TOOL_MODULES or any(relative.startswith(prefix) for prefix in registry['shared_dependencies']):
            record(relative, 'application', 'shared-dependency', all_ids)
            continue
        if not matches and relative in registry.get('reference_dependencies', []):
            record(relative, 'application', 'reviewed-reference; regenerate prose contracts', [])
        elif not matches and relative.startswith(('BE/', 'UI/')):
            record(relative, 'application', 'unknown-production-dependency', all_ids)
        else:
            record(relative, 'application', 'registered-dependency' if matches else 'unrelated', matches)
    for relative in manual_changed:
        matches = [recipe['id'] for recipe in registry['recipes'] if relative in recipe['pages']]
        if relative.startswith('images/'):
            matches = [recipe['id'] for recipe in registry['recipes']
                       if recipe.get('capture_prefix') and relative.startswith(recipe['capture_prefix'])]
        metadata = relative in ('README.md', 'CLAUDE.md', 'AUTHORING.md', '.manuale-authoring.json',
                                '.manuale-evidence.json', 'SCREENSHOT-PLACEHOLDERS.html')
        if not matches and not metadata:
            record(relative, 'manual', 'unknown-manual-input', all_ids)
        else:
            record(relative, 'manual', 'registered-content' if matches else 'authoring-metadata-only', matches)
    return [identifier for identifier in all_ids if identifier in selected]


IMPACT_MODULE = 'docs/scripts/manuale_impact.py'
IMPACT_MAP = 'docs/manuale/impact-map.json'


def impact_analyzer(application):
    """The snapshot's own impact analyzer, or None for snapshots that predate it."""
    application = Path(application).resolve()
    path = application / IMPACT_MODULE
    if not path.is_file() or not (application / IMPACT_MAP).is_file():
        return None
    name = 'run_owned_manual_impact_' + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    module = sys.modules.get(name)
    if module is None or getattr(module, '__file__', None) != str(path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def select_affected(registry, changed, manual_changed=(), *, root=ROOT, decisions=None, base=None,
                    base_root=None, report=None):
    """Registered-recipe decisions refined by the change-impact graph.

    The legacy per-path decisions are still recorded for compatibility. When
    the snapshot provides docs/scripts/manuale_impact.py, its report decides the
    recaptured workflows: full verification selects every workflow and
    workflows unknown to the report are selected too (fail closed). `base` is
    a git revision, `base_root` a retained baseline snapshot directory.
    """
    legacy = affected_recipes(registry, changed, manual_changed, root=root, decisions=decisions)
    analyzer = impact_analyzer(root)
    if analyzer is None:
        return legacy
    paths = [*changed, *(('manuale/' + relative) for relative in manual_changed)]
    try:
        head = analyzer.WorkTree(root)
        try:
            previous = (analyzer.WorkTree(base_root) if base_root else
                        analyzer.GitTree(root, base) if base else None)
        except (OSError, subprocess.CalledProcessError):
            previous = None
        impact = analyzer.analyze_trees(head, previous, paths)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        if report is not None:
            report.update(error='impact analysis unavailable: ' + str(exc))
        return legacy
    if report is not None:
        report.update(impact)
    identifiers = [recipe['id'] for recipe in registry['recipes']]
    known = set(impact['affected_workflows']) | set(impact['reused_workflows'])
    if impact['full_verification_required']:
        return identifiers
    return [identifier for identifier in identifiers
            if identifier in impact['affected_workflows'] or identifier not in known]


def record_impact(state, impact):
    """Bind the impact report to the run directory and summarize it in the selection."""
    if not impact:
        return
    file = Path(state['run']) / 'impact.json'
    write_json(file, impact)
    state['selection']['impact'] = {
        'path': file.name, 'sha256': sha(file), 'mode': impact.get('mode'),
        'full_verification_required': impact.get('full_verification_required'),
        'affected_workflows': impact.get('affected_workflows', []), 'checks': impact.get('checks', {}),
        **({'error': impact['error']} if 'error' in impact else {})}


def changed_snapshot_paths(repo, snapshot_input, since):
    """Committed diff plus every recorded dirty/deleted/untracked snapshot path."""
    committed = git(repo, 'diff', '--name-only', since, 'HEAD').splitlines()
    return sorted(set(committed) | set(snapshot_input['uncommitted_files']))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def ui_sources(application):
    files = subprocess.check_output(['git', '-C', str(application), 'ls-files', '--cached', '--others',
        '--exclude-standard', '-z', '--', 'UI/']).decode().split('\0')
    return {relative: sha(application / relative) if (application / relative).is_file() else None
            for relative in sorted(set(filter(None, files)))}


def reuse_frontend(state):
    previous_run = Path(state['reuse_frontend'])
    previous = json.loads((previous_run / 'run.json').read_text())
    old = Path(previous['application'])
    current = Path(state['application'])
    old_service = json.loads(Path(previous['override']).read_text())['services']['ui']
    new_service = json.loads(Path(state['override']).read_text())['services']['ui']
    if (old_service.get('command') != new_service['command'] or old_service.get('environment') != new_service['environment']
            or sha(old / 'selfhost/compose.dev.yml') != sha(current / 'selfhost/compose.dev.yml')):
        raise RuntimeError('Frontend reuse requires the same compiled server and build environment')
    sources = ui_sources(old)
    if sources != ui_sources(current):
        raise RuntimeError('Frontend reuse refused: UI implementation/build inputs changed')
    # Check the retained snapshot still agrees with its recorded dirty inputs.
    modified = git(old, 'diff', '--name-only', 'HEAD', '--', 'UI/').splitlines()
    for relative in modified:
        if sources.get(relative) != previous['application_input']['uncommitted_files'].get(relative):
            raise RuntimeError('Retained frontend source changed after its recorded snapshot')
    rendered_path = previous_run / 'render.json'
    rendered = rendered_path.exists() and json.loads(rendered_path.read_text()).get('status') == 'passed'
    captures = [previous_run / (recipe + '.json') for recipe in previous['selected_recipes']]
    browser_verified = any(file.exists() and (report := json.loads(file.read_text())).get('status') == 'passed'
        and report.get('backend') == 'real' and report.get('capture_id') == previous.get('capture_id')
        and report.get('application_revision') == previous['application_input']['revision']
        and report.get('tooling_hashes') == previous['tooling_hashes'] for file in captures)
    if not rendered and not browser_verified:
        raise RuntimeError('Reuse requires a retained, successful real-browser verification')
    source = old / 'UI/dist/public'
    if not (source / 'index.html').exists():
        raise RuntimeError('Retained compiled frontend is missing')
    artifacts = {}
    for file in sorted(source.rglob('*')):
        if file.is_symlink():
            raise RuntimeError('Refusing symlinks in retained frontend artifacts')
        if file.is_file():
            artifacts[str(file.relative_to(source))] = sha(file)
    # Subsequent reuse must match the audited artifact hashes, too.
    recorded = previous_run / 'frontend-build.json'
    if not recorded.exists():
        raise RuntimeError('Reuse requires the recorded compiled artifact hashes')
    recorded_build = json.loads(recorded.read_text())
    if recorded_build['artifacts'] != artifacts or recorded_build['sources'] != sources:
        raise RuntimeError('Retained frontend bundle changed after its recorded build')
    target = current / 'UI/dist/public'
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name('public-reuse')
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(source, staged)
    if target.exists():
        shutil.rmtree(target)
    staged.replace(target)
    return {'source_run': previous['run_id'], 'sources': sources, 'artifacts': artifacts}


@contextmanager
def timed(run, name, *, kind='phase'):
    """Record elapsed work without command arguments, environment or private logs."""
    started = time.monotonic()
    status = 'failed'
    try:
        yield
        status = 'passed'
    finally:
        path = Path(run) / 'timings.json'
        report = json.loads(path.read_text()) if path.exists() else {'format': 1, 'events': []}
        report['events'].append({'kind': kind, 'name': name, 'status': status,
                                 'elapsed_seconds': round(time.monotonic() - started, 3)})
        temporary = path.with_suffix('.tmp')
        write_json(temporary, report)
        temporary.replace(path)


def execute(command, run, label, *, cwd=None, env=None, input=None):
    print(label, flush=True)
    log = run / 'execution.log'
    with timed(run, label, kind='command'), log.open('ab') as stream:
        log.chmod(0o600)
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=stream, input=input)
        if result.returncode:
            raise RuntimeError(f'{label} failed ({result.returncode}); inspect {log}')


def snapshot(repo, target, run, *, allow_dirty=False, ref='HEAD', branch=None, standalone=False, kind='application'):
    if kind not in ('application', 'manual'):
        raise ValueError('Unknown snapshot kind: ' + str(kind))
    allowed_prefixes = ('BE/', 'UI/', 'docs/', 'selfhost/', '.github/', 'skills/') if kind == 'application' else (
        'docs/', 'faq/', 'tutorials/', 'images/', 'logo/', 'video/', '_snippets/')
    allowed_files = set() if kind == 'application' else {
        'mint.json', 'README.md', 'CLAUDE.md', '.manuale-authoring.json', '.manuale-evidence.json'}
    dirty = git(repo, 'status', '--porcelain')
    if dirty and not allow_dirty:
        raise RuntimeError(f'{repo} has uncommitted changes; use --allow-dirty to record an explicit working-tree snapshot')
    revision = git(repo, 'rev-parse', ref + '^{commit}')
    if standalone:
        # An owned local clone keeps all metadata writable inside the run when
        # the input repository's shared Git registry is read-only. Copy objects
        # instead of sharing alternates; the retained proof remains independent.
        execute(['git', 'clone', '--local', '--no-hardlinks', '--no-checkout', str(repo), str(target)],
                run, 'Create independent local documentation checkout')
        args = ['git', '-C', str(target), 'checkout']
        args += ['-b', branch] if branch else ['--detach']
        execute([*args, revision], run, 'Select the recorded input revision')
        remote = subprocess.run(['git', '-C', str(repo), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True)
        if remote.returncode == 0:
            execute(['git', '-C', str(target), 'remote', 'set-url', 'origin', remote.stdout.strip()],
                    run, 'Preserve the source repository origin')
    else:
        args = ['git', '-C', str(repo), 'worktree', 'add']
        args += ['-b', branch] if branch else ['--detach']
        execute([*args, str(target), revision], run, 'Create documentation worktree')
    local = {}
    byte_overlays = set()
    if dirty:
        if git(repo, 'rev-parse', ref) != git(repo, 'rev-parse', 'HEAD'):
            raise RuntimeError('Working changes can only be overlaid onto the current HEAD')
        patch = subprocess.check_output(['git', '-C', str(repo), 'diff', 'HEAD', '--binary'])
        if patch:
            execute(['git', '-C', str(target), 'apply', '--binary', '-'], run, 'Snapshot tracked changes', input=patch)
        untracked = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '--others', '--exclude-standard', '-z']).decode().split('\0')
        for relative in filter(None, untracked):
            if not relative.startswith(allowed_prefixes) and relative not in allowed_files:
                continue
            if any(part.startswith('.env') for part in Path(relative).parts) or Path(relative).is_absolute():
                raise RuntimeError('Refusing to snapshot runtime credentials')
            source = repo / relative
            if source.is_symlink():
                raise RuntimeError('Untracked symlinks require explicit repository review')
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    if allow_dirty and git(repo, 'rev-parse', ref) == git(repo, 'rev-parse', 'HEAD'):
        # Git can omit CRLF-to-LF translation from its binary diff. The
        # capture must execute the bytes selected from the developer checkout,
        # and the manifest must not silently describe those bytes as a blob.
        tracked = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z']).decode().split('\0')
        for relative in filter(None, tracked):
            byte_roots = ('BE/', 'UI/') if kind == 'application' else allowed_prefixes
            if (not relative.startswith(byte_roots) and relative not in allowed_files) or any(part.startswith('.env') for part in Path(relative).parts):
                continue
            source, destination = repo / relative, target / relative
            if source.is_file() and destination.is_file() and not source.is_symlink() and not destination.is_symlink():
                if source.read_bytes() != destination.read_bytes():
                    shutil.copy2(source, destination)
                    byte_overlays.add(relative)
    if dirty or byte_overlays:
        changed = subprocess.check_output(['git', '-C', str(target), 'diff', '--name-only', 'HEAD', '-z']).decode().split('\0')
        untracked = subprocess.check_output(['git', '-C', str(target), 'ls-files', '--others', '--exclude-standard', '-z']).decode().split('\0')
        for relative in sorted(set(filter(None, changed + untracked)) | byte_overlays):
            local[relative] = sha(target / relative) if (target / relative).is_file() else None
    return {'revision': git(target, 'rev-parse', 'HEAD'), 'state': 'working_tree' if local else 'committed', 'snapshot_kind': kind,
            'checkout_kind': 'standalone-local-clone' if standalone else 'linked-worktree', 'uncommitted_files': local}


def compose(state, *args):
    return ['docker', 'compose', '--env-file', state['env_file'], '--project-name', state['project'],
            '-f', str(Path(state['application']) / 'selfhost/compose.dev.yml'), '-f', state['override'], *args]


def management(state, *args):
    # Host ownership makes private mode-0600 artifacts readable by the runner
    # on Linux as well as Docker Desktop. Keep the service itself independent.
    return compose(state, 'run', '--rm', '--no-deps', '--user', f'{os.getuid()}:{os.getgid()}',
                   'api', 'python', 'manage.py', *args)


def validate_tooling(state):
    for relative, expected in state['tooling_hashes'].items():
        if sha(Path(state['application']) / relative) != expected:
            raise RuntimeError('Run tooling changed after snapshot: ' + relative)


def retained_evidence_helper(state):
    """Use the sealed target snapshot's verifier, never a baseline's executable."""
    validate_tooling(state)
    relative = 'BE/application/manuale/reuse.py'
    if relative not in state['tooling_hashes']:
        raise RuntimeError('Retained evidence requires the snapshotted reuse verifier')
    path = Path(state['application']) / relative
    cached = sys.modules.get('run_owned_manual_reuse')
    # Tool bytes were just checked. Keep immutable Git-object memoization
    # across phases; the verifier still rereads mutable source/report/image bytes.
    if cached is not None and getattr(cached, '__file__', None) == str(path):
        return cached
    spec = importlib.util.spec_from_file_location('run_owned_manual_reuse', path)
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    return helper


def validate_retained_evidence(state):
    binding = state.get('evidence_reuse')
    file = Path(state['run']) / 'evidence-reuse.json'
    if not binding:
        if file.exists():
            raise RuntimeError('Retained evidence plan is not bound to this run')
        return None
    if binding.get('path') != file.name or not file.is_file() or sha(file) != binding.get('sha256'):
        raise RuntimeError('Retained evidence plan changed after preparation')
    with timed(state['run'], 'retained-evidence'):
        return retained_evidence_helper(state).validate_reuse_plan(state)


def authoring_preflight(state):
    """Diagnose missing authored prose before allocating ports or starting services."""
    run = Path(state['run'])
    execute(['node', str(Path(state['application']) / 'docs/scripts/manuale-generate.mjs'),
             state['run'], '--authoring-preflight'], run,
            'Validate selected authored procedures and inventory missing reviewed MDX', cwd=state['application'])
    file = run / 'authoring-preflight.json'
    result = json.loads(file.read_text())
    state['authoring_preflight'] = {'path': file.name, 'sha256': sha(file),
                                    'status': result['status'], 'missing_sections': len(result['missing_sections'])}
    execute([sys.executable, str(Path(state['application']) / 'docs/scripts/manuale-coverage.py'),
             '--manual', state['manual'], '--output', str(run / 'workflow-coverage.json')], run,
            'Inventory preparation gaps across the complete reviewed manual')


def write_json(path, value, private=False):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    if private:
        path.chmod(0o600)


def manual_content_hashes(manual):
    """Record content bytes separately from the selected Git/input snapshot."""
    files = {}
    for directory, names, filenames in os.walk(manual):
        names[:] = [name for name in names if name != '.git']
        for name in filenames:
            if name == '.git' or name.startswith('.env'):
                continue
            file = Path(directory) / name
            if file.is_symlink():
                raise ValueError('Manual authoring does not follow unreviewed symlinks: ' + str(file.relative_to(manual)))
            files[file.relative_to(manual).as_posix()] = sha(file)
    return dict(sorted(files.items()))


def prepare_manual_authoring(state):
    """Preserve the reviewed MDX source; preparation never authors replacement prose."""
    validate_tooling(state)
    application, manual = Path(state['application']), Path(state['manual'])
    spec = importlib.util.spec_from_file_location('run_owned_manual_scaffold', application / 'docs/scripts/manuale-scaffold.py')
    scaffold = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scaffold)
    original = manual_content_hashes(manual)
    reviewed = scaffold.validate_authoring_input(manual)
    if not reviewed:
        raise ValueError('Select the reviewed MDX manual revision. For new sections, inspect the implementation '
                         'and use manuale-scaffold.py review-content before building or capturing.')
    generated = manual_content_hashes(manual)
    return {'mode': 'preserved-reviewed-input',
            'input_revision': state['manual_input']['revision'],
            'input_files': original,
            'generated_files': {relative: value for relative, value in generated.items() if original.get(relative) != value},
            'deleted_input_files': sorted(set(original) - set(generated)),
            'scaffold_sha256': state['tooling_hashes']['docs/scripts/manuale-scaffold.py'],
            'evidence_sha256': generated['.manuale-evidence.json'],
            'content_state': 'reviewed-input',
            'verified': False, 'publication_ready': False}


def edit_content(state, args):
    """Generate drafts before preflight, then seal the exact derived tooling."""
    validate_tooling(state)
    application = Path(state['application'])
    spec = importlib.util.spec_from_file_location('run_manual_editor', application / 'docs/scripts/manuale-edit.py')
    editor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(editor)
    try:
        result = editor.edit_run(state, profile=getattr(args, 'editor_profile', None),
            model=getattr(args, 'editor_model', None), timeout=getattr(args, 'editor_timeout', 180),
            max_sections=getattr(args, 'editor_max_sections', 12))
    finally:
        # CI injects this key only for the model step. Never pass it to builds,
        # browser scenarios, backend commands or fixture services afterward.
        os.environ.pop('CODEX_API_KEY', None)
    state['editorial_automation'] = result
    for relative, binding in result['generated_tooling'].items():
        if state['tooling_hashes'].get(relative) != binding['input_sha256']:
            raise ValueError('Editorial tooling did not match the selected input')
        state['tooling_hashes'][relative] = binding['sha256']
    original = state['manual_authoring']['input_files']
    generated = manual_content_hashes(Path(state['manual']))
    state['manual_authoring'].update(content_state='generated-editorial-draft',
        generated_files={relative: value for relative, value in generated.items() if original.get(relative) != value},
        deleted_input_files=sorted(set(original) - set(generated)),
        evidence_sha256=generated['.manuale-evidence.json'])
    write_json(Path(state['run']) / 'run.json', state)


def prepare(args):
    token = secrets.token_hex(6)
    run = (Path(args.output).resolve() if args.output else ROOT / 'quality-reports/manuale' / token)
    if run.exists():
        raise RuntimeError('Use a new output directory; retained runs must be resumed explicitly')
    run.mkdir(parents=True, mode=0o700)
    application, manual = run / 'application', run / 'manual'
    state = {'format': 1, 'run_id': token, 'project': 'assozeta-manuale-' + token,
             'application': str(application), 'manual': str(manual), 'run': str(run),
             'manual_repository': str(Path(args.manual_repo).resolve()), 'status': 'preparing',
             'release': args.release, 'reference_date': args.reference_date,
             'manual_url': args.manual_url.rstrip('/')}
    write_json(run / 'run.json', state)
    state['application_input'] = snapshot(ROOT, application, run, allow_dirty=args.allow_dirty,
                                          ref=args.application_ref, standalone=args.standalone_checkouts)
    state['preparation_stage'] = 'manual-snapshot'
    write_json(run / 'run.json', state)
    state['manual_branch'] = 'add/manuale-' + token
    state['manual_input'] = snapshot(Path(args.manual_repo).resolve(), manual, run, allow_dirty=args.allow_dirty, ref=args.manual_ref,
                                     kind='manual',
                                     branch=state['manual_branch'], standalone=args.standalone_checkouts)
    state['preparation_stage'] = 'configuration'
    write_json(run / 'run.json', state)
    registry = recipe_registry(application)
    registry_generator = application / 'docs/scripts/manuale-registry.mjs'
    if registry_generator.is_file():
        execute(['node', str(registry_generator), '--check'], run, 'Check generated workflow inventory')
    changed = []
    manual_changed = []
    if args.changed_since:
        changed = changed_snapshot_paths(application, state['application_input'], args.changed_since)
    if args.manual_changed_since:
        manual_changed = changed_snapshot_paths(manual, state['manual_input'], args.manual_changed_since)
    decisions = []
    impact = {}
    state['selected_recipes'] = select_affected(registry, changed, manual_changed, root=application,
                                                decisions=decisions, base=args.changed_since, report=impact
                                                ) if args.changed_since or args.manual_changed_since else [recipe['id'] for recipe in registry['recipes']]
    state['selection'] = {'mode': 'incremental' if args.changed_since or args.manual_changed_since else 'full',
                          'algorithm': 'impact-graph-v1' if impact.get('format') else 'registered-recipe-imports-v1',
                          'decisions': decisions,
                          'changed_sources': sorted(set(changed)), 'changed_manual': sorted(set(manual_changed))}
    record_impact(state, impact)
    if getattr(args, 'workflow', None):
        known = {recipe['id'] for recipe in registry['recipes']}
        unknown = set(args.workflow) - known
        if unknown:
            raise ValueError('Unknown workflows: ' + ', '.join(sorted(unknown)))
        state['selected_recipes'] = sorted(set(args.workflow))
        state['selection']['mode'] = 'explicit'
    tooling = ['docs/manuale/recipes.json', 'docs/manuale/page-map.json', 'docs/scripts/manuale.py',
               'docs/scripts/manuale-scaffold.py', 'BE/application/manuale/index.py',
               'docs/scripts/manuale-catalog.py', 'docs/scripts/manuale-generate.mjs',
               'BE/application/management/commands/seed_manuale.py', 'BE/application/management/commands/run_manuale_instance.py',
               'selfhost/compose.dev.yml', 'selfhost/tests/browser/package-lock.json']
    tooling.extend(str(path.relative_to(application)) for path in (application / 'docs/manuale').glob('*.mjs'))
    tooling.extend(str(path.relative_to(application)) for path in (application / 'selfhost/tests/browser/manuale').glob('*.mjs'))
    tooling.extend(str(path.relative_to(application)) for path in
        (application / 'selfhost/tests/browser/manuale/fixtures').rglob('*') if path.is_file())
    tooling.append('selfhost/tests/browser/playwright.manual.config.mjs')
    if (application / 'docs/scripts/manuale-edit.py').is_file():
        tooling.append('docs/scripts/manuale-edit.py')
    if (application / 'docs/scripts/manuale-coverage.py').is_file():
        tooling.append('docs/scripts/manuale-coverage.py')
    if (application / 'BE/application/manuale/reuse.py').is_file():
        tooling.append('BE/application/manuale/reuse.py')
    for relative in (IMPACT_MODULE, IMPACT_MAP):
        if (application / relative).is_file() and relative not in tooling:
            tooling.append(relative)
    if (application / 'docs/scripts/manuale-repeatability.py').is_file():
        tooling.append('docs/scripts/manuale-repeatability.py')
    if registry_generator.is_file():
        tooling.append('docs/scripts/manuale-registry.mjs')
    if (application / 'docs/scripts/manuale-project.mjs').is_file():
        tooling.append('docs/scripts/manuale-project.mjs')
    for relative in ('docs/scripts/manuale-ci.py', 'docs/scripts/manuale-policy.py',
                     'BE/application/manuale/render_reuse.py', 'BE/application/manuale/reader_render.py'):
        if (application / relative).is_file():
            tooling.append(relative)
    state['tooling_hashes'] = {relative: sha(application / relative) for relative in sorted(set(tooling))}
    # Selection above describes actual input changes. Generated authoring files
    # must not expand an incremental run or alter manual_input provenance.
    state['preparation_stage'] = 'manual-authoring'
    write_json(run / 'run.json', state)
    try:
        state['manual_authoring'] = prepare_manual_authoring(state)
        if getattr(args, 'edit_content', False):
            state['preparation_stage'] = 'automatic-editorial-update'
            write_json(run / 'run.json', state)
            edit_content(state, args)
        state['preparation_stage'] = 'authoring-preflight'
        write_json(run / 'run.json', state)
        authoring_preflight(state)
        state['preparation_stage'] = 'evidence-reuse'
        with timed(run, 'reuse-inputs'):
            reuse_inputs(state, args)
    except Exception as exc:
        state.update(status='preparation_failed', failure={'stage': state.get('preparation_stage', 'manual-authoring'),
                     'type': type(exc).__name__, 'message': str(exc)})
        write_json(run / 'run.json', state)
        raise
    state['status'] = 'prepared'
    state.pop('preparation_stage', None)
    if args.reuse_frontend:
        state['reuse_frontend'] = str(Path(args.reuse_frontend).resolve())
    write_json(run / 'run.json', state)
    return state


def configure_runtime(state):
    """Allocate private services only when a capture or live verification needs them."""
    if state.get('env_file'):
        return
    run, application, manual = (Path(state[key]) for key in ('run', 'application', 'manual'))
    token = state['run_id']
    node_dependencies(state)
    execute([str(application / 'selfhost/bin/assozeta'), 'dev-config'], run, 'Generate private development configuration')
    env_file = application / 'selfhost/.env.dev'
    try:
        ports = {key: free_port() for key in ('DEV_UI_PORT', 'DEV_API_PORT', 'DEV_MINIO_CONSOLE_PORT')}
        preview_port = free_port()
    except OSError as exc:
        state.update(status='preparation_failed', preparation_stage='port-allocation',
                     failure={'stage': 'port-allocation', 'type': type(exc).__name__, 'message': str(exc)})
        write_json(run / 'run.json', state)
        raise RuntimeError('Cannot allocate host ports for the isolated run; inspect ' + str(run / 'run.json')) from exc
    origin = 'http://127.0.0.1:' + str(ports['DEV_UI_PORT'])
    settings = {'COMPOSE_PROJECT_NAME': state['project'], 'DBNAME': 'manuale_' + token,
                'ASSOZETA_MANUAL_RUN_ID': token, 'APP_URL': origin, 'APP_HOST': '127.0.0.1:' + str(ports['DEV_UI_PORT']),
                'SITE_ADDRESS': origin, 'CSRF_TRUSTED_ORIGINS': origin,
                'RUNNING_VERSION': state['release'], 'MANUAL_APPLICATION_REVISION': state['application_input']['revision'],
                'MANUAL_SOURCE_ROOT': '/manual-code', 'MANUAL_INDEX_PATH': '/manual-run/index.json',
                'MANUAL_ASSET_ROOT': '/manual-source',
                'MANUAL_URL': state['manual_url'] + '/docs/introduzione', **ports}
    existing = dict(line.split('=', 1) for line in env_file.read_text().splitlines() if line and not line.startswith('#') and '=' in line)
    existing.update({key: str(value) for key, value in settings.items()})
    env_file.write_text('\n'.join(key + '=' + value for key, value in existing.items()) + '\n')
    env_file.chmod(0o600)
    override = {'services': {}}
    mounts = [f'{run}:/manual-run', f'{manual}:/manual-source', f'{application}:/manual-code:ro']
    for service in ('api', 'migrate', 'worker', 'beat'):
        override['services'][service] = {'image': 'assozeta-manuale-backend:' + token, 'volumes': mounts}
    override['services']['api']['command'] = ['python', 'manage.py', 'run_manuale_instance', '--reference-date', state['reference_date']]
    override['services']['ui'] = {'image': 'assozeta-manuale-ui:' + token,
        'environment': {'DEPLOY_ENV': 'development', 'OEM_ENV': 'assozeta',
                        'ASSOZETA_MANUAL_BUILD': '1', 'NODE_OPTIONS': '--max-old-space-size=3072'},
        'command': ['npm', 'exec', 'vite', 'preview', '--', '--host', '0.0.0.0', '--port', '5001', '--strictPort']}
    override['services']['renderer'] = {'image': 'assozeta-manuale-renderer:' + token}
    write_json(run / 'compose.override.json', override)
    state.update({'env_file': str(env_file), 'override': str(run / 'compose.override.json'), 'origin': origin,
                  'manual_preview_url': 'http://127.0.0.1:' + str(preview_port),
                  'ports': ports})
    state.pop('preparation_stage', None)
    write_json(run / 'run.json', state)


def node_dependencies(state):
    """Share installed Node dependencies without requiring a browser or services."""
    browser_modules = ROOT / 'selfhost/tests/browser/node_modules'
    if not browser_modules.exists():
        raise RuntimeError('Install browser dependencies with npm --prefix selfhost/tests/browser ci')
    destination = Path(state['application']) / 'selfhost/tests/browser/node_modules'
    if destination.is_symlink() and destination.resolve() == browser_modules.resolve():
        return
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('Run has unexpected Node dependencies: ' + str(destination))
    destination.symlink_to(browser_modules, target_is_directory=True)


def start(state):
    configure_runtime(state)
    run = Path(state['run'])
    execute(compose(state, 'config', '--quiet'), run, 'Validate isolated service configuration')
    execute(compose(state, 'build', 'api', 'ui', 'renderer'), run, 'Build run-specific application images')
    execute(compose(state, 'up', '-d', '--wait', 'postgres', 'redis', 'minio', 'renderer'), run, 'Start run-owned dependencies')
    execute(compose(state, '--profile', 'tools', 'run', '--rm', 'minio-init'), run, 'Initialize isolated object storage')
    execute(compose(state, '--profile', 'tools', 'run', '--rm', 'migrate'), run, 'Migrate isolated database')
    execute(management(state, 'seed_manuale',
                    '--reference-date', state['reference_date'], '--origin', state['origin'], '--output', '/manual-run/browser-input.json'),
            run, 'Seed fictional association and private browser session')
    if 'docs/scripts/manuale-repeatability.py' in state['tooling_hashes']:
        execute([sys.executable, str(Path(state['application']) / 'docs/scripts/manuale-repeatability.py'),
                 'fixture', '--run', str(run)], run, 'Retain the public fixture fingerprint before credential cleanup')
    frontend = None
    if state.get('reuse_frontend'):
        print('Reuse the verified compiled frontend after validating every UI/build input', flush=True)
        frontend = reuse_frontend(state)
    else:
        for candidate in state.get('frontend_candidates', []):
            try:
                frontend = reuse_frontend({**state, 'reuse_frontend': candidate})
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                state['cache']['decisions'].append({'run': candidate, 'kind': 'frontend', 'result': 'rejected', 'reason': str(exc)})
            else:
                state['cache']['decisions'].append({'run': candidate, 'kind': 'frontend', 'result': 'reused'})
                break
    if frontend is None:
        execute(compose(state, 'run', '--rm', '--no-deps', 'ui', 'npm', 'exec', 'vite', 'build'),
                run, 'Compile the real frontend before browser verification')
        source = Path(state['application']) / 'UI/dist/public'
        frontend = {'sources': ui_sources(Path(state['application'])),
            'artifacts': {str(file.relative_to(source)): sha(file) for file in sorted(source.rglob('*')) if file.is_file()}}
    write_json(run / 'frontend-build.json', frontend)
    execute(compose(state, 'up', '-d', '--wait', 'api', 'ui', 'worker'), run,
            'Start application and run-owned worker for real receipt/PDF processing')
    state['status'] = 'running'
    write_json(run / 'run.json', state)


def capture(state):
    run = Path(state['run'])
    validate_tooling(state)
    retained = validate_retained_evidence(state)
    if not state['selected_recipes']:
        if not retained or not retained['reused_recipes']:
            raise RuntimeError('No fresh captures or compatible retained workflow evidence selected')
        state.update(status='captured', capture_mode='retained-only')
        write_json(run / 'run.json', state)
        return
    baseline = run / 'capture-baseline'
    previous = [run / (recipe + '.json') for recipe in state['selected_recipes']]
    if previous and all(file.exists() and json.loads(file.read_text()).get('status') == 'passed' for file in previous):
        if baseline.exists():
            shutil.rmtree(baseline)
        baseline.mkdir()
        for file in previous:
            report = json.loads(file.read_text())
            shutil.copy2(file, baseline / file.name)
            for capture in report['screenshots']:
                relative = Path(capture['path'])
                if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'images':
                    raise RuntimeError('Refusing an unowned capture path')
                target = baseline / 'captures' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(run / 'captures' / relative, target)
                if capture.get('master'):
                    master = Path(capture['master']['path'])
                    if master != Path('masters') / relative:
                        raise RuntimeError('Refusing an unowned Full HD master')
                    target = baseline / master
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(run / master, target)
    state.update({'status': 'capturing', 'capture_id': secrets.token_hex(8)})
    write_json(run / 'run.json', state)
    environment = {**os.environ, 'ASSOZETA_MANUAL_RUN': str(run), 'ASSOZETA_MANUAL_APPLICATION': state['application']}
    capture_error = None
    batch = {'format': 1, 'status': 'running', 'capture_id': state['capture_id'], 'scenarios': []}
    try:
        for recipe in recipe_registry(Path(state['application']))['recipes']:
            if recipe['id'] not in state['selected_recipes']:
                continue
            execute(management(state, 'seed_manuale', '--reference-date', state['reference_date'],
                            '--origin', state['origin'], '--output', '/manual-run/browser-input.json',
                            *recipe_seed_options(recipe)),
                    run, 'Reset owned scenario state and browser session for ' + recipe['id'])
            try:
                execute(['node', str(Path(state['application']) / recipe['script'])], run,
                        'Verify real application scenario ' + recipe['id'], env=environment, cwd=state['application'])
            except (RuntimeError, subprocess.CalledProcessError) as error:
                # Every workflow is reseeded independently. Retain all failures
                # in one diagnostic batch, without generating a partial corpus.
                capture_error = capture_error or error
                batch['scenarios'].append({'id': recipe['id'], 'status': 'failed'})
            else:
                batch['scenarios'].append({'id': recipe['id'], 'status': 'passed'})
            write_json(run / 'capture-execution.json', batch)
        failures = [entry['id'] for entry in batch['scenarios'] if entry['status'] == 'failed']
        if failures:
            raise RuntimeError('Capture batch failed for ' + ', '.join(failures) + '; first failure: ' + str(capture_error)) from capture_error
    except BaseException as error:
        capture_error = error
        raise
    finally:
        # The next scenario already resets before running. One final reset also
        # clears abandoned uploads/people and token links from the final scenario,
        # including failed captures, using the same disposable database guard.
        try:
            execute(management(state, 'seed_manuale', '--reference-date', state['reference_date'],
                            '--origin', state['origin'], '--output', '/manual-run/browser-input.json'),
                    run, 'Restore baseline after the capture batch')
            state['fixture_reset'] = 'passed'
        except Exception:
            state['fixture_reset'] = 'failed'
            if capture_error is None:
                raise
        finally:
            batch['status'] = 'failed' if capture_error or state['fixture_reset'] != 'passed' else 'passed'
            write_json(run / 'capture-execution.json', batch)
            write_json(run / 'run.json', state)
    if baseline.exists():
        execute(management(state, 'compare_manuale_captures', '--run', '/manual-run',
            '--output', '/manual-run/variance.json'), run, 'Measure repeated screenshot variance and enforce identical-input stability')
    state['status'] = 'captured'
    write_json(run / 'run.json', state)
    remember_run(state)


def generate(state):
    validate_tooling(state)
    # The generator validates retained proof before writing MDX; the index
    # independently validates it before accepting the resulting corpus.
    catalog = str(Path(state['application']) / 'docs/scripts/manuale-catalog.py')
    execute([sys.executable, catalog, '--run', state['run'], '--preflight'], Path(state['run']),
            'Validate every navigation/backlog target against real application source and Git', cwd=state['application'])
    execute(['node', str(Path(state['application']) / 'docs/scripts/manuale-generate.mjs'), state['run']], Path(state['run']),
            'Generate verified Italian sections and complete coverage manifest', cwd=state['application'],
            env={**os.environ, 'ASSOZETA_MANUAL_PYTHON': sys.executable})
    execute([sys.executable, catalog, '--run', state['run']], Path(state['run']),
            'Bind section evidence to actual Git blobs and inventory every unverified section', cwd=state['application'])
    state['status'] = 'generated'
    write_json(Path(state['run']) / 'run.json', state)
    remember_run(state)


def compile_content(state):
    validate_tooling(state)
    node_dependencies(state)
    execute(['node', str(Path(state['application']) / 'selfhost/tests/browser/manuale/compile.mjs'), state['run']],
            Path(state['run']), 'Compile MDX and validate local navigation, anchors and images')


def build(state):
    """Build a complete candidate corpus from existing evidence, without services."""
    validate_tooling(state)
    retained = validate_retained_evidence(state)
    run = Path(state['run'])
    available = set(retained['reused_recipes'] if retained else [])
    for identifier in state['selected_recipes']:
        report = run / (identifier + '.json')
        if report.is_file():
            value = json.loads(report.read_text())
            if value.get('id') == identifier and value.get('status') == 'passed':
                available.add(identifier)
    missing = [recipe['id'] for recipe in recipe_registry(Path(state['application']))['recipes']
               if recipe['id'] not in available]
    if missing:
        write_json(run / 'build.json', {'format': 1, 'status': 'needs-capture', 'workflows': missing})
        raise RuntimeError('Build needs compatible captures for: ' + ', '.join(missing)
                           + '. Run capture for these workflows or supply a compatible --reuse-evidence baseline.')
    state['status'] = 'captured'
    write_json(run / 'run.json', state)
    report = {'format': 1, 'status': 'running', 'workflows': sorted(available),
              'scope': 'offline-candidate', 'release_verified': False}
    write_json(run / 'build.json', report)
    try:
        for phase in (generate, compile_content, index):
            report['phase'] = phase.__name__
            with timed(run, phase.__name__):
                phase(state)
    except Exception:
        report['status'] = 'failed'
        write_json(run / 'build.json', report)
        raise
    report.update(status='passed', phase='complete')
    write_json(run / 'build.json', report)


def index(state):
    # Evidence validation needs the real Git worktree/index. The API image does
    # not contain Git, and relocated mounts cannot resolve host worktree links.
    # Execute the sealed, database-independent builder beside those worktrees.
    validate_tooling(state)
    run = Path(state['run'])
    builder_path = Path(state['application']) / 'BE/application/manuale/index.py'
    spec = importlib.util.spec_from_file_location('manual_host_index', builder_path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    print('Validate evidence and atomically build retrieval corpus on the host', flush=True)
    result = builder.build_index(state['manual'], state['application'],
                                 json.loads((run / 'manifest.json').read_text()), artifact_root=run)
    builder.promote(result, run / 'index.json')
    state['status'] = 'indexed'
    write_json(run / 'run.json', state)


def verify(state, after_render=None):
    """Check generated pages through the in-app reader projection.

    Mintlify is no longer required: the in-app reader and the release bundle
    built from ``manuale/`` are the publication source of truth. This phase
    needs no preview server or browser; the real-browser reader check runs
    against the real backend in ``evaluate`` (embedded.mjs).
    """
    validate_tooling(state)
    run = Path(state['run'])
    # Load the verifier from this trusted runner, never from a downloaded cache.
    sys.path.insert(0, str(ROOT / 'BE'))
    from application.manuale.render_reuse import plan_render_reuse, validate_render_reuse
    from application.manuale.reader_render import READER_CHECKS, READER_RENDERER, render_pages
    plan = plan_render_reuse(state, state.get('reuse_evidence'))
    manifest = json.loads((run / 'manifest.json').read_text())
    for page in manifest['pages']:
        retained = plan['pages'].get(page['path'].removesuffix('.mdx'))
        if retained:
            for section in page['sections']:
                if section['id'] in retained['citation_anchors']:
                    section['citation_anchor'] = retained['citation_anchors'][section['id']]
    write_json(run / 'manifest.json', manifest)
    validate_render_reuse(state)
    print(f"Render decision: {len(plan['fresh_pages'])} fresh pages, {len(plan['reused_pages'])} retained pages", flush=True)
    report = {'status': 'running', 'renderer': READER_RENDERER,
              'mode': 'retained-only' if not plan['fresh_pages'] else ('partial' if plan['reused_pages'] else 'fresh'),
              'fresh_pages': plan['fresh_pages'], 'reused_pages': plan['reused_pages'],
              'pages': [plan['pages'][page]['render'] for page in plan['reused_pages']]}
    try:
        if plan['fresh_pages']:
            print('Verify generated pages, citation anchors and displayed images through the in-app reader projection', flush=True)
            report['pages'] += render_pages(state['manual'], manifest, plan['fresh_pages'])
            report['checks'] = list(READER_CHECKS)
        else:
            report['checks'] = ['original successful render evidence revalidated against exact current content']
        if not report['pages']:
            raise ValueError('No verified pages to render')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report.update(status='failed', error=str(exc))
        write_json(run / 'render.json', report)
        raise RuntimeError('Manual render verification failed: ' + str(exc)) from exc
    report['status'] = 'passed'
    # Publish the anchor mappings only after every page and image passed.
    write_json(run / 'manifest.json', manifest)
    write_json(run / 'render.json', report)
    validate_render_reuse(state)
    print('Manual render verification: passed', flush=True)
    state['status'] = 'rendered'
    write_json(run / 'run.json', state)
    if after_render:
        after_render()


def cleanup(state):
    run = Path(state['run']).resolve()
    if not re.fullmatch(r'[a-z0-9]{8,32}', state['run_id']) or state['project'] != 'assozeta-manuale-' + state['run_id']:
        raise RuntimeError('Refusing cleanup of an unowned project')
    if Path(state['override']).resolve().parent != run or Path(state['application']).resolve().parent != run:
        raise RuntimeError('Run paths are not owned by this cleanup')
    execute(compose(state, '--profile', 'tools', 'down', '--volumes', '--remove-orphans'), run, 'Remove only this run’s services and data volumes')
    remaining = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + state['project']], text=True).strip()
    if remaining:
        raise RuntimeError('Run-owned containers remain after cleanup')
    # Worktrees and diagnostics remain reviewable; credentials cease to be useful
    # after database removal and are removed from the retained evidence bundle.
    (run / 'browser-input.json').unlink(missing_ok=True)
    state['services_removed'] = True
    write_json(run / 'run.json', state)


def evaluate(state):
    # Association identity is read from the privately generated fixture, not a
    # remote caller's override. The evaluation report contains no session token.
    run = Path(state['run'])
    fixture = json.loads((run / 'browser-input.json').read_text())
    execute(management(state, 'evaluate_manuale',
                    '--association-id', fixture['association_id'], '--output', '/manual-run/evaluation.json'),
            run, 'Exercise manual retrieval and cited answers through existing MCP and agent')
    execute(['node', str(Path(state['application']) / 'selfhost/tests/browser/manuale/embedded.mjs')],
            run, 'Verify sidebar, header, embedded steps and screenshots against the real backend',
            env={**os.environ, 'ASSOZETA_MANUAL_RUN': str(run)}, cwd=state['application'])
    state['status'] = 'evaluated'
    write_json(run / 'run.json', state)


def install_dev(state):
    """Install into the existing development API; never migrate or seed it."""
    run = Path(state['run']).resolve()
    if run.parent != ROOT / 'quality-reports' / 'manuale':
        raise RuntimeError('Development installation requires a run owned by this checkout')
    cli = str(ROOT / 'selfhost/bin/assozeta')
    execute([cli, 'dev-compose', 'up', '-d', '--no-deps', 'api'], run,
            'Apply the live-source mount to the existing development API only')
    execute([cli, 'dev-compose', 'exec', '-T', 'api', 'python', 'manage.py',
             'install_manuale_development', '--run', '/manual-code/quality-reports/manuale/' + state['run_id']],
            run, 'Install the verified manual in the existing development reader and MCP')


def export(state):
    run = Path(state['run'])
    destination = run / 'export'
    destination.mkdir(exist_ok=True)
    if (run / 'masters').exists():
        shutil.copytree(run / 'masters', destination / 'masters', dirs_exist_ok=True)
    if state.get('evidence_reuse'):
        validate_retained_evidence(state)
        for directory in ('captures', 'evidence-origin'):
            if (run / directory).exists():
                shutil.copytree(run / directory, destination / directory, dirs_exist_ok=True)
    for relative in ('run.json', 'manifest.json', 'coverage.json', 'source-catalog.json', 'index.json', 'evaluation.json', 'render.json',
                     'evidence-reuse.json', 'render-reuse.json', 'render-inputs.json',
                     'authoring-preflight.json', 'workflow-coverage.json',
                     'tags-create-assign.json', 'manual-preview.png', 'variance.json', 'frontend-build.json',
                     'embedded-manual.json', 'embedded-manual.png', 'runtime-preflight.json',
                     'compile.json', 'build.json', 'timings.json'):
        source = run / relative
        if source.exists():
            shutil.copy2(source, destination / relative)
    for recipe in recipe_registry(Path(state['application']))['recipes']:
        source = run / (recipe['id'] + '.json')
        if source.exists():
            shutil.copy2(source, destination / source.name)
    if (run / 'render-origins').exists():
        shutil.copytree(run / 'render-origins', destination / 'render-origins', dirs_exist_ok=True)
    if (run / 'renders').exists():
        target = destination / 'renders'
        target.mkdir(exist_ok=True)
        for source in (run / 'renders').iterdir():
            if source.is_file() and source.suffix in ('.png', '.html'):
                shutil.copy2(source, target / source.name)
    manifest = json.loads((run / 'manifest.json').read_text())
    review_paths = set()
    for page in manifest['pages']:
        if not any(section.get('status') == 'verified' for section in page['sections']):
            continue
        target = destination / 'manual' / page['path']
        review_paths.add(page['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(state['manual']) / page['path'], target)
        for section in page['sections']:
            for image in section.get('screenshots', []):
                review_paths.add(image['path'])
                target = destination / 'manual' / image['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(state['manual']) / image['path'], target)
    # Intent-to-add includes new screenshots in the review patch without
    # staging their contents or committing the companion worktree.
    subprocess.run(['git', '-C', state['manual'], 'add', '--intent-to-add', '--', *sorted(review_paths)], check=True)
    patch = subprocess.check_output(['git', '-C', state['manual'], 'diff', 'HEAD', '--binary'])
    (destination / 'manual.patch').write_bytes(patch)
    print('Reviewable public-fixture artifacts: ' + str(destination), flush=True)


def verify_release(state, args):
    """Package the completed target once, then exercise its appliance boundary."""
    run = Path(state['run'])
    package = (Path(args.package_output).resolve() if args.package_output else
               Path(tempfile.mkdtemp(prefix='assozeta-manual-release-')) / 'package')
    report = {'status': 'running', 'release': state['release'], 'package': str(package),
              'previous_release': args.previous_release, 'publication': False}
    write_json(run / 'release-verification.json', report)
    try:
        with timed(run, 'package'):
            execute([sys.executable, str(ROOT / 'docs/scripts/manuale-release.py'), '--bundle',
                     '--run', str(run), '--application-repo', str(ROOT),
                     '--manual-repo', state['manual_repository'], '--release', state['release'],
                     '--output', str(package)], run, 'Package the evaluated target manual')
        with timed(run, 'appliance-rehearsal'):
            execute([sys.executable, str(ROOT / 'selfhost/tests/manual-release-smoke.py'),
                     str(package / 'runtime'), state['release'], '--previous-release', args.previous_release],
                    run, 'Verify fresh install and historical appliance upgrade/rollback')
        report['status'] = 'passed'
        print('Verified release package: ' + str(package), flush=True)
    except Exception:
        report['status'] = 'failed'
        raise
    finally:
        write_json(run / 'release-verification.json', report)


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['doctor', 'prepare', 'build', 'start', 'capture', 'generate', 'verify', 'index', 'evaluate', 'export', 'cleanup', 'install-dev', 'run', 'verify-release'])
    parser.add_argument('--run', help='Resume the recorded run directory')
    parser.add_argument('--manual-repo', default=str(ROOT.parent / 'manuale'))
    parser.add_argument('--application-ref', default='HEAD')
    parser.add_argument('--manual-ref', default='HEAD')
    parser.add_argument('--manual-url', default='https://manuale.bakney.com')
    parser.add_argument('--reference-date', default='2026-09-30')
    parser.add_argument('--release', default='manuale-development', help='Exact running version for this corpus; production indexes must match their target image version')
    parser.add_argument('--previous-release', default='1.0.17', help='Published historical version for verify-release appliance rehearsal')
    parser.add_argument('--package-output', help='New release package directory outside both input repositories and the run; default: an owned temporary directory')
    parser.add_argument('--output')
    parser.add_argument('--allow-dirty', action='store_true')
    parser.add_argument('--standalone-checkouts', action='store_true',
                        help='Use owned local clones when the input repositories have read-only Git metadata')
    parser.add_argument('--changed-since', help='Incremental verification using application changes since this Git ref')
    parser.add_argument('--manual-changed-since', help='Incremental verification using manual changes since this Git ref')
    parser.add_argument('--workflow', action='append', metavar='ID',
                        help='Capture a named workflow; repeat for several workflows')
    parser.add_argument('--no-cache', action='store_true', help='Use a cold build/capture without automatic evidence or frontend reuse')
    parser.add_argument('--reuse-frontend', help='Reuse a rendered run’s compiled bundle after verifying identical UI inputs')
    parser.add_argument('--reuse-evidence', metavar='BASELINE_RUN',
                        help='Retain individually compatible unselected workflows from a previously verified run')
    parser.add_argument('--edit-content', action='store_true', help='Generate bounded Codex drafts before existing capture/verification')
    parser.add_argument('--editor-profile', help='Explicit installed Codex profile for editorial generation')
    parser.add_argument('--editor-model', help='Explicit model override; otherwise use the CLI default')
    parser.add_argument('--editor-timeout', type=int, default=180, help='Seconds per editorial model invocation')
    parser.add_argument('--editor-max-sections', type=int, default=12, help='Sections per editorial batch, 1..24')
    parser.add_argument('--keep-services', action='store_true')
    parser.add_argument('--install-dev', action='store_true', help='Install the completed verification in the existing development API without seeding its data')
    return parser


def main():
    parser = argument_parser()
    args = parser.parse_args()
    if args.command == 'verify-release' and (args.allow_dirty or args.edit_content or args.install_dev):
        parser.error('verify-release requires committed inputs without editorial generation or development installation')
    if args.no_cache and (args.reuse_evidence or args.reuse_frontend):
        parser.error('--no-cache cannot be combined with explicit reuse')
    if args.run and args.workflow:
        parser.error('--workflow selects a new capture; a retained --run keeps its recorded selection')
    if args.workflow and (args.changed_since or args.manual_changed_since):
        parser.error('Use --workflow or affected-workflow selection, not both')
    runtime_report = None
    if args.command in ('doctor', 'run', 'capture', 'verify-release'):
        runtime_report = runtime_check()
        print_runtime_check(runtime_report)
        if args.command == 'doctor' and args.output:
            target = Path(args.output).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            write_json(target, runtime_report)
        if runtime_report['status'] != 'passed':
            sys.exit(2)
        if args.command == 'doctor':
            return
    state = json.loads((Path(args.run) / 'run.json').read_text()) if args.run else None
    try:
        new_capture = args.command == 'capture' and state is None
        if args.command in ('prepare', 'build', 'capture', 'run', 'verify-release') and state is None:
            state = prepare(args)
            print('Evidence/worktrees: ' + state['run'], flush=True)
        if args.command == 'prepare':
            return
        if state is None:
            parser.error('--run is required for this command')
        if runtime_report:
            write_json(Path(state['run']) / 'runtime-preflight.json', runtime_report)
        if args.command == 'verify-release' and args.run:
            # Packaging revalidates every required acceptance artifact. A
            # completed run can cross this boundary without another generation.
            verify_release(state, args)
            return
        if new_capture:
            try:
                # Seal the captured section bindings so a subsequent build
                # discovers this result automatically, including partial runs.
                for phase in (start, capture, generate):
                    with timed(state['run'], phase.__name__):
                        phase(state)
            finally:
                if not args.keep_services and state.get('env_file'):
                    cleanup(state)
            return
        if args.command in ('run', 'verify-release'):
            retained = validate_retained_evidence(state)
            if not state['selected_recipes'] and not (retained and retained['reused_recipes']):
                if retained:
                    raise RuntimeError('No compatible retained workflow evidence remains; inspect evidence-reuse.json '
                                       'and select fresh recipes before building the target corpus')
                state['status'] = 'unaffected'
                write_json(Path(state['run']) / 'run.json', state)
                print('No implemented recipe depends on these changes; existing corpus is unchanged. Full verification remains available.', flush=True)
                return
            original_failure = None
            try:
                for phase in (start, capture, generate, compile_content):
                    with timed(state['run'], phase.__name__):
                        phase(state)
                # Index, evaluate and export only after the render check passed.
                def complete_verification():
                    for phase in (index, evaluate, export):
                        with timed(state['run'], phase.__name__):
                            phase(state)
                    if args.install_dev:
                        install_dev(state)
                verify(state, after_render=complete_verification)
            except Exception as exc:
                original_failure = exc
                try:
                    execute(compose(state, '--profile', 'tools', 'logs', '--no-color'), Path(state['run']), 'Retain private service diagnostics')
                except Exception as diagnostic_error:
                    print('Service diagnostics unavailable: ' + str(diagnostic_error), file=sys.stderr)
                raise
            finally:
                if not args.keep_services and state.get('env_file'):
                    try:
                        cleanup(state)
                    except Exception as exc:
                        if original_failure:
                            raise RuntimeError(f'{original_failure}; cleanup also failed: {exc}') from original_failure
                        raise
            if args.command == 'verify-release':
                verify_release(state, args)
        else:
            with timed(state['run'], args.command):
                globals()[args.command.replace('-', '_')](state)
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        if state and args.command != 'install-dev' and not (
                args.command == 'verify-release' and state.get('status') == 'evaluated'):
            state.update({'status': 'failed', 'error': str(exc)})
            write_json(Path(state['run']) / 'run.json', state)
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
