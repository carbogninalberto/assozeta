# Carried release manual

A release published without `include_manual` reuses the verified manual of the
previous `:latest` release. The publish workflow copies that package unchanged
into both backend build contexts and passes its origin as `MANUAL_CARRIED_FROM`
(`<release> <revision>`, read from the package's `bundle.json`). Both Dockerfiles
write it to `/app/MANUAL_CARRIED_FROM`, next to `/app/release-manual`, and both
images then require the manual (`REQUIRE_MANUAL` is true for a fresh verified
artifact or a carried origin). The Go image takes the package from the Django
`:latest` image, so both images ship identical bytes.

## Django behaviour (`BE/application/manuale/bundle.py`)

- The marker is read from the bundle path's lexical parent, only when the bundle
  directory exists. It is read before the placeholder check, so a malformed marker
  fails even an optional placeholder package; an absent bundle ignores it.
- A missing marker, a non-regular file (directory, broken symlink) or one with only
  white space means no carried origin. Fields split on Python white space, which
  includes the ASCII information separators. Any other content must be exactly two
  fields with a 40-character lowercase hexadecimal revision; invalid UTF-8, an
  unreadable file or a byte order mark before the release fails.
- With an origin, every package check (index metadata version, receipt revision and
  release) uses the origin instead of the running revision/release. Asset checks are
  unchanged. The loaded index is then accepted for the running version.
- Availability, the reader (`/manuale/sections`, assets), search, section and
  evidence tools report the package's own metadata: `version` and
  `application_revision` stay those of the origin release.
- `get_manual_gaps` compares the running context with the metadata, so a carried
  package reports `version_status: incompatible` and `snapshot_status: stale`.
- A marker naming a different origin than the package, or a malformed marker, makes
  the bundle invalid: availability is false, reader/tools return the existing
  unavailable responses, assets 404 and the image gate fails.

## Go

`pkg/manual/carried.go` reads the marker with pathlib's parent and `is_file`
semantics and Python's split. `loadBundleOrigin` validates the package for the
origin and reports it as carried; `loadKnowledge` then skips the running-version
check for that package only. `CheckBundle`, `Available`, `LoadPublicBundle`, the
reader, assets and all four tools share this loader, so `cmd/check-manual` and
the server accept the same packages as Django. No configuration is added: the
marker location follows the bundle path, as in Django.

## Evidence

All suites were authored from the Django source and ran on Django first. Go failed
them before the change (`carried-valid` in the bundle CLI, two availability cases,
`carried-search` in the tool library and `carried-sections` over HTTP).

- `TESTS/adapters/django/manual_bundle.py`: 45 cases (26 carried) against the
  actual Django CLI and the built Go `check-manual`.
- `TESTS/adapters/django/manual_availability.py`: 53 cases (6 carried).
- `manual_reader_http.py --tools` with `--go-source` and `--backend go`: 74 cases
  (7 carried), complete results identical.
- `manual_reader_http.py` native, `--go-source`, `--backend go` and both
  `--versioned` runs: 76 cases (7 carried: sections, self-hosted sections, asset,
  private asset, other origin, malformed origin, malformed-origin asset); the
  Django/Go full-body differential passes on all four transports.
- MCP stdio (Django and Go, 40 cases each), reader library, retrieval, asset scope
  and refresh-lock suites re-run green; `go test -race ./pkg/manual/...
  ./cmd/check-manual/... ./internal/config/...` passes.
- `selfhost/tests/test_go_manual_delivery.py` executes the Go Dockerfile marker
  commands and evaluates the workflow conditions: both backends carry, the
  destination is each image's own context and `REQUIRE_MANUAL` follows the same
  rule for both. `make selfhost-test` passes.
- Mutation: validating a carried package with the running revision instead of the
  origin revision (`s.Release, _ = release, revision`) compiles and is CAUGHT by
  the bundle CLI (`carried-valid`) and availability (`carried-bundle-valid`).

Not covered here: building and running the actual release images
(`TESTS/adapters/go/manual_image.py` consumes the same 45 bundle fixtures in CI).
