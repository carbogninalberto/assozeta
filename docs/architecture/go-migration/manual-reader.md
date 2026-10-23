# Embedded manual reader

The installed-bundle library validates public applicability and reads independent,
hash-verified asset bytes. Its 36 explicit Django/Go comparisons cover source
freshness, feature selection, private sections, duplicate paths and asset denial.
The installation checker remains stricter about PNG headers than runtime reads.

The retrieval library consumes the existing sealed semantic model. It preserves
Italian token and action normalization, operation/protocol filtering, candidate
BM25 statistics, full-corpus vector alignment, score thresholds, rounded-score
ordering and the ordinary reader's ten-result limit. It never trains or rewrites
the model. Public projection preserves the existing embedded links and asset URLs
and copies nested results so concurrent requests cannot modify cached content.
Application messages retain their existing Italian wording.

`TESTS/adapters/django/manual_retrieval.py` checks 33 explicit cases and seven token
vectors against the actual Django `ManualIndex` and `_public` implementation,
then compares the Go result. Expected IDs and scores are specified in the fixture;
the Django oracle additionally supplies the complete projection for comparison.
Five Go groups check nested copies, concurrent requests, model immutability and
cancellation. The oracle uses the pinned offline NumPy version and forbids network
access and model training. It creates and removes only private fixture files.

The source loader follows the reader's separate preview, development, installed
bundle and runtime-cache precedence. It preserves `MANUAL_ASSET_ROOT`, verifies
source freshness before filtering public sections, and limits preview assets to
the preview path. Remote refresh holds the shared `.sync.lock`, validates the
corpus and deduplicated images before atomic promotion, and retains the previous
corpus after failure. Downloads have byte limits, timeouts and redirect validation;
inactive content-addressed assets retain the existing retirement grace period.

`TESTS/adapters/django/manual_reader_http.py --go-source` observes 69 real Django
HTTP cases on private PostgreSQL and an ASGI server, including identity, methods,
headers, restore admission and bounded SQL. It separately runs 32 Go library
source comparisons with independent fixture directories. The local HTTP publisher
checks download requests, installed-bundle precedence, failure retention and
verified asset promotion. Go tests also exercise concurrent refresh, cancellation,
atomic publication and pruning. These fixtures use current models rather than
historical migrations.

`TESTS/adapters/django/manual_refresh_lock.py` starts actual Django and Go
synchronizers in separate processes against one private cache and an HTTP
publisher that holds its first response. Three cases verify Django blocking Go
until cancellation, Django handing the lease to Go, and Go handing it to Django.
The competing runtime cannot download or promote while the lease is held;
after release both runtimes read the same corpus and reuse the verified asset.
All processes and private files are removed. A negative control that gives Go a
different lock filename fails these checks, then the unchanged shared-lock code
passes. This tests native-process interoperability.

The normal Compose services share a writable `/app/manuale` volume while retaining
read-only root filesystems. The Go image initializes the cache directory as UID/GID
10001 so either image may create the volume first. The immutable release bundle
remains separate. `TESTS/adapters/go/manual_cache_image.py` runs both initialization
orders against actual Go and Django images, checking restricted-file reuse, atomic
replacement, asset deletion and writes denied outside the cache. The previous Go
image fails the Go-first case; the corrected image passes both orders and removes
all owned containers and volumes. The self-host release workflow runs this check.
These filesystem probes complement the native refresh tests; they do not prove
HTTP refresh inside an installed image or a verified required release package.

The Go composition root now registers `/manuale/sections` and
`/manuale/assets/*`. Handlers resolve the existing actor/effective/scope identity;
the service enforces query admission and public-only source selection. A dedicated
repository reads only the live `self_hosted` flag, with no provider secrets or
maintainer privileges. Preview selection precedes the instance lookup. Preview
queries preserve Unicode case folding, whitespace splitting, title-hit ranking,
stable ties and the ten-result limit. Responses copy nested source data.

The same 69 contracts pass against the actual Go server with PostgreSQL and Redis,
and against both APIs through the shipped Caddy configuration using `--versioned`
with `--backend django` or `--backend go`. The portable suite compares full JSON,
selected headers, exact publisher requests, verified asset bytes and unchanged
business/audit/cache records. Ordinary reads stay at two SQL statements for owners
and three for collaborators at 16 and 128 sections; live feature context is one
statement. Go may use fewer identity statements than Django during impersonation.
No required case is skipped. These are native API processes behind real Caddy,
not newly built installed Go images.

