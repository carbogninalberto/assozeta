# Financial ownership reference correction

This bounded Django correction prevents cross-association mutations through
custom-account and account-transfer endpoints. It establishes an explicit safe
reference for the Go migration; it does not establish Go parity or verify an
endpoint family. All fourteen migration requirements remain in scope.

## Behavior

Account and transfer update/delete lookups now include the association resolved
from the authenticated effective owner. Foreign, absent and malformed target
identifiers return the same direct HTTP 404 JSON envelope:

- Accounts: `{"exception":"CustomAccounts matching query does not exist."}`
- Transfers: `{"exception":"CustomAccountsTransfer matching query does not exist."}`

Transfer creation and update accept source/destination accounts only from that
association. Serializer querysets default to empty without trusted context.
Partial updates validate retained references as well as supplied fields, so an
amount-only edit cannot retain a historical foreign account. Both nullable
references remain supported. `sport_association` remains in transfer responses
but is read-only in requests and supplied by the view on every write. The
account serializer already excludes association ownership.

Foreign or missing nested references return field-keyed HTTP 400 errors using
the existing DRF translations. The default Italian response contains
`Pk "<id>" non valido - l'oggetto non esiste.`; explicit English requests contain
`Invalid pk "<id>" - object does not exist.`. Both field errors appear when both
references are invalid. Existing actor permissions and collaborator-to-owner
resolution remain in force. A client group header cannot widen account scope.

Denied operations leave complete account, transfer, payment, installment,
audit-log and audit-index snapshots unchanged. The foreign-account fixture is
editable and has actual transfer/payment/installment relationships, so the
baseline deletion reaches the cascade path rather than being rejected by an
unrelated editable flag. This proves denial before those effects; it does not
establish the separate owned-account cascade policy.

## Parent verification

The coordinator executed real isolated PostgreSQL and Redis runs using the
production Python 3.13.16 interpreter and full Django migrations:

| Source and test scope | Result |
| --- | --- |
| Original `57e9bf284a44c0798a3c38c1b9bfe7225a54fbef`, 22 new ownership tests | Red: 20 failing test/subtest assertions and 8 errors; 0 skips |
| Corrected source, 22 ownership tests plus 42 existing balance-sheet tests | Green: 64 tests, 0 failures, 0 errors, 0 skips |

The fixture covers owned successes; foreign/missing/malformed targets; both
nested references; retained foreign references; nullable references; ownership
reassignment attempts; Italian/English errors; full and permissioned
collaborators; denied permissions; forged group headers; and durable/audit
invariance. A separate characterization records the unresolved historical list
exposure described below. It is not a security acceptance claim.

Receipts are retained locally under `quality-reports/go-migration/`:

- `financial-reference-red-r3/results.json`
- `financial-reference-green-r3/results.json`
- `parallel-scale-20261025/financial-reference-integration.json`

Both database runs verified source stability and removal of their owned
PostgreSQL/Redis resources. Integration compared every one of the green run's
417 backend source inputs against the integrated tree; all matched. The tests
are reproducible through Django's test runner with labels
`application.tests.test_balance_sheet_ownership` and
`application.tests.test_balance_sheet_views` in a disposable, fully migrated
environment. No production installation or customer data was used.

The implementation and tests are in
`BE/application/views/balance_sheet_views.py`,
`BE/application/serializers/balance_sheet.py`, and
`BE/application/tests/test_balance_sheet_ownership.py`.

## Remaining acceptance gaps

- Historical transfers may already contain foreign nested account references.
  The existing transfer list exposes their nested details. This remains an
  unresolved disclosure defect; this correction neither rewrites historical
  data nor approves reproducing that behavior in Go.
- Payment entry/update uses a separate writable account-reference serializer
  and requires its own bounded authorization correction.
- Owned-account deletion cascades, protected references, hooks, transactions,
  rollback and concurrency still require full Go/Django verification.
- This slice does not recharacterize non-object/multipart malformed requests,
  every locale, frontend workflows, query scaling or all financial behavior.

Go target mutations must enforce the same trusted target and nested-account
scope inside their explicit service/repository transaction boundaries. Existing
financial worker implementations remain isolated until they adopt the corrected
reference and pass independent differential and durable-effect checks.
