# Unchanged browser manifest and logo consumers

This acceptance exercises the existing UI against Django and the packaged Go
server. It proves browser rendering and native manifest parsing for the selected
public resources. It does not complete operator backend selection, installed
PWA behavior, service-worker/offline behavior or the full frontend migration.

## What the browser exercises

The same extracted UI bytes run in three modes: the shipped compatibility alias
using Django, and private fixture gateways mapping the four selected resources
through shipped Caddy v1 and v2. Those resources are instance status, instance
configuration, the manifest and the logo. Unknown API paths return 502; health
WebSocket traffic remains explicit Django coexistence. No API response, DOM
configuration or runtime API host is synthesized. External requests are blocked.

Each mode opens the login page and performs an actual reload. Playwright checks
the configured title and theme color, manifest link, visible logo URL and decoded
512-by-512 image dimensions. Observed responses must carry the exact PNG bytes
and manifest JSON. Chromium's `Page.getAppManifest` independently parses the
manifest without errors. The browser remains anonymous throughout.

The adapter observes Django requests independently of response headers. Two
separate negative controls route only the manifest or only the logo from v2 to
Django. They receive real successful responses and still fail the upstream
identity condition. The stock-mode control keeps the shipped `/api` behavior
separate from the private versioned gateway used for compatibility testing.
Positive and negative workloads use the same upstream validator. Pure failure
controls cover a real-success-shaped Django observation on v2 and prevent a
Docker timeout from exporting private command arguments or chained exceptions.

## Source and state boundaries

UI provenance binds the immutable frontend image and every extracted artifact.
Nine relevant source modules are verified against embedded source-map content,
including the instance store, login component, brand theme and API middleware.
Public service-worker, manifest, favicon, brand-cache and logo assets also match
their source bytes. Other build-input hashes are references, not a claim that
every input is embedded in a source map. In particular, no complete source-map
coverage for the application entrypoint or offline installation is inferred.

Each workload compares all 128 managed models, including automatically created
join tables, every owned S3 object with metadata/tags and an unrelated sentinel,
and exact Redis state. The fixture disables periodic image healthchecks only
during measurement; actual startup readiness and health still run. Their temporary
Redis keys must expire back to the exact initial cache before the browser starts.
No cache keys are excluded or flushed to make the check pass.

The current committed-image evidence uses revision
`9188744d8d09d32fb938a474369bf6cfd6a2899a`, Linux/arm64 image
`sha256:4b71e59340e965d08dfbceb9a38f75342226689e3d857d22d176e89fb6e66e5b`
and server digest
`6ec26660c4b173519cf24a6b573c90ffe124ac734c2fafd1332b83c22f19e743`.
The source-bound browser replay passes all five workloads, state checks and
cleanup. The earlier `324d374` image fails at its actual missing-logo HTTP 404;
that failure is retained separately from infrastructure-only fixture failures.

## Remaining acceptance

Runtime operator selection is unfinished: the existing UI still contains
build-time host/API configuration, while the fixture uses a private gateway.
The stock control proves that the current compatibility alias still selects
Django. Browser manifest parsing and a decoded logo do not prove OS icon
installation, an install prompt, standalone launch, offline updates, worker
handoff or an installed backend switch. Private-CA storage behavior remains the
separate open issue documented in [logo acceptance](instance-logo.md). No ledger
entry becomes verified through this browser slice.

CI builds a Go image from its checkout, reuses the one unchanged UI build, runs
the positive workflows and both fallback controls, and retains named result
JSON and screenshots. Private service files and fixture inputs are not uploaded.
The parent receipt is under ignored
`quality-reports/go-migration/instance-pwa-parent/final-hardened/results.json`.

The first hosted PWA run (`40051561946`, head `07507b8`) fails before navigation:
schema initialization clears `PLAYWRIGHT_BROWSERS_PATH`, so Playwright searches
the default cache although CI installed Chromium in its configured cache. The
adapter now captures the browser tool environment before initializing Django and
passes it explicitly to Node. Browser receipts record the resolved executable.
The local regression replays all five workloads with a nondefault cache and
verifies the executable path, state and cleanup. A separate missing-cache control
fails before navigation and still cleans up, proving there is no silent fallback
to the developer's installed default browser. These receipts are retained under
`quality-reports/go-migration/instance-pwa-parent/browser-cache-regression/`;
they do not turn the failed hosted run into a pass.
