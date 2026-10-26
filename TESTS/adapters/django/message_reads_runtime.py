"""Standalone private Django/Go acceptance for communications message reads.

The runner clears inherited application configuration, loads only the tracked
configuration example, generates synthetic credentials, owns private PostgreSQL
and Redis processes, applies every Django migration, then runs the authored
portable matrix against Django and a freshly built Go server over identical
state. ``--versioned`` replays the same matrix through Caddy v1/v2.

The uploadable output contains only ``results.json``. Service logs, generated
tokens, signing keys and the tokenized runtime fixture stay in private
temporary directories and are removed during cleanup.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from TESTS.adapters.django import message_reads as oracle
from TESTS.adapters.django.historical_read_routes import DB_NAME
from TESTS.adapters.go.query_observation import (
    MAX_BYTES, read_query_observations, assert_message_request_budget,
)


ROOT = oracle.ROOT
DATABASE = DB_NAME
SERVICES = ("worker", "django", "go", "caddy", "caddy-misroute", "redis", "postgres")
GROWTH_DETAIL_NUMBER = 709001
GROWTH_SIZES = (8, 24, 48)
EMBED_DIRECTIVE = re.compile(r"^\s*//go:embed\s+(.+?)\s*$", re.MULTILINE)
POSTGRES_STATEMENT = re.compile(
    r"(?:statement|execute [^:]+):\s+((?:SELECT|INSERT|UPDATE|DELETE|WITH).*?)"
    r"(?=\n\d{4}-\d\d-\d\d \d\d:\d\d:\d\d|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
QUERY_LOG_BARRIER = "SELECT 1 /* message-reads-log-boundary */"
TOOL_ENVIRONMENT_KEYS = frozenset({
    "PATH",
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "GOCACHE",
    "GOMAXPROCS",
    "GOFLAGS",
    "GOMODCACHE",
    "GOPATH",
    "GOTOOLCHAIN",
    "GOPROXY",
    "XDG_CACHE_HOME",
})
SOURCE_PATHS = (
    "BE/Dockerfile",
    "BE/requirements.txt",
    "BE/requirements-test.txt",
    "BE_GO/Dockerfile",
    "BE_GO/go.mod",
    "BE_GO/go.sum",
    "UI/Dockerfile",
    "selfhost/.env.example",
    "selfhost/caddy/Caddyfile",
    "TESTS/adapters/caddy.py",
    "TESTS/adapters/django/historical_read_cache.py",
    "TESTS/adapters/django/historical_read_population.py",
    "TESTS/adapters/django/historical_read_routes.py",
    "TESTS/adapters/django/login_jpeg.py",
    "TESTS/adapters/django/message_reads.py",
    "TESTS/adapters/django/message_reads_runtime.py",
    "TESTS/adapters/django/production_runtime.py",
    "TESTS/adapters/django/settings.py",
    "TESTS/adapters/go/payment_category_server.py",
    "TESTS/adapters/go/query_observation.py",
    "TESTS/test_go_query_observation.py",
    "TESTS/adapters/go/server.py",
    "TESTS/contracts/test_message_reads.py",
    "TESTS/fixtures/message_reads.json",
    "TESTS/http_client.py",
    "TESTS/test_message_reads_runtime.py",
    "TESTS/test_message_reads_manifest.py",
)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sanitized_tool_environment(environment=None):
    source = os.environ if environment is None else environment
    return {
        key: value
        for key, value in source.items()
        if key in TOOL_ENVIRONMENT_KEYS
    }


def go_embedded_inputs(root=ROOT):
    root = Path(root)
    embedded = set()
    for source in (root / "BE_GO").rglob("*.go"):
        for directive in EMBED_DIRECTIVE.findall(source.read_text(encoding="utf-8")):
            for pattern in directive.split():
                pattern = pattern.removeprefix("all:")
                matches = list(source.parent.glob(pattern))
                assert matches, f"Go embed pattern matched no files: {source}: {pattern}"
                for match in matches:
                    if match.is_file():
                        embedded.add(match)
                    elif match.is_dir():
                        embedded.update(
                            path for path in match.rglob("*") if path.is_file()
                        )
    return embedded


def source_paths(root=ROOT):
    root = Path(root)
    paths = {root / path for path in SOURCE_PATHS}
    paths.update((root / "BE").rglob("*.py"))
    paths.update(
        path
        for path in (root / "BE_GO").rglob("*")
        if path.is_file()
        and path.suffix in {".go", ".mod", ".sum", ".c", ".h", ".pem", ".sql"}
    )
    paths.update(go_embedded_inputs(root))
    return paths


def source_hashes(root=ROOT):
    root = Path(root)
    result = {}
    for path in sorted(source_paths(root)):
        assert path.is_file(), f"required acceptance source is missing: {path}"
        result[str(path.relative_to(root))] = sha256_file(path)
    return result


def source_stable(report, hashes_func=source_hashes):
    before = report.get("source_sha256")
    after = hashes_func()
    report["source_sha256_after"] = after
    report["source_stable"] = bool(before is not None and before == after)
    if not report["source_stable"]:
        report["source_changes"] = sorted(
            key
            for key in set(before or {}) | set(after)
            if (before or {}).get(key) != after.get(key)
        )
    return report["source_stable"]


def binary_hashes(paths, *, include_python=True):
    result = {}
    custom = {"python": Path(sys.executable)} if include_python else {}
    custom.update({name: Path(path) for name, path in paths.items()})
    for name, path in sorted(custom.items()):
        if not path.is_file():
            raise AssertionError(f"required binary input is missing: {name}: {path}")
    for name, path in sorted(custom.items()):
        mode = path.stat().st_mode
        if not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise AssertionError(f"binary input is not executable: {name}: {path}")
        result[name] = {
            "path": str(path),
            "sha256": sha256_file(path),
        }
    return result


def service_binary_paths(versioned):
    """Resolve every executable the private service lifecycle can invoke."""
    postgres = shutil.which("postgres")
    if not postgres:
        matches = sorted(
            Path("/opt/homebrew/opt").glob("postgresql*/bin/postgres")
        )
        postgres = str(matches[-1]) if matches else None
    assert postgres, "Native PostgreSQL is required"
    binary_directory = Path(postgres).parent
    paths = {
        "postgres": binary_directory / "postgres",
        "initdb": binary_directory / "initdb",
        "pg_ctl": binary_directory / "pg_ctl",
        "createdb": binary_directory / "createdb",
        "psql": binary_directory / "psql",
        "redis-server": shutil.which("redis-server"),
        "go": shutil.which("go"),
    }
    if versioned:
        paths["docker"] = shutil.which("docker")
    for name, path in paths.items():
        assert path, f"Required service binary is missing: {name}"
    return paths


def expected_workload_labels(versioned):
    native = ["django-native", "go-native"]
    return native + (["caddy-v1", "caddy-v2"] if versioned else [])


def prepare_output_directory(path):
    """Keep the uploadable destination limited to the sanitized receipt."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    temporary = path / "results.json.tmp"
    if temporary.is_file() or temporary.is_symlink():
        temporary.unlink()
    unexpected = sorted(
        item.name
        for item in path.iterdir()
        if item.name != "results.json"
    )
    assert not unexpected, (
        "Acceptance output directory must be dedicated; unexpected entries: "
        + ", ".join(unexpected)
    )
    return path


def growth_message_numbers(size):
    assert type(size) is int and size >= 0
    return {
        "detail": GROWTH_DETAIL_NUMBER,
        "list": [709100 + index for index in range(1, size + 1)],
        "transactions": [705000 + index for index in range(1, size + 1)],
    }


def validate_workload_order(workloads, versioned):
    expected = [
        ("django-native", "django", "native"),
        ("go-native", "go", "native"),
    ]
    if versioned:
        expected.extend([
            ("caddy-v1", "django", "caddy"),
            ("caddy-v2", "go", "caddy"),
        ])
    actual = [
        (row.get("label"), row.get("backend"), row.get("transport"))
        for row in workloads
    ]
    assert actual == expected, (
        f"acceptance workload order differs: expected {expected!r}, got {actual!r}"
    )
    return True


