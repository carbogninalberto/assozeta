"""Run the handoff suite with expected-red tracking.

Every test in TESTS/handoff must either pass or be listed in expected_red.json.
A listed test must FAIL with an assertion whose text contains one of its
recorded signatures. Everything else is a violation and exits 1:
  - a listed test that passes (promote it: remove the entry in the same change),
  - a listed test that fails for another reason, or errors (import errors,
    missing tools or fixtures, crashes are never a valid red),
  - any skip, expectedFailure or unexpectedSuccess (a skip is never evidence),
  - a listed test that no longer exists.
The report (default quality-reports/handoff/expected-red.json) states
"release_evidence": false. A release gate must refuse while any entry remains.

    python3 -B -m TESTS.handoff.run_expected_red [--report PATH] [test names...]
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / 'expected_red.json'


class Recorder(unittest.TextTestResult):
    def __init__(self, *arguments, **keywords):
        super().__init__(*arguments, **keywords)
        self.records = defaultdict(list)

    def record(self, test, outcome, text=''):
        self.records[test.id()].append((outcome, text))

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, 'pass')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, 'fail', self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, 'error', self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, 'skip', reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.record(test, 'expected-failure', self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.record(test, 'unexpected-success')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            outcome = 'fail' if issubclass(err[0], test.failureException) else 'error'
            self.record(test, outcome, f'{subtest.id()}\n' + self._exc_info_to_string(err, test))


def load_manifest():
    document = json.loads(MANIFEST.read_text())
    if document.get('format') != 1 or document.get('release_evidence') is not False:
        raise SystemExit('expected_red.json: format 1 with "release_evidence": false is required')
    for identifier, entry in document['tests'].items():
        if (not isinstance(entry.get('phase'), int) or not 0 <= entry['phase'] <= 7
                or not entry.get('reason') or not entry.get('owner')
                or not entry.get('signatures') or not all(isinstance(s, str) and s for s in entry['signatures'])):
            raise SystemExit(f'expected_red.json: incomplete entry {identifier}')
    return document


def classify(records, manifest, selected_all):
    results, violations = {}, []
    for identifier, outcomes in sorted(records.items()):
        kinds = {outcome for outcome, _ in outcomes}
        expected = manifest['tests'].get(identifier)
        failures = [text for outcome, text in outcomes if outcome == 'fail']
        if kinds & {'skip', 'expected-failure', 'unexpected-success'}:
            state = 'violation: skipped or expected-failure outcome is never evidence'
        elif 'error' in kinds:
            state = 'violation: error, not an assertion failure'
        elif expected is None:
            state = 'pass' if kinds == {'pass'} else 'violation: unexpected red'
        elif not failures:
            state = 'violation: now green; remove it from expected_red.json in this change'
        elif all(any(signature in text for signature in expected['signatures']) for text in failures):
            state = 'expected-red'
        else:
            state = 'violation: red for a different reason than recorded'
        results[identifier] = {'state': state, 'outcomes': sorted(kinds),
                               'detail': [text[-1500:] for outcome, text in outcomes if outcome != 'pass'][:3]}
        if expected:
            results[identifier].update(phase=expected['phase'], reason=expected['reason'])
        if state.startswith('violation'):
            violations.append(identifier)
    if selected_all:
        for identifier in sorted(set(manifest['tests']) - set(records)):
            results[identifier] = {'state': 'violation: listed test no longer exists'}
            violations.append(identifier)
    return results, violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--report', type=Path, default=ROOT / 'quality-reports/handoff/expected-red.json')
    parser.add_argument('names', nargs='*', help='test names (default: discover TESTS/handoff)')
    args = parser.parse_args(argv)
    manifest = load_manifest()
    loader = unittest.TestLoader()
    if args.names:
        suite = loader.loadTestsFromNames(args.names)
    else:
        suite = loader.discover(str(ROOT / 'TESTS/handoff'), pattern='test_*.py', top_level_dir=str(ROOT))
    started = time.time()
    runner = unittest.TextTestRunner(resultclass=Recorder, verbosity=2, stream=sys.stderr)
    result = runner.run(suite)
    results, violations = classify(result.records, manifest, not args.names)
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    report = {'release_evidence': False,
              'scope': 'feature-branch expected-red tracking; not release or phase-exit evidence',
              'revision': revision, 'seconds': round(time.time() - started, 1),
              'counts': {state: sum(1 for r in results.values() if r['state'].split(':')[0] == state)
                         for state in ('pass', 'expected-red', 'violation')},
              'tests': results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    for identifier, item in results.items():
        label = item['state'].upper() if not item['state'].startswith('violation') else 'VIOLATION'
        suffix = f" (phase {item['phase']}: {item['reason']})" if 'phase' in item else ''
        print(f'{label:12} {identifier}{suffix}' + ('' if label != 'VIOLATION' else f" -- {item['state']}"))
    print(json.dumps(report['counts']))
    return 1 if violations else 0


if __name__ == '__main__':
    raise SystemExit(main())
