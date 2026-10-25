# Historical import and read-route compatibility

The native acceptance slice imports the fixed historical archive through the supported
Django path, then reads the resulting data through native Django, native Go,
and the shipped Caddy `/api/v1` and `/api/v2` routes. Both backend orders run
in separate matrix jobs, each with its own disposable PostgreSQL cluster,
Redis database, Go process, Django process, Caddy container, and source
integrity check. The jobs do not share a database or import.

The reusable workflow is
[`.github/workflows/go-historical-read.yml`](../../../.github/workflows/go-historical-read.yml).
It does not disable Django migrations, use a current-schema fixture, or
substitute a fake import for the real worker path.

## Fixture provenance

The immutable base archive is `TESTS/fixtures/bakney-v1.json`: 15,420 bytes,
SHA-256 `0d0f5d58946de1306d93476261a6dc6fe09cb56c3b5e23b4d3dcd2c4e75f6713`,
with 67 historical model sections plus the manifest. Its historical source
revision is `0058de5909734bdd0f00867abbdb723649de4238` from
`carbogninalberto/django-bakney-sport`. Seven pinned source-file hashes bind the
fields and export/import behavior reviewed for this fixture.

`TESTS/fixtures/bakney-v1-read-routes.json` is a synthetic, append-only test
overlay, not production or customer data. It adds three users and three
collaboration invitations to the frozen archive. Its overlay digest is
`2bd99dcf584bfdc851bb2523861b7db5dac3acd082fb22efb73586659232ac68`;
the deterministically derived archive digest is
`c7f889d6975782da7b34a27194068202de69e47af4539f3e005f8730cff16c66`.
The payment, subscription, associate, and document IDs remain the IDs present
in the base archive. The three collaboration-invite IDs are new synthetic
overlay IDs; the base archive contains no invitation rows.

The overlay deliberately distinguishes an unknown user ID from foreign
ownership. A detail request for the unknown UUID must return the same empty
result as a deleted collaborator. That proves unknown-ID handling only; it is
not evidence that an unrelated persisted foreign-owner record is excluded.

The base archive contains an unconfigured instance and one archived text
attachment. The derived archive keeps one `Document` row and requires its
file bytes to match the archived bytes. The `Signature` section is intentionally
empty, while the historical `Subscription.signature_url` retains its original
`https://legacy.example.test/signature.png` URL. The importer must preserve
that URL and must not invent a signature row.

Only the retired software-plan entitlement sections are intentionally skipped:
one `BillingPlan` record and one `PreviewAndCustomFeatures` record. No other
non-empty section may be silently skipped.

## Acceptance sequence

Before runtime, the workflow runs the current portable controls directly:
the entire `TESTS.test_historical_read_adapter` module and
`TESTS.contracts.test_historical_read_routes.HistoricalReadFixtureControls`.
The command does not hardcode a test count. Cache, population and proxy cleanup
controls are required when their helpers are integrated; runtime success alone
does not establish that these independent negative controls reject bad results.

Each matrix job then runs:

```text
python TESTS/adapters/django/historical_read_routes.py \
  --first-backend <django|go> \
  --versioned \
  --output quality-reports/go-migration/historical-read-<django|go>-first
```

The adapter must perform the following checks:

1. Apply the actual current Django migration chain to an empty disposable
   PostgreSQL 16 database. The established native receipt records 547 applied
   migrations and zero remaining; this is not a `--run-syncdb` or current-model
   shortcut.
2. Start the real import worker, upload the derived archive, wait for actual
   `SUCCESS`, require no warnings or deferred/M2M failures, and configure the
   imported association.
3. Check every imported historical primary key in each non-retired, non-empty
   manifest section. The established native receipt checks 100 archived field
   values that ordinary import semantics preserve, one document byte stream,
   the retained legacy signature URL, and the empty `Signature` table. These
   are targeted historical fields; the fixture does not claim that all 128
   current models came from the archive.
4. Run the four portable HTTP read contracts through the first selected
   backend, then the second selected backend, against the same imported database.
   Each backend must perform seven read requests. Those requests must return
   the authored manifest, owner, collaborator, inactive, deleted, unknown-ID,
   and anonymous-denial results.
5. Snapshot all 128 managed models, including audit and audit-index tables,
   and every raw Redis DB1 key with both bytes and expiry. Reads may not mutate
   either. An unexpected readiness key, persistent readiness entry, or expiry
   drift must fail; the login projection admits only its exact documented
   effects.