def signing_key_pair():
    """Generate one private Ed25519 key pair for the disposable application."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private, public


def read_tracked_environment():
    """Read the tracked example in an otherwise empty process environment."""
    import environ

    with patch.dict(os.environ, {}, clear=True):
        environ.Env.read_env(str(ROOT / "selfhost/.env.example"))
        return dict(os.environ)


def private_environment(work, redis_port, tool_environment):
    """Build isolated application configuration from only the tracked example."""
    values = read_tracked_environment()
    private, public = signing_key_pair()
    values.update({
        "DJANGO_SETTINGS_MODULE": "TESTS.adapters.django.settings",
        "TESTS_DATABASE": DATABASE,
        "TESTS_WORKDIR": str(work),
        "DBTYPE": "POSTGRES",
        "DBNAME": DATABASE,
        "DBUSER": "contracts",
        "DBPASSWORD": "synthetic-db-only",
        "DBHOST": str(work),
        "DBPORT": "5432",
        "USE_PGBOUNCER": "False",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": str(redis_port),
        "REDIS_USERNAME": "default",
        "REDIS_PASSWORD": "synthetic-identity-only",
        "REDIS_SSLAUTH": "",
        "SECRET_KEY": "synthetic-message-reads-session-only",
        "INSTANCE_SETUP_TOKEN": "synthetic-message-reads-setup-only",
        "DOCUMENT_BYPASS_TOKEN": "synthetic-message-reads-document-only",
        "SENJA_WEBHOOK_SECRET": "synthetic-message-reads-webhook-only",
        "CRM_API_TOKEN": "synthetic-message-reads-crm-only",
        "JWT_SECRET_KEY_B64": "",
        "JWT_PUBLIC_KEY_B64": "",
        "JWT_SECRET_KEY": private,
        "JWT_PUBLIC_KEY": public,
        "DEBUG": "False",
        "RATELIMIT_ENABLE": "False",
        "CURRENT_HOST": "http://127.0.0.1:9",
        "CURRENT_HOST_PUPPETEER": "http://127.0.0.1:9",
        "ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
        "CSRF_TRUSTED_ORIGINS": "http://127.0.0.1",
        "CORS_ORIGIN_ALLOW_ALL": "False",
        "CORS_ALLOWED_ORIGINS": "http://127.0.0.1",
        "SESSION_COOKIE_SECURE": "False",
        "CSRF_COOKIE_SECURE": "False",
        "STORE_ON_DISK": "True",
        "AWS_ACCESS_KEY_ID": "synthetic-storage-only",
        "AWS_SECRET_ACCESS_KEY": "synthetic-storage-secret-only",
        "AWS_STORAGE_BUCKET_NAME": "message-reads-fixture",
        "AWS_S3_ENDPOINT_URL": "http://127.0.0.1:9",
        "AWS_S3_PUBLIC_BASE_URL": "",
        "MINIO_ROOT_USER": "synthetic-minio-only",
        "MINIO_ROOT_PASSWORD": "synthetic-minio-secret-only",
        "EMAIL_HOST": "",
        "EMAIL_HOST_USER": "",
        "EMAIL_HOST_PASSWORD": "",
        "SOCIAL_AUTH_GOOGLE_OAUTH2_KEY": "",
        "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET": "",
        "SOCIAL_AUTH_APPLE_ID_CLIENT": "",
        "SOCIAL_AUTH_APPLE_ID_TEAM": "",
        "SOCIAL_AUTH_APPLE_ID_KEY": "",
        "SOCIAL_AUTH_APPLE_ID_SECRET": "",
        "STRIPE_PUBLIC_KEY": "",
        "STRIPE_KEY": "",
        "STRIPE_WEBHOOK_SECRET": "",
        "AI_API_KEY": "",
        "AI_BASE_URL": "http://127.0.0.1:9",
        "MANUAL_CORPUS_BASE_URL": "",
        "MANUAL_SOURCE_ROOT": "",
        "DATA_RESTORE_LOCK_PATH": str(work / "lifecycle.lock"),
        "TZ": "UTC",
    })
    values.update(tool_environment)
    secret_names = {
        "SECRET_KEY",
        "INSTANCE_SETUP_TOKEN",
        "DOCUMENT_BYPASS_TOKEN",
        "SENJA_WEBHOOK_SECRET",
        "CRM_API_TOKEN",
        "JWT_SECRET_KEY",
        "JWT_PUBLIC_KEY",
        "DBPASSWORD",
        "REDIS_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
        "MINIO_ROOT_PASSWORD",
    }
    private_values = {values[name] for name in secret_names if values.get(name)}
    return values, private_values


def apply_private_environment(environment):
    os.environ.clear()
    os.environ.update(environment)


@contextmanager
def owned_message_database(output, report):
    """Remove private files even when service teardown retains live DB data."""
    from TESTS.adapters.django.historical_read_routes import owned_database

    work = None
    try:
        with owned_database(output, report) as owned:
            work, _, _ = owned
            try:
                yield owned
            finally:
                remove_sensitive_files(work, report)
    finally:
        if work is not None:
            # Retry after failed teardown as well, while leaving postgres/ intact.
            remove_sensitive_files(work, report)


def remove_sensitive_files(work, report):
    receipt = report.setdefault("sensitive_cleanup", {"results": {}, "errors": []})
    work = Path(work)

    def remove_manifest():
        private = work / "message-reads-private"
        if private.is_symlink():
            private.unlink()
        elif private.exists():
            shutil.rmtree(private)
        return not private.exists()

    def remove_sql_log():
        (work / "postgres.log").unlink(missing_ok=True)
        structured = work / "message-query-pglog"
        if structured.is_symlink():
            structured.unlink()
        elif structured.exists():
            shutil.rmtree(structured)
        return not (work / "postgres.log").exists() and not structured.exists()

    oracle.cleanup_attempt(receipt, "tokenized_fixture_removed", remove_manifest)
    oracle.cleanup_attempt(receipt, "private_sql_log_removed", remove_sql_log)
    oracle.cleanup_attempt(receipt, "private_query_observation_removed", lambda:
                           (work / "query-observations.jsonl").unlink(missing_ok=True) is None)
    receipt["verified"] = not receipt["errors"] and all(receipt["results"].values())


def initialize_django_apps():
    sys.path[:0] = [str(ROOT / "BE"), str(ROOT)]
    import django

    with patch("environ.Env.read_env"):
        django.setup()
    from django.apps import apps
    from django.conf import settings

    return apps, settings


def cache_override(redis_port):
    return {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": f"redis://127.0.0.1:{redis_port}/1",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "PASSWORD": "synthetic-identity-only",
            },
        }
    }


def migrate_full_schema(private_log):
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    assert not settings.MIGRATION_MODULES, "Acceptance requires full migration history"
    call_command(
        "migrate",
        interactive=False,
        verbosity=0,
        stdout=private_log,
        stderr=private_log,
    )
    executor = MigrationExecutor(connections["default"])
    remaining = executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert not remaining, f"Unapplied migrations remain: {len(remaining)}"
    return {
        "mode": "full-migration-history",
        "applied_migrations": len(executor.loader.applied_migrations),
        "remaining_plan": 0,
    }


# Deliberately authored allowlist, independent of production source loading.
# Any SQL shape drift requires parent review instead of broad table matching.
CORRELATED_SQL = {
    "account": """SELECT user_id, username, email, password, role, is_active, deleted,
 is_superuser, two_fa AS account_two_fa, connected_user_id, collaborator_role,
 COALESCE(collaborator_permissions, 'null'::jsonb) AS collaborator_permissions
 FROM bakney_user WHERE user_id = $1 AND NOT deleted""",
    "association": "SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted",
    "list": """SELECT m.message_id,m.sport_association_id,m.type,m.message,m.subject,m.created_at
 FROM communications_message m
 WHERE m.sport_association_id=$1 AND m.type IN ('EMAIL', 'INSIDE_APP')
 ORDER BY m.created_at DESC""",
    "detail": """SELECT t.message_transaction_id,t.message_id,t.recipient,t.sent_on,
 t.delivered_on,t.opened_on,t.clicked_on,t.bounced_on,t.bounced_reason,t.bounced_type,t.bounced_status
 FROM communications_messagetransaction t
 JOIN communications_message m ON m.message_id=t.message_id AND m.sport_association_id=$2
 WHERE t.message_id=$1 AND m.type IN ('EMAIL', 'INSIDE_APP')
 ORDER BY t.sent_on DESC, t.message_transaction_id""",
}
UUID_ARGUMENT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
QUERY_KEYS = ("total", "association", "messages", "transactions", "writes")
# Independent expectations for rejected POST probes in the authored matrix.
# The restore check precedes authentication even though these views never write.
# Keep it out of CORRELATED_SQL: growth/distinct-owner windows may not use it.
RESTORE_PENDING_SQL = "SELECT EXISTS (SELECT 1 FROM instance_datarestore WHERE state IN ($1, $2))"
PORTABLE_POST_EXPECTATIONS = {
    "message-reads-list-post-method": (
        "/communications/messages/list", 405, "a1000000-0000-4000-8000-000000700001"),
    "message-reads-list-athlete-post": (
        "/communications/messages/list", 403, "a1000000-0000-4000-8000-000000700004"),
    "message-reads-detail-athlete-post": (
        "/communications/messages/a1000000-0000-4000-8000-000000702001/detail",
        403, "a1000000-0000-4000-8000-000000700004"),
}


def _restore_pending_execution(sql, detail, protocol):
    return (" ".join(sql.split()) == RESTORE_PENDING_SQL
            and detail == "parameters: $1 = 'queued', $2 = 'running'"
            and protocol.startswith("execute "))


def _portable_failure(error):
    # Only exact fixed messages become public reason codes. Never copy exception
    # text: even an AssertionError can contain SQL, arguments, tokens or paths.
    return {
        "PostgreSQL evidence: portable raw and driver counts differ": "raw_driver_mismatch",
        "PostgreSQL evidence: unknown SQL category or extra statement": "unexpected_sql",
        "PostgreSQL evidence: missing or invalid exact bound arguments": "invalid_arguments",
        "PostgreSQL evidence: portable restore/account sequence differs": "restore_account_sequence",
        "PostgreSQL evidence: portable account scope differs": "account_scope",
        "PostgreSQL evidence: portable business and driver counts differ": "business_driver_mismatch",
    }.get(str(error), "portable_reconciliation_failed")


def evidence_require(condition, reason):
    # Do not interpolate SQL, bound values, JSON, exceptions, or private paths.
    if not condition:
        raise AssertionError("PostgreSQL evidence: " + reason)


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        evidence_require(key not in value, "duplicate JSON field")
        value[key] = item
    return value


class MessageQueryObservations:
    """Bind live response receipts to one append-only file and one server run.

    Only completed numeric records returned by the strict reader are used.
    Prefix hashing also rejects in-place rewrites, beyond inode/run-ID checks.
    The private stream survives until exit-zero final validation and hashing.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.identity = self.run_id = None
        self.length = 0
        self.prefix_sha256 = hashlib.sha256(b"").hexdigest()
        self.records = {}
        self.initial_request_ids = None
        self.read()

    def read(self, *, finalized=False):
        try:
            info = self.path.lstat()
            identity = (info.st_dev, info.st_ino)
            evidence_require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES,
                             "invalid observation file")
            evidence_require(self.identity in (None, identity), "observation file replaced")
            with self.path.open("rb") as source:
                data = source.read(MAX_BYTES + 1)
                opened = os.fstat(source.fileno())
            evidence_require((opened.st_dev, opened.st_ino) == identity and len(data) <= MAX_BYTES,
                             "observation file changed")
            evidence_require(len(data) >= self.length and
                             hashlib.sha256(data[:self.length]).hexdigest() == self.prefix_sha256,
                             "observation file rewritten or truncated")
            observed = read_query_observations(self.path, finalized=finalized)
            after = self.path.lstat()
            evidence_require((after.st_dev, after.st_ino) == identity,
                             "observation file replaced while reading")
            evidence_require(self.run_id in (None, observed["run_id"]), "observation run changed")
            evidence_require(all(observed["requests"].get(key) == value
                                 for key, value in self.records.items()), "observation records changed")
            if finalized:
                evidence_require(self.path.read_bytes() == data, "final observation file changed")
            self.identity, self.run_id = identity, observed["run_id"]
            self.length, self.prefix_sha256 = len(data), hashlib.sha256(data).hexdigest()
            self.records = observed["requests"]
            return observed
        except OSError:
            raise AssertionError("PostgreSQL evidence: observation file unavailable") from None

    def mark(self):
        records = frozenset(self.read()["requests"])
        if self.initial_request_ids is None:
            self.initial_request_ids = records
        return records

    def responses(self, mark, responses, *, overlap=False, timeout=5, exact_budget=True):
        identifiers = []
        for response in responses:
            headers = response[2]
            # HTTPMessage preserves duplicate wire headers; dicts are offline
            # verifier inputs only. A comma-joined header is invalid as well.
            values = (headers.get_all("X-Query-Observation-ID", [])
                      if hasattr(headers, "get_all") else
                      [value for key, value in headers.items()
                       if key.lower() == "x-query-observation-id"])
            evidence_require(len(values) == 1 and isinstance(values[0], str) and
                             re.fullmatch(re.escape(self.run_id) + r"-[1-9][0-9]*", values[0]),
                             "missing or forged response observation ID")
            identifiers.append(values[0])
        selected = set(identifiers)
        evidence_require(len(selected) == len(identifiers) and selected.isdisjoint(mark),
                         "duplicate or reused response observation ID")
        deadline = time.monotonic() + timeout
        while True:
            records = self.read()["requests"]
            delta = set(records) - mark
            evidence_require(delta <= selected, "extraneous observation request")
            if delta == selected:
                break
            evidence_require(time.monotonic() < deadline, "response observation summary timeout")
            time.sleep(0.01)
        per_request = {}
        for identifier in identifiers:
            row = records[identifier]
            if exact_budget:
                per_request[identifier] = assert_message_request_budget(row)
            else:
                # Portable rejection/negotiation/auth cases retain their
                # existing authored SQL expectations. Bind every header and
                # reconcile transport totals, without assigning them a false
                # successful-owner three-business-query claim.
                evidence_require(row["ping_starts"] == row["ping_ends"] == 0,
                                 "explicit driver Ping in portable request")
                evidence_require(row["connect_starts"] + row["reset_starts"] == row["query_starts"],
                                 "portable driver acquisition mismatch")
                per_request[identifier] = {**row, "raw_expected": row["query_starts"] + row["reset_probes"]}
        maximum = max((records[key]["active_requests_at_start"] for key in selected), default=0)
        if overlap:
            evidence_require(len(selected) == 8 and maximum == 8, "eight-request Go overlap not proved")
            evidence_require(sorted(records[key]["active_requests_at_start"] for key in selected)
                             == list(range(1, 9)), "unexpected concurrent request cohort")
        return {
            "per_request": per_request,
            "business": sum(records[key]["query_starts"] for key in selected),
            "raw_expected": sum(row["raw_expected"] for row in per_request.values()),
            "reset_probes": sum(row["reset_probes"] for row in per_request.values()),
            "active_requests_at_start_max": maximum,
        }

    def finalize(self, *, process_exit):
        evidence_require(type(process_exit) is int and process_exit == 0,
                         "Go observation process exit was not zero")
        observed = self.read(finalized=True)
        return {"verified": True, "run_id": observed["run_id"],
                "requests": len(observed["requests"]),
                "initial_request_ids": sorted(self.initial_request_ids or ()),
                "request_ids": sorted(observed["requests"]), "footer": observed["footer"],
                "sha256": self.prefix_sha256, "process_exit": process_exit}


