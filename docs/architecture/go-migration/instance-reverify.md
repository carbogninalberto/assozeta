# Instance routes — re-verification after the Django main merge

The merge of Django `main` (`0efe8f0e..e1e81f3a`) changed `BE/instance/urls.py`, `backups.py`,
`updater_client.py`, `tasks.py`, `core/asgi.py`, `core/middleware.py`, `core/settings.py` and
`core/celery.py`. Every ported `BE/instance/urls.py` route and `cleanup_data_restores` went back to
review. What changed for them:

| Entries | Django change | Result |
| --- | --- | --- |
| `admin/backups` | GET adds `schedule`, `actions`, `remote`; new POST (manual backup) | Go updated, see [instance-backups](instance-backups.md) |
| `admin/backups/download-token` | `password_refusal()` extracted, same behaviour | unchanged, re-run green |
| `admin/backups/<name>/download` | none | unchanged, re-run green |
| `admin`, `admin/diagnostics`, `admin/email`, `admin/integrations/<provider>`, `admin/restarts`, `admin/updates` | none | [instance-admin](instance-admin.md) re-run green |
| `admin/email/test`, `admin/integrations/<provider>/test`, `admin/releases` | none | [instance-outbound](instance-outbound.md) re-run green |
| `admin/logo`, `configure`, `reconfigure`, `logo`, `setup-token/validate` | none | [instance-setup](instance-setup.md) re-run green |
| `admin/bakney-pairing` | none (its beat entry moved to `core/celery.py`) | [sso-pairing](sso-pairing.md) re-run green |
| `admin/data-restore*` (4 routes) | none | [data-restore](data-restore.md) re-run green |
| `access`, `config`, `status`, `manifest.json` | none | [instance-bootstrap](instance-bootstrap.md) re-run (Django and Go) |
| `logo.png` | none | [instance-logo](instance-logo.md) re-run (Django, Go, Caddy v1/v2) |
| `cleanup_data_restores` | none in the task; the beat entry moved from settings to `core/celery.py` (the Go worker does not schedule) | [restore-cleanup](restore-cleanup.md) re-run |

The middleware change only skips the eager body read for `/instance/admin/backups/upload`, and the
ASGI wrapper only touches POSTs to that path, so no other route sees a different request.

## Shared Go changes

Kept additive for the other new backup routes (item, restore, schedule, upload, remote):
`updaterrunner.Client.CallPayload` (a JSON body on `call_runner`), `service.InstanceBackups.Relay`
(`relay()`), `repository.InstanceBackups.AppendAudit` (`audit()`, any action and extra
`additional_data`), and `InstanceBackupsReply.Data`/`Relayed` to render a runner body as it is.

## Evidence

Receipts are under `quality-reports/go-migration/instance-reverify-*` (gitignored):

- `backups-red` (Django 161/161, Go fails), `backups-green` (161 × 4), `backups-mutation` (CAUGHT).
- `admin-green` 141 × 4, `setup-green` 86 × 4, `outbound-green` 106 × 4, `sso-pairing-green` 172 × 4,
  `data-restore-green` 139 × 4.
- `bootstrap-django` and `bootstrap-go`: 128 cases each.
- `logo`: 76 cases on Django S3, Go, Caddy v1 and v2 (64 on the Django disk injection).
- `cleanup`: 7 worker-process scenarios against the actual Django task.
