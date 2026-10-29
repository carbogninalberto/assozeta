#!/usr/bin/env python3
"""Visible CI phases over the existing manual verifier; never bypass its gates."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from manuale_timing import Stage  # noqa: E402


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'docs/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def outputs(**values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key, value in values.items():
                output.write(key + '=' + str(value).lower() + '\n')


def summary(plan):
    lines = ['### Manual verification decision', '', '**Mode:** ' + plan['mode'], '',
             *['- ' + reason for reason in plan['reasons']], '',
             f"Fresh workflows: {len(plan['fresh_workflows'])}; reused workflows: {len(plan['reused_workflows'])}.", '',
             'Historical appliance rehearsal: ' + ('required' if plan['historical_rehearsal'] else 'compatible inputs; runtime checks still required'), '',
             '| Workflow | Decision |', '| --- | --- |',
             *[f'| {identifier} | fresh verification |' for identifier in plan['fresh_workflows']],
             *[f'| {identifier} | compatible original evidence |' for identifier in plan['reused_workflows']], '']
    decisions = [entry for candidate in plan.get('cache_decisions', []) if candidate.get('selected')
                 for entry in candidate.get('input_decisions', [])]
    if decisions:
        lines += ['| Changed input | Workflows affected | Reason |', '| --- | --- | --- |']
        lines += [f"| {entry['path']} | {', '.join(entry['recipes']) or 'unaffected'} | {entry['reason']} |"
                  for entry in decisions]
        lines += ['']
    report = '\n'.join(lines)
    print(report, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(report)


# Fixed metadata makes the materialized commit a pure function of the vendored tree.
MANUAL_COMMIT_ENVIRONMENT = {
    'GIT_AUTHOR_NAME': 'Assozeta manual', 'GIT_AUTHOR_EMAIL': 'manual@assozeta.invalid', 'GIT_AUTHOR_DATE': '2000-01-01T00:00:00+0000',
    'GIT_COMMITTER_NAME': 'Assozeta manual', 'GIT_COMMITTER_EMAIL': 'manual@assozeta.invalid', 'GIT_COMMITTER_DATE': '2000-01-01T00:00:00+0000',
}


def pin(args, policy):
    source = policy.pinned_source(override=args.manual_revision)
    destination = Path(args.manual_repo).resolve()
    if destination.exists():
        raise ValueError('Use a new manual authoring checkout')
    destination.mkdir(parents=True)
    # Materialize the committed vendored folder (never uncommitted workspace
    # files) as the root of a clean, deterministic companion repository.
    archive = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', 'HEAD:' + source['path']],
                             check=True, stdout=subprocess.PIPE).stdout
    subprocess.run(['tar', '-x', '-C', str(destination)], input=archive, check=True)
    git = lambda *arguments: subprocess.run(['git', '-C', str(destination), '-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false',
                                             *arguments], check=True, env={**os.environ, **MANUAL_COMMIT_ENVIRONMENT},
                                            stdout=subprocess.PIPE, text=True).stdout.strip()
    git('init', '-q', '-b', 'main')
    git('add', '-A', '-f')
    git('commit', '-q', '-m', 'Vendored manual content')
    git('remote', 'add', 'origin', 'https://github.com/' + source['repository'] + '.git')
    actual = git('rev-parse', 'HEAD')
    if git('status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('Materialized manual checkout is not clean')
    write(args.output, {**source, 'revision': actual})
    outputs(manual_revision=actual)


# ---------------------------------------------------------------------------
# Change-impact planning (execution modes A/B/C)
# ---------------------------------------------------------------------------

IMPACT_MODES = ('unaffected', 'content', 'behavioral', 'full')
IMPACT_CATEGORIES = {'unrelated', 'docs-text', 'behavior', 'capture-tooling', 'shared-contract',
                     'bot', 'mcp', 'security', 'unknown'}
FAIL_CLOSED_CATEGORIES = {'unknown', 'security', 'shared-contract'}
BEHAVIOR_CATEGORIES = {'behavior', 'capture-tooling'}
IMPACT_CHECKS = ('bot', 'mcp', 'security', 'runtime')
IMPACT_MODULE = 'docs/scripts/manuale_impact.py'


def workflow_ids(root=ROOT):
    return [recipe['id'] for recipe in json.loads((Path(root) / 'docs/manuale/recipes.json').read_text())['recipes']]


def changed_files(base, head, root=ROOT):
    """Paths changed between two revisions; an unknown base is never 'no change'."""
    if not base or not re.fullmatch(r'[0-9A-Za-z._/~^@{}-]+', base) or re.fullmatch(r'0+', base):
        raise ValueError('no comparable base revision')
    result = subprocess.run(['git', '-C', str(root), 'diff', '--name-only', '--no-renames', '-z', base, head or 'HEAD', '--'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise ValueError('base revision is not available in this checkout')
    return sorted(set(filter(None, result.stdout.decode().split('\0'))))


def fallback_impact(changed, reason, identifiers):
    """Fail closed: full verification of every workflow and every runtime check."""
    return {'format': 1, 'mode': 'full', 'full_verification_required': True, 'fallback': True,
            'affected_workflows': list(identifiers), 'reused_workflows': [], 'affected_pages': [],
            'checks': {name: True for name in IMPACT_CHECKS},
            'files': [{'path': path, 'category': 'unknown', 'workflows': [], 'reason': 'not classified'}
                      for path in changed],
            'reasons': [reason]}


def validate_impact(report, changed, identifiers):
    """Accept only the format-1 contract, then escalate any inconsistency to full.

    The analyzer may only ever narrow work it can explain. Anything it cannot
    explain (unclassified paths, fail-closed categories, behavioral changes
    without workflows) is treated as full verification here as well.
    """
    if not isinstance(report, dict) or report.get('format') != 1:
        raise ValueError('unsupported impact report format')
    if report.get('mode') not in IMPACT_MODES:
        raise ValueError('unknown impact mode')
    for key in ('affected_workflows', 'reused_workflows', 'affected_pages', 'files', 'reasons'):
        if not isinstance(report.get(key), list):
            raise ValueError('impact report field is not a list: ' + key)
    checks = report.get('checks')
    if not isinstance(checks, dict) or any(not isinstance(checks.get(name), bool) for name in IMPACT_CHECKS):
        raise ValueError('impact report checks are incomplete')
    if not isinstance(report.get('full_verification_required'), bool):
        raise ValueError('impact report does not state full_verification_required')
    known = set(identifiers)
    for key in ('affected_workflows', 'reused_workflows'):
        unknown = set(report[key]) - known
        if unknown:
            raise ValueError('impact report names unknown workflows: ' + ', '.join(sorted(map(str, unknown))))
    for entry in report['files']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                or entry.get('category') not in IMPACT_CATEGORIES or not isinstance(entry.get('workflows', []), list)
                or set(entry.get('workflows', [])) - known):
            raise ValueError('impact report contains an invalid file decision')
    result = json.loads(json.dumps(report))
    result['checks'] = {name: bool(checks[name]) for name in IMPACT_CHECKS}
    escalations = []
    classified = {entry['path'] for entry in result['files']}
    missing = [path for path in changed if path not in classified]
    if missing:
        escalations.append(f'{len(missing)} changed file(s) were not classified by the impact analysis')
    fail_closed = sorted({entry['category'] for entry in result['files'] if entry['category'] in FAIL_CLOSED_CATEGORIES})
    if fail_closed and result['mode'] != 'full':
        escalations.append('fail-closed categories require full verification: ' + ', '.join(fail_closed))
    behavior = [entry for entry in result['files'] if entry['category'] in BEHAVIOR_CATEGORIES]
    if behavior and result['mode'] in ('unaffected', 'content'):
        escalations.append('behavioral changes were reported without a behavioral mode')
    if result['mode'] == 'behavioral':
        selected = set(result['affected_workflows']) | {workflow for entry in behavior for workflow in entry.get('workflows', [])}
        if not selected:
            escalations.append('behavioral change without an affected workflow')
        result['affected_workflows'] = [identifier for identifier in identifiers if identifier in selected]
        result['reused_workflows'] = [identifier for identifier in identifiers if identifier not in selected]
    if result['full_verification_required'] and result['mode'] != 'full':
        escalations.append('impact analysis requires full verification')
    if result['mode'] == 'full' or escalations:
        result.update(mode='full', full_verification_required=True, affected_workflows=list(identifiers), reused_workflows=[])
        result['checks'].update(security=result['checks']['security'] or bool(fail_closed) or bool(escalations),
                                runtime=True)
        result['reasons'] = [str(reason) for reason in result['reasons']] + escalations
    return result


def load_impact_module(root=ROOT):
    path = Path(root) / IMPACT_MODULE
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('manuale_impact', path)
    value = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('manuale_impact', value)
    spec.loader.exec_module(value)
    return value


def ci_decision(report, *, forced, changed):
    """Map the (validated) impact report onto the jobs of manuale.yml."""
    mode, fallback = report['mode'], bool(report.get('fallback'))
    full = mode == 'full' or forced
    checks = report['checks']
    verify = full or mode == 'behavioral'
    return {
        'mode': 'full' if forced else mode,
        'verify': verify,
        # A fallback keeps today's trusted-baseline planning (fresh where bytes
        # changed); explicit, scheduled or analyzed full runs disable reuse.
        'force_full': forced or (mode == 'full' and not fallback),
        'content': mode == 'content' and not forced,
        'workflows': [] if full else list(report['affected_workflows']) if mode == 'behavioral' else [],
        'bot': full or checks['bot'] or mode == 'content',
        'mcp': full or checks['mcp'] or mode == 'content',
        'browser_helpers': full or fallback or any(path.startswith('selfhost/tests/browser/') for path in changed),
        'appliance': full or checks['runtime'] or checks['security'],
    }


def plan(args):
    identifiers = workflow_ids()
    forced = bool(args.force_full)
    changed = sorted(set(args.changed_file or []))
    with Stage('plan', {'event': os.environ.get('GITHUB_EVENT_NAME', 'local')}) as stage:
        try:
            if not changed:
                changed = changed_files(args.base, args.head)
            analyzer = load_impact_module()
            if analyzer is None:
                raise LookupError('impact analyzer is not installed')
            raw = analyzer.analyze(ROOT, changed, base=args.base or None, head=args.head or None)
            report = validate_impact(raw, changed, identifiers)
        except Exception as exc:  # noqa: BLE001 - any analyzer failure fails closed
            reason = ('Impact analysis unavailable (' + type(exc).__name__ + ': '
                      + re.sub(r'(?:/[^\s:]+)+', '[local path]', str(exc))[:200]
                      + '); falling back to trusted-baseline planning with full verification.')
            report = fallback_impact(changed, reason, identifiers)
        if forced:
            report['reasons'] = ['explicit or scheduled full verification'] + list(report['reasons'])
        decision = ci_decision(report, forced=forced, changed=changed)
        report['ci'] = decision
        stage.detail.update(mode=decision['mode'], changed_files=len(changed), fallback=bool(report.get('fallback')))
    write(args.output, report)
    outputs(mode=decision['mode'], verify=decision['verify'], force_full=decision['force_full'],
            content=decision['content'], workflows=','.join(decision['workflows']), bot=decision['bot'],
            mcp=decision['mcp'], browser_helpers=decision['browser_helpers'], appliance=decision['appliance'],
            fallback=bool(report.get('fallback')))
    print(json.dumps({'mode': decision['mode'], 'verify': decision['verify'], 'force_full': decision['force_full'],
                      'fallback': bool(report.get('fallback')), 'changed_files': len(changed),
                      'affected_workflows': len(report['affected_workflows'])}), flush=True)
    return report


def load_impact(path):
    if not path:
        return None
    path = Path(path)
    if not path.is_file():
        return None
    report = json.loads(path.read_text())
    return report if isinstance(report, dict) and report.get('format') == 1 else None


# ---------------------------------------------------------------------------
# Offline (Mode A) checks: no Docker, Postgres, Redis, MinIO or full app
# ---------------------------------------------------------------------------

# Django SimpleTestCase suites for the manual corpus, Bot and MCP tools. They
# use no database or service; tenant/auth runtime checks stay in modes B/C.
OFFLINE_BACKEND_TESTS = (
    'application/tests/test_agent_routing.py', 'application/tests/test_ai_provider.py',
    'application/tests/test_manuale.py', 'application/tests/test_manuale_sync.py',
    'application/tests/test_manuale_preview.py', 'application/tests/test_manuale_development.py',
)
# Non-secret placeholders that only satisfy settings import for those tests.
OFFLINE_BACKEND_ENVIRONMENT = {
    'SECRET_KEY': 'manual-ci-offline-placeholder', 'APP_HOST': 'localhost', 'DEBUG': 'false',
    'DOCUMENT_BYPASS_TOKEN': 'placeholder', 'SENJA_WEBHOOK_SECRET': 'placeholder', 'ADMIN_URL': 'admin/',
    'AWS_ACCESS_KEY_ID': 'placeholder', 'AWS_SECRET_ACCESS_KEY': 'placeholder', 'AWS_LOCATION': 'placeholder',
    'AWS_STORAGE_BUCKET_NAME': 'placeholder', 'AWS_S3_ENDPOINT_URL': 'http://127.0.0.1:9',
    'AWS_S3_REGION_NAME': 'placeholder', 'CRM_API_TOKEN': 'placeholder', 'CURRENT_HOST': 'localhost',
    'CURRENT_HOST_PUPPETEER': 'localhost', 'FACEBOOK_APP_ID': 'placeholder', 'FACEBOOK_SECRET_KEY': 'placeholder',
    'PUPPETEER_HOST': 'localhost', 'PUPPETEER_PORT': '9', 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': 'placeholder',
    'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': 'placeholder', 'JWT_SECRET_KEY': 'placeholder', 'JWT_PUBLIC_KEY': 'placeholder',
}


def node_tests(root=ROOT, *, with_browser):
    """Browser-helper tests that launch Chromium run only when Chromium is installed."""
    files = sorted((Path(root) / 'selfhost/tests/browser/manuale').glob('*.test.mjs'))
    browser = [path for path in files if re.search(r'\bchromium\b', path.read_text())]
    selected = files if with_browser else [path for path in files if path not in browser]
    return selected, [] if with_browser else browser


def offline(args):
    if args.suite == 'python':
        command, cwd, environment = [sys.executable, '-m', 'unittest', 'discover', '-s', 'docs/scripts/tests',
                                     '-p', 'test_manuale*.py'], ROOT, None
    elif args.suite == 'node':
        selected, deferred = node_tests(with_browser=args.with_browser)
        if deferred:
            print('Chromium helper tests run in browser verification: '
                  + ', '.join(path.name for path in deferred), flush=True)
        command, cwd, environment = ['node', '--test', *map(str, selected)], ROOT, None
    else:
        command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *OFFLINE_BACKEND_TESTS]
        cwd, environment = ROOT / 'BE', {**OFFLINE_BACKEND_ENVIRONMENT, **os.environ}
    with Stage('offline:' + args.suite, {'browser': bool(args.with_browser)}):
        result = subprocess.run(command, cwd=cwd, env=environment)
        if result.returncode:
            raise SystemExit(result.returncode)


# ---------------------------------------------------------------------------
# Manifest-only evidence lookup and cache reporting
# ---------------------------------------------------------------------------

def store_lookup(store, manual_revision, identifiers, *, repository, root=ROOT):
    """Manifest-only per-workflow lookup through the evidence store, when installed.

    Hits require an intact manifest from a producer the GitHub API authenticates
    as trusted; screenshots are not read or downloaded. Advisory only.
    """
    path = Path(root) / 'docs/scripts/manuale_evidence_store.py'
    if not store or not Path(store).is_dir() or not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('manuale_evidence_store', path)
    evidence = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('manuale_evidence_store', evidence)
    spec.loader.exec_module(evidence)
    schema = getattr(evidence, 'SCHEMA_VERSION', None)
    if schema is None or not callable(getattr(evidence, 'lookup', None)):
        return None
    hits, misses, pending, producers = [], [], [], set()
    for identifier in identifiers:
        revision = (evidence.workflow_manual_revision(Path(root), identifier)
                    if callable(getattr(evidence, 'workflow_manual_revision', None)) else manual_revision)
        fingerprint = evidence.workflow_fingerprint(Path(root), identifier, manual_revision=revision,
                                                    schema_version=schema)
        manifest = Path(store) / 'workflows' / identifier / fingerprint / 'manifest.json'
        if not manifest.is_file():
            misses.append({'name': identifier, 'detail': 'no evidence for ' + fingerprint[:12]})
            continue
        producers.add(str(json.loads(manifest.read_text()).get('producer', {}).get('run_id', '')))
        pending.append((identifier, fingerprint))
    trusted = evidence.github_trusted_producers(repository, producers)[0] if pending else {}
    for identifier, fingerprint in pending:
        try:
            entry = evidence.lookup(Path(store), identifier, fingerprint, trusted_producers=trusted,
                                    manifest_only=True, repository=repository)
        except Exception as exc:  # noqa: BLE001 - every rejection is a miss
            misses.append({'name': identifier, 'detail': 'rejected (' + type(exc).__name__ + ')'})
            continue
        (hits if entry is not None else misses).append({'name': identifier, 'detail': fingerprint[:12]})
    return {'source': 'evidence-store', 'hits': hits, 'misses': misses}


def restore_store(args, cache=None):
    """Restore trusted evidence-store manifests, plan per-workflow reuse, materialize hits.

    Order: index artifact (small) -> authenticate producers -> plan with the
    impact report -> blobs artifact only when something is reusable ->
    materialize with full re-verification. Every failure is a cold miss.
    """
    cache = cache or module('manuale-cache')
    output = Path(args.output).resolve()
    store, selection = output / 'store', output / 'selection'
    report = {'format': 1, 'status': 'cold', 'reused_workflows': [], 'capture_workflows': []}
    repository = args.repository
    impact = load_impact(getattr(args, 'impact', None))
    try:
        with Stage('evidence-store-index') as stage:
            index = cache.fetch_store(repository, store, kind='index')
            report['index'] = {key: index.get(key) for key in ('status', 'producer_run', 'artifact_digest', 'size_in_bytes')}
            stage.detail.update(status=index['status'], bytes=index.get('size_in_bytes'))
        if index['status'] != 'available':
            raise LookupError('no trusted evidence store')
        with Stage('evidence-store-plan') as stage:
            trusted = cache.store_producers(store, repository)
            write(output / 'trusted-producers.json', trusted)
            plan = cache.plan_store(store, ROOT, trusted_producers=trusted['trusted'], impact=impact,
                                    reference_date=args.reference_date, repository=repository)
            write(output / 'store-plan.json', plan)
            stage.detail.update(plan['counts'])
        report.update(capture_workflows=plan['capture_workflows'], plan_counts=plan['counts'])
        if not plan['reused_workflows']:
            raise LookupError('no reusable workflow evidence at the current fingerprints')
        with Stage('evidence-store-blobs') as stage:
            blobs = cache.fetch_store(repository, store, kind='blobs', producer_run=index['producer_run'])
            report['blobs'] = {key: blobs.get(key) for key in ('status', 'producer_run', 'artifact_digest', 'size_in_bytes')}
            stage.detail.update(status=blobs['status'], bytes=blobs.get('size_in_bytes'))
        if blobs['status'] != 'available':
            raise LookupError('screenshot blobs of the trusted store are unavailable')
        with Stage('evidence-store-materialize') as stage:
            result = cache.materialize_store(store, plan, ROOT, selection, trusted_producers=trusted['trusted'],
                                             reference_date=args.reference_date, repository=repository)
            stage.detail.update(materialized=len(result['materialized']), rejected=len(result['rejected']))
        report.update(status='materialized', selection=str(selection),
                      reused_workflows=sorted(item['workflow_id'] for item in result['materialized']),
                      capture_workflows=result['capture_workflows'],
                      rejected=[{'workflow_id': item['workflow_id'], 'reason': sanitize(item['reason'])[:200]}
                                for item in result['rejected']])
    except (LookupError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        report['reason'] = sanitize(str(exc))[:300]
        if report['status'] != 'materialized':
            # Never leave a partial selection behind for the verifier.
            (selection / 'evidence-store-selection.json').unlink(missing_ok=True)
    write(output / 'store-restore.json', report)
    outputs(store_reused=len(report['reused_workflows']), store_selection=report['status'] == 'materialized')
    print(json.dumps({key: report[key] for key in ('status', 'reused_workflows') if key in report}), flush=True)
    return report


def baseline_metadata(repository, policy, *, root=ROOT, api=None):
    """Find an admissible trusted baseline from metadata only; nothing is downloaded."""
    api = api or policy.gh_json
    info = api(f'repos/{repository}')
    default_ref = 'refs/remotes/origin/' + info['default_branch']
    subprocess.run(['git', '-C', str(root), 'fetch', '--no-tags', 'origin',
                    info['default_branch'] + ':' + default_ref], check=True, stdout=subprocess.DEVNULL)
    rejected = 0
    for artifact in api(f'repos/{repository}/actions/artifacts?name=manuale-verified-baseline&per_page=30')['artifacts']:
        identifier = artifact.get('workflow_run', {}).get('id')
        if not identifier:
            rejected += 1
            continue
        run = api(f'repos/{repository}/actions/runs/{identifier}')
        reason = policy.producer_rejection(run, artifact, repository, accepted_source=policy.accepted_source(
            run, repository, default_ref, root=root, api=api), default_branch=info['default_branch'])
        if reason:
            rejected += 1
            continue
        return {'status': 'available', 'producer_run': identifier, 'artifact_id': artifact.get('id'),
                'size_in_bytes': artifact.get('size_in_bytes'), 'rejected': rejected}
    return {'status': 'missing', 'rejected': rejected}


def lookup(args, policy, *, api=None):
    report = {'format': 1, 'mode': 'manifest-only', 'hits': [], 'misses': [], 'artifacts': {}}
    with Stage('evidence-lookup') as stage:
        result = None
        try:
            result = store_lookup(os.environ.get('MANUALE_EVIDENCE_STORE'), args.manual_revision, workflow_ids(),
                                  repository=args.repository)
        except Exception as exc:  # noqa: BLE001 - lookup is advisory; never trusted on error
            report['misses'].append({'name': 'evidence store', 'detail': 'lookup failed (' + type(exc).__name__ + ')'})
        if result:
            report.update(source=result['source'], hits=result['hits'], misses=report['misses'] + result['misses'])
        else:
            try:
                metadata = baseline_metadata(args.repository, policy, api=api)
            except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
                metadata = {'status': 'unavailable', 'reason': type(exc).__name__}
            report.update(source='github-artifact-metadata', baseline=metadata)
            if metadata['status'] == 'available':
                report['hits'].append({'name': 'trusted baseline', 'detail': f"producer run {metadata['producer_run']} "
                                       '(metadata only; bytes are verified when downloaded)'})
                if isinstance(metadata.get('size_in_bytes'), int):
                    report['artifacts']['trusted baseline (not downloaded)'] = metadata['size_in_bytes']
            else:
                report['misses'].append({'name': 'trusted baseline', 'detail': metadata['status']
                                         + '; the next behavioral or full run captures fresh evidence'})
        stage.detail.update(hits=len(report['hits']), misses=len(report['misses']))
    write(args.output, report)
    return report


def sanitize(text):
    return re.sub(r'(?:/[^\s:]+)+', '[local path]', str(text))


def cache_report(run=None, admission=None, previous=None):
    """Hit/miss and fresh/reused facts recorded by the run; nothing estimated."""
    report = {'format': 1, 'hits': [], 'misses': [], 'artifacts': {}}
    if previous and Path(previous).is_file():
        report.update({key: value for key, value in json.loads(Path(previous).read_text()).items()
                       if key in ('hits', 'misses', 'artifacts')})
    admission = json.loads(Path(admission).read_text()) if admission and Path(admission).is_file() else None
    if admission:
        if admission.get('status') == 'available':
            report['hits'].append({'name': 'trusted baseline', 'detail': f"producer run {admission.get('producer_run')}"})
        else:
            reason = next((item.get('reason') for item in admission.get('candidates', []) if item.get('reason')),
                          admission.get('reason', admission.get('status')))
            report['misses'].append({'name': 'trusted baseline', 'detail': sanitize(reason)})
    run = Path(run) if run else None
    if run and (run / 'ci-plan.json').is_file():
        decision = json.loads((run / 'ci-plan.json').read_text())
        report.update(mode=decision.get('mode'), fresh_workflows=decision.get('fresh_workflows', []),
                      reused_workflows=decision.get('reused_workflows', []))
        if decision.get('reused_workflows'):
            report['hits'].append({'name': 'workflow evidence', 'detail': f"{len(decision['reused_workflows'])} reused"})
        if decision.get('fresh_workflows'):
            report['misses'].append({'name': 'workflow evidence', 'detail': f"{len(decision['fresh_workflows'])} captured fresh"})
    if run and (run / 'render-reuse.json').is_file():
        render = json.loads((run / 'render-reuse.json').read_text())
        report.update(fresh_pages=render.get('fresh_pages', []), reused_pages=render.get('reused_pages', []))
    if run and (run / 'run.json').is_file():
        state = json.loads((run / 'run.json').read_text())
        for decision in state.get('cache', {}).get('decisions', []):
            if decision.get('kind') == 'evidence-store':
                if decision.get('retained'):
                    report['hits'].append({'name': 'evidence store', 'detail': f"{decision['retained']} workflow(s) reused"})
                for item in decision.get('rejected', []):
                    report['misses'].append({'name': 'evidence store ' + str(item.get('workflow_id')),
                                             'detail': sanitize(item.get('reason', 'rejected'))[:200]})
                if decision.get('result') == 'unavailable':
                    report['misses'].append({'name': 'evidence store', 'detail': sanitize(decision.get('reason', ''))[:200]})
            if decision.get('kind') == 'frontend':
                target = report['hits'] if decision.get('result') == 'reused' else report['misses']
                target.append({'name': 'frontend build', 'detail': sanitize(decision.get('reason', decision.get('result')))})
    return report


# ---------------------------------------------------------------------------
# Release gate (Mode C): only complete comprehensive verification authorizes
# ---------------------------------------------------------------------------

# Appliance checks every release must have recorded (manual-release-smoke.py).
REQUIRED_RELEASE_CHECKS = (
    'fresh installation: reader, assets, search and real browser assistance',
    'container recreation retains the image-matched manual',
    'missing/corrupt index and PNGs and mismatched version reject candidate startup',
    'ordinary CLI rejects each missing/corrupt manual candidate',
    'upgrade preserves association data',
    'historical backup rollback restores the original application/data pair',
)


def gate_failures(needs, *, comprehensive, release, revision, results=None):
    """Reasons the run cannot authorize its verified manual (empty: authorized)."""
    needs = needs if isinstance(needs, dict) else {}
    failures = []

    def result(job):
        value = needs.get(job)
        return value.get('result', 'missing') if isinstance(value, dict) else 'missing'

    outputs_ = (needs.get('verify') or {}).get('outputs') or {}
    for job in sorted(needs):
        if result(job) in ('failure', 'cancelled'):
            failures.append(f'{job} {result(job)}')
    if result('verify') != 'success':
        failures.append('manual verification did not succeed (' + result('verify') + ')')
    appliance_required = comprehensive or outputs_.get('appliance_required') == 'true'
    scope = outputs_.get('appliance_scope')
    if comprehensive:
        if result('contracts') != 'success':
            failures.append('mandatory verifier contracts did not succeed (' + result('contracts') + ')')
        if outputs_.get('comprehensive') != 'true':
            failures.append('verification did not run in comprehensive release mode')
        if scope != 'full':
            failures.append('release requires the full historical appliance rehearsal, not ' + str(scope))
        # release-binding.json must have been generated and verified for exactly
        # this commit and version (store-verify-binding in the verify job).
        if outputs_.get('release_binding') != 'verified':
            failures.append('release binding of the workflow evidence is missing or unverified')
        else:
            if outputs_.get('release_binding_release') != release:
                failures.append('release binding belongs to another release')
            if outputs_.get('release_binding_revision') != revision:
                failures.append('release binding belongs to another application revision')
    elif result('contracts') not in ('success', 'skipped'):
        failures.append('verifier contracts did not succeed (' + result('contracts') + ')')
    if appliance_required:
        if result('appliance') != 'success':
            failures.append('mandatory appliance verification did not succeed (' + result('appliance') + ')')
        if not isinstance(results, dict):
            failures.append('appliance results are missing')
        else:
            if results.get('status') != 'passed':
                failures.append('appliance results did not pass')
            if results.get('release') != release:
                failures.append('appliance results belong to another release')
            if results.get('application_revision') != revision:
                failures.append('appliance results belong to another application revision')
            expected = 'full' if comprehensive else scope
            if results.get('scope') != expected:
                failures.append(f"appliance scope {results.get('scope')} does not match required {expected}")
            if expected == 'full':
                recorded = [str(check) for check in results.get('checks', [])]
                for check in REQUIRED_RELEASE_CHECKS:
                    if not any(check in item for item in recorded):
                        failures.append('mandatory appliance check missing: ' + check)
    elif result('appliance') not in ('success', 'skipped'):
        failures.append('appliance verification did not succeed (' + result('appliance') + ')')
    return list(dict.fromkeys(failures))


def gate(args):
    needs = json.loads(args.needs_json or os.environ.get('MANUALE_NEEDS_JSON') or '{}')
    results = None
    if args.results and Path(args.results).is_file():
        try:
            results = json.loads(Path(args.results).read_text())
        except ValueError:
            results = None
    comprehensive = str(args.comprehensive).lower() == 'true'
    with Stage('gate', {'comprehensive': comprehensive}) as stage:
        failures = gate_failures(needs, comprehensive=comprehensive, release=args.release,
                                 revision=args.revision, results=results)
        stage.detail['authorized'] = not failures
    report = {'format': 1, 'authorized': not failures, 'comprehensive': comprehensive, 'release': args.release,
              'application_revision': args.revision, 'failures': failures,
              'jobs': {job: (value or {}).get('result') for job, value in needs.items()}}
    if args.output:
        write(args.output, report)
    outputs(authorized=not failures)
    lines = ['### Manual verification gate', '',
             ('Authorized: comprehensive release verification passed.' if comprehensive else
              'Passed: verification required for this change succeeded (does not authorize publication).')
             if not failures else 'Blocked:', '', *['- ' + failure for failure in failures], '']
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)
    if failures:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Existing verifier phases
# ---------------------------------------------------------------------------

def apply_build_cache(override, services=('api', 'ui', 'renderer'), *, prefix='manuale'):
    """Opt-in BuildKit layer cache for run-owned images; cache errors never fail a build."""
    path = Path(override)
    value = json.loads(path.read_text())
    for service in services:
        build = value.setdefault('services', {}).setdefault(service, {}).setdefault('build', {})
        build['cache_from'] = [f'type=gha,scope={prefix}-{service}']
        build['cache_to'] = [f'type=gha,mode=max,scope={prefix}-{service},ignore-error=true']
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def start_services(state, runner):
    if os.environ.get('MANUALE_BUILDX_CACHE') == 'gha' and not state.get('env_file'):
        runner.configure_runtime(state)
        apply_build_cache(state['override'])
    runner.start(state)


def prepare(args, runner, policy):
    sources = module('manuale-cache').validate_application_sources(ROOT)
    print(f'Portable source preflight passed: {len(sources)} overlay files; public npm flag restored from Git.', flush=True)
    impact = load_impact(getattr(args, 'impact', None))
    workflows = {item for value in (getattr(args, 'workflow', None) or []) for item in value.split(',') if item}
    if impact and impact.get('mode') == 'behavioral':
        workflows |= set(impact.get('affected_workflows', []))
    force_full = bool(args.force_full or (impact and impact.get('full_verification_required') and not impact.get('fallback')))
    parameters = ['run', '--manual-repo', args.manual_repo, '--output', args.output, '--release', args.release]
    selection = getattr(args, 'evidence_selection', None)
    if (not force_full and selection
            and (Path(selection) / 'evidence-store-selection.json').is_file()):
        # Verified per-workflow store evidence; admitted (or rejected) again by
        # the verifier's own gate, never trusted from this directory alone.
        parameters += ['--reuse-store', selection]
    if force_full:
        parameters += ['--no-cache']
    elif args.baseline and (Path(args.baseline) / 'run.json').is_file():
        parameters += ['--reuse-evidence', args.baseline]
    else:
        parameters += ['--no-cache']
    # Impact-selected workflows are captured in addition to every workflow the
    # byte comparison with the trusted baseline selects (never instead of it).
    for identifier in sorted(workflows):
        parameters += ['--workflow', identifier]
    options = runner.argument_parser().parse_args(parameters)
    options.baseline_optional = True
    # A baseline is compared with actual current bytes. A PR base SHA must not
    # force recaptures of changes already verified by a compatible newer run.
    with Stage('prepare', {'impact_workflows': len(workflows), 'force_full': force_full}) as stage:
        state = runner.prepare(options)
        plan = policy.verification_decision(state, force_full=force_full)
        stage.detail.update(mode=plan['mode'], fresh=len(plan['fresh_workflows']), reused=len(plan['reused_workflows']))
    if plan['mode'] == 'full' and state.get('evidence_reuse'):
        state['selected_recipes'] = plan['fresh_workflows']
        state.pop('evidence_reuse', None)
        state.pop('reuse_evidence', None)
        state.pop('evidence_store', None)
        (Path(state['run']) / 'evidence-reuse.json').unlink(missing_ok=True)
    if impact:
        plan['impact'] = {'mode': impact.get('mode'), 'fallback': bool(impact.get('fallback')),
                          'selected_workflows': sorted(workflows), 'reasons': impact.get('reasons', [])[:20]}
    # Content mode must not start services: any workflow the baseline cannot
    # cover escalates to the behavioral verifier instead of capturing here.
    escalate = bool(getattr(args, 'content_only', False) and plan['mode'] != 'reuse')
    plan['escalate'] = escalate
    state['ci_plan'] = 'ci-plan.json'
    write(Path(state['run']) / 'run.json', state)
    # Do not publish machine paths from cache discovery in the decision report.
    for decision in plan.get('cache_decisions', []):
        decision.pop('run', None)
        if decision.get('reason'):
            decision['reason'] = sanitize(decision['reason'])
    write(Path(state['run']) / 'ci-plan.json', plan)
    outputs(mode=plan['mode'], historical_rehearsal=plan['historical_rehearsal'],
            fresh_workflows=len(plan['fresh_workflows']), manual_revision=state['manual_input']['revision'],
            escalate=escalate)
    summary(plan)
    if escalate:
        print('Content-only verification escalates: ' + '; '.join(plan['reasons']), flush=True)


def phase(args, runner):
    run = Path(args.run).resolve()
    state = json.loads((run / 'run.json').read_text())
    plan = json.loads((run / 'ci-plan.json').read_text())
    sealed = None
    try:
        with runner.timed(run, 'ci-' + args.phase), Stage('phase:' + args.phase, {'mode': plan['mode']}) as stage:
            if args.phase == 'capture':
                stage.detail['fresh_workflows'] = len(state['selected_recipes'])
                if state['selected_recipes']:
                    start_services(state, runner)
                runner.capture(state)
            elif args.phase == 'compile':
                runner.generate(state)
                runner.compile_content(state)
            elif args.phase == 'render':
                # The renderer decides page reuse independently of capture
                # reuse, using exact generated content and renderer inputs.
                runner.verify(state)
            elif args.phase == 'index':
                # Offline corpus validation (content mode): no services.
                runner.index(state)
            elif args.phase == 'runtime':
                runner.index(state)
                if not state.get('env_file'):
                    start_services(state, runner)
                runner.evaluate(state)
                runner.export(state)
            elif args.phase == 'package':
                runner.execute([sys.executable, str(ROOT / 'docs/scripts/manuale-release.py'), '--bundle',
                    '--run', str(run), '--application-repo', str(ROOT), '--manual-repo', state['manual_repository'],
                    '--release', state['release'], '--output', args.package_output], run,
                    'Package verified content for this exact application revision and release')
            elif args.phase == 'baseline':
                render = json.loads((run / 'render-reuse.json').read_text())
                # Unchanged evidence is already retained by an admitted baseline;
                # sealing and uploading an identical copy only costs time.
                sealed = bool(args.force_seal or plan['mode'] == 'full' or plan['fresh_workflows'] or render['fresh_pages'])
                stage.detail['sealed'] = sealed
                if sealed:
                    module('manuale-cache').export_cache(run, Path(args.output))
            elif args.phase == 'cleanup':
                if state.get('env_file') and not state.get('services_removed'):
                    runner.cleanup(state)
    except Exception:
        if args.phase != 'cleanup':
            state['status'] = 'failed'
            write(run / 'run.json', state)
        raise
    if args.phase == 'baseline':
        timings = json.loads((run / 'timings.json').read_text()) if (run / 'timings.json').exists() else {}
        write(run / 'ci-results.json', {'status': 'passed', 'mode': plan['mode'],
              'application_revision': plan['application_revision'], 'manual_revision': plan['manual_revision'],
              'release': plan['release'], 'fresh_workflows': len(plan['fresh_workflows']),
              'reused_workflows': len(plan['reused_workflows']),
              'fresh_render_pages': len(render['fresh_pages']), 'reused_render_pages': len(render['reused_pages']),
              'avoided_browser_workflows': len(plan['reused_workflows']),
              'avoided_render_pages': len(render['reused_pages']),
              # Deprecated alias (Mintlify is no longer used); kept for existing readers.
              'avoided_mintlify_pages': len(render['reused_pages']),
              'baseline_sealed': sealed, 'timings': timings})
        outputs(sealed=sealed)
        print(f"Avoided {len(plan['reused_workflows'])} workflow recaptures and {len(render['reused_pages'])} "
              'reader page render checks.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('pin', 'baseline', 'prepare', 'phase', 'plan', 'offline', 'lookup', 'report',
                                            'gate', 'store'))
    parser.add_argument('--manual-repo')
    parser.add_argument('--manual-revision')
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', 'carbogninalberto/assozeta'))
    parser.add_argument('--baseline')
    parser.add_argument('--output')
    parser.add_argument('--release', default='manuale-development')
    parser.add_argument('--force-full', action='store_true')
    parser.add_argument('--run')
    parser.add_argument('--phase', choices=('capture', 'compile', 'render', 'index', 'runtime', 'package', 'baseline', 'cleanup'))
    parser.add_argument('--package-output')
    parser.add_argument('--force-seal', action='store_true', help='Seal a reusable baseline even without fresh evidence')
    parser.add_argument('--base', help='plan: base revision of the change')
    parser.add_argument('--head', help='plan: head revision of the change (default HEAD)')
    parser.add_argument('--changed-file', action='append', help='plan: explicit changed path (repeatable)')
    parser.add_argument('--impact', help='prepare: validated impact plan written by the plan command')
    parser.add_argument('--workflow', action='append', help='prepare: additionally capture these workflow ids')
    parser.add_argument('--content-only', action='store_true', help='prepare: never select captures; report escalation')
    parser.add_argument('--suite', choices=('python', 'node', 'bot-mcp'), help='offline: which suite to run')
    parser.add_argument('--with-browser', action='store_true', help='offline: include Chromium helper tests')
    parser.add_argument('--admission', help='report: baseline admission report')
    parser.add_argument('--merge', help='report: earlier cache report to extend')
    parser.add_argument('--needs-json', help='gate: toJSON(needs) of the gating job')
    parser.add_argument('--comprehensive', default='true', help='gate: true for release (Mode C) verification')
    parser.add_argument('--revision', help='gate: exact application revision')
    parser.add_argument('--results', help='gate: appliance results.json')
    parser.add_argument('--evidence-selection', help='prepare: store-materialized selection directory (from store)')
    parser.add_argument('--reference-date', default='2026-09-30', help='store: capture reference date')
    args = parser.parse_args()
    if args.command == 'plan':
        if not args.output:
            parser.error('--output is required')
        plan(args)
        return
    if args.command == 'offline':
        if not args.suite:
            parser.error('--suite is required')
        offline(args)
        return
    if args.command == 'report':
        write(args.output, cache_report(args.run, args.admission, args.merge))
        return
    if args.command == 'gate':
        if not args.revision:
            parser.error('--revision is required')
        gate(args)
        return
    if args.command == 'store':
        if not args.output:
            parser.error('--output is required')
        restore_store(args)
        return
    policy = module('manuale-policy')
    if args.command == 'lookup':
        lookup(args, policy)
    elif args.command == 'pin':
        with Stage('pin'):
            pin(args, policy)
    elif args.command == 'baseline':
        with Stage('baseline-admission') as stage:
            try:
                result = policy.fetch_baseline(args.repository, args.output, args.manual_repo, module('manuale-cache'))
            except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
                result = {'status': 'unavailable', 'reason': 'Trusted baseline lookup failed (' + type(exc).__name__
                          + '); fresh verification is required.'}
            stage.detail['result'] = result['status']
        result.pop('run', None)
        write(Path(args.output).parent / 'baseline-admission.json', result)
        print(json.dumps(result), flush=True)
    elif args.command == 'prepare':
        prepare(args, module('manuale'), policy)
    elif args.command == 'phase':
        if not args.run or not args.phase:
            parser.error('--run and --phase are required')
        phase(args, module('manuale'))


if __name__ == '__main__':
    main()
