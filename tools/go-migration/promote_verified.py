#!/usr/bin/env python3
"""Promote implementing ledger entries to verified from executed parity receipts (dry run by default).

  promote_verified.py --sweep SUMMARY.json [--components SUMMARY.json ...] [--patch PATCH.json] [--apply]

Inputs (all required to exist and be non-empty):
  --sweep       summary.json of a full sweep (sweep.py): results keyed http:<family> / process:<module>
  --components  summary.json of run_components.py: results keyed component:<name>, each listing its entries
  --patch       docs/architecture/go-migration/ledger-verified-patch.json: null contract fields to fill,
                source_removed entries to drop (with the covering entry), entries held back with a reason

An entry's results are the sweep keys its tests map to (TESTS/fixtures/<m>_http.json -> http:<m with - for _>,
TESTS/adapters/go/<x>_process.py -> process:<x>) plus every component listing it. It becomes verified only when
it has at least one result, every result passed and its receipt checks out, it is not held, and go, tests,
source_present and the five contract fields are set. Its prior evidence moves to notes; the new evidence is one
{command, revision, result: 'passed', artifact, receipt_sha256, scope} item per result. Results recorded at a
revision whose product files differ from the working tree are refused. --apply writes the ledger; without it
nothing is written.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from TESTS.inventory.catalog import validate  # noqa: E402

LEDGER = ROOT / 'docs/architecture/go-migration/ledger.json'
PATCH = ROOT / 'docs/architecture/go-migration/ledger-verified-patch.json'
REQUIRED = ('authentication', 'permissions', 'resource_scope', 'side_effects', 'transaction')
TRANSPORTS = ['django-native', 'go-native', 'caddy-v1', 'caddy-v2']
# Files that may differ between the revision a receipt was recorded at and the tree being promoted:
# the ledger and this promotion tooling, none of which a parity harness executes.
NON_PRODUCT = {'docs/architecture/go-migration/ledger.json', 'docs/architecture/go-migration/ledger-verified-patch.json',
               'tools/go-migration/promote_verified.py', 'tools/go-migration/run_components.py',
               'tools/go-migration/ledger_components.json', 'TESTS/inventory/test_promote_verified.py'}


class PromotionError(Exception):
    pass


def read_json(path, what):
    path = Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise PromotionError(f'missing or empty {what}: {path}')
    data = json.loads(path.read_text())
    if not data:
        raise PromotionError(f'empty {what}: {path}')
    return data


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result_keys(entry):
    keys = []
    for test in entry.get('tests', []):
        match = re.fullmatch(r'TESTS/fixtures/(\w+)_http\.json', test)
        if match:
            keys.append('http:' + match.group(1).replace('_', '-'))
        match = re.fullmatch(r'TESTS/adapters/go/(\w+_process)\.py', test)
        if match:
            keys.append('process:' + match.group(1))
    return keys


def changed_product_files(root, revision):
    """Files that differ between revision and the working tree, other than the ledger and promotion tooling."""
    listed = subprocess.run(['git', 'diff', '--name-only', revision, '--'], cwd=root, capture_output=True, text=True)
    if listed.returncode:
        raise PromotionError(f'cannot compare the tree with {revision}: {listed.stderr.strip()}')
    untracked = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'], cwd=root, capture_output=True, text=True)
    if untracked.returncode:
        raise PromotionError('cannot list untracked files: ' + untracked.stderr.strip())
    return sorted(set(listed.stdout.split() + untracked.stdout.split()) - NON_PRODUCT)


def sweep_results(summary, root):
    """Map http:/process: keys to evidence items, or to a failure reason."""
    head, label, results = summary.get('head'), summary.get('label'), summary.get('results') or {}
    if not head or not label or not results:
        raise PromotionError('sweep summary lacks head, label or results')
    out = {}
    for key, result in results.items():
        kind, name = key.split(':', 1)
        if not result.get('ok'):
            out[key] = f'sweep {key} failed: {result.get("last", "")[:200]}'
            continue
        if kind == 'http':
            artifact = Path(f'quality-reports/go-migration/{name}-http-sweep-{label}/results.json')
            command = f'python tools/go-migration/family.py run {name} --versioned --label sweep-{label}'
            path = root / artifact
            if not path.is_file() or not path.stat().st_size:
                out[key] = f'sweep {key} receipt missing: {artifact}'
                continue
            report = json.loads(path.read_text())
            fixture = root / f'TESTS/fixtures/{name.replace("-", "_")}_http.json'
            count = len(json.loads(fixture.read_text())['cases']) if fixture.is_file() else 0
            workloads = report.get('workloads') or []
            if (report.get('success') is not True or [w.get('label') for w in workloads] != TRANSPORTS
                    or not count or any(len(w.get('cases') or []) != count for w in workloads)):
                out[key] = f'sweep {key} receipt is not a {count}-case pass on all four transports'
                continue
            scope = f'HTTP family {name}: {count}/{count} cases on native Django, native Go, Caddy /api/v1 and /api/v2.'
        elif kind == 'process':
            artifact = Path(f'quality-reports/go-migration/sweep-{label}/process-{name}.log')
            command = f'python -m TESTS.adapters.go.{name}'
            path = root / artifact
            lines = [l for l in path.read_text().splitlines() if l.strip()] if path.is_file() else []
            if not lines or '"success": true' not in lines[-1]:
                out[key] = f'sweep {key} log missing or not successful: {artifact}'
                continue
            scope = f'Process harness {name}: ' + lines[-1][:300]
        else:
            raise PromotionError(f'unknown sweep result key {key}')
        out[key] = {'command': command, 'revision': head, 'result': 'passed', 'artifact': str(artifact),
                    'receipt_sha256': sha256(path), 'scope': scope}
    return head, out


def component_results(summary, root):
    head, results = summary.get('head'), summary.get('results') or {}
    if not head or not results:
        raise PromotionError('component summary lacks head or results')
    out, entries = {}, collections.defaultdict(list)
    for key, result in results.items():
        for identifier in result.get('entries') or []:
            entries[identifier].append(key)
        commands = result.get('commands') or []
        if not result.get('ok') or not commands or not all(c.get('ok') for c in commands):
            failed = next((c for c in commands if not c.get('ok')), {})
            out[key] = f'{key} failed: {failed.get("command", "not run")} ({failed.get("check", "")})'
            continue
        items = []
        for command in commands:
            path = root / command.get('artifact', '')
            if not command.get('artifact') or not path.is_file() or not path.stat().st_size \
                    or sha256(path) != command.get('receipt_sha256'):
                items = f'{key} receipt missing or changed: {command.get("artifact")}'
                break
            items.append({'command': command['command'], 'revision': head, 'result': 'passed',
                          'artifact': command['artifact'], 'receipt_sha256': command['receipt_sha256'],
                          'scope': f'{key.split(":", 1)[1]} harness: {command["check"]}.'})
        out[key] = items
    return head, out, entries


def apply_patch(entries, patch):
    by_id = {entry['id']: entry for entry in entries}
    filled = 0
    for identifier, fields in (patch.get('contract') or {}).items():
        entry = by_id.get(identifier)
        if entry is None:
            raise PromotionError(f'patch names an unknown entry: {identifier}')
        for field, value in fields.items():
            if field not in entry['contract'] or not isinstance(value, str) or not value.strip():
                raise PromotionError(f'{identifier}: invalid contract field {field}')
            current = entry['contract'][field]
            if current is None:
                entry['contract'][field] = value
                filled += 1
            elif current != value:
                raise PromotionError(f'{identifier}: refusing to overwrite recorded {field}')
    dropped = []
    for identifier, drop in (patch.get('drop_source_removed') or {}).items():
        entry, cover = by_id.get(identifier), by_id.get(drop.get('covered_by', ''))
        if entry is None:
            continue  # already dropped
        if entry['status'] != 'source_removed' or entry['source_present']:
            raise PromotionError(f'{identifier} is not source_removed')
        if cover is None or not cover['source_present'] or cover['kind'] != entry['kind'] \
                or cover['django']['symbol'] != entry['django']['symbol'] \
                or cover['declaration'].get('schedule', cover['declaration']) != entry['declaration'].get('schedule', entry['declaration']):
            raise PromotionError(f'{identifier}: covering entry missing or declaring something else')
        if not drop.get('note', '').strip():
            raise PromotionError(f'{identifier}: drop needs a rationale note')
        if drop['note'] not in cover['notes']:
            cover['notes'].append(drop['note'])
        entries.remove(entry)
        dropped.append(identifier)
    return filled, dropped


def prior_evidence_note(item):
    if isinstance(item, str):
        return 'Implementing evidence: ' + item
    text = item.get('result') or ''
    where = item.get('artifact') or ''
    return 'Implementing evidence: ' + ' '.join(str(part) for part in (text, f'({where})' if where else '') if part)


def promote(document, patch, sweep, components, root=ROOT):
    entries = document['entries']
    filled, dropped = apply_patch(entries, patch)
    results, component_entries, heads = {}, collections.defaultdict(list), set()
    if sweep is not None:
        head, found = sweep_results(sweep, root)
        heads.add(head)
        results.update(found)
    for summary in components:
        head, found, listed = component_results(summary, root)
        heads.add(head)
        results.update(found)
        for identifier, keys in listed.items():
            component_entries[identifier].extend(k for k in keys if k not in component_entries[identifier])
    if not results:
        raise PromotionError('no results to promote from')
    for head in heads:
        changed = changed_product_files(root, head)
        if changed:
            raise PromotionError(f'results recorded at {head[:12]} but product files changed since: ' + ', '.join(changed[:10]))
    hold = patch.get('hold') or {}
    promoted, blocked = [], {}
    for entry in entries:
        if entry['status'] != 'implementing':
            continue
        identifier = entry['id']
        keys = result_keys(entry) + component_entries.get(identifier, [])
        reasons = []
        if identifier in hold:
            reasons.append('held: ' + hold[identifier])
        if not keys:
            reasons.append('no parity result maps to this entry')
        for key in keys:
            if key not in results:
                reasons.append(f'no result for {key}')
            elif isinstance(results[key], str):
                reasons.append(results[key])
        missing = [field for field in REQUIRED if entry['contract'].get(field) is None]
        if missing:
            reasons.append('contract lacks ' + ', '.join(missing))
        if not entry['go'] or not entry['tests'] or not entry['source_present']:
            reasons.append('go, tests or source_present missing')
        if reasons:
            blocked[identifier] = reasons
            continue
        evidence = []
        for key in keys:
            value = results[key]
            evidence.extend(value if isinstance(value, list) else [value])
        entry['notes'] = entry['notes'] + [n for n in (prior_evidence_note(i) for i in entry['evidence']) if n not in entry['notes']]
        entry['evidence'] = evidence
        entry['status'] = 'verified'
        promoted.append(identifier)
    validate(entries)
    return {'filled_contract_fields': filled, 'dropped': dropped, 'promoted': promoted, 'blocked': blocked,
            'status': dict(sorted(collections.Counter(e['status'] for e in entries).items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sweep', type=Path, required=True)
    parser.add_argument('--components', type=Path, nargs='*', default=[])
    parser.add_argument('--patch', type=Path, default=PATCH)
    parser.add_argument('--ledger', type=Path, default=LEDGER)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        document = read_json(args.ledger, 'ledger')
        patch = read_json(args.patch, 'patch')
        sweep = read_json(args.sweep, 'sweep summary')
        if args.apply and len(sweep.get('results') or {}) != sweep.get('total'):
            raise PromotionError(f'sweep incomplete: {len(sweep.get("results") or {})}/{sweep.get("total")} jobs recorded')
        components = [read_json(path, 'component summary') for path in args.components]
        report = promote(document, patch, sweep, components)
    except PromotionError as exc:
        print('GUARD: ' + str(exc), file=sys.stderr)
        return 97
    if args.apply:
        args.ledger.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({**report, 'promoted': len(report['promoted']), 'applied': args.apply}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
