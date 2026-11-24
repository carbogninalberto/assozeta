"""Phase 3, part B: running attempts and SQL jobs (D05, D07, D13, abort). Contract: test_p3_email_job.py docstring."""
import unittest

from TESTS.handoff.p3_suite import ScenarioAssertions


class RunningAttempts(ScenarioAssertions, unittest.TestCase):
    SUITE = 'durable'

    def test_early_acked_job_with_zero_broker_depth_holds_settle(self):
        self.scenario('d05_early_ack_zero_depth_holds_settle')

    def test_killed_attempt_before_its_effect_is_released_and_runs_once(self):
        self.scenario('d05_killed_attempt_is_released_and_runs_once')
    def test_late_ack_redelivery_after_worker_loss_runs_again_without_a_switch(self):
        self.scenario('late_ack_redelivery_after_worker_loss_runs_again')

    def test_late_ack_redelivery_through_a_transaction_pooler_runs_again(self):
        self.scenario('late_ack_redelivery_through_a_transaction_pooler')

    def test_late_ack_redelivery_of_a_message_queued_before_the_worker_started_runs_again(self):
        self.scenario('late_ack_redelivery_of_a_startup_backlog_message')

    def test_redelivery_while_the_executor_runs_neither_runs_nor_steals_the_attempt(self):
        self.scenario('redelivery_while_the_executor_runs_waits')

    def test_a_django_execution_longer_than_one_lease_keeps_it(self):
        self.scenario('long_execution_keeps_its_lease_django')

    def test_an_orphaned_django_child_keeps_its_lease(self):
        self.scenario('orphan_child_keeps_its_lease')

    def test_a_django_child_dying_with_its_parent_lets_the_lease_lapse(self):
        self.scenario('child_dying_with_its_parent_lets_the_lease_lapse')

    def test_a_go_execution_longer_than_one_lease_keeps_it(self):
        self.scenario('long_execution_keeps_its_lease_go')

    def test_stopped_django_worker_cannot_complete_after_the_switch(self):
        self.scenario('d05_stopped_worker_cannot_complete_later')

    def test_go_worker_dying_with_a_lease_is_settled_after_the_lease(self):
        self.scenario('d05_go_lease_holder_dies')

    def test_stopped_go_worker_resuming_before_commit_is_fenced(self):
        self.scenario('d05_stopped_go_worker_resumes_before_commit')


class SqlJobsAndRollback(ScenarioAssertions, unittest.TestCase):
    SUITE = 'durable'

    def test_go_pending_leased_terminal_and_historical_rows_on_the_reverse_switch(self):
        self.scenario('d07_go_rows_on_reverse')

    def test_legacy_broker_task_becomes_a_go_durable_job(self):
        self.scenario('d07_legacy_broker_task_to_go_durable')

    def test_rollback_transfers_the_target_backlog_under_a_new_generation(self):
        self.scenario('d13_rollback_with_target_backlog')

    def test_abort_before_commit_returns_staged_and_held_work_to_the_source(self):
        self.scenario('abort_before_commit_returns_work_to_source')


if __name__ == '__main__':
    unittest.main()
