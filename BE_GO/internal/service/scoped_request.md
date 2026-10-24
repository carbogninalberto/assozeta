# Impersonated request admission

`IdentityResolver` invokes `ScopedRequestGuard` after revalidating the actor,
session and effective person, before collaborator-owner mapping and permission
grants. Association-bound requests require trusted route metadata; an absent
guard or operation cannot silently authorize them. A guard failure returns an
empty identity, without the invalid-session flag reserved for failed eligibility.

`RequestReferences.Operation` and path parameter names come from server route
registration, never client headers. Parsed query/body values remain untrusted.
`RequestObject` preserves dictionary order. `dto.ParseReferenceJSON` preserves
128-bit integers, nested values and Python's first-position/last-value duplicate
key behavior. The HTTP adapter must supply last values for duplicate query keys,
handle form/multipart bodies and impose byte/upload limits. JSON nesting beyond
1,000 levels returns a structural error; this is not a finalized HTTP error policy.

The guard implements the 44 athlete operations, account-wide credential exclusions,
association username checks, subscription-add association selection, path aliases,
nested IDs/lists and athlete-only new-account roles. It retains Django's denial
order. Ordinary and administrator impersonation without an association binding
do not run this association-specific gate; their endpoint rules still apply.

Reference validation builds an ordered plan ending at its first structural/role
failure. It reads at most 512 distinct references per resource kind per batch,
then evaluates the original order. Alternating kinds and duplicate IDs do not
cause a database call per field. Medical uploads use bounded Redis MGET batches,
with linked-subscription SQL only for references not granted by the cache.
Infrastructure errors propagate without a usable identity.

Compatibility details include alias normalization before removing `_id`, exact
association username comparison except Unicode case folding for `search_profile`,
UUID hex/URN/integer input, ignored literal null/empty/bool ordinary references,
and the different handling of a bool/empty value selected from an object. Invalid
group UUIDs follow the existing manager's no-group fallback; association/family
predicates remain mandatory. The group header never selects the association.

`MedicalUploads` reads the existing Django cache database/key version and the
bounded protocol-4/5 UUID string record. The key retains the raw session and
medical-reference spelling, matching `medical_upload_key`. It never renews TTL or
caches authorization locally. It does not execute pickle globals/constructors.
Unknown cache serialization is an infrastructure error, not an upload grant.

The private account adapter now runs 132 explicit Django/Go guard cases, including
all 44 operation names and six full resolver cases with Django-issued sessions
and real route resolution. Component cases do not assert those endpoints execute
successfully. Real database tests use 1, 16, 128, 512 and 1,025 distinct tag IDs
interleaved with a repeated course reference: SQL counts are 2, 2, 2, 2 and 4.
Thirty-two concurrent guard calls use four connections without retaining them.
Redis TTL/no-renewal, revocation and expiry are checked on the same guard instance;
Django observes the Go-side revocation. Business rows/audits remain unchanged.

Remaining work includes public HTTP composition/DTOs/error bodies, request-size
admission, cookies/login writes, endpoint returned-query filtering, instructor and
ordinary-user permissions, private document access, shared-certificate cloning,
upload workflow writes/audits and authorization across concurrent business writes.
This read admission does not lock records. Business services must recheck and
coordinate their mutation invariants in the owning transaction. No business or
authentication endpoint is certified by these component tests alone.
