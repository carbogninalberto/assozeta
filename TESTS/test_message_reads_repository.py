"""Offline failure-path checks; these never initialize Django or real services."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from TESTS.adapters.go import message_reads_repository as runner


class MessageRepositoryRunnerTests(unittest.TestCase):
    def events(self):
        return "\n".join(json.dumps({"Action": "pass", "Test": name})
                         for name in sorted(runner.EXPECTED_TESTS))

    def test_failed_go_exit_cannot_pass_even_with_pass_events(self):
        with self.assertRaises(AssertionError):
            runner.validate_go_events(self.events(), 1)

    def test_optimized_subprocess_rejected_before_shared_imports_preflight_or_build(self):
        # Import interception proves the lifecycle/preflight is never imported;
        # Popen interception makes accidental compiler/service launch fail too.
        script = """
import builtins
import runpy
import subprocess
import sys
original_import = builtins.__import__
def forbidden_process(*args, **kwargs):
    raise SystemExit(91)
def guarded_import(name, *args, **kwargs):
    if name.startswith('TESTS.adapters.django'):
        raise SystemExit(92)
    return original_import(name, *args, **kwargs)
subprocess.Popen = forbidden_process
builtins.__import__ = guarded_import
sys.argv = ['message_reads_repository', '--output', sys.argv[1]]
runpy.run_module('TESTS.adapters.go.message_reads_repository', run_name='__main__')
raise SystemExit(93)
"""
        for flag in ("-O", "-OO"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([sys.executable, "-B", flag, "-c", script, directory],
                                        cwd=runner.ROOT, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), {
                    "success": False, "complete_message_family_parity": False,
                    "errors": [{"stage": "startup", "type": "AcceptanceFailure",
                                "check": "optimized-python"}],
                })
                self.assertEqual(list(Path(directory).iterdir()), [])

    def failure_diagnostic(self, events, *, source_sha256=None):
        if source_sha256 is None:
            source_sha256 = runner.sha256_file(runner.ROOT / runner.GO_SOURCE)
        with self.assertRaises(runner.AcceptanceFailure) as caught:
            runner.validate_go_events("\n".join(json.dumps(e) for e in events), 1,
                                      source_sha256=source_sha256)
        return runner.error_code("acceptance", caught.exception)

    def test_failed_go_locations_are_bound_to_known_test_and_exact_source(self):
        name = "TestMessageRepositoryExecutedPlansLive"
        events = [{"Action": "pass", "Test": other}
                  for other in sorted(runner.EXPECTED_TESTS - {name})]
        events += [{"Action": "output", "Test": name,
                    "Output": "    message_reads_integration_test.go:42: private SQL/args/token/stack",
                    "error": "malicious private value", "file": "private-filename"},
                   {"Action": "output", "Test": name,
                    "Output": "    message_reads_integration_test.go:42: duplicate private diagnostic"},
                   {"Action": "fail", "Test": name, "Output": "private failure"}]
        diagnostic = self.failure_diagnostic(events)
        self.assertEqual(diagnostic, {
            "stage": "acceptance", "type": "AcceptanceFailure", "check": "go-execution",
            "unpassed_tests": [name], "failed_locations": [{
                "test": name, "file": runner.GO_SOURCE,
                "source_sha256": runner.sha256_file(runner.ROOT / runner.GO_SOURCE),
                "lines": [42],
            }],
        })
        self.assertNotIn("private", json.dumps(diagnostic))

    def test_go_locations_reject_fake_filenames_nonpositive_or_oversized_lines(self):
        name = "TestMessageRepositoryExecutedPlansLive"
        line_count = len((runner.ROOT / runner.GO_SOURCE).read_bytes().splitlines())
        outputs = [
            "other.go:42: private", "../message_reads_integration_test.go:42: private",
            "/tmp/message_reads_integration_test.go:42: private",
            "message_reads_integration_test.go.evil:42: private",
            "prefix message_reads_integration_test.go:42: private",
            "\nmessage_reads_integration_test.go:42: private",
            "message_reads_integration_test.go:0: private",
            "message_reads_integration_test.go:-1: private",
            "message_reads_integration_test.go:+1: private",
            "message_reads_integration_test.go:01: private",
            "message_reads_integration_test.go:１: private",
            f"message_reads_integration_test.go:{line_count + 1}: private",
            "message_reads_integration_test.go:" + "9" * 10000 + ": private",
            {"file": "message_reads_integration_test.go", "line": 42},
            ["message_reads_integration_test.go:42: private"], None, 42,
        ]
        for output in outputs:
            with self.subTest(output_type=type(output).__name__):
                diagnostic = self.failure_diagnostic([
                    {"Action": "output", "Test": name, "Output": output},
                    {"Action": "fail", "Test": name},
                ])
                self.assertNotIn("failed_locations", diagnostic)

    def test_go_locations_ignore_unknown_missing_passed_tests_and_nonoutput_events(self):
        name = "TestMessageRepositoryExecutedPlansLive"
        location = "    message_reads_integration_test.go:42: private"
        events = [
            {"Action": "output", "Test": "UnknownPrivateTest", "Output": location},
            {"Action": "fail", "Test": "UnknownPrivateTest", "Output": location},
            {"Action": "output", "Output": location},
            {"Action": "output", "Test": "TestMessageRepositoryHydrationLive", "Output": location},
            {"Action": "pass", "Test": "TestMessageRepositoryHydrationLive"},
            {"Action": "fail", "Test": name, "Output": location},
        ]
        diagnostic = self.failure_diagnostic(events)
        self.assertNotIn("failed_locations", diagnostic)
        self.assertNotIn("PrivateTest", json.dumps(diagnostic))
        for bad_test in ([name], {"test": name}, 42):
            diagnostic = self.failure_diagnostic([
                {"Action": "output", "Test": bad_test, "Output": location}])
            self.assertEqual(diagnostic["check"], "go-events")
            self.assertNotIn("failed_locations", diagnostic)

    def test_go_locations_cap_unique_lines_per_test(self):
        events = []
        for name in sorted(runner.EXPECTED_TESTS):
            events.extend({"Action": "output", "Test": name,
                           "Output": f"    message_reads_integration_test.go:{line}: private"}
                          for line in range(1, 40))
            events.append({"Action": "fail", "Test": name})
        diagnostic = self.failure_diagnostic(events)
        self.assertEqual(len(diagnostic["failed_locations"]), 3)
        for location in diagnostic["failed_locations"]:
            self.assertEqual(location["lines"], list(range(1, runner.MAX_FAILURE_LINES + 1)))

    def test_go_locations_reject_source_hash_drift(self):
        diagnostic = self.failure_diagnostic([
            {"Action": "output", "Test": "TestMessageRepositoryExecutedPlansLive",
             "Output": "    message_reads_integration_test.go:42: private"},
            {"Action": "fail", "Test": "TestMessageRepositoryExecutedPlansLive"},
        ], source_sha256="0" * 64)
        self.assertEqual(diagnostic["check"], "binding")
        self.assertNotIn("failed_locations", diagnostic)

    def test_failure_receipt_names_unpassed_tests_without_copying_output(self):
        name = sorted(runner.EXPECTED_TESTS)[0]
        content = json.dumps({"Action": "fail", "Test": name, "Output": "private SQL and token"})
        with self.assertRaises(runner.AcceptanceFailure) as caught:
            runner.validate_go_events(content, 1)
        diagnostic = runner.error_code("acceptance", caught.exception)
        self.assertEqual(diagnostic["check"], "go-execution")
        self.assertEqual(diagnostic["unpassed_tests"], sorted(runner.EXPECTED_TESTS))
        self.assertNotIn("private SQL", json.dumps(diagnostic))

    def test_missing_skipped_duplicate_or_failed_tests_cannot_pass(self):
        runner.validate_go_events(self.events(), 0)
        for content in ("", self.events() + '\n{"Action":"skip"}',
                        self.events() + '\n{"Action":"fail"}',
                        self.events() + "\n" + self.events()):
            with self.subTest(content=content), self.assertRaises(AssertionError):
                runner.validate_go_events(content, 0)

    def test_missing_dsn_fails_before_any_subprocess(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, "private_process") as process:
            with self.assertRaises(AssertionError):
                runner.run_go(Path(directory), {}, Path(directory) / "private.log")
            process.assert_not_called()

    def test_compiler_failure_is_not_a_successful_empty_run(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, "private_process", return_value=1):
            with self.assertRaises(AssertionError):
                runner.run_go(Path(directory), {runner.DSN_ENV: "private-fixture"},
                              Path(directory) / "private.log")

    def test_source_binding_includes_runner_test_and_tagged_source(self):
        hashes = runner.source_hashes()
        for name in runner.OWNED_SOURCES:
            self.assertIn(name, hashes)
        for name in ("BE_GO/internal/repository/message_reads.go",
                     "BE/communications/models.py",
                     "TESTS/adapters/django/message_reads_runtime.py"):
            self.assertIn(name, hashes)
        changed = dict(hashes)
        changed[runner.OWNED_SOURCES[0]] = "0" * 64
        with self.assertRaises(AssertionError):
            runner.require_binding(hashes, changed)

    def test_artifact_binding_rejects_changed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "repository.test"
            binary.write_bytes(b"compiled first")
            before = runner.sha256_file(binary)
            binary.write_bytes(b"compiled changed")
            with self.assertRaises(AssertionError):
                runner.require_binding(before, runner.sha256_file(binary))

    def test_cleanup_failure_keeps_gate_closed_and_attempts_remaining_steps(self):
        calls = []
        receipt = {"results": {}, "errors": []}
        def fail():
            raise RuntimeError("postgres://private:password@host/db token-value")
        runner.cleanup_step(receipt, "fixture", fail)
        runner.cleanup_step(receipt, "cache", lambda: calls.append("cache"))
        self.assertEqual(calls, ["cache"])
        self.assertFalse(runner.cleanup_verified(receipt))
        self.assertNotIn("password", json.dumps(receipt))
        self.assertNotIn("token-value", json.dumps(receipt))

    def test_diagnostics_are_fixed_codes_and_unknown_payload_is_rejected(self):
        for value in ({"sql": "SELECT private"}, {"token": "eyJprivate"},
                      {"dsn": "postgres://private"}):
            with self.assertRaises(AssertionError):
                runner.validate_receipts({"hydration": value, "plans": {}, "cancellation": {}})
        error = runner.error_code("go", RuntimeError("private SQL and tokens"))
        self.assertEqual(error, {"stage": "go", "type": "RuntimeError"})

    def safe_receipts(self):
        indexes = []
        for number, (relation, column) in enumerate((
            ("application_sportassociation", "user_id"),
            ("communications_message", "sport_association_id"),
            ("communications_message", "message_id"),
            ("communications_messagetransaction", "message_id"),
        )):
            indexes.append({"relation": relation, "columns": [column], "unique": False,
                            "primary": False, "name_sha256": f"{number:064x}",
                            "definition_sha256": "d" * 64})
        stages = []
        for owners in (8, 24, 48):
            plans = []
            for kind in ("association", "list", "association", "detail") * 2:
                expected = 1 if kind == "association" else 128
                plans.append({"kind": kind, "sql_sha256": "a" * 64,
                              "expected_rows": expected, "nodes": [{
                                  "node": "Seq Scan", "relation": "communications_message",
                                  "index_sha256": "", "estimated_rows": expected,
                                  "actual_rows": expected, "loops": 1,
                                  "shared_hit_blocks": 1, "shared_read_blocks": 0,
                              }]})
            stages.append({"owners": owners, "messages": 6 + owners * 128,
                           "transactions": 6 + owners * 128, "plans": plans})
        return {
            "hydration": {"own_messages": 4, "own_transactions": 4, "foreign_messages": 1,
                          "empty_details": 5, "missing_owners": 5, "literal_and_null_fields": True,
                          "resolved_owner_boundary": True},
            "plans": {"indexes": indexes, "stages": stages, "planner_forced": False,
                      "executed_sql_traced": True},
            "cancellation": {"lock_wait_observed": True, "blocker_held_at_return": True,
                             "context_canceled": True, "return_ms": 100,
                             "pool_in_use_before_release": 0, "pool_in_use_after_followup": 0,
                             "followup_rows": 4},
        }

    def test_safe_sequential_scans_are_reported_without_index_use_claim(self):
        receipt = self.safe_receipts()
        self.assertIs(runner.validate_receipts(receipt), receipt)

    def test_private_plan_fields_and_nonfinite_counts_are_rejected(self):
        safe = self.safe_receipts()
        for key, value in (("Filter", "private SQL argument"), ("token", "private-token"),
                           ("node", "private exception"), ("actual_rows", float("nan")),
                           ("index_sha256", "private-index-name")):
            receipt = copy.deepcopy(safe)
            receipt["plans"]["stages"][0]["plans"][0]["nodes"][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                runner.validate_receipts(receipt)

    def test_incomplete_or_unobserved_cancellation_cannot_pass(self):
        for key, value in (("blocker_held_at_return", False), ("lock_wait_observed", False),
                           ("pool_in_use_before_release", 1), ("return_ms", 2001)):
            receipt = self.safe_receipts()
            receipt["cancellation"][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                runner.validate_receipts(receipt)

    def test_preflight_failure_replaces_prior_success_and_never_starts_services(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, "execute_owned") as services, \
                patch("TESTS.adapters.django.production_runtime.require_production_python",
                      side_effect=RuntimeError("private diagnostic")), \
                redirect_stdout(io.StringIO()):
            path = Path(directory) / "results.json"
            path.write_text('{"success":true}')
            self.assertEqual(runner.main(["--output", directory]), 1)
            receipt = json.loads(path.read_text())
            self.assertFalse(receipt["success"])
            self.assertFalse(receipt["complete_message_family_parity"])
            self.assertNotIn("private diagnostic", path.read_text())
            services.assert_not_called()

    def test_unsafe_output_directory_names_are_not_printed(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, "execute_owned") as services, \
                redirect_stdout(io.StringIO()) as output:
            (Path(directory) / "private-token-file").write_text("private")
            self.assertEqual(runner.main(["--output", directory]), 1)
            self.assertNotIn("private-token-file", output.getvalue())
            self.assertFalse(json.loads(output.getvalue())["success"])
            services.assert_not_called()


if __name__ == "__main__":
    unittest.main()
