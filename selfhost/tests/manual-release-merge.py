"""Merge the parallel current and historical appliance rehearsal results.

An ``--part all`` rehearsal writes one results.json. The full appliance run
splits its current and historical sequences across two jobs, each with its own
runner and its own results.json; this merges them into a single file with the
same top-level keys an ``--part all`` run writes plus the per-part summaries, so
the release gate keeps reading one bounded artifact.

Only results.json is copied: execution logs, browser artifacts and fixture data
remain in the part artifacts and are never republished here.
"""
import argparse
import json
from pathlib import Path
import sys

# Both parts really build the production images and resolve the historical
# digests, so each records this check once; the merged sequence keeps the first.
DUPLICATED_CHECK = 'production images built and historical image digests resolved'
PART_FIELDS = ('release', 'application_revision')


class Mismatch(Exception):
    """The two parts do not describe one mergeable full rehearsal."""

    def __init__(self, failures):
        self.failures = list(failures)
        super().__init__('; '.join(self.failures))


def load(directory, part):
    """Read one part's bounded results; nothing else is read or copied."""
    path = Path(directory) / 'results.json'
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Mismatch([part + ' results are unreadable at ' + str(path) + ' (' + type(exc).__name__ + ')']) from exc
    if not isinstance(value, dict):
        raise Mismatch([part + ' results are not a JSON object'])
    return value


def checks(value, part):
    recorded = value.get('checks')
    if not isinstance(recorded, list) or not all(isinstance(item, str) for item in recorded):
        raise Mismatch([part + ' results do not record checks as a list of strings'])
    return recorded


def timings(value, part):
    recorded = value.get('timings')
    if not isinstance(recorded, list) or not all(isinstance(item, dict) for item in recorded):
        raise Mismatch([part + ' results do not record timings as a list of objects'])
    return [{**item, 'part': part} for item in recorded]


def validate(current, historical):
    """Every reason the two parts cannot be merged into one release result."""
    failures = []
    for name, value in (('current', current), ('historical', historical)):
        if value.get('part') != name:
            failures.append(name + ' results record part ' + repr(value.get('part')))
        if value.get('status') != 'passed':
            failures.append(name + ' part did not pass (' + repr(value.get('status')) + ')')
        if value.get('scope') != 'full':
            failures.append(name + ' part did not rehearse the full scope (' + repr(value.get('scope')) + ')')
    for field in PART_FIELDS:
        mine, other = current.get(field), historical.get(field)
        if not isinstance(mine, str) or not mine:
            failures.append('current results do not record ' + field)
        elif mine != other:
            failures.append('parts disagree on ' + field + ': ' + repr(mine) + ' and ' + repr(other))
    return failures


def summary(name, value):
    return {'part': name, 'status': value.get('status'), 'elapsed_seconds': value.get('elapsed_seconds'),
            'checks_recorded': len(value.get('checks') or [])}


def merge_results(current, historical):
    """One results.json in the shape an ``--part all`` run writes, plus parts."""
    failures = validate(current, historical)
    if failures:
        raise Mismatch(failures)
    merged = list(checks(current, 'current'))
    for check in checks(historical, 'historical'):
        # Both parts really build the production images and resolve the
        # historical digests; the duplicate would only inflate the evidence.
        if check == DUPLICATED_CHECK and check in merged:
            continue
        merged.append(check)
    # Every other field already describes this exact rehearsal: release,
    # application_revision, scope, corpus identity and runner come from current.
    value = dict(current)
    # Evidence only one part can produce (the current Go artifact here, the
    # installed historical runtime there) is kept; shared fields stay current's.
    for key, item in historical.items():
        value.setdefault(key, item)
    value['part'] = 'merged'
    value['checks'] = merged
    value['timings'] = timings(current, 'current') + timings(historical, 'historical')
    value['parts'] = {name: summary(name, part)
                      for name, part in (('current', current), ('historical', historical))}
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', type=Path, required=True,
                        help='Directory holding the current part results.json')
    parser.add_argument('--historical', type=Path, required=True,
                        help='Directory holding the historical part results.json')
    parser.add_argument('--output', type=Path, required=True,
                        help='Directory that receives the merged results.json')
    args = parser.parse_args(argv)
    try:
        value = merge_results(load(args.current, 'current'), load(args.historical, 'historical'))
    except Mismatch as exc:
        for failure in exc.failures:
            print('Merge rejected: ' + failure, file=sys.stderr)
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'results.json').write_text(json.dumps(value, indent=2) + '\n')
    print('Merged ' + str(len(value['checks'])) + ' checks from the current and historical parts', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
