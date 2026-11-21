# Instance runtime switch (handoff Phase 1)

`InstanceRuntimeView` (`instance/admin/runtime`, GET and POST) in `BE/instance/runtime.py` and the
Go handler `InstanceBackupActionsHandler.Runtime` (`BE_GO/internal/handler/instance_runtime.go`,
service `BE_GO/internal/service/instance_runtime.go`) expose the same owner-only submission and
status of an instance runtime switch. Both relay to the update runner, which owns the operation
(`selfhost/updater/runtime_switch.py`, journal kind `runtime_switch`) and the durable authority
(`public.assozeta_go_job_ownership` runtime/generation/operation_id, changed only by
`public.assozeta_commit_runtime_authority`, migration `000005_runtime_authority`).

## Behaviour

- Identity, then `IsInstanceOwner` on the authenticated actor, then method dispatch
  (Allow `GET, POST, HEAD, OPTIONS`). Every response carries `Cache-Control: no-store`.
- GET relays runner `GET /runtime` and returns its status and JSON verbatim.
- POST requires exactly `{target: 'django'|'go', expected_generation: int >= 1 (not bool),
  idempotency_key: UUID, password}`. Anything else is 400 `{error: 'Richiesta non valida.'}` before
  the password. Then the owner password confirmation shared with the backup actions and the Go tasks
  switch (same attempt counter and limit). Then runner `POST /runtime-switches` with
  `{request_id: canonical key, actor_id, target, expected_generation}` (30 s). The runner's
  200/202/400/409/503 status and body come back verbatim. A transport failure is 503 with the
  updater's unreachable text.
- Audit: one UPDATE entry on the instance configuration only when the runner accepted a new
  operation (202, `replayed: false`): changes `{runtime_switch: [previous, requested]}`,
  additional_data `{actor_id, operation, expected_generation}`. The password never reaches the
  runner, the audit or a response.

In Phase 1 the controller refuses a real switch at preflight (`switch_unavailable`). A request for
the effective runtime is a recorded no-op.

## Evidence

Receipts are under `quality-reports/go-migration/instance-runtime-http-*` (gitignored):

- `red-go-route-absent`: Django 29/29, Go 404 with its route removed.
- `green-versioned`: 29 × 4.
- `mutation-audit-replay`: Go audits a replayed submission, CAUGHT.

Behaviour of the controller, the authority and the CLI is pinned by
`selfhost/tests/test_handoff_authority.py`, `BE_GO/migrations/runtime_authority_handoff_integration_test.go`
(`TESTS/adapters/go/handoff_authority.py`) and `BE/instance/tests/test_runtime_switch_handoff.py`.
