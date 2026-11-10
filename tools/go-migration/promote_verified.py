#!/usr/bin/env python3
"""Promote implementing ledger entries to verified from executed parity receipts (dry run by default).

  promote_verified.py --sweep SUMMARY.json [--components SUMMARY.json ...] [--patch PATCH.json] [--apply]

Inputs (all required to exist and be non-empty):
  --sweep       one or more summary.json files of a full sweep (sweep.py), later ones winning per key: results keyed http:<family> / process:<module>;
                a hand-written summary for one family re-run adds "family_label" (the family.py run label)
  --components  summary.json of run_components.py: results keyed component:<name>, each listing its entries
  --patch       docs/architecture/go-migration/ledger-verified-patch.json: null contract fields to fill,
                source_removed entries to drop (with the covering entry), entries held back with a reason

An entry's results are the sweep keys its tests map to (TESTS/fixtures/<m>_http.json -> http:<m with - for _>,
TESTS/adapters/go/<x>_process.py -> process:<x>) plus every component listing it. It becomes verified only when
it has at least one result, every result passed and its receipt checks out, it is not held, and go, tests,
source_present and the five contract fields are set. Its prior evidence moves to notes; the new evidence is one
{command, revision, result: 'passed', artifact, receipt_sha256, scope} item per result. Results recorded at a
revision whose product files differ from the working tree are refused unless the patch's revision_changes
records that revision -> the tree's revision with a reason and rerun_keys covering every key the changed files
can affect (entry go/tests/source files, family and process harness files, component command files); those keys
then need results at the newer revision. Verified entries are re-evaluated too and demoted when they no longer
pass. --apply writes the ledger; without it nothing is written.
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
    # A full sweep labels family runs sweep-<label>; a single family re-run names its own run label.
    run_label = summary.get('family_label') or f'sweep-{label}'
    out = {}
    for key, result in results.items():
        kind, name = key.split(':', 1)
        if not result.get('ok'):
            out[key] = f'sweep {key} failed: {result.get("last", "")[:200]}'
            continue
        if kind == 'http':
            artifact = Path(f'quality-reports/go-migration/{name}-http-{run_label}/results.json')
            command = f'python tools/go-migration/family.py run {name} --versioned --label {run_label}'
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
            reports = []
            for line in (path.read_text().splitlines() if path.is_file() else []):
                try:
                    parsed = json.loads(line) if line.lstrip().startswith('{') else None
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict) and 'success' in parsed:
                    reports.append(line.strip())
            # stdout and stderr share the log: the harness's last JSON report decides, not a trailing warning.
            if not reports or json.loads(reports[-1])['success'] is not True:
                out[key] = f'sweep {key} log missing or not successful: {artifact}'
                continue
            scope = f'Process harness {name}: ' + reports[-1][:300]
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


def family_key(path):
    """The sweep key a family or process harness file belongs to, if any."""
    for pattern in (r'TESTS/fixtures/(?:generate_)?(\w+)_http\.(?:json|py)', r'TESTS/contracts/test_(\w+)_http\.py',
                    r'TESTS/adapters/django/(\w+)_http\.py'):
        match = re.fullmatch(pattern, path)
        if match:
            return 'http:' + match.group(1).replace('_', '-')
    match = re.fullmatch(r'TESTS/adapters/go/(\w+_process)\.py', path)
    return 'process:' + match.group(1) if match else None


def command_files(command):
    """Repository files a recorded component command runs (script paths and -m modules)."""
    files = set()
    for token in command.split():
        if token.endswith('.py') and '/' in token:
            files.add(token)
        elif re.fullmatch(r'TESTS(\.\w+)+', token):
            files.add(token.replace('.', '/') + '.py')
    return files


def impacted_keys(changed, entries, entry_keys, component_commands):
    """Result keys whose evidence a set of changed files can invalidate (a floor; reviewers may add more)."""
    changed = set(changed)
    keys = set()
    for entry in entries:
        if changed & set(entry['go'] + entry['tests'] + [entry['django']['file']]):
            keys.update(entry_keys(entry))
    keys.update(k for k in (family_key(path) for path in changed) if k)
    keys.update(key for key, files in component_commands.items() if files & changed)
    return keys


def check_revisions(heads, patch, root, entries, entry_keys, component_commands):
    """Return {head: rerun keys} for heads older than the tree, refusing any change no record covers."""
    stale = {}
    for head in heads:
        changed = changed_product_files(root, head)
        if not changed:
            continue
        record = next((r for r in patch.get('revision_changes') or [] if head.startswith(r.get('from', '-'))), None)
        if record is None or not record.get('to') or not record.get('reason'):
            raise PromotionError(f'results recorded at {head[:12]} but product files changed since: ' + ', '.join(changed[:10]))
        if changed_product_files(root, record['to']):
            raise PromotionError(f'revision record {head[:12]} -> {record["to"][:12]} does not match the tree')
        between = subprocess.run(['git', 'diff', '--name-only', head, record['to'], '--'], cwd=root,
                                 capture_output=True, text=True, check=True).stdout.split()
        floor = impacted_keys(set(between) - NON_PRODUCT, entries, entry_keys, component_commands)
        rerun = set(record.get('rerun_keys') or [])
        missing = sorted(floor - rerun)
        if missing:
            raise PromotionError(f'revision record {head[:12]} -> {record["to"][:12]} omits impacted keys: ' + ', '.join(missing))
        stale[head] = rerun
    return stale


def promote(document, patch, sweeps, components, root=ROOT):
    """Re-evaluate every implementing and verified entry from the given results (later summaries win)."""
    entries = document['entries']
    filled, dropped = apply_patch(entries, patch)
    results, heads, component_entries, component_commands = {}, {}, collections.defaultdict(list), {}
    for sweep in sweeps:
        head, found = sweep_results(sweep, root)
        results.update(found)
        heads.update({key: head for key in found})
    for summary in components:
        head, found, listed = component_results(summary, root)
        results.update(found)
        heads.update({key: head for key in found})
        for identifier, keys in listed.items():
            component_entries[identifier].extend(k for k in keys if k not in component_entries[identifier])
        for key, result in (summary.get('results') or {}).items():
            component_commands[key] = set().union(*[command_files(c.get('command', '')) for c in result.get('commands') or []] or [set()])
    if not results:
        raise PromotionError('no results to promote from')
    entry_keys = lambda entry: result_keys(entry) + component_entries.get(entry['id'], [])
    stale = check_revisions(set(heads.values()), patch, root, entries, entry_keys, component_commands)
    for key, head in heads.items():
        if key in stale.get(head, ()):
            results[key] = f'{key} recorded at {head[:12]} needs a re-run after the reviewed revision change'
    hold = patch.get('hold') or {}
    promoted, refreshed, demoted, blocked = [], [], [], {}
    for entry in entries:
        if entry['status'] not in ('implementing', 'verified'):
            continue
        identifier = entry['id']
        keys = entry_keys(entry)
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
            if entry['status'] == 'verified':
                entry['status'] = 'implementing'
                demoted.append(identifier)
            continue
        evidence = []
        for key in keys:
            value = results[key]
            evidence.extend(value if isinstance(value, list) else [value])
        if entry['status'] == 'implementing':
            entry['notes'] = entry['notes'] + [n for n in (prior_evidence_note(i) for i in entry['evidence']) if n not in entry['notes']]
            promoted.append(identifier)
        elif entry['evidence'] != evidence:
            refreshed.append(identifier)
        entry['evidence'] = evidence
        entry['status'] = 'verified'
    validate(entries)
    return {'filled_contract_fields': filled, 'dropped': dropped, 'promoted': promoted, 'refreshed': refreshed,
            'demoted': demoted, 'blocked': blocked,
            'status': dict(sorted(collections.Counter(e['status'] for e in entries).items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sweep', type=Path, nargs='+', required=True)
    parser.add_argument('--components', type=Path, nargs='*', default=[])
    parser.add_argument('--patch', type=Path, default=PATCH)
    parser.add_argument('--ledger', type=Path, default=LEDGER)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        document = read_json(args.ledger, 'ledger')
        patch = read_json(args.patch, 'patch')
        sweeps = [read_json(path, 'sweep summary') for path in args.sweep]
        for sweep in sweeps:
            if args.apply and len(sweep.get('results') or {}) != sweep.get('total'):
                raise PromotionError(f'sweep incomplete: {len(sweep.get("results") or {})}/{sweep.get("total")} jobs recorded')
        components = [read_json(path, 'component summary') for path in args.components]
        report = promote(document, patch, sweeps, components)
    except PromotionError as exc:
        print('GUARD: ' + str(exc), file=sys.stderr)
        return 97
    if args.apply:
        args.ledger.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({**report, 'promoted': len(report['promoted']), 'applied': args.apply}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
