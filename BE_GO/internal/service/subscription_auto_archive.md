# Automatic subscription archive task body

`SubscriptionAutoArchive.Run` is `auto_archive_subscription` as of v1.0.35. It is
registered in the native worker (version 1) and the scheduler maps the persisted
`auto-archive-subscription` row; Django remains the default owner. Behaviour,
schedule and evidence: `docs/architecture/go-migration/sync-v1035.md`.

Version 1 accepts exactly an empty JSON object and the instance association UUID.
Invalid envelopes and already-cancelled contexts stop before SQL. The service
freezes the instant, reads the candidates in one fenced transaction, then archives
each eligible subscription (from the balance year's `year_end + 1 day`) in its own
fenced READ COMMITTED transaction: `archived` on the subscription, its payment and
that payment's invoice, each with the auditlog diff and index Django's
`save(update_fields=['archived'])` writes. An invalid balance year or subscription
calendar, or a failing write, is logged and the run goes on. Lease loss,
cancellation and commit failures end the job; completion is a separate fenced
transaction with a NULL result.

The earlier body (one transaction, date and payment defaults, scoped references)
followed the task before v1.0.35 and is replaced. Payment description and date
defaults are no longer written by this task.

Run `TESTS/adapters/django/accounts.py --only subscription-auto-archive` (also part
of full accounts and `--only task-bodies`) and
`TESTS/adapters/go/nightly_subscriptions_worker_process.py` with the production
Python environment.
