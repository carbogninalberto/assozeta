# Storage credential construction — migration dependency

`New` constructs an explicit AWS credentials provider from parsed storage options
and the process's existing AWS environment/shared files. It does not mutate the
environment, write credential files or log provider errors. The S3 deletion client
accepts the returned provider through its existing constructor. The disposable
MinIO rehearsal now uses this constructor for its signed deletion operations.
The production root/worker and complete credential-chain parity remain unfinished.

The implementation uses the [official AWS configuration/providers](https://docs.aws.amazon.com/sdk-for-go/v2/developer-guide/configure-gosdk.html): config v1.33.9, credentials v1.20.9 and
STS v1.51.4, alongside the existing SDK root/S3 pins. Boto3 selection differs from
SDK defaults: storage options take precedence; an explicit storage profile rejects
static keys and overrides ambient credential/profile selection; otherwise
`AWS_DEFAULT_PROFILE` precedes `AWS_PROFILE`, including explicit empty values.
Environment credentials still precede an environment-selected profile. Missing
selected profiles and malformed shared files fail even with static credentials.
File paths expand existing variables/user prefixes without dropping unknown names.
Shared credentials override config-file credentials. Values and tokens stay literal.

The shared-file validator rejects malformed declarations and duplicate sections or
options that the SDK otherwise silently ignores/overwrites. Each regular file is
limited to 1 MiB. Missing/nonregular paths are ignored like Boto3. This is a
structural check before SDK loading, not a claim that every Python INI feature is
implemented; DEFAULT inheritance and complete Unicode/continuation semantics still
need differential coverage.

Three explicit corrections reject access-only, secret-only and standalone-token
options rather than constructing unusable partial credentials or selecting another
identity. A fourth bounds credential-process output at 1 MiB. Actual Boto3 behavior
is recorded separately from these intended Go outcomes. Process stdin is closed,
stderr discarded, and subprocess groups killed on cancellation/overflow. Commands
have a ten-second deadline and a one-second pipe cleanup bound. The SDK's custom
command builder is applied to directly selected process providers and to process
sources beneath nested assume-role profiles, before any credential retrieval.
Linux/macOS process groups follow the backend's deployment/development platforms.

The default HTTP client has a ten-second timeout, four connections per host and
no redirects. Responses are capped at 1 MiB. Endpoint-credential providers receive
that client explicitly: the config loader does not propagate its general HTTP
client to this provider. Credential HTTP retries are disabled, including that
separate provider retry policy. Injected HTTP clients retain responsibility for
their own deadlines/redirect policy. Errors expose fixed categories, not raw SDK
messages, paths, command output, tokens or response bodies.

Retrieval honors caller cancellation, has a ten-second caller bound and rejects
already-expired credentials. SDK caches coalesce concurrent retrieval. The shared
cache may continue one refresh after an individual waiter cancels; its HTTP/process
operation remains independently bounded. Returned expiry retains the provider's
actual timestamp, and expired entries refresh on the next request. No pre-expiry
refresh parity with Boto3 is claimed.

Verification:

- `TESTS/adapters/django/storage_credentials.py`: 44 authored production Python
  3.13.2 cases invoke actual S3Storage/Boto3 with private files/processes, then Go
  under race detection. No home profiles or external credential network are used.
- Go wire tests: 64 concurrent endpoint retrievals make one request; HTTP errors,
  malformed/oversized responses, denial, no retries/redirects, expired credentials,
  cached cancellation, caller deadline and transport completion are checked.
  Short-lived credentials remain valid and refresh after their actual expiry.
- Two nested role profiles over a real local STS protocol endpoint verify source
  signing, configured duration/session/external ID and bounded caching: 32 callers
  cause one credential process and two STS requests. Process-group cancellation and
  file-size bounds are exercised separately.
- Seven real MinIO groups and four actual Django cases retain object/version/
  delete-marker/retention/denial behavior using the new provider constructor.

Evidence lives under `quality-reports/go-migration/storage-credentials/`.
The initial undefined constructor, lenient INI behavior, expired process output,
unbounded endpoint HTTP, oversized output and short-lived-expiry failures were
observed before the corresponding corrections.

Remaining gates include complete Boto3 role/default-duration/MFA/web-identity/SSO/
metadata and legacy Boto credential-file behavior, provider refresh/error semantics,
full INI compatibility, live cloud evidence and composition with all `.env`-only
fallback-chain settings. Nested-role wire tests establish the tested SDK protocol
boundary, not full Django role parity. Production destructive cleanup still needs
durable intent, reference/upload exclusion, restore fencing and scheduler ownership.
