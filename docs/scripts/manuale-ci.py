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


def pin(args, policy):
    source = policy.pinned_source(override=args.manual_revision)
    destination = Path(args.manual_repo).resolve()
    if destination.exists():
        raise ValueError('Use a new manual authoring checkout')
    subprocess.run(['git', 'clone', '--no-checkout', 'https://github.com/' + source['repository'] + '.git',
                    str(destination)], check=True)
    subprocess.run(['git', '-C', str(destination), 'fetch', 'origin', source['revision']], check=True)
    subprocess.run(['git', '-C', str(destination), 'checkout', '--detach', source['revision']], check=True)
    actual = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != source['revision']:
        raise ValueError('Manual source pin changed during checkout')
    write(args.output, source)
    outputs(manual_revision=actual)


def prepare(args, runner, policy):
    sources = module('manuale-cache').validate_application_sources(ROOT)
    print(f'Portable source preflight passed: {len(sources)} overlay files; public npm flag restored from Git.', flush=True)
    parameters = ['run', '--manual-repo', args.manual_repo, '--output', args.output, '--release', args.release]
    if args.force_full:
        parameters += ['--no-cache']
    elif args.baseline and (Path(args.baseline) / 'run.json').is_file():
        parameters += ['--reuse-evidence', args.baseline]
    else:
        parameters += ['--no-cache']
    options = runner.argument_parser().parse_args(parameters)
    options.baseline_optional = True
    # A baseline is compared with actual current bytes. A PR base SHA must not
    # force recaptures of changes already verified by a compatible newer run.
    state = runner.prepare(options)
    plan = policy.verification_decision(state, force_full=args.force_full)
    if plan['mode'] == 'full' and state.get('evidence_reuse'):
        state['selected_recipes'] = plan['fresh_workflows']
        state.pop('evidence_reuse', None)
        state.pop('reuse_evidence', None)
        (Path(state['run']) / 'evidence-reuse.json').unlink(missing_ok=True)
    state['ci_plan'] = 'ci-plan.json'
    write(Path(state['run']) / 'run.json', state)
    # Do not publish machine paths from cache discovery in the decision report.
    for decision in plan.get('cache_decisions', []):
        decision.pop('run', None)
        if decision.get('reason'):
            decision['reason'] = re.sub(r'(?:/[^\s:]+)+', '[local path]', decision['reason'])
    write(Path(state['run']) / 'ci-plan.json', plan)
    outputs(mode=plan['mode'], historical_rehearsal=plan['historical_rehearsal'],
            fresh_workflows=len(plan['fresh_workflows']), manual_revision=state['manual_input']['revision'])
    summary(plan)


def phase(args, runner):
    run = Path(args.run).resolve()
    state = json.loads((run / 'run.json').read_text())
    plan = json.loads((run / 'ci-plan.json').read_text())
    try:
        with runner.timed(run, 'ci-' + args.phase):
            if args.phase == 'capture':
                if state['selected_recipes']:
                    runner.start(state)
                runner.capture(state)
            elif args.phase == 'compile':
                runner.generate(state)
                runner.compile_content(state)
            elif args.phase == 'render':
                # The renderer decides page reuse independently of capture
                # reuse, using exact generated content and renderer inputs.
                runner.verify(state)
            elif args.phase == 'runtime':
                runner.index(state)
                if not state.get('env_file'):
                    runner.start(state)
                runner.evaluate(state)
                runner.export(state)
            elif args.phase == 'package':
                runner.execute([sys.executable, str(ROOT / 'docs/scripts/manuale-release.py'), '--bundle',
                    '--run', str(run), '--application-repo', str(ROOT), '--manual-repo', state['manual_repository'],
                    '--release', state['release'], '--output', args.package_output], run,
                    'Package verified content for this exact application revision and release')
            elif args.phase == 'baseline':
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
        render = json.loads((run / 'render-reuse.json').read_text())
        timings = json.loads((run / 'timings.json').read_text()) if (run / 'timings.json').exists() else {}
        write(run / 'ci-results.json', {'status': 'passed', 'mode': plan['mode'],
              'application_revision': plan['application_revision'], 'manual_revision': plan['manual_revision'],
              'release': plan['release'], 'fresh_workflows': len(plan['fresh_workflows']),
              'reused_workflows': len(plan['reused_workflows']),
              'fresh_render_pages': len(render['fresh_pages']), 'reused_render_pages': len(render['reused_pages']),
              'avoided_browser_workflows': len(plan['reused_workflows']),
              'avoided_mintlify_pages': len(render['reused_pages']), 'timings': timings})
        print(f"Avoided {len(plan['reused_workflows'])} workflow recaptures and {len(render['reused_pages'])} Mintlify page renders.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('pin', 'baseline', 'prepare', 'phase'))
    parser.add_argument('--manual-repo')
    parser.add_argument('--manual-revision')
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', 'carbogninalberto/assozeta'))
    parser.add_argument('--baseline')
    parser.add_argument('--output')
    parser.add_argument('--release', default='manuale-development')
    parser.add_argument('--force-full', action='store_true')
    parser.add_argument('--run')
    parser.add_argument('--phase', choices=('capture', 'compile', 'render', 'runtime', 'package', 'baseline', 'cleanup'))
    parser.add_argument('--package-output')
    args = parser.parse_args()
    policy = module('manuale-policy')
    if args.command == 'pin':
        pin(args, policy)
    elif args.command == 'baseline':
        try:
            result = policy.fetch_baseline(args.repository, args.output, args.manual_repo, module('manuale-cache'))
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            result = {'status': 'unavailable', 'reason': 'Trusted baseline lookup failed (' + type(exc).__name__
                      + '); fresh verification is required.'}
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
