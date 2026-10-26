"""Parent-only native PostgreSQL repository acceptance; no HTTP/server matrix.

Run in a fresh production Python process with existing dependencies installed.
Reuses the message-read private PostgreSQL/Redis lifecycle and all migrations.
Only results.json is publishable; SQL, plans, DSNs, tokens and test output stay
in private temporary storage. No caller database configuration is accepted.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

# Every acceptance/closed-schema assert below must execute. Reject optimization
# before importing shared preflight/lifecycle code or touching output/services.
if not __debug__ or sys.flags.optimize:
    print(json.dumps({"success": False, "complete_message_family_parity": False,
                      "errors": [{"stage": "startup", "type": "AcceptanceFailure",
                                  "check": "optimized-python"}]}))
    raise SystemExit(1)

import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from TESTS.adapters.django import message_reads as oracle
from TESTS.adapters.django import message_reads_runtime as lifecycle

DSN_ENV = "ASSOZETA_MESSAGE_REPOSITORY_DSN"
EXPECTED_TESTS = frozenset({
    "TestMessageRepositoryHydrationLive",
    "TestMessageRepositoryExecutedPlansLive",
    "TestMessageRepositoryBlockedCancellationLive",
    "TestMessageRepositoryConsumerFailurePendingRowsLive",
})
OWNED_SOURCES = (
    "BE_GO/internal/repository/message_reads_integration_test.go",
    "TESTS/adapters/go/message_reads_repository.py",
    "TESTS/test_message_reads_repository.py",
)
GO_SOURCE = OWNED_SOURCES[0]
# Go testing indents locations by four spaces. Do not search arbitrary text,
# accept paths/other filenames, or convert unbounded attacker-controlled ints.
GO_SOURCE_LINE = re.compile(r"\A[ \t]{0,8}message_reads_integration_test\.go:([1-9][0-9]{0,5}):")
MAX_FAILURE_LINES = 8  # Per known failed test; at most 32 locations overall.
HEX = re.compile(r"[0-9a-f]{64}\Z")
RELATIONS = {"application_sportassociation", "communications_message",
             "communications_messagetransaction"}
COLUMNS = {"sport_association_id", "message_id", "message_transaction_id", "user_id"}
NODES = {"Sort", "Incremental Sort", "Seq Scan", "Index Scan", "Index Only Scan",
         "Bitmap Heap Scan", "Bitmap Index Scan", "Nested Loop", "Hash Join",
         "Merge Join", "Hash", "Materialize", "Memoize", "Gather", "Gather Merge"}
CHECKS = {"binding", "go-build", "go-execution", "go-events", "go-receipts",
          "migration-count", "managed-model-count", "seeded-state"}


class AcceptanceFailure(AssertionError):
    def __init__(self, check, unpassed_tests=(), failed_locations=()):
        if check not in CHECKS:
            raise ValueError("unknown acceptance check")
        super().__init__(check)
        self.check = check
        self.unpassed_tests = sorted(set(unpassed_tests) & EXPECTED_TESTS)
        self.failed_locations = list(failed_locations)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    hashes = lifecycle.source_hashes()
    for name in OWNED_SOURCES:
        hashes[name] = sha256_file(ROOT / name)
    return dict(sorted(hashes.items()))


def require_binding(before, after):
    if not before or before != after:
        raise AcceptanceFailure("binding")


def failed_go_locations(events, source_sha256):
    """Project only known failed tests and bounded lines in the exact source."""
    if source_sha256 is None:
        return []
    source = (ROOT / GO_SOURCE).read_bytes()
    require_binding(source_sha256, hashlib.sha256(source).hexdigest())
    line_count = len(source.splitlines())
    failed = {e.get("Test") for e in events
              if e.get("Action") == "fail" and type(e.get("Test")) is str
              and e["Test"] in EXPECTED_TESTS}
    lines = {name: set() for name in failed}
    for event in events:
        name, output = event.get("Test"), event.get("Output")
        if (event.get("Action") != "output" or type(name) is not str
                or name not in failed or type(output) is not str):
            continue
        match = GO_SOURCE_LINE.match(output)
        if match:
            line = int(match[1])
            if line <= line_count and len(lines[name]) < MAX_FAILURE_LINES:
                lines[name].add(line)
    return [{"test": name, "file": GO_SOURCE, "source_sha256": source_sha256,
             "lines": sorted(lines[name])} for name in sorted(lines) if lines[name]]


def validate_go_events(content, exit_code, *, source_sha256=None):
    events = [json.loads(line) for line in content.splitlines() if line.strip()]
    if any(type(e) is not dict or ("Test" in e and type(e["Test"]) is not str)
           or type(e.get("Action")) is not str for e in events):
        raise AcceptanceFailure("go-events")
    passed = [e["Test"] for e in events if e.get("Action") == "pass" and "Test" in e]
    locations = failed_go_locations(events, source_sha256)
    if exit_code != 0:
        raise AcceptanceFailure("go-execution", EXPECTED_TESTS - set(passed), locations)
    if (not events or any(e.get("Action") in {"skip", "fail"} for e in events)
            or len(passed) != len(EXPECTED_TESTS) or set(passed) != EXPECTED_TESTS):
        raise AcceptanceFailure("go-events", EXPECTED_TESTS - set(passed), locations)
    return sorted(passed)


def exact_keys(value, keys):
    assert type(value) is dict and set(value) == set(keys), "unsafe or incomplete receipt shape"


def number(value, *, maximum=10**8):
    assert type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def validate_receipts(receipts):
    """Closed schema: unknown strings/keys/raw EXPLAIN diagnostics cannot upload."""
    exact_keys(receipts, {"hydration", "plans", "cancellation", "consumer_failure"})
    assert receipts["hydration"] == {
        "own_messages": 4, "own_transactions": 4, "foreign_messages": 1,
        "empty_details": 5, "missing_owners": 5, "literal_and_null_fields": True,
        "resolved_owner_boundary": True,
    }
    cancellation = receipts["cancellation"]
    exact_keys(cancellation, {"lock_wait_observed", "blocker_held_at_return", "context_canceled",
                              "return_ms", "pool_in_use_before_release",
                              "pool_in_use_after_followup", "followup_rows"})
    for key in ("lock_wait_observed", "blocker_held_at_return", "context_canceled"):
        assert cancellation[key] is True
    assert cancellation["pool_in_use_before_release"] == 0
    assert cancellation["pool_in_use_after_followup"] == 0
    assert cancellation["followup_rows"] == 4
    number(cancellation["return_ms"], maximum=2000)
    consumer = receipts["consumer_failure"]
    expected = {
        "list_sql_wrapped_for_pending_control": True, "first_row_bytes": 131072,
        "consumer_calls": 1, "later_row_lock_wait_observed": True,
        "blocker_held_at_return": True, "consumer_error_preserved": True,
        "caller_context_live_at_return": True, "backend_idle_or_gone": True,
        "pool_in_use_at_return": 0, "pool_in_use_after_followup": 0, "followup_rows": 4,
    }
    exact_keys(consumer, {*expected, "return_ms"})
    for key, value in expected.items():
        assert type(consumer[key]) is type(value) and consumer[key] == value
    assert type(consumer["return_ms"]) is int and 0 <= consumer["return_ms"] < 2000
    plans = receipts["plans"]
    exact_keys(plans, {"indexes", "stages", "planner_forced", "executed_sql_traced"})
    assert plans["planner_forced"] is False and plans["executed_sql_traced"] is True
    assert type(plans["indexes"]) is list and 4 <= len(plans["indexes"]) <= 32
    metadata = {}
    leading = set()
    for index in plans["indexes"]:
        exact_keys(index, {"relation", "columns", "unique", "primary",
                           "name_sha256", "definition_sha256"})
        assert index["relation"] in RELATIONS
        assert type(index["columns"]) is list and 1 <= len(index["columns"]) <= 4
        assert all(column in COLUMNS for column in index["columns"])
        assert type(index["unique"]) is bool and type(index["primary"]) is bool
        assert HEX.fullmatch(index["name_sha256"]) and HEX.fullmatch(index["definition_sha256"])
        metadata[index["name_sha256"]] = index["relation"]
        leading.add((index["relation"], index["columns"][0]))
    assert {("application_sportassociation", "user_id"),
            ("communications_message", "sport_association_id"),
            ("communications_message", "message_id"),
            ("communications_messagetransaction", "message_id")} <= leading
    assert type(plans["stages"]) is list and len(plans["stages"]) == 3
    query_hashes = {}
    for stage, owners in zip(plans["stages"], (8, 24, 48)):
        exact_keys(stage, {"owners", "messages", "transactions", "plans"})
        assert stage["owners"] == owners
        assert stage["messages"] == stage["transactions"] == 6 + owners * 128
        assert type(stage["plans"]) is list and len(stage["plans"]) == 8
        for plan, kind in zip(stage["plans"], ("association", "list", "association", "detail") * 2):
            exact_keys(plan, {"kind", "sql_sha256", "expected_rows", "nodes"})
            assert plan["kind"] == kind and HEX.fullmatch(plan["sql_sha256"])
            query_hashes.setdefault(kind, plan["sql_sha256"])
            assert query_hashes[kind] == plan["sql_sha256"]
            expected = 1 if kind == "association" else 128
            assert plan["expected_rows"] == expected
            assert type(plan["nodes"]) is list and 1 <= len(plan["nodes"]) <= 32
            for node in plan["nodes"]:
                exact_keys(node, {"node", "relation", "index_sha256", "estimated_rows",
                                  "actual_rows", "loops", "shared_hit_blocks", "shared_read_blocks"})
                assert node["node"] in NODES and node["relation"] in RELATIONS | {""}
                index_hash = node["index_sha256"]
                assert index_hash == "" or index_hash in metadata
                if index_hash and node["relation"]:
                    assert metadata[index_hash] == node["relation"]
                for key in ("estimated_rows", "actual_rows", "loops", "shared_hit_blocks", "shared_read_blocks"):
                    number(node[key])
            assert plan["nodes"][0]["actual_rows"] == expected
            assert plan["nodes"][0]["loops"] == 1
    return receipts


def error_code(stage, error):
    kind = type(error).__name__
    result = {"stage": stage, "type": kind if kind in {
        "AssertionError", "RuntimeError", "TimeoutExpired", "OSError", "FileNotFoundError",
        "ValueError", "JSONDecodeError", "KeyboardInterrupt", "SystemExit", "AcceptanceFailure",
    } else "Exception"}
    if isinstance(error, AcceptanceFailure):
        result.update(check=error.check, unpassed_tests=error.unpassed_tests)
        if error.failed_locations:
            result["failed_locations"] = error.failed_locations
    return result


def cleanup_step(receipt, name, operation):
    try:
        assert operation() is not False
        receipt["results"][name] = True
    except BaseException as error:
        receipt["results"][name] = False
        receipt["errors"].append(error_code(name, error))


def cleanup_verified(receipt):
    return bool(receipt["results"] and not receipt["errors"] and all(receipt["results"].values()))


def private_process(command, *, environment, log, timeout, stdout=None):
    # Kill the whole owned process group on timeout/interruption, including the
    # test2json child test binary. No service or child survives a failed runner.
    with subprocess.Popen(command, cwd=ROOT / "BE_GO", env=environment,
                          stdout=stdout or log, stderr=log, start_new_session=True) as process:
        try:
            return process.wait(timeout=timeout)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)


def run_go(work, environment, private_log):
    assert environment.get(DSN_ENV), "explicit private DSN required; never skip"
    work = Path(work)
    binary = work / "message-repository.test"
    receipt_dir = work / "message-repository-receipts"
    receipt_dir.mkdir()
    go_source_hash = sha256_file(ROOT / GO_SOURCE)
    with Path(private_log).open("w") as log:
        # Compile once and execute that exact hashed artifact. Neither build nor
        # execution may download dependencies or a toolchain.
        build = private_process(
            ["go", "test", "-c", "-race", "-tags=messageintegration", "-o", str(binary),
             "./internal/repository"], environment=environment, log=log, timeout=180)
        if build != 0:
            raise AcceptanceFailure("go-build")
        binary_hash = sha256_file(binary)
        with (work / "go-events.jsonl").open("w") as events:
            code = private_process(
                ["go", "tool", "test2json", "-t", "-p", "repository", str(binary),
                 "-test.v", "-test.count=1", "-test.timeout=180s",
                 "-test.run=^TestMessageRepository.*Live$"],
                environment={**environment, "ASSOZETA_MESSAGE_REPOSITORY_RECEIPTS": str(receipt_dir)},
                log=log, stdout=events, timeout=200)
        require_binding(binary_hash, sha256_file(binary))
    passed = validate_go_events((work / "go-events.jsonl").read_text(), code,
                                source_sha256=go_source_hash)
    try:
        receipts = validate_receipts({name: json.loads((receipt_dir / (name + ".json")).read_text())
                                      for name in ("hydration", "plans", "cancellation", "consumer_failure")})
    except (AssertionError, ValueError, OSError, TypeError) as error:
        raise AcceptanceFailure("go-receipts") from error
    return {"passed": passed, "binary_sha256": binary_hash, "receipts": receipts}


def planned_fixture_rows():
    """Fixed cleanup manifest also works if the existing seeder fails mid-way."""
    from application.models import Associate, SportAssociation, User
    from communications.models import Message, MessageTransaction

    rows = [(User, oracle._uid(n)) for n in range(700001, 700008)]
    rows += [(SportAssociation, oracle._uid(n)) for n in range(701001, 701004)]
    rows += [(Associate, oracle._uid(700101))]
    rows += [(Message, oracle._uid(row["number"])) for row in oracle.MESSAGE_SEED_RECORDS]
    rows += [(MessageTransaction, oracle._uid(row["number"])) for row in oracle.TRANSACTION_SEED_RECORDS]
    for index in range(48):
        rows.extend([(User, oracle._uid(760000 + index)),
                     (SportAssociation, oracle._uid(761000 + index))])
    return rows


def seed_plan_owners():
    from application.models import SportAssociation, User
    from django.db import transaction

    # bulk_create follows the established fixture pattern and avoids save hooks
    # for these bounded synthetic records; no global hook suppression.
    with transaction.atomic():
        for index in range(48):
            owner = User(pk=oracle._uid(760000 + index), username=f"message-plan-{index}",
                         email=f"message-plan-{index}@example.test", password="!synthetic",
                         role=User.ASSOCIATION, first_name="Plan", last_name="Fixture")
            User._base_manager.bulk_create([owner])
            SportAssociation._base_manager.bulk_create([
                SportAssociation(pk=oracle._uid(761000 + index), user=owner,
                                 denomination=f"Plan scope {index}", tax_code=f"7600000{index:04d}")])


@contextmanager
def fixture_scope(apps, work, raw, cleanup):
    from django.db import transaction
    from TESTS.adapters.django.historical_read_cache import (
        snapshot_historical_read_cache, PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY,
    )
    original = oracle.snapshot_models(apps)
    original_cache = snapshot_historical_read_cache(raw)
    if len(original) != 128:
        raise AcceptanceFailure("managed-model-count")
    assert not original_cache.entries, "private cache must begin empty"
    planned = planned_fixture_rows()
    assert all(not model._base_manager.filter(pk=identifier).exists() for model, identifier in planned)
    try:
        fixture = oracle.seed_fixture(apps, work / "message-reads-private", raw)
        seed_plan_owners()
        # Nonempty exercised rows, including extra owners, bind all-model/cache
        # comparisons to actual repository reads and rolled-back native writes.
        baseline = oracle.snapshot_models(apps)
        yield fixture
        try:
            oracle.assert_state_unchanged(baseline, oracle.snapshot_models(apps))
            oracle.assert_cache_unchanged(fixture["baseline_cache"], snapshot_historical_read_cache(raw),
                                         fixture["contrasts"])
        except AssertionError as error:
            raise AcceptanceFailure("seeded-state") from error
    finally:
        def delete_rows():
            with transaction.atomic():
                oracle.delete_created({"created": planned})
        cleanup_step(cleanup, "fixture_rows_removed", delete_rows)
        cleanup_step(cleanup, "cache_contrasts_removed",
                     lambda: raw.delete(PERSISTENT_CONTRAST_KEY, EXPIRING_CONTRAST_KEY) >= 0)
        cleanup_step(cleanup, "models_restored", lambda: oracle.snapshot_models(apps) == original)
        cleanup_step(cleanup, "cache_restored", lambda: snapshot_historical_read_cache(raw) == original_cache)
        cleanup_step(cleanup, "private_manifest_removed", lambda:
                     (work / "message-reads-private/message_reads-runtime.json").unlink(missing_ok=True))


def execute_owned(private, service_report, cleanup, report, tools):
    with lifecycle.owned_message_database(private, service_report) as (work, redis_port, log):
        environment, _secrets = lifecycle.private_environment(work, redis_port, tools)
        lifecycle.apply_private_environment(environment)
        apps, _ = lifecycle.initialize_django_apps()
        from django.db import connections
        from django.test.utils import override_settings
        import redis

        raw = None
        with override_settings(CACHES=lifecycle.cache_override(redis_port)):
            try:
                connections.close_all()
                schema = lifecycle.migrate_full_schema(log)
                if schema != {"mode": "full-migration-history", "applied_migrations": 547,
                              "remaining_plan": 0}:
                    raise AcceptanceFailure("migration-count")
                report["schema"] = schema
                raw = redis.Redis(host="127.0.0.1", port=redis_port, db=1,
                                  password="synthetic-identity-only", decode_responses=False)
                with fixture_scope(apps, work, raw, cleanup):
                    environment = {**tools, DSN_ENV:
                                   f"host={work} port=5432 user=contracts dbname={lifecycle.DATABASE} sslmode=disable"}
                    report["go"] = run_go(private, environment, private / "go-private.log")
                    report["reads_preserved_seeded_state"] = True
            finally:
                if raw is not None:
                    cleanup_step(cleanup, "redis_client_closed", raw.close)
                cleanup_step(cleanup, "django_connections_closed", connections.close_all)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = lifecycle.prepare_output_directory(args.output)
    except (AssertionError, OSError) as error:
        print(json.dumps({"success": False, "errors": [error_code("output", error)],
                          "complete_message_family_parity": False}))
        return 1
    report = {"success": False, "scope": "bounded real PostgreSQL message repository",
              "complete_message_family_parity": False, "errors": []}
    # Invalidate an earlier receipt before preflight/build/services can fail.
    oracle.write_publishable(output / "results.json", report, ())
    cleanup = {"results": {}, "errors": []}
    services = {}
    before = None
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        report["python"] = require_production_python()
        before = source_hashes()
        report["source_sha256"] = before
        report["tool_sha256"] = lifecycle.binary_hashes(lifecycle.service_binary_paths(False))
        tools = lifecycle.sanitized_tool_environment()
        tools.update({"LC_ALL": "C", "LANG": "C", "GOPROXY": "off", "GOTOOLCHAIN": "local",
                      "GOSUMDB": "off", "GOFLAGS": "-mod=readonly"})
        with patch.dict(os.environ, tools, clear=True):
            directory = None
            try:
                with tempfile.TemporaryDirectory(prefix="az-message-repository-private-", dir="/tmp") as directory:
                    execute_owned(Path(directory), services, cleanup, report, tools)
            finally:
                report["private_output_removed"] = bool(directory and not Path(directory).exists())
    except BaseException as error:
        report["errors"].append(error_code("acceptance", error))
    finally:
        # This executes after service teardown, including failure paths.
        try:
            after = source_hashes()
            report["source_sha256_after"] = after
            require_binding(before, after)
            report["source_stable"] = True
        except BaseException as error:
            report["source_stable"] = False
            report["errors"].append(error_code("source-binding", error))
        report["fixture_cleanup"] = cleanup
        report["owned_directory_removed"] = services.get("owned_directory_removed") is True
        states = services.get("service_cleanup", {})
        report["services_stopped"] = all(states.get(name) == "stopped" for name in ("postgres", "redis"))
        sensitive = services.get("sensitive_cleanup", {})
        report["sensitive_cleanup_verified"] = sensitive.get("verified") is True
        report["cleanup_verified"] = bool(cleanup_verified(cleanup) and report["services_stopped"]
                                          and report["owned_directory_removed"]
                                          and report["sensitive_cleanup_verified"]
                                          and report.get("private_output_removed"))
        report["success"] = bool(not report["errors"] and report["source_stable"]
                                 and report["cleanup_verified"] and report.get("go")
                                 and report.get("reads_preserved_seeded_state"))
        # No raw exception/service log/Go event data enters this projection.
        oracle.write_publishable(output / "results.json", report, ())
    print(json.dumps({"success": report["success"], "complete_message_family_parity": False}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
