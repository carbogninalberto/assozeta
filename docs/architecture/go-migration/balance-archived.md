# Balance-sheet archived list — implementing with HTTP parity

`GET balance-sheet/archived` (`balance_sheet_archived` in
`BE/application/views/balance_sheet_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/balance_accounts.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** `IsAuthenticated` and `IsAssociationUser`; the registry maps the
  path to `bookeeping.management.balancesheet.read`; administrators need
  impersonation; not an athlete view. `SportAssociation.objects.get(user=...)`
  runs before the role check (a missing association is a 500; a
  non-association role gets `403 {"msg": "Info not available."}`).
- **Body:** despite the name, the association's balance sheets with
  `archived=False`, newest first, each `{data, creation_date, year, draft}`
  (`creation_date` is a raw datetime rendered by DRF's encoder, `draft` is
  `status_flag == 1`), plus `sport_association`: `SportAssociationSerializer`,
  ported earlier as Go's association profile.

## Evidence (TDD)

`TESTS/fixtures/balance_archived_http.json` (generator alongside) authors 12
cases. The embedded association object is checked by its authored key set and
ID, and the runner requires it to be identical across backends. RED: Django
12/12 on the first run and Go 404 before any Go code. GREEN: native Django,
native Go, Caddy `/api/v1` and `/api/v2` pass 12/12 with the model state
unchanged; including archived sheets fails the owner case.
