"""Private fixtures and observations for communications message reads.

The module seeds only synthetic rows and never turns observed responses into
expected responses. Programmatic ``run()`` accepts caller-owned disposable
state; its ``--run`` CLI entry point delegates to the standalone runtime.
"""
import argparse
import ast
from contextvars import ContextVar
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from uuid import UUID


ROOT = Path(__file__).resolve().parents[3]
SOURCE_FIXTURE = ROOT / "TESTS/fixtures/message_reads.json"
MATRIX_VERSION = 1

MESSAGE_SEED_RECORDS = (
    {
        "number": 702001,
        "association": "own",
        "type": "EMAIL",
        "message": """<p>Prova <strong>A & B</strong> "quoted" 'single'</p>""",
        "subject": """  Quote & attività <2026>  """,
        "created_at": datetime(2026, 10, 23, 8, 9, 10, 123456, tzinfo=timezone.utc),
    },
    {
        "number": 702002,
        "association": "own",
        "type": "INSIDE_APP",
        "message": "Post senza data",
        "subject": None,
        "created_at": None,
    },
    {
        "number": 702003,
        "association": "own",
        "type": "EMAIL",
        "message": "Avviso più vecchio",
        "subject": "old",
        "created_at": datetime(2026, 10, 23, 7, 0, 0, tzinfo=timezone.utc),
    },
    {
        "number": 702004,
        "association": "foreign",
        "type": "EMAIL",
        "message": "Foreign private body",
        "subject": "foreign",
        "created_at": datetime(2026, 10, 23, 6, 0, 0, tzinfo=timezone.utc),
    },
    {
        "number": 702005,
        "association": "own",
        "type": "SMS",
        "message": "Unsupported private body",
        "subject": "unsupported",
        "created_at": datetime(2026, 10, 23, 5, 0, 0, tzinfo=timezone.utc),
    },
    {
        "number": 702006,
        "association": "own",
        "type": "EMAIL",
        "message": "No delivery history",
        "subject": "empty",
        "created_at": datetime(2026, 10, 23, 4, 0, 0, tzinfo=timezone.utc),
    },
)

TRANSACTION_SEED_RECORDS = (
    {
        "number": 703001,
        "message": 702001,
        "recipient": "first@example.test",
        "sent_on": datetime(2026, 10, 23, 12, 0, 0, 123456, tzinfo=timezone.utc),
        "delivered_on": datetime(2026, 10, 23, 12, 0, 1, 123456, tzinfo=timezone.utc),
        "opened_on": datetime(2026, 10, 23, 12, 1, 0, 1, tzinfo=timezone.utc),
        "clicked_on": None,
        "bounced_on": None,
        "bounced_reason": None,
        "bounced_type": None,
        "bounced_status": None,
    },
    {
        "number": 703002,
        "message": 702001,
        "recipient": "second@example.test",
        "sent_on": datetime(2026, 10, 23, 12, 0, 0, 123456, tzinfo=timezone.utc),
        "delivered_on": None,
        "opened_on": None,
        "clicked_on": None,
        "bounced_on": None,
        "bounced_reason": None,
        "bounced_type": None,
        "bounced_status": None,
    },
    {
        "number": 703003,
        "message": 702001,
        "recipient": "bounced@example.test",
        "sent_on": datetime(2026, 10, 23, 11, 0, 0, tzinfo=timezone.utc),
        "delivered_on": None,
        "opened_on": None,
        "clicked_on": None,
        "bounced_on": datetime(2026, 10, 23, 11, 0, 2, tzinfo=timezone.utc),
        "bounced_reason": "Mailbox full",
        "bounced_type": "permanent",
        "bounced_status": "5.2.2",
    },
    {
        "number": 703004,
        "message": 702004,
        "recipient": "foreign@example.test",
        "sent_on": datetime(2026, 10, 23, 10, 30, 0, tzinfo=timezone.utc),
        "delivered_on": None,
        "opened_on": None,
        "clicked_on": None,
        "bounced_on": None,
        "bounced_reason": None,
        "bounced_type": None,
        "bounced_status": None,
    },
    {
        "number": 703005,
        "message": 702003,
        "recipient": "older@example.test",
        "sent_on": datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc),
        "delivered_on": None,
        "opened_on": None,
        "clicked_on": None,
        "bounced_on": None,
        "bounced_reason": None,
        "bounced_type": None,
        "bounced_status": None,
    },
    {
        "number": 703007,
        "message": 702005,
        "recipient": "unsupported@example.test",
        "sent_on": datetime(2026, 10, 23, 9, 0, 0, tzinfo=timezone.utc),
        "delivered_on": None,
        "opened_on": None,
        "clicked_on": None,
        "bounced_on": None,
        "bounced_reason": None,
        "bounced_type": None,
        "bounced_status": None,
    },
)