Differential normalization is limited to header method/token ordering and each
fixture's verified corpus identity, which includes its ephemeral publisher port.
Every returned identity is first checked against an actual fixture source. Gzip
responses are decompressed before comparison; Go preserves Django's size threshold,
streaming-asset behavior, `Vary`, quality-token handling and randomized gzip header.
Existing Italian messages, missing-source responses and denial precedence remain
unchanged. The initial missing-route 404 and missing encoding-header failures are
retained under `quality-reports/go-migration/manual-source-integration/`.

`TESTS/adapters/django/profile_browser.py --manual-reader` additionally verifies
the unchanged built frontend with real password login, public chapter navigation,
deep-link reload, search and no-evidence results, authenticated PNGs and zoom,
mobile navigation without horizontal overflow, anonymous denial and private-image
denial. Both native backends pass with the same authored synthetic corpus and a
640x360 PNG pattern. The test hashes the exact image Blob consumed by the reader
and checks decoded dimensions. Chromium's debugger returned empty streamed image
bodies in the first test attempt; inspecting the consumed Blob preserves the exact
byte assertion. Desktop and mobile screenshots were inspected. The same workflow
fails with a real manual-sections 404 against pre-route Go revision `f2993b0`.

This browser check extends the existing profile workflow and its source-map and
compiled-artifact checks. A private Caddy selects the named backend for canonical
`/api` calls; root WebSockets and the documented logo/manifest/export-denial routes
still use Django. This complements the separate actual `/api/v1` and `/api/v2`
HTTP contracts; it does not prove installed alias switching, assistant behavior,
Go WebSockets, a production manual corpus or packaged HTTP refresh. The existing
fresh-login/profile-edit/audit checks also pass with the manual enabled. CI runs
the combined workflow and retains its reports and screenshots.

The whole untagged Go race suite and vet pass. MCP, assistant behavior, packaged
HTTP refresh and a verified required-release package with its full browser checks
remain open. Historical manual artifacts retain their original revision and
verification scope.

The manual-tool function oracle is separate from reader HTTP and browser tests.
`manual_reader_http.py --tools` runs 63 authored cases through the actual Django
functions, live PostgreSQL context resolution and real source loaders. It covers
all four tools: search, section reads, technical evidence and gap diagnostics.
No context resolver, loader or permission decision is replaced. Portable contracts
check result shapes, complete public projection keys, independently derived search
scores, exact owner evidence/provenance, denial messages, database/file retention
and SQL bounds. Public search uses one query at 2, 16 and 128 public sections;
ordinary owner context uses three, and owner diagnostics currently use six because
the context is resolved twice.

Unlike ordinary HTTP reading, trusted owner tool calls can retrieve maintainer
sections. That authority requires the active, undeleted primary owner of the
self-hosted instance and the same association; administrator status alone is
insufficient. Public technical-evidence calls load the corpus before denying
access, while public gap calls deny before loading it. Invalid queries precede
source access. Runtime gap diagnostics may report an incompatible version, while
an incompatible installed bundle fails its strict loader. Development tools ignore
preview knowledge, and owner diagnostics report reappeared deleted sources even
when ordinary retrieval rejects them. These differences are explicit contracts.
The oracle treats `user_id` and association as trusted call context; MCP/agent
argument filtering and their transports still need their own tests. The Go tool
service and transport implementation remain open; these cases establish Django
behavior, not Go parity.

The separate full Django manual capture at `d3165cf` completed with 47 of 60
workflows passing. Twelve workflows failed with HTTP, Docker or browser
timeouts, missing UI elements or unsettled UI state;
the dashboard-list final baseline comparison also observed asynchronously
populated document fields after member-detail navigation. Its denial assertions
passed. The scenario now waits for and reads the real baseline PDF before taking
the initial snapshot; the full final equality assertion is retained. Focused
validation passed against an isolated working-tree snapshot of `f08f716` plus
this scenario correction: one workflow, with real PDF generation and download,
and successful cleanup of its services, volumes and network. This focused run
does not establish a complete capture or release package. The other failures
still require investigation; no failures are waived.
All owned services, volumes and networks were removed. The failed batch never
reached generation, verification, indexing, evaluation or release-package export.
