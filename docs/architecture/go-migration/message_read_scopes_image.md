# Packaged message authorization and warm sessions

`TESTS.adapters.go.message_read_scopes_image` is a parent-only executable acceptance runner. It consumes an already built exact immutable Go image, full expected revision, original build receipt and matching Go build context. It never builds, installs or falls back to native Go. Offline controls are rejection/shape evidence only; live image acceptance remains for the coordinator.

The runner seeds once and uses the same issued JWTs, impersonation sessions, full 547-migration schema, 128 managed models and exact Redis DB1 TYPE/DUMP/absolute-expiry baseline for native Django, packaged Go, real Caddy v1 and real Caddy v2. Each transport runs the unchanged 38 authored authorization scopes followed by both existing warm-session sequences (revoke-list and expire-detail). The actual ASGI observer also rejects a real v2 request deliberately routed to Django. All state/cache/process boundaries and session restoration remain enforced by the existing workload helpers.

The packaged backend process guard reads the actual owned Docker container on every call and returns fresh `{pid, instance, running}` evidence. Its identity binds container ID, immutable image, owner label, nonroot user, Linux runtime PID, start time and zero restarts. Caddy guards combine the actual backend guard with the existing live gateway inspection. The runner does not invent a Popen or use a constant process assertion.

Provenance binds the complete context manifest (including go.sum and assets), original build-receipt bytes, source revision, packaged `/app/server` digest, nonroot user, read-only root, health/package version, exact third-party licence bytes and source manifests before/server-start/after. Frozen build inputs must match the executing checkout's Go context. Input receipts and image artifacts remain untouched. A fresh output directory is mandatory; old successes cannot be overwritten or relabelled. Native runtime acceptance remains independent; a native-only receipt cannot satisfy image acceptance.

Logs, tokenized manifests and service diagnostics stay in private temporary storage. Public `results.json` contains fixed error codes, bounded statuses/counts/booleans and source/binary hashes. Failure, restart, foreign ownership, permission errors, missing cases, source drift and failed cleanup all reject acceptance. Optimized Python is rejected before output creation or service startup.

Coordinator command, in a fresh parent process using provisioned production Python 3.13.16 and existing local service/Caddy images:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m TESTS.adapters.go.message_read_scopes_image \
  --image "$IMMUTABLE_GO_IMAGE_ID" \
  --expected-revision "$EXACT_40_CHARACTER_SOURCE_REVISION" \
  --build-receipt "$ORIGINAL_BUILD_RECEIPT" \
  --go-source "$MATCHING_FROZEN_SOURCE_ROOT" \
  --output "$NEW_ACCEPTANCE_DIRECTORY"
```

Optional `--postgres` and `--redis` select already available local fixture images. PostgreSQL/Redis use the established image fixture; Django uses the full migration helper rather than the syncdb-only current-schema initializer. The runner composes the existing network-aware `caddy.proxy` lifetime with removal verification, passing the image fixture's owned network and `category-api:8000` upstream. This works with Linux host-gateway semantics; Django remains reachable through the standard host-gateway mapping. The existing native `owned_caddy` helper handles the deliberate misroute control. No shared interface changes are required.

Offline checks:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest TESTS.adapters.go.test_message_read_scopes_image
```

This bounded slice makes no query-budget, performance, browser, installed activation, complete message-family, or full route-verification claim. Those acceptance gaps remain separate.
