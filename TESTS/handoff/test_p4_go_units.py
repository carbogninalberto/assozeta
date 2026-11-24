"""Phase 4 Go unit contracts behind the ``handoff`` build tag (no services needed).

Part A: BE_GO/internal/service/schedule_parity_handoff_test.go -- the native schedule
evaluator decides every authored Beat case of TESTS/fixtures/scheduler-authority.json
(DST gap and fold in a local timezone, day_of_week, clocked and completed one-offs,
absolute/relative expiry) exactly as Django's ModelEntry does (contract: test_p4_scheduling).

A compile error is an error, never a valid red.
"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


def go_test(package, pattern):
    if not shutil.which('go'):
        raise RuntimeError('Go is required; never skipped')
    completed = subprocess.run(['go', 'test', '-tags', 'handoff', '-count=1', '-run', pattern, package],
                               cwd=ROOT / 'BE_GO', capture_output=True, text=True, timeout=900)
    output = completed.stdout + completed.stderr
    if '[build failed]' in output or '[setup failed]' in output or 'no test files' in output:
        raise RuntimeError(f'go test {package} did not build:\n{output[-3000:]}')
    if 'no tests to run' in output:
        raise RuntimeError(f'go test {package} -run {pattern} matched no test')
    return completed.returncode, output


class GoScheduleEvaluator(unittest.TestCase):
    def test_native_evaluator_matches_every_authored_beat_case(self):
        code, output = go_test('./internal/service/', '^TestP4ScheduleDueMatchesEveryAuthoredBeatCase$')
        self.assertEqual(code, 0, 'Go schedule evaluator differs from Beat:\n' + output[-4000:])


if __name__ == '__main__':
    unittest.main()