def _business_execution(sql, detail):
    shapes = {" ".join(value.split()): key for key, value in CORRELATED_SQL.items()}
    category = shapes.get(" ".join(sql.split()))
    evidence_require(category is not None, "unknown SQL category or extra statement")
    arity = 2 if category == "detail" else 1
    pattern = "parameters: " + ", ".join(
        rf"\${index} = '({UUID_ARGUMENT})'" for index in range(1, arity + 1)
    )
    matched = re.fullmatch(pattern, detail) if isinstance(detail, str) else None
    evidence_require(matched is not None, "missing or invalid exact bound arguments")
    return category, matched.groups()


def _exact_probe(sql, detail, protocol):
    return sql == "-- ping" and detail is None and protocol == "statement"


def reconcile_message_postgres(content, binding, proof, *, barrier, receipt=None):
    """Reconcile same-owner SQL scope/multiplicities with independent HTTP proofs.

    Identical SQL cannot assign batch executions to an HTTP request. Each of the
    eight driver/query receipts supplies its own budget; PG supplies exact scoped
    business multiplicities and an independent raw/probe aggregate.
    """
    diagnostic = postgres_statement_diagnostics(content, barrier=barrier)
    receipt = {} if receipt is None else receipt
    receipt["query"] = diagnostic["query"]
    receipt["postgres_statement_counts"] = diagnostic["statement_counts"]
    proof = proof() if callable(proof) else proof
    receipt["observation"] = proof
    counts = dict.fromkeys(CORRELATED_SQL, 0)
    phases = []
    expected_args = {"account": (binding["owner"],),
                     "association": (binding["owner"],),
                     "list": (binding["association"],),
                     "detail": (binding["message"], binding["association"])}
    for sql, detail, protocol in _bounded_events(content, barrier):
        if _exact_probe(sql, detail, protocol):
            continue  # Still included in raw totals and must match driver proof.
        category, args = _business_execution(sql, detail)
        evidence_require(args == expected_args[category], "foreign-scope execution")
        evidence_require(category in ("account", "association", binding["route"]), "wrong-route execution")
        counts[category] += 1
        phases.append(category)
    n = len(proof["per_request"])
    evidence_require(n in (1, 8), "invalid message measurement size")
    if n == 1:
        evidence_require(phases == ["account", "association", binding["route"]],
                         "reordered sequential message execution")
    evidence_require(counts == {key: n if key in ("account", "association", binding["route"]) else 0
                                for key in CORRELATED_SQL}, "business shape multiplicity differs")
    statements = diagnostic["statement_counts"]
    evidence_require(statements["unknown"] == 0 and diagnostic["query"]["writes"] == 0,
                     "unknown or write execution")
    evidence_require(statements["business"] == proof["business"] == 3 * n,
                     "business budget differs")
    evidence_require(statements["readiness_transport_probe"] == proof["reset_probes"],
                     "raw and driver probe counts differ")
    evidence_require(statements["total"] == diagnostic["query"]["total"] == proof["raw_expected"],
                     "raw and driver totals differ")
    receipt["business_shapes"] = counts
    receipt["split_accounting_verified"] = True
    return receipt


def postgres_events(content):
    evidence_require(isinstance(content, str) and content.endswith("\n"), "incomplete JSON log")
    events = []
    sessions = {}
    processes = {}
    for line in content.splitlines():
        try:
            event = json.loads(line, object_pairs_hook=_unique_json_object)
        except (ValueError, TypeError):
            raise AssertionError("PostgreSQL evidence: invalid JSON log") from None
        evidence_require(isinstance(event, dict), "invalid event")
        evidence_require(
            type(event.get("pid")) is int and event["pid"] > 0
            and type(event.get("line_num")) is int and event["line_num"] > 0
            and isinstance(event.get("session_id"), str) and bool(event["session_id"]),
            "missing process/session identity",
        )
        session = event["session_id"]
        evidence_require(processes.get(event["pid"], session) == session, "ambiguous process/session identity")
        processes[event["pid"]] = session
        prior = sessions.get(session)
        if prior is not None:
            evidence_require(prior == (event["pid"], event["line_num"] - 1), "session sequence mismatch")
        sessions[session] = (event["pid"], event["line_num"])
        evidence_require(
            event.get("user") == "contracts" and event.get("dbname") == DATABASE
            and event.get("backend_type") == "client backend"
            and event.get("error_severity") == "LOG" and event.get("state_code", "00000") == "00000",
            "unexpected event scope or severity",
        )
        # Parameters must be in this same native JSON event. No DETAIL pairing,
        # line-prefix heuristics, or fallback association by connection exists.
        evidence_require(isinstance(event.get("message"), str), "missing statement event")
        matched = re.fullmatch(r"(statement|execute [^:\r\n]+): (.+)", event["message"], re.DOTALL)
        evidence_require(matched is not None, "unexpected nonstatement event")
        evidence_require(not any(key in event for key in ("statement", "internal_query", "context")),
                         "unexpected secondary statement")
        events.append((matched[2], event.get("detail"), matched[1]))
    return events


def _bounded_events(content, barrier):
    evidence_require(
        isinstance(barrier, str) and re.fullmatch(
            r"SELECT 1 /\* message-reads-log-boundary(?::[0-9a-f]{32})? \*/", barrier
        ) is not None,
        "invalid explicit barrier",
    )
    events = postgres_events(content)
    positions = [index for index, (sql, detail, protocol) in enumerate(events)
                 if sql == barrier and detail is None and protocol == "statement"]
    evidence_require(positions == [len(events) - 1], "missing, repeated, or nonfinal explicit barrier")
    return events[:-1]


def parse_postgres_requests(content, bindings, *, barrier, driver_proof=None):
    """Prove exact SQL executions for independently bound synthetic requests.

    Private input only. Output contains counters and caller-owned labels, never
    statements, session identities, arguments, or a token. Connections can be
    reused by many requests and each request can use several connections.
    """
    evidence_require(isinstance(bindings, dict) and len(bindings) == 8, "expected eight request bindings")
    seen_ids = set()
    for cid, binding in bindings.items():
        evidence_require(isinstance(cid, str) and re.fullmatch(r"distinct-(?:go-)?[0-7]", cid), "invalid request label")
        evidence_require(isinstance(binding, dict) and set(binding) == {"owner", "association", "message", "route"},
                         "invalid request binding")
        evidence_require(binding["route"] in ("list", "detail"), "unknown request route")
        for key in ("owner", "association", "message"):
            value = binding[key]
            evidence_require(isinstance(value, str) and re.fullmatch(UUID_ARGUMENT, value), "invalid bound UUID")
            evidence_require(value not in seen_ids, "ambiguous request binding")
            seen_ids.add(value)
    shape_to_category = {" ".join(sql.split()): category for category, sql in CORRELATED_SQL.items()}
    phases = {cid: [] for cid in bindings}
    per_request = {cid: dict.fromkeys(QUERY_KEYS, 0) for cid in bindings}
    probes = 0
    for sql, detail, _protocol in _bounded_events(content, barrier):
        if driver_proof is not None and _exact_probe(sql, detail, _protocol):
            probes += 1
            continue
        category = shape_to_category.get(" ".join(sql.split()))
        evidence_require(category is not None, "unknown SQL category or extra statement")
        arity = 2 if category == "detail" else 1
        pattern = "parameters: " + ", ".join(
            rf"\${index} = '({UUID_ARGUMENT})'" for index in range(1, arity + 1)
        )
        matched = re.fullmatch(pattern, detail) if isinstance(detail, str) else None
        evidence_require(matched is not None, "missing or invalid exact bound arguments")
        args = matched.groups()
        assigned = []
        for cid, binding in bindings.items():
            expected = {
                "account": (binding["owner"],), "association": (binding["owner"],),
                "list": (binding["association"],),
                "detail": (binding["message"], binding["association"]),
            }[category]
            if args == expected and (category in ("account", "association") or category == binding["route"]):
                assigned.append(cid)
        evidence_require(len(assigned) == 1, "unassigned, ambiguous, or wrong-scope execution")
        cid = assigned[0]
        expected_phases = ["account", "association", bindings[cid]["route"]]
        phase = phases[cid]
        evidence_require(len(phase) < 3 and category == expected_phases[len(phase)], "retry, extra, or reordered execution")
        phase.append(category)
        counts = per_request[cid]
        counts["total"] += 1
        counts["association"] += int(category == "association")
        counts["messages"] += int(category in ("list", "detail"))
        counts["transactions"] += int(category == "detail")
    evidence_require(all(len(phase) == 3 for phase in phases.values()), "missing execution or zero-work request")
    result = {
        "aggregate": {key: sum(counts[key] for counts in per_request.values()) for key in QUERY_KEYS},
        "per_request": per_request,
    }
    if driver_proof is not None:
        evidence_require(len(driver_proof["per_request"]) == 8 and driver_proof["business"] == 24,
                         "invalid distinct driver proof")
        evidence_require(probes == driver_proof["reset_probes"] and
                         24 + probes == driver_proof["raw_expected"], "distinct driver probe mismatch")
        result["raw_total"] = 24 + probes
        result["reset_probes"] = probes
    return result


