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

This matrix tests expired/revoked sessions before their first request. A
successful read followed by revocation/expiry in the same server process remains
a separate acceptance gap. This suite makes no query-budget, performance,
browser, package or full-family claim. The migration ledger remains unchanged.
