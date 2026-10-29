"""Offline controls for the message-read Django/Go acceptance adapter.

These tests deliberately do not import Django, start PostgreSQL or Redis, or
open a network connection. Runtime parity is never claimed here.
"""
import copy
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from TESTS.adapters.django import historical_read_cache as cache
from TESTS.adapters.django import message_reads as adapter
from TESTS.adapters.django import message_reads_runtime as runtime
from TESTS import test_go_query_observation as observation_fixture


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeEntry:
    redis_type: bytes
    serialized_value: bytes
    expires_at_ms: int


class FakeRedis:
    """Command double for the actual cache helper; no external test imports."""

    def __init__(self):
        self.entries = {}

    def execute_command(self, command, key):
        entry = self.entries.get(key)
        if command == "PEXPIRETIME":
            return entry.expires_at_ms if entry else -2
        if command == "TYPE":
            return entry.redis_type if entry else b"none"
        if command == "DUMP":
            return entry.serialized_value if entry else None
        if command == "GET":
            return entry.serialized_value.removeprefix(b"synthetic-dump:") if entry else None
        raise AssertionError(command)

    def exists(self, key):
        return int(key in self.entries)

    def set(self, key, value, *, nx=False):
        if nx and key in self.entries:
            return None
        self.entries[key] = FakeEntry(b"string", b"synthetic-dump:" + value, -1)
        return True

    def persist(self, key):
        self.entries[key] = replace(self.entries[key], expires_at_ms=-1)
        return 1

    def pexpireat(self, key, expiry):
        self.entries[key] = replace(self.entries[key], expires_at_ms=expiry)
        return 1

    def eval(self, script, numkeys, max_keys, max_bytes):
        assert "PEXPIRETIME" in script and numkeys == 0
        assert len(self.entries) <= max_keys
        assert sum(len(key) + len(row.redis_type) + len(row.serialized_value)
                   for key, row in self.entries.items()) <= max_bytes
        return [[key, [row.redis_type, row.serialized_value, row.expires_at_ms]]
                for key, row in self.entries.items()]


class DjangoLivenessControls(unittest.TestCase):
    """Handle doubles exercise plumbing only; no live process proof is claimed."""

    def handles(self):
        observer = object()
        server = SimpleNamespace(started=False, should_exit=False, force_exit=False, run=lambda: None,
            config=SimpleNamespace(app=observer),
            servers=[SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: ('127.0.0.1', 1234))])])
        thread = SimpleNamespace(alive=False)
        thread.is_alive = lambda: thread.alive
        return server, thread, observer

    def test_guard_tracks_actual_handles_before_start_during_run_and_after_stop(self):
        server, thread, observer = self.handles()
        guard = runtime.django_liveness_guard(server, thread, observer)
        before = guard()
        self.assertEqual(before['pid'], os.getpid())
        self.assertGreater(before['instance'], 0)
        self.assertIs(before['running'], False)
        thread.alive = server.started = True
        live = guard()
        self.assertIs(live['running'], True)
        self.assertEqual({k: live[k] for k in ('pid', 'instance')},
                         {k: before[k] for k in ('pid', 'instance')})
        for flag in ('should_exit', 'force_exit'):
            setattr(server, flag, True)
            self.assertIs(guard()['running'], False)
            setattr(server, flag, False)
        server.config.app = object()
        self.assertIs(guard()['running'], False)
        server.config.app = observer
        with mock.patch.object(runtime.os, 'getpid', return_value=live['pid'] + 1):
            self.assertIs(guard()['running'], False)
        thread.alive = False
        self.assertIs(guard()['running'], False)
        other = runtime.django_liveness_guard(*self.handles())
        self.assertNotEqual(other()['instance'], live['instance'])

    def run_context(self, *, callback=None, startup_failure=False, body_failure=False):
        from TESTS import http_client
        server, thread, observer = self.handles()
        events = []
        def start():
            events.append('start')
            if startup_failure:
                raise RuntimeError('offline-start')
            thread.alive = server.started = True
        def join(timeout):
            self.assertTrue(server.should_exit)
            events.append('join')
            thread.alive = False
        thread.start, thread.join = start, join
        modules = {'uvicorn': SimpleNamespace(Config=lambda *a, **kw: None,
                    Server=lambda config: server),
                   'django.core.asgi': SimpleNamespace(get_asgi_application=lambda: object())}
        report = {'service_cleanup': {}}
        with mock.patch.dict(sys.modules, modules), \
             mock.patch.object(runtime.oracle, 'AsgiObserver', return_value=observer), \
             mock.patch.object(runtime.threading, 'Thread', return_value=thread), \
             mock.patch.object(http_client, 'poll', side_effect=lambda get, *a, **kw: self.assertTrue(get())), \
             mock.patch('socket.socket', side_effect=AssertionError('socket forbidden')):
            kwargs = {} if callback is None else {'liveness_observer': callback}
            try:
                with runtime.django_application(report, False, None, **kwargs) as value:
                    self.assertEqual(value, ('http://127.0.0.1:1234', observer, 1234))
                    if body_failure:
                        raise RuntimeError('offline-body')
            finally:
                self.assertEqual(report['service_cleanup']['django'], 'stopped')
                self.assertFalse(thread.alive)
        return events

    def test_original_context_tuple_and_default_call_remain_compatible(self):
        self.assertEqual(self.run_context(), ['start', 'join'])

    def test_callback_receives_live_guard_that_dies_on_cleanup(self):
        guards = []
        def remember(guard):
            self.assertTrue(guard()['running'])
            guards.append(guard)
        self.run_context(callback=remember)
        self.assertFalse(guards[0]()['running'])

    def test_callback_and_body_failure_still_stop_owned_thread(self):
        guards = []
        def broken(guard):
            guards.append(guard)
            raise RuntimeError('offline-callback')
        with self.assertRaisesRegex(RuntimeError, '^offline-callback$'):
            self.run_context(callback=broken)
        self.assertFalse(guards[0]()['running'])
        with self.assertRaisesRegex(RuntimeError, '^offline-body$'):
            self.run_context(body_failure=True)

    def test_start_failure_does_not_join_unstarted_thread_or_mask_original(self):
        with self.assertRaisesRegex(RuntimeError, '^offline-start$'):
            self.run_context(startup_failure=True)


class SeedControls(unittest.TestCase):
    def test_all_owned_list_rows_and_the_empty_delivery_row_are_seeded(self):
        fixture = adapter.load_authored_manifest()
        owned = [
            row
            for row in adapter.MESSAGE_SEED_RECORDS
            if row["association"] == "own" and row["type"] in {"EMAIL", "INSIDE_APP"}
        ]
        self.assertEqual({row["number"] for row in owned}, {
            702001,
            702002,
            702003,
            702006,
        })
        self.assertIn(702006, {row["number"] for row in owned})
        self.assertNotIn(
            702006,
            {row["message"] for row in adapter.TRANSACTION_SEED_RECORDS},
        )
        expected = next(
            row for row in fixture["cases"]
            if row["name"] == "message-reads-list-owner"
        )["response"]
        self.assertEqual(len(expected), 4)
        self.assertEqual(expected[-1]["message_id"], fixture["ids"]["empty_email"])

    def test_unsupported_message_has_transaction_seven_zero_three_zero_zero_seven(self):
        fixture = adapter.load_authored_manifest()
        record = next(
            row for row in adapter.TRANSACTION_SEED_RECORDS
            if row["number"] == 703007
        )
        self.assertEqual(record["message"], 702005)
        self.assertEqual(
            fixture["ids"]["unsupported_type_transaction"],
            adapter._uid(703007),
        )
        self.assertNotIn(
            703006,
            {row["number"] for row in adapter.TRANSACTION_SEED_RECORDS},
        )

    def test_seed_records_cover_every_message_transaction_model_field(self):
        fields = adapter.django_transaction_fields()
        self.assertEqual(len(fields), 11)
        for record in adapter.TRANSACTION_SEED_RECORDS:
            values = adapter.transaction_model_values(record, object())
            self.assertEqual(set(values), fields)
        with self.assertRaises(AssertionError):
            adapter.transaction_model_values(
                {**adapter.TRANSACTION_SEED_RECORDS[0], "unexpected": None},
                object(),
            )

    def test_seed_validation_rejects_duplicates_bad_references_and_partial_rows(self):
        mutations = []
        duplicate = copy.deepcopy(adapter.MESSAGE_SEED_RECORDS)
        duplicate[-1]["number"] = duplicate[-2]["number"]
        mutations.append(("message", duplicate, adapter.TRANSACTION_SEED_RECORDS))
        missing = copy.deepcopy(adapter.TRANSACTION_SEED_RECORDS)
        missing[-1].pop("bounced_status")
        mutations.append(("transaction", adapter.MESSAGE_SEED_RECORDS, missing))
        foreign_reference = copy.deepcopy(adapter.TRANSACTION_SEED_RECORDS)
        foreign_reference[0]["message"] = 799999
        mutations.append(("reference", adapter.MESSAGE_SEED_RECORDS, foreign_reference))
        for name, messages, transactions in mutations:
            with self.subTest(name=name), self.assertRaises(AssertionError):
                adapter.validate_seed_records(messages, transactions)
        receipt = adapter.validate_seed_records()
        self.assertEqual(receipt, {"messages": 6, "transactions": 6})

    def test_growth_ids_do_not_collide_with_seed_detail_or_each_other(self):
        identifiers = runtime.growth_message_numbers(48)
        combined = [
            identifiers["detail"],
            *identifiers["list"],
            *identifiers["transactions"],
        ]
        seeded = {
            record["number"]
            for record in adapter.MESSAGE_SEED_RECORDS
        } | {
            record["number"]
            for record in adapter.TRANSACTION_SEED_RECORDS
        }
        self.assertEqual(len(combined), len(set(combined)))
        self.assertFalse(seeded & set(combined))

    def test_actor_labels_become_tokens_without_changing_authored_expectations(self):
        authored = adapter.load_authored_manifest()
        original = copy.deepcopy(authored)
        tokens = {
            name: f"private-token-{name}"
            for name in sorted({
                case["actor"] for case in authored["cases"] if "actor" in case
            })
        }
        runtime_fixture = adapter.runtime_manifest(authored, tokens)
        self.assertEqual(authored, original)
        self.assertEqual(
            [case.get("response") for case in runtime_fixture["cases"]],
            [case.get("response") for case in original["cases"]],
        )
        for source, converted in zip(original["cases"], runtime_fixture["cases"]):
            self.assertNotIn("actor", converted)
            if "actor" in source:
                self.assertEqual(converted["token"], tokens[source["actor"]])
            else:
                self.assertNotIn("token", converted)


