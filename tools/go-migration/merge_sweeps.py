#!/usr/bin/env python3
"""Merge the shard summaries of one sweep into a single summary.json.

  merge_sweeps.py --label L [--markdown FILE]            reads quality-reports/go-migration/sweep-L/summary-shard-*.json
  merge_sweeps.py --out SUMMARY.json SHARD.json [...]    explicit inputs

Writes the merged summary (same format as sweep.py, accepted by promote_verified.py --sweep) and exits 1,
listing each problem, when a shard is absent, the shards disagree on commit or label, a job has no result,
or a job failed. --markdown appends a result table (for $GITHUB_STEP_SUMMARY).
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def merge(summaries):
    """Return (merged summary, problems). Problems are empty only for a complete, fully passing sweep."""
    problems = []
    if not summaries:
        return None, ['no shard summaries to merge']
    heads = sorted({str(item.get('head')) for item in summaries})
    labels = sorted({str(item.get('label')) for item in summaries})
    if len(heads) != 1 or not summaries[0].get('head'):
        problems.append('shards ran on different or unknown commits: ' + ', '.join(heads))
    if len(labels) != 1 or not summaries[0].get('label'):
        problems.append('shards carry different or missing labels: ' + ', '.join(labels))
    counts = {tuple(item['shard'])[1] for item in summaries if item.get('shard')}
    if len(counts) > 1 or (counts and any(not item.get('shard') for item in summaries)):
        problems.append('summaries come from different shard layouts')
    elif counts:
        count = counts.pop()
        seen = [item['shard'][0] for item in summaries]
        problems += [f'shard {index}/{count} has no summary' for index in range(1, count + 1) if index not in seen]
        problems += [f'shard {index}/{count} appears more than once' for index in sorted(set(seen)) if seen.count(index) > 1]
    expected, results = set(), {}
    for item in summaries:
        expected.update(item.get('expected') or item.get('results') or {})
        for name, result in (item.get('results') or {}).items():
            if name in results:
                problems.append(f'{name} has results in more than one summary')
            results[name] = result
    if not expected:
        problems.append('the summaries expect no jobs')
    for name in sorted(expected):
        if name not in results:
            problems.append(f'{name} has no result')
        elif not results[name].get('ok'):
            problems.append(f'{name} failed (rc {results[name].get("rc")}): {str(results[name].get("last", ""))[:200]}')
    merged = {'head': summaries[0].get('head'), 'label': summaries[0].get('label'), 'total': len(expected),
              'passed': sum(1 for name in expected if results.get(name, {}).get('ok')),
              'expected': sorted(expected), 'results': {name: results[name] for name in sorted(results)}}
    return merged, problems


def markdown(merged, problems):
    lines = ['## Family sweep', '']
    if merged:
        lines += [f'`{merged["label"]}` on `{merged["head"]}`: {merged["passed"]}/{merged["total"]} passed.', '']
    if problems:
        lines += ['### Problems', ''] + [f'- {problem}' for problem in problems] + ['']
    if merged:
        lines += ['| Job | Result | Seconds |', '| --- | --- | ---: |']
        for name in merged['expected']:
            result = merged['results'].get(name)
            state = 'missing' if result is None else 'pass' if result.get('ok') else 'FAIL'
            lines.append(f'| `{name}` | {state} | {"" if result is None else result.get("seconds", "")} |')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('summaries', type=Path, nargs='*')
    parser.add_argument('--label')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--markdown', type=Path)
    args = parser.parse_args(argv)
    paths, out = list(args.summaries), args.out
    if args.label:
        directory = ROOT / 'quality-reports/go-migration' / f'sweep-{args.label}'
        paths += sorted(directory.glob('summary-shard-*.json'))
        out = out or directory / 'summary.json'
    if not out:
        parser.error('give --label or --out')
    summaries, problems = [], []
    for path in paths:
        try:
            summaries.append(json.loads(path.read_text()))
        except (OSError, ValueError) as error:
            problems.append(f'{path}: unreadable ({error})')
    merged, found = merge(summaries)
    problems += found
    if merged:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(merged, indent=1) + '\n')
    if args.markdown:
        with args.markdown.open('a') as stream:
            stream.write(markdown(merged, problems))
    for problem in problems:
        print('PROBLEM ' + problem)
    print(json.dumps({'summary': str(out), 'total': merged['total'] if merged else 0,
                      'passed': merged['passed'] if merged else 0, 'problems': len(problems), 'success': not problems}))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
