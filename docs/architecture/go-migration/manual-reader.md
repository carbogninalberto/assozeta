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

`TESTS/adapters/django/manual_reader_http.py --go-source` observes 58 real Django
HTTP cases on private PostgreSQL and an ASGI server, including identity, methods,
headers, restore admission and bounded SQL. It separately runs 32 Go library
source comparisons with independent fixture directories. The local HTTP publisher
checks download requests, installed-bundle precedence, failure retention and
verified asset promotion. Go tests also exercise concurrent refresh, cancellation,
atomic publication and pruning. These fixtures use current models rather than
historical migrations.

The Go HTTP reader is not activated. The Django HTTP observations establish its
contract; they are not evidence of Go HTTP authentication or transport parity.
Remaining work includes full HTTP composition, preview query projection, actual
Django/Go cross-process lock contention, writable installed-cache permissions,
MCP, assistant behavior and a verified required-release package. Historical
manual artifacts retain their original revision and verification scope.
