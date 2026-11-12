# Periodic scheduler loop

`ScheduleLoop` serially invokes the existing bounded `SchedulePager`. It does not
select a different task set, seed Beat rows, change execution ownership, or
activate a scheduler in the installed Compose lifecycle. The supported publisher
remains the explicitly validated daily email reset task and its limited interval
or UTC daily cron definitions.

`NewScheduleLoop(ticker, ScheduleLoopOptions{PollInterval: ...})` accepts an
explicit interval from one millisecond through five seconds; zero chooses one
second. `Run(ctx, initialCursor)` returns the last page cursor and an error when
it stops. Nil contexts, nil or typed-nil tickers, malformed cursors and invalid
intervals are rejected. One loop instance admits only one concurrent `Run`.

Each completed page is followed by the configured positive delay. The loop
preserves `NextCursor` before inspecting a page error, including an ambiguous
publication result. It never resets the high-water mark to retry that source
immediately. Completion of the finite rotation allows a later authoritative
reread; durable occurrence receipts and source locking provide deduplication.
The cursor is memory-only. Restarting starts a fresh rotation and does not assert
that an earlier ambiguous operation failed.

Inactive ownership, busy or pending restores, page timeouts and transient SQL
failures defer at the same bounded pace. Invalid page configuration or candidate
shape, a missing required restore lock, and an unavailable restore volume stop
the loop. Terminal error text is fixed; error identity remains available to
internal callers. A command wraps the ticker to report sanitized deferred or
degraded states before the loop continues. Running the loop is not evidence of
a successful publication or active ownership. Empty and unsupported-only pages
can complete without exercising publication admission; they cannot justify an
authoritative heartbeat or a restore-safe status.

Raw evaluator `NextCheckAfter` values do not control the loop's sleep. This keeps
markerless edits discoverable even when an old definition reported a very large
delay. With a fixed high-water bound, new higher IDs wait for the next finite
rotation. Revisit latency still scales with page count, actual page work and
pacing; the five-second interval limit is not a per-source latency guarantee.

Cancellation stops future page intake and interrupts pacing. The active `Tick`
runs synchronously and must return after joining its transaction cleanup before
`Run` can return. Work deadlines are cancellation requests, not permission to
release a live admission lease. Cleanup may exceed the page deadline, and remote
completion still requires the separate server transaction fence and exclusive
restore drain described in `transaction-drain.md`. The loop adds no process-wide
ownership lock and makes no completed Django-to-Go handoff claim.

The service tests exercise constructor and cursor validation, explicit error
continuation, finite-cursor preservation, positive pacing despite extreme raw
delays, deferred ownership/maintenance, terminal redaction, cancellation during
pacing, cancellation joined through a deliberately blocked page cleanup, and
concurrent-run rejection. Native process, PostgreSQL, and packaged-image proofs
are separate requirements; the unit suite does not substitute for them.
