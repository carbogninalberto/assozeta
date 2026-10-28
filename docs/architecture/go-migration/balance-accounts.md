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

## Account add and update

`POST balance-sheet/accounts/add` and `PATCH balance-sheet/accounts/<uid>/update`
(`BE_GO/internal/{dto,service,handler}/balance_account_write*.go`, repository in
`balance_accounts.go`) keep the list's access rules (athletes get DRF's generic
403 from `IsAssociationUser`) and port `CustomAccountSerializer` validation:
`name` and `account_code` (CharField(100)), `initial_balance` (DecimalField(10,
2)), `account_type` (a ChoiceField over 1-3 compared as `str(value)`), `enabled`
and `editable` (a form body without them sends False unless partial,
`BooleanField.default_empty_html`, as the Django reference showed) and a
writable `custom_account_id` with its UniqueValidator. Errors are DRF's 400
field map. Add saves with the owner's association; update gets the
association's account (a malformed or foreign id is
`404 {"exception": "CustomAccounts matching query does not exist."}`; the
non-editable flag does not block updates). Both answer the serialized account
with the list's balance and deletable (group-aware payments). auditlog records
the create (the transfer relations' related_name accessors render as their
empty manager) and the changed fields, indexed directly. A new
`custom_account_id` on update would make `Model.save()` insert a copy; Go
refuses it with a 500 (not ported). Account delete (a cascading hard delete of
its payments) is not ported yet.
`TESTS/fixtures/balance_account_writes_http.json` authors 29 cases.

## Transfer add, update and delete

`POST balance-sheet/accounts-transfer/add`, `PATCH .../<uid>/update` and
`DELETE .../<uid>/delete` keep the same access rules and port
`CustomAccountTransferSerializer`: a writable `custom_account_transfer_id`
(UniqueValidator), `custom_account_from`/`custom_account_to` (nullable relations
whose queryset is the association's accounts, so another association's or an
unknown account is `Invalid pk "…" - object does not exist.`, a malformed one
the model field's `“…” is not a valid UUID.`), `amount` (DecimalField(10, 2))
and `date` (DateTimeField in UTC, default now). Add and update save with the
owner's association and return the serialized transfer; update and delete get
the association's transfer (otherwise, including a malformed id,
`404 {"exception": "CustomAccountsTransfer matching query does not exist."}`).
auditlog records the create and the changed fields (indexed directly) and the
delete (indexed through `changes_json`). The serializer's `validate()` also
rechecks the instance's stored accounts on a partial update that omits them;
Go does not repeat that recheck yet, so it differs only for a transfer whose
stored account belongs to another association (data these views never
write). `TESTS/fixtures/balance_transfer_writes_http.json` authors 30
cases; the Django reference passed them on the first run.
