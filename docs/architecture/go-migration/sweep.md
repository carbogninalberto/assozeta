# Family sweep

The sweep runs every HTTP family (`TESTS/fixtures/*_http.json`, on native Django, native Go, Caddy
`/api/v1` and Caddy `/api/v2`) and every process harness (`TESTS/adapters/go/*_process.py`) on one
commit. A job passes on exit 0 with a last stdout line containing `"success": true`.

## Locally

```sh
python tools/go-migration/sweep.py --label L                       # everything, 2 jobs at a time
python tools/go-migration/sweep.py --label L --families association-list,balance-archived
python tools/go-migration/sweep.py --label L --only process
python tools/go-migration/sweep.py --label L --shard 3/16          # every 16th job, starting at the 3rd
python tools/go-migration/sweep.py --label L --resume              # re-run only what did not pass
```

It needs a clean tree and the migration interpreter (`GO_MIGRATION_PYTHON`, default
`quality-reports/go-migration/python31316/bin/python`). Results go to
`quality-reports/go-migration/sweep-L/`: `summary.json` (`summary-shard-I-of-N.json` for a shard) and one
log per job. Family receipts are in `quality-reports/go-migration/<family>-http-sweep-L/`.

`python tools/go-migration/merge_sweeps.py --label L` joins the shard summaries into `summary.json`. It
exits 1 and lists the problems when a shard or a job is missing or a job failed.

## Faster local runs

Family runs (`family.py run`, `sweep.py`, and the driver they call) use one shared PostgreSQL server per
machine. Each run creates its own database, `assozeta_contract_run_<time>_<pid>_<hex>`, and drops it at
the end, also on failure. When a migrated template `assozeta_contract_tpl_<key>` exists the run clones it
instead of migrating; the key hashes the Django and Go migration sources, the models, the settings and the
Django and Python versions, so the first run after a migration, model or settings change builds the
template and later runs clone it. Django `migrate` and the Go migrations still run on every database, where
they are no-ops on a clone and still assert the full history. The Go server, the Go migrate tool and the
image codec binaries are reused from a content-addressed cache that keeps the 12 most recent entries.

Private per run: Redis, and the standalone harnesses. The process harnesses in
`TESTS/adapters/go/*_process.py` and the other standalone harnesses never call `activate`, because they
build schemas with `run_syncdb`, read their own server log, or need a fixed database name. Concurrent runs
retry when another run took their Redis or Go server port, and the instance-admin diagnostics API stub
publishes its ephemeral port as `ASSOZETA_DIAGNOSTICS_API_URL`.

Defaults: server directory `$XDG_CACHE_HOME/assozeta-test-postgres` (or `~/.cache/assozeta-test-postgres`),
binary cache `$XDG_CACHE_HOME/assozeta-test-binaries` (or `~/.cache/assozeta-test-binaries`).

| Variable | Effect |
| --- | --- |
| `ASSOZETA_TEST_PRIVATE_POSTGRES=1` | opt out: a private server per run, as before |
| `ASSOZETA_TEST_SHARED_POSTGRES` | use (or create) the shared server in this directory |
| `ASSOZETA_TEST_BINARY_CACHE` | directory of the Go/codec binary cache |

```sh
python tools/go-migration/shared_postgres.py start             # start (and initialise) the server
python tools/go-migration/shared_postgres.py status            # running, run databases, templates
python tools/go-migration/shared_postgres.py cleanup           # drop runs older than 6 hours (--hours N)
python tools/go-migration/shared_postgres.py cleanup --templates  # also drop templates; the next run rebuilds them
python tools/go-migration/shared_postgres.py stop              # stop it between sessions
```

`cleanup` also drops databases left by a process that no longer exists. The server keeps running between
runs, so nothing has to be started by hand. Each family `results.json` records `postgres_mode` (`shared` or
`private`), `shared_postgres` (`from_template`, `database_dropped`, `template_published`) and
`phase_seconds` per measured phase.

Measured locally (loaded machine, private -> shared): association-list 38.6 s -> 11.0 s, with Django
`migrate` 22 s -> 0.3-0.7 s on a template clone; payment-writes 113 -> 98 s; course-calendar 159 -> 111 s;
subscription-create 645 -> 598 s, dominated by its cases rather than by setup.

GitHub Actions runners use the same default: the workflows set none of the three variables.

## On GitHub Actions

`.github/workflows/go-family-sweep.yml` plans the shards, runs them as a matrix and merges the result.

```sh
gh workflow run go-family-sweep.yml --ref BRANCH                    # all jobs, 16 shards, label ci-<short sha>
gh workflow run go-family-sweep.yml --ref BRANCH -f families=association-list,balance-archived -f shards=2
gh workflow run go-family-sweep.yml --ref BRANCH -f shards=24 -f label=rc1
```

Pushes to `go/ci-family-matrix` and `go-ci/**` also start it: three small families on two shards, or the
whole suite when the head commit message contains `[full-sweep]`. A new run on the same ref cancels the
previous one.

The runners install the requirement files with `TESTS/adapters/django/constraints-parity.txt` as a pip
constraint, which pins the transitive dependencies to the versions the fixtures were recorded with. Python
and Go both read the host's `mime.types` files to guess an upload's content type, so the runners replace
theirs with `TESTS/fixtures/mime.types`, the table the fixtures were recorded with (macOS ships the same
one as `/etc/apache2/mime.types`). The production image has no such file, so there Django falls back to
Python's built-in table, which lacks some of these types (`.xlsx`, `.sql`).

The `Merge shards` job writes the result table to the run summary, fails if any job failed or is missing,
and uploads one artifact, `family-sweep-<label>`, holding the merged summary, the logs and the receipts.

## Promoting from a CI run

Check out the commit the run used, unpack the artifact into `quality-reports/go-migration/` and pass the
merged summary to `promote_verified.py`:

```sh
git checkout SHA
gh run download RUN_ID -n family-sweep-LABEL -D quality-reports/go-migration
python tools/go-migration/promote_verified.py --sweep quality-reports/go-migration/sweep-LABEL/summary.json
```

Add `--apply` to write the ledger once the dry run looks right.
