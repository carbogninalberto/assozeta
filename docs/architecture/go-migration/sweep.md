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
