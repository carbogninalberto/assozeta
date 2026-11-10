# Instance updates and restarts in production mode

`InstanceUpdatesView` (`instance/admin/updates`, GET and POST) and `InstanceRestartsView`
(`instance/admin/restarts`, POST) in `BE/instance/administration.py`, with
`updater_client.call_runner` and `release_catalog.fetch_release`, are implemented in Go
(`BE_GO/internal/service/instance_updates.go`, `instanceprobe/release_fetch.go`, the updaterrunner
client). Identity, `IsInstanceOwner`, OPTIONS and the development-mode answers are unchanged and
described in `instance-admin.md`.

## call_runner

`<ASSOZETA_UPDATER_DIRECTORY>/runner.sock` with `Authorization: Bearer <token file, stripped>`,
`Content-Type: application/json` and, for a payload, exactly `json.dumps(payload)`. A missing socket,
a missing or blank token, a transport failure or an undecodable answer is `UpdaterUnavailable`
(`Il servizio di aggiornamento non è raggiungibile. …`). An answer of status 400 or more is
`UpdaterRejected(body.get('error', 'Aggiornamento non disponibile.'), status)`.

## GET admin/updates

`call_runner('GET', '/status')`. A `protocol` other than 1 (Python equality: `1.0` and `true` count)
answers `{available: false, reason: 'Il servizio di aggiornamento richiede una versione compatibile.',
active: null, history: []}`; otherwise the runner's object is relayed. Unavailable answers the same
shape with its reason; rejected answers `{error}` with the runner's status. A non-object status is the
500 page.

## POST admin/updates

After `UpdateRequestSerializer`: the runner status (protocol 1 or 503); the active operation and the
history are searched for the request id: the same actor, release and tag replay `202 {operation}`,
otherwise 409 `Questo identificativo è già stato usato per un altro aggiornamento.`. Then `available`
(503 with the reason or the default), `can_update is False` (409), `fetch_release(release_id)`
(GitHub `releases/<id>`; `ReleaseError` texts are 503), the tag and `artifacts_ready` (409), the
version check against `RUNNING_VERSION` (409), and `call_runner('POST', '/updates', {release_id,
tag, request_id, actor_id, source_version})`, relayed with 202.

## POST admin/restarts

Only `request_id` (400 otherwise), the UUID, then `call_runner('POST', '/restarts', {request_id,
actor_id})`, relayed with 202; unavailable is 503, rejected keeps the runner's status.

## Not ported (500 page before any write)

- Runner answers Go does not decode like `json.loads` (UTF-16/32, surrogates, NaN, deep nesting) and
  reason texts that are not strings; a release id beyond 64 bits.
- Release feeds the release normalizer refuses (see `instance-outbound.md`).

## Evidence

`TESTS/fixtures/instance_updates_http.json` (40 cases, `TESTS/fixtures/generate_instance_updates_http.py`).
The runner (`TESTS/adapters/django/instance_updates_http.py`) owns a sequence-scripted update runner
on a Unix socket and reuses instance-outbound's HTTPS proxy and certificate authority for GitHub; both
servers run in production mode. Every runner and release API request is compared exactly.

- RED (`v2-admin-red`): Django 40/40, Go 500 on `instance-updates-status` (production refused).
- GREEN (`v2-admin-green`): 40/40 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`v2-admin-mutation-version-check`): accepting a release that is not newer than the
  running version fails `instance-updates-request-release-not-newer`.
- Re-run with the shared changes: instance-admin 164/164 and instance-outbound 106/106 native
  (`v2-admin-rerun`).
