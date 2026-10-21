# Social credential selection and admission

`SocialLogin` implements the account-selection and credential-result rules in
Django's `oauth2_login`. It depends explicitly on a streaming candidate reader and
a `SocialTokenVerifier`; this increment does **not** implement Google/Apple network
clients or register the public Go login route.

Identifiers use the same Python-compatible text conversion, whitespace stripping,
email-shape decision and PostgreSQL case-insensitive lookup as password login.
Every candidate is inspected in UUID order. A unique casefold-equal username wins;
otherwise exactly one candidate is required. Counts saturate at two and only two
candidate records are retained. No account means not-found; ambiguity is rejected
before provider dispatch. Passwords are neither checked nor written and are
removed from the selected value.

The five existing aliases map to Google or Apple. Unsupported backend values use
the original Python string representation in the 400 response. Provider calls
receive the original token value unchanged. A failed verification is rejected
before checking email; a successful result requires a string email. Comparison
uses Unicode full lowercase, not casefold: sharp-s must not authorize a different
address, while dotted-I and final-sigma behavior matches Python. Account active/
deleted state is checked after provider and email results, as in Django.
The current view does not require `email_verified` or match a stored provider
subject; controlled fixtures explicitly retain that behavior. This is a record of
the existing contract, not new provider-token validation evidence.

One constructor-supplied admission bound covers candidate lookup and provider I/O.
The repository releases its database connection before provider verification; no
transaction or row lock spans a remote request. Context cancellation can stop a
queued or running request, and every exit releases its admission slot. Provider
clients must supply their own bounded requests, timeouts and response validation.
Only those concrete clients, wired at the composition root, can establish the
trusted verifier result in a deployable backend.

`SocialAdmission` then shares the existing username/audit preparation and OTP
boundary with password admission. Preparation reloads current eligibility and
commits username/audit normalization before OTP. OTP denial therefore retains
that committed invariant; it does not update last-login, issue tokens or create
a response. The trusted audit actor remains independent of the target account.
Password-session regression tests continue to exercise the shared helper.

The accounts adapter runs 41 declared cases through the actual Django login view,
audit middleware and OTP implementation, and then concrete Go repositories and
admission services on the same private PostgreSQL database. Google/Apple verifier
responses are controlled deliberately: these cases prove alias/selection/error
ordering, raw-token forwarding, email comparison and durable admission effects,
not remote cryptographic verification. Successful Django view calls also issue a
real JWT; their final last-login effect is reset separately because Go admission
stops before session completion. All managed model tables must match after the
declared resets. Audit fields/indexes, selected identities and provider call
counts are checked explicitly.

Small admission cases use exactly one, two, five or six business statements.
Read-only selection for 1/16/128/512 candidates uses one SELECT; 32 concurrent
selections use 32 SELECTs with a four-connection pool. A persisted inactive/active
transition is observed immediately. Cancellation, closed-database failure and
provider errors return no account. Unit checks separately verify provider I/O
starts after reader closure, two-slot concurrency, canceled waiters and slot reuse.
Handler tests cover social error status/body and the absence of session cookies.

Still required: concrete Google ID-token/access-token and Apple verification,
environment/configuration/provider wiring, complete public HTTP composition,
mid-request account/provider configuration changes, global rate/audit middleware,
image dispatch, browser workflows, versioned routing and release/switch evidence.


`LoginSessions.LoginRequest` now selects the password or social admission using
the transport DTO and sends the admitted account to shared session completion.
The password-only API uses the same completion helper. Both paths retain checks
for cancellation, inconsistent token/profile identity and finalization failure
before returning tokens. The separate session adapter contains 26 full-response
cases, including eight social cases with controlled provider results and real
PostgreSQL/JWT/audit/finalization behavior. This preserves the distinction between
verifying session orchestration and verifying provider credentials remotely.

The separate `SocialClientIDs` component now reads live instance overrides; see
`social_configuration.md`. It is not yet connected to a concrete verifier or the
composition root.