class SourceAndOrderingControls(unittest.TestCase):
    def test_actual_owned_database_creation_matches_every_caller(self):
        from TESTS.adapters.django import historical_read_routes as helper

        commands = []

        def command(args, **kwargs):
            del kwargs
            commands.append([str(arg) for arg in args])
            return mock.Mock(returncode=3 if args[-1] == "status" else 0)

        process = mock.Mock()
        process.poll.return_value = 0
        reservation = mock.MagicMock()
        reservation.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 16379)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            helper.shutil, "which", side_effect=lambda name: "/fake/bin/" + name
        ), mock.patch.object(helper.subprocess, "run", side_effect=command), mock.patch.object(
            helper.subprocess, "Popen", return_value=process
        ), mock.patch.object(helper.socket, "socket", return_value=reservation), mock.patch.object(
            runtime, "read_tracked_environment", return_value={}
        ), mock.patch.object(runtime, "signing_key_pair", return_value=("private", "public")):
            with helper.owned_database(Path(directory), {}) as (work, port, log):
                environment, _ = runtime.private_environment(work, port, {})
                query_log = runtime.PostgresQueryLog(work, log)
                query_log.command(["SELECT", "1"], "--command")
        alterations = [args[-1] for args in commands if args[0].endswith("/psql") and args[-1].startswith("ALTER SYSTEM")]
        self.assertIn("ALTER SYSTEM SET log_destination = 'jsonlog'", alterations)
        self.assertIn("ALTER SYSTEM SET log_parameter_max_length = '-1'", alterations)
        self.assertIn("ALTER SYSTEM SET logging_collector = 'on'", alterations)
        restarts = [args for args in commands if args[0].endswith("/pg_ctl") and "restart" in args]
        self.assertEqual(len(restarts), 1)
        self.assertEqual(restarts[0][restarts[0].index("-D") + 1], str(work / "postgres"))
        created = next(args[-1] for args in commands if args[0].endswith("/createdb"))
        self.assertEqual(environment["DBNAME"], created)
        self.assertEqual(environment["TESTS_DATABASE"], created)
        for args in commands:
            if args[0].endswith("/psql"):
                self.assertEqual(args[args.index("-d") + 1], created)

    def test_actual_native_factory_receives_the_same_database_cache_and_keys(self):
        from TESTS.adapters.go import payment_category_server as native

        key_names = (
            "SECRET_KEY", "JWT_SECRET_KEY", "JWT_PUBLIC_KEY", "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY", "AWS_STORAGE_BUCKET_NAME", "AWS_S3_REGION_NAME",
            "AWS_S3_ENDPOINT_URL", "AWS_LOCATION", "SOCIAL_AUTH_GOOGLE_OAUTH2_KEY",
            "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET", "SENJA_WEBHOOK_SECRET",
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            runtime, "read_tracked_environment", return_value={
                "AWS_S3_REGION_NAME": "us-east-1", "AWS_LOCATION": "private",
            }
        ), mock.patch.object(runtime, "signing_key_pair", return_value=("private-pem", "public-pem")):
            work = Path(directory)
            environment, _ = runtime.private_environment(work, 16379, {})
            database = {"NAME": environment["TESTS_DATABASE"], "USER": environment["DBUSER"],
                        "PASSWORD": environment["DBPASSWORD"], "HOST": environment["DBHOST"],
                        "PORT": environment["DBPORT"]}
            settings = SimpleNamespace(
                **{key: environment[key] for key in key_names},
                DATABASES={"default": database}, DATA_RESTORE_LOCK_PATH=str(work / "lock"),
            )
            listener = mock.MagicMock()
            listener.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 18000)
            process = mock.Mock()
            process.returncode = 0
            process.poll.return_value = None
            process.wait.side_effect = lambda **kwargs: setattr(process.poll, "return_value", 0)
            with mock.patch.dict(sys.modules, {"django.conf": SimpleNamespace(settings=settings)}), mock.patch.object(
                native, "build_server"
            ), mock.patch.object(native.socket, "socket", return_value=listener), mock.patch.object(
                native.subprocess, "Popen", return_value=process
            ) as launch, mock.patch.object(native.Client, "request", return_value=(200, {})):
                with native.native_server(work, io.StringIO(), {}, "127.0.0.1:16379",
                                          query_observation=True) as server:
                    self.assertEqual(server["base_url"], "http://127.0.0.1:18000")
                    self.assertEqual(server["query_observation_path"], work / "query-observations.jsonl")
            self.assertEqual(launch.call_args.args[0][1:],
                             ["--query-observation-file", str(work / "query-observations.jsonl")])
            launched = launch.call_args.kwargs["env"]
            self.assertEqual(launched["DBNAME"], runtime.DATABASE)
            self.assertEqual(launched["REDIS_PASSWORD"], environment["REDIS_PASSWORD"])
            self.assertEqual(launched["JWT_SECRET_KEY"], environment["JWT_SECRET_KEY"])
            self.assertEqual(launched["JWT_PUBLIC_KEY"], environment["JWT_PUBLIC_KEY"])
            configured = runtime.cache_override(16379)["default"]
            self.assertTrue(configured["LOCATION"].endswith(":16379/1"))
            self.assertEqual(configured["OPTIONS"]["PASSWORD"], environment["REDIS_PASSWORD"])

    def test_actual_caddy_helper_configures_v2_to_django_and_removes_its_container(self):
        from TESTS.adapters import caddy

        commands = []
        configuration = []

        def docker(args, **kwargs):
            del kwargs
            commands.append(args)
            if args[1] == "run":
                mount = next(arg for arg in args if arg.endswith(":/etc/caddy/Caddyfile:ro"))
                configuration.append(Path(mount.split(":", 1)[0]).read_text())
            output = {
                "port": b"127.0.0.1:18080", "inspect": b'{"container_id":"owned","image_id":"image"}',
            }.get(args[1], b"")
            return SimpleNamespace(stdout=output, returncode=0)

        metadata = []
        with mock.patch.object(caddy.subprocess, "run", side_effect=docker), mock.patch.object(
            caddy.Client, "request", return_value=(200, {})
        ):
            with caddy.proxy("host.docker.internal:18000", "host.docker.internal:18000",
                             io.StringIO(), container_observer=metadata.append) as (base, _):
                self.assertEqual(base, "http://127.0.0.1:18080")
        self.assertRegex(configuration[0],
                         r'handle_path /api/v2/\*\s*\{\s*reverse_proxy host\.docker\.internal:18000')
        self.assertNotIn("api-go:8000", configuration[0])
        self.assertEqual(len(metadata), 1)
        self.assertEqual(sum(args[1] == "run" for args in commands), 1)
        self.assertEqual(sum(args[1:3] == ["rm", "-f"] for args in commands), 1)

    def test_postgres_transaction_table_is_not_a_message_table(self):
        evidence = runtime.parse_postgres_statements(
            '2026-10-25 12:00:00 UTC [1] LOG:  statement: '
            'SELECT * FROM "communications_messagetransaction"'
        )
        self.assertEqual(evidence["messages"], 0)
        self.assertEqual(evidence["transactions"], 1)

    def test_postgres_observer_catches_writes_outside_message_family(self):
        evidence = runtime.parse_postgres_statements(
            '2026-10-25 12:00:00 UTC [1] LOG:  statement: '
            'UPDATE application_user SET last_name = \'changed\''
        )
        self.assertEqual(evidence["writes"], 1)

    def test_postgres_total_includes_user_queries_but_excludes_own_barrier(self):
        statements = [
            "SELECT * FROM communications_message", "SELECT * FROM bakney_user",
            "SELECT * FROM bakney_user", runtime.QUERY_LOG_BARRIER,
        ]
        content = "\n".join(
            f"2026-10-25 12:00:0{index} UTC [1] LOG:  statement: {sql}"
            for index, sql in enumerate(statements)
        )
        query = runtime.parse_postgres_statements(content)
        self.assertEqual(query["total"], 3)
        self.assertEqual(query["messages"], 1)

    def test_clean_process_cli_help_does_not_require_runtime_configuration(self):
        script = ROOT / "TESTS/adapters/django/message_reads_runtime.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd="/tmp",
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--output", result.stdout)
        self.assertIn("--versioned", result.stdout)
        self.assertNotIn("PostgreSQL", result.stderr)

    def test_source_manifest_contains_runner_tests_and_embedded_inputs(self):
        paths = {
            str(path.relative_to(adapter.ROOT))
            for path in runtime.source_paths()
        }
        for required in (
            "TESTS/adapters/django/message_reads.py",
            "TESTS/adapters/django/message_reads_runtime.py",
            "TESTS/test_message_reads_runtime.py",
            "TESTS/fixtures/message_reads.json",
            "TESTS/contracts/test_message_reads.py",
            "BE_GO/go.mod",
            "TESTS/adapters/go/query_observation.py",
            "TESTS/test_go_query_observation.py",
        ):
            self.assertIn(required, paths)
        self.assertTrue(any(path.startswith("BE/") for path in paths))
        self.assertTrue(any(path.startswith("BE_GO/internal/") for path in paths))

    def test_source_drift_is_fatal_and_reports_exact_changed_path(self):
        before = {"a.py": "one", "b.py": "two"}
        after = {"a.py": "changed", "b.py": "two"}
        calls = iter((after,))
        report = {"source_sha256": before}
        self.assertFalse(
            runtime.source_stable(report, hashes_func=lambda: next(calls))
        )
        self.assertEqual(report["source_changes"], ["a.py"])

    def test_binary_binding_hashes_each_artifact_and_rejects_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory, "server")
            first.write_bytes(b"built-server")
            first.chmod(0o755)
            receipt = runtime.binary_hashes({"go-server": first})
            self.assertEqual(
                receipt["go-server"]["sha256"],
                runtime.sha256_file(first),
            )
            first.unlink()
            with self.assertRaises(AssertionError):
                runtime.binary_hashes({"go-server": first})

    def test_output_directory_rejects_uploadable_private_or_extra_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "service.log").write_text("private diagnostics")
            with self.assertRaisesRegex(AssertionError, "service.log"):
                runtime.prepare_output_directory(output)
            (output / "service.log").unlink()
            (output / "results.json.tmp").write_text("partial")
            runtime.prepare_output_directory(output)
            self.assertEqual(set(output.iterdir()), set())
            (output / "results.json").write_text("{}\n")
            runtime.prepare_output_directory(output)
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {"results.json"},
            )

    def test_workload_order_is_django_go_then_caddy_v1_v2(self):
        rows = [
            {"label": "django-native", "backend": "django", "transport": "native"},
            {"label": "go-native", "backend": "go", "transport": "native"},
            {"label": "caddy-v1", "backend": "django", "transport": "caddy"},
            {"label": "caddy-v2", "backend": "go", "transport": "caddy"},
        ]
        self.assertTrue(runtime.validate_workload_order(rows, versioned=True))
        for mutation in (
            [rows[1], rows[0], *rows[2:]],
            rows[:2],
            [rows[0], rows[2], rows[1], rows[3]],
            [*rows, rows[-1]],
        ):
            with self.subTest(labels=[row["label"] for row in mutation]):
                with self.assertRaises(AssertionError):
                    runtime.validate_workload_order(mutation, versioned=True)

    def test_native_only_never_claims_versioned_workloads(self):
        self.assertEqual(
            runtime.expected_workload_labels(versioned=False),
            ["django-native", "go-native"],
        )
        with self.assertRaises(AssertionError):
            runtime.validate_workload_order([
                {"label": "django-native", "backend": "django", "transport": "native"},
                {"label": "go-native", "backend": "go", "transport": "native"},
                {"label": "caddy-v1", "backend": "django", "transport": "caddy"},
            ], versioned=False)

    def test_postgres_log_parser_counts_reads_and_writes_by_message_family(self):
        content = """
2026-10-25 12:00:00.000 UTC [1] LOG:  statement: SELECT * FROM application_sportassociation
2026-10-25 12:00:00.001 UTC [1] LOG:  statement: SELECT
  m.*, t.*
FROM communications_messagetransaction t
JOIN communications_message m ON m.message_id = t.message_id
2026-10-25 12:00:00.002 UTC [1] LOG:  execute unnamed: UPDATE communications_message SET subject = 'private'
2026-10-25 12:00:00.003 UTC [1] LOG:  statement: SELECT 1
"""
        evidence = runtime.parse_postgres_statements(content)
        self.assertEqual(evidence["association"], 1)
        self.assertEqual(evidence["messages"], 2)
        self.assertEqual(evidence["transactions"], 1)
        self.assertEqual(evidence["writes"], 1)
        self.assertEqual(evidence["total"], 4)


class ObservationAndStateControls(unittest.TestCase):
    def test_actual_portable_go_workload_uses_postgres_observation(self):
        from TESTS.contracts import test_message_reads as portable

        case = copy.deepcopy(adapter.load_authored_manifest()["cases"][0])
        case.pop("actor")
        case["token"] = "synthetic-control-token"
        fixture = {"runtime": {"cases": [case]}, "runtime_path": Path("/unused"),
                   "contrasts": object(), "baseline_state": {"model": []},
                   "baseline_cache": object()}
        query = {"total": 2, "association": 1, "messages": 1,
                 "transactions": 0, "writes": 0}
        postgres = mock.Mock()
        postgres.since.return_value = query
        queries = mock.Mock()
        queries.delta.return_value = dict.fromkeys(query, 0)
        observer = mock.Mock()
        observer.delta.return_value = []
        with mock.patch.object(portable, "load_manifest", return_value=fixture["runtime"]), mock.patch.object(
            portable.Client, "request", return_value=(200, case["response"], {
                "Content-Language": "en", "Allow": "GET, OPTIONS", "Content-Type": "application/json",
            })
        ), mock.patch.object(adapter, "snapshot_models", return_value={"model": []}), mock.patch.object(
            cache, "snapshot_historical_read_cache", return_value=object()
        ), mock.patch.object(adapter, "assert_cache_unchanged", return_value={}):
            result = adapter.run_portable_workload(
                label="go-native", backend="go", base_url="http://offline.invalid",
                fixture=fixture, apps=object(), observer=observer, queries=queries,
                raw=object(), private_log=io.StringIO(), postgres_queries=postgres,
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["requests"][0]["query"], query)
        postgres.mark.assert_called_once()
        postgres.since.assert_called_once_with(postgres.mark.return_value)
        queries.delta.assert_not_called()

    def test_backend_startup_cannot_remove_unsupported_transaction(self):
        from TESTS.contracts import test_message_reads as portable

        seeded = {"communications.MessageTransaction": [{"pk": adapter._uid(703007)}]}
        with mock.patch.object(adapter, "snapshot_models", return_value={
            "communications.MessageTransaction": [],
        }), mock.patch.object(portable, "load_manifest", return_value={"cases": []}):
            with self.assertRaises(adapter.AcceptanceStateError):
                adapter.run_portable_workload(
                    label="go-native", backend="go", base_url="http://offline.invalid",
                    fixture={"baseline_state": seeded, "baseline_cache": object(),
                             "contrasts": object(), "runtime_path": Path("/unused"),
                             "runtime": {"cases": []}},
                    apps=object(), observer=mock.Mock(), queries=mock.Mock(),
                    raw=object(), private_log=io.StringIO(), postgres_queries=mock.Mock(),
                )

    def test_v2_control_owns_separate_proxy_and_keeps_complete_delta(self):
        from TESTS.adapters import caddy
        from TESTS.contracts import test_message_reads as portable

        case = next(case for case in adapter.load_authored_manifest()["cases"]
                    if case["name"] == "message-reads-list-owner")
        observed = [{"seq": 8, "method": "GET", "path": case["path"],
                     "cid": "caddy-v2-deliberate-django-misroute", "status": 200,
                     "error_type": None}]
        observer = mock.Mock()
        observer.delta.return_value = observed
        queries = mock.Mock()
        queries.delta.return_value = {"messages": 1, "transactions": 0, "writes": 0}
        lifecycle = []

        @contextmanager
        def proxy(django, golang, log, *, container_observer):
            self.assertEqual(django, "host.docker.internal:18000")
            self.assertEqual(golang, django)
            lifecycle.append("start")
            container_observer({"container_id": "owned-control", "image_id": "image"})
            try:
                yield "http://owned-caddy.invalid", "caddy:pinned"
            finally:
                lifecycle.append("remove")

        report = runtime._service_report()
        with mock.patch.object(caddy, "proxy", side_effect=proxy), mock.patch.object(
            portable, "assert_case", return_value=(200, case["response"])
        ) as request, mock.patch.object(runtime.subprocess, "run", return_value=mock.Mock(returncode=1)), mock.patch.object(
            adapter, "snapshot_models", return_value={"model": []}
        ), mock.patch.object(cache, "snapshot_historical_read_cache", return_value=object()), mock.patch.object(
            adapter, "assert_cache_unchanged", return_value={}
        ):
            result = runtime.v2_misroute_control(
                report=report, private_log=io.StringIO(), django_port=18000,
                fixture={"runtime": {"cases": [case]}, "contrasts": object()},
                apps=object(), observer=observer, queries=queries, raw=object(),
            )
        self.assertEqual(request.call_args.args[1], "http://owned-caddy.invalid/api/v2")
        self.assertEqual(result["asgi_delta"], observed)
        self.assertTrue(result["rejected_by_attribution"])
        self.assertEqual(lifecycle, ["start", "remove"])
        self.assertEqual(report["service_cleanup"]["caddy-misroute"], "stopped")

    def test_real_asgi_byte_headers_preserve_correlation_and_query_context(self):
        queries = mock.Mock()
        queries.begin.return_value = "context-token"

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"[]"})

        async def ignore(*args):
            pass

        observer = adapter.AsgiObserver(app, queries)
        asyncio.run(observer({
            "type": "http", "method": "GET", "path": "/communications/messages/list",
            "headers": [(b"x-correlation-id", b"real-marker")],
        }, ignore, ignore))
        self.assertEqual(observer.delta(0)[0]["cid"], "real-marker")
        queries.begin.assert_called_once_with("real-marker")
        queries.end.assert_called_once_with("context-token")

    @staticmethod
    def event(cid="case-one", status=200, path="/communications/messages/list"):
        return {
            "seq": 3,
            "method": "GET",
            "path": path,
            "cid": cid,
            "status": status,
            "error_type": None,
        }

    def test_complete_attribution_accepts_one_django_event_and_no_go_event(self):
        event = self.event()
        self.assertEqual(
            adapter.assert_attribution("django", "GET", event["path"], 200, [event], "case-one"),
            [event],
        )
        self.assertEqual(
            adapter.assert_attribution("go", "GET", event["path"], 200, [], "case-one"),
            [],
        )

    def test_wrong_backend_changed_marker_duplicate_and_runaway_requests_fail(self):
        event = self.event()
        cases = (
            ("go", [event]),
            ("django", []),
            ("django", [event, {**event, "seq": 4}]),
            ("django", [{**event, "cid": "other"}]),
            ("django", [{**event, "status": 500}]),
            ("django", [{**event, "error_type": "RuntimeError"}]),
        )
        for backend, observed in cases:
            with self.subTest(backend=backend, observed=observed):
                with self.assertRaises(adapter.AttributionError):
                    adapter.assert_attribution(
                        backend, "GET", event["path"], 200, observed, "case-one"
                    )

    def test_query_expectations_are_explicit_for_list_detail_and_rejections(self):
        def case(name, *, status, path):
            return {"name": name, "status": status, "path": path}

        cases = (
            (case("message-reads-list-owner", status=200,
                  path="/communications/messages/list"),
             {"messages": 1, "transactions": 0, "writes": 0}),
            (case("message-reads-detail-owner", status=200,
                  path="/communications/messages/x/detail"),
             {"messages": 1, "transactions": 1, "writes": 0}),
            (case("message-reads-detail-empty", status=404,
                  path="/communications/messages/x/detail"),
             {"messages": 1, "transactions": 1, "writes": 0}),
            (case("message-reads-detail-uppercase-uuid", status=200,
                  path="/communications/messages/AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA/detail"),
             {"messages": 1, "transactions": 1, "writes": 0}),
            (case("message-reads-detail-unsupported-type", status=404,
                  path="/communications/messages/x/detail"),
             {"messages": 1, "transactions": 0, "writes": 0}),
            (case("message-reads-list-athlete-options", status=403,
                  path="/communications/messages/list"),
             {"messages": 0, "transactions": 0, "writes": 0}),
            (case("message-reads-list-options", status=200,
                  path="/communications/messages/list"),
             {"messages": 0, "transactions": 0, "writes": 0}),
        )
        for source, expected in cases:
            with self.subTest(case=source["name"]):
                query = {**expected, "association": expected["messages"], "total": 3}
                self.assertEqual(
                    adapter.assert_query_evidence(source, query),
                    expected,
                )
                query["writes"] = 1
                with self.assertRaises(AssertionError):
                    adapter.assert_query_evidence(source, query)

    def test_go_joined_detail_observation_covers_success_empty_and_rejected_rows(self):
        fixture = adapter.load_authored_manifest()
        for name in (
            "message-reads-detail-owner", "message-reads-detail-empty",
            "message-reads-detail-foreign", "message-reads-detail-unsupported-type",
        ):
            case = next(case for case in fixture["cases"] if case["name"] == name)
            query = {"total": 2, "association": 1, "messages": 1,
                     "transactions": 1, "writes": 0}
            with self.subTest(name=name):
                adapter.assert_query_evidence(case, query, backend="go")
                with self.assertRaises(AssertionError):
                    adapter.assert_query_evidence(case, {**query, "transactions": 0}, backend="go")

    def test_complete_durable_state_rejects_missing_rows_and_scalar_type_changes(self):
        before = {
            "application.User": [{"pk": "one", "active": True}],
            "communications.Message": [],
        }
        self.assertEqual(adapter.assert_state_unchanged(before, copy.deepcopy(before))["models"], 2)
        for after in (
            {**copy.deepcopy(before), "communications.Message": [{"pk": "extra"}]},
            {**copy.deepcopy(before), "application.User": [{"pk": "one", "active": 1}]},
            {"application.User": copy.deepcopy(before["application.User"])},
        ):
            with self.subTest(after=after), self.assertRaises(adapter.AcceptanceStateError):
                adapter.assert_state_unchanged(before, after)

    def test_all_key_atomic_cache_mutations_are_rejected(self):
        raw = FakeRedis()
        contrasts = cache.seed_historical_read_cache_contrasts(
            raw, now_ms=1_800_000_000_000
        )
        before = cache.snapshot_historical_read_cache(raw)
        self.assertTrue(adapter.assert_cache_unchanged(before, before, contrasts)["typed_values_exact"])

        key = contrasts.persistent_key
        original = raw.entries[key]
        mutations = (
            replace(original, serialized_value=original.serialized_value + b"x"),
            replace(original, expires_at_ms=1_800_000_999_999),
        )
        for changed in mutations:
            raw.entries[key] = changed
            after = cache.snapshot_historical_read_cache(raw)
            with self.subTest(redis_type=after.entries[key].redis_type):
                with self.assertRaises(adapter.AcceptanceStateError):
                    adapter.assert_cache_unchanged(before, after, contrasts)
            raw.entries[key] = original

        raw.entries[b"unexpected-key"] = FakeEntry(b"string", b"private", -1)
        with self.assertRaises(adapter.AcceptanceStateError):
            adapter.assert_cache_unchanged(
                before, cache.snapshot_historical_read_cache(raw), contrasts
            )


