# Balance-sheet accounts and transfers list — implementing with HTTP parity

`GET balance-sheet/accounts/list` and `GET balance-sheet/accounts-transfer/list`
(`balance_sheet_accounts_list` and `balance_sheet_accounts_transfer_list` in
`BE/application/views/balance_sheet_views.py`) are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/balance_accounts.go`).
Django remains the production execution owner. Account and transfer writes and
the balance sheet itself are not ported yet.

## Behavior

- **Identity:** `IsAuthenticated` and `IsAssociationUser` (association or
  collaborator role, else `403 You do not have permission to perform this
  action.`); the registry maps the paths to `bookeeping.management.accounts.read`
  and `bookeeping.management.accountstransfers.read`; administrators need
  impersonation; neither view is an athlete view. `request.user` is the scope
  user; a non-association role reaching the view gets `403 {"msg": "Info not
  available."}`. `SportAssociation.objects.get(user=request.user)` (a missing
  row is a 500).
- **Accounts:** with `related` exactly `true` (the default),
  `CustomAccountSerializer` ordered by `editable` only (ties unordered):
  `current_balance` is the initial balance plus incoming minus outgoing
  transfers plus paid payments (expenses negative) through `Payment.objects`
  (live, `X-Group-Id`-filtered), returned as a Decimal that DRF's encoder
  renders as a float; `deletable` is editable with no transfers and no payment
  in that manager. Any other `related` value returns raw dictionaries,
  unordered, with `initial_balance` as a float.
- **Transfers:** `CustomAccountTransferInfoSerializer`, unordered: both accounts
  as `{custom_account_id, name, account_type, account_code}` or null, `amount`
  as a two-decimal string, `date` in DRF UTC form.

## Go implementation

One query per list (plus the association lookup): balances are summed exactly
in `numeric`, then rendered with Python's float repr.

## Evidence (TDD)

`TESTS/fixtures/balance_accounts_http.json` (generator alongside) authors 25
cases covering transfers in both directions and without an account, paid,
unpaid, expense, deleted and grouped payments, and a non-editable account.
RED: Django 25/25 on the first run and Go 404 before any Go code. GREEN: native
Django, native Go, Caddy `/api/v1` and `/api/v2` pass 25/25 with the model state
unchanged; counting unpaid payments fails the owner case.