class PostgresQueryLog:
    """Observe native JSON events in the disposable PostgreSQL cluster (15+)."""

    def __init__(self, work, private_log):
        postgres = shutil.which("postgres")
        if not postgres:
            matches = sorted(Path("/opt/homebrew/opt").glob("postgresql*/bin/postgres"))
            postgres = str(matches[-1]) if matches else None
        assert postgres, "Native PostgreSQL is required"
        self.binaries = Path(postgres).parent
        self.work = Path(work)
        self.private_log = private_log
        self.log_directory = self.work / "message-query-pglog"
        self.log_directory.mkdir(mode=0o700)
        self.sql_log = self.log_directory / "events.json"
        self._drained = None
        settings = {
            "logging_collector": "on", "log_destination": "jsonlog",
            "log_directory": str(self.log_directory), "log_filename": "events.log",
            "log_rotation_age": "0", "log_rotation_size": "0",
            "log_truncate_on_rotation": "off", "log_statement": "all",
            "log_parameter_max_length": "-1", "log_min_duration_statement": "-1",
            "log_duration": "off", "log_connections": "off", "log_disconnections": "off",
        }
        for name, value in settings.items():
            self.command(["ALTER SYSTEM SET", name, "=", "'" + value.replace("'", "''") + "'"], "--command")
        # Startup-only collector setting: this is the owned cluster, before
        # migrations or application servers. The shared lifecycle is untouched.
        self._run([
            self.binaries / "pg_ctl", "-D", self.work / "postgres", "-m", "fast",
            "-w", "restart", "-l", self.work / "postgres.log",
            "-o", f"-k {self.work} -h '' -F",
        ])

    def _run(self, arguments):
        return subprocess.run(
            [str(argument) for argument in arguments], check=True, timeout=60,
            stdout=self.private_log, stderr=subprocess.STDOUT,
        )

    def command(self, sql_tokens, mode):
        self._run([
            self.binaries / "psql", "-h", self.work, "-U", "contracts",
            "-d", DATABASE, "-X", "-w", "-Atq", "-v", "ON_ERROR_STOP=1", mode, " ".join(sql_tokens),
        ])

    def _boundary(self):
        info = self.sql_log.stat()
        return {"offset": info.st_size, "identity": (info.st_dev, info.st_ino)}

    def _through_barrier(self, mark):
        from uuid import uuid4

        barrier = QUERY_LOG_BARRIER.replace(" */", ":" + uuid4().hex + " */")
        self.command([barrier], "--command")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            boundary = self._boundary()
            evidence_require(boundary["identity"] == mark["identity"] and boundary["offset"] >= mark["offset"],
                             "private log rotated or truncated")
            with self.sql_log.open("rb") as stream:
                stream.seek(mark["offset"])
                raw = stream.read(boundary["offset"] - mark["offset"])
            if raw.endswith(b"\n"):
                try:
                    content = raw.decode("utf-8", "strict")
                    records = [json.loads(line, object_pairs_hook=_unique_json_object) for line in content.splitlines()]
                except (ValueError, UnicodeError):
                    raise AssertionError("PostgreSQL evidence: invalid private JSON log") from None
                if any(isinstance(event, dict) and event.get("message") == "statement: " + barrier for event in records):
                    self._drained = boundary
                    return content, barrier
            time.sleep(0.01)
        raise AssertionError("PostgreSQL evidence: log barrier was not observed")

    def mark(self):
        self.private_log.flush()
        boundary = self._boundary()
        # Drain earlier snapshots/seed SQL through a unique explicit barrier.
        # A byte offset alone would race the asynchronous logging collector.
        start = self._drained or {"offset": 0, "identity": boundary["identity"]}
        self._through_barrier(start)
        return dict(self._drained)

    def since(self, mark):
        pending = getattr(self, "portable_observation", None)
        expectation = getattr(self, "portable_expectation", None)
        try:
            if pending is not None:
                pending["split_accounting_verified"] = False
            content, barrier = self._through_barrier(mark)
            events = _bounded_events(content, barrier)
            result = count_postgres_statements(sql for sql, _detail, _protocol in events)
            if pending is None:
                return result
            diagnostic = postgres_statement_diagnostics(
                content, barrier=barrier, portable_restore=expectation is not None)
            pending["query"] = diagnostic["query"]
            pending["postgres_statement_counts"] = diagnostic["statement_counts"]
            probes = sum(_exact_probe(sql, detail, protocol) for sql, detail, protocol in events)
            evidence_require(result["total"] == pending["raw_expected"] and probes == pending["reset_probes"],
                             "portable raw and driver counts differ")
            business = []
            for sql, detail, protocol in events:
                if _exact_probe(sql, detail, protocol):
                    continue  # Counted above and reconciled with independent driver proof.
                if expectation is not None and _restore_pending_execution(sql, detail, protocol):
                    business.append(("restore_pending", ()))
                else:
                    business.append(_business_execution(sql, detail))
            if expectation is not None:
                evidence_require([category for category, _ in business] == ["restore_pending", "account"],
                                 "portable restore/account sequence differs")
                evidence_require(business[1][1] == (expectation[2],), "portable account scope differs")
                pending["restore_pending_reads"] = 1
            evidence_require(diagnostic["statement_counts"]["business"] == len(business) == pending["business"],
                             "portable business and driver counts differ")
            pending["split_accounting_verified"] = True
            return result
        except BaseException as error:
            if pending is not None:
                pending["failure_stage"] = "postgres_reconciliation"
                pending["failure_reason"] = _portable_failure(error)
            raise
        finally:
            # Failed evidence remains in its receipt, never as the next request's
            # authorization. Subsequent snapshots cannot reconcile this window.
            self.portable_observation = None
            self.portable_expectation = None

    def since_with_diagnostics(self, mark):
        content, barrier = self._through_barrier(mark)
        return postgres_statement_diagnostics(content, barrier=barrier)

    def since_by_request(self, mark, bindings, *, driver_proof=None, receipt=None):
        content, barrier = self._through_barrier(mark)
        if receipt is not None:
            diagnostic = postgres_statement_diagnostics(content, barrier=barrier)
            receipt.update(query=diagnostic["query"], postgres_statement_counts=diagnostic["statement_counts"])
        driver_proof = driver_proof() if callable(driver_proof) else driver_proof
        if receipt is not None:
            receipt["driver_observation"] = driver_proof
        return parse_postgres_requests(content, bindings, barrier=barrier, driver_proof=driver_proof)

    def since_message(self, mark, binding, proof, *, receipt):
        content, barrier = self._through_barrier(mark)
        return reconcile_message_postgres(content, binding, proof, barrier=barrier, receipt=receipt)


def run_locked_same_owner_batch(query_log, observations, request, cids, *, receipt, timeout=10):
    # Keep driver exceptions/connection strings/SQL exclusively in private logs.
    try:
        return _locked_same_owner_batch(query_log, observations, request, cids,
                                       receipt=receipt, timeout=timeout)
    except Exception as error:
        query_log.private_log.write(traceback.format_exc())
        query_log.private_log.flush()
        if isinstance(error, AssertionError):
            raise
        raise AssertionError("PostgreSQL evidence: overlap coordinator failed") from None


def _locked_same_owner_batch(query_log, observations, request, cids, *, receipt, timeout):
    """Fixture-only table lock, acquired before the measured PostgreSQL mark.

    One separate control connection disables only its own statement logging.
    Every monitor query uses that connection. No Go logging/session settings are
    changed and no business log record is filtered. Eight exact blocked SELECTs
    and no completed futures are required before release; response IDs then bind
    that cohort to eight independent observer records.
    """
    import psycopg2

    evidence_require(len(cids) == len(set(cids)) == 8, "expected eight same-owner requests")
    control = psycopg2.connect(host=str(query_log.work), port=5432, user="contracts",
                               dbname=DATABASE, connect_timeout=5,
                               options="-c statement_timeout=5000 -c lock_timeout=5000 "
                                       "-c idle_in_transaction_session_timeout=20000")
    executor = None
    futures = []
    mark = None
    lock = receipt.setdefault("overlap_lock", {"released": False, "workers_joined": False,
                                               "control_closed": False, "blocked_backends": 0})
    try:
        control.autocommit = True
        with control.cursor() as cursor:
            cursor.execute("SET SESSION log_statement = 'none'")
        control.autocommit = False
        with control.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            controller_pid = cursor.fetchone()[0]
            cursor.execute("LOCK TABLE communications_message IN ACCESS EXCLUSIVE MODE")
        mark = query_log.mark()
        observation_mark = observations.mark()
        executor = ThreadPoolExecutor(max_workers=8)
        ready = threading.Barrier(8, timeout=timeout)

        def send(cid):
            ready.wait()
            return request(cid)

        futures = [executor.submit(send, cid) for cid in cids]
        deadline = time.monotonic() + timeout
        expected = " ".join(CORRELATED_SQL["list"].split())
        while True:
            evidence_require(not any(future.done() for future in futures),
                             "same-owner request finished before overlap proof")
            with control.cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute("""SELECT pid, query, pg_blocking_pids(pid)
                    FROM pg_stat_activity WHERE datname = current_database()
                    AND usename = 'contracts' AND backend_type = 'client backend'
                    AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'""")
                rows = cursor.fetchall()
            # Strictly require the exact controller as direct blocker, rather
            # than accepting arbitrary lock waiters or a table-name substring.
            blocked = [pid for pid, sql, blockers in rows
                       if blockers == [controller_pid] and " ".join(sql.split()) == expected]
            lock["blocked_backends"] = len(set(blocked))
            evidence_require(len(blocked) <= 8, "extraneous blocked message request")
            if len(blocked) == len(set(blocked)) == 8:
                evidence_require(not any(future.done() for future in futures),
                                 "same-owner request completed during overlap proof")
                lock["all_eight_active_before_finish"] = True
                break
            evidence_require(time.monotonic() < deadline, "eight Go backends did not block on controller")
            time.sleep(0.01)
        # Release before waiting for any HTTP result, even if a worker failed.
        control.commit()
        lock["released"] = True
        responses = [future.result(timeout=20) for future in futures]
        return mark, observation_mark, responses
    finally:
        # Release first, then join all workers. No failure may retain the lock
        # while the executor waits for HTTP timeouts (Client timeout is 15s).
        failed = sys.exc_info()[0] is not None
        cleanup_errors = []
        try:
            control.rollback()
            lock["released"] = True
        except BaseException:
            cleanup_errors.append("rollback")
        try:
            control.close()
            lock["control_closed"] = bool(control.closed)
        except BaseException:
            cleanup_errors.append("close")
        try:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
                lock["workers_joined"] = all(future.done() for future in futures)
            else:
                lock["workers_joined"] = True
        except BaseException:
            cleanup_errors.append("join")
        lock["cleanup_errors"] = cleanup_errors
        if (failed or cleanup_errors) and mark is not None:
            diagnostic = query_log.since_with_diagnostics(mark)
            receipt.update(query=diagnostic["query"], postgres_statement_counts=diagnostic["statement_counts"])
        evidence_require(not cleanup_errors and lock["released"] and lock["workers_joined"] and lock["control_closed"],
                         "overlap lock cleanup failed")


def postgres_statement_diagnostics(content, *, barrier, portable_restore=False):
    """Classify private executions without removing any from the raw budget.

    The exact pgx v5.9.2 PgConn.Ping statement may come from session reset or a
    readiness Ping. SQL alone cannot distinguish those callers or bind a probe
    to an HTTP request. This classification provides diagnostic counts only.
    """
    events = _bounded_events(content, barrier)
    counts = dict.fromkeys(("total", "business", "readiness_transport_probe", "unknown"), 0)
    shapes = {" ".join(sql.split()): category for category, sql in CORRELATED_SQL.items()}
    for sql, detail, protocol in events:
        counts["total"] += 1
        category = shapes.get(" ".join(sql.split()))
        arity = 2 if category == "detail" else 1
        arguments = "parameters: " + ", ".join(
            rf"\${index} = '{UUID_ARGUMENT}'" for index in range(1, arity + 1)
        )
        if sql == "-- ping" and detail is None and protocol == "statement":
            counts["readiness_transport_probe"] += 1
        elif category is not None and isinstance(detail, str) and re.fullmatch(arguments, detail):
            counts["business"] += 1
        elif portable_restore and _restore_pending_execution(sql, detail, protocol):
            counts["business"] += 1
        else:
            counts["unknown"] += 1
    return {
        "query": count_postgres_statements(sql for sql, _detail, _protocol in events),
        "statement_counts": counts,
    }


def safe_query_counts(value, keys):
    """Project only fixed numeric counters into the public growth receipt."""
    evidence_require(isinstance(value, dict) and all(
        type(value.get(key)) is int and value[key] >= 0 for key in keys
    ), "invalid diagnostic counters")
    return {key: value[key] for key in keys}


def parse_postgres_statements(content):
    """Legacy stderr parser retained only for existing offline controls."""
    return count_postgres_statements(sql for sql in POSTGRES_STATEMENT.findall(content)
                                     if sql.strip() != QUERY_LOG_BARRIER)


def count_postgres_statements(statements):
    association = messages = transactions = total = writes = 0
    for statement in statements:
        lowered = statement.lower()
        total += 1
        def references(table):
            return bool(re.search(r'\b(?:from|join|update|into)\s+"?' + table + r'"?\b', lowered))

        association += int(references("application_sportassociation"))
        messages += int(references("communications_message"))
        transactions += int(references("communications_messagetransaction"))
        writes += int(
            statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE "))
        )
    return {
        "total": total,
        "association": association,
        "messages": messages,
        "transactions": transactions,
        "writes": writes,
    }


