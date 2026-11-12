"""Admission transport ownership controls; no HTTP/database acceptance claim."""
from contextlib import contextmanager, ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from TESTS.adapters.django import message_mutations as adapter
from TESTS.adapters.django import historical_read_cache as cache


class StopBeforeHTTP(Exception):
    pass


class AdmissionTransportTests(unittest.TestCase):
    def exercise(self, custom, fail_admission=False):
        events = []
        case = {'name': 'transport-control', 'admission': 'locked'}
        fixture = {'runtime': {'cases': [case]}, 'baseline_state': {},
                   'baseline_cache': None, 'contrasts': {}}
        @contextmanager
        def failures(actual_case, actual_fixture):
            self.assertIs(actual_case, case)
            self.assertIs(actual_fixture, fixture)
            events.append('failure-enter')
            try:
                yield
            finally:
                events.append('failure-exit')
        @contextmanager
        def admission(actual_case, actual_fixture):
            self.assertIs(actual_case, case)
            self.assertIs(actual_fixture, fixture)
            events.append('admission-enter')
            if fail_admission:
                raise StopBeforeHTTP()
            try:
                yield
            finally:
                events.append('admission-exit')
        with ExitStack() as stack:
            for obj, name, kwargs in (
                (adapter, 'require_private_database', {}),
                (adapter.contract, 'validate_runtime', {}),
                (adapter.shared, 'assert_state_unchanged', {}),
                (adapter.shared, 'assert_cache_unchanged', {}),
                (adapter.scopes, 'snapshot_state', {'side_effect': [{}, StopBeforeHTTP()]}),
                (cache, 'snapshot_historical_read_cache', {'return_value': None}),
                (adapter, 'failure_control', {'side_effect': failures}),
            ):
                stack.enter_context(patch.object(obj, name, **kwargs))
            default = stack.enter_context(patch.object(adapter, 'admission_control',
                side_effect=AssertionError('wrong lock transport') if custom else admission))
            http = stack.enter_context(patch.object(adapter.contract, 'assert_case'))
            with self.assertRaises(StopBeforeHTTP):
                adapter.run_workload('transport', 'go', 'http://127.0.0.1:1', fixture,
                    None, None, SimpleNamespace(), remote_addr='127.0.0.1',
                    process_guard=lambda: {'pid': 123, 'instance': 'owned', 'running': True},
                    **({'admission_controller': admission} if custom else {}))
            http.assert_not_called()
            if custom:
                default.assert_not_called()
            else:
                default.assert_called_once_with(case, fixture)
        self.assertEqual(events, ['failure-enter', 'admission-enter'] +
                         ([] if fail_admission else ['admission-exit']) + ['failure-exit'])

    def test_explicit_container_transport_owns_admission_until_body_failure(self):
        self.exercise(True)

    def test_default_host_transport_remains_unchanged(self):
        self.exercise(False)

    def test_container_acquisition_failure_unwinds_without_http_or_fallback(self):
        self.exercise(True, True)


if __name__ == '__main__':
    unittest.main()
