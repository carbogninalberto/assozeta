"""Execute CLI manual admission with explicit current/historical Compose fixtures."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS = (ROOT / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]


class GoManualPreflightTests(unittest.TestCase):
    def run_check(self, *, golang=True, pinned=True, failure='', config_failure=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / '.env'
            content = ('ASSOZETA_VERSION=2.0.1\nASSOZETA_BACKEND_REF=django@sha256:fixture\n'
                       'ASSOZETA_BACKEND_GO_IMAGE=private.example/backend-go\n')
            if pinned:
                content += 'ASSOZETA_BACKEND_GO_REF=go@sha256:fixture\n'
            environment.write_text(content)
            harness = r'''
prod_compose() {
    [ "$*" = 'config --services' ] || return 98
    [ "$TEST_CONFIG_FAILURE" = false ] || return 8
    printf 'api\nworker\nbeat\n'
    [ "$TEST_GO" = false ] || printf 'api-go\n'
}
sh() {
    shift
    printf 'check %s\n' "$*"
    [ "$TEST_FAILURE" != "${3:-django}" ] || return 9
}
if ! check_prod_manual; then
    printf 'rejected\n'
    exit 9
fi
printf 'admitted\n'
'''
            result = subprocess.run(['sh', '-c', DEFINITIONS + harness], capture_output=True, text=True,
                env={**os.environ, 'ASSOZETA_INSTALL_ROOT': directory,
                     'TEST_GO': str(golang).lower(), 'TEST_FAILURE': failure,
                     'TEST_CONFIG_FAILURE': str(config_failure).lower()})
            self.assertEqual(environment.read_text(), content)
            return result

    def test_both_candidates_validated_with_immutable_go_reference(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            'check django@sha256:fixture 2.0.1', 'check go@sha256:fixture 2.0.1 go', 'admitted'])

    def test_explicit_go_repository_supplies_tag_when_unpinned(self):
        result = self.run_check(pinned=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('check private.example/backend-go:2.0.1 2.0.1 go', result.stdout.splitlines())

    def test_historical_compose_without_go_never_looks_for_go_image(self):
        result = self.run_check(golang=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['check django@sha256:fixture 2.0.1', 'admitted'])

    def test_go_validation_failure_stops_admission(self):
        result = self.run_check(failure='go')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('admitted', result.stdout)
        self.assertIn('check go@sha256:fixture 2.0.1 go', result.stdout)

    def test_django_failure_cannot_be_overwritten_by_go_success_in_conditional(self):
        result = self.run_check(failure='django')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('admitted', result.stdout)
        self.assertNotIn('check go@', result.stdout)

    def test_historical_compose_cannot_overwrite_django_failure_in_conditional(self):
        result = self.run_check(failure='django', golang=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('admitted', result.stdout)

    def test_compose_failure_is_not_treated_as_historical_absence(self):
        result = self.run_check(config_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('admitted', result.stdout)


if __name__ == '__main__':
    unittest.main()