class StructuredPostgresControls(unittest.TestCase):
    def bindings(self):
        return {
            f"distinct-{index}": {
                "owner": adapter._uid(710000 + index),
                "association": adapter._uid(711000 + index),
                "message": adapter._uid(712000 + index),
                "route": "list" if index % 2 == 0 else "detail",
            } for index in range(8)
        }

    def event(self, sql, args=(), *, pid=91, session="synthetic-session", line=1):
        event = {"pid": pid, "session_id": session, "line_num": line,
                 "user": "contracts", "dbname": runtime.DATABASE,
                 "backend_type": "client backend", "error_severity": "LOG",
                 "state_code": "00000", "message": "execute fixture: " + sql}
        if args:
            event["detail"] = "parameters: " + ", ".join(
                f"${index} = '{value}'" for index, value in enumerate(args, 1)
            )
        return event

    def events(self):
        bindings = self.bindings()
        events = []
        # All requests share sessions and switch session between phases. The
        # interleaving cannot be attributed by connection or aggregate division.
        lines = {91: 0, 92: 0}
        for phase in ("account", "association", "data"):
            for index, binding in enumerate(bindings.values()):
                pid = 91 + (index + (phase == "association")) % 2
                lines[pid] += 1
                category = binding["route"] if phase == "data" else phase
                args = {
                    "account": (binding["owner"],),
                    "association": (binding["owner"],),
                    "list": (binding["association"],),
                    "detail": (binding["message"], binding["association"]),
                }[category]
                events.append(self.event(runtime.CORRELATED_SQL[category], args,
                                         pid=pid, session=f"session-{pid}", line=lines[pid]))
        events.append(self.event(runtime.QUERY_LOG_BARRIER, pid=99, session="barrier"))
        events[-1]["message"] = "statement: " + runtime.QUERY_LOG_BARRIER
        return events

    def parse(self, events=None, bindings=None):
        return runtime.parse_postgres_requests(
            "".join(json.dumps(event) + "\n" for event in (self.events() if events is None else events)),
            self.bindings() if bindings is None else bindings,
            barrier=runtime.QUERY_LOG_BARRIER,
        )

    def test_exact_parameters_correlate_across_reused_and_switched_sessions(self):
        result = self.parse()
        self.assertEqual(result["aggregate"], {
            "total": 24, "association": 8, "messages": 8, "transactions": 4, "writes": 0,
        })
        self.assertEqual(set(result["per_request"]), set(self.bindings()))
        for cid, binding in self.bindings().items():
            self.assertEqual(result["per_request"][cid], {
                "total": 3, "association": 1, "messages": 1,
                "transactions": int(binding["route"] == "detail"), "writes": 0,
            })
        encoded = json.dumps(result)
        for binding in self.bindings().values():
            for key in ("owner", "association", "message"):
                self.assertNotIn(binding[key], encoded)
        self.assertNotIn("SELECT", encoded)
        self.assertNotIn("parameters", encoded)

    def test_adversarial_events_fail_closed_without_private_error_output(self):
        def change(index, key, value):
            events = self.events()
            events[index][key] = value
            return events

        cases = {
            "unknown-sql": change(0, "message", "statement: SELECT 'private-sentinel'"),
            "write": change(0, "message", "statement: UPDATE bakney_user SET deleted=true"),
            "hidden-write": change(0, "message", "statement: WITH x AS (DELETE FROM bakney_user RETURNING *) SELECT * FROM x"),
            "wrong-category": change(0, "message", "execute fixture: " + runtime.CORRELATED_SQL["list"]),
            "missing-parameters": change(0, "detail", ""),
            "unknown-parameters": change(0, "detail", "parameters: $1 = 'private-sentinel'"),
            "extra-parameters": change(0, "detail", self.events()[0]["detail"] + ", $2 = 'private-sentinel'"),
            "wrong-database": change(0, "dbname", "other"),
            "wrong-user": change(0, "user", "other"),
            "error": change(0, "error_severity", "ERROR"),
            "missing-session": change(0, "session_id", ""),
            "pid-session-conflict": change(2, "pid", 333),
            "reordered-session": change(2, "line_num", 1),
            "missing-session-line": change(2, "line_num", 900),
            "orphan-detail": change(0, "message", "parameters: $1 = 'private-sentinel'"),
        }
        events = self.events()
        events[0]["detail"], events[1]["detail"] = events[1]["detail"], events[0]["detail"]
        # Both still map to known owners but repeat a later request's account.
        events[0]["detail"] = events[1]["detail"]
        cases["interleaved-details"] = events
        events = self.events()
        events[17]["detail"] = "parameters: $1 = '%s', $2 = '%s'" % (
            self.bindings()["distinct-1"]["message"], self.bindings()["distinct-3"]["association"])
        cases["wrong-scope"] = events
        cases["missing-statement"] = self.events()[1:]
        cases["zero-work"] = self.events()[-1:]
        cases["extra-statement"] = self.events()[:-1] + [self.events()[0], self.events()[-1]]
        cases["retry"] = [self.events()[0], *self.events()]
        events = self.events()
        events[1]["detail"] = events[0]["detail"]
        cases["unchanged-aggregate-redistributed-work"] = events
        cases["no-barrier"] = self.events()[:-1]
        cases["extra-after-barrier"] = [*self.events(), self.events()[0]]
        cases["barrier-only-explicit"] = change(-1, "message", "statement: SELECT 1")
        # Data before association is forbidden even on another connection.
        events = self.events()
        events[0], events[16] = events[16], events[0]
        cases["request-order"] = events
        for name, events in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, "PostgreSQL evidence") as caught:
                self.parse(events)
            self.assertNotIn("private-sentinel", str(caught.exception))
            self.assertNotIn("SELECT", str(caught.exception))

    def test_ambiguous_bindings_fail_before_counting(self):
        for key in ("owner", "association", "message"):
            bindings = self.bindings()
            bindings["distinct-1"][key] = bindings["distinct-0"][key]
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.parse(bindings=bindings)

    def test_json_is_strict_and_never_exposes_decoder_or_private_values(self):
        valid = "".join(json.dumps(event) + "\n" for event in self.events())
        for content in (valid[:-1], "private-sentinel\n", valid + "{}\n",
                        valid.replace('"pid": 91', '"pid": 91, "pid": 91', 1)):
            with self.subTest(content=content[:20]), self.assertRaisesRegex(AssertionError, "PostgreSQL evidence") as caught:
                runtime.parse_postgres_requests(content, self.bindings(), barrier=runtime.QUERY_LOG_BARRIER)
            self.assertNotIn("private-sentinel", str(caught.exception))


