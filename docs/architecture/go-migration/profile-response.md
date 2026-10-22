# Collaborator personal-profile response

The unchanged frontend replaces cached `userData` with the successful
`PATCH /profile/update` response. Login and profile-info already include the
connected owner's association for collaborators; the old update response omitted
it. A permitted collaborator could save personal data successfully, then reload
into a null dereference at `Account.svelte`'s organization fields. The real
Django browser failure was recorded before this correction.

Django and Go now add the existing allowlisted association projection and its
compressed logo to a successful collaborator update response. Personal fields
still belong to the selected collaborator, including during owner impersonation.
The existing owner-only feature metadata, authorization, mutation rules, and audit
attribution are unchanged. Collaborator-supplied association changes remain
ignored. This intentionally corrects the old response contract; it does not claim
byte parity with the defective legacy response.

The response uses the connected owner read under the existing personal-row lock.
It must match the admitted tenant; association-scoped impersonation also retains
its existing association constraint. A concurrently reassigned collaborator gets
a sanitized post-commit 500 without reading or disclosing the new tenant. The
personal mutation and its audit remain committed, preserving the existing
response-serialization transaction boundary. This is not a rollback or a retry
promise. Normal missing-permission requests still fail before mutation.

No login builder is called: response construction does not create settings,
membership-card configuration, tokens or other state. The public association
serializer/projection remains unchanged; credential and private integration fields
are not added. The frontend, login and profile-info routes remain unchanged.

## Query and effect budget

| Collaborator response path | Previous | Corrected |
| --- | --- | --- |
| Django added association reads | 0 | 2 SELECTs: association joined with owner, groups |
| Go complete response business reads | 1 | 3 SELECTs: person, association joined with owner, groups |
| Go complete response shared-fence protocol reads | 1 | 2 |
| Go complete response total reads | 2 | 5 |
| Go mismatched admitted owner | no association projection | 2 personal snapshot/fence reads, zero association reads |
| Personal mutation locked-row read | existing columns | adds `connected_user_id`, no additional statement |

These are response-layer budgets, excluding authentication, permission admission,
mutation and audit statements. All tenant groups are still returned; statement
count is fixed while response size scales with those groups. The no-op response
performs no writes. Existing person/association audit failure rollback and
concurrent-write behavior remain covered by the focused profile adapter.

## Verification

Run `python -m TESTS.adapters.django.accounts --only profile-update` with the
project's supported Django environment. It runs actual Django and Go HTTP
contracts on private PostgreSQL/Redis, durable audit/index and whole-model
snapshots, five locked-row concurrency scenarios per backend, Django no-op query
checks, and both mandatory `profileupdateintegration` PostgreSQL groups. The new
Go group measures 0/16/128 added tenant groups with 33 unrelated groups, plus
scope denial and the locked connected-owner projection. No cases are skipped.

Focused Go unit tests cover roles, all registration-flag combinations, missing or
foreign connected owners, impersonation association mismatch, immutable admission
snapshots and post-commit personal-field preservation. The unchanged-UI browser adapter also passes against both API versions: wrong
and correct password login, permitted personal edit, ordinary reload, server
readback and a second fresh password login. It verifies exact durable personal
and audit changes, unchanged owner/password data, export denial and actual health
and notification WebSockets. Both runs use the same 560-file immutable frontend
artifact; thirteen workflow source maps match the inspected frontend source.
Before bootstrap implementation, the pinned Go source returns a real status 404
and cannot reach the login UI, while the corrected Django control passes.

Run `python TESTS/adapters/django/profile_browser.py --help` for artifact preparation
and execution options. This fixture routes only the named workflow to each API;
canonical manifest/logo/export denial and root WebSockets remain real Django
coexistence. It uses current model fixtures, not historical migrations. External
provider scripts/fonts are blocked, but application requests and authentication
are real. The evidence does not establish complete frontend parity, installed
backend switching or worker ownership transfer.

## Manual evidence dependency closure

Changing `BE/application/views/profile_views.py` changes the file hash consumed
by the following six reviewed recipe modules and sixteen authored-workflow
modules. Existing sealed artifacts must not be relabelled as evidence for the new
source. Review the affected bindings and capture dependencies, then generate a
fresh verified release package. Whole-file invalidation does not imply every
listed workflow changed behavior. The personal profile/organization settings
response requires fresh relevant workflow evidence. No manual revision or source
hash was silently refreshed by this correction.

| Binding module under `docs/manuale/` | Authored capture workflows directly declaring this source |
| --- | --- |
| `automatic-attendance-authored-workflows.mjs` | `attendance-automatic`, `attendance-carnet-variants`, `dashboard-display` |
| `core-legacy-authored-workflows.mjs` | `receipts-approve-download`, `receipts-edit-delete` |
| `course-settings-authored-workflows.mjs` | `course-installments-manage`, `settings-receipts-card` |
| `dashboard-lists-authored-workflows.mjs` | `dashboard-lists` |
| `dashboard-recipes.mjs` | Reviewed recipe source binding |
| `fiscal-settings-authored-workflows.mjs` | `fiscal-year-settings` |
| `medical-followups-authored-workflows.mjs` | `medical-followups` |
| `member-authored-workflows.mjs` | `members-filter-export`, `members-fee-plan-selection` |
| `membership-links-cards-authored-workflows.mjs` | `membership-links-cards` |
| `organization-access-recipes.mjs` | Reviewed recipe source binding |
| `organization-additional-reference-recipes.mjs` | Reviewed recipe source binding |
| `organization-basics-authored-workflows.mjs` | `organization-settings`, `collaborator-permissions`, `registration-default-category`, `organization-document-templates` |
| `organization-maintenance-authored-workflows.mjs` | `archive-maintenance`, `collaborator-removal` |
| `payment-classification-authored-workflows.mjs` | `payment-classification-local` |
| `payment-maintenance-authored-workflows.mjs` | `payments-maintenance` |
| `payment-recipes.mjs` | Reviewed recipe source binding |
| `registration-forms-recipes.mjs` | Reviewed recipe source binding |
| `registration-initial-authored-workflows.mjs` | `registration-forms-manage` |
| `settings-additional-reference-recipes.mjs` | Reviewed recipe source binding |
| `settings-print-authored-workflows.mjs` | `settings-print`, `settings-signup-checkout` |
| `stripe-local-authored-workflows.mjs` | `stripe-local-settings-filters` |
| `two-factor-login-authored-workflows.mjs` | `account-two-factor-login` |

The closure above is source-derived from this reviewed snapshot: 22 modules and
26 directly declared authored workflows. Transitive release selection and newly
aligned main-branch capture policies must be evaluated by the release assembler;
this table does not authorize reusing old screenshots or bypassing its gates.
