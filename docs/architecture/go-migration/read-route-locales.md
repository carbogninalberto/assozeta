# Read-route language compatibility

This slice covers language selection and six framework messages for the public
manifest and collaborator list. It does not establish complete route parity.
The supported Django language inventory contains 99 codes. Static portable
expectations cover credentials, permission, content negotiation, format lookup,
method denial and unsupported media, plus cookie/header precedence and malformed
language ranges.

The portable fixture does not import either implementation. Its expected
translations are authored from the pinned Django/DRF catalogs, CPython gettext
alias expansion and per-message fallback. The source extraction recipe and BSD
notices live in `BE_GO/THIRD_PARTY_NOTICES.md`. Acceptance uses these static
expectations against actual HTTP responses, not one backend's observed output
as the expected response for the other.

## Independent acceptance

Run with the interpreter pinned by `BE/Dockerfile` and disposable PostgreSQL,
Redis and Docker available:

```sh
python -m unittest TESTS.contracts.test_read_route_locales -v
TESTS_READ_ROUTE_LOCALES_GO=1 TESTS_READ_ROUTE_LOCALES_VERSIONED=1 \
  python TESTS/adapters/django/accounts.py --only read-route-locales
```

The adapter starts actual Django, builds native Go, and uses the shipped Caddy
configuration for `/api/v1` and `/api/v2`. Each versioned request has independent
Django upstream attribution. A deliberately misrouted v2 request must be rejected
even when its response matches the contract. The fixture checks all 128 managed
models including through tables, raw cache values, and all-user-table PostgreSQL
tuple counters. Counter evidence includes reverted tuple writes, but does not
claim to detect every attempted SQL statement. Owned fixtures and processes must
be cleaned up before acceptance.

Receipts under `quality-reports/go-migration/read-route-locales/` bind the oracle,
Go source, native binary, pinned interpreter, gettext/locale source and compiled
Django/DRF catalogs. An earlier result is not evidence for a subsequently changed
source or executable.

The first actual Django run exposed two fixture-authoring errors: the purported
long header was only 415 characters, and Russian messages ignored the preferred
`ru_RU` catalog. Both were corrected from parser/catalog source. The corrected
baseline passes all 840 cases; the published Go build fails 800 of those same
cases. Those source-bound failures remain retained as implementation red evidence.
A separate compiled handler red demonstrated that the manifest response-boundary
middleware overwrote a broader selected language with its older selector. The
manifest branch now uses the same selector as its handler, including short-circuit
responses. Other endpoint branches retain their separately characterized behavior.

The integrated native/versioned run passes **848 cases on each of Django, Go,
Caddy v1 and Caddy v2**: 119 selection cases on both routes plus 610 message cases.
The additional selection edges exercise literal Latin-1 NBSP/NEL header bytes
and quoted octal cookies. All 128 models and cache values remain unchanged, all
user-table tuple-write deltas are zero, and the equivalent-response fallback is
rejected. Source stability covers 1,124 oracle/build files and 1,190 compiled
runtime catalogs; receipts also record the exact native binary and both actual
Caddy container/image identities. This proof was run at rebased `f9a71e3` plus
the recorded uncommitted file hashes, not a claim about a different image.

## Remaining boundaries

Invalid/expired JWTs, custom identity denials, malformed scoped request bodies,
invalid resource identifiers, HTML 400/500 paths and other routes remain separate
contracts. This slice does not establish historical-data behavior, packaged or
installed acceptance, operator activation, complete browser workflows or full
performance requirements. No route status is promoted from this subset.
