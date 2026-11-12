# Native scheduler process — restricted, inactive delivery

The native scheduler composes the existing bounded schedule pager and atomic
occurrence publisher. It only publishes the supported persisted daily email
reset definitions. It does not seed schedules, migrate the database, change
ownership, consume Celery messages, or start from ordinary Compose services.
Django remains the default scheduling and worker runtime.

This is an Assozeta command extension within the existing Go module. SQL stays
in repositories, timing and loop behavior stay in services, and the composition
root supplies configuration, dependencies and process cancellation explicitly.
The existing environment file and restore coordination volume are reused; no
second credentials file is introduced.

The command accepts `--env-file`, `--page-size`, `--poll-interval`,
`--tick-timeout`, `--source-timeout` and `--startup-timeout`. Startup checks are
read-only. They require the expected Go operational schema and the Beat columns
used by discovery/publication. Missing, empty or whitespace-only restore-lock
configuration fails closed. An existing exclusive maintenance lease defers work.

`scheduler process initialized` means startup checks passed. It does not mean Go
owns scheduling, every persisted schedule is supported, or a recent page made
progress. Inactive ownership, deferred maintenance and failed operations receive
fixed diagnostics. Task arguments, source names, SQL errors and credentials must
not appear in these messages.

Each loop iteration processes one bounded page, retaining its returned
`{AfterID, ThroughID}` cursor even when publication fails. The frozen high-water
mark gives a finite rotation despite new higher IDs. Definitions are reread at
publication; no change-marker cache hides markerless edits. Positive bounded
pacing prevents a tight loop and does not trust raw evaluator delays as sleep
instructions. A page bound does not guarantee every source is revisited within
five seconds at arbitrary schedule counts.

SIGTERM and SIGINT cancel the current operation and prevent another page. Process
shutdown joins the operation's transaction and local connection cleanup. Remote
COMMIT can still have an ambiguous outcome after disconnect: the shared database
transaction fence and restore's exclusive drain protect snapshot ordering; the
next process rereads durable cursor/receipt state. Neither a process exit nor a
file lease alone proves the remote transaction has ended.

## Executable proof

Run using the pinned production Python environment and test dependencies:

```sh
python TESTS/adapters/go/scheduler_process.py
```

The adapter builds race-instrumented scheduler, worker and migration binaries,
starts private PostgreSQL, and migrates actual `django_celery_beat` tables using
the pinned Django runtime. Its restore row and communication table are explicit
synthetic transaction/effect sentinels, not full business-schema parity.
Ownership changes are trusted fixture actions, never an operator handoff API.

The twelve native scenarios pass with race-instrumented binaries. Their named
assertions cover schema/coordination failures, default Django and
absent ownership, two scheduler processes publishing one occurrence, native
worker consumption, markerless edits, ownership loss, restore and file-lock
admission, TERM during admitted publication, and KILL during deferred COMMIT
followed by restart. Success is recorded only after private processes and
PostgreSQL are stopped. The retained report describes executed cases and source
hashes; an unexecuted or failed run is not acceptance evidence.

A packaged scheduler must run with the existing non-root/read-only settings and
an appropriate process health policy. The image's server HTTP `/readyz` check
cannot diagnose a standalone scheduler process. Delivery of the binary does not
enable a scheduler service or establish installed health monitoring.

The separate image adapter consumes an existing image without building or
retagging it:

```sh
python TESTS/adapters/go/scheduler_image.py --image IMAGE
```

It requires the production Python environment and Docker. It migrates actual
Beat tables in a private PostgreSQL container through an ephemeral loopback port;
the packaged scheduler uses the private Docker network. All four image scenarios
pass. Their assertions cover
image configuration, Django-default inactivity, a Linux exclusive lifecycle
lease, and one committed occurrence followed by clean TERM exit. Image success
must be established separately from the native result.

## Remaining acceptance boundaries

The effective Django configuration is not the list of 21 static declarations.
Fresh Beat also installs SSO maintenance and backend cleanup, and historical or
custom persisted schedules remain authoritative. See
[scheduler authority](scheduler-authority.md) for the supported evaluator subset.

Full Beat replacement, unsupported timing/routing options, remaining task bodies,
Celery queue disposition, Django producer quiescence, cross-runtime deduplication,
operator selection and interrupted handoff remain required. This process slice
establishes none of those activation guarantees and does not complete requirements
7, 12 or the fourteen-requirement migration.
