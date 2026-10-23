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
passes. This tests native-process interoperability, not installed container
volume permissions.

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

The whole untagged Go race suite and vet pass. Installed writable-cache permissions,
real reader browser workflows, MCP, assistant behavior and a verified
required-release package remain open. Historical manual artifacts retain their
original revision and verification scope.

The separate full Django manual capture at `d3165cf` completed with 47 of 60
workflows passing. Twelve failures involved unavailable/timed-out HTTP reads;
the dashboard-list final baseline comparison also observed asynchronously
populated document fields after member-detail navigation. Its denial assertions
passed. The scenario now waits for and reads the real baseline PDF before taking
the initial snapshot; the full final equality assertion is retained. Focused
validation passed against an isolated working-tree snapshot of `f08f716` plus
this scenario correction: one workflow, with real PDF generation and download,
and successful cleanup of its services, volumes and network. This focused run
does not establish a complete capture or release package. HTTP availability
failures still require investigation; no failures are waived.
All owned services, volumes and networks were removed. The failed batch never
reached generation, verification, indexing, evaluation or release-package export.
