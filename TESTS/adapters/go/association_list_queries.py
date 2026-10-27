"""Parent-only real PostgreSQL query-count proof for sport-associations/list.

Run with the production interpreter in a fresh process:
``python -m TESTS.adapters.go.association_list_queries --output NEW_DIRECTORY``.
Seeds associations through the Django ORM, each with an onboarding row and a
billing subscription, and measures at two sizes: Go's repository must issue
exactly one query; Django's view queries are counted for comparison. Only the
credential-free results.json is written to the output directory.
"""
import argparse
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

SIZES = (5, 105)
SOURCES = ("BE_GO/internal/repository/association_list.go", "BE_GO/internal/repository/association_list_integration_test.go",
           "BE_GO/internal/domain/association_list.go", "TESTS/adapters/go/association_list_queries.py",
           "BE/application/views/profile_views.py", "BE/application/serializers/auth_serializers.py")


def source_hashes():
    return {name: shared.sha256_file(ROOT / name) for name in SOURCES}


def grow(count):
    """Add owners and associations until `count` active associations exist."""
    from uuid import uuid4
    from application.models import BillingSubscription, SportAssociation, User, UsersOnboarding
    while SportAssociation.objects.count() < count:
        owner = User.objects.create(username=f"al-{uuid4().hex[:12]}", email=f"{uuid4().hex[:12]}@example.invalid",
                                    role=User.ASSOCIATION, is_active=True)
        SportAssociation.objects.create(user=owner, denomination=f"ASD {uuid4().hex[:6]}", tax_code="01234567890")
        UsersOnboarding.objects.create(user=owner)
        BillingSubscription.objects.create(user=owner)


def django_queries():
    """Query count of the real view for a superuser, outside identity resolution."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from application.models import SportAssociation
    from application.serializers.auth_serializers import SportAssociationToolSerializer
    with CaptureQueriesContext(connection) as captured:
        rows = SportAssociationToolSerializer(SportAssociation.objects.all().iterator(chunk_size=100), many=True).data
    return len(rows), len(captured.captured_queries)


def run_go(private, environment, rows):
    fixture = private / f"fixture-{rows}.json"
    fixture.write_text(json.dumps({"rows": rows}))
    receipts = private / f"receipts-{rows}"
    receipts.mkdir()
    binary = private / "association-list.test"
    with (private / "go-private.log").open("a") as log:
        if not binary.exists() and shared.private_process(["go", "test", "-c", "-race", "-tags=associationlistintegration",
                "-o", str(binary), "./internal/repository"], environment=environment, log=log, timeout=240) != 0:
            raise AssertionError("go-build")
        code = shared.private_process([str(binary), "-test.count=1", "-test.timeout=120s", "-test.run=^TestAssociationListQueryCountLive$"],
            environment={**environment, "ASSOZETA_ASSOCIATION_LIST_FIXTURE": str(fixture),
                         "ASSOZETA_ASSOCIATION_LIST_RECEIPTS": str(receipts)}, log=log, timeout=150)
    if code != 0:
        raise AssertionError("go-tests")
    return json.loads((receipts / "go.json").read_text())


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
                go_environment = {**tools, "ASSOZETA_ASSOCIATION_LIST_DSN":
                                  f"host={work} port=5432 user=contracts dbname={lifecycle.DATABASE} sslmode=disable"}
                phases = []
                for size in SIZES:
                    grow(size)
                    rows, queries = django_queries()
                    assert rows == size, "django-rows"
                    go = run_go(private, go_environment, size)
                    assert go["rows"] == size and go["queries"] == 1, "go-queries"
                    phases.append({"associations": size, "django_queries": queries, "go_queries": go["queries"],
                                   "go_elapsed_ms": go["elapsed_ms"]})
                assert phases[1]["django_queries"] > phases[0]["django_queries"], "django-grows"
                report["phases"] = phases
            finally:
                connections.close_all()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = lifecycle.prepare_output_directory(args.output)
    report = {"success": False, "scope": "real PostgreSQL association-list query count against Django",
              "complete_parity": False, "errors": []}
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
            with tempfile.TemporaryDirectory(prefix="az-association-list-private-", dir="/tmp") as directory:
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
                                 and report.get("private_output_removed") and report.get("phases"))
        (output / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"success": report["success"], "complete_parity": False}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
