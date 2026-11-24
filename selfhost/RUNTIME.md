# Choosing the application runtime

## Fresh installations

Django is the default. Its API, Celery worker, Beat scheduler and realtime relay
serve the whole instance. Switching to Go moves those responsibilities to the
Go application services. PostgreSQL, Redis, object storage, the PDF renderer and
the web proxy serve both modes.

## Existing installations

During an upgrade from a mixed installation, the updater reads the instance's
saved API setting. It selects Django for `v1` and Go for `v2`. If the setting
is missing or unrecognised, it selects Django. A browser's cached preference has
no effect on this choice.

Before backup or replacement, the adoption preflight checks whether the target
can take over outstanding work. If it reports blockers, the upgrade stops and
the old release keeps running. Resolve the reported items before retrying the
upgrade. Once adopted, the instance keeps its effective runtime across upgrades.

## Migrations

A runtime switch never runs schema migrations. Both runtimes use the installed
release's schema. To apply schema changes from a newer release, use the normal
upgrade, which takes a backup before migrating.

## Reversal

From the installation directory, run `./bin/assozeta runtime-switch django` to
return the instance to Django, or `./bin/assozeta runtime-switch go` to select
Go again. Each command hands current work to the requested runtime.

If a switch is interrupted, run `./bin/assozeta runtime-recover`. The updater
uses the saved operation and database ownership record to resume recovery,
including when both application APIs are down. Run `./bin/assozeta status` to
check the effective runtime, services and readiness.
