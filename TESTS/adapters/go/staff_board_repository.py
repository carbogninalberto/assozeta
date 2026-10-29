"""Parent-only real PostgreSQL staff-board repository acceptance.

Run with the production interpreter in a fresh process:
``python -m TESTS.adapters.go.staff_board_repository --output NEW_DIRECTORY``.
Reuses the message-read private PostgreSQL/Redis lifecycle and full migration
history, seeds through the Django ORM, runs the Go repository tests against the
same database, then reads and saves the Go-written row through Django. Only the
credential-free results.json is written to the output directory.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

if not __debug__ or sys.flags.optimize:
    print(json.dumps({"success": False, "check": "optimized-python"}))
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from TESTS.adapters.django import message_reads_runtime as lifecycle
from TESTS.adapters.go import message_reads_repository as shared

EXPECTED_TESTS = frozenset({"TestStaffBoardRepositoryListLive", "TestStaffBoardRepositoryMutationsLive"})
BULK = 100
SOURCES = ("BE_GO/internal/repository/staff_board.go", "BE_GO/internal/repository/staff_board_integration_test.go",
           "BE_GO/internal/service/staff_board.go", "BE_GO/internal/domain/staff_board.go",
           "TESTS/adapters/go/staff_board_repository.py", "TESTS/adapters/go/go_operational_migrations.py",
           "BE_GO/migrations/000004_realtime_events.up.sql")
DOCUMENT = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Fissato"}]}]}
GO_DOCUMENT = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Scritto da Go è"}]}]}


def source_hashes():
    return {name: shared.sha256_file(ROOT / name) for name in SOURCES}


def seed():
    """Create two associations, a collaborator and ordered staff-board rows."""
    from uuid import uuid4
    from application.models import SportAssociation, User
    from communications.models import StaffBoardMessage

    def user(role, **extra):
        return User.objects.create(username=f"sb-{uuid4().hex[:12]}", email=f"{uuid4().hex[:12]}@example.invalid",
                                   role=role, is_active=True, **extra)

    owner = user(User.ASSOCIATION)
    association = SportAssociation.objects.create(user=owner, denomination="ASD Staff Board à")
    collaborator = user(User.COLLABORATOR, first_name="Mario", last_name="Rossi", connected_user=owner,
                        collaborator_role=User.FULL)
    foreign_owner = user(User.ASSOCIATION)
    foreign = SportAssociation.objects.create(user=foreign_owner, denomination="Foreign")
    departing = user(User.COLLABORATOR, connected_user=owner, collaborator_role=User.FULL)
    base = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    rows = {}

    def message(label, author, *, minutes, pinned=False, document=None, target=association):
        row = StaffBoardMessage.objects.create(sport_association=target, author=author, content=label,
                                               document=document, pinned=pinned)
        StaffBoardMessage.objects.filter(pk=row.pk).update(created_at=base + timedelta(minutes=minutes))
        rows[label] = str(row.pk)

    message("pinned_old", collaborator, minutes=1, pinned=True, document=DOCUMENT)
    message("new", owner, minutes=50)
    message("deleted_author", departing, minutes=40)
    message("to_delete", owner, minutes=30)
    message("foreign", foreign_owner, minutes=60, target=foreign)
    for index in range(BULK):
        StaffBoardMessage.objects.filter(pk=StaffBoardMessage.objects.create(
            sport_association=association, author=owner, content=f"bulk {index}").pk
        ).update(created_at=base - timedelta(minutes=index + 1))
    departing.delete()  # Real SET_NULL through the ORM.
    return {"owner": str(owner.pk), "association": str(association.pk), "denomination": association.denomination,
            "collaborator": str(collaborator.pk), "foreign_association": str(foreign.pk), "rows": rows, "bulk": BULK}


def snapshot(exclude=()):
    from communications.models import StaffBoardMessage
    return {str(row["staff_board_message_id"]): row for row in StaffBoardMessage.objects.exclude(pk__in=list(exclude))
            .order_by("pk").values()}


def run_go(private, environment, fixture):
    fixture_path = private / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    receipts = private / "receipts"
    receipts.mkdir()
    binary = private / "staff-board-repository.test"
    log_path = private / "go-private.log"
    with log_path.open("w") as log:
        if shared.private_process(["go", "test", "-c", "-race", "-tags=staffboardintegration", "-o", str(binary),
                                   "./internal/repository"], environment=environment, log=log, timeout=240) != 0:
            raise AssertionError("go-build")
        with (private / "go-events.jsonl").open("w") as events:
            code = shared.private_process(["go", "tool", "test2json", "-t", "-p", "repository", str(binary),
                "-test.v", "-test.count=1", "-test.timeout=180s", "-test.run=^TestStaffBoardRepository.*Live$"],
                environment={**environment, "ASSOZETA_STAFF_BOARD_FIXTURE": str(fixture_path),
                             "ASSOZETA_STAFF_BOARD_RECEIPTS": str(receipts)}, log=log, stdout=events, timeout=200)
    events = [json.loads(line) for line in (private / "go-events.jsonl").read_text().splitlines() if line.strip()]
    passed = {event["Test"] for event in events if event.get("Action") == "pass" and "Test" in event}
    if code != 0 or any(event.get("Action") in ("skip", "fail") for event in events) or passed != EXPECTED_TESTS:
        raise AssertionError("go-tests")
    return {"passed": sorted(passed), "binary_sha256": shared.sha256_file(binary)}, \
        json.loads((receipts / "mutations.json").read_text())


def verify_with_django(fixture, receipt, seeded_before):
    """Django must read, render and save the Go-written row; seeds stay intact."""
    from types import SimpleNamespace
    from uuid import UUID
    from communications.models import StaffBoardMessage
    from communications.serializers import StaffBoardMessageSerializer

    written = StaffBoardMessage.objects.select_related("author", "sport_association").get(pk=receipt["go_written"])
    assert str(written.sport_association_id) == fixture["association"], "association"
    assert str(written.author_id) == fixture["collaborator"] and written.content == "Scritto da Go è", "fields"
    assert written.document == GO_DOCUMENT and written.pinned is False, "document/pinned"
    assert written.created_at == datetime.fromisoformat(receipt["created_at"]), "created_at"
    assert written.updated_at == datetime.fromisoformat(receipt["updated_at"]), "updated_at"
    request = SimpleNamespace(user=SimpleNamespace(pk=UUID(fixture["collaborator"])), collaborator=False)
    data = StaffBoardMessageSerializer(written, context={"request": request}).data
    assert data["author_name"] == "Mario Rossi" and data["is_owner"] is True, "serializer"
    assert not StaffBoardMessage.objects.filter(pk=receipt["deleted"]).exists(), "deleted"
    expected = {key: value for key, value in seeded_before.items() if key != receipt["deleted"]}
    assert snapshot(exclude=[receipt["go_written"]]) == expected, "seeded rows changed"
    written.pinned = True
    written.save()  # Django's own write path accepts the Go-written row.
    assert StaffBoardMessage.objects.get(pk=written.pk).pinned is True, "django save"
    return {"django_read": True, "django_rendered": True, "django_saved": True, "deletion_observed": True,
            "seeded_rows_unchanged": True}


def execute(private, report, services, tools):
    with lifecycle.owned_message_database(private, services) as (work, redis_port, log):
        environment, _ = lifecycle.private_environment(work, redis_port, tools)
        lifecycle.apply_private_environment(environment)
        lifecycle.initialize_django_apps()
        from django.db import connections
        from django.test.utils import override_settings
        with override_settings(CACHES=lifecycle.cache_override(redis_port)):
            try:
                connections.close_all()
                schema = lifecycle.migrate_full_schema(log)
                assert schema["mode"] == "full-migration-history" and schema["remaining_plan"] == 0, "schema"
                report["schema"] = schema
                from TESTS.adapters.go import go_operational_migrations
                with (private / "go-migrate.log").open("w") as migrate_log:
                    report["go_operational_schema"] = go_operational_migrations.apply(work, {**environment, **tools}, migrate_log)
                fixture = seed()
                seeded = snapshot()
                go_environment = {**tools, "ASSOZETA_STAFF_BOARD_DSN":
                                  f"host={work} port=5432 user=contracts dbname={lifecycle.DATABASE} sslmode=disable"}
                report["go"], receipt = run_go(private, go_environment, fixture)
                report["django"] = verify_with_django(fixture, receipt, seeded)
                report["bulk_rows"] = BULK
            finally:
                connections.close_all()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = lifecycle.prepare_output_directory(args.output)
    report = {"success": False, "scope": "real PostgreSQL staff-board repository and Django read-back",
              "complete_staff_board_parity": False, "errors": []}
    services = {}
    before = None
    try:
        from TESTS.adapters.django.production_runtime import require_production_python
        report["python"] = require_production_python()
        before = source_hashes()
        report["source_sha256"] = before
        tools = lifecycle.sanitized_tool_environment()
        tools.update({"LC_ALL": "C", "LANG": "C", "GOPROXY": "off", "GOTOOLCHAIN": "local",
                      "GOSUMDB": "off", "GOFLAGS": "-mod=readonly"})
        with patch.dict(os.environ, tools, clear=True):
            with tempfile.TemporaryDirectory(prefix="az-staff-board-private-", dir="/tmp") as directory:
                execute(Path(directory), report, services, tools)
            report["private_output_removed"] = not Path(directory).exists()
    except BaseException as error:  # Fixed code only; no exception text is published.
        report["errors"].append({"stage": "acceptance", "type": type(error).__name__,
                                 "check": str(error) if isinstance(error, AssertionError) else None})
    finally:
        report["source_stable"] = before is not None and source_hashes() == before
        states = services.get("service_cleanup", {})
        report["services_stopped"] = all(states.get(name) == "stopped" for name in ("postgres", "redis"))
        report["success"] = bool(not report["errors"] and report["source_stable"] and report["services_stopped"]
                                 and report.get("private_output_removed") and report.get("go") and report.get("django"))
        (output / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"success": report["success"], "complete_staff_board_parity": False}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