MESSAGE_SEED_FIELDS = frozenset(
    {"number", "association", "type", "message", "subject", "created_at"}
)
TRANSACTION_SEED_FIELDS = frozenset(
    {
        "number",
        "message",
        "recipient",
        "sent_on",
        "delivered_on",
        "opened_on",
        "clicked_on",
        "bounced_on",
        "bounced_reason",
        "bounced_type",
        "bounced_status",
    }
)


class AttributionError(AssertionError):
    """A response cannot be attributed to the intended backend."""


class AcceptanceStateError(AssertionError):
    """A read oracle changed complete durable or cache state."""


def _uid(number):
    return f"a1000000-0000-4000-8000-{number:012d}"


def _stable(value):
    """Preserve JSON scalar types while making all model values deterministic."""
    if isinstance(value, datetime):
        return {
            "type": "datetime",
            "value": value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    if isinstance(value, UUID):
        return {"type": "uuid", "value": str(value)}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, dict):
        return {str(key): _stable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def digest(value):
    payload = json.dumps(
        _stable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def django_transaction_fields(root=ROOT):
    """Read MessageTransaction concrete assignment names without importing Django."""
    source = Path(root, "BE/communications/models.py")
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    model = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MessageTransaction"
    )
    fields = set()
    for node in model.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "models"
        ):
            fields.add(node.targets[0].id)
    assert len(fields) == 11, f"MessageTransaction model fields differ: {sorted(fields)}"
    return fields


def transaction_model_values(record, message):
    """Translate one named seed record to exactly the model's 11 field values."""
    assert set(record) == TRANSACTION_SEED_FIELDS, (
        "transaction seed record fields differ",
        sorted(set(record) ^ TRANSACTION_SEED_FIELDS),
    )
    values = {
        (
            "message_transaction_id"
            if key == "number"
            else key
        ): value
        for key, value in record.items()
        if key != "message"
    }
    values["message"] = message
    assert set(values) == django_transaction_fields(), (
        "seed translation does not cover MessageTransaction fields",
    )
    return values


def validate_seed_records(
    messages=MESSAGE_SEED_RECORDS, transactions=TRANSACTION_SEED_RECORDS
):
    """Validate every named record before any database mutation."""
    message_numbers = set()
    for record in messages:
        assert set(record) == MESSAGE_SEED_FIELDS, (
            "message seed record fields differ",
            sorted(set(record) ^ MESSAGE_SEED_FIELDS),
        )
        number = record["number"]
        assert type(number) is int and number not in message_numbers
        assert record["association"] in {"own", "foreign"}
        assert record["type"] in {"EMAIL", "INSIDE_APP", "SMS"}
        assert isinstance(record["message"], str)
        assert record["subject"] is None or isinstance(record["subject"], str)
        assert record["created_at"] is None or isinstance(record["created_at"], datetime)
        message_numbers.add(number)
    assert len(message_numbers) == 6
    transaction_numbers = set()
    for record in transactions:
        assert set(record) == TRANSACTION_SEED_FIELDS, (
            "transaction seed record fields differ",
            sorted(set(record) ^ TRANSACTION_SEED_FIELDS),
        )
        number = record["number"]
        assert type(number) is int and number not in transaction_numbers
        assert record["message"] in message_numbers
        assert isinstance(record["recipient"], str) and record["recipient"]
        assert isinstance(record["sent_on"], datetime)
        for name in ("delivered_on", "opened_on", "clicked_on", "bounced_on"):
            assert record[name] is None or isinstance(record[name], datetime)
        for name in ("bounced_reason", "bounced_type", "bounced_status"):
            assert record[name] is None or isinstance(record[name], str)
        transaction_numbers.add(number)
    assert 703007 in transaction_numbers
    assert not any(
        record["message"] == 702006 for record in transactions
    ), "empty-delivery message must remain visible without a transaction"
    return {"messages": len(messages), "transactions": len(transactions)}


