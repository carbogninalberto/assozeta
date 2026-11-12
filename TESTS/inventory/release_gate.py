"""Block installable Go releases while the compatibility inventory is unfinished.

This is a necessary gate, not proof of complete migration acceptance. Behavioral,
release, restore, browser, performance and integration gates remain independent.
The ledger is read-only: a release attempt never promotes migration statuses.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from TESTS.inventory.catalog import DEFAULT_LEDGER, ROOT, discover, reconcile, validate


class ReleaseGateError(ValueError):
    pass


def load_ledger(path):
    def unique_object(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ReleaseGateError('Compatibility ledger contains duplicate JSON keys.')
            result[name] = value
        return result

    def invalid_constant(_value):
        raise ReleaseGateError('Compatibility ledger contains a nonstandard JSON number.')

    try:
        return json.loads(Path(path).read_text(), object_pairs_hook=unique_object,
                          parse_constant=invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseGateError('Compatibility ledger cannot be read as valid JSON.') from exc


def check_release_inventory(document, root):
    if (not isinstance(document, dict) or type(document.get('format')) is not int
            or document['format'] != 1 or not isinstance(document.get('entries'), list)
            or not document['entries']):
        raise ReleaseGateError('Release requires a nonempty compatibility ledger in format 1.')
    entries = document['entries']
    try:
        validate(entries)
        current = reconcile(entries, discover(Path(root)))
    except (KeyError, TypeError, ValueError, AttributeError, OSError, SyntaxError, UnicodeError) as exc:
        raise ReleaseGateError('Compatibility ledger or source inventory is invalid.') from exc
    if current != entries:
        raise ReleaseGateError('Compatibility ledger is stale; review new, changed or removed source declarations.')
    counts = dict(sorted(Counter(entry['status'] for entry in entries).items()))
    if any(entry['status'] not in {'verified', 'deprecated'} for entry in entries):
        raise ReleaseGateError('Go migration inventory is incomplete; installable release blocked: '
                               + json.dumps(counts, sort_keys=True))
    return {'entries': len(entries), 'status': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, default=DEFAULT_LEDGER)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = check_release_inventory(load_ledger(args.ledger), args.root)
    except ReleaseGateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({**result, 'scope': 'necessary inventory gate; not full migration acceptance'}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
