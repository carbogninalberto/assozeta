"""Negative controls for shutdown fixture cleanup evidence."""
import unittest

from TESTS.adapters.django.payment_category_shutdown import _attempt


class CleanupEvidenceTests(unittest.TestCase):
    def test_failed_state_comparison_cannot_be_reported_as_cleanup_success(self):
        results, failures = {}, []
        self.assertFalse(_attempt(results, failures, 'snapshot_restored', lambda: False))
        self.assertIs(results['snapshot_restored'], False)
        self.assertEqual(len(failures), 1)

    def test_zero_process_exit_and_none_cleanup_result_are_successful(self):
        for value in (0, None):
            with self.subTest(value=value):
                results, failures = {}, []
                self.assertTrue(_attempt(results, failures, 'closed', lambda: value))
                self.assertEqual(results, {'closed': True})
                self.assertEqual(failures, [])

    def test_cleanup_exception_is_retained_and_does_not_prevent_next_cleanup(self):
        results, failures = {}, []

        def failed_rollback():
            raise RuntimeError('synthetic rollback failure')

        self.assertFalse(_attempt(results, failures, 'rollback', failed_rollback))
        self.assertTrue(_attempt(results, failures, 'close', lambda: None))
        self.assertEqual(results, {'rollback': False, 'close': True})
        self.assertEqual(len(failures), 1)
