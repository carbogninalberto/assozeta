"""Parent-only subscription/list/export and invoice/list/export HTTP runner; imports are inert.

Seeds associates, tutor relations, medical certificates, tags, subscriptions,
documents, invoices and payments (audit disabled). Every case is checked
against the complete model state and the run's private Celery broker:
- a full subscription export whose associate has no primary tutor relation
  promotes the first one (is_primary False -> True) with one auditlog update
  entry indexed through associate.sport_association; nothing else changes;
- invoice exports with m=files publish exactly one export_invoices_to_zip
  message; every other case publishes nothing and changes nothing.
"""
from datetime import date, datetime
from decimal import Decimal
import time

from TESTS.contracts import test_misc_exports_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/misc_exports_http.json', 'TESTS/contracts/test_misc_exports_http.py',
           'TESTS/adapters/django/misc_exports_http.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/views/invoice_views.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/payment_models.py', 'BE/application/utils/api_utils.py', 'BE/application/tasks.py',
           'BE/application/mixin.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/permissions_registry.py', 'BE/application/urls.py',
           'BE/core/celery.py', 'BE/instance/restore/task_base.py') + harness.SOURCES

RELATION = 'application.AssociateTutorRelation'
MANAGED = (RELATION, harness.AUDIT, harness.INDEX)
ASSOCIATE_FIELDS = ('first_name', 'last_name', 'sex', 'tax_code', 'born_city', 'address_city', 'address', 'address_cap', 'email',
                    'phone_label', 'phone', 'phone_2_label', 'phone_2', 'phone_3_label', 'phone_3', 'phone_4_label', 'phone_4',
                    'is_minor', 'deleted')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import (Associate, AssociateTutorRelation, Invoice, MedicalCertificate, Payment, Subscription)
    from application.models.subscriptions_models import Tags
    from docmanager.models import Document
    ids = source['ids']
    own = ids['own_association']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], born_date=day(row['born_date']),
                                    **{key: row[key] for key in ASSOCIATE_FIELDS}))
    for child, tutor, primary in source['relations']:
        relation = AssociateTutorRelation._base_manager.create(associate_id=associates[child]['id'], tutor_id=associates[tutor]['id'],
                                                               is_primary=primary)
        created.append((AssociateTutorRelation, relation.pk))
    certificates = source['certificates']
    for row in certificates.values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], expiration_date=day(row['expiration'])))
    for row in source['tags'].values():
        insert(Tags, Tags(tag_id=row['id'], tag_name=row['name'], sport_association_id=row['association']))
    for row in source['subscriptions'].values():
        owner = ids['owner'] if row['association'] == own else ids['foreign_owner']
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
            associate_id=associates[row['associate']]['id'] if row['associate'] else None, status_flag=row['status'],
            creation_date=moment(row['created']), start_date=day(row['start']), end_date=day(row['end']),
            subscription_number=row['number'], subscription_type=row['kind'],
            medical_id=certificates[row['medical']]['id'] if row['medical'] else None,
            signature_storage_key=row['signature_key'], signature_url=row['signature_url'],
            group_id=ids[row['group']] if row['group'] else None, archived=row['archived'], deleted=row['deleted']))
        Subscription.tags.through.objects.bulk_create([Subscription.tags.through(subscription_id=row['id'], tags_id=source['tags'][label]['id'])
                                                       for label in row['tags']])
    documents = source['documents']
    for row in documents.values():
        insert(Document, Document(document_id=row['id'], filename=row['filename'], creation_date=moment('2026-01-05T08:00:00Z')))
    invoices = source['invoices']
    for row in invoices.values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=row['association'], creation_date=moment(row['created']),
                                membership_fee=Decimal(row['membership']), activity_fee=Decimal(row['activity']), number=row['number'],
                                document_pdf_id=documents[row['document']]['id'] if row['document'] else None, archived=row['archived'],
                                deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], type=row['type'], subject=0, amount=Decimal('50.00'),
                                creation_date=moment('2026-01-05T08:00:00Z'), sport_association_id=row['association'],
                                invoice_id=invoices[row['invoice']]['id'],
                                associate_id=associates[row['associate']]['id'] if row['associate'] else None,
                                deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None))


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))


def prepare(source):
    return {'started': time.time()}


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    harness.celery_published(contract, fixture, effect['celery'] if effect else [], since=since, until=until)
    promoted = effect['promoted'] if effect else []
    if not promoted:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    associates = source['associates']
    old_r, new_r = keyed(before[RELATION], 'id'), keyed(after[RELATION], 'id')
    expected_keys = []
    for child, _ in promoted:
        mine = sorted((r for r in old_r.values() if str(scalar(r['associate_id'])) == associates[child]['id']), key=lambda r: int(r['id']))
        contract.require(mine and not any(r['is_primary'] for r in mine), 'fixture.promotion-candidate')
        expected_keys.append(str(mine[0]['id']))
    contract.require(set(old_r) == set(new_r), 'state.relations-kept')
    for key in old_r:
        if key in expected_keys:
            contract.require(new_r[key] == {**old_r[key], 'is_primary': True}, 'state.relation-promoted')
        else:
            contract.require(new_r[key] == old_r[key], 'state.relation-unchanged')
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(promoted) and len(indexes) == len(promoted), 'audit.count')
    added.sort(key=lambda event: event['id'])
    for event, key, (child, actor) in zip(added, expected_keys, promoted):
        harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model='AssociateTutorRelation', key=key,
                                  action=1, changes={'is_primary': ['False', 'True']}, actor=actor,
                                  association=associates[child]['association'], path='associate.sport_association', context=context)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
