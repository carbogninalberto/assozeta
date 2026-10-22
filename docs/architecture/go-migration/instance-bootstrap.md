# Instance bootstrap and manual availability

Go now registers `GET`, `HEAD` and `OPTIONS` for `/instance/status`,
`/instance/config` and `/instance/access`. The composition root supplies an
explicit repository, public DTO service and offline manual availability loader.
The existing versioned proxy exposes these under `/api/v2`; ordinary installations
continue selecting Django. This increment does not implement instance setup,
administration, logo/manifest serving or manual reader/search/assets.

Status and config preserve Django's public authentication bypass, including
ignored malformed Bearer and impersonation headers. Access authenticates the
actual actor before scope and method checks. Ownership follows the configured
primary association's owner foreign key; a collaborator's association membership
does not substitute another actor. Missing configuration, non-self-hosted mode,
inactive/deleted accounts and administrator exceptions retain their declared
responses. Session cookies remain separate from API identity.

The public projection preserves display defaults, legacy OAuth fallback, live
integration overrides, registration flags, null/missing values and malformed
configuration errors. SQL selects only public provider settings, including
historically accepted pair-array forms with last-key-wins behavior. Private
provider values are excluded before the snapshot enters Go. Canonical logo and
manifest URLs remain `/api/instance/...`, with explicit Django coexistence.
Locale headers survive early restore/CORS responses on these three paths.

The source-authored fixture passes 86 cases against actual Django ASGI and the
compiled Go server, using real JWT credentials, PostgreSQL and Redis. Cases cover
role/scope denial, public bypass, method/negotiation errors, session isolation,
restore admission, live configuration edits and secret canaries. Managed data
does not change; stale password-hash sessions retain the existing cache-revocation
behavior. The missing-route, malformed-CORS locale and internal pair-array secret
failures were reproduced before their fixes.

Four PostgreSQL groups verify secret projection and measured plans at 100,
1,000 and 10,000 configurations. Public body reads use one
SELECT; authenticated access uses two including persisted actor lookup. Actual
plans return one row with 4/6/6 shared buffer hits at those fixture sizes. JSON
projection cost still depends on settings size; this is not a latency or constant
cost claim. Current-model `syncdb` fixtures do not prove historical migrations.

The reviewed source handoff and receipts are under ignored
`quality-reports/go-migration/instance-bootstrap-go/` in the isolated bootstrap
worktree; root integration records exact copied hashes under
`quality-reports/go-migration/bootstrap-integration/`. Browser acceptance and a
genuine required-release manual image remain pending evidence at this checkpoint.

## Manual package boundary

Availability preserves the existing release-package, configured corpus URL and
development/runtime fallback precedence. It does not fetch a remote corpus or
equate availability with a working reader. Forty-seven authored cases compare the
actual Django helper with Go. Image-owned version/revision and required-package
metadata retain their authority over environment fallback.

The separate `check-manual` command validates release identity, evidence receipt,
index and PNG asset paths/hashes/header checks. Nineteen actual Django/Go command
cases cover valid packages, optional absence, present invalid packages, required
failures and the empty-root working-directory rule. Availability and strict
package validation deliberately have different asset-validation behavior, matching
their existing Django callers.

Release builds download the same verified artifact into both backend contexts.
The Go image packages the validator, normalizes the bundle to root-owned readable
and non-writable files, and runs the required gate as UID 10001. Server, worker,
scheduler, migrator, codec, licenses and health configuration remain present.
Five host tests execute metadata/permission and gate-invocation fragments. The
actual non-root image also passes all nineteen checker cases with read-only
storage and no network; image-owned metadata, binary hashes, package permissions
and fixture cleanup are verified. The image is
`sha256:5ed0dc50dd1397c568897cb66891b83bcd576c4f15dfaed9240dfcd4f8875450`,
built from the explicitly dirty `9a090cc` snapshot recorded in the integration
manifest. This synthetic checker proof uses an optional empty image package; the
genuine required-release artifact rehearsal remains pending. The Django image
gate remains unchanged.

Candidate lifecycle validation now checks both packages before managed files,
environment or a pending recovery snapshot can change. CLI install/start/update
uses the Go check only when the source Compose configuration declares `api-go`;
a Compose inspection error fails admission. Go always requires its metadata
marker and packaged checker. The historical Django marker exemption remains
limited to its existing versions, and explicit development images keep their
optional-package semantics. Nineteen shell/CLI checks, fourteen distribution
checks and the full 174-test self-host suite pass. Explicit failure propagation
also preserves rejection when the caller uses a shell conditional, which disables
implicit `errexit` handling inside the function. Three actual image-helper cases
verify optional success, wrong-release rejection and required-package absence;
the derived invalid fixture image is removed. The full update/recovery matrix
must still be rerun with this additional Go gate after main alignment.
