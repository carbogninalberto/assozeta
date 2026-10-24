# Storage TLS implementation and acceptance

This work covers the owned credential-provider transport and public-logo S3
transport. The migration remains incomplete. It does not establish every
provider operation, storage upload, proxy feature or backend activation path.

## Selected trust and initialization

The pinned Django S3Storage oracle distinguishes an absent setting from an
explicitly empty setting. Selection is `AWS_CA_BUNDLE`, then the selected shared
profile's `ca_bundle`, then `REQUESTS_CA_BUNDLE`. A selected nonempty bundle
replaces default roots. Missing, invalid and wrong bundles must fail HTTPS targets;
they must never trigger a fallback to disabled verification. Plain HTTP does not
read the certificate file. Existing explicit-empty selection disables verification
in Django, and that separate legacy behavior is part of the authored contract.

Bundle paths retain literal dollar signs and whitespace; only the characterized
user-prefix expansion applies. Credentials and storage clients initialize
without opening the selected bundle. Verification loads it for a new handshake,
so repairing a file can recover the same client while an existing keepalive
connection retains its established trust. Chain, hostname/IP, validity and
server-authentication checks are required, including resumed connections.

The external AWS config module remains pinned to v1.33.9. Its complete upstream
source and license are retained under `BE_GO/third_party/aws-config`; a narrow,
default-off option lets the supplied HTTP client own CA handling. This avoids
the upstream resolver's eager file read and concrete-client type assertion while
preserving credential selection and the bounded provider HTTP wrapper. An exact
reverse-patch/hash guard checks the declared adaptation. Its nested upstream
tests run explicitly because application `go test ./...` does not traverse that
external module. There is still one Assozeta-owned Go module.

## Default roots and HTTPS proxies

Actual Linux probing found that native Go roots accept `SSL_CERT_FILE` and
`SSL_CERT_DIR`, whereas Django's default certifi policy ignores those variables.
Merely stripping environment overrides would still leave different OS root sets.
The implementation therefore pins the existing certifi dependency to 2026.10.19
and embeds its exact 120-certificate bundle for the owned storage transports.
Each caller receives an independent pool. The upstream license and provenance
are retained; the guard compares both artifact bytes with the installed pinned
Python distribution. Changes to a bundle and its local hash together still fail
the independent Python comparison. Update both backend artifacts together.

The proxy oracle uses separate private roots and DNS/IP names for an HTTPS
CONNECT proxy and target. Django applies selected roots to both handshakes while
verifying their respective names. A target-only bundle fails before CONNECT;
a proxy-only bundle reaches CONNECT but fails target verification. A combined
bundle succeeds. Explicit proxy-specific trust has its own behavior and must
not be inferred from the ordinary inherited-policy case.

Plain HTTP targets forwarded through an HTTPS proxy have a separate legacy
rule: botocore selects `CERT_NONE` from the target scheme before connecting to
the proxy. Actual Django requests succeed with absent, valid, wrong, missing,
invalid and explicitly empty CA selections and emit `InsecureRequestWarning`.
This is a preselected transport policy, not a retry after verification failure.
The Go implementation must preserve this narrow branch without weakening
HTTPS-target verification. Direct plain HTTP still does not load a CA file.

An initial proxy fixture lacked required authority-key identifiers. Python's
strict outer-proxy verification rejected that chain, while a separately supplied
botocore proxy context used different flags. The initial result and a dedicated
malformed-chain matrix are retained. Correcting the positive fixture does not
erase that distinct validation boundary or prove identical OpenSSL and Go
certificate-validation semantics.

The corrected transport matches all 30 independently authored outer-proxy chain
decisions, including the nine earlier permissive differences and the positive
missing-extension boundaries. A separate control runs all 60 strict/ordinary
chain decisions with OpenSSL 3.0.22 in the production Python base image; it matches
the native OpenSSL 3.5.9 oracle. That control is chain verification, not another
packaged Django HTTP execution. One concrete distinction remains outside the
default outer-proxy proof: the characterized explicit proxy context accepts a
trusted root without basic constraints, while ordinary Go verification rejects
it. The green strict matrix must not be generalized to that separate context.

## Evidence and remaining work

The original configured-bundle component matrix passes 32 Django and 32 Go
cases. The actual native and packaged logo endpoint replay also passes 32 cases
per runtime, preserving 128 managed models and verifying provider signatures,
lazy startup, responses and owned cleanup. This evidence binds image
`sha256:691788061222f7cfa6d51c170dba9e0a2ad001bfbeecea066261d1d551374e8c`
to its uncommitted source receipt; its embedded `9188744` revision is only the
base. It predates the subsequent default-root and proxy corrections and does not
verify those changes. The earlier packaged shared-bundle failure remains retained;
the passing replay uses immutable per-case profile files with exact mount checks,
without claiming a conclusively proven cause for the earlier failure.

Independent proxy characterization passes 14 complete-certificate and 14
malformed-chain cases. Default-root artifact checks, four guard failure controls,
and pool-isolation checks pass. The final integrated source passes 34 Django/Go component cases and 34 native
plus 34 actual packaged endpoint cases, including Linux `SSL_CERT_FILE` and
`SSL_CERT_DIR` rejection. The endpoint replay takes 541.144 seconds, independently
verifies 204 SigV4 requests, preserves all 128 managed models and verifies log
redaction and owned cleanup. It binds image
`sha256:e483ba58b0e400285c6dd8632321de420abfbcf47519e0b70dd2f0d67fa228ca`
and executable
`c38c47e4f843e8d101a99d87d555e55e79867a509e350cf817b5818961bfb118`
to all 817 Go/module source hashes. Its embedded `9188744` is the base of an
explicitly recorded uncommitted build, not a clean revision claim. The parent
imports the exact 147-file change manifest and independently passes the 34-case
component oracle, full race/vet, explicit upstream config tests and both guards.
Existing 44 credential, 82 role/metadata and 38 web-identity cases also pass.
The canonical aggregate receipt is
`quality-reports/go-migration/storage-tls/acceptance-final.json`.
Proxy acceptance remains actual component HTTP/TLS rather than a packaged proxy
matrix; broader storage and migration acceptance remains incomplete.

The separate storage CI job checks the dependency adaptation, default-root
artifact, upstream config module, owned transports, Django oracle and a fresh
packaged server. It uploads named result JSON only. It does not reuse private
developer certificates, fixture files or an ignored local build receipt.

Resource claims remain scoped: credential responses retain their 1 MiB cap and
provider time/redirect bounds. The host transport cache is not a global limit on
all live connections, and a configured local CA file is not covered by the
remote response-body cap. These checks do not establish universal bounded memory
or all TLS/proxy behavior.

Go's HTTP transport may continue a shared connection dial after the waiting
request is canceled. Cancellation must return promptly to that caller, and the
underlying TLS dial remains bounded by its configured handshake timeout. This is
separate from cancellation of an already established logo payload download; its
earlier immediate-close evidence must not be generalized to shared TLS dials.
