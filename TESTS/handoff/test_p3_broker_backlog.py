"""Phase 3, part B: the Redis backlog (D02, D03, D04, D06, D10, D15). Contract: test_p3_email_job.py docstring."""
import unittest

from TESTS.handoff.p3_suite import ScenarioAssertions


class BrokerBacklog(ScenarioAssertions, unittest.TestCase):
    SUITE = 'broker'

    def test_priority_subqueues_of_both_queues_cross_by_identity_django_to_go(self):
        self.scenario('d02_priorities_forward')

    def test_priority_subqueues_of_both_queues_cross_by_identity_go_to_django(self):
        self.scenario('d02_priorities_reverse')

    def test_crash_between_pop_and_unacked_bookkeeping_loses_nothing(self):
        self.scenario('d03_pop_before_bookkeeping')

    def test_controller_crashes_after_receipt_source_ack_and_destination_publish_converge(self):
        self.scenario('d03_controller_crashes')

    def test_prefetched_running_held_and_next_month_jobs_get_one_disposition_each(self):
        self.scenario('d04_prefetch_running_held_and_next_month')

    def test_visibility_restore_racing_staging_leaves_no_source_copy(self):
        self.scenario('d04_visibility_restore_races_staging')

    def test_retry_on_the_boundary_keeps_id_lineage_stamp_and_eta(self):
        self.scenario('d06_retry_on_the_boundary')

    def test_child_published_during_drain_keeps_lineage_and_runs_once_on_target(self):
        self.scenario('d06_child_during_drain')

    def test_original_expiry_still_applies_on_the_target(self):
        self.scenario('d06_expiry_applies_on_target')

    def test_legacy_and_unsupported_messages_are_adopted_or_blocked_with_a_reason(self):
        self.scenario('d10_legacy_and_unsupported_messages')

    def test_two_tenants_and_a_backlog_larger_than_the_batch_cross_completely(self):
        self.scenario('d15_tenants_and_batches')


if __name__ == '__main__':
    unittest.main()