@contextmanager
def django_application(report, versioned, queries):
    import uvicorn
    from TESTS.adapters.django import historical_read_routes
    from TESTS.http_client import poll

    from django.core.asgi import get_asgi_application

    observer = oracle.AsgiObserver(get_asgi_application(), queries)
    server = uvicorn.Server(uvicorn.Config(
        observer,
        host="0.0.0.0" if versioned else "127.0.0.1",
        port=0,
        lifespan="off",
        log_level="critical",
        access_log=False,
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    historical_read_routes.service_state(report, "django", "start_attempted")
    try:
        thread.start()
        poll(
            lambda: server.started and thread.is_alive(),
            bool,
            timeout=25,
        )
        historical_read_routes.service_state(report, "django", "running")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", observer, port
    except BaseException:
        historical_read_routes.service_state(report, "django", "cleanup_failed")
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=25)
        stopped = not thread.is_alive()
        historical_read_routes.service_state(
            report, "django", "stopped" if stopped else "cleanup_failed"
        )
        assert stopped, "Django ASGI server did not stop"


def _request(client, token, path, cid):
    from TESTS.http_client import Client

    return Client(client, token).request(
        "GET",
        path,
        headers={
            "Accept-Language": "en",
            "X-Correlation-ID": cid,
        },
        include_headers=True,
    )


def growth_expected_response(fixture, size, route):
    """Author complete DTOs from the synthetic workload specification."""
    if route == "detail":
        return [{
            "message_transaction_id": oracle._uid(705000 + index),
            "message": oracle._uid(GROWTH_DETAIL_NUMBER),
            "recipient": f"growth-{index:02d}@example.test",
            "sent_on": f"2026-10-23T13:00:00.{index:06d}Z",
            "delivered_on": None, "opened_on": None, "clicked_on": None,
            "bounced_on": None, "bounced_reason": None,
            "bounced_type": None, "bounced_status": None,
        } for index in range(size, 0, -1)]
    assert route == "list"
    authored = next(case["response"] for case in fixture["authored"]["cases"]
                    if case["name"] == "message-reads-list-owner")
    # PostgreSQL DESC places the authored NULL first, then microseconds DESC.
    rows = [{
        "message_id": oracle._uid(709100 + index),
        "sport_association": oracle._uid(701001), "type": "EMAIL",
        "message": f"Growth list {index:02d}", "subject": f"growth {index:02d}",
        "created_at": f"2026-10-23T14:00:00.{index:06d}Z",
    } for index in range(size, 0, -1)]
    return [authored[0], *rows, {
        "message_id": oracle._uid(GROWTH_DETAIL_NUMBER),
        "sport_association": oracle._uid(701001), "type": "EMAIL",
        "message": "Growth detail", "subject": "growth detail",
        "created_at": "2026-10-23T14:00:00Z",
    }, *authored[1:]]


def assert_growth_response(response, expected):
    """Check the full body and the same successful headers as the portable read."""
    status, content, headers = response
    assert status == 200, status
    assert content == expected, "growth response differs from authored complete DTOs"
    assert headers.get("Content-Language") == "en", headers
    assert headers.get("Content-Type", "").split(";", 1)[0] == "application/json", headers
    assert set(headers.get("Allow", "").replace(" ", "").split(",")) == {"GET", "OPTIONS"}
    assert headers.get("Set-Cookie") is None
    assert headers.get("WWW-Authenticate") is None


def distinct_owner_specifications():
    """Authored independent scopes and DTOs, never derived from backend output."""
    rows = []
    for index in range(8):
        binding = {
            "owner": oracle._uid(710000 + index),
            "association": oracle._uid(711000 + index),
            "message": oracle._uid(712000 + index),
            "route": "list" if index % 2 == 0 else "detail",
        }
        transaction_id = oracle._uid(713000 + index)
        message = {
            "message_id": binding["message"], "sport_association": binding["association"],
            "type": "EMAIL", "message": f"Independent &lt;{index}&gt; &amp; &quot;body&quot;",
            "subject": "None", "created_at": "2026-10-24T15:00:00Z",
        }
        delivery = {
            "message_transaction_id": transaction_id, "message": binding["message"],
            "recipient": f"independent-{index}@example.test", "sent_on": "2026-10-24T16:00:00Z",
            "delivered_on": None, "opened_on": None, "clicked_on": None,
            "bounced_on": None, "bounced_reason": None, "bounced_type": None, "bounced_status": None,
        }
        rows.append({
            "cid": f"distinct-go-{index}", "binding": binding,
            "transaction_id": transaction_id, "body": f'Independent <{index}> & "body"',
            "recipient": delivery["recipient"],
            "path": "/communications/messages/list" if binding["route"] == "list"
                    else f'/communications/messages/{binding["message"]}/detail',
            "expected": [message] if binding["route"] == "list" else [delivery],
        })
    return rows


def seed_distinct_owner_fixture(created, private_values):
    from application.models import SportAssociation, User
    from auditlog.context import disable_auditlog
    from communications.models import Message, MessageTransaction
    from django.db import transaction
    from rest_framework_simplejwt.tokens import AccessToken

    rows = distinct_owner_specifications()

    def seed(model, identifier, **values):
        evidence_require(not model._base_manager.filter(pk=identifier).exists(), "independent fixture collision")
        row = model(pk=identifier, **values)
        model._base_manager.bulk_create([row])
        created.append((model, identifier))
        return row

    with disable_auditlog(), transaction.atomic():
        for index, row in enumerate(rows):
            binding = row["binding"]
            owner = seed(
                User, binding["owner"], username=f"message-distinct-{index}",
                email=f"message-distinct-{index}@example.test", password="!synthetic",
                role=User.ASSOCIATION, first_name="Independent", last_name="Fixture",
                is_active=True, deleted=False,
            )
            association = seed(
                SportAssociation, binding["association"], user=owner,
                denomination=f"Independent message scope {index}", tax_code=f"710000000{index:02d}",
            )
            message = seed(
                Message, binding["message"], sport_association=association,
                type=Message.EMAIL, message=row["body"], subject=None,
            )
            Message._base_manager.filter(pk=message.pk).update(
                created_at=datetime(2026, 10, 24, 15, tzinfo=timezone.utc)
            )
            delivery = seed(
                MessageTransaction, row["transaction_id"], message=message, recipient=row["recipient"],
            )
            MessageTransaction._base_manager.filter(pk=delivery.pk).update(
                sent_on=datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
            )
            token = AccessToken()
            token["user_id"] = binding["owner"]
            row["token"] = str(token)
            private_values.add(row["token"])
    return rows


def run_distinct_owner_queries(*, apps, fixture, raw, observer, go_base,
                               owned_postgres_log, created, private_values, observations=None, progress=None):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache

    rows = seed_distinct_owner_fixture(created, private_values)
    bindings = {row["cid"]: row["binding"] for row in rows}
    before_state = oracle.snapshot_models(apps)
    before_cache = snapshot_historical_read_cache(raw)
    cursor = observer.cursor()
    mark = owned_postgres_log.mark()
    observation_mark = observations.mark() if observations is not None else None
    ready = threading.Barrier(8, timeout=15)

    def request(row):
        ready.wait()
        return _request(go_base, row["token"], row["path"], row["cid"])

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(request, rows))
    # Collect SQL before model/cache inspection can issue observer-unowned SQL.
    progress = {} if progress is None else progress
    proof = None
    if observations is not None:
        evidence = owned_postgres_log.since_by_request(
            mark, bindings, driver_proof=lambda: observations.responses(observation_mark, responses),
            receipt=progress,
        )
        proof = progress["driver_observation"]
    else:
        evidence = owned_postgres_log.since_by_request(mark, bindings)
    for row, response in zip(rows, responses, strict=True):
        assert_growth_response(response, row["expected"])
    observed = observer.delta(cursor)
    assert observed == [], "independent Go batch reached Django"
    evidence_require(set(evidence) == ({"aggregate", "per_request", "raw_total", "reset_probes"}
                                     if proof is not None else {"aggregate", "per_request"})
                     and set(evidence["per_request"]) == set(bindings), "incomplete correlated receipt")
    for cid, binding in bindings.items():
        evidence_require(evidence["per_request"][cid] == {
            "total": 3, "association": 1, "messages": 1,
            "transactions": int(binding["route"] == "detail"), "writes": 0,
        }, "independent per-request budget differs")
    evidence_require(evidence["aggregate"] == {
        "total": 24, "association": 8, "messages": 8, "transactions": 4, "writes": 0,
    }, "independent aggregate differs")
    oracle.assert_state_unchanged(before_state, oracle.snapshot_models(apps))
    oracle.assert_cache_unchanged(before_cache, snapshot_historical_read_cache(raw), fixture["contrasts"])
    result = {
        "scope": "eight distinct owners; not same-owner per-request measurement",
        "backend": "go", "transport": "native", "requests": 8,
        "list_requests": 4, "detail_requests": 4, "query": evidence["aggregate"],
        "per_request_queries": evidence["per_request"],
        "driver_observation": proof,
        "response_observation_ids": dict(zip(bindings, proof["per_request"], strict=True)) if proof is not None else {},
        "raw_total": evidence.get("raw_total", evidence["aggregate"]["total"]),
        "reset_probes": evidence.get("reset_probes", 0),
        "per_request_query_budgets_verified": True, "complete_responses_verified": True,
        "split_accounting_verified": proof is not None,
        "asgi_events": 0, "state_unchanged": True, "cache_unchanged": True,
        "response_sha256": {row["cid"]: oracle.digest(response[1])
                            for row, response in zip(rows, responses, strict=True)},
        "correlation": "exact SQL shape and bound UUIDs in native PostgreSQL JSON events",
        "same_owner_per_request_counts_measured": False,
        "acceptance_gate": False, "parent_review_required": False,
    }
    progress.update(result)
    return progress


