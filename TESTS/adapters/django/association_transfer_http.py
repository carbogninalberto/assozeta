"""Parent-only association-transfer HTTP runner; imports are inert.

Seeds (audit disabled) 24 Document rows with their
SportAssociationDocumentsArchive rows (21 own exports on distinct dates, an
own non-export document, an own 'Export_' document and one foreign export),
six django-celery-results TaskResult rows, a self-hosted
InstanceConfiguration whose primary association is the own one, and the
foreign association's active export key in the Django cache (Redis DB 1,
pickled the way django-redis stores it). The cache key is removed by the
harness cleanup, which restores the cache it observed before seeding.

Every case is a read or a refusal: the complete model state and the cache
must be unchanged. The download token of each export/list row is checked with
django.core.signing against the row's document and association.
"""
from datetime import date, datetime
import pickle

from TESTS.contracts import test_association_transfer_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/association_transfer_http.json', 'TESTS/contracts/test_association_transfer_http.py',
           'TESTS/adapters/django/association_transfer_http.py', 'BE/application/views/export_views.py',
           'BE/application/export_progress.py', 'BE/application/models/user_models.py', 'BE/docmanager/models.py',
           'BE/docmanager/download_tokens.py', 'BE/instance/permissions.py', 'BE/instance/models.py',
           'BE/application/permissions_registry.py', 'BE/core/middleware.py') + harness.SOURCES
ACTIVE_KEY = ':1:association-export-active:{}'
_redis = []


def verify_token(token):
    from django.core import signing
    from docmanager.download_tokens import DOWNLOAD_TOKEN_MAX_AGE, DOWNLOAD_TOKEN_SALT
    try:
        return signing.loads(token, salt=DOWNLOAD_TOKEN_SALT, max_age=DOWNLOAD_TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None


contract.TOKEN_VERIFIER[0] = verify_token


def seed_rows(source, created):
    from django_celery_results.models import TaskResult
    from application.models.user_models import SportAssociationDocumentsArchive
    from docmanager.models import Document
    from instance.models import InstanceConfiguration
    ids = source['ids']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    contract.require(not SportAssociationDocumentsArchive._base_manager.filter(
        sport_association_id__in=[ids['own_association'], ids['foreign_association'], ids['deleted_association']],
        document__filename__startswith='export_').exists(), 'fixture.exports-exist')
    for key, row in source['documents'].items():
        insert(Document, Document(document_id=row['id'], filename=row['filename'], filepath=row['filepath'],
                                  file_size_bytes=row['size'], creation_date=datetime.fromisoformat(row['created'])))
        archive = source['archives'][key]
        insert(SportAssociationDocumentsArchive, SportAssociationDocumentsArchive(
            sport_association_documents_archive_id=archive['id'], sport_association_id=ids[archive['association']],
            document_id=row['id'], date=date.fromisoformat(archive['date'])))
    for row in source['tasks'].values():
        insert(TaskResult, TaskResult(task_id=row['task_id'], status=row['status'], result=row['result'],
                                      content_type='application/json', content_encoding='utf-8', meta='{"children": []}',
                                      task_name='application.tasks.import_association_data'))
    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec = dict(source['instance'])
    spec['primary_association_id'] = ids[spec.pop('primary_association')]
    insert(InstanceConfiguration, InstanceConfiguration(**spec))
    raw = _redis[-1]
    for association, snapshot in source['active_keys'].items():
        key = ACTIVE_KEY.format(ids[association])
        contract.require(raw.set(key, pickle.dumps(snapshot, pickle.HIGHEST_PROTOCOL), nx=True), 'fixture.cache-key')


_fixture_context = harness.fixture_context_for(contract, seed_rows)


def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    _redis.append(raw)
    return _fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt)


run_workload = harness.run_workload_for(contract)
