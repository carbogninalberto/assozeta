"""Parent-only subscription/<uid>/reject, archive and delete HTTP runner; imports are inert.

Seeds associates, a course, tags, payments, subscriptions (with tag links),
course subscriptions (with membership payment links) and installments (audit
disabled) and verifies each case against the complete model state: exactly the authored subscription, payment,
course subscription and installment rows change or disappear, many-to-many
rows of deleted rows go, SET_NULL payment references of deleted payments are
cleared, and the authored auditlog entries are appended in order (delete
entries rendered from the rows before the request plus the authored in-memory
differences) with their index rows.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import base64
import ipaddress
import os
import re

from TESTS.contracts import test_subscription_lifecycle_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/subscription_lifecycle_http.json', 'TESTS/contracts/test_subscription_lifecycle_http.py',
           'TESTS/adapters/django/subscription_lifecycle_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/services/subscription_service.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/signals.py', 'BE/application/mixin.py', 'BE/application/permissions.py', 'BE/application/impersonation_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/utils/api_utils.py', 'BE/application/models/carnet_models.py',
           'BE/docmanager/models.py', 'TESTS/adapters/django/instance_logo.py') + harness.SOURCES
SUB, PAYMENT, CS, INSTALLMENT = ('application.Subscription', 'application.Payment', 'application.CourseSubscription',
                                 'application.CourseSubscriptionInstallment')
TAG_LINK = 'application.Subscription_tags'
MEMBERSHIP_LINK = 'application.CourseSubscription_membership_payments'
CARNET_LINK = 'application.CarnetSubscription_course_subscription'
ATTACHMENT_LINK = 'application.Payment_attachments'
# Models of the rows that reference a subscription (and what they need), deleted by its Collector.
RELATED_MODELS = ('docmanager.Document', 'application.ProductsAndServices', 'application.CampsAndRetreats',
                  'application.CampsAndRetreatsPeriod', 'application.MedicalAppointments', 'application.SubscriptionMembership',
                  'application.SoldProducts', 'application.SubscriptionToken', 'application.SubscriptionFile',
                  'application.SubscriptionTransfer', 'application.CampsAndRetreatsSubscription',
                  'application.CampsAndRetreatsSubscriptionPeriod', 'application.CarnetSubscription', 'application.Invoice',
                  'application.InvoiceRows')
MANAGED = (SUB, PAYMENT, CS, INSTALLMENT, TAG_LINK, MEMBERSHIP_LINK, CARNET_LINK, ATTACHMENT_LINK, harness.AUDIT, harness.INDEX) + RELATED_MODELS
BUCKET = 'subscription-lifecycle-fixture'
SENTINEL = ('subscription-lifecycle/sentinel/keep.txt', b'sentinel')
STORAGE = {}
TABLES = {'subscriptions': (SUB, 'subscription_id', 'subscriptions'), 'payments': (PAYMENT, 'payment_id', 'payments'),
          'cs': (CS, 'course_subscription_id', 'course_subscriptions'),
          'installments': (INSTALLMENT, 'course_subscription_installment_id', 'installments')}
MODELS = {'subscription': ('Subscription', 'subscriptions'), 'payment': ('Payment', 'payments'),
          'coursesubscription': ('CourseSubscription', 'course_subscriptions'),
          'coursesubscriptioninstallment': ('CourseSubscriptionInstallment', 'installments'),
          **{label.split('.')[1].lower(): (label.split('.')[1], 'related') for label in RELATED_MODELS}}


@contextmanager
def service_context(report, log):
    """A private MinIO as Django's default storage (the Go server reads the same settings)."""
    import boto3
    from botocore.config import Config
    from django.test.utils import override_settings
    from TESTS.adapters.django.instance_logo import minio
    source = contract.load_source()
    with minio(log, dict(os.environ), report) as (endpoint, access, secret):
        client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                              region_name='us-east-1',
                              config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
        client.create_bucket(Bucket=BUCKET)
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1], ContentType='text/plain')
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        STORAGE.update(client=client, location=source['location'])
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
                                   AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=endpoint,
                                   AWS_LOCATION=source['location'], AWS_S3_ADDRESSING_STYLE='path'):
                yield
            contract.require(listing() == {SENTINEL[0]}, 'storage.objects-restored')
        finally:
            STORAGE.clear()


def listing():
    pages = STORAGE['client'].get_paginator('list_objects_v2').paginate(Bucket=BUCKET)
    return {item['Key'] for page in pages for item in page.get('Contents', [])}


