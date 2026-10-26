# Automatic subscription archive task body

`SubscriptionAutoArchive.Run` implements the database effects of
`auto_archive_subscription` with explicit constructor dependencies, trusted
instance-wide job identity and one transaction. It is not registered in a
production worker or scheduler. Django retains operational ownership. Manual
subscription archive/delete workflows remain separate supported operations.

Version 1 accepts exactly an empty JSON object and the instance association UUID.
Invalid envelopes and already-cancelled contexts stop before SQL. The service
freezes a UTC execution instant, scans nondeleted/unarchived subscriptions whose
association owner enables `auto_archive`, and preserves access across groups,
drafts, inactive owners and deleted associations. Fiscal eligibility uses the
creation timestamp and owner's balance-sheet start day/month. It preserves the
literal strict `now > end-at-UTC-midnight` test: equality does not archive, one
microsecond later does. This is not an inclusive whole-last-day interpretation.
Custom subscription end dates affect save defaults, not fiscal eligibility.

Eligible subscriptions receive the already-characterized `Subscription.save`
date defaults, retaining existing dates and unhandled duration modes. Their
existing payments receive the scoped default category/description preparation
and become archived; linked invoices become archived. Shared payment/invoice
identities are deduplicated per page; subsequent pages only audit actual changes.
Optional absent payments/invoices are skipped, supporting independent subscriptions.
Money, payment status, file links and unrelated fields are never rewritten.

The original task cannot reach these effects because its naive clock cannot be
compared with the aware fiscal end, then its diagnostic accesses nonexistent
`subscription.id`. The oracle patches only those two defects to run the real
model saves/signals. Additional intentional corrections are explicit:

- A NULL payment currently leaves a saved subscription; a NULL invoice also leaves
  the payment saved. Both failures abort subsequent work.
  Go archives all eligible existing parts and continues past absent optional links.
- The legacy default-category/reverse-title lookup can cross association ownership.
  Go rejects foreign selected references, using the separate preparation contract.
- Saving an already archived, soft-deleted payment or invoice generates a false
  full-field UPDATE audit with previous values `None`. The default manager cannot
  find the old row. The association resolver accepts both auditlog's in-memory
  tuple changes and persisted JSON lists, so each false event must have exactly
  one index linked to that event and its stored association, with resolution path
  `changes_json.sport_association` and the frozen execution timestamp. Persisted
  changes must independently resolve to the same association and fallback path.
  The oracle counts these indexes alongside the normal direct indexes. Go uses
  actual stored values and logs only real deltas, with direct association indexes
  even for changed historical linked rows. Correctly indexing the Django false
  events does not correct their audit contents or make them expected Go events.
- Django can commit earlier saves/audits before a later failure. Go commits all
  subscription/payment/invoice fields, audit/index rows and fenced job completion
  together, or rolls them all back, including preceding pages.

The repository uses READ COMMITTED so executor heartbeat updates do not invalidate
completion. Pages contain at most 100 subscriptions in UUID order, including UUID
zero. Subscription rows are locked for update and association/owner settings for
share. Selected payments are locked in UUID order before description preparation.
Invoice writes lock and revalidate current association ownership. SQL applies
association predicates to each linked mutation and updates only archive/default
fields. Related title/default selection is the preparation statement's snapshot;
it does not lock all descriptive source rows or prevent concurrent insertion of
new category/relationship candidates.

Paging bounds application memory and query round trips, not the total transaction
size or number of locks. Locks persist until the complete job transaction ends;
future production registration must supply an appropriate deadline and lease policy.
Under READ COMMITTED a later page can observe newly committed rows after the cursor;
rows inserted behind it await a later occurrence. There is no SKIP LOCKED omission.
Two overlapping archive jobs serialize conflicting rows and do not duplicate
successful field-change audits. This does not establish all mixed-runtime business
workflow races or production scheduler ownership.

Business/calendar/scope/SQL errors follow the task's handled `None` outcome only
after a successful rollback, with a fixed diagnostic code and separately fenced
NULL completion. Cancellation propagates without completing recoverable work.
Stale completion rolls back all effects. Begin, rollback, commit and completion
failures propagate; ambiguous commit failures are not claimed as successful work.
No new automatic business-error retry policy is introduced.

The actual task exposes `acks_late=false`, `max_retries=3`,
`default_retry_delay=180`, no rate/time/soft-time limit. Its static declaration runs
at 01:00 on days 1–4 of January, June and September. The adapter records initialized
and persisted schedules separately; reconciling their known discrepancy, retry
publication and single-owner production scheduling is still required.

The production-Python adapter requires thirteen PostgreSQL groups under the race
detector. Ten authored model scenarios check exact whole-row readback, fourteen
normal audit/index events, historical false-audit characterization, shared links,
leap/custom/retained dates, inactive/deleted owners and associations, and disabled
auto-archive scopes. The Django run additionally requires two historical false
events and their two fallback indexes, for sixteen total indexes; the Go run
requires only the fourteen real events and direct indexes. Django can resave
Go-written rows; the fixture checks the same exact fallback indexes for the two
historical false events before deleting those events and their cascading indexes
and requiring the complete snapshot to be unchanged. Additional groups cover
optional links, invalid envelopes/calendars, strict fiscal boundaries, foreign
ownership, each write/audit/index failure,
stale completion, cancellation/pool recovery, overlapping jobs, real executor
heartbeat, failure on page two, and truthful historical linked audits.

Query tracing requires `2*ceil(n/100)+5` statements for independent subscriptions
and `6*ceil(n/100)+5` for distinct linked subscription/payment/invoice chains,
including BEGIN/shared transaction fence/final empty read/completion/COMMIT, across increasing sizes through
512. An executed 4,096-row page plan returns 100 rows using existing subscription,
association and user indexes. These fixtures claim no general latency improvement.

Run `TESTS/adapters/django/accounts.py --only subscription-auto-archive` with the
production Python environment. Full accounts and `--only task-bodies` include it.
Evidence is under `quality-reports/go-migration/subscription-auto-archive/`.
Offline negative controls run with
`python -m unittest TESTS.test_subscription_auto_archive_index -v`. They exercise
the adapter's index assertions with synthetic rows, rejecting missing, duplicate,
mislinked, foreign, mistimed or incorrectly resolved indexes and incorrect total
counts. They do not run production signals, model saves, PostgreSQL or Go and do
not establish runtime acceptance.
These are current-model fixtures; historical migrations, restored archives,
production registration/scheduling and backend activation remain separate gates.
