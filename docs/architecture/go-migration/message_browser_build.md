# Reproducible inputs for message-browser acceptance

The parent-owned builder compiles the unchanged UI and native Go/image-codec
inputs in a fresh private workspace. It does not install dependencies, run
services, or establish endpoint-family parity.

Provision the locked UI/browser packages and declared Go module graph first:

```sh
npm ci --prefix UI
npm ci --prefix selfhost/tests/browser
npm exec --prefix selfhost/tests/browser -- playwright install --with-deps chromium
(cd BE_GO && go mod download all)
python -m TESTS.adapters.django.message_browser_build \
  --output quality-reports/go-migration/message-browser-build-FRESH
```

Use the required production Python version and provisioned Node 22, Go,
PostgreSQL/Redis, Docker, and native codec libraries for subsequent acceptance.
The output parent must already exist and have no symlink components. The output
itself must be new. The builder forces offline Go resolution and owns build
flags, configuration and environment; it never consumes ambient compiler or
Node hooks. `go.sum` includes checksums for the complete declared module graph,
including indirect modules, without changing selected versions in `go.mod`.

The builder copies all UI inputs including PostCSS and the package lock. It
records before/after source, dependency, Node, native input and artifact hashes,
and validates the actual linked workflow source maps. Go dependency binding
covers the selected offline module graph and its module directories, excluding
unrelated modules in the shared cache. Escaping links and source drift fail.
UI and native helper processes have owned process groups; interruption and
timeout terminate those groups before output removal. If native termination
cannot be verified, the private output is retained and success is refused.

A successful run supplies `artifact/`, `server`, `image-codec`,
`build-record.json`, `build-observation.json` and `go-build-record.json`.
Pass these exact products to the browser runtime:

```sh
python -m TESTS.adapters.django.message_reads_browser_runtime \
  --ui-dir quality-reports/go-migration/message-browser-build-FRESH/artifact \
  --build-observation quality-reports/go-migration/message-browser-build-FRESH/build-observation.json \
  --go-binary quality-reports/go-migration/message-browser-build-FRESH/server \
  --image-codec quality-reports/go-migration/message-browser-build-FRESH/image-codec \
  --go-build-record quality-reports/go-migration/message-browser-build-FRESH/go-build-record.json \
  --playwright-module selfhost/tests/browser/node_modules/@playwright/test \
  --output quality-reports/go-migration/message-browser-live-FRESH
```

The CI workflow retains independent HTTP/query, repository, browser and
scope/lifecycle gates. It uploads only redacted runtime receipts. Build records,
sourcemaps, source copies, private logs and credentials are not upload artifacts.
Compiler, standard library, build-cache and system-library bytes are not fully
content-bound; this is input traceability, not a claim of identical binaries
across platforms. Before/after hashes do not detect transient adversarial writes.

Parent controls include actual OS group termination with a live helper and with
an already-exited helper leaving a descendant. Both groups disappeared and their
helpers were reaped. The integrated offline matrix passes 308 tests. The final `message-browser-owned-build-r3` compiled 683 UI inputs into 558
artifacts and bound 231 Go module-graph entries. UI/native input stability and
private cleanup passed; its raw build-record SHA-256 is
`1f38a4a1bf056e8cf88e0a64eaa413c9165c0a3798ee645858f0220c3e99d9fc`.
`message-browser-owned-live-r3` passed v1/v2 with all 1,203 source inputs matching
at parent review, unchanged artifact provenance and complete cleanup. Receipt
SHA-256: `85947fa190906c3009b5c858b159108aa355add4eae682370ca5ccd9b0c25090`.
This establishes the bounded authored workflow, not realtime, second-page,
packaged/installed or complete-family acceptance. Historical unsuccessful runs
and earlier successful receipts retain their own source/artifact hashes.