def object_key(name):
    return STORAGE['location'] + '/' + name


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    contract.require(row is not None, 'fixture.default-category')
    return str(row.pk)


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Payment, PaymentCategory,
                                    Subscription, Tags)
    ids = source['ids']
    group = lambda label: ids[label] if label else None

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category'], name='Iscrizioni fixture', sport_association_id=ids['own_association']))
    for row in source['associates'].values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first'], last_name=row['last'],
                                    sex='M', is_minor=False))
    for row in source['courses'].values():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], course_type=1, title=row['title'], fee=Decimal('50.00')))
    for row in source['tags'].values():
        insert(Tags, Tags(tag_id=row['id'], tag_name=row['name'], sport_association_id=ids['own_association'], creation_date=moment('2026-01-01T00:00:00Z')))
    for row in source['payments'].values():
        insert(Payment, Payment(payment_id=row['id'], sport_association_id=row['association'], subject=row['subject'], amount=Decimal(row['amount']),
                                paid=row['paid'], archived=row['archived'], deleted=row['deleted'], group_id=group(row['group']),
                                associate_id=source['associates'][row['associate']]['id'] if row['associate'] else None,
                                payment_category_id=source['category'] if row['category'] else None, creation_date=moment(row['created']),
                                description=row['description'],
                                invoice_id=source['related'][row['invoice']]['id'] if row['invoice'] else None))
    for row in source['subscriptions'].values():
        owner = ids['owner'] if row['association'] == ids['own_association'] else ids['foreign_owner']
        insert(Subscription, Subscription(subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
                                          associate_id=source['associates'][row['associate']]['id'], status_flag=row['status'],
                                          acceptance_date=day(row['acceptance']), start_date=day(row['start']), end_date=day(row['end']),
                                          creation_date=moment(row['created']), group_id=group(row['group']), archived=row['archived'],
                                          deleted=row['deleted'], payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None,
                                          signature_storage_key=row['signature_key']))
        Subscription.tags.through.objects.bulk_create([Subscription.tags.through(subscription_id=row['id'], tags_id=source['tags'][t]['id'])
                                                       for t in row['tags']])
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=source['courses'][row['course']]['id'], paid=row['paid'], creation_date=moment(row['created']),
            subscription_id=source['subscriptions'][row['subscription']]['id'],
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, multi_payments=row['multi_payments'],
            one_fee_payment=row['one_fee_payment'], archived=row['archived'], type=row['type'], multiple_quote=row['multiple_quote'],
            billed_frequency=row['billed_frequency'], billed_from=moment(row['billed_from']), billed_until=moment(row['billed_until']),
            billed_from_day_of_month=row['billed_from_day_of_month'], membership_active=row['membership_active'],
            membership_fee=Decimal(row['membership_fee']), auto_renewal=row['auto_renewal'], deleted=row['deleted']))
        CourseSubscription.membership_payments.through.objects.bulk_create([CourseSubscription.membership_payments.through(
            coursesubscription_id=row['id'], payment_id=source['payments'][p]['id']) for p in row.get('membership_payments', [])])
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['cs']]['id'], id=row['number'],
            amount=Decimal(row['amount']), paid=False, payment_date=moment(row['payment_date']), archived=row['archived'],
            payment_id=source['payments'][row['payment']]['id'] if row['payment'] else None, group_id=group(row['group']), deleted=row['deleted']))
    from django.apps import apps
    for row in source['related'].values():
        insert(apps.get_model(row['model']), apps.get_model(row['model'])(**row['fields']))


base_fixture_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    """The seeded signature objects for the workload; afterwards only the sentinel stays."""
    source = contract.load_source()
    client = STORAGE['client']
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        try:
            for name, body in source['objects'].items():
                client.put_object(Bucket=BUCKET, Key=object_key(name), Body=base64.b64decode(body), ContentType='image/png')
            fixture['default_category'] = default_category()
            # The views iterate unordered querysets (heap order). Space freed by earlier workloads can place seeded
            # rows out of insertion order, so every workload starts from the tables rewritten in key order (the
            # fixture's keys follow its insertion order).
            from django.db import connection
            with connection.cursor() as cursor:
                for table in ('application_payment', 'application_coursesubscription', 'application_coursesubscriptioninstallment'):
                    cursor.execute(f'CLUSTER {table} USING {table}_pkey')
            yield fixture
        finally:
            for key in listing() - {SENTINEL[0]}:
                client.delete_object(Bucket=BUCKET, Key=key)


def captured(source):
    """auditlog's delete diffs of the seeded rows, captured before each case."""
    from application.models import CourseSubscription, CourseSubscriptionInstallment, Payment, Subscription
    rows = {}
    for model, key in ((Subscription, 'subscriptions'), (Payment, 'payments'), (CourseSubscriptionInstallment, 'installments'),
                       (CourseSubscription, 'course_subscriptions')):
        for row in model._base_manager.filter(pk__in=[r['id'] for r in source[key].values()]):
            rows[str(row.pk)] = replace.deleted_changes(model, row)
    from django.apps import apps
    for item in source['related'].values():
        model = apps.get_model(item['model'])
        for row in model._base_manager.filter(pk=item['id']):
            rows[str(row.pk)] = replace.deleted_changes(model, row)
    rows['$objects'] = listing()
    return rows