class NativeLogBoundaryControls(unittest.TestCase):
    # Reuse authorship helpers without rerunning the parser test methods.
    bindings = StructuredPostgresControls.bindings
    event = StructuredPostgresControls.event
    events = StructuredPostgresControls.events
    parse = StructuredPostgresControls.parse

    def test_diagnostics_retain_exact_probe_in_raw_total(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.observer(directory)
            mark = observer.mark()
            events = self.events()[:-1]
            probe = self.event("-- ping", pid=111, session="probe")
            probe["message"] = "statement: -- ping"
            with observer.sql_log.open("a") as stream:
                stream.writelines(json.dumps(event) + "\n" for event in [*events, probe])
            evidence = observer.since_with_diagnostics(mark)
        self.assertEqual(evidence["query"], {
            "total": 25, "association": 8, "messages": 8, "transactions": 4, "writes": 0,
        })
        self.assertEqual(evidence["statement_counts"], {
            "total": 25, "business": 24, "readiness_transport_probe": 1, "unknown": 0,
        })
        encoded = json.dumps(evidence)
        for private in ("SELECT", "-- ping", "parameters", "synthetic-session", adapter._uid(710000)):
            self.assertNotIn(private, encoded)

    def test_diagnostics_never_classify_malformed_controls_or_unknown_sql_as_probes(self):
        variants = ["-- ping ", " -- ping", "-- PING", "-- ping\nSELECT 1",
                    "-- ping\nUPDATE bakney_user SET deleted=true", "-- ping;",
                    "SELECT 1", runtime.QUERY_LOG_BARRIER.replace(" */", ":" + "a" * 32 + " */"),
                    runtime.QUERY_LOG_BARRIER + " SELECT 1", "private-sentinel"]
        events = [self.event(sql, pid=200 + i, session=f"unknown-{i}")
                  for i, sql in enumerate(variants)]
        for event in events:
            event["message"] = "statement: " + event["message"].split(": ", 1)[1]
        # Exact text with parameters or extended-protocol envelope is also unknown.
        events.extend([self.event("-- ping", (adapter._uid(710000),), pid=300, session="args"),
                       self.event("-- ping", pid=301, session="execute")])
        events.append(self.events()[-1])
        evidence = runtime.postgres_statement_diagnostics(
            "".join(json.dumps(event) + "\n" for event in events),
            barrier=runtime.QUERY_LOG_BARRIER,
        )
        self.assertEqual(evidence["query"]["total"], len(variants) + 2)
        self.assertEqual(evidence["statement_counts"], {
            "total": len(variants) + 2, "business": 0,
            "readiness_transport_probe": 0, "unknown": len(variants) + 2,
        })

    def test_diagnostics_require_canonical_explicit_barrier_and_complete_business_args(self):
        for barrier in ("SELECT 1", "-- ping", runtime.QUERY_LOG_BARRIER + " SELECT 1"):
            event = self.event(barrier)
            event["message"] = "statement: " + barrier
            with self.subTest(barrier=barrier), self.assertRaises(AssertionError):
                runtime.postgres_statement_diagnostics(json.dumps(event) + "\n", barrier=barrier)
        events = self.events()
        events[0]["detail"] += ", $2 = 'private-sentinel'"
        evidence = runtime.postgres_statement_diagnostics(
            "".join(json.dumps(event) + "\n" for event in events),
            barrier=runtime.QUERY_LOG_BARRIER,
        )
        self.assertEqual(evidence["statement_counts"]["unknown"], 1)
        self.assertEqual(evidence["statement_counts"]["business"], 23)
        self.assertNotIn("private-sentinel", json.dumps(evidence))

    def test_diagnostic_barriers_and_event_integrity_still_fail_closed(self):
        events = self.events()
        malformed_barrier = copy.deepcopy(events[-1])
        malformed_barrier["detail"] = "parameters: $1 = 'private-sentinel'"
        variants = [events[:-1], [*events, events[-1]], [*events, events[0]],
                    [*events[:-1], malformed_barrier]]
        for records in variants:
            with self.subTest(records=len(records)), self.assertRaises(AssertionError):
                runtime.postgres_statement_diagnostics(
                    "".join(json.dumps(event) + "\n" for event in records),
                    barrier=runtime.QUERY_LOG_BARRIER,
                )
        valid = "".join(json.dumps(event) + "\n" for event in events)
        for content in (valid[:-1], valid.replace('"pid": 91', '"pid": 91, "pid": 91', 1)):
            with self.assertRaises(AssertionError):
                runtime.postgres_statement_diagnostics(content, barrier=runtime.QUERY_LOG_BARRIER)

    def test_probes_are_neither_filtered_nor_assigned_to_distinct_requests(self):
        events = self.events()
        probes = [self.event("-- ping", pid=400 + i, session=f"probe-{i}") for i in range(2)]
        for event in probes:
            event["message"] = "statement: -- ping"
        records = [*events[:-1], *probes, events[-1]]
        content = "".join(json.dumps(event) + "\n" for event in records)
        evidence = runtime.postgres_statement_diagnostics(content, barrier=runtime.QUERY_LOG_BARRIER)
        self.assertEqual(evidence["query"]["total"], 26)
        self.assertEqual(evidence["statement_counts"]["readiness_transport_probe"], 2)
        with self.assertRaisesRegex(AssertionError, "unknown SQL category or extra statement"):
            runtime.parse_postgres_requests(content, self.bindings(), barrier=runtime.QUERY_LOG_BARRIER)

    def test_unknown_writes_and_private_fields_never_disappear_from_diagnostics(self):
        events = self.events()
        for i, sql in enumerate(("UPDATE bakney_user SET deleted=true",
                                 "WITH x AS (DELETE FROM bakney_user RETURNING *) SELECT * FROM x")):
            event = self.event(sql, pid=500 + i, session=f"private-sentinel-{i}")
            event["message"] = "statement: " + sql
            events.insert(-1, event)
        evidence = runtime.postgres_statement_diagnostics(
            "".join(json.dumps(event) + "\n" for event in events), barrier=runtime.QUERY_LOG_BARRIER)
        self.assertEqual(evidence["query"]["total"], 26)
        self.assertEqual(evidence["query"]["writes"], 1)
        self.assertEqual(evidence["statement_counts"]["unknown"], 2)
        self.assertNotIn("private-sentinel", json.dumps(evidence))
        self.assertEqual(runtime.safe_query_counts({"total": 1, "SQL": "private-sentinel"}, ("total",)),
                         {"total": 1})
        for invalid in (True, -1, "private-sentinel", None):
            with self.assertRaisesRegex(AssertionError, "invalid diagnostic counters") as caught:
                runtime.safe_query_counts({"total": invalid}, ("total",))
            self.assertNotIn("private-sentinel", str(caught.exception))

    def observer(self, directory):
        observer = runtime.PostgresQueryLog.__new__(runtime.PostgresQueryLog)
        observer.sql_log = Path(directory) / "events.json"
        observer.private_log = io.StringIO()
        observer._drained = None
        observer.sql_log.write_text(json.dumps({"message": "prior startup event"}) + "\n")

        def command(tokens, mode):
            self.assertEqual(mode, "--command")
            # Unique session per barrier, just as each psql process creates one.
            event = self.event(tokens[0], pid=999, session="boundary-" + tokens[0])
            event["message"] = "statement: " + tokens[0]
            with observer.sql_log.open("a") as stream:
                stream.write(json.dumps(event) + "\n")
        observer.command = mock.Mock(side_effect=command)
        return observer

    def test_real_observer_drains_prior_events_then_correlates_whole_window(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.observer(directory)
            mark = observer.mark()
            with observer.sql_log.open("a") as stream:
                stream.writelines(json.dumps(event) + "\n" for event in self.events()[:-1])
            result = observer.since_by_request(mark, self.bindings())
        self.assertEqual(result["aggregate"]["total"], 24)
        self.assertEqual(observer.command.call_count, 2)
        self.assertNotEqual(observer.command.call_args_list[0].args[0], observer.command.call_args_list[1].args[0])

    def test_only_exact_current_barrier_is_excluded_from_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.observer(directory)
            mark = observer.mark()
            event = self.event(runtime.QUERY_LOG_BARRIER, pid=111, session="extra")
            event["message"] = "statement: " + runtime.QUERY_LOG_BARRIER
            with observer.sql_log.open("a") as stream:
                stream.write(json.dumps(event) + "\n")
            self.assertEqual(observer.since(mark)["total"], 1)

    def test_rotated_or_truncated_private_log_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.observer(directory)
            mark = observer.mark()
            for bad in ({**mark, "identity": (0, 0)}, {**mark, "offset": 10**9}):
                with self.subTest(mark=bad), self.assertRaisesRegex(AssertionError, "rotated or truncated"):
                    observer.since_by_request(bad, self.bindings())

    def test_missing_collector_barrier_cannot_return_empty_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.observer(directory)
            mark = observer._boundary()
            observer.command = mock.Mock()
            with mock.patch.object(runtime.time, "monotonic", side_effect=[0, 6]):
                with self.assertRaisesRegex(AssertionError, "barrier was not observed"):
                    observer.since_by_request(mark, self.bindings())

    def test_native_success_state_code_may_be_omitted(self):
        events = self.events()
        for event in events:
            event.pop("state_code")
        self.assertEqual(self.parse(events)["aggregate"]["total"], 24)


class PortableRestoreAdmissionControls(unittest.TestCase):
    # Authored independently of the production repository and runtime allowlist.
    restore_sql = "SELECT EXISTS (SELECT 1 FROM instance_datarestore WHERE state IN ($1, $2))"
    names = ("message-reads-list-post-method", "message-reads-list-athlete-post",
             "message-reads-detail-athlete-post")

    def workload(self, name, *, mutation=None, probes=0, versioned=False):
        from TESTS.contracts import test_message_reads as portable

        case = copy.deepcopy(next(row for row in adapter.load_authored_manifest()["cases"]
                                  if row["name"] == name))
        actor = case.pop("actor")
        case["token"] = "private-token-sentinel"
        owner = adapter._uid(700001 if actor == "owner" else 700004)
        helper = StructuredPostgresControls()
        events = [helper.event(self.restore_sql, ("queued", "running"), session="restore"),
                  helper.event(runtime.CORRELATED_SQL["account"], (owner,), pid=92, session="account")]
        if mutation == "wrong-states":
            events[0]["detail"] = "parameters: $1 = 'running', $2 = 'queued'"
        elif mutation == "extra-argument":
            events[0]["detail"] += ", $3 = 'private-argument-sentinel'"
        elif mutation == "missing-arguments":
            events[0].pop("detail")
        elif mutation == "wrong-protocol":
            events[0]["message"] = "statement: " + self.restore_sql
        elif mutation == "different-shape":
            events[0]["message"] += " OR true"
        elif mutation == "foreign-account":
            events[1]["detail"] = "parameters: $1 = '" + adapter._uid(799999) + "'"
        elif mutation == "missing-restore":
            events.pop(0)
        elif mutation == "duplicate-restore":
            events.insert(1, helper.event(self.restore_sql, ("queued", "running"), pid=93, session="extra"))
        elif mutation == "account-instead-of-restore":
            events[0] = helper.event(runtime.CORRELATED_SQL["account"], (owner,), session="extra")
        elif mutation == "reordered":
            events.reverse()
        elif mutation == "audit-write":
            events[0] = helper.event("INSERT INTO auditlog_logentry (object_pk) VALUES ($1)",
                                     ("private-argument-sentinel",), session="audit")
        elif mutation == "wrong-method":
            case["method"] = "HEAD"
        elif mutation == "wrong-route":
            case["path"] = "/communications/messages/other/detail"
        elif mutation == "unlisted-case":
            case["name"] = "unlisted-post"
        for i in range(probes):
            event = helper.event("-- ping", pid=200 + i, session=f"probe-{i}")
            event["message"] = "statement: -- ping"
            events.insert(1, event)
        if mutation == "malformed-probe":
            events[1]["message"] += " "
        barrier = helper.event(runtime.QUERY_LOG_BARRIER, pid=99, session="barrier")
        barrier["message"] = "statement: " + runtime.QUERY_LOG_BARRIER
        content = "".join(json.dumps(event) + "\n" for event in [*events, barrier])
        postgres = runtime.PostgresQueryLog.__new__(runtime.PostgresQueryLog)
        postgres.mark = mock.Mock(return_value=object())
        postgres._through_barrier = mock.Mock(return_value=(content, runtime.QUERY_LOG_BARRIER))
        if mutation == "private-exception":
            postgres._through_barrier.side_effect = AssertionError("SELECT private-token-sentinel")
        fixture = {"runtime": {"cases": [case]}, "runtime_path": Path("/unused"),
                   "contrasts": object(), "baseline_state": {"model": []}, "baseline_cache": object()}
        observer = mock.Mock()
        observer.delta.return_value = []
        status = 200 if mutation == "wrong-status" else case["status"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))
            postgres.observations = runtime.MessageQueryObservations(path)

            def response(*args, **kwargs):
                path.write_bytes(observation_fixture.encode(observation_fixture.stream([
                    observation_fixture.request(1, queries=2, connects=0, resets=2,
                                                probes=0 if mutation == "unattested-probe" else probes)])))
                return status, case["response"], {
                    "X-Query-Observation-ID": ("private-token-sentinel" if mutation == "invalid-header"
                                                else observation_fixture.RUN + "-1"),
                    "Content-Language": "en", "Allow": "GET, OPTIONS",
                    "Content-Type": "application/json",
                }

            with mock.patch.object(portable, "load_manifest", return_value=fixture["runtime"]), mock.patch.object(
                portable.Client, "request", side_effect=response
            ), mock.patch.object(adapter, "snapshot_models", return_value={"model": []}) as snapshots, mock.patch.object(
                cache, "snapshot_historical_read_cache", return_value={}
            ), mock.patch.object(adapter, "assert_cache_unchanged", return_value={}):
                result = runtime.run_portable_workload(
                    label="caddy-v2" if versioned else "go-native", backend="go",
                    base_url="http://offline.invalid/api/v2" if versioned else "http://offline.invalid",
                    fixture=fixture, apps=object(), observer=observer, queries=mock.Mock(),
                    raw=object(), private_log=io.StringIO(), postgres_queries=postgres,
                )
            self.assertGreaterEqual(snapshots.call_count, 2)
        return result, postgres

    def test_three_authored_rejections_require_restore_then_account_in_both_transports(self):
        for name in self.names:
            for versioned in (False, True):
                for probes in (0, 1, 2):
                    with self.subTest(name=name, versioned=versioned, probes=probes):
                        result, postgres = self.workload(name, probes=probes, versioned=versioned)
                        self.assertTrue(result["success"], result["requests"])
                        proof = result["query_observation_headers"][0]
                        self.assertEqual(proof["business"], 2)
                        self.assertEqual(proof["raw_expected"], 2 + probes)
                        self.assertEqual(proof["postgres_statement_counts"], {
                            "total": 2 + probes, "business": 2,
                            "readiness_transport_probe": probes, "unknown": 0})
                        self.assertTrue(proof["split_accounting_verified"])
                        self.assertEqual(proof["restore_pending_reads"], 1)
                        self.assertEqual(result["requests"][0]["query"]["writes"], 0)
                        self.assertIsNone(postgres.portable_observation)
                        self.assertIsNone(postgres.portable_expectation)

    def test_restore_allowance_is_exact_case_scoped_and_never_hides_other_sql(self):
        mutations = ("wrong-states", "extra-argument", "missing-arguments", "wrong-protocol", "different-shape",
                     "foreign-account", "missing-restore", "duplicate-restore",
                     "account-instead-of-restore", "reordered", "audit-write", "wrong-status",
                     "wrong-method", "wrong-route", "unlisted-case", "malformed-probe", "unattested-probe")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result, postgres = self.workload(self.names[0], mutation=mutation, probes=1)
                self.assertFalse(result["success"])
                proof = result["query_observation_headers"][0]
                self.assertFalse(proof["split_accounting_verified"])
                self.assertIn("failure_reason", proof)
                self.assertEqual(proof["query"]["total"],
                                 2 if mutation == "missing-restore" else 4 if mutation == "duplicate-restore" else 3)
                if mutation == "audit-write":
                    self.assertEqual(proof["query"]["writes"], 1)
                    self.assertEqual(proof["postgres_statement_counts"]["unknown"], 1)
                if mutation == "foreign-account":
                    self.assertEqual(proof["failure_reason"], "account_scope")
                if mutation in ("reordered", "account-instead-of-restore"):
                    self.assertEqual(proof["failure_reason"], "restore_account_sequence")
                self.assertIsNone(postgres.portable_observation)
                self.assertIsNone(postgres.portable_expectation)
                for private in ("SELECT", "INSERT", "parameters", "private-argument-sentinel",
                                "private-token-sentinel", adapter._uid(700001), adapter._uid(799999)):
                    self.assertNotIn(private, json.dumps(proof))

    def test_untrusted_exception_text_is_never_published_and_pending_proof_is_cleared(self):
        result, postgres = self.workload(self.names[0], mutation="private-exception")
        self.assertFalse(result["success"])
        proof = result["query_observation_headers"][0]
        self.assertEqual(proof["failure_reason"], "portable_reconciliation_failed")
        self.assertNotIn("private-token-sentinel", json.dumps(result))
        self.assertIsNone(postgres.portable_observation)

    def test_header_observation_failure_has_safe_reason_and_rejects_workload(self):
        result, postgres = self.workload(self.names[0], mutation="invalid-header")
        self.assertFalse(result["success"])
        self.assertEqual(result["query_observation_headers"], [{
            "split_accounting_verified": False, "failure_stage": "response_observation",
            "failure_reason": "response_observation_failed"}])
        self.assertNotIn("private-token-sentinel", json.dumps(result))
        postgres._through_barrier.assert_not_called()

    def test_restore_query_remains_unknown_and_rejected_in_growth_and_distinct_windows(self):
        helper = StructuredPostgresControls()
        events = helper.events()
        events.insert(0, helper.event(self.restore_sql, ("queued", "running"), pid=500, session="restore"))
        content = "".join(json.dumps(event) + "\n" for event in events)
        diagnostic = runtime.postgres_statement_diagnostics(content, barrier=runtime.QUERY_LOG_BARRIER)
        self.assertEqual(diagnostic["statement_counts"]["unknown"], 1)
        self.assertEqual(diagnostic["query"]["total"], 25)
        proof = {"per_request": {str(i): {} for i in range(8)}, "business": 24,
                 "raw_expected": 25, "reset_probes": 1}
        with self.assertRaisesRegex(AssertionError, "unknown SQL category or extra statement"):
            runtime.parse_postgres_requests(content, helper.bindings(), barrier=runtime.QUERY_LOG_BARRIER,
                                            driver_proof=proof)
        receipt = {}
        with self.assertRaisesRegex(AssertionError, "unknown SQL category or extra statement"):
            runtime.reconcile_message_postgres(content, helper.bindings()["distinct-0"], proof,
                                                barrier=runtime.QUERY_LOG_BARRIER, receipt=receipt)
        self.assertEqual(receipt["postgres_statement_counts"]["unknown"], 1)
        self.assertEqual(receipt["query"]["total"], 25)


class IndependentFixtureControls(unittest.TestCase):
    def test_authored_fixtures_have_disjoint_scopes_and_full_distinct_dtos(self):
        rows = runtime.distinct_owner_specifications()
        identifiers = [row["binding"][key] for row in rows for key in ("owner", "association", "message")]
        identifiers += [row["transaction_id"] for row in rows]
        self.assertEqual(len(set(identifiers)), 32)
        self.assertEqual(rows[0]["expected"], [{
            "message_id": adapter._uid(712000), "sport_association": adapter._uid(711000),
            "type": "EMAIL", "message": "Independent &lt;0&gt; &amp; &quot;body&quot;",
            "subject": "None", "created_at": "2026-10-24T15:00:00Z",
        }])
        self.assertEqual(rows[1]["expected"], [{
            "message_transaction_id": adapter._uid(713001), "message": adapter._uid(712001),
            "recipient": "independent-1@example.test", "sent_on": "2026-10-24T16:00:00Z",
            "delivered_on": None, "opened_on": None, "clicked_on": None, "bounced_on": None,
            "bounced_reason": None, "bounced_type": None, "bounced_status": None,
        }])
        self.assertEqual({row["binding"]["route"] for row in rows}, {"list", "detail"})
        self.assertEqual(len({json.dumps(row["expected"], sort_keys=True) for row in rows}), 8)

    def test_seed_helper_tracks_every_row_for_reverse_cleanup_and_tokens_for_redaction(self):
        def model():
            class Model:
                ASSOCIATION = 3
                EMAIL = "EMAIL"
                _base_manager = mock.Mock()
                _base_manager.filter.return_value.exists.return_value = False

                def __init__(self, pk, **values):
                    self.pk = pk
                    self.__dict__.update(values)
            return Model
        user, association, message, delivery = (model() for _ in range(4))

        class Token(dict):
            def __str__(self):
                return "private-token-for-" + self["user_id"]

        modules = {
            "application.models": SimpleNamespace(User=user, SportAssociation=association),
            "communications.models": SimpleNamespace(Message=message, MessageTransaction=delivery),
            "auditlog.context": SimpleNamespace(disable_auditlog=lambda: mock.MagicMock()),
            "django.db": SimpleNamespace(transaction=SimpleNamespace(atomic=lambda: mock.MagicMock())),
            "rest_framework_simplejwt.tokens": SimpleNamespace(AccessToken=Token),
        }
        created, private = [], set()
        with mock.patch.dict(sys.modules, modules):
            rows = runtime.seed_distinct_owner_fixture(created, private)
        self.assertEqual(len(created), 32)
        self.assertEqual(len(private), 8)
        self.assertEqual(private, {row["token"] for row in rows})
        self.assertEqual([model for model, _ in created], [user, association, message, delivery] * 8)
        self.assertEqual(user._base_manager.bulk_create.call_count, 8)
        self.assertEqual(message._base_manager.filter.return_value.update.call_count, 8)
        self.assertEqual(delivery._base_manager.filter.return_value.update.call_count, 8)
        self.assertEqual(message._base_manager.bulk_create.call_args_list[0].args[0][0].message,
                         'Independent <0> & "body"')
        user._base_manager.filter.return_value.exists.return_value = True
        with mock.patch.dict(sys.modules, modules), self.assertRaisesRegex(AssertionError, "collision"):
            runtime.seed_distinct_owner_fixture([], set())
        self.assertEqual(user._base_manager.bulk_create.call_count, 8)

    def run_batch(self, mutation=None):
        rows = runtime.distinct_owner_specifications()
        for row in rows:
            row["token"] = "private-token-" + row["cid"]
        helper = StructuredPostgresControls()
        evidence = helper.parse()
        evidence["per_request"] = {cid.replace("distinct-", "distinct-go-"): counts
                                   for cid, counts in evidence["per_request"].items()}
        if mutation == "redistribution":
            evidence["per_request"]["distinct-go-0"]["total"] = 2
            evidence["per_request"]["distinct-go-1"]["total"] = 4
        if mutation == "missing":
            evidence["per_request"].pop("distinct-go-0")
        if mutation == "extra":
            evidence["per_request"]["extra"] = evidence["per_request"]["distinct-go-0"]
        if mutation == "writes":
            evidence["aggregate"]["writes"] = 1
        postgres = mock.Mock()
        postgres.since_by_request.return_value = evidence
        observer = mock.Mock()
        observer.delta.return_value = [{}] if mutation == "misroute" else []
        states = [{"model": []}, {"model": [{}]}] if mutation == "state" else [{"model": []}] * 2

        def request(base, token, path, cid):
            row = rows[int(cid.rsplit("-", 1)[1])]
            self.assertEqual(token, row["token"])
            self.assertEqual(path, row["path"])
            content = copy.deepcopy(row["expected"])
            if mutation == "response":
                content = rows[(int(cid.rsplit("-", 1)[1]) + 2) % 8]["expected"]
            return 200, content, {"Content-Language": "en", "Allow": "GET, OPTIONS",
                                  "Content-Type": "application/json"}

        private = set()
        with mock.patch.object(runtime, "seed_distinct_owner_fixture", return_value=rows), mock.patch.object(
            runtime, "_request", side_effect=request
        ), mock.patch.object(adapter, "snapshot_models", side_effect=states), mock.patch.object(
            cache, "snapshot_historical_read_cache", return_value=object()
        ), mock.patch.object(adapter, "assert_cache_unchanged", side_effect=(
            adapter.AcceptanceStateError("cache mutation") if mutation == "cache" else None
        )):
            result = runtime.run_distinct_owner_queries(
                apps=object(), fixture={"contrasts": object()}, raw=object(), observer=observer,
                go_base="go", owned_postgres_log=postgres, created=[], private_values=private,
            )
        postgres.since_by_request.assert_called_once_with(
            postgres.mark.return_value, {row["cid"]: row["binding"] for row in rows},
        )
        return result

    def test_distinct_batch_never_promotes_acceptance_or_claims_same_owner_counts(self):
        result = self.run_batch()
        self.assertTrue(result["per_request_query_budgets_verified"])
        self.assertFalse(result["parent_review_required"])
        self.assertFalse(result["acceptance_gate"])
        self.assertFalse(result["same_owner_per_request_counts_measured"])
        encoded = json.dumps(result)
        self.assertNotIn("private-token", encoded)
        self.assertNotIn(adapter._uid(710000), encoded)
        self.assertNotIn("SELECT", encoded)

    def test_distinct_batch_rejects_scope_response_state_cache_and_count_mutations(self):
        for mutation in ("redistribution", "missing", "extra", "writes", "misroute", "state", "cache", "response"):
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                self.run_batch(mutation)


class GrowthBoundaryControls(unittest.TestCase):
    def run_growth(self, mutation=None, progress=None):
        """Exercise the real growth runner with only ORM and transport replaced."""
        authored = adapter.load_authored_manifest()
        base = copy.deepcopy(next(row["response"] for row in authored["cases"]
                                  if row["name"] == "message-reads-list-owner"))
        requests = []
        events = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        observation_path = Path(directory.name) / "private.jsonl"
        observation_rows = []
        observation_path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))
        observations = runtime.MessageQueryObservations(observation_path)

        class Row:
            EMAIL = "EMAIL"
            _base_manager = mock.Mock()

            def __init__(self, **values):
                self.__dict__.update(values)

        def query(cid):
            detail = "-detail-" in cid
            count = 1
            if mutation == "redistributed" and "concurrent-django-" in cid:
                count = {"0": 0, "1": 2}.get(cid.rsplit("-", 1)[-1], 1)
            return {"total": 3 if detail or "go-" in cid else 2, "association": 1,
                    "messages": count, "transactions": int(detail), "writes": 0}

        def expected(size, detail):
            if detail:
                return [{
                    "message_transaction_id": adapter._uid(705000 + index),
                    "message": adapter._uid(709001),
                    "recipient": f"growth-{index:02d}@example.test",
                    "sent_on": f"2026-10-23T13:00:00.{index:06d}Z",
                    "delivered_on": None, "opened_on": None, "clicked_on": None,
                    "bounced_on": None, "bounced_reason": None,
                    "bounced_type": None, "bounced_status": None,
                } for index in range(size, 0, -1)]
            rows = [{
                "message_id": adapter._uid(709100 + index),
                "sport_association": adapter._uid(701001), "type": "EMAIL",
                "message": f"Growth list {index:02d}", "subject": f"growth {index:02d}",
                "created_at": f"2026-10-23T14:00:00.{index:06d}Z",
            } for index in range(size, 0, -1)]
            return [base[0], *rows, {
                "message_id": adapter._uid(709001),
                "sport_association": adapter._uid(701001), "type": "EMAIL",
                "message": "Growth detail", "subject": "growth detail",
                "created_at": "2026-10-23T14:00:00Z",
            }, *base[1:]]

        Row._base_manager.get.return_value.pk = adapter._uid(701001)

        def request(url, token, path, cid):
            del token
            requests.append(cid)
            concurrent = "concurrent" in cid
            size = 48 if concurrent else int(cid.rsplit("-", 1)[-1])
            content = expected(size, path.endswith("/detail"))
            if mutation == "duplicate" or (mutation == "concurrent-duplicate" and concurrent):
                content = [content[0]] * len(content)
            if url == "django":
                events.append({"seq": len(events), "method": "GET", "path": path,
                               "cid": cid, "status": 200, "error_type": None})
            headers = {"Content-Language": "en", "Allow": "GET, OPTIONS",
                       "Content-Type": "application/json"}
            if url == "go":
                probes = int(mutation == "attested-probe-growth" and cid == "growth-go-list-48")
                row = observation_fixture.request(len(observation_rows) + 1, probes=probes)
                row["active_requests_at_start"] = int(cid.rsplit("-", 1)[1]) + 1 if concurrent else 1
                observation_rows.append(row)
                observation_path.write_bytes(observation_fixture.encode([
                    observation_fixture.record("header"), *observation_rows]))
                headers["X-Query-Observation-ID"] = row["request_id"]
            return 200, content, headers

        postgres = mock.Mock()
        postgres.mark.side_effect = lambda: len(requests)
        postgres.since.side_effect = lambda mark: {
            key: sum(query(cid)[key] for cid in requests[mark:])
            for key in ("total", "association", "messages", "transactions", "writes")
        }
        def measured(mark, binding, proof, *, receipt):
            helper = StructuredPostgresControls()
            records = []
            for cid in requests[mark:]:
                route = "detail" if "-detail-" in cid else "list"
                for category in ("account", "association", route):
                    args = {"account": (binding["owner"],), "association": (binding["owner"],),
                            "list": (binding["association"],),
                            "detail": (binding["message"], binding["association"])}[category]
                    records.append(helper.event(runtime.CORRELATED_SQL[category], args,
                                                line=len(records) + 1))
                if mutation in ("probe-growth", "attested-probe-growth") and cid == "growth-go-list-48":
                    probe = helper.event("-- ping", line=len(records) + 1)
                    probe["message"] = "statement: -- ping"
                    records.append(probe)
                if mutation in ("unknown", "write") and cid == "growth-go-list-8":
                    unknown = helper.event("SELECT 42" if mutation == "unknown" else "UPDATE bakney_user SET deleted=true",
                                           line=len(records) + 1)
                    records.append(unknown)
            barrier = helper.event(runtime.QUERY_LOG_BARRIER, pid=99, session="boundary")
            barrier["message"] = "statement: " + runtime.QUERY_LOG_BARRIER
            records.append(barrier)
            return runtime.reconcile_message_postgres(
                "".join(json.dumps(row) + "\n" for row in records), binding, proof,
                barrier=runtime.QUERY_LOG_BARRIER, receipt=receipt,
            )
        postgres.since_message.side_effect = measured

        def offline_cohort(query_log, observed, send, cids, *, receipt):
            # Offline growth orchestration only. Lock mechanics have separate
            # fake-driver controls; no runtime overlap is asserted by this test.
            mark, observed_mark = query_log.mark(), observed.mark()
            responses = [send(cid) for cid in cids]
            return mark, observed_mark, responses

        queries = mock.Mock()
        queries.delta.side_effect = query
        observer = mock.Mock()
        observer.cursor.side_effect = lambda: len(events)
        observer.delta.side_effect = lambda cursor: copy.deepcopy(events[cursor:])
        modules = {
            "application.models": SimpleNamespace(SportAssociation=Row),
            "communications.models": SimpleNamespace(Message=Row, MessageTransaction=Row),
            "auditlog.context": SimpleNamespace(disable_auditlog=lambda: mock.MagicMock()),
        }
        with mock.patch.dict(sys.modules, modules), mock.patch.object(
            runtime, "_request", side_effect=request
        ), mock.patch.object(adapter, "snapshot_models", return_value={"model": []}), mock.patch.object(
            cache, "snapshot_historical_read_cache", return_value=object()
        ), mock.patch.object(adapter, "assert_cache_unchanged", return_value={}), mock.patch.object(
            runtime, "run_distinct_owner_queries", return_value={"parent_owned_live_evidence": True}
        ), mock.patch.object(runtime, "run_locked_same_owner_batch", side_effect=offline_cohort):
            return runtime.run_growth_queries(
                apps=object(), fixture={"tokens": {"owner": "synthetic"},
                                        "authored": authored, "contrasts": object(),
                                        "baseline_state": {"model": []}, "baseline_cache": object()},
                raw=object(), observer=observer, queries=queries,
                django_base="django", go_base="go", owned_postgres_log=postgres, created=[],
                progress=progress, observations=observations,
            )

    def test_growth_retains_counts_before_unattested_probe_failure(self):
        progress = {}
        with self.assertRaisesRegex(AssertionError, "raw and driver probe counts differ"):
            self.run_growth("probe-growth", progress)
        self.assertFalse(progress["success"])
        self.assertFalse(progress["acceptance_gate"])
        self.assertFalse(progress["bounded_checks_passed"])
        rows = progress["routes"]["go"]["list"]
        self.assertEqual([row["query"]["total"] for row in rows], [3, 3, 4])
        self.assertEqual([row["postgres_statement_counts"]["business"] for row in rows], [3, 3, 3])
        self.assertEqual(rows[-1]["postgres_statement_counts"]["readiness_transport_probe"], 1)
        self.assertEqual(progress["concurrency"], {})

    def test_growth_accepts_variable_raw_totals_only_with_driver_attestation(self):
        result = self.run_growth("attested-probe-growth")
        self.assertEqual([row["query"]["total"] for row in result["routes"]["go"]["list"]], [3, 3, 4])
        self.assertTrue(result["bounded_checks_passed"])
        self.assertTrue(result["success"])
        self.assertFalse(result["acceptance_gate"])

    def test_growth_retains_sample_before_response_unknown_or_write_failure(self):
        for mutation in ("duplicate", "unknown", "write"):
            progress = {}
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                self.run_growth(mutation, progress)
            self.assertFalse(progress["acceptance_gate"])
            if mutation == "duplicate":
                self.assertEqual(progress["routes"]["django"]["list"][0]["size"], 8)
            else:
                sample = progress["routes"]["go"]["list"][0]
                self.assertEqual(sample["postgres_statement_counts"]["unknown"], 1)
                self.assertEqual(sample["query"]["writes"], int(mutation == "write"))

    def test_growth_rejects_repeated_rows_of_the_right_length(self):
        with self.assertRaises(AssertionError):
            self.run_growth("duplicate")

    def test_concurrent_responses_reject_repeated_rows_of_the_right_length(self):
        with self.assertRaises(AssertionError):
            self.run_growth("concurrent-duplicate")

    def test_concurrent_query_budgets_cannot_be_redistributed_between_cids(self):
        with self.assertRaises(AssertionError):
            self.run_growth("redistributed")

    def test_completed_growth_subgate_cannot_promote_final_read_scope_acceptance(self):
        result = self.run_growth()
        self.assertTrue(result["success"])
        self.assertFalse(result["acceptance_gate"])
        self.assertTrue(result["concurrency"]["go"]["per_request_query_budgets_verified"])
        self.assertEqual(len(result["concurrency"]["go"]["per_request_queries"]), 8)
        self.assertFalse(result["parent_review_required"])
        self.assertEqual(result["blockers"], [])


