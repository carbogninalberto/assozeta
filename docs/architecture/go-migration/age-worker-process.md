# Native associate age worker

The existing worker registers `update_age_of_associates`, version 1, alongside
daily email balance reset. The command uses the existing environment, bounded
pool, task deadline, ownership fence and restore admission. This adds no
producer, scheduler definition, owner selector or installed service activation.
Django remains the default owner.

The supported envelope is global (no association), with an empty JSON object.
Unsupported versions, nonempty payloads and tenant scope cannot change business
rows. The constructor and service preserve the existing task semantics: UTC
age comparison uses 6575 elapsed days, not a calendar eighteenth birthday.
Eligible minors in disabled or deleted associations remain within the historical
task scope; deleted associates and missing birth dates are excluded.

A successful job commits promotions, exact audit/index records and completion
together. A replay of a completed job does not overwrite later operator
corrections. Historical handled business or audit failures roll back those
effects and complete with a NULL result and a fixed warning; they are not
silently converted into automatic business retries. Admission, ownership,
completion and commit failures retain the existing executor error policy.

Ownership is checked before business effects and again for completion. Restore
admission retains the shared file lease through transaction and local driver
cleanup. The shared PostgreSQL transaction fence also participates in the
exclusive restore drain. A local process exit alone does not prove a remote
transaction has ended. Cancellation prevents new work; cleanup can extend beyond
the admitted-work drain deadline.

## Native proof

Run with pinned production Python and the existing test dependencies:

```sh
python TESTS/adapters/go/age_worker_process.py
```

The adapter builds race-instrumented native worker and migration commands and
uses private PostgreSQL with actual current Django models created by `syncdb`.
It does not prove historical schema migration. All producers and ownership
changes belong to the disposable fixture. The separate associate-age oracle
compares the historical Django task implementation.

Nine required groups cover default Django and missing ownership, two workers
with one exact business effect, envelope denials, replay after operator
correction, maintenance and pending restore, SIGKILL reclaim, final-attempt
exhaustion, SIGTERM cancellation and cleanup, and handled audit-index failure.
The nine-row fixture checks three promotions, three exact audit records and two
direct association indexes, while preserving unrelated rows and a sentinel.
An old worker without the registration fails the completion case while leaving
the age job pending and business rows unchanged.

Reports under `quality-reports/go-migration/age-worker-process/` retain the
explicit scenarios, source hashes and cleanup outcome. Success is published
only after child processes and private PostgreSQL are stopped. A separate
packaged-image adapter must establish shipped-command behavior on a specified
immutable image; native execution does not establish image delivery.

The packaged adapter also passes all five cases on immutable image
`sha256:aabe018e2d87de7a5dd900114559f392869ada7e20e6357a0ff223ea2a6b572f`,
using the same actual current-model fixture. The existing seven-case worker
image regression passes on that identical image. These runs verify non-root,
read-only execution, actual Linux maintenance exclusion, default Django inactivity,
exact committed effects and handled-failure completion; all private containers
and networks are removed before success. Run:

```sh
python TESTS/adapters/go/age_worker_image.py --image IMAGE
```

Both image adapters observe executed claim SQL through private PostgreSQL statement
records. Sampling `pg_stat_activity` was unreliable because completed owned claim
transactions leave the connection idle at `COMMIT`; retained failure evidence shows
54 such samples while real claim statements executed. The observer correction keeps
the same deadlines, image and no-effect assertions. Evidence is under
`quality-reports/go-migration/age-worker-image/` in the isolated age worktree.
The image records a dirty `f97975a` snapshot plus the frozen feature/drain/scheduler
changes; it is not a clean aligned release image or installed ownership proof.
