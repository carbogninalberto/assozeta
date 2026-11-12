---
name: go-port-family
description: Port one Django route family of the Assozeta backend to Go with proven HTTP parity (TDD - authored cases pass on Django first, then on native Go and Caddy /api/v1 and /api/v2, plus a mutation control), using tools/go-migration/family.py and the shared contract/harness/Go helpers. Use for any "port/reimplement <route> in Go" or Go migration family work on the codex/go-backend-parity branch.
---

# Port one route family to Go

A family is the routes of one Django view/viewset area that share fixtures (for example
`course/locations` add, update and delete). The bar is exact parity: status, headers,
body, the complete database state after every case, auditlog entries and their index rows,
on four transports. Never weaken a check to make Go pass; change Go.

Read `reference.md` (same folder) before authoring: it lists the Django/DRF behaviours
already characterized and the Go helpers to reuse. Do not re-derive them from old families.

## Commands (repository root of the Go worktree)

```
T=tools/go-migration/family.py
$T status                       # completion and uncharacterized routes by prefix
$T inspect 'course/list'        # ledger entry, registry permission, full view source
$T scaffold course-list --route 'course/list' --doc course-list.md
$T run course-list              # native Django + Go; first run is RED (Go 404)
$T run course-list --versioned  # adds Caddy /api/v1 and /api/v2: the GREEN evidence
$T mutation course-list --file BE_GO/internal/... --old '...' --new '...'   # must print CAUGHT
$T record course-list --spec /path/spec.json   # ledger entries, receipts, inventory test
```

`run` prints one JSON line: `success`, the first `errors` entry (`case`, `check`,
`actual_status`) and per-transport case counts. Reports land in
`quality-reports/go-migration/<family>-http-<label>/` (gitignored). In another worktree set
`GO_MIGRATION_PYTHON` to the main worktree's `quality-reports/go-migration/python31316/bin/python`.

## Workflow

1. **Inspect** the routes (`inspect`), then read only what the view calls: serializer, model
   save/delete overrides, managers, signals (`grep -rn "sender=Model"`), registry permission.
2. **Author the sequence** in `TESTS/fixtures/generate_<family>_http.py` (run it with
   `python3 -I`). Cover: anonymous 401, admin without session, restricted collaborator
   (registry message), owner scoped to an athlete, wrong method 405, OPTIONS, validation
   errors (every field rule), not-found/foreign/deleted/group-hidden rows, athletes and
   owners without an association, each success path with its effect, and the odd paths
   the code really has (non-atomic saves, viewset DELETE routing, ignored fields). Expected
   bodies are authored from reading Django, not copied from a run.
3. **Contract** (`TESTS/contracts/test_<family>_http.py`): only the family's Allow set and
   `new`/`now`/`unordered` options for `http_family.assert_case`.
4. **Adapter** (`TESTS/adapters/django/<family>_http.py`): `SOURCES` (every Django file the
   behaviour depends on, plus `harness.SOURCES`), `seed_rows(source, created)`, and for writes
   an effect check using `family_harness` (`require_unmanaged_unchanged`, `audit_delta`,
   `check_audit_event`, `pairs`). Reads pass no effect: every case must leave state unchanged.
5. **RED**: `run` must pass Django N/N and fail Go. If Django fails, the authored
   expectation is wrong: fix it from the source (to see the real body, temporarily dump
   `actual` in the contract to a scratch file and remove the dump afterwards).
6. **Go**: `domain`, `dto` (validation), `repository` (SQL, audit), `service` (Django's order of
   operations), `handler` (dispatch, error mapping), route in `cmd/server/main.go`. Reuse the
   helpers in `reference.md`; `gofmt`, `go build ./...`, `go vet`, `go test ./internal/...`.
7. **GREEN**: `run` natively until green, then `run --versioned` (four transports).
8. **Mutation**: one realistic semantic mutation (drop a filter, make a save atomic, swap
   a status) via `mutation`; it must be CAUGHT and the file is restored automatically.
9. **Re-run families whose shared code you touched** (`run` each; they run concurrently).
10. **Record**: write `docs/architecture/go-migration/<area>.md` (behaviour summary, what is not
    ported, evidence), then `record` with a spec (see `family.py record --help`); it validates
    RED/GREEN/mutation receipts and runs `TESTS/inventory`.
11. **Commit** only the family's files plus shared files you changed (never the unrelated
    dirty docs in the worktree), following the git-commit-as-me skill (identity check,
    `scan-secrets.sh`, message: what Django does, then the TDD evidence). Push to
    `codex/go-backend-parity`.

## Quality gates (all required)

- Django passes every authored case before any Go code exists (RED).
- GREEN on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact effects.
- A mutation control is CAUGHT, and every family sharing touched code is re-run green.
- Not-ported behaviour is refused explicitly in Go before any write (never a silent partial
  port) and listed in the doc and ledger notes.

## Parallel work

Families can run concurrently (each run owns its database and ports). For parallel agents,
give each one family, its own git worktree and branch from `codex/go-backend-parity`, and
the instruction to stop after step 9 and return its spec JSON; the coordinator merges branches
one at a time (re-running `go build` and the merged families), then runs `record` and commits.
`main.go` and `ledger.json` are the only shared files that conflict.
