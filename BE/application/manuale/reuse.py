"""Offline compatibility of retained captures; never creates a browser result.

The compatibility record targets a new corpus. Original reports, revisions,
nonces and image bytes remain immutable. Ordinary runtime version gates remain
outside this module. ReuseError is deliberately a ValueError for existing gates.
"""
import argparse
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from application.manuale.index import canonical, digest, sections, verify_capture_frame


class ReuseError(ValueError):
    pass


CORE_TOOLING = (
    'BE/application/management/commands/seed_manuale.py',
    'BE/application/management/commands/run_manuale_instance.py',
    'selfhost/tests/browser/playwright.manual.config.mjs',
    'selfhost/tests/browser/manuale/scenario.mjs',
    'selfhost/tests/browser/manuale/frame.mjs',
    'selfhost/tests/browser/manuale/redaction.mjs',
    'docs/manuale/manual-content.mjs',
)
CONFIG_INPUTS = (
    'selfhost/compose.dev.yml', 'selfhost/tests/browser/package.json',
    'selfhost/tests/browser/package-lock.json', 'UI/package.json',
    'UI/package-lock.json', 'UI/vite.config.js', 'BE/requirements.txt',
)
ID = re.compile(r'[a-z0-9][a-z0-9-]*\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
IMPORT = re.compile(r'''(?:\b(?:import|export)\s+(?:[^;]*?\s+from\s+)?|\b(?:import|require)\s*\(\s*)["'](\.[^"']+)["']''')


def owned(root, relative):
    """Reject traversal, absolute paths and symlinks, including parent symlinks."""
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ReuseError('invalid-path')
    parts = Path(relative).parts
    if Path(relative).is_absolute() or any(part in ('.', '..') for part in parts):
        raise ReuseError('unowned-path: ' + relative)
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise ReuseError('symlink-path: ' + relative)
    if not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise ReuseError('unowned-path: ' + relative)
    return target


def file_hash(root, relative):
    try:
        return digest(owned(root, relative).read_bytes())
    except OSError as exc:
        raise ReuseError('missing-file: ' + relative) from exc


def load(root, relative):
    try:
        return json.loads(owned(root, relative).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReuseError('invalid-json: ' + relative) from exc


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise ReuseError('snapshot-git-binding-unavailable')
    return result.stdout


@lru_cache(maxsize=8192)
def committed_hash(root, revision, relative):
    # Git objects are immutable. Working-tree bytes are deliberately reread on
    # every check so an edit during validation still invalidates the evidence.
    return digest(git(root, 'show', revision + ':' + relative))


def verify_snapshot(root, snapshot, paths):
    """Every retained byte must match the recorded dirty input or real Git blob."""
    revision = snapshot.get('revision', '')
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ReuseError('invalid-snapshot-revision')
    git(root, 'cat-file', '-e', revision + '^{commit}')
    dirty = snapshot.get('uncommitted_files', {})
    for relative in sorted(set(paths)):
        target = owned(root, relative)
        if relative in dirty:
            expected = dirty[relative]
            if expected is None:
                if target.exists():
                    raise ReuseError('deleted-snapshot-input-reappeared: ' + relative)
                continue
            if not isinstance(expected, str) or not HASH.fullmatch(expected) or file_hash(root, relative) != expected:
                raise ReuseError('dirty-snapshot-input-changed: ' + relative)
        else:
            expected = committed_hash(str(Path(root).resolve()), revision, relative)
            if file_hash(root, relative) != expected:
                raise ReuseError('committed-snapshot-input-changed: ' + relative)


def registry(root):
    value = load(root, 'docs/manuale/recipes.json')
    if value.get('format') != 1 or not isinstance(value.get('shared_dependencies'), list):
        raise ReuseError('invalid-recipe-registry')
    recipes = value.get('recipes', [])
    if len({item.get('id') for item in recipes}) != len(recipes):
        raise ReuseError('duplicate-recipe-id')
    for item in recipes:
        if not ID.fullmatch(item.get('id', '')) or not isinstance(item.get('version'), int):
            raise ReuseError('invalid-recipe-contract')
    return value, {item['id']: item for item in recipes}


def inventory(root, prefixes):
    names = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').decode().split('\0')
    matched = {name for name in names if name and any(name.startswith(prefix) for prefix in prefixes)}
    # Registered production prefixes have to account for additions and deletions.
    return matched, {name for name in matched if owned(root, name).is_file()}


def closure(root, recipe):
    pending = [recipe['script'], *CORE_TOOLING]
    for dependency in recipe.get('dependencies', []):
        if dependency.endswith(('.mjs', '.js', '.cjs')):
            pending.append(dependency)
        elif dependency.endswith('/') and dependency.startswith(('docs/manuale/', 'selfhost/tests/browser/manuale/')):
            pending.extend(sorted(inventory(root, [dependency])[1]))
    for relative in CONFIG_INPUTS:
        if owned(root, relative).exists():
            pending.append(relative)
    found = set()
    while pending:
        relative = pending.pop()
        if relative in found:
            continue
        found.add(relative)
        text = owned(root, relative).read_text()
        if not relative.endswith(('.mjs', '.js', '.cjs')):
            continue
        for specifier in IMPORT.findall(text):
            candidate = Path(relative).parent / specifier
            # Relative imports normally contain '..'; resolve them before owned().
            resolved = (Path(root) / candidate).resolve()
            if not resolved.is_relative_to(Path(root).resolve()):
                raise ReuseError('tooling-import-escapes-checkout')
            imported = resolved.relative_to(Path(root).resolve()).as_posix()
            if not Path(imported).suffix:
                alternatives = [imported + suffix for suffix in ('.mjs', '.js', '.cjs')]
                alternatives += [imported + '/index' + suffix for suffix in ('.mjs', '.js', '.cjs')]
                imported = next((name for name in alternatives if owned(root, name).is_file()), imported)
            if imported.endswith(('.mjs', '.js', '.cjs')):
                pending.append(imported)
    return found


def dependency_contract(root, value, recipe):
    prefixes = [prefix for prefix in value['shared_dependencies'] + recipe.get('dependencies', [])
                if prefix.startswith(('BE/', 'UI/'))]
    known, production = inventory(root, prefixes)
    tooling = closure(root, recipe)
    return known | tooling | {'docs/manuale/recipes.json'}, production, tooling


def fixture_version(root):
    text = owned(root, 'BE/application/management/commands/seed_manuale.py').read_text()
    match = re.search(r'^FIXTURE_VERSION\s*=\s*(\d+)\s*$', text, re.M)
    if not match:
        raise ReuseError('unknown-fixture-version')
    return int(match[1])


def body_hash(manual, relative, identifier, title):
    available = sections(owned(manual, relative).read_text())
    item = available.get(identifier)
    if not item or item['title'] != title:
        raise ReuseError('editorial-section-removed: ' + relative + '#' + identifier)
    body = re.sub(r'(/images/[^\s"\')]+)\.placeholder\.svg', r'\1.png', item['mdx'].strip())
    return digest(body)


def check_editorial(state, entries):
    sidecar = load(state['manual'], '.manuale-evidence.json')
    if sidecar.get('format') != 1 or sidecar.get('purpose') != 'reviewed-content' or sidecar.get('verified') is not False:
        raise ReuseError('invalid-editorial-sidecar')
    for item in entries:
        key = item['path'] + '#' + item['id']
        binding = sidecar.get('sections', {}).get(key, {})
        actual = body_hash(state['manual'], item['path'], item['id'], item['title'])
        if actual not in {item['editorial_content_sha256'], item['published_content_sha256']} or binding.get('content_sha256') != actual:
            raise ReuseError('editorial-content-changed: ' + key)
        if (binding.get('title') != item['title'] or binding.get('recipe_module') != item['recipe_module']
                or binding.get('recipe_sha256') != item['recipe_sha256']
                or file_hash(state['application'], item['recipe_module']) != item['recipe_sha256']):
            raise ReuseError('editorial-recipe-binding-changed: ' + key)
        if binding.get('editorial_content_sha256', actual) != item['editorial_content_sha256']:
            raise ReuseError('editorial-origin-changed: ' + key)
        if (actual == item['published_content_sha256'] and (actual != item['editorial_content_sha256'] or binding.get('materialization'))
                and binding.get('materialization') != item['materialization']):
            raise ReuseError('editorial-capture-provenance-changed: ' + key)


def origin_sections(old, manifest, identifier, report_hash, report):
    sidecar = load(old['manual'], '.manuale-evidence.json')
    result = []
    for page in manifest['pages']:
        for item in page.get('sections', []):
            if item.get('status') != 'verified' or identifier not in item.get('scenario_ids', []):
                continue
            if item.get('scenario_ids') != [identifier]:
                raise ReuseError('multi-scenario-editorial-binding-requires-fresh-capture')
            key = page['path'] + '#' + item['id']
            binding = sidecar.get('sections', {}).get(key, {})
            title = binding.get('title', '')
            actual = body_hash(old['manual'], page['path'], item['id'], title)
            materialization = {'format': 1, 'capture_id': report['capture_id'], 'scenario_id': identifier, 'report_sha256': report_hash}
            if actual != item.get('content_sha256') or binding.get('content_sha256') != actual or binding.get('materialization') != materialization:
                raise ReuseError('origin-editorial-binding-invalid: ' + key)
            result.append({'path': page['path'], 'id': item['id'], 'title': title,
                           'editorial_content_sha256': binding.get('editorial_content_sha256', actual),
                           'published_content_sha256': actual, 'recipe_module': binding['recipe_module'],
                           'recipe_sha256': binding['recipe_sha256'], 'materialization': materialization})
    if not result:
        raise ReuseError('no-retained-verified-editorial-sections')
    return result


def check_report(state, recipe, report, origin):
    if (report.get('id') != recipe['id'] or report.get('status') != 'passed' or report.get('backend') != 'real'
            or report.get('application_revision') != origin['application_revision'] or report.get('capture_id') != origin['capture_id']):
        raise ReuseError('original-report-provenance-invalid')
    expected = {'reference_date': state['reference_date'], 'fixture_profile': recipe.get('fixture', 'baseline'),
                'fixture_version': fixture_version(state['application']), 'capture_format': 'full-hd-v1',
                'viewport': {'width': 1920, 'height': 1080}, 'device_scale_factor': 1,
                'locale': 'it-IT', 'timezone': 'Europe/Rome', 'theme': 'light'}
    for key, value in expected.items():
        if report.get(key) != value:
            raise ReuseError('capture-settings-changed: ' + key)
    if not report.get('checks') or not report.get('source_hashes') or not report.get('screenshots'):
        raise ReuseError('incomplete-original-report')
    prefix = recipe.get('capture_prefix', '')
    if not prefix.startswith('images/') or not prefix.endswith('/'):
        raise ReuseError('missing-owned-capture-prefix')
    paths, checkpoints = set(), set()
    for frame in report['screenshots']:
        if not frame.get('path', '').startswith(prefix) or frame['path'] in paths or not frame.get('checkpoint') or frame['checkpoint'] in checkpoints:
            raise ReuseError('invalid-original-checkpoints')
        paths.add(frame['path']); checkpoints.add(frame['checkpoint'])
        owned(state['run'], 'captures/' + frame['path'])
        owned(state['run'], frame.get('master', {}).get('path', ''))
        verify_capture_frame(state['run'], frame)
    for relative, expected_hash in report['source_hashes'].items():
        if not relative.startswith(('BE/', 'UI/')) or file_hash(state['application'], relative) != expected_hash:
            raise ReuseError('captured-source-changed: ' + relative)


def target(state):
    return {'application_revision': state['application_input']['revision'], 'release': state['release'],
            'tooling_hashes_sha256': digest(canonical(state['tooling_hashes'])), 'reference_date': state['reference_date']}


def _entry(state, old, identifier, old_registry, new_registry, old_recipes, new_recipes, manifest, prior_plan):
    previous, current = old_recipes.get(identifier), new_recipes[identifier]
    if previous != current or old_registry['shared_dependencies'] != new_registry['shared_dependencies']:
        raise ReuseError('recipe-version-script-or-dependency-contract-changed')
    old_known, old_production, old_tooling = dependency_contract(old['application'], old_registry, previous)
    new_known, production, tooling = dependency_contract(state['application'], new_registry, current)
    if old_production != production or old_tooling != tooling:
        raise ReuseError('dependency-closure-added-or-deleted')
    report_path = identifier + '.json'
    report = load(old['run'], report_path)
    hashes = report.get('source_hashes', {})
    verify_snapshot(old['application'], old['application_input'], old_known | set(hashes))
    verify_snapshot(state['application'], state['application_input'], new_known | set(hashes))
    dependencies = {relative: file_hash(old['application'], relative) for relative in sorted(production | tooling | set(hashes))}
    for relative, expected in dependencies.items():
        if file_hash(state['application'], relative) != expected:
            raise ReuseError('dependency-bytes-changed: ' + relative)
    binding = next((item for item in manifest['scenarios'] if item.get('id') == identifier), None)
    if not binding or binding.get('status') != 'passed' or binding.get('report_path') != report_path or binding.get('report_sha256') != file_hash(old['run'], report_path):
        raise ReuseError('original-report-binding-invalid')
    origin = {'run_id': old['run_id'], 'application_revision': old['application_input']['revision'],
              'release': old['release'], 'capture_id': old.get('capture_id')}
    origin_manifest = 'manifest.json'
    if binding.get('reuse'):
        # The baseline's own reuse plan is identical for every recipe in this build;
        # validate it once (including a failure) instead of once per retained recipe.
        if 'result' not in prior_plan:
            try:
                prior_plan['result'] = validate_reuse_plan(old)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                prior_plan['result'] = exc
        if isinstance(prior_plan['result'], Exception):
            raise prior_plan['result']
        prior = prior_plan['result']
        prior_entry = prior['recipes'].get(identifier)
        if not prior_entry:
            raise ReuseError('missing-original-reuse-provenance')
        origin = prior_entry['origin']
        origin_manifest = prior_entry['origin_manifest']['path']
    elif report.get('tooling_hashes') != old['tooling_hashes']:
        raise ReuseError('original-tooling-report-binding-invalid')
    check_report(old, previous, report, origin)
    if binding.get('application_revision') != report.get('application_revision') or binding.get('source_hashes') != hashes:
        raise ReuseError('original-scenario-binding-invalid')
    editorial = origin_sections(old, manifest, identifier, binding['report_sha256'], report)
    check_editorial(state, editorial)
    return {'origin': origin, 'origin_manifest': {'path': f'evidence-origin/{identifier}.manifest.json',
                'sha256': file_hash(old['run'], origin_manifest)},
            'report_path': report_path, 'report_sha256': binding['report_sha256'],
            'recipe_contract_sha256': digest(canonical({'recipe': current, 'shared_dependencies': new_registry['shared_dependencies']})),
            'dependency_hashes': dependencies, 'production_paths': sorted(production), 'tooling_paths': sorted(tooling),
            'sections': editorial}, origin_manifest


def build_reuse_plan(state, baseline_run, *, stage=True):
    """Create explicit per-recipe decisions; selected recipes always remain fresh."""
    old_run = Path(baseline_run).resolve()
    if old_run == Path(state['run']).resolve():
        raise ReuseError('baseline-must-be-a-different-owned-run')
    old = load(old_run, 'run.json')
    old['run'] = str(old_run)
    # A later renderer/evaluation failure does not invalidate independently sealed browser
    # captures. Reuse still verifies each passed report, source, editorial
    # binding and image; the new corpus must pass rendering and evaluation.
    if old.get('status') not in ('generated', 'evaluated', 'indexed', 'rendered', 'failed'):
        raise ReuseError('baseline-run-not-verified')
    manifest = load(old_run, 'manifest.json')
    if (manifest.get('metadata', {}).get('application_revision') != old['application_input']['revision']
            or manifest['metadata'].get('release') != old['release']):
        raise ReuseError('baseline-manifest-target-mismatch')
    old_registry, old_recipes = registry(old['application'])
    current_registry, recipes = registry(state['application'])
    fresh = list(state.get('selected_recipes', []))
    if len(set(fresh)) != len(fresh) or set(fresh) - set(recipes):
        raise ReuseError('invalid-fresh-recipe-selection')
    plan = {'format': 1, 'status': 'validated', 'target': target(state), 'fresh_recipes': fresh,
            'reused_recipes': [], 'rejected': [], 'recipes': {}}
    staged = []
    prior_plan = {}
    for identifier in recipes:
        if identifier in fresh:
            continue
        try:
            entry, origin_manifest = _entry(state, old, identifier, old_registry, current_registry, old_recipes, recipes, manifest, prior_plan)
            plan['recipes'][identifier] = entry
            plan['reused_recipes'].append(identifier)
            staged.append((identifier, origin_manifest))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            plan['rejected'].append({'id': identifier, 'reasons': [str(exc)]})
    if stage:
        for identifier, origin_manifest in staged:
            entry = plan['recipes'][identifier]
            report = load(old_run, entry['report_path'])
            files = [(entry['report_path'], entry['report_path']), (origin_manifest, entry['origin_manifest']['path'])]
            files += [(relative, relative) for frame in report['screenshots'] for relative in ('captures/' + frame['path'], frame['master']['path'])]
            files += [(frame['stability']['path'], frame['stability']['path']) for frame in report['screenshots'] if frame.get('stability')]
            for source, destination in files:
                contents = owned(old_run, source).read_bytes()
                output = owned(state['run'], destination)
                if output.exists() and output.read_bytes() != contents:
                    raise ReuseError('staged-evidence-path-collision: ' + destination)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(contents)
        path = owned(state['run'], 'evidence-reuse.json')
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        validate_reuse_plan(state, plan)
    return plan


def reuse_compatible_captures(state, baseline_run, *, stage=True):
    """Full coverage: retain compatible proof and recapture every rejected recipe."""
    probe = build_reuse_plan({**state, 'selected_recipes': []}, baseline_run, stage=False)
    state['selected_recipes'] = [entry['id'] for entry in probe['rejected']]
    state.setdefault('selection', {})['recapture_reasons'] = probe['rejected']
    return build_reuse_plan(state, baseline_run, stage=stage)


def validate_reuse_plan(state, plan=None):
    """Backend/generator gate, independent of the retained baseline location."""
    if plan is None:
        pointer = state.get('evidence_reuse', {})
        relative = pointer.get('path', 'evidence-reuse.json')
        if relative != 'evidence-reuse.json':
            raise ReuseError('invalid-reuse-plan-path')
        if pointer.get('sha256') and file_hash(state['run'], relative) != pointer['sha256']:
            raise ReuseError('reuse-plan-hash-changed')
        plan = load(state['run'], relative)
    if plan.get('format') != 1 or plan.get('status') != 'validated' or plan.get('target') != target(state):
        raise ReuseError('reuse-target-mismatch')
    reused, fresh = plan.get('reused_recipes', []), plan.get('fresh_recipes', [])
    if (set(fresh) != set(state.get('selected_recipes', [])) or len(set(fresh)) != len(fresh) or len(set(reused)) != len(reused)
            or set(reused) & set(fresh) or set(reused) != set(plan.get('recipes', {}))):
        raise ReuseError('reuse-selection-mismatch')
    value, recipes = registry(state['application'])
    if set(fresh) - set(recipes):
        raise ReuseError('invalid-fresh-recipe-selection')
    for identifier in reused:
        if identifier not in recipes:
            raise ReuseError('reused-recipe-removed')
        entry = plan['recipes'][identifier]
        recipe = recipes[identifier]
        contract = digest(canonical({'recipe': recipe, 'shared_dependencies': value['shared_dependencies']}))
        known, production, tooling = dependency_contract(state['application'], value, recipe)
        if (entry.get('recipe_contract_sha256') != contract or entry.get('production_paths') != sorted(production)
                or entry.get('tooling_paths') != sorted(tooling)):
            raise ReuseError('current-recipe-dependency-closure-changed')
        report = load(state['run'], entry['report_path'])
        if entry['report_path'] != identifier + '.json' or file_hash(state['run'], entry['report_path']) != entry['report_sha256']:
            raise ReuseError('reused-report-bytes-changed')
        expected_paths = production | tooling | set(report.get('source_hashes', {}))
        if set(entry['dependency_hashes']) != expected_paths:
            raise ReuseError('incomplete-reuse-dependency-binding')
        for relative, expected in entry['dependency_hashes'].items():
            if not HASH.fullmatch(expected) or file_hash(state['application'], relative) != expected:
                raise ReuseError('reuse-dependency-changed: ' + relative)
        retained = entry['origin_manifest']
        if retained['path'] != f'evidence-origin/{identifier}.manifest.json' or file_hash(state['run'], retained['path']) != retained['sha256']:
            raise ReuseError('origin-manifest-bytes-changed')
        original = load(state['run'], retained['path'])
        binding = next((item for item in original.get('scenarios', []) if item.get('id') == identifier), {})
        if (binding.get('status') != 'passed' or binding.get('report_sha256') != entry['report_sha256'] or binding.get('application_revision') != entry['origin']['application_revision']
                or binding.get('source_hashes') != report.get('source_hashes')):
            raise ReuseError('retained-origin-report-binding-invalid')
        if (original.get('metadata', {}).get('application_revision') != entry['origin']['application_revision']
                or original['metadata'].get('release') != entry['origin']['release']):
            raise ReuseError('retained-origin-manifest-target-invalid')
        retained_sections = {(page['path'], section['id']): section for page in original.get('pages', [])
                             for section in page.get('sections', [])
                             if section.get('status') == 'verified' and section.get('scenario_ids') == [identifier]}
        editorial = entry.get('sections', [])
        if (not editorial or len({(item['path'], item['id']) for item in editorial}) != len(editorial)
                or {(item['path'], item['id']) for item in editorial} != set(retained_sections)
                or any(item['published_content_sha256'] != retained_sections[(item['path'], item['id'])]['content_sha256'] for item in editorial)):
            raise ReuseError('retained-editorial-section-binding-invalid')
        check_report(state, recipe, report, entry['origin'])
        check_editorial(state, entry['sections'])
    return plan


def validate_reused_report(state, report, *, report_path=None, plan=None):
    validated = validate_reuse_plan(state, plan)
    entry = validated['recipes'].get(report.get('id'))
    if not entry or report != load(state['run'], entry['report_path']):
        raise ReuseError('report-is-not-validated-reused-evidence')
    if report_path is not None and Path(report_path).resolve() != owned(state['run'], entry['report_path']).resolve():
        raise ReuseError('reused-report-path-mismatch')
    return entry


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', metavar='RUN', required=True)
    args = parser.parse_args()
    try:
        run = Path(args.validate).resolve()
        state = load(run, 'run.json')
        state['run'] = str(run)
        print(json.dumps(validate_reuse_plan(state), ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, 'Evidence reuse rejected: ' + str(exc) + '\n')