def load_authored_manifest(path=SOURCE_FIXTURE):
    """Validate the authored portable input without deriving observed responses."""
    fixture = json.loads(Path(path).read_text())
    assert fixture.get("matrix_version") == MATRIX_VERSION
    cases = fixture.get("cases")
    assert isinstance(cases, list) and cases
    names = [case.get("name") for case in cases]
    assert all(isinstance(name, str) and name for name in names)
    assert len(names) == len(set(names))
    from TESTS.contracts.test_message_reads import REQUIRED_NAMES
    assert set(names) == REQUIRED_NAMES, "Version-one matrix cases differ"
    for case in cases:
        assert "response" in case or case.get("method") == "HEAD"
        assert set(case) <= {
            "actor",
            "body",
            "headers",
            "language",
            "method",
            "name",
            "path",
            "response",
            "response_contains",
            "status",
        }
    lists = [
        case for case in cases if case["name"] in {
            "message-reads-list-owner",
            "message-reads-list-query-and-group-ignored",
            "message-reads-list-full-collaborator",
        }
    ]
    assert len(lists) == 3
    assert all(len(case["response"]) == 4 for case in lists)
    detail = next(
        case for case in cases if case["name"] == "message-reads-detail-empty"
    )
    assert detail["status"] == 404
    assert fixture["ids"]["unsupported_type_transaction"] == _uid(703007)
    return fixture


def runtime_manifest(authored, tokens):
    """Copy authored expectations and replace repeated actor labels with tokens."""
    fixture = copy.deepcopy(authored)
    used = set()
    for case in fixture["cases"]:
        actor = case.pop("actor", None)
        if actor is None:
            assert "token" not in case
            continue
        assert actor in tokens, f"fixture actor has no runtime token: {actor}"
        case["token"] = tokens[actor]
        used.add(actor)
    assert used == set(tokens), (sorted(used), sorted(tokens))
    return fixture


def snapshot_models(apps):
    """Snapshot every managed concrete model, including join/through tables."""
    result = {}
    for model in sorted(
        apps.get_models(include_auto_created=True), key=lambda item: item._meta.label
    ):
        if not model._meta.managed or model._meta.proxy:
            continue
        result[model._meta.label] = [
            {key: _stable(value) for key, value in row.items()}
            for row in model._base_manager.order_by("pk").values()
        ]
    assert result, "managed model snapshot is empty"
    return result


def state_receipt(snapshot):
    assert isinstance(snapshot, dict) and snapshot
    return {
        "models": len(snapshot),
        "rows": sum(len(rows) for rows in snapshot.values()),
        "sha256": digest(snapshot),
    }


def assert_state_unchanged(before, after):
    before_digest = digest(before)
    after_digest = digest(after)
    if before_digest != after_digest:
        changed = sorted(
            label
            for label in set(before) | set(after)
            if digest(before.get(label)) != digest(after.get(label))
        )
        raise AcceptanceStateError(
            "read changed complete managed state for model(s): "
            + ", ".join(changed[:8])
        )
    return state_receipt(after)


def assert_cache_unchanged(before, after, contrasts):
    from TESTS.adapters.django.historical_read_cache import (
        compare_historical_read_cache,
    )

    try:
        return compare_historical_read_cache(before, after, contrasts)
    except AssertionError as error:
        raise AcceptanceStateError(str(error)) from error


def assert_query_evidence(case, query, backend="django"):
    """Return the explicitly expected message-family query counts for a case."""
    name = case["name"]
    assert backend in {"django", "go"}
    path = case["path"].split("?", 1)[0]
    expected = {"messages": 0, "transactions": 0, "writes": 0}
    if name in {
        "message-reads-list-owner",
        "message-reads-list-query-and-group-ignored",
        "message-reads-list-full-collaborator",
    }:
        expected["messages"] = 1
    elif path.endswith("/detail"):
        if name in {
            "message-reads-detail-owner",
            "message-reads-detail-empty",
            "message-reads-detail-uppercase-uuid",
        }:
            expected.update(messages=1, transactions=1)
        elif name in {
            "message-reads-detail-foreign",
            "message-reads-detail-unsupported-type",
        }:
            expected["messages"] = 1
    if backend == "go" and name in {
        "message-reads-detail-foreign", "message-reads-detail-unsupported-type",
    }:
        # The Go repository reads a scoped join even when it returns no rows.
        expected.update(messages=1, transactions=1)
    for key, value in expected.items():
        assert query.get(key) == value, (
            f"{name} {key} query count differs: expected {value}, got {query.get(key)}"
        )
    if "total" in query:
        assert all(
            type(query[key]) is int and query[key] >= 0 for key in query
        ), f"{name} query receipt is malformed: {query}"
        minimum = (max(expected["messages"], expected["transactions"])
                   if backend == "go" else expected["messages"] + expected["transactions"])
        assert query["total"] >= minimum
    return expected


