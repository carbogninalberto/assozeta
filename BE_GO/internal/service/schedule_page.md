# Bounded periodic discovery and publication pages

`SchedulePager.Tick` is an inert service call, not a scheduler process. It reads
one bounded page of existing Django Beat source IDs and discovery metadata, then
invokes the [exact-row publisher](occurrence.md) for recognized enabled sources.
It does not sleep, spawn goroutines, seed declarations, change ownership or cache
authoritative definitions. No installed service invokes it yet.

The explicit cursor contains `AfterID` and `ThroughID`. At the start of a rotation,
an indexed descending primary-key lookup captures the highest existing ID. Every
page uses `id > AfterID AND id <= ThroughID`, in ascending order, with a limit.
New higher IDs wait for the next rotation, so continuous inserts cannot prevent
returning to earlier sources. Deletes, edits and new lower IDs do not create a
database snapshot: each recognized source is reread under publication locks,
and rows already passed are reconsidered on a later rotation.

Discovery deliberately avoids filtering by task/enabled without a suitable
index. Such a filter could scan arbitrarily many nonmatching rows despite LIMIT.
Only ID, task and enabled metadata is loaded. The default page size is 16, with
a maximum of 64. A new rotation needs at most two discovery statements; later
pages need one. It uses no OFFSET, full-table count or change-marker reset.

The service validates cursor, configuration and the entire returned page before
publication. Disabled/unrelated rows advance the cursor without writes.
Unsupported, busy and deleted sources are explicit outcomes and allow later rows
to proceed. Ownership loss, restore admission, cancellation and unexpected SQL or
commit errors stop the page. The returned cursor still identifies the last
attempted row and retains its rotation limit; an ambiguous commit is never
retried immediately within that page. Only a fully consumed final range resets
the cursor. The caller must retain the returned cursor even on error.

Default cooperative deadlines are two seconds per tick and 500 milliseconds per
source, with a maximum tick budget of five seconds. Context-aware repositories
and publishers must return and finish their transaction cleanup before Tick
returns. Cancellation stops admission of subsequent work; required rollback and
driver cleanup may extend wall-clock return beyond that budget. No claim of
five-second per-source revisit latency follows: that depends
on schedule cardinality, contention and caller pacing. Raw `NextCheckAfter`
values remain timing observations, not instructions for unrestricted sleep.
Read-only metadata traversal is not an owner/restore heartbeat; publication
independently performs the required admission checks.

The PostgreSQL adapter uses the actual pinned Django Beat schema. Nine required
race groups cover invalid bounds, sparse mixed datasets of 100/1,000/10,000 rows,
finite rotation under continuous inserts, markerless edits, busy/unsupported/
deleted sources, ownership/restore refusal, query counts and SQL/cancellation
rollback. Plans are measured without disabling sequential scans: at 100 rows,
the 64-row limit case naturally scans 100 rows using two buffers and sorts;
the 1,000/10,000-row range cases use primary-key index scans with two to four
buffers. Highest-ID lookups use backward index-only scans with two to three
buffers. These fixture measurements do not establish throughput or physical
I/O bounds for arbitrary production data or index bloat.

Sixteen successful publications use 194 measured statements: two discovery
statements plus twelve per independently locked source transaction, including its
shared server-drain fence. The test requires this exact cost for successful publications. This is bounded by page
size and preserves independent publication rollback; it is not one transaction
over every schedule. Separate service tests cover deadlines, malformed candidate
pages, redacted errors and preservation of earlier outcomes after a later failure.

Run the required private fixture with production Python:

```sh
python TESTS/adapters/go/schedule_page.py
```

Evidence is under ignored `quality-reports/go-migration/schedule-page/`. A native
scheduler, readiness/health supervision, cardinality/backlog diagnostics, all
remaining task/schedule shapes and coordinated Celery handoff remain required.