class SplitPostgresControls(unittest.TestCase):
    def sample(self, *, count=1, probes=1, route="list"):
        helper = StructuredPostgresControls()
        binding = {"owner": adapter._uid(700001), "association": adapter._uid(701001),
                   "message": adapter._uid(709001), "route": route}
        events = []
        for _ in range(count):
            for category in ("account", "association", route):
                args = {"account": (binding["owner"],), "association": (binding["owner"],),
                        "list": (binding["association"],),
                        "detail": (binding["message"], binding["association"])}[category]
                events.append(helper.event(runtime.CORRELATED_SQL[category], args, line=len(events) + 1))
        for _ in range(probes):
            event = helper.event("-- ping", line=len(events) + 1)
            event["message"] = "statement: -- ping"
            events.append(event)
        barrier = helper.event(runtime.QUERY_LOG_BARRIER, pid=99, session="barrier")
        barrier["message"] = "statement: " + runtime.QUERY_LOG_BARRIER
        events.append(barrier)
        return binding, events

    def proof(self, count=1, probes=1):
        records = [observation_fixture.numeric(observation_fixture.request(i, probes=int(i <= probes)))
                   for i in range(1, count + 1)]
        budgets = {str(i): runtime.assert_message_request_budget(row) for i, row in enumerate(records)}
        return {"per_request": budgets, "business": 3 * count,
                "raw_expected": 3 * count + probes, "reset_probes": probes}

    def reconcile(self, events, binding, proof, receipt=None):
        return runtime.reconcile_message_postgres(
            "".join(json.dumps(row) + "\n" for row in events), binding, proof,
            barrier=runtime.QUERY_LOG_BARRIER, receipt=receipt,
        )

    def test_exact_scoped_multiplicities_and_independent_raw_totals(self):
        for count in (1, 8):
            for route in ("list", "detail"):
                binding, events = self.sample(count=count, probes=count, route=route)
                result = self.reconcile(events, binding, self.proof(count, count))
                self.assertTrue(result["split_accounting_verified"])
                self.assertEqual(result["query"]["total"], 4 * count)
                self.assertEqual(result["business_shapes"]["account"], count)
                encoded = json.dumps(result)
                for value in binding.values():
                    if value != route:
                        self.assertNotIn(value, encoded)
                self.assertNotIn("SELECT", encoded)

    def test_raw_probe_mismatch_extra_account_and_all_unknown_controls_fail_with_counts(self):
        binding, original = self.sample()
        cases = {}
        for mutation in ("unknown", "write", "probe-args", "probe-protocol", "malformed-args",
                         "foreign-owner", "foreign-association", "extra-account-fewer-probes", "fourth-probe"):
            events = copy.deepcopy(original)
            if mutation == "unknown":
                events[0]["message"] = "statement: SELECT 'private-sentinel'"
            elif mutation == "write":
                events[0]["message"] = "statement: DELETE FROM bakney_user"
            elif mutation == "probe-args":
                events[-2]["detail"] = "parameters: $1 = 'private-sentinel'"
            elif mutation == "probe-protocol":
                events[-2]["message"] = "execute fixture: -- ping"
            elif mutation == "malformed-args":
                events[0]["detail"] += ", $2 = 'private-sentinel'"
            elif mutation in ("foreign-owner", "foreign-association"):
                index = 0 if mutation == "foreign-owner" else 2
                events[index]["detail"] = "parameters: $1 = '%s'" % adapter._uid(999999)
            elif mutation == "extra-account-fewer-probes":
                events[-2] = copy.deepcopy(events[0])
                events[-2]["line_num"] = 4
            else:
                for line in range(5, 8):
                    event = copy.deepcopy(events[-2])
                    event["line_num"] = line
                    events.insert(-1, event)
            cases[mutation] = events
        for mutation, events in cases.items():
            receipt = {}
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError) as caught:
                self.reconcile(events, binding, self.proof(), receipt)
            self.assertIn("query", receipt)
            self.assertIn("postgres_statement_counts", receipt)
            self.assertNotIn("private-sentinel", str(caught.exception) + json.dumps(receipt))
        for probes in (0, 2):
            with self.subTest(proof_probes=probes), self.assertRaises(AssertionError):
                self.reconcile(original, binding, self.proof(probes=probes))

    def test_distinct_parser_accepts_only_probes_matching_actual_driver_aggregate(self):
        helper = StructuredPostgresControls()
        original = helper.events()
        probe = helper.event("-- ping", pid=400, session="probe")
        probe["message"] = "statement: -- ping"
        events = [*original[:-1], probe, original[-1]]
        content = "".join(json.dumps(row) + "\n" for row in events)
        result = runtime.parse_postgres_requests(content, helper.bindings(),
                    barrier=runtime.QUERY_LOG_BARRIER, driver_proof=self.proof(8, 1))
        self.assertEqual(result["raw_total"], 25)
        self.assertEqual(result["aggregate"]["total"], 24)
        for probes in (0, 2):
            with self.assertRaises(AssertionError):
                runtime.parse_postgres_requests(content, helper.bindings(),
                    barrier=runtime.QUERY_LOG_BARRIER, driver_proof=self.proof(8, probes))
        for mutation in ("protocol", "parameters", "extra-account", "redistribute"):
            changed = copy.deepcopy(events)
            if mutation == "protocol":
                changed[-2]["message"] = "execute fixture: -- ping"
            elif mutation == "parameters":
                changed[-2]["detail"] = "parameters: $1 = 'private-sentinel'"
            elif mutation == "extra-account":
                changed.insert(-1, copy.deepcopy(changed[0]))
            else:
                changed[1]["detail"] = changed[0]["detail"]
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                runtime.parse_postgres_requests("".join(json.dumps(row) + "\n" for row in changed),
                    helper.bindings(), barrier=runtime.QUERY_LOG_BARRIER, driver_proof=self.proof(8, 1))

    def test_sequential_phase_order_and_failed_proof_keep_raw_diagnostics(self):
        binding, events = self.sample()
        events[0]["message"], events[1]["message"] = events[1]["message"], events[0]["message"]
        receipt = {}
        with self.assertRaisesRegex(AssertionError, "reordered sequential"):
            self.reconcile(events, binding, self.proof(), receipt)
        binding, events = self.sample()
        receipt = {}
        with self.assertRaisesRegex(AssertionError, "simulated summary failure"):
            self.reconcile(events, binding, mock.Mock(side_effect=AssertionError("simulated summary failure")), receipt)
        self.assertEqual(receipt["query"]["total"], 4)
        self.assertEqual(receipt["postgres_statement_counts"]["business"], 3)


