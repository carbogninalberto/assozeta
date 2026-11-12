# Bounded S3 object deletion

`Client` implements `domain.ObjectDeleter` using the official AWS SDK for Go v2
S3 module, pinned to v1.114.4 (Go 1.24 minimum). The constructor receives an explicit
endpoint, region, bucket, addressing mode, credential provider and optional HTTP
transport. It does not discover credentials, read `.env`, use instance metadata or
authorize a document. Existing environment/profile configuration and composition
root wiring remain separate work. No production worker invokes this client yet.

`DeleteBatch` accepts at most 1,000 distinct key/version identities and makes one
verbose `DeleteObjects` request. An empty batch performs no I/O. Keys are already
resolved, including `AWS_LOCATION`; the client never normalizes them again. UTF-8,
XML character validity, nonempty keys and 1,024-byte key/version limits are checked
before admission. Duplicate identities fail rather than making outcome mapping
ambiguous. Results retain request order even when the provider reorders them.

Each object reports `deleted`, `access_denied`, `unavailable` or `rejected`.
Explicit `NoSuchKey`/`NoSuchVersion` means deleted. Provider messages and unknown
codes are not returned or logged. A successful HTTP response alone is insufficient:
missing, duplicate or unexpected identities invalidate the entire result, exposing
no partial success. A syntactically valid partial failure retains every individual
outcome. A missing bucket is an error, unlike Django S3 storage's broad handling of
HTTP 404. Unrecognized 404 responses are rejected, not called missing buckets.

The configured deadline covers waiting for an admission slot and provider I/O.
Concurrency is explicitly limited to 1–32 calls. The default transport is cloned
when possible, with matching connection limits. Cancellation and every error release
admission. Response bytes are bounded by an explicit limit (at most 16 MiB); this
bounds the payload, not total SDK/runtime memory. Redirects and SDK retries are
disabled. The durable workflow must decide retries from persisted intent; the
client never sleeps, starts untracked work or retries a possibly completed effect.
Errors contain fixed messages and do not wrap raw provider/credential errors.
Implicit S3 Express session creation is disabled; directory buckets are unverified.

A nonempty version ID deletes exactly that version. Empty version IDs preserve
ordinary S3 current-object deletion: an unversioned object disappears; a versioned
bucket creates a delete marker while retaining earlier versions. The marker ID is
returned. The optional marker flag preserves omission separately from explicit
false: the pinned MinIO omits it when removing an existing marker by version ID.
An independent Boto3 probe establishes this response shape. Repeating deletion
of the current object in a versioned bucket can create more
markers; this client does not claim a once-only external effect. Services must
coordinate references, pending uploads, immutable object identity, durable intent,
completion and restore fencing before using it. A timeout or malformed response
can follow a real deletion and therefore means an unknown outcome. Database
rollback cannot undo that effect. The medical cleanup task remains unimplemented.

`TESTS/adapters/django/s3_objects.py` starts a private, loopback-only MinIO container
with disposable tmpfs storage and synthetic credentials. It uses the repository's
digest-pinned self-host image, checks its binary release, and removes only its own
container. There are no host data mounts or customer/provider credentials. Docker
and the pinned image are required; unavailable infrastructure fails rather than
skipping the check. CI runs this adapter and retains safe result artifacts.

Four actual Django storage cases establish present/missing object deletion, the
legacy missing-bucket 404 behavior and versioned deletion without erasing history.
Seven Go/MinIO groups verify batches of 1/16/128/512/1,000 with one request each,
missing objects and replay, selected-version deletion/replay, current-object delete
markers and removal of a specific marker to restore earlier bytes, real
compliance-retention partial failures, invalid credentials and a
missing bucket. Boto3 then independently verifies exact preserved bytes, surviving
versions, removed versions and returned marker identity. The pinned MinIO returns
`InvalidRequest` for retention, mapped to `rejected`; AWS's `AccessDenied` is tested
at the wire boundary and maps to `access_denied`. Neither becomes success.

HTTP tests additionally cover request signing/session tokens, exact XML identities,
reordered outcomes, malformed/oversized/incomplete responses, fixed error codes,
single-attempt 503/redirect behavior, active and queued cancellation/deadlines,
admission recovery and configuration limits. Evidence is under
`quality-reports/go-migration/s3-objects/`. This proves the provider dependency on
the recorded MinIO build, not live AWS/DigitalOcean, full storage/environment
compatibility, private-file authorization or a completed cleanup workflow.

Primary protocol references:

- [S3 DeleteObjects](https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObjects.html)
- [SDK retries and timeouts](https://docs.aws.amazon.com/sdk-for-go/v2/developer-guide/configure-retries-timeouts.html)
- [S3 Object Lock behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- [S3 version IDs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/versioning-workflows.html)
- [AWS Object Lock access-denied cases](https://docs.aws.amazon.com/AmazonS3/latest/userguide/troubleshoot-403-errors.html)

The MinIO rehearsal now constructs its primary credentials through
`../s3credentials`, including isolated shared-file selection. See that package
contract for verified provider behavior and remaining credential-chain gaps.
