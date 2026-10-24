# Public instance logo — implementation and acceptance work

The canonical `/instance/logo.png` route has explicit Go composition, transport,
service and storage boundaries. The filename in the route does not select an
object: the service tries `instance/logo.png`, `.jpg`, `.svg`, then `.webp`.
The separate upload route and alternative URL suffixes are not implemented by
this change. Django remains the active backend and migration owner.

## Storage and response behavior

Production Django always uses its configured S3 default storage for this view;
`STORE_ON_DISK` does not select a filesystem implementation here. Go preserves
that selection and initializes credentials lazily, so broken logo storage does
not prevent unrelated server startup. The filesystem adapter is exercised only
through an explicit test composition override.

An existence-check error aborts the request. An object-open/preparation error
falls through to the next candidate, matching the Django exception boundary.
Go prepares a seekable S3 body before sending response headers, including HEAD.
Selected filenames, content types and `no-cache` behavior come from the service;
provider metadata cannot replace them. Stored bytes are delivered unchanged.
Streaming gzip retains the characterized tiny/empty-body, HEAD, randomized
padding and `gzip;q=0` behavior. Authentication is disabled for this public view;
global session, locale, CORS, security and restore admission still apply.

The storage client bounds admission to four prepared responses, retaining each
slot until its body closes. Provider connect, TLS, response-header and idle I/O
waits have bounds; progressing large transfers have no whole-object size or
duration cap. Transient operation errors and interrupted successful downloads
have separate bounded retries. A download retry truncates and rewinds the same
file. This does not establish equivalence for every provider's retry taxonomy
or timing.

## Preparation storage

The original packaged `/tmp` configuration was insufficient: an actual 65 MiB
object exceeded its 64 MiB tmpfs and incorrectly fell through to another logo.
The production service therefore needs private disk-backed preparation space,
configured by `GO_LOGO_SPOOL_DIR`. The native default remains `os.TempDir`.
This directory contains no authoritative media and is excluded from data backups.

Each file starts with mode 0600 and is unlinked while empty, before requesting
the remote body. The descriptor retains read/seek/retry behavior; close or
process exit releases its storage. If unlink fails, preparation fails before
remote payload consumption. No global temporary-directory setting changes.
Disk capacity remains an operational constraint, and anonymous Docker volume
metadata can outlive a container even though the payload has no remaining name.

## Verification boundaries

The final parent HTTP replay passes **64 Django filesystem cases and 76 S3
cases on each of Django, native Go, Caddy v1 and Caddy v2**, with stable source
hashes in 135.909 seconds. A separate Go filesystem composition replay passes
all 64 cases. Both preserve all 128 managed models, object/cache state and the
owned cleanup checks. The independent upstream observer rejects a deliberately
misrouted actual HTTP 200 from Django.

Raw repeated Accept and Accept-Encoding lines reproduce the original Go 406 and
missing-gzip differences before the scoped fix. Both streaming files and finite
OPTIONS responses now combine these headers. The finite encoder clones and
restores its caller's request; deterministic tests cover compressed bytes and
error restoration. Short OPTIONS metadata can legitimately remain uncompressed
when randomized padding removes the size benefit. The authored alternative
retains exact decoded metadata and requires any compressed wire body to be
smaller. The raw transport explicitly sends identity when compression is not
being requested, matching the ordinary client's existing wire behavior.

Actual slow-client tests retain a 65 MiB zero-link file descriptor after response
headers on native Go and v2, then prove its release on disconnect. The earlier
smaller body was fully buffered through the proxy before observation; that
failed harness run is retained and was resolved by a stronger stimulus, without
weakening the descriptor assertion. Held provider-download cancellation closes
the Go upstream and spool before the provider barrier is released. Django/v1
behavior remains the explicit difference described below. Final parent receipts
are `instance-logo-parent/final76/results.json` and
`instance-logo-parent/final-disk/results.json` under the ignored evidence root.

**Provider trust compatibility remains incomplete.** A separate private localhost
TLS probe against pinned Django S3Storage confirms support for `AWS_CA_BUNDLE`,
shared AWS `ca_bundle` configuration and the `REQUESTS_CA_BUNDLE` fallback. The
Go logo transport with static credentials does not apply those trust settings;
observed handshakes reject the private CA. An explicit trusted transport succeeds
against the same server, so this is not an unavailable-server result. With the
production credential constructor, AWS/shared CA settings also fail during SDK
initialization because its custom-bundle resolver rejects the bounded HTTP-client
wrapper. Both credential initialization and object transport require a tested
fix. These component probes do not establish endpoint or complete provider
parity, and this route remains `implementing`.

The packaged resource runner passes on the clean committed `9188744` image
`sha256:4b71e59340e965d08dfbceb9a38f75342226689e3d857d22d176e89fb6e66e5b`.
Its receipt binds all 601 Go/module source hashes and the Dockerfile, plus server
digest `6ec26660c4b173519cf24a6b573c90ffe124ac734c2fafd1332b83c22f19e743`.
The runner verifies the exact clean checkout, packaged revision and executable;
this supersedes the earlier explicitly recorded uncommitted-source image proof.

That replay serves exact 65 MiB bytes and four concurrent 20 MiB responses,
cancels a fifth admission waiter, and releases spools on response and storage
disconnects. Observed resident growth is 3,723,264 bytes; this is a bounded
workload observation, not a general performance claim. Actual SIGKILL probes
observe a prepared 20 MiB inode and a partial 64 KiB inode with zero links and
allocated blocks, then process exit, an empty retained volume, successful restart
on that same volume and exact subsequent delivery. Reclamation follows the
zero-link inode lifetime; it is not inferred from noisy host free-space changes.
All 128 managed models remain unchanged and the synthetic source receives only
HEAD/GET. Source hashes and owned service/network/volume cleanup pass. The parent
also passes all 66 existing self-host Go tests. Receipts are under ignored
`quality-reports/go-migration/instance-logo-parent/committed-spool/`.

The parent also passes `make selfhost-test`, the full Go race suite, `go vet`,
workflow lint and the real-kernel unlink-denial runner. Seccomp returns EPERM for
actual unlink operations: one metadata HEAD and zero payload GETs occur, no file
descriptor or admission slot remains, and the residual file is empty with mode
0600. Persistent OS denial prevents deleting even that empty name; verified
teardown of the owned tmpfs/container removes it. This test uses a tagged test
binary and does not add production fault hooks. Its runner is required in CI.

The portable authored contract covers candidate precedence, exact bytes,
metadata isolation, large legacy reads, HEAD/OPTIONS/method denials, negotiation,
gzip, sessions, restore admission, CORS, language, invalid locations, provider
errors, disappearance races and transient recovery. Actual HTTP runs use private
current-schema PostgreSQL, Redis and MinIO. Versioned runs independently observe
Django traffic and reject an actual successful response from the wrong upstream.
Managed-state and object preservation and owned-resource cleanup are mandatory.

The filesystem run is a separate test-only composition boundary. Packaged spool
acceptance uses deterministic opaque bytes with `image/png` content type; it
proves exact large-body delivery and resource behavior, not image decoding.
Browser rendering and native manifest parsing pass in the separate
[unchanged-UI PWA acceptance](instance-pwa.md). Installed backend selection,
worker ownership, historical import
and complete provider behavior remain open. No ledger entry is verified by this
slice.

Go cancels an in-progress download when its client disconnects; the observed
Django synchronous download can continue until the held provider is released.
This difference is explicit, not a claim of identical cancellation semantics.