class LockCoordinatorControls(unittest.TestCase):
    """Driver/executor fakes exercise cleanup and ordering, never live overlap."""

    def run_control(self, mutation=None):
        order = []
        control = mock.MagicMock(closed=False)
        cursor = control.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = lambda sql: order.append(sql)
        cursor.fetchone.return_value = (555,)
        count = 7 if mutation == "seven" else 8
        rows = [(600 + i, runtime.CORRELATED_SQL["list"], [555]) for i in range(count)]
        if mutation == "wrong-controller":
            rows[0] = (600, runtime.CORRELATED_SQL["list"], [556])
        elif mutation == "wrong-select":
            rows[0] = (600, "SELECT * FROM communications_message", [555])
        elif mutation == "duplicate":
            rows[-1] = rows[0]
        elif mutation == "monitor":
            cursor.fetchall.side_effect = RuntimeError("SELECT 'private-sentinel'")
        cursor.fetchall.return_value = rows
        control.commit.side_effect = lambda: order.append("commit")
        control.rollback.side_effect = (RuntimeError("private-sentinel") if mutation == "cleanup" else
                                        lambda: order.append("rollback"))

        def close():
            order.append("close")
            control.closed = True
        control.close.side_effect = close
        postgres = mock.Mock(work=Path("/private/tmp/offline-only"), private_log=io.StringIO())
        postgres.mark.side_effect = lambda: order.append("PG-mark") or 10
        postgres.since_with_diagnostics.return_value = {"query": {"total": 24},
            "statement_counts": {"total": 24, "business": 24, "readiness_transport_probe": 0, "unknown": 0}}
        observations = mock.Mock()
        observations.mark.side_effect = lambda: order.append("observation-mark") or frozenset()
        executor = mock.Mock()
        futures = []
        for _ in range(8):
            future = mock.Mock()
            future.done.side_effect = lambda: "join" in order or (mutation == "early-finish")
            future.result.side_effect = lambda timeout: order.append("result") or (200, [], {})
            futures.append(future)
        executor.submit.side_effect = futures
        executor.shutdown.side_effect = lambda **_: order.append("join")
        receipt = {}
        with mock.patch.dict(sys.modules, {"psycopg2": SimpleNamespace(connect=mock.Mock(return_value=control))}), mock.patch.object(
            runtime, "ThreadPoolExecutor", return_value=executor
        ):
            try:
                result = runtime.run_locked_same_owner_batch(postgres, observations, object(),
                    [f"same-{i}" for i in range(8)], receipt=receipt, timeout=0)
            except AssertionError as error:
                self.assertNotIn("private-sentinel", str(error))
                self.assertIn("rollback", order if mutation != "cleanup" else ["rollback"])
                self.assertIn("close", order)
                self.assertIn("join", order)
                self.assertLess(order.index("close"), order.index("join"))
                self.assertIn("query", receipt)
                raise
        self.assertLess(order.index("SET SESSION log_statement = 'none'"), order.index("PG-mark"))
        self.assertLess(order.index("LOCK TABLE communications_message IN ACCESS EXCLUSIVE MODE"), order.index("PG-mark"))
        self.assertLess(order.index("commit"), order.index("result"))
        self.assertTrue(receipt["overlap_lock"]["workers_joined"])
        self.assertTrue(receipt["overlap_lock"]["control_closed"])
        self.assertEqual(executor.submit.call_count, 8)
        return result, receipt

    def test_exact_controller_select_and_all_eight_before_release(self):
        _, receipt = self.run_control()
        self.assertEqual(receipt["overlap_lock"]["blocked_backends"], 8)
        self.assertTrue(receipt["overlap_lock"]["all_eight_active_before_finish"])

    def test_timeout_wrong_controller_select_early_finish_and_cleanup_fail_closed(self):
        for mutation in ("seven", "wrong-controller", "wrong-select", "duplicate", "early-finish", "monitor", "cleanup"):
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                self.run_control(mutation)