class QueryTrace:
    """Count SQL in the Django ASGI request context, one correlation per case."""

    def __init__(self):
        from django.db import connections
        from django.db.backends.signals import connection_created

        self.correlation = ContextVar("message_reads_query_correlation", default=None)
        self.lock = threading.Lock()
        self.counts = {}
        self.connection_created = connection_created
        self.attached = []
        for connection in connections.all():
            self._attach(connection)
        connection_created.connect(self._connected, weak=False)

    def _attach(self, connection):
        if self._trace not in connection.execute_wrappers:
            connection.execute_wrappers.append(self._trace)
        self.attached.append(connection)

    def _connected(self, sender, connection, **kwargs):
        del sender, kwargs
        self._attach(connection)

    def _trace(self, execute, sql, params, many, context):
        cid = self.correlation.get()
        if cid:
            upper = sql.lstrip().upper()
            with self.lock:
                count = self.counts[cid]
                count["total"] += 1
                count["association"] += int(
                    'FROM "APPLICATION_SPORTASSOCIATION"' in upper
                )
                count["messages"] += int(
                    'FROM "COMMUNICATIONS_MESSAGE"' in upper
                )
                count["transactions"] += int(
                    'FROM "COMMUNICATIONS_MESSAGETRANSACTION"' in upper
                )
                count["writes"] += int(
                    upper.startswith(("INSERT ", "UPDATE ", "DELETE "))
                )
        return execute(sql, params, many, context)

    def expect(self, cid):
        assert cid and cid not in self.counts, f"duplicate query correlation: {cid}"
        with self.lock:
            self.counts[cid] = {
                "total": 0,
                "association": 0,
                "messages": 0,
                "transactions": 0,
                "writes": 0,
            }
        return cid

    def begin(self, cid):
        if cid not in self.counts:
            self.expect(cid)
        return self.correlation.set(cid)

    def end(self, token):
        self.correlation.reset(token)

    def delta(self, cid):
        with self.lock:
            return dict(self.counts[cid])

    def close(self):
        self.connection_created.disconnect(self._connected)
        for connection in self.attached:
            while self._trace in connection.execute_wrappers:
                connection.execute_wrappers.remove(self._trace)
        self.attached.clear()


class AsgiObserver:
    """Record every HTTP request, including altered markers and unexpected routes."""

    def __init__(self, application, queries=None):
        self.application = application
        self.queries = queries
        self.events = []
        self.lock = threading.Lock()

    def cursor(self):
        with self.lock:
            return len(self.events)

    def delta(self, cursor):
        with self.lock:
            return copy.deepcopy(self.events[cursor:])

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.application(scope, receive, send)
            return
        headers = {
            name.decode("latin1").lower(): value.decode("latin1")
            for name, value in scope.get("headers", [])
        }
        cid = headers.get("x-correlation-id", "")
        with self.lock:
            index = len(self.events)
            self.events.append({
                "seq": index,
                "method": scope.get("method"),
                "path": scope.get("path"),
                "cid": cid,
                "status": None,
                "error_type": None,
            })

        async def observed_send(message):
            if message.get("type") == "http.response.start":
                with self.lock:
                    self.events[index]["status"] = message.get("status")
            await send(message)

        query_token = self.queries.begin(cid) if self.queries and cid else None
        try:
            await self.application(scope, receive, observed_send)
        except BaseException as error:
            with self.lock:
                self.events[index]["error_type"] = type(error).__name__
            raise
        finally:
            if query_token is not None:
                self.queries.end(query_token)


def assert_attribution(backend, method, path, status, observed, cid):
    """Assert the complete ASGI delta for native Django, Go, v1 and v2."""
    assert backend in {"django", "go"}
    upstream_path = path.split("?", 1)[0]
    if backend == "django":
        expected = [{
            "seq": observed[0]["seq"] if observed else 0,
            "method": method,
            "path": upstream_path,
            "cid": cid,
            "status": status,
            "error_type": None,
        }]
    else:
        expected = []
    if observed != expected:
        raise AttributionError(
            f"complete ASGI delta differs for {backend} {method} {upstream_path}: "
            f"expected {expected!r}, observed {observed!r}"
        )
    return expected


def cleanup_attempt(receipt, name, operation):
    """Record cleanup failure and continue to the next owned cleanup."""
    try:
        result = operation()
        if result is False:
            raise AssertionError(f"{name}: verification returned False")
    except BaseException as error:
        receipt.setdefault("results", {})[name] = False
        receipt.setdefault("errors", []).append(
            f"{name}: {type(error).__name__}: {error}"
        )
        return False
    receipt.setdefault("results", {})[name] = True
    return True


