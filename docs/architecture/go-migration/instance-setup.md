# Instance setup, reconfigure and logo uploads — implementing with HTTP parity

The first-run and branding views of `BE/instance` are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/instance_setup.go`, composed in
`BE_GO/cmd/server/instance_setup.go`, S3 writes in `BE_GO/pkg/storage/instancelogo/write.go`):

| Route | Django view | Permission | Methods |
| --- | --- | --- | --- |
| `instance/setup-token/validate` | `InstanceSetupTokenValidateView` | `SetupTokenOrAuthenticated` | POST |
| `instance/configure` | `InstanceSetupView` | `SetupTokenOrAuthenticated` | POST (JSON only) |
| `instance/reconfigure` | `InstanceReconfigureView` | `IsAuthenticated` | PUT (JSON only) |
| `instance/logo` | `InstanceLogoUploadView` | `SetupTokenOrAuthenticated` | POST (multipart, form) |
| `instance/admin/logo` | `OwnerLogoView` | `IsInstanceOwner` | POST (multipart, form) |

`InstanceConfiguration.save` (the singleton check and the empty display settings default) is
part of the same port. The configuration body and the administrator check reuse
[instance-bootstrap](instance-bootstrap.md) and [instance-admin](instance-admin.md); the S3 client
is the [instance-logo](instance-logo.md) storage with its own instance and two added operations.

## Shared behaviour

- Identity treats `instance/...` as an administration path (no impersonation, no collaborator
  registry; a non-administrator sending `X-Impersonation-Id` gets the 403 of instance-admin).
  `request.user` is the authenticated account itself.
- `SetupTokenOrAuthenticated` reads the first configuration. With one, it allows only a superuser
  or the user of the primary association. Without one, it compares `X-Setup-Token` with
  `INSTANCE_SETUP_TOKEN` (constant time; an empty setting allows nobody). A denial without an
  authenticated account is DRF's 401 `Authentication credentials were not provided.` with the
  Bearer challenge; otherwise 403 `Setup token missing or invalid.`.
- Permissions run before OPTIONS and method dispatch. `Allow` is the view's method plus OPTIONS.
  OPTIONS names the class without `View`; the description is the class docstring (OwnerLogoView
  has none) and `parses` lists the view's parsers.
- `request.data` is parsed when the view reads it, so the configured/owner checks answer before a
  body of another media type is 415.

## configure

- Once configured: 400 `{"success": false, "error": "Instance already configured. Use /instance/reconfigure to update."}`
  (the body is not parsed).
- `InstanceSetupSerializer` errors answer 400 `{"success": false, "errors": ...}`: `domain`
  (CharField 255, then at least 3 characters, stored lowercased), nested `oem` (name 255, abbreviation
  50, `primaryColor` pattern, `supportEmail`, `logo` empty or `/api/instance/logo.png`), `oauth` and
  `stripe` (default `{}`, not validated when omitted; `clientPortal` URLField), and `initialization`
  (`type` fresh/import; its `validate()` runs only without field errors and reports one message:
  the missing fresh field in order, a password shorter than 8 characters after trimming, or the
  missing import task id).
- Fresh setup, one transaction in Django's order: the configuration (provenance `fresh`, the
  default display settings, OAuth ids with null as `''`, the Stripe columns empty), the owner
  (`create_user`: email with a lowercased domain, a 20-hex-digit username, the PBKDF2-SHA256 hash at
  1,000,000 iterations, role association), the association (`tax_code` empty, both fees 20.00),
  the `cassa` and `banca` accounts and the onboarding row, then `config.save()` with the primary
  association. Auditlog create entries are written for the user, the association, both accounts and
  the onboarding row (actor: the authenticated account, none for a token-only request), with index
  rows `self` and `direct` for the association and the accounts. Response
  `{"success": true, "message": "Istanza configurata con successo", "association_id": ..., "redirect": "/login"}`.
- Import setup: a task without a ready result (no stored row, or a state other than SUCCESS,
  FAILURE or REVOKED) answers 500 `{"success": false, "error": "Import task is still in progress. Please wait for completion."}`
  and nothing is stored (Django rolls the configuration back).
- Import setup with a ready SUCCESS row: the stored result decides the association. A dict whose
  `success` key is present and falsy answers 500 `Import failed: <error or 'Unknown error'>`. Its
  `sport_association_id` (or, when falsy, `association_id`) is looked up in
  `SportAssociation.original_objects` (deleted rows included): unknown is 500
  `Association <id> not found after import.`. Without an id, or with a result that is not a dict,
  the last association by primary key is linked. The configuration is created with provenance
  `import`, `onboarding_completed_at` set and the primary association, then saved; no audit
  entry is written (the model is not registered). Response as for the fresh setup, with the linked
  association's id.
- The singleton check failing inside the transaction answers 500 with its message.

## reconfigure

- 404 `{"success": false, "error": "Instance not configured"}`, then 403
  `{"success": false, "error": "Only the instance owner can reconfigure"}` for anyone but a superuser
  or the primary association's user, then the JSON body.
- `InstanceReconfigureSerializer(partial=True)`: nested `oem`, `oauth`, `stripe` with only the given
  keys; `display_settings` any JSON but null; `meta_title` (255), `meta_description`, and the three
  `show_*` booleans. Any `oauth` data sets both client ids (a missing one becomes `''`); `stripe` is
  validated and never stored. `save(update_fields=...)` always includes `updated_at`, so `{}` saves
  only that. An empty (falsy) `display_settings` is stored as the defaults by
  `InstanceConfiguration.save`. Response `{"success": true, "message": "Configurazione aggiornata"}`.

## logo and admin/logo

- `request.FILES['file']` (the last part named `file` with a filename): missing is 400
  `No file provided`; the part's lowercased media type must be PNG, JPEG or WebP; more than 5 MiB is
  400 `File too large. Maximum size is 5MB.`.
- Every `instance/logo.{png,jpg,svg,webp}` object is deleted, then `instance/logo.<ext>` is saved
  with the part's content type and `AWS_S3_OBJECT_PARAMETERS` (`Cache-Control: max-age=86400`, the
  default ACL when set). With a configuration, `logo_path` becomes `/api/instance/logo.png`
  (`save(update_fields=['logo_path', 'updated_at'])`) and `logo_url` is the configuration
  serializer's versioned URL; without one it is `/api/instance/logo.png`. A storage, database or
  serializer failure answers 500 `{"success": false, "error": "Failed to save logo"}`.
- `admin/logo` adds `IsInstanceOwner` (403
  `Only the instance owner or an administrator can administer this installation.`).

## Not ported (Django keeps these)

Go answers the server error page before any write:

- `configure` with `type: import` whose task row is `FAILURE` or `REVOKED` (Celery rebuilds the
  exception and Django answers its text), whose row Celery would decode differently (non-`utf-8`
  encoding, metadata with `status` or children, `__type__` values) or not at all, whose association id
  is not a string UUID, or whose error value is not a string, integer, boolean or null.
- Values the model columns reject inside Django's transaction: an owner email or support email over
  254 characters.
- A `domain` or owner email domain with non-ASCII characters (Python's Unicode lowercasing), and
  `clientPortal` values that need URLValidator's IDNA fallback.
- A logo name that exists again after the deletes when `AWS_S3_FILE_OVERWRITE` is false (Django
  would save under a generated alternative name).
- Other exceptions inside the setup transaction (Django answers 500 with the exception text).

## Evidence

`TESTS/fixtures/instance_setup_http.json` has 86 cases, from
`TESTS/fixtures/generate_instance_setup_http.py`. The runner owns a private MinIO for the whole run
and switches Django's default storage to S3 on it; both servers get the same `AWS_*` settings and
`INSTANCE_SETUP_TOKEN` (a placeholder). It restores Django's default password hashers for the
served Django (the contract settings prefer MD5 for fixture passwords). Phase A runs without a
configuration (token checks, uploads with the token, configure validation, a pending import, the
fresh setup); the runner then signs a token for the created owner and phase B runs on the created
configuration. After every case the complete S3 listing is compared, and every write states the
complete managed fields. The fresh setup is checked row by row: the owner's password with Django's
`check_password` and the default hasher's algorithm and iterations, every other field against its
model default, and the five audit entries against auditlog's create diff of the stored rows.

- RED (`native-r1`): Django 86/86, Go 404 on the first case.
- GREEN (`versioned-r1`): 86/86 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with
  exact row, audit and storage effects.
- Mutation (`mutation-r1`): storing an empty `display_settings` as given instead of the defaults
  fails `instance-setup-reconfigure-setup-owner` on `state.display_settings`.
- Re-run with the shared changes (`setup-rerun-r1`): instance-admin 141/141 and
  association-transfer 146/146 on native Django and Go; `go test ./internal/... ./pkg/storage/instancelogo/`.

v2 admin change (import setup): 99 cases; 13 import cases before the fresh setup, each linked
configuration checked field by field and removed by the runner. RED (`v2-admin-red`): Django 99/99,
Go 500 on `instance-setup-configure-import-linked`. GREEN (`v2-admin-green`): 99/99 on the four
transports. Mutation (`v2-admin-mutation-association-key`): ignoring the `association_id` key fails
`instance-setup-configure-import-association-id-key` on `import.primary-association`.
