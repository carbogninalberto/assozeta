# Public PWA manifest — implemented, acceptance incomplete

`GET`, `HEAD` and `OPTIONS /instance/manifest.json` now have explicit Go wiring.
The route reads existing instance branding without authentication, matching
Django's `InstanceManifestView`. The ledger is `implementing`; this does not
complete instance administration, logo serving, installed activation or PWA
browser acceptance.

## Contract and implementation

A dedicated repository selects only `name`, `abbreviation`, `meta_description`,
`logo_path` and `primary_color`, ordered by the existing primary key with LIMIT 1.
It does not load provider settings or credentials. The service owns defaults,
12-Unicode-code-point abbreviation fallback and icon presentation; the handler
owns transport and passes the original request context. Constructors are explicit.
Database errors remain errors, not unconfigured-instance defaults.

The unconfigured manifest retains Django's Bakney Sport/Bakney names, default
Assozeta SVG asset, Italian description and primary color. A configured row uses
its actual name/color, preserves nonempty abbreviation/description and emits two
icons at 192x192 and 512x512. Suffix matching is case-sensitive and operates on the
stored string: `.svg`, `.jpg`/`.jpeg` and `.webp` select those types; uppercase or
query-suffixed values retain image/png. Stored icon URLs and the root start URL
remain unchanged. Logo serving and alias/backend selection still need their own
acceptance evidence; this endpoint does not rewrite stored links.

Like the Django class, the endpoint disables Bearer authentication. Invalid or
expired tokens and forged impersonation/group headers do not select branding or
block the public read. Global session handling still expires stale cookies.
Format filtering precedes Accept negotiation, including blank and duplicate-last
blank format values. Unsupported methods, metadata OPTIONS, HEAD, translated
errors, CORS preflight and restore-lock rejection retain their measured behavior.

## Executed HTTP and PostgreSQL evidence

The authored bootstrap fixture adds **42 manifest cases** to the existing 86.
Actual Django and Go each pass all **128 cases**, with exact selected responses,
headers, database/cache preservation and secret-canary checks. Successful no-cookie
manifest GET/HEAD requests each execute one SELECT. The first Go run reaches the
new path and fails with actual HTTP404 against the authored HTTP200 expectation.
The initial Django fixture had an extra period in an Italian405 expectation;
that expectation was corrected to the pinned translation before implementation
and is retained separately from the Go missing-route evidence.

Actual PostgreSQL checks read the manifest in a database-enforced read-only
transaction at 100, 1,000 and 10,000 configuration rows. Each read uses one query,
returns the first primary-key row and retains bounded EXPLAIN row/buffer behavior;
large fixtures must use indexes rather than sequentially scanning configuration
rows. A separate test observes the actual manifest SELECT blocked by an exclusive
table lock, cancels it, then proves reader backend/transaction/pool release while
the blocker remains held. Recovery succeeds after release. No whole-application
performance improvement is inferred from these component checks.

The adapter verifies both required PostgreSQL test names and all four original
bounds subcases; a successful zero-test run cannot pass. Native evidence is under
ignored `quality-reports/go-migration/instance-manifest-parent/`, including
`native-acceptance.json`, Django and Go final receipts, missing-route red output
and test logs. The ordinary bootstrap outputs also retain the actual plan JSON.
The same 42 authored manifest cases also pass through shipped Caddy v1/v2.
An independent per-request Django observer sees every v1 request and no v2
requests. Deliberately routing v2 to Django produces a valid HTTP200 response but
is rejected as `upstream_mismatch`. Query, selected state/cache and header checks
remain enforced. Both required PostgreSQL tests and all four bounds subcases pass
in this run, and owned proxies/services are removed.

Compression and all Vary header lines are checked explicitly. The first Go
transport run failed the missing Accept-Encoding variation token. The route now
reuses the tested Django response-encoding middleware, including randomized gzip
padding, real decoding, HEAD handling and the characterized lowercase `gzip;q=0`
behavior. An authored proxy-specific expectation accounts for shipped Caddy
compressing a large response for uppercase `GZIP`, while native Django/Go retain
their case-sensitive rule. That expectation applies equally to v1 and v2; the
initial proxy-expectation failure is preserved. Header token order/case and split
Vary lines are compared semantically; decoded payloads remain exact and no error
body is truncated before secret checks.

Final parent evidence is `instance-manifest-parent/final-acceptance.json` under
the ignored evidence root. PWA browser and installed acceptance remain separate;
no ledger entry is promoted to verified.

## Packaged manifest HTTP evidence

`TESTS/adapters/go/instance_manifest_image.py` independently runs the same **42
authored cases on each of four workloads**: Django, the immutable packaged Go
server and shipped Caddy v1/v2. The parent run uses image
`sha256:ca4cc6971d5ffb4177180babd8a32b115354ffb91bb09e7d2b3410adc9d9365b`,
Linux/arm64, revision `324d3741d3907b9ff1bf45d78325528870763080`. It verifies UID
10001, a read-only root, packaged executable digest/revision/version, and actual
startup readiness/health. No executable is mounted into the image. Both complete
parent runs passed; the final source-bound run took 103.58 seconds.

Every request checks all 128 managed models, including automatically created
join tables, plus exact session-cache behavior and the existing secret canaries.
Real PostgreSQL statement logs between unique query delimiters supply query and
write counts. Separate controls capture one SELECT and an INSERT whose transaction
is rolled back and leaves zero rows. The image's periodic healthcheck is disabled
only within this fixture to exclude unrelated SELECTs; explicit startup probes
still run. Raw SQL and private fixture environment files are not exported.

Independent Django request totals are **42/0/42/0** for Django/image/v1/v2. A
deliberately misrouted v2 request returns actual HTTP200 but fails the upstream
observer. The older `c742cd2` image, which lacks this route, fails with actual
HTTP404 against the authored HTTP200 expectation after Django passes; its owned
resources and managed rows are also cleaned. Successful requests retain the
one-SELECT expectations, and all gzip, Vary, session, negotiation and restore
cases remain mandatory. Restore admission holds real host and container locks
separately, including a container lock-contender check; this does **not** prove
cross-kernel lock sharing.

Final and negative receipts are under ignored
`instance-manifest-image-parent/{final,missing-route-red}/results.json`. Their
source hashes, managed-row restoration, ASGI shutdown and proxy/container/network
cleanup pass. CI now requires this runner on its freshly built image and exports
only `instance-manifest-image/results.json`. These are current-schema packaged
HTTP checks: native PostgreSQL bounds/cancellation remain mandatory separately,
and PWA browser, historical data and installed switching remain open.

The bootstrap validator now rejects JSON boolean/number aliases and non-integer
write counters. Pure regressions reproduce the former false positives before
the fix; the exact published153 fixture and its hosted observations pass the
stricter comparisons. A fresh parent run with the stricter validator also passes
all 42 cases in each of the four packaged workloads, with stable source hashes
and complete cleanup (`instance-manifest-image-parent/strict-json/results.json`).
The validator supports an explicit encoding choice for short buffered responses
whose randomized gzip padding may remove the size benefit; any compressed result
must really decode and be smaller. Existing manifest encoding expectations remain
unchanged.

```sh
LC_ALL=C LANG=C python TESTS/adapters/django/instance_bootstrap.py --backend django
LC_ALL=C LANG=C python TESTS/adapters/django/instance_bootstrap.py --backend go --manifest-versioned
python TESTS/adapters/go/instance_manifest_image.py --image IMAGE --expected-revision FULL_GIT_REVISION
cd BE_GO
go vet ./...
go vet -tags=instancebootstrapintegration ./...
go test -race ./...
```