6. On first login, independently verify that the short historical salt is
   replaced with a fresh adequate salt and that the `pbkdf2_sha256` digest with
   1,000,000 iterations verifies the supplied historical password. The next
   backend login must preserve the already-upgraded hash. The exact audit rows,
   audit indexes, ownership resolution, timestamps, correlation IDs, user
   normalization, and lazy membership-card initialization are checked.
7. Require real Caddy routing for both API versions. `/api/v1` must reach
   Django and `/api/v2` must reach Go, with four HTTP tests and seven reads per
   workload. The proxy must use the Caddy image pinned by `UI/Dockerfile`.
   A deliberate v2-to-Django equivalent response must still be rejected by
   upstream attribution.
8. Verify the build inputs did not change during acceptance, then stop every
   process and container and remove the owned database directory. A successful
   receipt is not valid unless cleanup is explicitly complete.

`--versioned` remains mandatory. It does not replace the two native workloads;
it runs after them and exercises the shipped proxy routes.

## Bounded packaged Go runner

`TESTS/adapters/go/historical_read_image.py` supplies an immutable non-root Go
production image to the same `run_runtime` oracle. It uses the established
`payment_category_image.ImageFixture` for private PostgreSQL/Redis containers,
loopback fixture ports, an owned network, and label-checked teardown. It does
**not** call the current-schema initializer: the historical oracle applies the
full Django migration chain to the empty private database, performs the real
worker import and configuration, and requires exact population verification
before either backend workload. Population/cache/login/audit and full per-request
ASGI attribution assertions remain mandatory, including the real equivalent
Caddy v2-to-Django misroute rejection. Image health checks remain enabled and
readiness-prefixed Redis keys remain inside the exact cache comparison.

Each invocation requires `--image sha256:<64 lowercase hex digits>`, a full
40-character `--expected-revision`, `--build-receipt`, and an explicit
`--first-backend`. Both actual Caddy API versions always run after the two
direct transports. For this runner, the historical `go-native` workload label
means direct HTTP to the supplied packaged server; its metadata explicitly says
`immutable-production-image`. It does not mean a locally built Go executable.

The parent's build receipt must be a JSON object containing `success: true`,
`source_stable: true`, `image_id`, `source_revision`, `server_sha256`, and
`source_sha256`. The manifest must exactly equal
`historical_read_image.go_source_hashes(frozen_root)`: every file under `BE_GO`,
including the Dockerfile, module files, notices, manual assets, and embedded
inputs. Frozen contexts beneath `quality-reports` are supported. The executing
oracle's Go context must equal that frozen context. The receipt's binary digest
must equal `/app/server` in the running container, and `/app/CODE_REVISION` must
equal the full expected revision. Tags, short revisions, incomplete manifests,
and mismatching source/binaries fail before acceptance.

The runner binds the Django sources, templates/translations, relevant fixture
bytes, all Python test helpers, Caddy inputs, and the entire Go build context
before/after execution. It verifies the actual image ID, UID 10001, production
entrypoint, read-only root, restricted capabilities and mounts, exact packaged
framework notice bytes, and packaged version/health agreement. The server is
rechecked before workloads and on exit. Server removal and whole-fixture removal
have separate positive evidence; failed cleanup prevents a successful receipt.

The optional interface preserves every positional parameter and
`versioned=False` in `historical_read_routes.run_runtime`:

```python
run_runtime(work, redis_port, log, report, tool_env, archive, contract,
            first_backend, versioned=True,
            go_server_factory=factory, database=private_database,
            go_login_clock=linux_clock)
```

All three new parameters are keyword-only and default to `None`. `database` has
`NAME`, `HOST`, `PORT`, `USER`, and `PASSWORD`; it changes the connection only.
The context-manager factory takes
`(work, log, tool_env, redis_address, *, versioned=False)` and yields:

- Existing `base_url`, `proxy_upstream`, and optional `network` fields.
- `metadata`, a `MappingProxyType` with immutable JSON-compatible values.
- `verify_unchanged()`, which raises on runtime identity or executable drift.
- `cleanup={'verified': False}`, set to `True` only after verified owned teardown.
- Optional immutable `request_headers` for direct Go requests. The image runner
  authors `X-Forwarded-For: 127.0.0.1` to preserve the existing exact login audit
  expectation. Caddy request headers are unchanged.

The default factory retains the native process observer, PID, owned executable
and codec hashes, process liveness, and verified process stop. The wrapper
checks metadata stability and integrity on entry/exit, including workload errors.

