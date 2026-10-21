# Final login user writes — component contract

`LoginFinalization.Finalize` runs for a trusted account ID after credential/OTP
admission and successful response construction. It is not wired into a public Go
login route. A future coordinator must withhold response tokens and cookies until
this call commits; earlier password, username and default-card configuration
transactions remain separate, matching the inspected login sequence.

The service updates `bakney_user.last_login` for every successful finalization.
A non-null `delete_on` is cleared if it is today or later. Django uses
`timezone.now().date()`, hence the UTC day, independently of the instance's display
timezone. There are two clock reads when `delete_on` is present: the first records
last login, the second decides the date. Crossing midnight between those reads
can leave the previous day's deletion scheduled. A null date needs only one read.
Past dates remain unchanged, including leap days; future dates through 9999-12-31
are tested. PostgreSQL stores the timestamp with its existing microsecond precision.

`last_login` and `password` are excluded by Django's User audit registration.
Clearing `delete_on` emits exactly one User action-1 event with string changes
`{"delete_on":["YYYY-MM-DD","None"]}`. The object representation uses the current
first/last names. The target user's own nondeleted association has index priority;
otherwise the trusted request actor's own association is used. Collaborators do
not inherit their connected owner's association for this resolver. Anonymous audit
metadata has no actor email. Event/index fields reuse the existing model-audit
SQL; no credentials are selected or rewritten by this stage.

A read-committed transaction locks the target user and updates only `last_login`
and `delete_on`. Cancellation, update failure, event/index failure and commit
failure roll back this invariant. Serialization/deadlock conflicts use the existing
repository classification and at most three full attempts. Unit tests exercise
retry success/exhaustion; this component does not claim an additional forced
PostgreSQL deadlock rehearsal. Finalization does not redo credential admission:
active/deleted state changes need coordinated end-to-end admission policy before
public login can be certified.

## Evidence

`TESTS/adapters/django/login_finalization.py` calls the actual password-login view
with production password hashing, JWT generation, response builder and audit
middleware on the disposable current-model database. Only the two business-clock
reads in the view are fixed. Seventeen explicitly expected cases cover null,
past/today/future dates, UTC midnight, prior last-login replacement, owner/actor
index precedence, deleted associations, collaborators and administrator login.
Go writes are then checked by Django for the same date/timestamp outcomes and
exact audit/index fields. Generated event/index IDs and event times are volatile;
they must retain valid types, UUID version and ordering. This does not normalize
last_login: that value must equal the explicit fixed clock exactly. Every managed
table is compared after restoring the declared fixture mutations.

Real PostgreSQL tests check two statements for no cancellation, four for an
unindexed cancellation and five with an association index. Thirty-two simultaneous
Go calls on a four-connection pool perform 67 statements and produce one
cancellation event/index; the remaining calls update last_login only. Tests also
cover actual permission and deferred actor/index FK failures, injected failures
after update/audit and before commit, cancellation, missing users and pool release.
A current password, first name and inactive flag survive finalization; the audit
representation uses the fresh name.

## Deliberate differences and open integration

The actual Django view is also exercised with denied database writes. Denied User
UPDATE retains its pre-save audit/index, while a denied index INSERT is swallowed
and leaves the updated user and audit. Go rolls back the whole invariant as
required by the migration transaction rule. These failure side effects differ;
they are not claimed as identical parity.

Django's final `user.save()` writes stale model fields: a password/name/active flag
changed after response construction is overwritten. The fixture explicitly proves
this behavior and its extra audit changes. Go preserves those current fields and
only writes the finalization columns. This is an intentional concurrency-safety
difference, not evidence that a login racing account revocation is fully handled.

Concurrent unmodified Django saves do not participate in the Go read/lock/diff
protocol; mixed-runtime cancellation/audit races remain open. Public HTTP
composition, response augmentation, session delivery on failure, social login,
rate limiting, global middleware and trusted request audit context also remain
unimplemented or unverified. No endpoint or audit signal is marked verified by
these component checks.