def run_growth_queries(
    *,
    apps,
    fixture,
    raw,
    observer,
    queries,
    django_base,
    go_base,
    owned_postgres_log,
    created,
    private_values=None,
    progress=None,
    observations=None,
):
    """Exercise growing list/detail rows and bounded concurrent reads."""
    from application.models import SportAssociation
    from auditlog.context import disable_auditlog
    from communications.models import Message, MessageTransaction
    from TESTS.adapters.django.historical_read_cache import (
        snapshot_historical_read_cache,
    )

    evidence_require(observations is not None, "same-owner Go observation interface required")
    sizes = GROWTH_SIZES
    samples = {"django": {"list": [], "detail": []}, "go": {"list": [], "detail": []}}
    progress = {} if progress is None else progress
    progress.update({
        "success": False, "acceptance_gate": False, "parent_review_required": False,
        "bounded_checks_passed": False, "sizes": list(sizes), "routes": samples,
        "concurrency": {},
        "blockers": [],
    })
    oracle.assert_fixture_baseline(apps, fixture, raw)
    owner = SportAssociation._base_manager.get(pk=oracle._uid(701001))
    token = fixture["tokens"]["owner"]
    growth_message = oracle._uid(growth_message_numbers(0)["detail"])
    with disable_auditlog():
        Message._base_manager.bulk_create([Message(
            pk=growth_message,
            sport_association=owner,
            type=Message.EMAIL,
            message="Growth detail",
            subject="growth detail",
        )])
        created.append((Message, growth_message))
        Message._base_manager.filter(pk=growth_message).update(
            created_at=datetime(2026, 10, 23, 14, 0, 0, tzinfo=timezone.utc)
        )
    previous = 0
    for size in sizes:
        with disable_auditlog():
            new_messages = []
            new_transactions = []
            for index in range(previous + 1, size + 1):
                identifier = oracle._uid(
                    growth_message_numbers(size)["list"][index - 1]
                )
                new_messages.append(Message(
                    pk=identifier,
                    sport_association=owner,
                    type=Message.EMAIL,
                    message=f"Growth list {index:02d}",
                    subject=f"growth {index:02d}",
                ))
                new_transactions.append(MessageTransaction(
                    pk=oracle._uid(705000 + index),
                    message_id=growth_message,
                    recipient=f"growth-{index:02d}@example.test",
                ))
            if new_messages:
                Message._base_manager.bulk_create(new_messages)
                MessageTransaction._base_manager.bulk_create(new_transactions)
                created.extend((Message, row.pk) for row in new_messages)
                created.extend((MessageTransaction, row.pk) for row in new_transactions)
                for index in range(previous + 1, size + 1):
                    Message._base_manager.filter(
                        pk=oracle._uid(709100 + index)
                    ).update(created_at=datetime(
                        2026, 10, 23, 14, 0, 0, index, tzinfo=timezone.utc
                    ))
                    MessageTransaction._base_manager.filter(
                        pk=oracle._uid(705000 + index)
                    ).update(sent_on=datetime(
                        2026, 10, 23, 13, 0, 0, index, tzinfo=timezone.utc
                    ))
        previous = size
        for backend, base_url in (("django", django_base), ("go", go_base)):
            for route in ("list", "detail"):
                cid = f"growth-{backend}-{route}-{size}"
                path = (
                    "/communications/messages/list"
                    if route == "list"
                    else f"/communications/messages/{growth_message}/detail"
                )
                before_state = oracle.snapshot_models(apps)
                before_cache = snapshot_historical_read_cache(raw)
                cursor = observer.cursor()
                mark = owned_postgres_log.mark() if backend == "go" else None
                observation_mark = observations.mark() if backend == "go" and observations is not None else None
                queries.expect(cid)
                started = time.monotonic()
                response = _request(base_url, token, path, cid)
                status, content, _ = response
                elapsed = time.monotonic() - started
                sample = {"size": size, "elapsed_seconds": elapsed}
                # Retain the sample before *any* verifier can reject it.
                samples[backend][route].append(sample)
                if backend == "django":
                    sample["query"] = safe_query_counts(queries.delta(cid), QUERY_KEYS)
                else:
                    owned_postgres_log.since_message(
                        mark, {"owner": oracle._uid(700001), "association": str(owner.pk),
                               "message": growth_message, "route": route},
                        lambda: observations.responses(observation_mark, [response]), receipt=sample,
                    )
                query = sample["query"]
                observed = observer.delta(cursor)
                assert_growth_response(response, growth_expected_response(fixture, size, route))
                oracle.assert_attribution(
                    backend, "GET", path, status, observed, cid
                )
                oracle.assert_state_unchanged(
                    before_state, oracle.snapshot_models(apps)
                )
                oracle.assert_cache_unchanged(
                    before_cache,
                    snapshot_historical_read_cache(raw),
                    fixture["contrasts"],
                )
                assert query["messages"] == 1, query
                assert query["transactions"] == (1 if route == "detail" else 0), query
                assert query["writes"] == 0, query
                if backend == "go":
                    assert query["association"] == 1, query
                    counts = sample["postgres_statement_counts"]
                    assert counts["total"] == query["total"], "diagnostic raw total mismatch"
                    assert sum(counts[key] for key in (
                        "business", "readiness_transport_probe", "unknown"
                    )) == counts["total"], "diagnostic partition mismatch"
                    assert counts["unknown"] == 0, "unknown PostgreSQL growth statement"
                sample["response_sha256"] = oracle.digest(content)
    for backend, routes in samples.items():
        for route, rows in routes.items():
            assert len({row["observation"]["business"] if backend == "go" and observations is not None
                        else row["query"]["total"] for row in rows}) == 1, (
                f"{backend} {route} query count grew with candidate rows"
            )

    concurrency = 8
    concurrency_rows = {}
    progress["concurrency"] = concurrency_rows
    for backend, base_url in (("django", django_base), ("go", go_base)):
        before_state = oracle.snapshot_models(apps)
        before_cache = snapshot_historical_read_cache(raw)
        cursor = observer.cursor()
        cids = [f"growth-concurrent-{backend}-{index}" for index in range(concurrency)]

        def request(cid):
            queries.expect(cid)
            return _request(
                base_url,
                token,
                "/communications/messages/list",
                cid,
            )

        cohort = {"requests": concurrency, "per_request_query_budgets_verified": False}
        concurrency_rows[backend] = cohort
        if backend == "go":
            evidence_require(observations is not None, "same-owner Go observation interface required")
            mark, observation_mark, responses = run_locked_same_owner_batch(
                owned_postgres_log, observations, request, cids, receipt=cohort,
            )
            owned_postgres_log.since_message(
                mark, {"owner": oracle._uid(700001), "association": str(owner.pk),
                       "message": growth_message, "route": "list"},
                lambda: observations.responses(observation_mark, responses, overlap=True), receipt=cohort,
            )
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                responses = list(executor.map(request, cids))
        for response in responses:
            assert_growth_response(response, growth_expected_response(fixture, sizes[-1], "list"))
        observed = observer.delta(cursor)
        per_request = {}
        if backend == "django":
            assert len(observed) == concurrency
            assert {row["cid"] for row in observed} == set(cids)
            assert all(row["status"] == 200 for row in observed)
            for cid in cids:
                oracle.assert_attribution(
                    "django", "GET", "/communications/messages/list", 200,
                    [row for row in observed if row["cid"] == cid], cid,
                )
                budget = queries.delta(cid)
                assert budget == samples[backend]["list"][-1]["query"], (
                    f"concurrent request {cid} query budget differs: {budget}"
                )
                per_request[cid] = budget
            query = {
                key: sum(queries.delta(cid)[key] for cid in cids)
                for key in ("total", "messages", "transactions", "writes")
            }
        else:
            assert observed == []
            query = cohort["query"]
            per_request = cohort["observation"]["per_request"]
        assert query["messages"] == concurrency, query
        assert query["transactions"] == 0 and query["writes"] == 0, query
        oracle.assert_state_unchanged(
            before_state, oracle.snapshot_models(apps)
        )
        oracle.assert_cache_unchanged(
            before_cache,
            snapshot_historical_read_cache(raw),
            fixture["contrasts"],
        )
        cohort.update({
            "requests": concurrency,
            "query": query,
            "asgi_events": len(observed),
            "per_request_queries": per_request,
            "per_request_query_budgets_verified": True,
            "complete_responses_verified": True,
        })
    progress["distinct_owner_concurrency"] = {}
    distinct = run_distinct_owner_queries(
        apps=apps, fixture=fixture, raw=raw, observer=observer, go_base=go_base,
        owned_postgres_log=owned_postgres_log, created=created,
        private_values=private_values if private_values is not None else set(),
        observations=observations,
        progress=progress["distinct_owner_concurrency"],
    )
    progress.update({
        "success": True,
        "acceptance_gate": False,
        "parent_review_required": False,
        "distinct_owner_concurrency": distinct,
        "bounded_checks_passed": True,
        "blockers": [],
        "sizes": list(sizes),
        "routes": samples,
        "concurrency": concurrency_rows,
    })
    return progress


def v2_misroute_control(
    *, report, private_log, django_port, fixture, apps, observer, queries, raw
):
    """Route real Caddy v2 to Django and reject the complete observed delta."""
    import copy
    from TESTS.adapters.django.historical_read_cache import (
        snapshot_historical_read_cache,
    )
    from TESTS.contracts.test_message_reads import assert_case
    import unittest

    source = next(
        case for case in fixture["runtime"]["cases"]
        if case["name"] == "message-reads-list-owner"
    )
    case = copy.deepcopy(source)
    case["name"] = "caddy-v2-deliberate-django-misroute"
    before_state = oracle.snapshot_models(apps)
    before_cache = snapshot_historical_read_cache(raw)
    cursor = observer.cursor()
    queries.expect(case["name"])
    upstream = f"host.docker.internal:{django_port}"
    with owned_caddy(report, private_log, upstream, upstream,
                     service="caddy-misroute") as (gateway, image, container):
        status, _ = assert_case(
            unittest.TestCase(),
            gateway + "/api/v2",
            case,
        )
    observed = observer.delta(cursor)
    oracle.assert_attribution(
        "django", "GET", case["path"], status, observed, case["name"]
    )
    query = queries.delta(case["name"])
    oracle.assert_query_evidence({**case, "name": source["name"]}, query)
    rejected = False
    try:
        oracle.assert_attribution(
            "go", "GET", case["path"], status, observed, case["name"]
        )
    except oracle.AttributionError:
        rejected = True
    assert rejected, "v2 misroute was not rejected by Django observation attribution"
    oracle.assert_state_unchanged(before_state, oracle.snapshot_models(apps))
    oracle.assert_cache_unchanged(
        before_cache,
        snapshot_historical_read_cache(raw),
        fixture["contrasts"],
    )
    return {
        "success": True,
        "attempted_v2_backend": "go",
        "transport": "caddy",
        "public_prefix": "/api/v2",
        "configured_routes": {"/api/v1": "django", "/api/v2": "django"},
        "container": dict(container),
        "caddy_image": image,
        "asgi_delta": observed,
        "actual_django_requests": len(observed),
        "status": status,
        "rejected_by_attribution": rejected,
        "query": query,
    }


def run_portable_workload(**kwargs):
    """Keep the original 30-case matrix; bind Go wire headers before snapshots."""
    postgres = kwargs.get("postgres_queries")
    observations = getattr(postgres, "observations", None) if postgres is not None else None
    if kwargs["backend"] != "go" or observations is None:
        return oracle.run_portable_workload(**kwargs)
    from TESTS.contracts import test_message_reads as portable

    original = portable.Client
    receipts = []

    class BoundClient(original):
        def request(self, method, path, data=None, headers=None, **options):
            include_headers = options.get("include_headers", False)
            options["include_headers"] = True
            mark = observations.mark()
            response = super().request(method, path, data, headers, **options)
            try:
                proof = observations.responses(mark, [response], exact_budget=False)
            except BaseException:
                receipts.append({"split_accounting_verified": False,
                                 "failure_stage": "response_observation",
                                 "failure_reason": "response_observation_failed"})
                raise
            cid = (headers or {}).get("X-Correlation-ID", "")
            expectation = next((expected for name, expected in PORTABLE_POST_EXPECTATIONS.items()
                                if (cid == name or re.fullmatch(
                                    re.escape(kwargs.get("label", "")) + r"-[1-9][0-9]*-" + re.escape(name), cid))
                                and method == "POST" and path == expected[0]
                                and response[0] == expected[1]), None)
            postgres.portable_observation = proof
            postgres.portable_expectation = expectation
            receipts.append(proof)
            return response if include_headers else response[:2]

    with patch.object(portable, "Client", BoundClient):
        result = oracle.run_portable_workload(**kwargs)
    result["query_observation_headers"] = receipts
    evidence_require(len(receipts) == result["observed_requests"], "portable header receipts incomplete")
    return result


def run_versioned(
    *,
    report,
    private_log,
    fixture,
    apps,
    observer,
    queries,
    raw,
    django_base,
    django_port,
    go_runtime,
    postgres_queries,
):
    workloads = []
    with owned_caddy(
        report, private_log, f"host.docker.internal:{django_port}",
        go_runtime["proxy_upstream"],
    ) as (gateway, image, container):
        report["versioned_runtime"] = {
            "caddy_image": image,
            "routes": {"/api/v1": "django", "/api/v2": "go"},
            "same_fixture": True,
            "container": dict(container),
        }
        for route, backend in (("/api/v1", "django"), ("/api/v2", "go")):
            workloads.append(run_portable_workload(
                label=f"caddy-{route.rsplit('/', 1)[-1]}",
                backend=backend,
                base_url=gateway + route,
                fixture=fixture,
                apps=apps,
                observer=observer,
                queries=queries,
                raw=raw,
                private_log=private_log,
                postgres_queries=postgres_queries,
            ))
    # This second proxy has its own container, configuration and verified
    # cleanup. It exercises the real public v2 path with a deliberate misroute.
    report["misroute_control"] = v2_misroute_control(
        report=report,
        private_log=private_log,
        django_port=django_port,
        fixture=fixture,
        apps=apps,
        observer=observer,
        queries=queries,
        raw=raw,
    )
    return workloads