`go_login_clock` uses the established `payment_category_shutdown.AuditClock`
interface. Packaged Go login bounds come from actual PostgreSQL
`SELECT clock_timestamp()` samples, after verifying that PostgreSQL and Go share
the Docker Linux kernel boot ID. Both Go logins retain their before/after
samples and exact timestamp inequalities. Django login, migration, import, and
configuration bounds retain host UTC. These clock domains are not combined and
no broad time tolerance is introduced.

Parent runtime examples, from the integrated checkout after building and
recording the corrected immutable image (substitute its exact ID and revision):

```sh
/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python \
  TESTS/adapters/go/historical_read_image.py \
  --image sha256:<exact-image-id> --expected-revision <full-source-revision> \
  --build-receipt /absolute/path/build-receipt.json \
  --go-source /absolute/path/frozen-root --first-backend django \
  --output quality-reports/go-migration/historical-image-django-first

/Users/alberto/Desktop/Bakney/Workdir/assozeta-go/quality-reports/go-migration/python31316/bin/python \
  TESTS/adapters/go/historical_read_image.py \
  --image sha256:<same-exact-image-id> --expected-revision <same-full-source-revision> \
  --build-receipt /absolute/path/build-receipt.json \
  --go-source /absolute/path/frozen-root --first-backend go \
  --output quality-reports/go-migration/historical-image-go-first
```

Use separate processes and fresh outputs. Each receipt explicitly keeps
`both_orders_verified: false`. The parent can call `assert_both_orders` on both
receipts to require separate fixture identities, opposite first backends, and
matching image/binary/source/build-receipt bindings. Parent inspection and real
runtime tests are required before acceptance. Offline controls cannot substitute
for either order. The native matrix and offline image controls run in CI; packaged runtime receipts below are parent-run evidence.

## CI integration

The parent quality workflow calls the reusable job:

```yaml
  historical-read:
    name: Historical import and read compatibility
    uses: ./.github/workflows/go-historical-read.yml
```

The reusable workflow installs the pinned Go module version, reads the
production Python version from `BE/Dockerfile`, installs
`TESTS/adapters/django/requirements-credentials.txt`, PostgreSQL 16, Redis,
the required image/build libraries, the locale used by the existing CI, and
the pinned Caddy image. The 20-minute job timeout covers runner setup, Go build,
migration/import work, and the two transport variants while retaining the
dependency and process deadlines in the adapter.

Each artifact contains exactly one receipt:

```text
go-historical-read-django-first/results.json
go-historical-read-go-first/results.json
```

Service logs and private diagnostics remain outside artifacts.

## Current bounded evidence and remaining gates

Parent receipts `historical-native-django-strictpath-r10` and
`historical-native-go-strictpath-r10` pass both fresh first-backend orders with
production Python 3.13.16, 547 migrations and zero remaining migrations. Each
checks all 128 managed-model populations, 13 imported records, 14 exact audit
rows, five indexes, 28 reads and four logins across native Django/Go and both
Caddy API versions. Go-first exercises initial password normalization and lazy
card initialization. Source stability and every owned cleanup check pass.

The independent population review demonstrated that an arbitrary document-path
prefix plus a matching audit update could escape the earlier gate. The corrected
oracle requires the exact empty-prefix layout, authored filename and document
ID, and a canonical UTC timestamp within the actual import window at the
importer's whole-second precision. Both reported false passes fail the new
controls. The 182 combined historical/image/message controls pass; earlier
receipts are not relabeled as evidence for this correction.

The fresh packaged receipts `historical-image-django-strictpath-r2` and
`historical-image-go-strictpath-r2` also pass. Both use immutable image
`sha256:ce2d2b66271fb6d1decd7d8b138fd7fbcf5c7fec169f7e4e9bd5bc2b2146b29f`,
built from base `757bb46ae294a9aeecc11c802aeae8dac82fbafc` plus the explicitly
recorded unpublished message-read source overlay. Full build-context hashes,
server digest, oracle source hashes and the build receipt identify the actual
artifact; the base revision alone does not. The two-order parent check requires
identical immutable build inputs and two different disposable fixture IDs.
Normal image health checks stay enabled. Exact cache types/bytes/absolute
expirations, Linux clock bounds, non-root/read-only execution, notices,
equivalent-response misroute rejection and label-checked cleanup pass.

The new reusable native workflow has not yet produced hosted evidence for this
commit. Packaged Django import, installed upgrades, coordinated selection,
interrupted restore, rollback, representative performance and the remaining
historical fixtures remain open. Import is still Django-only
(`go_import_supported: false`). This bounded slice does not prove whole
read-family parity, full frontend behavior or compatibility with every supported
customer archive. All 14 migration requirements remain incomplete and no ledger
status is promoted by these receipts.