def redact_private(value, secrets):
    """Return a publishable structure with exact private values replaced."""
    ordered = sorted(
        (str(secret) for secret in secrets if secret), key=len, reverse=True
    )
    if isinstance(value, dict):
        return {key: redact_private(item, ordered) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_private(item, ordered) for item in value]
    if isinstance(value, str):
        for secret in ordered:
            if secret in value:
                value = value.replace(secret, "<redacted fixture secret>")
        return value
    return value


def assert_no_private_values(value, secrets):
    ordered = [str(secret) for secret in secrets if secret]
    if isinstance(value, dict):
        for item in value.values():
            assert_no_private_values(item, ordered)
    elif isinstance(value, list):
        for item in value:
            assert_no_private_values(item, ordered)
    elif isinstance(value, str):
        leaked = [secret for secret in ordered if secret in value]
        if leaked:
            raise AssertionError(
                f"publishable receipt contains {len(leaked)} private value(s)"
            )


def write_publishable(path, value, secrets):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    publishable = redact_private(value, secrets)
    assert_no_private_values(publishable, secrets)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(publishable, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    os.replace(temporary, path)
    return publishable


def metadata():
    """Inspect the Django route/serializer contract without opening a service."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TESTS.adapters.django.settings")
    os.environ.setdefault("DEBUG", "False")
    os.environ.setdefault("CURRENT_HOST", "http://127.0.0.1:9")
    os.environ.setdefault("CURRENT_HOST_PUPPETEER", "http://127.0.0.1:9")
    os.environ.setdefault("TESTS_DATABASE", "assozeta_contract_metadata")
    os.environ.setdefault("TESTS_WORKDIR", "/tmp/assozeta-message-reads-metadata")
    os.environ.setdefault("DBTYPE", "POSTGRES")
    os.environ.setdefault("DBHOST", "/tmp/message-reads-metadata-no-connect")
    os.environ.setdefault("DBPORT", "5432")
    os.environ.setdefault("DBNAME", "assozeta_contract_metadata")
    os.environ.setdefault("DBUSER", "contracts")
    os.environ.setdefault("DBPASSWORD", "metadata-only")
    os.environ.setdefault("USE_PGBOUNCER", "False")
    os.environ.setdefault("STORE_ON_DISK", "True")
    sys.path[:0] = [str(ROOT / "BE"), str(ROOT)]

    import environ
    environ.Env.read_env(str(ROOT / "selfhost/.env.example"))
    os.environ.setdefault("SECRET_KEY", "message-reads-metadata-only")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    signing_key = Ed25519PrivateKey.generate()
    os.environ.update(
        JWT_SECRET_KEY_B64="",
        JWT_PUBLIC_KEY_B64="",
        JWT_SECRET_KEY=signing_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        JWT_PUBLIC_KEY=signing_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode(),
    )
    import django
    import logging
    logging.disable(logging.CRITICAL)
    try:
        django.setup()
    finally:
        logging.disable(logging.NOTSET)

    from django.urls import resolve
    from communications.serializers import MessageSerializer, MessageTransactionSerializer

    routes = {}
    for path in (
        "/communications/messages/list",
        f"/communications/messages/{_uid(702001)}/detail",
    ):
        match = resolve(path)
        routes[path] = {
            "view": match.func.__name__,
            "methods": sorted(getattr(match.func.cls, "http_method_names", [])),
        }
    return {
        "routes": routes,
        "message_fields": list(MessageSerializer().fields),
        "transaction_fields": list(MessageTransactionSerializer().fields),
        "attachments": [],
        "pagination": None,
        "filters": [],
    }


def seed_fixture(apps, private_work, raw):
    """Seed only private synthetic identities, messages and delivery rows."""
    from application.models import Associate, SportAssociation, User
    from auditlog.context import disable_auditlog
    from communications.models import Message, MessageTransaction
    from django.db import transaction
    from rest_framework_simplejwt.tokens import AccessToken

    counts = validate_seed_records()
    private_work.mkdir(parents=True, exist_ok=True)
    created = []

    def seed(model, identifier, **values):
        identifier = _uid(identifier)
        assert not model._base_manager.filter(pk=identifier).exists(), (
            "fixture collision",
            model._meta.label,
            identifier,
        )
        row = model(pk=identifier, **values)
        model._base_manager.bulk_create([row])
        created.append((model, identifier))
        return row

    def user(identifier, role, **values):
        return seed(
            User,
            identifier,
            username=f"message-reads-{identifier}",
            email=f"message-reads-{identifier}@example.test",
            password="!synthetic",
            role=role,
            first_name="Message",
            last_name="Fixture",
            **values,
        )

    def token(person):
        value = AccessToken()
        value["user_id"] = str(person.pk)
        return str(value)

    with disable_auditlog(), transaction.atomic():
        owner = user(700001, User.ASSOCIATION)
        full = user(
            700002,
            User.COLLABORATOR,
            connected_user=owner,
            collaborator_role=User.FULL,
        )
        restricted = user(
            700003,
            User.COLLABORATOR,
            connected_user=owner,
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=["association.calendar.read"],
        )
        athlete = user(700004, User.ATHLETE)
        no_association = user(700005, User.ASSOCIATION)
        deleted_owner = user(700006, User.ASSOCIATION)
        foreign_owner = user(700007, User.ASSOCIATION)
        own = seed(
            SportAssociation,
            701001,
            user=owner,
            denomination="Message reads own",
            tax_code="70000000001",
        )
        foreign = seed(
            SportAssociation,
            701002,
            user=foreign_owner,
            denomination="Message reads foreign",
            tax_code="70000000002",
        )
        seed(
            SportAssociation,
            701003,
            user=deleted_owner,
            denomination="Message reads deleted",
            tax_code="70000000003",
            deleted=True,
        )
        seed(
            Associate,
            700101,
            user=athlete,
            sport_association=own,
            first_name="Athlete",
            last_name="Fixture",
        )
        messages = {}
        for record in MESSAGE_SEED_RECORDS:
            association = own if record["association"] == "own" else foreign
            row = seed(
                Message,
                record["number"],
                sport_association=association,
                type=record["type"],
                message=record["message"],
                subject=record["subject"],
            )
            Message._base_manager.filter(pk=row.pk).update(
                created_at=record["created_at"]
            )
            messages[record["number"]] = row
        for record in TRANSACTION_SEED_RECORDS:
            row = seed(
                MessageTransaction,
                record["number"],
                **transaction_model_values(record, messages[record["message"]]),
            )
            MessageTransaction._base_manager.filter(pk=row.pk).update(
                sent_on=record["sent_on"]
            )

    tokens = {
        "owner": token(owner),
        "full_collaborator": token(full),
        "restricted_collaborator": token(restricted),
        "athlete": token(athlete),
        "owner_without_association": token(no_association),
        "deleted_association_owner": token(deleted_owner),
    }
    authored = load_authored_manifest()
    converted = runtime_manifest(authored, tokens)
    runtime_path = private_work / "message_reads-runtime.json"
    runtime_path.write_text(
        json.dumps(converted, indent=2, ensure_ascii=False) + "\n"
    )
    runtime_path.chmod(0o600)

    from TESTS.adapters.django.historical_read_cache import (
        seed_historical_read_cache_contrasts,
        snapshot_historical_read_cache,
    )

    contrasts = seed_historical_read_cache_contrasts(raw)
    baseline_cache = snapshot_historical_read_cache(raw)
    return {
        "created": created,
        "messages": messages,
        "tokens": tokens,
        "authored": authored,
        "runtime": converted,
        "runtime_path": runtime_path,
        "contrasts": contrasts,
        "baseline_state": snapshot_models(apps),
        "baseline_cache": baseline_cache,
        "seed_counts": counts,
    }


def delete_created(fixture, extra_created=()):
    from auditlog.context import disable_auditlog

    with disable_auditlog():
        for model, identifier in reversed([*fixture["created"], *extra_created]):
            model._base_manager.filter(pk=identifier).delete()


def delete_cache_contrasts(raw, contrasts):
    raw.delete(contrasts.persistent_key, contrasts.expiring_key)


def assert_fixture_baseline(apps, fixture, raw):
    """Backend startup and gaps between reads must preserve the seeded oracle."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache

    assert_state_unchanged(fixture["baseline_state"], snapshot_models(apps))
    assert_cache_unchanged(
        fixture["baseline_cache"], snapshot_historical_read_cache(raw), fixture["contrasts"]
    )


def run_portable_workload(
    *, label, backend, base_url, fixture, apps, observer, queries, raw, private_log,
    postgres_queries=None,
):
    """Run the actual authored module against one backend with full observation."""
    from TESTS.adapters.django.historical_read_cache import (
        snapshot_historical_read_cache,
    )
    from TESTS.contracts import test_message_reads as portable

    assert backend != "go" or postgres_queries is not None, (
        "Go acceptance requires an owned PostgreSQL query observer"
    )
    assert_fixture_baseline(apps, fixture, raw)
    evidence = []
    counter = {"value": 0}
    original_client = portable.Client

    class ObservedClient(original_client):
        def request(self, method, path, data=None, headers=None, **kwargs):
            counter["value"] += 1
            supplied = dict(headers or {})
            case_name = supplied.get(
                "X-Correlation-ID", f"request-{counter['value']}"
            )
            cid = f"{label}-{counter['value']}-{case_name}"
            supplied["X-Correlation-ID"] = cid
            before_state = snapshot_models(apps)
            before_cache = snapshot_historical_read_cache(raw)
            assert_state_unchanged(fixture["baseline_state"], before_state)
            assert_cache_unchanged(fixture["baseline_cache"], before_cache, fixture["contrasts"])
            cursor = observer.cursor()
            queries.expect(cid)
            mark = postgres_queries.mark() if backend == "go" else None
            response = None
            query = None
            assertion_error = None
            try:
                response = super().request(method, path, data, supplied, **kwargs)
                query = (postgres_queries.since(mark) if backend == "go"
                         else queries.delta(cid))
                after_state = snapshot_models(apps)
                after_cache = snapshot_historical_read_cache(raw)
                observed = observer.delta(cursor)
                state = assert_state_unchanged(before_state, after_state)
                cache = assert_cache_unchanged(
                    before_cache, after_cache, fixture["contrasts"]
                )
                attribution = assert_attribution(
                    backend, method, path, response[0], observed, cid
                )
                assert_query_evidence({
                    "name": case_name,
                    "status": response[0],
                    "path": path,
                }, query, backend=backend)
                evidence.append({
                    "case": case_name,
                    "backend": backend,
                    "method": method,
                    "path": path.split("?", 1)[0],
                    "status": response[0],
                    "asgi_events": len(observed),
                    "django_attribution": len(attribution),
                    "query": query,
                    "state": state,
                    "cache": cache,
                })
            except BaseException as error:
                assertion_error = error
                after_state = snapshot_models(apps)
                after_cache = snapshot_historical_read_cache(raw)
                observed = observer.delta(cursor)
                # Preserve the request's receipt, without counting later state
                # snapshots as application SQL on an assertion failure.
                evidence.append({
                    "case": case_name,
                    "backend": backend,
                    "method": method,
                    "path": path.split("?", 1)[0],
                    "status": response[0] if response else None,
                    "asgi_events": len(observed),
                    "django_attribution": None,
                    "query": query,
                    "error_type": type(error).__name__,
                    "state_unchanged": before_state == after_state,
                    "cache_unchanged": before_cache == after_cache,
                })
            if assertion_error is not None:
                raise assertion_error
            return response

    diagnostics = io.StringIO()
    result = None
    try:
        with patch.dict(os.environ, {
            "TESTS_BASE_URL": base_url,
            "TESTS_FIXTURE": str(fixture["runtime_path"]),
        }), patch.object(portable, "Client", ObservedClient):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(
                portable.MessageReadsContract
            )
            result = unittest.TextTestRunner(
                stream=diagnostics, verbosity=0
            ).run(suite)
    finally:
        expected = [
            case["name"] for case in fixture["runtime"]["cases"]
        ]
        observed_names = [row["case"] for row in evidence]
        complete = observed_names == expected
        success = bool(
            result is not None
            and result.wasSuccessful()
            and not result.skipped
            and complete
            and len(evidence) == len(expected)
        )
        if not success:
            private_log.write(f"Message reads portable workload: {label}\n")
            private_log.write(diagnostics.getvalue())
            private_log.write(
                f"Observed case sequence: {observed_names!r}\n"
                f"Expected case sequence: {expected!r}\n"
            )
            private_log.flush()
    return {
        "label": label,
        "backend": backend,
        "transport": "caddy" if "/api/v" in base_url else "native",
        "tests": result.testsRun if result else 0,
        "assertion_cases": len(fixture["runtime"]["cases"]),
        "observed_requests": len(evidence),
        "success": success,
        "failed_tests": (
            [test.id() for test, _ in result.failures + result.errors]
            if result else ["suite-load"]
        ),
        "complete_case_sequence": complete,
        "requests": evidence,
    }


def run(work, log, tool_env, address, *, go_server_factory=None, output=None,
        postgres_queries=None):
    """Run with caller-owned PostgreSQL/Redis; ``address`` is Redis host:port."""
    import redis
    import uvicorn
    from django.apps import apps
    from django.core.asgi import get_asgi_application
    from django.db import connections
    from TESTS.adapters.django.historical_read_cache import (
        snapshot_historical_read_cache,
    )
    from TESTS.http_client import poll

    assert postgres_queries is not None, (
        "Caller-owned acceptance requires an owned PostgreSQL query observer"
    )
    output = Path(output or ROOT / "quality-reports/go-migration/message-reads")
    private = Path(work) / "message-reads-private"
    private.mkdir(parents=True, exist_ok=True)
    result_path = output / "results.json"
    raw = redis.Redis(
        host=address.rsplit(":", 1)[0],
        port=int(address.rsplit(":", 1)[1]),
        db=1,
        password="synthetic-identity-only",
        decode_responses=False,
    )
    original = snapshot_models(apps)
    queries = QueryTrace()
    observer = AsgiObserver(get_asgi_application(), queries)
    server = uvicorn.Server(uvicorn.Config(
        observer,
        host="127.0.0.1",
        port=0,
        lifespan="off",
        log_level="critical",
        access_log=False,
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    fixture = None
    report = {
        "success": False,
        "scope": "communications message list/detail reads",
        "workloads": [],
        "complete_message_family_parity": False,
    }
    try:
        fixture = seed_fixture(apps, private, raw)
        thread.start()
        poll(lambda: server.started and thread.is_alive(), bool, timeout=20)
        port = server.servers[0].sockets[0].getsockname()[1]
        report["workloads"].append(run_portable_workload(
            label="django-native",
            backend="django",
            base_url=f"http://127.0.0.1:{port}",
            fixture=fixture,
            apps=apps,
            observer=observer,
            queries=queries,
            raw=raw,
            private_log=log,
        ))
        if tool_env.get("TESTS_MESSAGE_READS_GO") == "1":
            factory = go_server_factory
            if factory is None:
                from TESTS.adapters.go.payment_category_server import native_server
                factory = native_server
            with factory(work, log, tool_env, address) as go_runtime:
                report["workloads"].append(run_portable_workload(
                    label="go-native",
                    backend="go",
                    base_url=go_runtime["base_url"],
                    fixture=fixture,
                    apps=apps,
                    observer=observer,
                    queries=queries,
                    raw=raw,
                    private_log=log,
                    postgres_queries=postgres_queries,
                ))
        report["cache"] = assert_cache_unchanged(
            fixture["baseline_cache"],
            snapshot_historical_read_cache(raw),
            fixture["contrasts"],
        )
        report["success"] = bool(
            report["workloads"]
            and all(row["success"] for row in report["workloads"])
        )
    finally:
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=20)
        cleanup = {"results": {}, "errors": []}
        cleanup_attempt(cleanup, "django_stopped", lambda: not thread.is_alive())
        cleanup_attempt(cleanup, "query_trace_detached", queries.close)
        if fixture is not None:
            cleanup_attempt(
                cleanup,
                "runtime_manifest_removed",
                lambda: not fixture["runtime_path"].exists()
                or fixture["runtime_path"].unlink() is None,
            )
            cleanup_attempt(
                cleanup,
                "fixture_rows_removed",
                lambda: delete_created(fixture),
            )
            cleanup_attempt(
                cleanup,
                "cache_contrasts_removed",
                lambda: delete_cache_contrasts(raw, fixture["contrasts"]),
            )
        cleanup_attempt(cleanup, "cache_client_closed", raw.close)
        connections.close_all()
        cleanup_attempt(
            cleanup,
            "managed_state_restored",
            lambda: snapshot_models(apps) == original,
        )
        if cleanup["results"].get("managed_state_restored"):
            report["managed_state"] = state_receipt(original)
        report["cleanup"] = cleanup
        report["cleanup_verified"] = bool(
            not cleanup["errors"]
            and all(cleanup["results"].values())
        )
        report["success"] = bool(report["success"] and report["cleanup_verified"])
        write_publishable(
            result_path,
            report,
            fixture["tokens"].values() if fixture else (),
        )
    assert report["success"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--versioned", action="store_true")
    options = parser.parse_args()
    if options.metadata:
        print(json.dumps(metadata(), indent=2, sort_keys=True))
        return
    if not options.run:
        parser.error(
            "choose --metadata or --run; final acceptance uses "
            "message_reads_runtime.py --output"
        )
    from TESTS.adapters.django import message_reads_runtime

    arguments = [
        "--output",
        str(
            options.output
            or ROOT / "quality-reports/go-migration/message-reads"
        ),
    ]
    if options.versioned:
        arguments.append("--versioned")
    raise SystemExit(message_reads_runtime.main(arguments))


if __name__ == "__main__":
    main()