@contextmanager
def owned_caddy(report, private_log, django, golang, *, service="caddy"):
    """Track each proxy independently, including verified container removal."""
    from TESTS.adapters.django import historical_read_routes
    from TESTS.adapters.caddy import proxy
    from contextlib import ExitStack

    historical_read_routes.service_state(report, service, "start_attempted")
    container = {}
    stack = ExitStack()
    body_error = None
    try:
        gateway, image = stack.enter_context(proxy(
            django, golang, private_log, container_observer=container.update,
        ))
        assert container.get("container_id"), "Owned Caddy identity was not observed"
        historical_read_routes.service_state(report, service, "running")
        yield gateway, image, container
    except BaseException as error:
        body_error = error
    try:
        stack.close()
        container_id = container.get("container_id")
        if container_id:
            inspected = subprocess.run(
                ["docker", "inspect", container_id],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            )
            assert inspected.returncode == 1, "Owned Caddy removal was not verified"
    except BaseException as cleanup_error:
        historical_read_routes.service_state(report, service, "cleanup_failed")
        raise cleanup_error from body_error
    historical_read_routes.service_state(report, service, "stopped")
    if body_error is not None:
        raise body_error


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="directory for the sanitized results.json receipt",
    )
    parser.add_argument(
        "--versioned",
        action="store_true",
        help="also replay all cases through shipped Caddy /api/v1 and /api/v2",
    )
    return parser.parse_args(argv)


def cleanup_complete(report):
    from TESTS.adapters.django.historical_read_routes import cleanup_complete as check

    return check(report) and report.get("sensitive_cleanup", {}).get("verified", True) and all(
        report.get("service_cleanup", {}).get(name) in {"not_started", "stopped"}
        for name in SERVICES
    )


def _service_report():
    from TESTS.adapters.django.historical_read_routes import service_state

    report = {
        "service_cleanup": {name: "not_started" for name in SERVICES},
        "owned_directory_removed": False,
        "cleanup_errors": [],
    }
    for name in SERVICES:
        service_state(report, name, "not_started")
    return report


def _record_error(report, stage, error):
    report.setdefault("errors", []).append({
        "stage": stage,
        "type": type(error).__name__,
        "message": str(error),
    })