class HTTPObservationControls(unittest.TestCase):
    """Pure offline verifier controls; these do not establish HTTP overlap."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "private.jsonl"
        self.path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))

    def responses(self, count):
        return [(200, [], {"X-Query-Observation-ID": f"{observation_fixture.RUN}-{i}"})
                for i in range(1, count + 1)]

    def complete(self, count=8, **kwargs):
        rows = [observation_fixture.request(i, **kwargs) for i in range(1, count + 1)]
        self.path.write_bytes(observation_fixture.encode(observation_fixture.stream(rows)))

    def test_eight_independent_receipts_and_final_hash(self):
        session = runtime.MessageQueryObservations(self.path)
        mark = session.mark()
        self.complete()
        proof = session.responses(mark, self.responses(8), overlap=True)
        self.assertEqual(len(proof["per_request"]), 8)
        self.assertEqual(proof["raw_expected"], 32)
        self.assertEqual(proof["reset_probes"], 8)
        self.assertEqual(proof["active_requests_at_start_max"], 8)
        receipt = session.finalize(process_exit=0)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["sha256"], runtime.sha256_file(self.path))
        self.assertNotIn("SELECT", json.dumps(receipt))

    def test_missing_duplicate_forged_and_extra_response_ids(self):
        for mutation in ("missing", "duplicate", "forged", "forged-valid", "wrong-run", "extra"):
            with self.subTest(mutation=mutation):
                session = runtime.MessageQueryObservations(self.path)
                mark = session.mark()
                self.complete()
                responses = self.responses(8)
                if mutation == "missing":
                    responses[0][2].clear()
                elif mutation == "duplicate":
                    responses[0][2].update(responses[1][2])
                elif mutation == "forged":
                    responses[0][2]["X-Query-Observation-ID"] = "client-label"
                elif mutation == "forged-valid":
                    responses[0][2]["X-Query-Observation-ID"] = observation_fixture.RUN + "-900"
                elif mutation == "wrong-run":
                    responses[0][2]["X-Query-Observation-ID"] = observation_fixture.OTHER_RUN + "-1"
                else:
                    responses.pop()
                with self.assertRaises(AssertionError):
                    session.responses(mark, responses, overlap=True, timeout=0)
                self.path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))

    def test_duplicate_wire_header_is_rejected(self):
        from email.message import Message

        session = runtime.MessageQueryObservations(self.path)
        mark = session.mark()
        self.complete(1)
        headers = Message()
        headers["X-Query-Observation-ID"] = observation_fixture.RUN + "-1"
        headers["X-Query-Observation-ID"] = observation_fixture.RUN + "-1"
        with self.assertRaises(AssertionError):
            session.responses(mark, [(200, [], headers)])

    def test_portable_wrapper_preserves_responses_and_binds_rejection_zero_query_record(self):
        from TESTS.contracts import test_message_reads as portable

        session = runtime.MessageQueryObservations(self.path)
        postgres = runtime.PostgresQueryLog.__new__(runtime.PostgresQueryLog)
        postgres.observations = session
        helper = StructuredPostgresControls()
        barrier = helper.event(runtime.QUERY_LOG_BARRIER, pid=99, session="boundary")
        barrier["message"] = "statement: " + runtime.QUERY_LOG_BARRIER
        postgres._through_barrier = mock.Mock(return_value=(json.dumps(barrier) + "\n", runtime.QUERY_LOG_BARRIER))
        body = {"detail": "Authentication credentials were not provided."}

        class OfflineClient:
            def request(inner, *args, **kwargs):
                self.assertTrue(kwargs["include_headers"])
                rows = observation_fixture.stream([
                    observation_fixture.request(1, queries=0, connects=0, resets=0, probes=0)])
                self.path.write_bytes(observation_fixture.encode(rows))
                return 401, body, {"X-Query-Observation-ID": observation_fixture.RUN + "-1"}

        def matrix(**kwargs):
            response = portable.Client().request("GET", "/communications/messages/list")
            self.assertEqual(response, (401, body))
            self.assertEqual(postgres.since(object())["total"], 0)
            return {"observed_requests": 1}

        with mock.patch.object(portable, "Client", OfflineClient), mock.patch.object(
            adapter, "run_portable_workload", side_effect=matrix
        ):
            result = runtime.run_portable_workload(backend="go", postgres_queries=postgres)
        proof = result["query_observation_headers"][0]
        self.assertEqual(proof["business"], 0)
        self.assertEqual(proof["raw_expected"], 0)
        self.assertIsNone(postgres.portable_observation)

    def test_portable_raw_totals_and_probe_count_must_match_driver_record(self):
        postgres = runtime.PostgresQueryLog.__new__(runtime.PostgresQueryLog)
        helper = StructuredPostgresControls()
        barrier = helper.event(runtime.QUERY_LOG_BARRIER, pid=99, session="boundary")
        barrier["message"] = "statement: " + runtime.QUERY_LOG_BARRIER
        postgres._through_barrier = mock.Mock(return_value=(json.dumps(barrier) + "\n", runtime.QUERY_LOG_BARRIER))
        for proof in ({"raw_expected": 1, "reset_probes": 0}, {"raw_expected": 0, "reset_probes": 1}):
            postgres.portable_observation = proof
            with self.assertRaises(AssertionError):
                postgres.since(object())

    def test_wrong_run_replacement_and_same_inode_rewrite(self):
        for mutation in ("run", "file", "rewrite"):
            with self.subTest(mutation=mutation):
                self.path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))
                session = runtime.MessageQueryObservations(self.path)
                session.mark()
                if mutation == "file":
                    other = self.path.with_suffix(".new")
                    other.write_bytes(self.path.read_bytes())
                    other.replace(self.path)
                elif mutation == "run":
                    self.path.write_bytes(self.path.read_bytes().replace(
                        observation_fixture.RUN.encode(), observation_fixture.OTHER_RUN.encode()))
                else:
                    self.path.write_bytes(self.path.read_bytes().replace(b'"version":1', b'"version":2'))
                with self.assertRaises(AssertionError):
                    session.mark()

    def test_individual_four_two_redistribution_and_driver_failures(self):
        for mutation in ("redistribution", "fourth-probe", "retry", "connect", "reset", "ping"):
            with self.subTest(mutation=mutation):
                self.path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))
                session = runtime.MessageQueryObservations(self.path)
                mark = session.mark()
                rows = [observation_fixture.request(i) for i in range(1, 9)]
                if mutation == "redistribution":
                    rows[0]["query_starts"] = rows[0]["query_ends"] = 4
                    rows[1]["query_starts"] = rows[1]["query_ends"] = 2
                elif mutation == "fourth-probe":
                    rows[0] = observation_fixture.request(1, connects=0, resets=4, probes=4)
                elif mutation == "retry":
                    rows[0] = observation_fixture.request(1, queries=4)
                elif mutation == "ping":
                    rows[0] = observation_fixture.request(1, pings=1)
                else:
                    rows[0][mutation + "_errors"] = 1
                    rows[0]["driver_errors"] = 1
                self.path.write_bytes(observation_fixture.encode(observation_fixture.stream(rows)))
                with self.assertRaises(AssertionError):
                    session.responses(mark, self.responses(8), overlap=True)

    def test_only_seven_overlap_fails(self):
        session = runtime.MessageQueryObservations(self.path)
        mark = session.mark()
        rows = [observation_fixture.request(i) for i in range(1, 9)]
        rows[-1]["active_requests_at_start"] = 7
        self.path.write_bytes(observation_fixture.encode(observation_fixture.stream(rows)))
        with self.assertRaises(AssertionError):
            session.responses(mark, self.responses(8), overlap=True)

    def test_response_arrival_can_precede_summary_but_timeout_is_bounded(self):
        session = runtime.MessageQueryObservations(self.path)
        mark = session.mark()
        with mock.patch.object(runtime.time, "sleep", side_effect=lambda _: self.complete(1)):
            proof = session.responses(mark, self.responses(1))
        self.assertEqual(proof["raw_expected"], 4)
        self.path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))
        session = runtime.MessageQueryObservations(self.path)
        with self.assertRaises(AssertionError):
            session.responses(session.mark(), self.responses(1), timeout=0)

    def test_no_final_receipt_without_footer_exit_zero_and_unchanged_file(self):
        for mutation in ("missing", "invalid", "exit", "replacement"):
            with self.subTest(mutation=mutation):
                self.path.write_bytes(observation_fixture.encode([observation_fixture.record("header")]))
                session = runtime.MessageQueryObservations(self.path)
                self.complete(1)
                if mutation == "missing":
                    self.path.write_bytes(observation_fixture.encode(observation_fixture.stream()[:-1]))
                elif mutation == "invalid":
                    rows = observation_fixture.stream()
                    rows[-1]["active_queries"] = 1
                    self.path.write_bytes(observation_fixture.encode(rows))
                elif mutation == "replacement":
                    other = self.path.with_suffix(".new")
                    other.write_bytes(self.path.read_bytes())
                    other.replace(self.path)
                with self.assertRaises(AssertionError):
                    session.finalize(process_exit=7 if mutation == "exit" else 0)


class SafetyAndCleanupControls(unittest.TestCase):
    def test_workload_and_service_stop_failure_still_remove_sensitive_files(self):
        from TESTS.adapters.django import historical_read_routes as helper

        @contextmanager
        def failed_service(output, report):
            del output
            try:
                yield work, 16379, io.StringIO()
            finally:
                report["service_cleanup"]["postgres"] = "cleanup_failed"
                raise RuntimeError("simulated PostgreSQL stop failure")

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            private = work / "message-reads-private"
            private.mkdir()
            manifest = private / "message_reads-runtime.json"
            manifest.write_text('{"token": "private-token"}')
            sql_log = work / "postgres.log"
            sql_log.write_text("private SQL parameters")
            structured = work / "message-query-pglog"
            structured.mkdir()
            (structured / "events.json").write_text('{"detail":"private SQL parameters"}')
            live_data = work / "postgres"
            live_data.mkdir()
            (live_data / "PG_VERSION").write_text("17")
            report = runtime._service_report()
            with mock.patch.object(helper, "owned_database", side_effect=failed_service):
                with self.assertRaisesRegex(RuntimeError, "stop failure"):
                    with runtime.owned_message_database(work, report):
                        raise ValueError("simulated workload failure")
            self.assertFalse(manifest.exists())
            self.assertFalse(sql_log.exists())
            self.assertFalse(structured.exists())
            self.assertTrue((live_data / "PG_VERSION").exists())
            self.assertTrue(report["sensitive_cleanup"]["verified"])
            self.assertFalse(runtime.cleanup_complete(report))

    def test_inherited_credentials_are_excluded_from_tool_environment(self):
        inherited = {
            "PATH": "/tools",
            "HOME": "/private-home",
            "DBPASSWORD": "must-not-propagate",
            "JWT_SECRET_KEY": "must-not-propagate",
            "TESTS_MESSAGE_READS_GO": "1",
            "TESTS_DATABASE": "developer_database",
            "GOCACHE": "/private-cache",
        }
        result = runtime.sanitized_tool_environment(inherited)
        self.assertEqual(result, {
            "PATH": "/tools",
            "HOME": "/private-home",
            "GOCACHE": "/private-cache",
        })

    def test_tool_environment_preserves_explicit_build_resource_limits(self):
        self.assertEqual(runtime.sanitized_tool_environment({
            "GOMAXPROCS": "2", "GOFLAGS": "-p=1", "DBPASSWORD": "private",
        }), {"GOMAXPROCS": "2", "GOFLAGS": "-p=1"})

    def test_applying_private_environment_removes_every_inherited_value(self):
        with mock.patch.dict(os.environ, {
            "DBPASSWORD": "inherited-secret",
            "JWT_SECRET_KEY": "inherited-key",
            "TESTS_MESSAGE_READS_GO": "1",
            "PATH": "/tools",
        }, clear=True):
            runtime.apply_private_environment({"PATH": "/private-tools"})
            self.assertEqual(dict(os.environ), {"PATH": "/private-tools"})

    def test_private_environment_clears_inherited_configuration(self):
        with mock.patch.object(
            runtime,
            "read_tracked_environment",
            return_value={
                "DBPASSWORD": "replace-me",
                "JWT_SECRET_KEY": "replace-me",
                "TESTS_DATABASE": "developer_database",
            },
        ), mock.patch.object(
            runtime,
            "signing_key_pair",
            return_value=("private-control-key", "public-control-key"),
        ), mock.patch.dict(os.environ, {
            "DBPASSWORD": "inherited-secret",
            "JWT_SECRET_KEY": "inherited-key",
            "TESTS_DATABASE": "developer_database",
            "TESTS_MESSAGE_READS_GO": "1",
        }, clear=False):
            environment, secrets = runtime.private_environment(
                Path("/tmp/message-reads-control"),
                6379,
                {"PATH": "/fake/tools", "LANG": "C"},
            )
        self.assertEqual(environment["DBPASSWORD"], "synthetic-db-only")
        self.assertNotEqual(environment["JWT_SECRET_KEY"], "inherited-key")
        self.assertEqual(environment["TESTS_DATABASE"], runtime.DATABASE)
        self.assertNotIn("TESTS_MESSAGE_READS_GO", environment)
        self.assertNotIn("inherited-secret", secrets)
        self.assertNotIn("inherited-key", secrets)
        self.assertTrue(secrets)

    def test_redaction_removes_exact_tokens_keys_and_private_fixture_values(self):
        private = (
            "eyJhbGciOiJIUzI1NiJ9.private-token",
            "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
            "synthetic-db-only",
        )
        evidence = {
            "nested": [{"value": value, "safe": "public"} for value in private],
            "summary": "clean",
        }
        redacted = adapter.redact_private(evidence, private)
        adapter.assert_no_private_values(redacted, private)
        encoded = json.dumps(redacted)
        for value in private:
            self.assertNotIn(value, encoded)

    def test_cleanup_failure_is_visible_and_does_not_stop_later_attempts(self):
        receipt = {"results": {}, "errors": []}
        calls = []

        def remove_fixture():
            calls.append("fixture")
            raise RuntimeError("fixture still present")

        def remove_cache():
            calls.append("cache")
            return True

        self.assertFalse(adapter.cleanup_attempt(receipt, "fixture", remove_fixture))
        self.assertTrue(adapter.cleanup_attempt(receipt, "cache", remove_cache))
        self.assertEqual(calls, ["fixture", "cache"])
        self.assertFalse(receipt["results"]["fixture"])
        self.assertTrue(receipt["results"]["cache"])
        self.assertIn("fixture still present", receipt["errors"][0])

    def test_service_cleanup_requires_every_owned_service_and_directory(self):
        report = {
            "service_cleanup": {
                name: "not_started"
                for name in runtime.SERVICES
            },
            "owned_directory_removed": True,
        }
        self.assertTrue(runtime.cleanup_complete(report))
        report["service_cleanup"]["go"] = "running"
        self.assertFalse(runtime.cleanup_complete(report))
        report["service_cleanup"]["go"] = "cleanup_failed"
        self.assertFalse(runtime.cleanup_complete(report))
        report["service_cleanup"]["go"] = "stopped"
        report["owned_directory_removed"] = False
        self.assertFalse(runtime.cleanup_complete(report))

    def test_separate_caddy_cleanup_failure_is_fatal_and_visible(self):
        from TESTS.adapters import caddy

        @contextmanager
        def failing_proxy(*args, container_observer):
            container_observer({"container_id": "owned-control"})
            try:
                yield "http://offline.invalid", "caddy:pinned"
            finally:
                raise RuntimeError("simulated container removal failure")

        report = runtime._service_report()
        report["owned_directory_removed"] = True
        with mock.patch.object(caddy, "proxy", side_effect=failing_proxy):
            with self.assertRaisesRegex(RuntimeError, "container removal failure"):
                with runtime.owned_caddy(report, io.StringIO(), "django", "django",
                                         service="caddy-misroute"):
                    raise ValueError("simulated workload failure")
        self.assertEqual(report["service_cleanup"]["caddy-misroute"], "cleanup_failed")
        self.assertFalse(runtime.cleanup_complete(report))


class RuntimeAcceptanceControls(unittest.TestCase):
    """Synthetic decision inputs only; passing these is not runtime acceptance."""

    def candidate(self, versioned=True):
        authored = adapter.load_authored_manifest()
        sequence = 1
        records = [observation_fixture.request(1, queries=0, connects=1, resets=0, pings=1, probes=0)]

        def proof(count=1, overlap=False):
            nonlocal sequence
            rows = {}
            for index in range(count):
                sequence += 1
                record = observation_fixture.request(sequence, probes=0)
                record["active_requests_at_start"] = index + 1 if overlap else 1
                records.append(record)
                rows[record["request_id"]] = runtime.assert_message_request_budget({
                    key: value for key, value in record.items()
                    if key not in {"type", "run_id", "request_id", "complete"}
                })
            return {"per_request": rows, "business": count * 3,
                    "raw_expected": count * 3, "reset_probes": 0,
                    "active_requests_at_start_max": count if overlap else 1}

        def sql_counts(count=1):
            return {"total": count * 3, "business": count * 3,
                    "readiness_transport_probe": 0, "unknown": 0}

        cache_receipt = {"typed_values_exact": True, "absolute_expirations_exact": True,
                         "readiness_prefix_excluded": False, "unexpected_readiness_keys": 0}
        state = {"models": 1, "rows": 1, "sha256": "a" * 64}
        report = runtime._service_report()
        sources = {name: "b" * 64 for name in runtime.SOURCE_PATHS}
        report.update({
            "success": False, "acceptance_gate": False, "parent_review_required": False,
            "complete_message_family_parity": False,
            "runtime_executed": True, "versioned": versioned, "errors": [],
            "workload_order_valid": True, "source_stable": True,
            "source_sha256": sources, "source_sha256_after": sources.copy(),
            "go_build_source_sha256": sources.copy(), "owned_directory_removed": True,
            "cleanup_verified": True, "cache": cache_receipt,
            "fixture": {"runtime_cases": len(authored["cases"]), "baseline_state": state},
            "schema": {"mode": "full-migration-history", "applied_migrations": 1, "remaining_plan": 0},
            "fixture_cleanup": {"errors": [], "results": dict.fromkeys((
                "runtime_manifest_removed", "fixture_rows_removed", "cache_contrasts_removed",
                "managed_state_restored"), True)},
            "sensitive_cleanup": {"verified": True, "errors": [], "results": dict.fromkeys((
                "tokenized_fixture_removed", "private_sql_log_removed", "private_query_observation_removed"), True)},
            "managed_state": state,
            "go_artifact": {"kind": "freshly-built-native-server", "same_fixture": True,
                            "build_source_sha256": sources.copy(), "binary_sha256": {
                                name: {"sha256": "c" * 64, "path": "/private/synthetic/" + name}
                                for name in ("go-server", "image-codec")}},
            "workloads": [],
        })
        for service in ("django", "go", "postgres", "redis") + (("caddy", "caddy-misroute") if versioned else ()):
            report["service_cleanup"][service] = "stopped"
        for label in runtime.expected_workload_labels(versioned):
            backend = "django" if label in ("django-native", "caddy-v1") else "go"
            workload = {"label": label, "backend": backend,
                        "transport": "caddy" if label.startswith("caddy") else "native",
                        "success": True, "complete_case_sequence": True, "failed_tests": [],
                        "assertion_cases": len(authored["cases"]),
                        "observed_requests": len(authored["cases"]), "tests": 1, "requests": []}
            headers = []
            for case in authored["cases"]:
                query = {"total": 3, "writes": 0}
                workload["requests"].append({
                    "case": case["name"], "method": case["method"], "backend": backend,
                    "path": case["path"].split("?", 1)[0], "status": case["status"],
                    "query": query, "state": state, "cache": cache_receipt,
                    "asgi_events": int(backend == "django"), "django_attribution": int(backend == "django"),
                })
                if backend == "go":
                    headers.append({**proof(), "query": query, "split_accounting_verified": True,
                                    "postgres_statement_counts": {"total": 3, "business": 3,
                                                                 "readiness_transport_probe": 0, "unknown": 0}})
            if backend == "go":
                workload["query_observation_headers"] = headers
            report["workloads"].append(workload)
        routes = {backend: {route: [] for route in ("list", "detail")} for backend in ("django", "go")}
        for backend, paths in routes.items():
            for route, samples in paths.items():
                for size in (8, 24, 48):
                    sample = {"size": size, "query": {"total": 3, "writes": 0}, "response_sha256": "d" * 64}
                    if backend == "go":
                        sample.update(observation=proof(), split_accounting_verified=True,
                                      postgres_statement_counts=sql_counts())
                    samples.append(sample)
        concurrency = {}
        for backend in ("django", "go"):
            cohort = {"requests": 8, "per_request_query_budgets_verified": True,
                      "complete_responses_verified": True}
            if backend == "go":
                cohort.update(observation=proof(8, overlap=True), split_accounting_verified=True,
                              query={"total": 24, "writes": 0}, postgres_statement_counts=sql_counts(8),
                              overlap_lock={"blocked_backends": 8, "all_eight_active_before_finish": True,
                                            "released": True, "control_closed": True,
                                            "workers_joined": True, "cleanup_errors": []})
                cohort["per_request_queries"] = cohort["observation"]["per_request"]
            else:
                cohort["per_request_queries"] = {str(i): {"total": 3} for i in range(8)}
            concurrency[backend] = cohort
        distinct_proof = proof(8)
        distinct = {"requests": 8, "complete_responses_verified": True,
                    "per_request_query_budgets_verified": True, "state_unchanged": True,
                    "cache_unchanged": True, "driver_observation": distinct_proof,
                    "split_accounting_verified": True, "query": {"total": 24, "writes": 0},
                    "raw_total": 24, "reset_probes": 0,
                    "postgres_statement_counts": sql_counts(8),
                    "per_request_queries": {row["cid"]: {"total": 3} for row in runtime.distinct_owner_specifications()},
                    "response_observation_ids": dict(zip(
                        (row["cid"] for row in runtime.distinct_owner_specifications()), distinct_proof["per_request"], strict=True))}
        report["growth"] = {"success": True, "bounded_checks_passed": True, "blockers": [],
                            "sizes": [8, 24, 48], "routes": routes, "concurrency": concurrency,
                            "distinct_owner_concurrency": distinct}
        footer = observation_fixture.stream(records)[-1]
        report["query_observation"] = {"verified": True, "footer": footer, "process_exit": 0,
                                       "run_id": observation_fixture.RUN, "requests": sequence,
                                       "initial_request_ids": [records[0]["request_id"]],
                                       "request_ids": [row["request_id"] for row in records],
                                       "sha256": "e" * 64}
        if versioned:
            container = {"container_id": "f" * 64, "image_id": "sha256:" + "a" * 64}
            report["versioned_runtime"] = {"routes": {"/api/v1": "django", "/api/v2": "go"},
                                           "same_fixture": True, "container": container, "caddy_image": "caddy:synthetic"}
            report["misroute_control"] = {"success": True, "rejected_by_attribution": True,
                                         "transport": "caddy", "public_prefix": "/api/v2",
                                         "actual_django_requests": 1, "container": container, "status": 200}
        return report, authored["cases"]

    def test_only_complete_finalized_read_scope_can_pass(self):
        for versioned in (False, True):
            report, cases = self.candidate(versioned)
            original = copy.deepcopy(report)
            self.assertTrue(runtime.runtime_acceptance(report, cases))
            self.assertEqual(report, original)  # A pure decision, never a receipt relabeler.
            self.assertFalse(report["complete_message_family_parity"])
            self.assertFalse(report["acceptance_gate"])

    def test_missing_footer_nonzero_exit_and_unfinalized_observer_fail(self):
        for key, value in (("footer", None), ("process_exit", 7), ("process_exit", False),
                           ("verified", False), ("sha256", ""), ("request_ids", []),
                           ("initial_request_ids", [])):
            report, cases = self.candidate()
            report["query_observation"][key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(runtime.runtime_acceptance(report, cases))

    def test_failed_missing_reordered_or_unproved_portable_case_fails(self):
        for mutation in ("failed", "missing", "order", "sql", "query", "duplicate-id", "status", "skipped"):
            report, cases = self.candidate()
            workload = report["workloads"][1]
            if mutation == "failed": workload["success"] = False
            if mutation == "missing": workload["requests"].pop()
            if mutation == "order": workload["requests"].reverse()
            if mutation == "sql": workload["query_observation_headers"][0]["split_accounting_verified"] = False
            if mutation == "query": workload["requests"][0]["query"] = None
            if mutation == "duplicate-id": workload["query_observation_headers"][1] = workload["query_observation_headers"][0]
            if mutation == "status": workload["requests"][0]["status"] = 500
            if mutation == "skipped": workload["skipped"] = ["portable"]
            with self.subTest(mutation=mutation):
                self.assertFalse(runtime.runtime_acceptance(report, cases))

    def test_partial_growth_and_seven_request_overlap_fail(self):
        for mutation in ("sample", "size", "growth", "growth-sql", "overlap7", "lock7", "lock-cleanup",
                         "distinct", "distinct-proof", "distinct-sql"):
            report, cases = self.candidate()
            growth = report["growth"]
            if mutation == "sample": growth["routes"]["go"]["list"].pop()
            if mutation == "size": growth["sizes"] = [8, 24]
            if mutation == "growth": growth["success"] = False
            if mutation == "growth-sql": growth["routes"]["go"]["detail"][0].pop("split_accounting_verified")
            if mutation == "overlap7": growth["concurrency"]["go"]["observation"]["active_requests_at_start_max"] = 7
            if mutation == "lock7": growth["concurrency"]["go"]["overlap_lock"]["blocked_backends"] = 7
            if mutation == "lock-cleanup": growth["concurrency"]["go"]["overlap_lock"]["released"] = False
            if mutation == "distinct": growth.pop("distinct_owner_concurrency")
            if mutation == "distinct-proof": growth["distinct_owner_concurrency"]["driver_observation"] = None
            if mutation == "distinct-sql": growth["distinct_owner_concurrency"].pop("split_accounting_verified")
            with self.subTest(mutation=mutation):
                self.assertFalse(runtime.runtime_acceptance(report, cases))

    def test_unstable_missing_build_or_artifacts_fail(self):
        for mutation in ("flag", "after", "build", "artifact-source", "server", "codec", "sources"):
            report, cases = self.candidate()
            if mutation == "flag": report["source_stable"] = False
            if mutation == "after": report["source_sha256_after"] = {}
            if mutation == "build": report["go_build_source_sha256"] = {}
            if mutation == "artifact-source": report["go_artifact"]["build_source_sha256"] = {}
            if mutation in ("server", "codec"): report["go_artifact"]["binary_sha256"].pop("go-server" if mutation == "server" else "image-codec")
            if mutation == "sources": report["source_sha256"] = {}
            with self.subTest(mutation=mutation):
                self.assertFalse(runtime.runtime_acceptance(report, cases))

    def test_runtime_errors_cache_schema_state_or_cleanup_failure_fails(self):
        for mutation in ("runtime", "errors", "cleanup", "private", "fixture", "model", "cache", "schema", "service", "directory"):
            report, cases = self.candidate()
            if mutation == "runtime": report["runtime_executed"] = False
            if mutation == "errors": report["errors"] = ["runtime failure"]
            if mutation == "cleanup": report["cleanup_verified"] = False
            if mutation == "private": report["sensitive_cleanup"]["results"]["private_query_observation_removed"] = False
            if mutation == "fixture": report["fixture_cleanup"]["results"]["fixture_rows_removed"] = False
            if mutation == "model": report["fixture_cleanup"]["results"]["managed_state_restored"] = False
            if mutation == "cache": report["cache"]["absolute_expirations_exact"] = False
            if mutation == "schema": report["schema"]["remaining_plan"] = 1
            if mutation == "service": report["service_cleanup"]["go"] = "not_started"
            if mutation == "directory": report["owned_directory_removed"] = False
            with self.subTest(mutation=mutation):
                self.assertFalse(runtime.runtime_acceptance(report, cases))

    def test_versioned_requires_real_caddy_and_rejected_misroute(self):
        for mutation in ("missing", "accepted", "caddy", "routes", "order", "matrix"):
            report, cases = self.candidate()
            if mutation == "missing": report.pop("misroute_control")
            if mutation == "accepted": report["misroute_control"]["rejected_by_attribution"] = False
            if mutation == "caddy": report["versioned_runtime"].pop("container")
            if mutation == "routes": report["versioned_runtime"]["routes"]["/api/v2"] = "django"
            if mutation == "order": report["workloads"].reverse()
            if mutation == "matrix": report["workloads"].pop()
            with self.subTest(mutation=mutation):
                self.assertFalse(runtime.runtime_acceptance(report, cases))

    def test_empty_initial_and_subgate_only_receipts_fail(self):
        report, cases = self.candidate()
        for incomplete in ({}, runtime._service_report(), {"growth": report["growth"]},
                           {"distinct_owner_concurrency": report["growth"]["distinct_owner_concurrency"]}):
            self.assertFalse(runtime.runtime_acceptance(incomplete, cases))

    def test_final_id_coverage_requires_every_unique_measured_response(self):
        for mutation in ("extra", "reused-initial", "missing", "duplicate", "wrong-run", "footer-count", "incomplete-footer"):
            report, cases = self.candidate()
            final = report["query_observation"]
            if mutation == "extra":
                final["request_ids"].append(f"{observation_fixture.RUN}-999")
                final["requests"] += 1
                for key in ("request_starts", "request_finishes", "finish_calls", "summaries"):
                    final["footer"][key] += 1
            if mutation == "reused-initial": final["initial_request_ids"].append(final["request_ids"][-1])
            if mutation == "missing": final["request_ids"].pop()
            if mutation == "duplicate": final["request_ids"][-1] = final["request_ids"][0]
            if mutation == "wrong-run": final["request_ids"][-1] = "foreign-1"
            if mutation == "footer-count": final["footer"]["summaries"] -= 1
            if mutation == "incomplete-footer": final["footer"]["complete"] = False
            with self.subTest(mutation=mutation):
                self.assertFalse(runtime.runtime_acceptance(report, cases))

    def test_distinct_business_total_stays_separate_from_attested_raw_probe_total(self):
        for mutation in (None, "unattested", "raw-mismatch", "business-inflation"):
            report, cases = self.candidate()
            distinct = report["growth"]["distinct_owner_concurrency"]
            proof = distinct["driver_observation"]
            distinct["raw_total"] += 1
            distinct["reset_probes"] += 1
            distinct["postgres_statement_counts"]["total"] += 1
            distinct["postgres_statement_counts"]["readiness_transport_probe"] += 1
            if mutation != "unattested":
                row = next(iter(proof["per_request"].values()))
                row["reset_probes"] += 1
                row["raw_expected"] += 1
                proof["reset_probes"] += 1
                proof["raw_expected"] += 1
                report["query_observation"]["footer"]["reset_probes"] += 1
            if mutation == "raw-mismatch": distinct["raw_total"] -= 1
            if mutation == "business-inflation": distinct["query"]["total"] += 1
            with self.subTest(mutation=mutation):
                self.assertEqual(runtime.runtime_acceptance(report, cases), mutation is None)
                self.assertEqual(proof["business"], 24)

    def test_footer_error_active_and_unbalanced_counters_fail_despite_claimed_verified(self):
        mutations = [(key, 1) for key in observation_fixture.ZERO_ONLY]
        mutations += [(key, 999) for key in ("query_ends", "driver_ends", "connect_ends", "reset_ends",
                                            "ping_ends", "driver_starts", "reset_probes")]
        mutations += [("version", 2), ("active_requests_at_start", 1)]
        for key, value in mutations:
            report, cases = self.candidate()
            report["query_observation"]["footer"][key] = value
            with self.subTest(counter=key):
                self.assertFalse(runtime.runtime_acceptance(report, cases))


if __name__ == "__main__":
    unittest.main()