def plain(value):
    if isinstance(value, dict) and set(value) == {'type', 'value'}:
        return value['value']
    if isinstance(value, date):
        return value.isoformat()
    return value


def flat(row):
    return {key: plain(value) for key, value in row.items()}


def bind(value, bindings):
    if isinstance(value, dict):
        return {key: bind(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, bindings) for item in value]
    return bindings.get(value, value) if isinstance(value, str) else value


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    bindings = {'$default_category': fixture['default_category']}
    gone = {name: {source[table][label]['id'] for label in effect[name]['deleted']} for name, (_, _, table) in TABLES.items()}
    for name, (model, key, table) in TABLES.items():
        changes = {source[table][label]['id']: bind(columns, bindings) for label, columns in effect[name]['changed'].items()}
        expected = []
        for row in before[model]:
            row = flat(row)
            ident = str(row[key])
            if ident in gone[name]:
                continue
            row = {**row, **changes.get(ident, {})}
            if row.get('payment_id') is not None and str(row['payment_id']) in gone['payments']:
                row['payment_id'] = None
            expected.append(row)
        contract.require([flat(r) for r in after[model]] == expected, 'state.' + name)
    related_gone = {source['related'][label]['id'] for label in effect['related_deleted']}
    contract.require(related_gone <= {item['id'] for item in source['related'].values() if not item['parent']}, 'fixture.related')
    for label in RELATED_MODELS:
        from django.apps import apps
        key = apps.get_model(label)._meta.pk.attname
        kept = [flat(r) for r in before[label] if str(flat(r)[key]) not in related_gone]
        contract.require([flat(r) for r in after[label]] == kept, 'state.' + label)
    gone_objects = {object_key(name) for name in effect['objects_deleted']}
    objects = context['prepared']['$objects']
    contract.require(gone_objects <= objects and listing() == objects - gone_objects, 'storage.objects')
    for link, columns in ((TAG_LINK, (('subscription_id', 'subscriptions'),)),
                          (MEMBERSHIP_LINK, (('coursesubscription_id', 'cs'), ('payment_id', 'payments'))),
                          (CARNET_LINK, (('coursesubscription_id', 'cs'),)), (ATTACHMENT_LINK, (('payment_id', 'payments'),))):
        kept = [flat(r) for r in before[link] if not any(str(flat(r)[column]) in gone[name] for column, name in columns)]
        contract.require([flat(r) for r in after[link]] == kept, 'state.' + link)

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: int(row['id']))
    contract.require(len(added) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    for position, (event, entry) in enumerate(zip(added, effect['audit'])):
        name, table = MODELS[entry['model']]
        key = source[table][entry['label']]['id']
        where = f":{position}:{entry['model']}:{entry['label']}"
        # CourseSubscription.__str__ is str(self.__dict__): only the ModelState address is not reproducible.
        representation = re.sub(r' object at 0x[0-9a-f]+>', ' object at 0x?>', event['object_repr']) if entry['repr'] else event['object_repr']
        contract.require(event['content_type_id'] == types[entry['model']] and event['action'] == entry['action'] and event['object_pk'] == key
                         and representation == (entry['repr'] or f'{name} object ({key})'), 'audit.identity' + where)
        changes = entry['changes']
        if changes is None and entry.get('created'):
            # A diff against None (auditlog found no old instance): the captured delete diff reversed.
            changes = {**{name: [pair[1], pair[0]] for name, pair in context['prepared'][key].items()}, **entry['overrides']}
        elif changes is None:
            changes = {**context['prepared'][key], **entry['overrides']}
        contract.require(event['changes'] == bind(changes, bindings), 'audit.changes' + where)
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if entry['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == entry['index'][0]
                             and index['resolution_path'] == entry['index'][1], 'index.fields' + where)


_run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
_assert_case = contract.assert_case


def _owner_settings(ids, values):
    from auditlog.context import disable_auditlog
    from application.models import User
    with disable_auditlog():
        User._base_manager.filter(pk=ids['owner']).update(**values)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    """run_workload_for; a case's `setup` owner settings hold only while its request runs (the state snapshots
    before and after see the fixture's settings)."""
    from application.models import User
    ids = contract.load_source()['ids']

    def assert_case(case, base_url, **options):
        setup = (case.get('setup') or {}).get('owner')
        if not setup:
            return _assert_case(case, base_url, **options)
        saved = User._base_manager.filter(pk=ids['owner']).values(*setup).get()
        contract.require(saved != setup, 'fixture.setup-changes-settings')
        _owner_settings(ids, setup)
        try:
            return _assert_case(case, base_url, **options)
        finally:
            _owner_settings(ids, saved)

    contract.assert_case = assert_case
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        contract.assert_case = _assert_case