def runtime_acceptance(report, expected_cases):
    """Pure final decision for this run's bounded HTTP/query read scope.

    Consume receipts from the strict runtime verifiers only after orchestration
    has finalized. This does not validate imported historical artifacts, replace
    SQL/driver/response verification, or establish frontend or family parity.
    Missing, partial or failed evidence always returns False.
    """
    def require(condition):
        evidence_require(condition, "incomplete runtime acceptance evidence")

    def digest(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    def cache_valid(value):
        require(value["typed_values_exact"] is True and value["absolute_expirations_exact"] is True)
        require(value["readiness_prefix_excluded"] is False and value["unexpected_readiness_keys"] == 0)

    identifiers = set()

    def observation(proof, count, *, owner_budget=False):
        rows = proof["per_request"]
        require(len(rows) == count and count > 0 and identifiers.isdisjoint(rows))
        identifiers.update(rows)
        for row in rows.values():
            require(row["query_starts"] == row["query_ends"])
            require(row["connect_starts"] == row["connect_ends"] and row["reset_starts"] == row["reset_ends"])
            require(row["ping_starts"] == row["ping_ends"] == 0)
            require(row["driver_starts"] == row["driver_ends"] == row["query_starts"] ==
                    row["connect_starts"] + row["reset_starts"])
            require(0 <= row["reset_probes"] <= row["reset_starts"])
            require(row["raw_expected"] == row["query_starts"] + row["reset_probes"])
            if owner_budget:
                require(row["query_starts"] == 3)
        require(proof["business"] == sum(row["query_starts"] for row in rows.values()))
        require(proof["raw_expected"] == sum(row["raw_expected"] for row in rows.values()))
        require(proof["reset_probes"] == sum(row["reset_probes"] for row in rows.values()))

    def sql_receipt(receipt, proof, *, business_total=False):
        require(receipt["split_accounting_verified"] is True)
        require(receipt["query"]["writes"] == 0)
        counts = receipt["postgres_statement_counts"]
        require(counts["unknown"] == 0 and counts["business"] == proof["business"])
        require(counts["readiness_transport_probe"] == proof["reset_probes"])
        require(counts["total"] == proof["raw_expected"] ==
                counts["business"] + counts["readiness_transport_probe"])
        if business_total:
            # The distinct-owner parser returns authored business aggregates;
            # its separately retained raw total includes every attested probe.
            require(receipt["query"]["total"] == counts["business"])
            require(receipt["raw_total"] == counts["total"] and receipt["reset_probes"] == proof["reset_probes"])
        else:
            require(receipt["query"]["total"] == counts["total"])

    try:
        require(report["runtime_executed"] is True and report["errors"] == [] and report["cleanup_errors"] == [])
        require(type(report["versioned"]) is bool and report["workload_order_valid"] is True)
        validate_workload_order(report["workloads"], report["versioned"])
        require(expected_cases and len({case["name"] for case in expected_cases}) == len(expected_cases))
        require(report["fixture"]["runtime_cases"] == len(expected_cases))
        for workload in report["workloads"]:
            require(workload["success"] is True and workload["complete_case_sequence"] is True)
            require(workload["failed_tests"] == [] and not workload.get("skipped"))
            require(workload["tests"] > 0 and workload["assertion_cases"] == workload["observed_requests"] == len(expected_cases))
            requests = workload["requests"]
            require([row["case"] for row in requests] == [case["name"] for case in expected_cases])
            for row, case in zip(requests, expected_cases, strict=True):
                require(not row.get("error_type") and row["backend"] == workload["backend"])
                require((row["method"], row["path"], row["status"]) ==
                        (case["method"], case["path"].split("?", 1)[0], case["status"]))
                require(row["query"]["writes"] == 0 and row["state"] == report["fixture"]["baseline_state"])
                cache_valid(row["cache"])
                require(row["asgi_events"] == row["django_attribution"] == int(workload["backend"] == "django"))
            if workload["backend"] == "go":
                receipts = workload["query_observation_headers"]
                require(len(receipts) == len(expected_cases))
                for row, proof in zip(requests, receipts, strict=True):
                    observation(proof, 1)
                    sql_receipt(proof, proof)
                    require(proof["query"] == row["query"])

        growth = report["growth"]
        require(growth["success"] is True and growth["bounded_checks_passed"] is True and growth["blockers"] == [])
        require(growth["sizes"] == list(GROWTH_SIZES) and set(growth["routes"]) == {"django", "go"})
        for backend, routes in growth["routes"].items():
            require(set(routes) == {"list", "detail"})
            for samples in routes.values():
                require([row["size"] for row in samples] == growth["sizes"])
                for row in samples:
                    require(digest(row["response_sha256"]) and row["query"]["writes"] == 0)
                    if backend == "go":
                        observation(row["observation"], 1, owner_budget=True)
                        sql_receipt(row, row["observation"])
                if backend == "django":
                    require(len({row["query"]["total"] for row in samples}) == 1)
        require(set(growth["concurrency"]) == {"django", "go"})
        for backend, cohort in growth["concurrency"].items():
            require(cohort["requests"] == 8 and len(cohort["per_request_queries"]) == 8)
            require(cohort["complete_responses_verified"] is True and cohort["per_request_query_budgets_verified"] is True)
            if backend == "go":
                proof = cohort["observation"]
                observation(proof, 8, owner_budget=True)
                sql_receipt(cohort, proof)
                require(cohort["per_request_queries"] == proof["per_request"] and proof["active_requests_at_start_max"] == 8)
                lock = cohort["overlap_lock"]
                require(lock["blocked_backends"] == 8 and lock["all_eight_active_before_finish"] is True)
                require(all(lock[key] is True for key in ("released", "control_closed", "workers_joined")))
                require(lock["cleanup_errors"] == [])
        distinct = growth["distinct_owner_concurrency"]
        labels = {row["cid"] for row in distinct_owner_specifications()}
        require(distinct["requests"] == len(labels) and set(distinct["per_request_queries"]) == labels)
        require(all(distinct[key] is True for key in ("complete_responses_verified", "per_request_query_budgets_verified",
                                                     "state_unchanged", "cache_unchanged")))
        proof = distinct["driver_observation"]
        observation(proof, len(labels), owner_budget=True)
        sql_receipt(distinct, proof, business_total=True)
        require(set(distinct["response_observation_ids"]) == labels and
                set(distinct["response_observation_ids"].values()) == set(proof["per_request"]))

        final = report["query_observation"]
        require(final["verified"] is True and type(final["process_exit"]) is int and final["process_exit"] == 0)
        footer = final["footer"]
        require(footer["complete"] is True and footer["type"] == "footer" and footer["run_id"] == final["run_id"])
        # Finalize still uses the strict reader. Independently reject a damaged
        # receipt here rather than trusting its verified/complete booleans alone.
        zeros = (
            "driver_errors", "driver_unbound_starts", "driver_unbound_ends", "connect_errors",
            "reset_errors", "ping_errors", "driver_violations", "unknown_driver_kinds",
            "invalid_reset_probes", "query_errors", "unbound_starts", "unbound_ends", "violations",
            "late_starts", "late_ends", "duplicate_ends", "duplicate_finishes", "reused_contexts",
            "missing_contexts", "foreign_contexts", "context_mismatches", "active_requests",
            "active_queries", "active_driver_operations", "post_close_events", "active_requests_at_start",
        )
        require(all(type(footer[key]) is int and footer[key] == 0 for key in zeros))
        totals = ("query_starts", "query_ends", "driver_starts", "driver_ends", "reset_probes",
                  "connect_starts", "connect_ends", "reset_starts", "reset_ends", "ping_starts", "ping_ends",
                  "request_starts", "request_finishes", "finish_calls", "summaries")
        require(all(type(footer[key]) is int and footer[key] >= 0 for key in totals))
        require(type(footer["version"]) is int and footer["version"] == 1)
        require(footer["query_starts"] == footer["query_ends"] and footer["driver_starts"] == footer["driver_ends"])
        require(all(footer[kind + "_starts"] == footer[kind + "_ends"] for kind in ("connect", "reset", "ping")))
        require(footer["driver_starts"] == sum(footer[kind + "_starts"] for kind in ("connect", "reset", "ping")))
        require(footer["reset_probes"] <= footer["reset_starts"])
        require(digest(final["sha256"]) and len(set(final["request_ids"])) == len(final["request_ids"]) == final["requests"])
        initial = set(final["initial_request_ids"])
        require(initial.isdisjoint(identifiers) and len(initial) == len(final["initial_request_ids"]))
        require(set(final["request_ids"]) == identifiers | initial)  # Includes startup readiness, without a magic count.
        require(all(re.fullmatch(re.escape(final["run_id"]) + r"-[1-9][0-9]*", key) for key in final["request_ids"]))
        require(all(footer[key] == final["requests"] for key in ("request_starts", "request_finishes", "finish_calls", "summaries")))
        sources = report["source_sha256"]
        require(report["source_stable"] is True and set(SOURCE_PATHS) <= set(sources))
        require(all(digest(value) for value in sources.values()))
        artifact = report["go_artifact"]
        require(sources == report["source_sha256_after"] == report["go_build_source_sha256"] == artifact["build_source_sha256"])
        require(artifact["kind"] == "freshly-built-native-server" and artifact["same_fixture"] is True)
        require(all(digest(artifact["binary_sha256"][name]["sha256"]) and artifact["binary_sha256"][name]["path"]
                    for name in ("go-server", "image-codec")))
        require(report["schema"]["mode"] == "full-migration-history" and report["schema"]["applied_migrations"] > 0)
        require(report["schema"]["remaining_plan"] == 0 and digest(report["managed_state"]["sha256"]))
        cache_valid(report["cache"])
        require(report["cleanup_verified"] is True and cleanup_complete(report))
        for section, names in (("fixture_cleanup", ("runtime_manifest_removed", "fixture_rows_removed", "cache_contrasts_removed", "managed_state_restored")),
                               ("sensitive_cleanup", ("tokenized_fixture_removed", "private_sql_log_removed", "private_query_observation_removed"))):
            require(report[section]["errors"] == [] and all(report[section]["results"][name] is True for name in names))
        require(report["sensitive_cleanup"]["verified"] is True)
        required_services = {"django", "go", "postgres", "redis"}
        if report["versioned"]:
            required_services.update(("caddy", "caddy-misroute"))
            proxy = report["versioned_runtime"]
            require(proxy["same_fixture"] is True and proxy["routes"] == {"/api/v1": "django", "/api/v2": "go"})
            misroute = report["misroute_control"]
            require(misroute["success"] is True and misroute["rejected_by_attribution"] is True)
            require(misroute["transport"] == "caddy" and misroute["public_prefix"] == "/api/v2" and
                    misroute["status"] == 200 and misroute["actual_django_requests"] == 1)
            require(proxy["caddy_image"] and all(value["container"]["container_id"] and value["container"]["image_id"]
                                                for value in (proxy, misroute)))
        require(all(report["service_cleanup"][name] == "stopped" for name in required_services))
        return True
    except (AssertionError, KeyError, TypeError, ValueError, AttributeError):
        return False


def main(argv=None):
    args = parse_args(argv)
    from TESTS.adapters.django.production_runtime import require_production_python

    prepare_output_directory(args.output)
    result_path = args.output / "results.json"
    report = _service_report()
    report.update({
        "success": False,
        "scope": "communications message list/detail reads",
        "runtime_executed": False,
        "versioned": args.versioned,
        "workloads": [],
        "complete_message_family_parity": False,
        "acceptance_gate": False,
        "parent_review_required": False,
        "errors": [],
    })
    expected_cases = []
    private_values = set()
    source_snapshot = None
    log = None
    try:
        report["python"] = require_production_python()
        report["python"]["binary"] = binary_hashes({})["python"]
        report["service_binary_sha256"] = binary_hashes(
            service_binary_paths(args.versioned),
            include_python=False,
        )
        source_snapshot = source_hashes()
        report["source_sha256"] = source_snapshot
        tool_environment = sanitized_tool_environment()
        tool_environment.update({"LANG": "C", "LC_ALL": "C"})
        # Service children must not inherit any developer application secret.
        apply_private_environment(tool_environment)
        with tempfile.TemporaryDirectory(
            prefix="az-message-reads-private-", dir="/tmp"
        ) as private_directory:
            private_output = Path(private_directory)
            from TESTS.adapters.django import historical_read_routes

            with owned_message_database(
                private_output, report
            ) as (work, redis_port, log):
                environment, private_values = private_environment(
                    work, redis_port, tool_environment
                )
                apply_private_environment(environment)
                apps, _settings = initialize_django_apps()
                from django.db import connections
                from django.test.utils import override_settings

                override = override_settings(CACHES=cache_override(redis_port))
                override.enable()
                raw = None
                queries = None
                try:
                    connections.close_all()
                    query_log = PostgresQueryLog(work, log)
                    report["schema"] = migrate_full_schema(log)
                    original_state = oracle.snapshot_models(apps)
                    from application.models import User
                    from instance.models import InstanceConfiguration

                    assert not InstanceConfiguration.objects.exists(), (
                        "migration fixture is unexpectedly configured"
                    )
                    assert not User.original_objects.exists(), (
                        "migration fixture is unexpectedly populated"
                    )
                    import redis

                    raw = redis.Redis(
                        host="127.0.0.1",
                        port=redis_port,
                        db=1,
                        password="synthetic-identity-only",
                        decode_responses=False,
                    )
                    fixture = oracle.seed_fixture(
                        apps,
                        work / "message-reads-private",
                        raw,
                    )
                    expected_cases = fixture["authored"]["cases"]
                    private_values.update(fixture["tokens"].values())
                    report["fixture"] = {
                        "seed_counts": fixture["seed_counts"],
                        "runtime_cases": len(fixture["runtime"]["cases"]),
                        "baseline_facts": fixture["authored"]["baseline_facts"],
                        "private_fixture_removed_on_cleanup": True,
                        "baseline_state": oracle.state_receipt(
                            fixture["baseline_state"]
                        ),
                    }
                    extra_created = []
                    queries = oracle.QueryTrace()
                    build_sources = source_hashes()
                    report["go_build_source_sha256"] = build_sources
                    try:
                        with django_application(
                            report, args.versioned, queries
                        ) as (django_base, observer, django_port):
                            report["workloads"].append(oracle.run_portable_workload(
                                label="django-native",
                                backend="django",
                                base_url=django_base,
                                fixture=fixture,
                                apps=apps,
                                observer=observer,
                                queries=queries,
                                raw=raw,
                                private_log=log,
                            ))
                            from TESTS.adapters.go.payment_category_server import (
                                native_server,
                            )

                            go_process = {"process": None}
                            observations = None
                            historical_read_routes.service_state(
                                report, "go", "start_attempted"
                            )

                            def remember(process):
                                go_process["process"] = process

                            try:
                                with native_server(
                                    work,
                                    log,
                                    tool_environment,
                                    f"127.0.0.1:{redis_port}",
                                    versioned=args.versioned,
                                    process_observer=remember,
                                    query_observation=True,
                                ) as go_runtime:
                                    observations = MessageQueryObservations(go_runtime["query_observation_path"])
                                    query_log.observations = observations
                                    process = go_process["process"]
                                    assert process is not None and process.poll() is None
                                    executable = Path(process.args[0]).resolve()
                                    assert executable.parent == work.resolve(), (
                                        "Go executable escaped the private work directory"
                                    )
                                    artifacts = {
                                        "go-server": executable,
                                        "image-codec": executable.with_name("image-codec"),
                                    }
                                    report["go_artifact"] = {
                                        "kind": "freshly-built-native-server",
                                        "binary_sha256": binary_hashes(
                                            artifacts, include_python=False
                                        ),
                                        "build_source_sha256": build_sources,
                                        "same_fixture": True,
                                    }
                                    historical_read_routes.service_state(
                                        report, "go", "running"
                                    )
                                    report["workloads"].append(
                                        run_portable_workload(
                                            label="go-native",
                                            backend="go",
                                            base_url=go_runtime["base_url"],
                                            fixture=fixture,
                                            apps=apps,
                                            observer=observer,
                                            queries=queries,
                                            raw=raw,
                                            private_log=log,
                                            postgres_queries=query_log,
                                        )
                                    )
                                    if args.versioned:
                                        report["versioned_workloads"] = run_versioned(
                                            report=report,
                                            private_log=log,
                                            fixture=fixture,
                                            apps=apps,
                                            observer=observer,
                                            queries=queries,
                                            raw=raw,
                                            django_base=django_base,
                                            django_port=django_port,
                                            go_runtime=go_runtime,
                                            postgres_queries=query_log,
                                        )
                                    report["growth"] = {}
                                    run_growth_queries(
                                        apps=apps,
                                        fixture=fixture,
                                        raw=raw,
                                        observer=observer,
                                        queries=queries,
                                        django_base=django_base,
                                        go_base=go_runtime["base_url"],
                                        owned_postgres_log=query_log,
                                        created=extra_created,
                                        private_values=private_values,
                                        progress=report["growth"],
                                        observations=observations,
                                    )
                                    for blocker in report["growth"].get("blockers", []):
                                        _record_error(report, "query-attribution", AssertionError(blocker))
                                    report["remaining_verification_gates"] = report["growth"].get("blockers", [])
                            finally:
                                process = go_process["process"]
                                if process is not None and process.poll() is None:
                                    try:
                                        historical_read_routes.stop_process(process)
                                    except BaseException:
                                        historical_read_routes.service_state(
                                            report, "go", "cleanup_failed"
                                        )
                                        raise
                                stopped = process is None or process.poll() is not None
                                historical_read_routes.service_state(
                                    report,
                                    "go",
                                    "stopped" if stopped else "cleanup_failed",
                                )
                                if not stopped:
                                    raise AssertionError("Go server did not stop")
                                if observations is not None:
                                    report["query_observation"] = {"verified": False}
                                    try:
                                        report["query_observation"] = observations.finalize(
                                            process_exit=process.returncode if process is not None else None,
                                        )
                                    except BaseException as error:
                                        _record_error(report, "query-observation-finalization", error)
                    finally:
                        assert queries is not None
                        queries.close()
                    del query_log
                    report["workloads"].extend(
                        report.pop("versioned_workloads", [])
                    )
                    from TESTS.adapters.django.historical_read_cache import (
                        snapshot_historical_read_cache,
                    )

                    report["cache"] = oracle.assert_cache_unchanged(
                        fixture["baseline_cache"],
                        snapshot_historical_read_cache(raw),
                        fixture["contrasts"],
                    )
                    report["runtime_executed"] = True
                    fixture_cleanup = {
                        "results": {},
                        "errors": report["cleanup_errors"],
                    }
                    oracle.cleanup_attempt(
                        fixture_cleanup,
                        "runtime_manifest_removed",
                        lambda: not fixture["runtime_path"].exists()
                        or fixture["runtime_path"].unlink() is None,
                    )
                    oracle.cleanup_attempt(
                        fixture_cleanup,
                        "fixture_rows_removed",
                        lambda: oracle.delete_created(fixture, extra_created),
                    )
                    oracle.cleanup_attempt(
                        fixture_cleanup,
                        "cache_contrasts_removed",
                        lambda: oracle.delete_cache_contrasts(
                            raw, fixture["contrasts"]
                        ),
                    )
                    oracle.cleanup_attempt(
                        fixture_cleanup,
                        "managed_state_restored",
                        lambda: oracle.snapshot_models(apps) == original_state,
                    )
                    if fixture_cleanup["results"].get("managed_state_restored"):
                        report["managed_state"] = oracle.state_receipt(
                            oracle.snapshot_models(apps)
                        )
                    report["fixture_cleanup"] = fixture_cleanup
                    for result in fixture_cleanup["results"].values():
                        if not result:
                            report["cleanup_errors"].append(
                                "fixture cleanup verification failed"
                            )
                except BaseException as error:
                    _record_error(report, "runtime", error)
                    # Keep full diagnostics private; the publishable receipt has
                    # only type/stage and a redacted summary.
                    if log is not None:
                        log.write(traceback.format_exc())
                        log.flush()
                finally:
                    if raw is not None:
                        try:
                            raw.close()
                        except BaseException as error:
                            report["cleanup_errors"].append(
                                "redis_close: " + type(error).__name__
                            )
                    if queries is not None:
                        try:
                            queries.close()
                        except BaseException as error:
                            report["cleanup_errors"].append(
                                "query_trace_close: " + type(error).__name__
                            )
                    try:
                        connections.close_all()
                    except BaseException as error:
                        report["cleanup_errors"].append(
                            "database_connections: " + type(error).__name__
                        )
                    override.disable()
    except BaseException as error:
        _record_error(report, "orchestration", error)
        if log is not None and not log.closed:
            log.write(traceback.format_exc())
            log.flush()
    finally:
        try:
            source_stable(report)
        except BaseException as error:
            report["source_stable"] = False
            report["cleanup_errors"].append(
                "source_stability: " + type(error).__name__
            )
        report["cleanup_verified"] = cleanup_complete(report)
        try:
            validate_workload_order(report["workloads"], args.versioned)
            order_valid = True
        except AssertionError as error:
            order_valid = False
            _record_error(report, "workload-order", error)
        report["workload_order_valid"] = order_valid
        if log is not None and not log.closed:
            try:
                log.close()
            except BaseException as error:
                _record_error(report, "private-log-close", error)
        report["success"] = report["acceptance_gate"] = runtime_acceptance(report, expected_cases)
        oracle.write_publishable(result_path, report, private_values)
    if report["success"]:
        print(json.dumps({
            "success": True,
            "output": str(result_path),
            "versioned": args.versioned,
            "workloads": len(report["workloads"]),
            "runtime_executed": True,
        }))
        return 0
    print(json.dumps({
        "success": False,
        "output": str(result_path),
        "runtime_executed": report.get("runtime_executed", False),
        "errors": len(report.get("errors", []))
        + len(report.get("cleanup_errors", [])),
    }), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
