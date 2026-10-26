"""Portable message list/detail contract; no Django, application, or Go imports."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from TESTS.http_client import Client


MATRIX_VERSION = 1
SOURCE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/message_reads.json"
REQUIRED_NAMES = {
    "message-reads-list-owner",
    "message-reads-list-query-and-group-ignored",
    "message-reads-detail-owner",
    "message-reads-detail-foreign",
    "message-reads-detail-empty",
    "message-reads-detail-malformed-uuid",
    "message-reads-detail-uppercase-uuid",
    "message-reads-detail-format-before-uuid",
    "message-reads-list-locale-italian-athlete",
    "message-reads-list-full-collaborator",
    "message-reads-list-restricted-collaborator",
    "message-reads-list-athlete",
    "message-reads-list-anonymous",
    "message-reads-list-anonymous-forged-scope",
    "message-reads-list-owner-without-association",
    "message-reads-list-deleted-association",
    "message-reads-list-options",
    "message-reads-detail-options",
    "message-reads-list-post-method",
    "message-reads-list-head-method",
    "message-reads-detail-unsupported-type",
    "message-reads-format-before-forged-identity",
    "message-reads-list-athlete-options",
    "message-reads-list-athlete-post",
    "message-reads-list-athlete-head",
    "message-reads-detail-athlete-options",
    "message-reads-detail-athlete-post",
    "message-reads-detail-athlete-head",
    "message-reads-format-before-auth",
    "message-reads-accept-before-auth",
}
TOKENLESS_NAMES = {
    "message-reads-list-anonymous",
    "message-reads-list-anonymous-forged-scope",
    "message-reads-format-before-auth",
    "message-reads-accept-before-auth",
    "message-reads-format-before-forged-identity",
}


def assert_json_equal(test, actual, expected, label):
    if isinstance(expected, bool):
        test.assertIs(type(actual), bool, label)
    test.assertEqual(actual, expected, label)


def assert_case(test, base_url, case):
    headers = {"Accept-Language": "en", "X-Correlation-ID": case["name"]}
    headers.update(case.get("headers", {}))
    client = Client(base_url, case.get("token"))
    status, content, received = client.request(
        case["method"],
        case["path"],
        case.get("body"),
        headers,
        include_headers=True,
    )
    test.assertEqual(status, case["status"], case["name"])
    if "response" in case:
        if case["response"] == "<stable-server-error-page>":
            test.assertIn("Server Error (500)", content, case["name"])
        else:
            assert_json_equal(test, content, case["response"], case["name"])
    if "response_contains" in case:
        test.assertIn(case["response_contains"], content, case["name"])
    if status < 500:
        test.assertEqual(
            received.get("Content-Language"),
            case.get("language", headers["Accept-Language"]),
            case["name"],
        )
        test.assertEqual(
            received.get("WWW-Authenticate"),
            'Bearer realm="api"' if status == 401 else None,
            case["name"],
        )
        test.assertEqual(
            set(received.get("Allow", "").replace(" ", "").split(",")),
            {"GET", "OPTIONS"},
            case["name"],
        )
        test.assertEqual(
            received.get("Content-Type", "").split(";", 1)[0],
            "application/json",
            case["name"],
        )
    test.assertIsNone(received.get("Set-Cookie"), case["name"])
    return status, content


def load_manifest(test=None, path=None, *, require_runtime=False):
    test = test or unittest.TestCase()
    if require_runtime:
        configured = path or os.environ.get("TESTS_FIXTURE")
        test.assertTrue(configured, "TESTS_FIXTURE must identify a tokenized runtime fixture")
        fixture_path = Path(configured)
    else:
        fixture_path = Path(path or os.environ.get("TESTS_FIXTURE", SOURCE_FIXTURE))
    fixture = json.loads(fixture_path.read_text())
    test.assertEqual(fixture["matrix_version"], MATRIX_VERSION)
    names = [case["name"] for case in fixture["cases"]]
    test.assertEqual(len(names), len(set(names)))
    test.assertEqual(REQUIRED_NAMES, set(names), "Version-one matrix cases differ")
    test.assertTrue(all(
        "response" in case or case["status"] == 405 or case["method"] == "HEAD"
        for case in fixture["cases"]
    ))
    if require_runtime:
        for case in fixture["cases"]:
            test.assertNotIn("actor", case, f"{case['name']} retained a source actor label")
            if case["name"] in TOKENLESS_NAMES:
                test.assertIsNone(case.get("token"), f"{case['name']} unexpectedly has a token")
            else:
                test.assertIsInstance(case.get("token"), str, f"{case['name']} requires a runtime token")
                test.assertTrue(case["token"], f"{case['name']} requires a non-empty runtime token")
    return fixture


class MessageReadsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_manifest(require_runtime=True)
        cls.base_url = os.environ["TESTS_BASE_URL"]

    def test_declared_cases(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["name"]):
                assert_case(self, self.base_url, case)


def tokenized_control_manifest(source):
    runtime = copy.deepcopy(source)
    for index, case in enumerate(runtime["cases"], 1):
        case.pop("actor", None)
        if case["name"] not in TOKENLESS_NAMES:
            case["token"] = f"control-token-{index}"
    return runtime


class MessageReadsManifestControls(unittest.TestCase):
    def test_source_manifest_is_authored_and_has_four_owned_list_rows(self):
        fixture = load_manifest()
        owner = next(case for case in fixture["cases"] if case["name"] == "message-reads-list-owner")
        self.assertEqual(len(owner["response"]), 4)
        self.assertEqual(owner["response"][-1]["message_id"], fixture["ids"]["empty_email"])
        self.assertIn("unsupported_type_transaction", fixture["seed_requirements"])

    def test_runtime_manifest_requires_tokens_and_rejects_actor_labels(self):
        source = load_manifest()
        runtime = tokenized_control_manifest(source)
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(runtime, handle)
            handle.flush()
            loaded = load_manifest(path=handle.name, require_runtime=True)
            self.assertEqual(len(loaded["cases"]), len(source["cases"]))

            runtime["cases"][0]["actor"] = "owner"
            handle.seek(0)
            handle.truncate()
            json.dump(runtime, handle)
            handle.flush()
            with self.assertRaises(AssertionError):
                load_manifest(path=handle.name, require_runtime=True)

    def test_runtime_manifest_requires_each_protected_case_token(self):
        runtime = tokenized_control_manifest(load_manifest())
        protected = next(case for case in runtime["cases"] if case["name"] not in TOKENLESS_NAMES)
        protected.pop("token")
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(runtime, handle)
            handle.flush()
            with self.assertRaises(AssertionError):
                load_manifest(path=handle.name, require_runtime=True)

    def test_actual_contract_setup_accepts_tokenized_runtime_fixture(self):
        runtime = tokenized_control_manifest(load_manifest())
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(runtime, handle)
            handle.flush()
            with mock.patch.dict(os.environ, {
                "TESTS_FIXTURE": handle.name,
                "TESTS_BASE_URL": "http://127.0.0.1:9",
            }, clear=False):
                MessageReadsContract.setUpClass()
        self.assertEqual(MessageReadsContract.fixture["matrix_version"], MATRIX_VERSION)


if __name__ == "__main__":
    unittest.main()
