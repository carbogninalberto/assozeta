# Message-read authorization acceptance

This suite adds 38 source-authored impersonation and nested-ownership cases to
the existing message list/detail contracts. It covers administrator-to-owner and
full-collaborator reads, separation from the original actor's association,
foreign owners, restricted collaborators and athletes, missing/expired/revoked
sessions, invalid actor/target/session bindings, recognized query scope checks,
ignored group headers, and foreign message/delivery references. Equal recipient
strings across associations cannot substitute for the parent message's ownership.

The portable specification is `TESTS/fixtures/message_read_scopes.json` and
`TESTS/contracts/test_message_read_scopes.py`. Backend preparation lives only in
`TESTS/adapters/django/message_read_scopes.py`; it substitutes disposable tokens
and session IDs, never observed response values. Session setup is synthetic and
does not constitute acceptance of the impersonation management endpoints.

Run with the production Python version and preprovisioned PostgreSQL, Redis, Go,
native image-codec dependencies and Docker:

```sh
python -m TESTS.adapters.django.message_read_scopes_runtime \
  --versioned --output quality-reports/go-migration/message-scopes-FRESH
```

The output directory must be new. The runner applies all 547 Django migrations,
executes the identical authored cases against Django, Go and both shipped Caddy
versions, and compares all 128 managed concrete/through models plus every DB1
key's type, serialized value and absolute expiry around each read. No read-side
audit changes, cache exclusions or session renewal are allowed. A deliberately
misrouted v2 request must be rejected using the actual Django ASGI observation.

The shared HTTP client returns original redirect responses without following
them, preventing a redirect from hiding an incorrect status or forwarding
credentials. Socket-free controls exercise urllib's real redirect handler chain
for 301/302/303/307/308, same/cross-origin destinations, GET/POST and raw/JSON
responses. The runner owns Go build flags and disables ambient workspace/config,
overlay and compiler overrides. Source hashes, freshly built executable hashes,
process exit, exact case sequence and all cleanup checks gate acceptance.

The parent-owned `message-scopes-live-r1` passed all 38 cases in each of four
workloads and its real misrouting negative control. All 1,448 source hashes
matched the candidate before and after execution. Database/cache state was
restored, services stopped, private files removed and Go exited normally.

- Receipt SHA-256: `9d3dee92108ba21fddc3a47e9d6690d5901540995fe5b6376f759d678b95d3f3`.
- Go executable SHA-256: `3269b276860fc5cc9841f1d1d86a1b41bdbe3dba06e99b2d47e4343b2cf81deb`.
- Image codec SHA-256: `afb52d9ea4d6fa7ece160a9432f4842678757ec36fe6bf0855950344caec72f9`.

## Warm-session revocation and expiry

`TESTS/fixtures/message_session_lifecycle.json` and its portable contract add two
ordered sequences to each runtime: an authorized list read followed by deletion
of the issued impersonation session, and an authorized detail read followed by
Redis expiry of that session. The identical credentials and request must then
receive 403 without restarting the server or reseeding between phases.

The runner retains actual Django thread/server and Go process handles. Versioned
workloads also bind the owned Caddy container identity, start time and restart
count. Each boundary checks all 128 models and the complete cache; only the
specific session key may disappear. Finally blocks restore its exact serialized
value and absolute expiry and verify the complete baseline. These direct Redis
transitions do not verify impersonation management endpoints.

The parent-owned `message-scopes-lifecycle-live-r1` passed on production Python
3.13.16: all 38 original cases plus both lifecycle sequences on Django-native,
Go-native, Caddy-v1 and Caddy-v2. All 1,452 source inputs matched before/after and
at parent review. The real misroute control, state/cache restoration, normal Go
exit and private/service cleanup passed. Its receipt SHA-256 is
`03e08fdf7653b50552acd69d62624217ba6ca121130f789b38c9e31ab56608fa`.
The executable and codec hashes match those above. The integrated offline matrix
passed 272 controls, including the existing browser suites in the same process.
Earlier receipts retain their original source revisions.

This suite makes no query-budget, performance, browser, package or full-family
claim. The migration ledger remains unchanged.

Independent review of this candidate found two harness gaps: restoring a known
session depended on a successful cache snapshot, and receipt checks did not
reject inconsistent cache digests across paired workloads/scenarios. The live
run above remains historical execution evidence; corrected-source acceptance
and publication are pending fixes and replay.

Both review findings are fixed: the known session is restored independently of
whole-cache observation, failed verification still rejects acceptance, and
paired scopes/lifecycle baselines and both removed-session digests must agree.
Negative controls reproduce and reject each contradiction and snapshot failure.
The final `message-scopes-lifecycle-live-r2` passed all four workloads on the
corrected source, with all 1,454 inputs matching at parent review, full cleanup,
and unchanged native executable hashes. Receipt SHA-256:
`1764141815a7a8d1ccd0a4d49d3c6e7b79b10a3e475ea19728470569f85123f1`.
The final combined offline matrix passed 308 controls on Python 3.13.16.
