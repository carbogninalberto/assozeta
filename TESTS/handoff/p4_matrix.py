"""The Phase 4 task matrix: every row of tmp/go-handoff/task-coverage.md as data.

One Family per task-coverage row, ordered cheapest and most numerous first. A family is run by
TESTS/handoff/p4_scenarios_matrix.py through its module (TESTS/handoff/p4_family_<id>.py), on its
own stack when selected with ASSOZETA_HANDOFF_P4_FAMILIES=<id>[,<id>...], so implementers can work
on families in parallel. ``switch_points`` are the boundaries task-coverage.md names for the row;
``needs`` the private services the family's stack adds beyond PostgreSQL/Redis/SMTP; ``size`` the
relative implementation effort (S < M < L < XL) and ``seconds`` the expected green suite cost.
``covered_by`` names rows whose scenarios live in Part A/B (the three scheduled exceptions) or in
Phase 3 (the regression fixture).
"""
from collections import namedtuple

Family = namedtuple('Family', 'id size seconds row tasks switch_points needs covered_by')

FAMILIES = (
    Family('db_maintenance', 'S', 120,
           'Jobs mapped by the Go scheduler: auto_mark_attendance, update_age_of_associates, '
           'reset_communication_daily_email_balance, clear_expired_subscription_tokens',
           ('auto_mark_attendance', 'update_age_of_associates', 'reset_communication_daily_email_balance',
            'clear_expired_subscription_tokens'),
           ('steady', 'queued', 'rerun_after_completion'), (), None),
    Family('legacy_names', 'S', 60,
           'Legacy names and test-only coverage: auto_move_users_to_base_plan, nurturing_warm_leads, '
           'save_binary_to_storage, send_updated_certificate_email',
           ('auto_move_users_to_base_plan', 'nurturing_warm_leads', 'save_binary_to_storage',
            'send_updated_certificate_email'),
           ('steady', 'queued'), (), None),
    Family('handback', 'S', 60,
           'Consumer hand-backs (envelope- and handler-level): an admitted supported input finishes on the '
           'effective runtime; an unsupported one gets its documented outcome, never the dormant queue',
           (), ('steady',), (), None),
    Family('certificate_emails', 'M', 180,
           'Jobs mapped by the Go scheduler: send_expiring_certificate_email, send_expired_certificate_email, '
           'send_reminders, send_user_partial_registration_email',
           ('send_expiring_certificate_email', 'send_expired_certificate_email', 'send_reminders',
            'send_user_partial_registration_email'),
           ('steady', 'queued', 'after_child_published'), (), None),
    Family('email', 'M', 180,
           'Broker consumers: core.tasks.send_mail_async, send_email_text, send_email_template, send_email_html',
           ('core.tasks.send_mail_async', 'send_email_text', 'send_email_template', 'send_email_html'),
           ('steady', 'queued', 'after_child_published', 'smtp_retry', 'crash_after_smtp_accept'), (), None),
    Family('collectors', 'M', 240,
           'Jobs mapped by the Go scheduler: auto_archive_subscription, clear_deleted_subscription, '
           'auto_delete_orphan_medical_certificate, delete_users_with_request',
           ('auto_archive_subscription', 'clear_deleted_subscription', 'auto_delete_orphan_medical_certificate',
            'delete_users_with_request'),
           ('steady', 'queued', 'batch_boundary'), ('storage',), None),
    Family('retention', 'M', 240,
           'Jobs mapped by the Go scheduler: cleanup_old_exports, delete_old_audit_logs, instance.restore.cleanup, '
           'instance.sso.tasks.maintain_bakney_pairings',
           ('cleanup_old_exports', 'delete_old_audit_logs', 'instance.restore.cleanup',
            'instance.sso.tasks.maintain_bakney_pairings'),
           ('steady', 'queued', 'rollback_mid_batch'), ('storage', 'bakney_peer'), None),
    Family('workflows', 'L', 300,
           'Broker consumers: check_workflows_trigger, check_workflows_trigger_cron, workflow_block_execute',
           ('check_workflows_trigger', 'check_workflows_trigger_cron', 'workflow_block_execute'),
           ('after_scheduling', 'before_execution', 'after_child_published'), ('storage',), None),
    Family('payments', 'L', 300,
           'Jobs mapped by the Go scheduler: association_quote_assign, renew_memberships_payments, '
           'change_type_of_stripe_payments, generate_coupon_if_not_exists, mark_as_paid_payments_checking_stripe',
           ('association_quote_assign', 'renew_memberships_payments', 'change_type_of_stripe_payments',
            'generate_coupon_if_not_exists', 'mark_as_paid_payments_checking_stripe'),
           ('steady', 'queued', 'renewal_lock_retry', 'rate_limit'), ('stripe',), None),
    Family('envelope_options', 'L', 300,
           'What to compare beyond task names (D06, D17): per-attempt/root/parent ids, priority, expiry, '
           'stale-restore stamps, rate limits, soft/hard timeouts at the switch boundary, delayed retries and '
           'callbacks after several round trips',
           (), ('retry_boundary', 'expiry_boundary', 'rate_limit_boundary', 'time_limit_boundary',
                'delayed_after_round_trips'), (), None),
    Family('storage', 'L', 300,
           'Broker consumers: save_to_storage, export_invoices_to_zip',
           ('save_to_storage', 'export_invoices_to_zip'),
           ('queued', 'partial_upload'), ('storage',), None),
    Family('print', 'XL', 420,
           'Broker consumers: print_document_compensation, print_document_customer_invoice, print_document_invoice, '
           'print_document_medical_appointment, print_document_subscription',
           ('print_document_compensation', 'print_document_customer_invoice', 'print_document_invoice',
            'print_document_medical_appointment', 'print_document_subscription'),
           ('queued', 'after_object_created'), ('storage', 'renderer', 'api'), None),
    Family('association_data', 'XL', 480,
           'Broker consumers: export_association_data, import_association_data, instance.restore.execute',
           ('export_association_data', 'import_association_data', 'instance.restore.execute'),
           ('queued', 'partial_work', 'progress_polling', 'restore_serialization'), ('storage', 'runner'), None),
    Family('backup_notify', 'M', 180,
           'Broker consumers: instance.backups.notify_failures (runner socket/token, deduplicated notices after a switch)',
           ('instance.backups.notify_failures',), ('steady', 'queued'), ('runner',), None),
    Family('scheduled_exceptions', '-', 0,
           'Three scheduled exceptions: backup notices, result expiry, WhatsApp delivery',
           ('celery.backend_cleanup', 'communications.tasks.deliver_whatsapp'), (), (),
           ('TESTS.handoff.test_p4_scheduling.ScheduledExceptions', 'TESTS.handoff.test_p4_outbox_realtime.WhatsAppOutbox')),
    Family('early_ack_fixture', '-', 0,
           'fixture.early_ack regression fixture', ('fixture.early_ack',), (), (),
           ('TESTS.handoff.test_p3_durable_jobs.RunningAttempts',)),
)

BY_ID = {family.id: family for family in FAMILIES}


def family_of(task):
    owners = [family.id for family in FAMILIES if task in family.tasks]
    return owners[0] if len(owners) == 1 else None
