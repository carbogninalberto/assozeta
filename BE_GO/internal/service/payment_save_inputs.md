# Existing-payment save preparation

`PaymentSavePreparation` supplies bounded, read-only plans for trusted background
work, initially the supported automatic subscription archive. It composes the
existing description calculator with one repository statement for up to 512
payment/association references. It does not persist a payment or implement the
archive task. The separate `subscription_auto_archive.md` service now uses it
inside its mutation transaction. HTTP actor/group admission remains the caller's
responsibility.

The raw batch limit is checked before duplicate collapse. Identical references
retain first occurrence order; conflicting associations for the same payment fail
before SQL. A zero UUID is a valid stored identity. Empty batches execute no SQL.
A missing or foreign payment, invalid selected relationship or failed description
calculation rejects the entire result without exposing partial plans.

The repository accepts an explicit DB or caller-owned transaction. A single SQL
snapshot includes existing archived/deleted payments, their current category,
invoice and account, and selected description relationships. This preserves the
background model's historical FK visibility. The caller must bind preparation to
its mutation transaction and appropriate concurrency controls before applying any
plan; a read snapshot is neither a durable authorization grant nor a write receipt.

Category lookup preserves case-insensitive name matching and lowest-primary-key
ordering across eligible global/own categories, including archived/deleted ones.
An existing foreign category fails; it is not silently replaced. When no eligible
default exists, the planned category remains NULL. The actual Django lookup picks
an earlier foreign category, even when deleted/archived. Excluding it is an explicit
scope correction required before Go writes, rather than equivalent legacy output.
Invoice and account references must also belong to the payment's association.

For a NULL course-payment description, the first course subscription, installment
or carnet subscription is selected by primary key in that priority order. Deleted
course/installment rows and disabled carnet rows remain visible. Selected course,
carnet and optional parent membership references must belong to the payment's
association. Tenant predicates apply in the SQL joins so foreign titles are never
projected. An invalid first relation fails rather than falling through to another.
Unused lower-priority relations are not read. Existing descriptions, including the
empty string, bypass reverse relationship lookup. A missing carnet can still use
its stored payment metadata override, matching the calculator's established rules.

Twelve authored description cases run through actual Django `Payment.save()` and
then through Go preparation. The adapter separately confirms the legacy foreign
default category and six reverse-reference scope defects. Eleven denial inputs
cover top-level ownership/missing rows, categories, invoices, accounts, course,
installment and carnet references, including parent memberships. Whole managed
model snapshots prove preparation is read-only and fixture cleanup restores the
preexisting private database state.

Eight required PostgreSQL groups run under race detection. They cover descriptions
and defaults, fail-closed batches, limits/deduplication/zero UUID, malformed metadata,
caller-transaction visibility, query and pool bounds, actual blocked-query
cancellation/recovery, and an executed plan. Reads use one statement for 1, 16, 128
and 512 payments with course relations; 32 concurrent reads release a four-connection
pool. The plan uses 128 references spread across 4,096 payments and course
subscriptions after statistics collection. It uses the existing payment FK index
for course-subscription lookups; the payment table itself is scanned sequentially.
This proves bounded round trips, not constant database work or a latency advantage.

Run the production Python environment with
`TESTS/adapters/django/accounts.py --only payment-save-inputs`. Full accounts also
includes the fixture. Evidence is under
`quality-reports/go-migration/payment-save-inputs/`, including the initial missing
implementation failure and the executed JSON plan. These are current-model schema
fixtures, not historical migration, backend-switch or complete save/archive proof.
Money fields are not changed. Full mutation locking, default-account creation,
financial invariants and general writes remain separate required work. The archive
service now supplies its specific locking, writes/audits/indexes and fenced completion.
