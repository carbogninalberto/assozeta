"""Parent-only modules list/overview/check-link HTTP runner; imports are inert.

Seeds modules of the own association, a soft-deleted association and none,
their responses, documents and attachment links (audit disabled). Every
route is a read: each case must leave the complete model state unchanged.
"""
from datetime import date, datetime

from TESTS.contracts import test_modules_reads_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/modules_reads_http.json', 'TESTS/contracts/test_modules_reads_http.py',
           'TESTS/adapters/django/modules_reads_http.py', 'BE/application/views/modules_views.py',
           'BE/application/serializers/modules_serializers.py', 'BE/application/models/modules_models.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/utils.py', 'BE/application/utils/api_utils.py', 'BE/docmanager/models.py',
           'BE/application/permissions_registry.py', 'BE/application/urls.py') + harness.SOURCES


def moment(value):
    return None if value is None else datetime.fromisoformat(value.replace('Z', '+00:00'))


def day(value):
    return None if value is None else date.fromisoformat(value)


def seed_rows(source, created):
    from application.models import Module, ModuleResponses
    from docmanager.models import Document
    ids = source['ids']
    for row in source['documents'].values():
        Document._base_manager.bulk_create([Document(document_id=row['id'], filename=row['filename'], token=row['token'],
                                                     creation_date=moment('2026-08-01T00:00:00Z'))])
        created.append((Document, row['id']))
    for row in source['modules'].values():
        Module._base_manager.bulk_create([Module(module_id=row['id'], custom_link=row['link'], sport_association_id=row['association'],
            creation_date=moment(row['created']), title=row['title'], require_approval=row['require_approval'],
            start_date=day(row['start']), end_date=day(row['end']), always_active=row['always_active'],
            max_responses=row['max_responses'], queue_mode=row['queue_mode'], only_users=row['only_users'],
            payment_required=row['payment_required'], payment_data=row['payment_data'], response_message=row['response_message'],
            allow_attachments=row['allow_attachments'], elements=row['elements'])])
        created.append((Module, row['id']))
    for row in source['responses'].values():
        ModuleResponses._base_manager.bulk_create([ModuleResponses(module_response_id=row['id'],
            module_id=source['modules'][row['module']]['id'], creation_date=moment(row['created']),
            progressive_response_number=row['number'], queue_position=row['queue'], response=row['response'],
            approved=row['approved'], user_id=ids[row['user']] if row['user'] else None, payment_id=None)])
        created.append((ModuleResponses, row['id']))
        ModuleResponses.attachments.through.objects.bulk_create([ModuleResponses.attachments.through(
            moduleresponses_id=row['id'], document_id=source['documents'][d]['id']) for d in row['attachments']])


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
