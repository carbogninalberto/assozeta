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

These checks cover installed-bundle and retrieval libraries. They do not activate
HTTP routes or prove authentication, preview/development source selection, remote
refresh, installed cache permissions, MCP, assistant behavior or a verified
required-release package. Source loading and full HTTP composition require their
own contracts and live evidence before the reader can be exposed. Historical
manual artifacts retain their original revision and verification scope.
