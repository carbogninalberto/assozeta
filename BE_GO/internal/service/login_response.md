# Login response composition — incomplete image dependency

`LoginResponses.Build` composes the existing base profile, association profile and
membership-card service for an already admitted account. It accepts an explicit
JWT pair and trusted audit context. It does not authenticate, issue tokens, perform
final user writes, set cookies or register an HTTP route. The login coordinator
must still call finalization and withhold the response on any failure.

## Preserved response rules

- Role 1 labels the response `association`, role 2 `athlete`, and role 3
  `association`. Historical stored role 0 also labels it `association`, because
  Django indexes `ROLE_CHOICES[role-1]`; it does not receive association fields.
  Other supported-schema integer values fail before profile serialization, even
  for a superuser. These labels do not grant permissions.
- Superusers return immediately with role `administrator`,
  `user_data.is_superuser=true` and top-level `requires_welcome=false`. They do not
  read an association, instructor, tables settings or card configuration.
- Ordinary athletes and role 0 return the base profile without these optional
  fields. Their avatar is returned unchanged, as in Django.
- Association users select their own nondeleted association. Collaborators select
  their connected owner's nondeleted association and add top-level
  `requires_welcome=false`. Base user preferences, tables settings and
  `temporary_invoice_deletion` remain the collaborator's own values.
- Only collaborators query `Instructor.associated_user_id`, selecting the lowest
  instructor UUID. Django applies no owner/draft predicate to this metadata read;
  fixtures include a draft instructor owned by another user. No record means the
  `instructor_id` key is absent, not null. This is not instructor authorization.
- `preview_and_custom_features` comes from the instance's three registration flags
  in fixed family/federation/multiple order, with their existing Italian names and
  empty descriptions. An empty feature set is `[]`. Retired plan/M2M entitlements
  are not queried and flags never grant data permissions.
- `tables_settings` preserves JSON null, scalars, arrays and integers beyond 2^53.
  It appears only for association/collaborator responses. Optional DTO fields use
  pointer presence so absent, false, empty arrays and explicit null stay distinct.
- `empty_sections=true` appears only when regulation or demand is null or empty;
  whitespace is present text. Logo processing occurs only inside that branch.
  Association/collaborator avatar processing occurs whenever the avatar is nonnull.
- Card configuration is added after image processing and retains its existing
  atomic default-creation/audit/index transaction. Failures return no partial DTO
  or supplied tokens. Prior committed invariants remain separate.

## Image gate

The image processor is an explicit `domain.LoginImageProcessor` dependency.
`pkg/storage/loginimage.Processor` now composes the existing file pipelines with
shared admission/deadline and data-URI handling. Its registry still has explicit
missing-codec and ambiguous-header gaps; it is not wired into public login.

The real PostgreSQL fixture injects this processor. Ten additional cases verify
owner/collaborator avatar and logo conversion, athlete/administrator bypass,
complete-section logo bypass, malformed URIs, GBR/SUN precedence, ICO logo conversion, and WebP/CUR/PCD avatar/logo conversion. Default card
creation/audit follows successful conversion. Thirty-two mixed image responses
retain the existing query bounds and four-connection pool. A real missing decoder
returns no response or supplied tokens after five profile/association/settings
statements, before the two card-configuration statements. Fixture mutations are
restored and the complete managed-table snapshot remains equal.

A nil processor still fails when an image requires processing. Unit doubles remain
useful for ordering/failure injection, but the added image evidence uses concrete
codecs. Full image-registry, public-login and session-delivery parity remain open.

## Registration environment

`config.Config` now reads the existing `FAMILY_REGISTRATION_ENABLED` (default true),
`FEDERATION_REGISTRATION_ENABLED` (false) and `MULTIPLE_REGISTRATION_ENABLED`
(false). `Env.bool` semantics include signed/nonzero integer strings, underscores,
Python 3.13.2 Unicode decimal digits, the 4300-digit default limit and Django's
case-insensitive true words. Unknown strings are false. Proxy references inherit
that flag's default when missing; cycles fail startup without logging values.

For these flags, `.env` values use Django's literal `read_env` rules before process
environment precedence: exact assignments, last duplicate, line-based quoting,
literal inline comments and deferred `$VAR` references. Go's existing dotenv
loader still handles other settings; this does not certify all deployment-variable
or malformed-file compatibility. Both views use the same file and environment,
without a second credential file or mutation of the process environment.

## Executed component evidence

`TESTS/adapters/django/login_response.py` specifies 47 full response JSON/outcome
cases and runs the actual Django `JWTTokenService.build_login_response` with its
real serializers, queries and default-card write. Only serializer time is fixed;
input token strings are explicit synthetic response fixtures, not proof of JWT
issuance. Go composes the actual PostgreSQL services. Whole JSON values are
compared with exact number decoding. All managed tables match after restoring the
declared default configurations and their audit/index records. Django reads those rows.

The same adapter establishes 28 explicitly expected boolean values, eight dotenv
cases and cyclic-proxy failure using the installed Django environment parser.
Go config parsing and loading must match those expected outcomes.

Ordinary association/collaborator responses use seven business statements with
existing configuration and ten for default creation. Instructor counts
0/1/2/128/512 do not change the seven-statement bound. Thirty-two concurrent
512-instructor compositions use 224 statements on a four-connection pool. Current
settings/instructor changes are observed after explicit persisted transitions.
A real denied-SELECT role proves instructor access is required for collaborator
responses but is not a new dependency of owner responses. Errors and cancellation
release transactions and return no partial response.

Query counts do not prove workload performance or query-plan adequacy. Whole-login
revocation/snapshot consistency, mixed-runtime default creation/audit races, complete image-registry
coverage, social authentication, rate limiting, full HTTP/global middleware and
cookie/finalization composition remain open. This component does not advance any
ledger entry to verified.

The current fixture composes CUR avatars/logos, including CUR/TGA precedence,
and PCD avatar conversion with failed-rotation logo fallback. Eleven concrete
image scenarios participate in the 32 mixed concurrent responses. TIFF bypass
and missing-decoder no-token/no-card-query cases remain required; complete image
and public-login parity is not established.
