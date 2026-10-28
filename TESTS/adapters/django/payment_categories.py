"""Real HTTP oracle for the four payment-category endpoints.

The adapter runs against the parent-owned disposable PostgreSQL/Redis pair. It
records actual Django responses, complete target persistence, audit rows and
managed-table deltas in a portable fixture. Foreign-tenant association rewrites
and partial VAT/category writes are deliberately probed as defects rather than
accepted as parity.
"""
import argparse
from contextlib import ExitStack
from decimal import Decimal
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
import json
import os
from pathlib import Path
import re
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[3]


def uid(number):
    return f'00000000-0000-4000-8000-{number:012d}'


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(datetime_timezone.utc).isoformat().replace('+00:00', 'Z')
    return str(value)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime,)):
        return _iso(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if hasattr(value, 'hex') and type(value).__name__ == 'UUID':
        return str(value)
    return value


def metadata():
    """Read serializer/model metadata without connecting to PostgreSQL."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'TESTS.adapters.django.settings')
    os.environ.setdefault('DEBUG', 'False')
    os.environ.setdefault('CURRENT_HOST', 'http://127.0.0.1:9')
    os.environ.setdefault('CURRENT_HOST_PUPPETEER', 'http://127.0.0.1:9')
    os.environ.setdefault('TESTS_DATABASE', 'assozeta_contract_metadata')
    os.environ.setdefault('TESTS_WORKDIR', '/tmp/assozeta-payment-category-oracle-go-cache')
    os.environ.setdefault('DBTYPE', 'POSTGRES')
    os.environ.setdefault('DBHOST', '/tmp/payment-category-metadata-no-connect')
    os.environ.setdefault('DBPORT', '5432')
    os.environ.setdefault('DBNAME', 'assozeta_contract_metadata')
    os.environ.setdefault('DBUSER', 'contracts')
    os.environ.setdefault('DBPASSWORD', 'metadata-only')
    os.environ.setdefault('USE_PGBOUNCER', 'False')
    os.environ.setdefault('STORE_ON_DISK', 'True')
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]

    import environ
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.setdefault('SECRET_KEY', 'payment-category-metadata-only')
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    signing_key = Ed25519PrivateKey.generate()
    os.environ.update(
        JWT_SECRET_KEY_B64='',
        JWT_PUBLIC_KEY_B64='',
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
    from application.models.payment_models import PaymentCategory, VatManagement
    from application.serializers.payment_serializers import PaymentCategorySerializer, VatManagementSerializer

    serializer = PaymentCategorySerializer()
    vat_serializer = VatManagementSerializer()
    category_meta = serializer.fields
    vat_meta = vat_serializer.fields
    routes = {}
    for path in (
        '/payment/category/list',
        '/payment/category/add',
        f'/payment/category/{uid(179990)}/update',
        f'/payment/category/{uid(179991)}/delete',
    ):
        match = resolve(path)
        actions = getattr(getattr(match.func, 'cls', None), 'actions', {})
        routes[path] = {
            'operation': getattr(getattr(match.func, 'cls', None), '__name__', match.func.__name__),
            'actions': sorted(actions),
            'permission': ['IsAuthenticated'],
        }

    model_fields = {}
    for name in (
        'payment_category_id', 'name', 'creation_date', 'sport_association',
        'expense', 'tax_deductible', 'archived', 'type', 'vat_management', 'deleted',
    ):
        field = PaymentCategory._meta.get_field(name)
        model_fields[name] = {
            'type': type(field).__name__,
            'null': field.null,
            'blank': field.blank,
            'primary_key': field.primary_key,
            'has_default': field.has_default(),
            'default': None if not field.has_default() else (
                '<callable>' if callable(field.default) else _jsonable(field.default)
            ),
        }
    vat_model_fields = {}
    for name in ('vat_management_id', 'vat', 'deduction'):
        field = VatManagement._meta.get_field(name)
        vat_model_fields[name] = {
            'type': type(field).__name__,
            'null': field.null,
            'blank': field.blank,
            'primary_key': field.primary_key,
            'has_default': field.has_default(),
            'default': None if not field.has_default() else (
                '<callable>' if callable(field.default) else _jsonable(field.default)
            ),
        }
    return {
        'success': True,
        'routes': routes,
        'category_serializer': {
            name: {
                'class': type(field).__name__,
                'read_only': bool(getattr(field, 'read_only', False)),
                'required': bool(getattr(field, 'required', False)),
                'allow_null': bool(getattr(field, 'allow_null', False)),
                'validators': [type(validator).__name__ for validator in getattr(field, 'validators', [])],
            }
            for name, field in category_meta.items()
        },
        'vat_serializer': {
            name: {
                'class': type(field).__name__,
                'read_only': bool(getattr(field, 'read_only', False)),
                'required': bool(getattr(field, 'required', False)),
                'allow_null': bool(getattr(field, 'allow_null', False)),
                'validators': [type(validator).__name__ for validator in getattr(field, 'validators', [])],
            }
            for name, field in vat_meta.items()
        },
        'category_model_fields': model_fields,
        'vat_model_fields': vat_model_fields,
        'model_defaults': {
            'expense': PaymentCategory._meta.get_field('expense').default,
            'tax_deductible': PaymentCategory._meta.get_field('tax_deductible').default,
            'archived': PaymentCategory._meta.get_field('archived').default,
            'type': PaymentCategory._meta.get_field('type').default,
            'deleted': PaymentCategory._meta.get_field('deleted').default,
            'vat': VatManagement._meta.get_field('vat').default,
            'deduction': VatManagement._meta.get_field('deduction').default,
        },
    }


def run(work, log, tool_env, address, *, go_server_factory=None):
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connection, transaction
    from django.db.models import Q
    from django.test.utils import CaptureQueriesContext
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.impersonation import session_key
    from application.models import (
        User, SportAssociation, Associate, Payment, PaymentCategory, VatManagement,
        CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsPeriodsService,
    )
    from application.models.audit_models import AuditLogIndex
    from rest_framework_simplejwt.tokens import AccessToken
    from rest_framework.test import APIClient
    from TESTS.http_client import Client, poll
    import redis
    import uvicorn

    if address != '127.0.0.1:0' and ':' not in address:
        raise RuntimeError('Payment-category Redis address must be host:port')
    run_go = os.environ.get('TESTS_PAYMENT_CATEGORIES_GO') == '1'
    versioned = os.environ.get('TESTS_PAYMENT_CATEGORIES_VERSIONED') == '1'
    lifecycle = os.environ.get('TESTS_PAYMENT_CATEGORIES_LIFECYCLE') == '1'
    if versioned and not run_go:
        raise RuntimeError('Versioned payment-category contracts require both native backends first')

    def managed_snapshot():
        return {
            model._meta.label: [
                {key: _jsonable(value) for key, value in row.items()}
                for row in model._base_manager.order_by('pk').values()
            ]
            for model in apps.get_models()
            if model._meta.managed and not model._meta.proxy
        }

    original = managed_snapshot()
    original_vat_ids = set(VatManagement.objects.values_list('pk', flat=True))
    seeded_ids = []
    fixture_users = {}
    sessions = []
    categories = {}
    vats = {}
    references = {}
    audit_baseline = set()
    cases = []
    defects = []
    missing = object()

    def fixture_user(number, role, **values):
        values.setdefault('username', f'payment-category-{number}')
        values.setdefault('email', f'payment-category-{number}@example.test')
        values.setdefault('password', '!payment-category-only')
        user = User.original_objects.create(user_id=uid(number), role=role, **values)
        seeded_ids.append(user.pk)
        fixture_users[number] = user
        return user

    with disable_auditlog():
        owner = fixture_user(160000, User.ASSOCIATION, first_name='Owner', last_name='Fixture')
        foreign = fixture_user(160001, User.ASSOCIATION, first_name='Foreign', last_name='Fixture')
        athlete = fixture_user(160002, User.ATHLETE, first_name='Athlete', last_name='Fixture')
        collaborator_read = fixture_user(
            160003, User.COLLABORATOR, connected_user=owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['bookeeping.payments.read'],
        )
        collaborator_update = fixture_user(
            160004, User.COLLABORATOR, connected_user=owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['bookeeping.payments.read', 'bookeeping.payments.update'],
        )
        collaborator_denied = fixture_user(
            160005, User.COLLABORATOR, connected_user=owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.courses.read'],
        )
        instructor = fixture_user(
            160006, User.COLLABORATOR, connected_user=owner, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.courses.read'],
        )
        owner_no_association = fixture_user(160007, User.ASSOCIATION)
        inactive = fixture_user(160008, User.ATHLETE, is_active=False)
        deleted = fixture_user(160009, User.ATHLETE, deleted=True)
        foreign_athlete = fixture_user(160010, User.ATHLETE)
        fixture_user(160011, User.COLLABORATOR, connected_user=owner, collaborator_role=User.FULL,
                     collaborator_permissions=None)
        owner_deleted_association = fixture_user(160012, User.ASSOCIATION)
        own_association = SportAssociation.original_objects.create(
            sport_association_id=uid(161000), user=owner, denomination='Payment Category Owner',
        )
        foreign_association = SportAssociation.original_objects.create(
            sport_association_id=uid(161001), user=foreign, denomination='Payment Category Foreign',
        )
        deleted_association = SportAssociation.original_objects.create(
            sport_association_id=uid(161012), user=owner_deleted_association,
            denomination='Payment Category Deleted', deleted=True,
        )
        seeded_ids.extend((own_association.pk, foreign_association.pk, deleted_association.pk))
        Associate.objects.create(
            associate_id=uid(162000), sport_association=own_association,
            user=athlete, first_name='Athlete',
        )
        Associate.objects.create(
            associate_id=uid(162001), sport_association=own_association,
            user=collaborator_update, first_name='Collaborator',
        )
        fixtures = (
            ('own-active', 170001, 171001, own_association, datetime(2026, 10, 21, 10, tzinfo=datetime_timezone.utc),
             {'name': 'Own active', 'expense': False, 'tax_deductible': True, 'archived': False, 'type': 1,
              'deleted': False}, 16, 2),
            ('own-archived', 170002, 171002, own_association, datetime(2026, 10, 20, 9, tzinfo=datetime_timezone.utc),
             {'name': 'Own archived', 'expense': True, 'tax_deductible': False, 'archived': True, 'type': 2,
              'deleted': False}, 0, 0),
            ('own-deleted', 170003, 171003, own_association, datetime(2026, 10, 19, 8, tzinfo=datetime_timezone.utc),
             {'name': 'Own deleted', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': True}, 1, 0),
            ('global-active', 170004, 171004, None, datetime(2026, 10, 22, 11, tzinfo=datetime_timezone.utc),
             {'name': 'Global active', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 5, 1),
            ('global-deleted', 170005, 171005, None, datetime(2026, 10, 18, 7, tzinfo=datetime_timezone.utc),
             {'name': 'Global deleted', 'expense': True, 'tax_deductible': False, 'archived': False, 'type': 2,
              'deleted': True}, 0, 0),
            ('foreign-active', 170006, 171006, foreign_association, datetime(2026, 10, 16, 6, tzinfo=datetime_timezone.utc),
             {'name': 'Foreign active', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 1, 0),
            ('global-archived', 170007, 171007, None, datetime(2026, 10, 17, 5, tzinfo=datetime_timezone.utc),
             {'name': 'Global archived', 'expense': False, 'tax_deductible': True, 'archived': True, 'type': 1,
              'deleted': False}, 0, 0),
            ('referenced', 170010, 171010, own_association, datetime(2026, 10, 15, 4, tzinfo=datetime_timezone.utc),
             {'name': 'Referenced category', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 1, 0),
            ('patch-name', 170011, 171011, own_association, datetime(2026, 10, 14, 3, tzinfo=datetime_timezone.utc),
             {'name': 'Patch original', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 0, 0),
            ('patch-vat', 170012, 171012, own_association, datetime(2026, 10, 13, 2, tzinfo=datetime_timezone.utc),
             {'name': 'Patch VAT', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 2, 0),
            ('delete', 170013, 171013, own_association, datetime(2026, 10, 12, 1, tzinfo=datetime_timezone.utc),
             {'name': 'Delete category', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 0, 0),
            ('patch-archived', 170014, 171014, own_association, datetime(2026, 10, 11, tzinfo=datetime_timezone.utc),
             {'name': 'Patch archived', 'expense': False, 'tax_deductible': False, 'archived': True, 'type': 1,
              'deleted': False}, 0, 0),
            ('patch-deleted', 170015, 171015, own_association, datetime(2026, 10, 10, tzinfo=datetime_timezone.utc),
             {'name': 'Patch deleted', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': True}, 0, 0),
            ('delete-repeat', 170016, 171016, own_association, datetime(2026, 10, 9, tzinfo=datetime_timezone.utc),
             {'name': 'Delete repeat', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 0, 0),
            ('tenant-rewrite', 170017, 171017, own_association, datetime(2026, 10, 8, tzinfo=datetime_timezone.utc),
             {'name': 'Tenant rewrite', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 0, 0),
            ('tenant-null', 170018, 171018, own_association, datetime(2026, 10, 7, tzinfo=datetime_timezone.utc),
             {'name': 'Tenant null', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
              'deleted': False}, 0, 0),
            ('patch-invalid', 170019, 171019, own_association, datetime(2026, 10, 6, tzinfo=datetime_timezone.utc),
             {'name': 'Patch invalid', 'expense': False, 'tax_deductible': False, 'archived': False, 'type': 1,
             'deleted': False}, 0, 0),
        )
        for name, number, vat_number, association, created, values, vat, deduction in fixtures:
            vat_row = VatManagement.objects.create(
                vat_management_id=uid(vat_number), vat=vat, deduction=deduction,
            )
            category = PaymentCategory.objects.create(
                payment_category_id=uid(number), creation_date=created,
                sport_association=association, vat_management=vat_row, **values,
            )
            vats[name] = vat_row
            categories[name] = category
            seeded_ids.extend((vat_row.pk, category.pk))
        payment = Payment.objects.create(
            payment_id=uid(172000), sport_association=own_association,
            payment_category=categories['referenced'], amount='10.00', subject=Payment.OTHER,
            description='Reference preservation',
        )
        camp = CampsAndRetreats.objects.create(
            camps_and_retreats_id=uid(172001), sport_association=own_association, title='Reference camp',
        )
        period = CampsAndRetreatsPeriod.objects.create(
            camps_and_retreats_period_id=uid(172002), camps_and_retreats=camp,
            start_date=datetime(2026, 1, 1, tzinfo=datetime_timezone.utc),
            end_date=datetime(2026, 1, 2, tzinfo=datetime_timezone.utc), title='Period',
        )
        service = CampsAndRetreatsPeriodsService.objects.create(
            camps_and_retreats_period_service_id=uid(172003), camps_and_retreats_period=period,
            title='Referenced service', payment_category=categories['referenced'],
        )
        User.original_objects.filter(pk=owner.pk).update(
            default_payment_category=categories['referenced'],
            default_payment_category_courses=categories['referenced'],
        )
        references.update(payment_id=payment.pk, camp_id=camp.pk, period_id=period.pk, service_id=service.pk)
        seeded_ids.extend((payment.pk, camp.pk, period.pk, service.pk))
    owner.refresh_from_db()
    foreign.refresh_from_db()

    audit_baseline = set(LogEntry.objects.values_list('pk', flat=True))

    def token_for(number, *, expired=False):
        value = AccessToken()
        value['user_id'] = uid(number)
        if expired:
            value.set_exp(from_time=timezone.now() - timedelta(hours=8), lifetime=timedelta(hours=1))
        return str(value)

    tokens = {
        number: token_for(number)
        for number in (
            160000, 160001, 160002, 160003, 160004, 160005,
            160006, 160007, 160008, 160009, 160010, 160011, 160012,
        )
    }
    tokens['expired'] = token_for(160000, expired=True)
    tokens['admin'] = token_for(1)
    tokens['malformed'] = 'not-a-jwt'
    session_map = {}
    for name, actor, target, association in (
        ('owner-athlete', 160000, 160002, 161000),
        ('owner-collaborator-read', 160000, 160003, 161000),
        ('owner-collaborator-update', 160000, 160004, 161000),
        ('admin-owner', 1, 160000, None),
        ('admin-foreign-owner', 1, 160001, None),
    ):
        session = uid(163000 + len(session_map))
        assert cache.get(session_key(session)) is None, f'payment-category session collision: {name}'
        cache.set(session_key(session), {
            'actor_id': uid(actor),
            'target_id': uid(target),
            'association_id': uid(association) if association else None,
        }, 3600)
        session_map[name] = session
        sessions.append(session)

    def category_record(category):
        category.refresh_from_db()
        vat = category.vat_management
        return {
            'payment_category_id': str(category.pk),
            'name': category.name,
            'creation_date': _iso(category.creation_date),
            'sport_association': str(category.sport_association_id) if category.sport_association_id else None,
            'expense': category.expense,
            'tax_deductible': category.tax_deductible,
            'archived': category.archived,
            'type': category.type,
            'vat_management': None if vat is None else {
                'vat_management_id': str(vat.pk),
                'vat': vat.vat,
                'deduction': vat.deduction,
            },
            'deleted': category.deleted,
        }

    initial_categories = {
        category.pk: {
            key: value for key, value in PaymentCategory._base_manager.filter(pk=category.pk).values().get().items()
            if key != 'payment_category_id'
        }
        for category in categories.values()
    }
    initial_vats = {
        vat.pk: {
            key: value for key, value in VatManagement._base_manager.filter(pk=vat.pk).values().get().items()
            if key != 'vat_management_id'
        }
        for vat in vats.values()
    }

    def persistence_records():
        return {
            'payment_categories': [
                {key: _jsonable(value) for key, value in row.items()}
                for row in PaymentCategory._base_manager.order_by('pk').values()
            ],
            'vat_management': [
                {key: _jsonable(value) for key, value in row.items()}
                for row in VatManagement._base_manager.order_by('pk').values()
            ],
            'payment': {
                key: _jsonable(value) for key, value in
                Payment._base_manager.filter(pk=references['payment_id']).values().get().items()
            },
            'camp_service': {
                key: _jsonable(value) for key, value in
                CampsAndRetreatsPeriodsService.objects.filter(pk=references['service_id']).values().get().items()
            },
            'owner_defaults': {
                key: _jsonable(value) for key, value in
                User.original_objects.filter(pk=owner.pk).values(
                    'default_payment_category_id', 'default_payment_category_courses_id',
                ).get().items()
            },
        }

    def audit_records(events):
        result = []
        for event in events:
            indexes = list(AuditLogIndex.objects.filter(log_entry=event))
            result.append({
                'cid': event.cid,
                'model': event.content_type.model,
                'object_pk': event.object_pk,
                'action': event.action,
                'changes': event.changes_dict,
                'actor_id': str(event.actor_id),
                'actor_email': event.actor_email,
                'additional_data': event.additional_data,
                'remote_addr': event.remote_addr,
                'indexes': [
                    {
                        'sport_association_id': str(index.sport_association_id),
                        'resolution_path': index.resolution_path,
                    }
                    for index in indexes
                ],
            })
        return result

    stable_audit_ids = {
        *(str(value) for value in initial_categories),
        *(str(value) for value in initial_vats),
        *(uid(number) for number in (
            1, 160000, 160001, 160002, 160003, 160004, 160005, 160006,
            160007, 160008, 160009, 160010, 160011, 160012,
            161000, 161001, 161012, 162000, 162001,
        )),
        *session_map.values(),
        uid(174000), uid(174100), uid(178001),
        *(str(value) for value in references.values()),
    }

    def normalize_audit_evidence(evidence, events=None):
        uuid_pattern = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
        timestamp_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$')
        # Preserve generated-object identity across each record and its links.
        # One blanket placeholder would hide swapped VAT/category references.
        dynamic_ids = {
            event['object_pk'].lower(): f'<{event["model"]}:{event["cid"]}>'
            for event in (evidence if events is None else events)
            if event['object_pk'].lower() not in stable_audit_ids and event['action'] == 0
        }

        def normalize_uuid(match):
            identifier = match.group(0).lower()
            if identifier in stable_audit_ids:
                return identifier
            assert identifier in dynamic_ids, ('unmapped generated identity in evidence', identifier)
            return dynamic_ids[identifier]

        def value(item, key=None):
            if isinstance(item, dict):
                return {field: value(content, field) for field, content in item.items()}
            if isinstance(item, list):
                return [value(content, key) for content in item]
            if key == 'creation_date' and isinstance(item, str) and timestamp_pattern.match(item):
                return '<datetime>'
            if isinstance(item, str):
                return uuid_pattern.sub(normalize_uuid, item)
            return item

        return value(evidence)

    def normalize_persistence_evidence(evidence, events):
        normalized = normalize_audit_evidence(evidence, events)
        for key in ('payment_categories', 'vat_management'):
            normalized[key].sort(key=lambda row: json.dumps(row, sort_keys=True))
        return normalized

    def add_case(name, method, path, *, token, body=missing, status, check=None,
                 raw_body=missing, content_type=None, headers=None, query='', allow=None, volatile=()):
        case = {
            'name': name,
            'method': method,
            'path': path,
            'status': status,
            'token': token,
            'body': None if body is missing else body,
            'headers': headers or {},
            'query': query,
            'remote_addr': '198.51.100.91',
            'allow': allow,
            'volatile': list(volatile),
        }
        if body is missing:
            case.pop('body')
        if raw_body is not missing:
            case['raw_body'] = raw_body
        if content_type:
            case['content_type'] = content_type
        if name in ('patch-invalid-uuid', 'delete-invalid-uuid', 'add-list-body', 'add-null-body', 'add-form', 'add-multipart'):
            assert status == 500
            case['response'] = '<stable-server-error-page>'
            case['response_contains'] = 'Server Error (500)'
            case['content_type_contains'] = 'text/html'
        case['_check'] = check
        cases.append(case)
        return case

    list_path = '/payment/category/list'
    add_path = '/payment/category/add'

    def options_contract(title):
        return lambda status, response: response == {
            'name': title,
            'description': '',
            'renders': ['application/json'],
            'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
        }

    add_case('list-owner-complete', 'GET', list_path, token=tokens[160000], status=200,
             allow=['GET', 'OPTIONS'])
    add_case('list-query-ignored', 'GET', list_path, token=tokens[160000], status=200,
             query='?deleted=true&archived=true&sport_association=' + uid(161001),
             allow=['GET', 'OPTIONS'])
    add_case('list-collaborator-read', 'GET', list_path, token=tokens[160003], status=200,
             allow=['GET', 'OPTIONS'])
    add_case('list-collaborator-update', 'GET', list_path, token=tokens[160004], status=200,
             allow=['GET', 'OPTIONS'])
    add_case('list-collaborator-full', 'GET', list_path, token=tokens[160011], status=200,
             allow=['GET', 'OPTIONS'])
    add_case('list-collaborator-no-read', 'GET', list_path, token=tokens[160005], status=403,
             check=lambda status, response: response.get('detail') == 'Missing permission: bookeeping.payments.read',
             allow=['GET', 'OPTIONS'])
    add_case('list-athlete', 'GET', list_path, token=tokens[160002], status=403,
             check=lambda status, response: response == {'error': 'user not allowed.'}, allow=['GET', 'OPTIONS'])
    add_case('list-instructor', 'GET', list_path, token=tokens[160006], status=403,
             allow=['GET', 'OPTIONS'])
    add_case('list-owner-no-association', 'GET', list_path, token=tokens[160007], status=200,
             allow=['GET', 'OPTIONS'])
    add_case('list-owner-deleted-association', 'GET', list_path, token=tokens[160012], status=200,
             allow=['GET', 'OPTIONS'])
    add_case('list-admin-direct', 'GET', list_path, token=tokens['admin'], status=403,
             check=lambda status, response: response.get('detail') == 'Seleziona un utente da impersonificare per accedere a questa sezione.')
    add_case('list-admin-impersonated', 'GET', list_path, token=tokens['admin'], status=200,
             headers={'X-Impersonation-Id': session_map['admin-owner']},
             allow=['GET', 'OPTIONS'])
    add_case('list-owner-impersonates-athlete', 'GET', list_path, token=tokens[160000], status=403,
             headers={'X-Impersonation-Id': session_map['owner-athlete']},
             check=lambda status, response: response == {
                 'detail': 'Operazione non disponibile nel contesto dell’associazione.',
             })
    add_case('list-forged-target', 'GET', list_path, token=tokens[160000], status=403,
             headers={'User-Id': uid(160001)},
             check=lambda status, response: response == {
                 'detail': 'Sessione di impersonificazione non valida. Torna al tuo account.',
                 'impersonation_invalid': 'True',
             })
    add_case('list-options', 'OPTIONS', list_path, token=tokens[160000], status=200,
             check=options_contract('Payment Category List'),
             allow=['GET', 'OPTIONS'])
    add_case('list-head', 'HEAD', list_path, token=tokens[160000], status=405,
             check=lambda status, response: response == '', allow=['GET', 'OPTIONS'])
    add_case('list-post', 'POST', list_path, token=tokens[160000], body={}, status=405,
             check=lambda status, response: response == {'detail': 'Method "POST" not allowed.'},
             allow=['GET', 'OPTIONS'])
    add_case('list-format', 'GET', list_path, token=tokens[160000], status=404, query='?format=xml',
             check=lambda status, response: response == {'detail': 'Not found.'})
    add_case('list-accept-html', 'GET', list_path, token=tokens[160000], status=406,
             headers={'Accept': 'text/html'},
             check=lambda status, response: response == {'detail': 'Could not satisfy the request Accept header.'})
    add_case('list-anonymous', 'GET', list_path, token=None, status=401,
             check=lambda status, response: response == {'detail': 'Authentication credentials were not provided.'})
    add_case('list-malformed', 'GET', list_path, token=tokens['malformed'], status=401)
    add_case('list-expired', 'GET', list_path, token=tokens['expired'], status=401)
    add_case('list-inactive', 'GET', list_path, token=tokens[160008], status=401,
             check=lambda status, response: response == {'detail': 'User is inactive', 'code': 'user_inactive'})
    add_case('list-deleted', 'GET', list_path, token=tokens[160009], status=401,
             check=lambda status, response: response == {'detail': 'User not found', 'code': 'user_not_found'})

    fixed_category = uid(174000)
    fixed_vat = uid(174100)
    fixed_created = '2026-09-01T12:34:56.120000Z'
    fixed_body = {
        'payment_category_id': fixed_category,
        'name': 'Fixed full category',
        'creation_date': fixed_created,
        'sport_association': uid(161001),
        'expense': True,
        'tax_deductible': True,
        'archived': False,
        'type': 2,
        'vat_management': {'vat_management_id': fixed_vat, 'vat': 16, 'deduction': 2},
        'deleted': False,
    }
    fixed_expected = {
        'payment_category_id': fixed_category,
        'name': 'Fixed full category',
        'creation_date': '2026-09-01T12:34:56.120000Z',
        'sport_association': uid(161000),
        'expense': True,
        'tax_deductible': True,
        'archived': False,
        'type': 2,
        'vat_management': {'vat_management_id': fixed_vat, 'vat': 16, 'deduction': 2},
        'deleted': False,
    }
    add_case('add-fixed-complete', 'POST', add_path, token=tokens[160000], body=fixed_body,
             query='?sport_association=' + uid(161001), status=200,
             check=lambda status, response: response == {'data': fixed_expected, 'msg': 'payment category created'},
             allow=['POST', 'OPTIONS'])
    add_case('add-defaults-nested-vat', 'POST', add_path, token=tokens[160000],
             body={'name': 'Defaults nested VAT', 'vat_management': {}}, status=200,
             check=lambda status, response: response['msg'] == 'payment category created' and
             response['data']['sport_association'] == uid(161000) and
             response['data']['vat_management']['vat'] == 0 and
             response['data']['vat_management']['deduction'] == 0 and
             all(response['data'][key] is False for key in ('expense', 'tax_deductible', 'archived', 'deleted')) and
             response['data']['type'] == 1,
             allow=['POST', 'OPTIONS'], volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-existing-vat-id', 'POST', add_path, token=tokens[160000],
             body={'name': 'Reuse VAT', 'vat_management': {
                 'vat_management_id': str(vats['own-active'].pk), 'vat': 1, 'deduction': 1}},
             headers={'Accept-Language': 'it'}, status=400,
             check=lambda status, response: response == {'vat_management': {
                 'vat_management_id': ['vat management con questo vat management id esiste già.']}},
             allow=['POST', 'OPTIONS'])
    add_case('add-collaborator-update', 'POST', add_path, token=tokens[160004],
             body={'name': 'Collaborator category', 'vat_management': {}}, status=200,
             check=lambda status, response: response['data']['sport_association'] == uid(161000),
             allow=['POST', 'OPTIONS'], volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-admin-impersonated', 'POST', add_path, token=tokens['admin'],
             headers={'X-Impersonation-Id': session_map['admin-owner']},
             body={'name': 'Admin category', 'vat_management': {}}, status=200,
             check=lambda status, response: response['data']['sport_association'] == uid(161000),
             allow=['POST', 'OPTIONS'], volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-collaborator-full', 'POST', add_path, token=tokens[160011],
             body={'name': 'Full collaborator category', 'vat_management': {}}, status=200,
             check=lambda status, response: response['data']['sport_association'] == uid(161000),
             volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-collaborator-read-denied', 'POST', add_path, token=tokens[160003],
             body={'name': 'Denied', 'vat_management': {}}, status=403,
             check=lambda status, response: response.get('detail') == 'Missing permission: bookeeping.payments.update')
    add_case('add-instructor-denied', 'POST', add_path, token=tokens[160006],
             body={'name': 'Denied', 'vat_management': {}}, status=403)
    add_case('add-athlete-denied', 'POST', add_path, token=tokens[160002],
             body={'name': 'Denied', 'vat_management': {}}, status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('add-athlete-malformed-body', 'POST', add_path, token=tokens[160002],
             raw_body='{', status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('add-admin-direct-denied', 'POST', add_path, token=tokens['admin'],
             body={'name': 'Denied', 'vat_management': {}}, status=403)
    add_case('add-anonymous', 'POST', add_path, token=None,
             body={'name': 'Denied', 'vat_management': {}}, status=401)
    add_case('add-malformed', 'POST', add_path, token=tokens['malformed'],
             body={'name': 'Denied', 'vat_management': {}}, status=401)
    add_case('add-inactive', 'POST', add_path, token=tokens[160008],
             body={'name': 'Denied', 'vat_management': {}}, status=401)
    add_case('add-deleted', 'POST', add_path, token=tokens[160009],
             body={'name': 'Denied', 'vat_management': {}}, status=401)
    add_case('add-missing-name', 'POST', add_path, token=tokens[160000],
             body={'vat_management': {}}, status=400,
             check=lambda status, response: 'name' in response)
    add_case('add-null-name', 'POST', add_path, token=tokens[160000],
             body={'name': None, 'vat_management': {}}, status=400,
             check=lambda status, response: 'name' in response)
    add_case('add-empty-name', 'POST', add_path, token=tokens[160000],
             body={'name': '', 'vat_management': {}}, status=400,
             check=lambda status, response: 'name' in response)
    add_case('add-long-name', 'POST', add_path, token=tokens[160000],
             body={'name': 'x' * 256, 'vat_management': {}}, status=400,
             check=lambda status, response: 'name' in response)
    add_case('add-missing-vat', 'POST', add_path, token=tokens[160000],
             body={'name': 'Missing VAT'}, status=400,
             check=lambda status, response: response.get('vat_management') == ['This field is required.'])
    add_case('add-null-vat', 'POST', add_path, token=tokens[160000],
             body={'name': 'Null VAT', 'vat_management': None}, status=400,
             check=lambda status, response: response.get('vat_management') == ['This field may not be null.'])
    add_case('add-invalid-vat-choice', 'POST', add_path, token=tokens[160000],
             body={'name': 'Invalid VAT', 'vat_management': {'vat': 99}}, status=400,
             check=lambda status, response: 'vat' in response.get('vat_management', {}))
    add_case('add-vat-multiple-errors', 'POST', add_path, token=tokens[160000],
             body={'name': 'Invalid VAT fields', 'vat_management': {'vat': 99, 'deduction': 99}},
             status=400)
    add_case('add-invalid-type-choice', 'POST', add_path, token=tokens[160000],
             body={'name': 'Invalid type', 'type': 3, 'vat_management': {}}, status=400,
             check=lambda status, response: response.get('type') == ['"3" is not a valid choice.'])
    add_case('add-boolean-yes-coercion', 'POST', add_path, token=tokens[160000],
             body={'name': 'Boolean yes', 'expense': 'yes', 'vat_management': {}}, status=200,
             check=lambda status, response: response['data']['expense'] is True,
             volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-coercions', 'POST', add_path, token=tokens[160000],
             body={'name': 'Coercions', 'expense': '1', 'tax_deductible': 0, 'archived': 'false',
                   'type': '2', 'sport_association': None,
                   'vat_management': {'vat': '1', 'deduction': '2'}}, status=200,
             check=lambda status, response: response['data']['expense'] is True and
             response['data']['tax_deductible'] is False and response['data']['archived'] is False and
             response['data']['type'] == 2 and response['data']['vat_management']['vat'] == 1 and
             response['data']['vat_management']['deduction'] == 2 and
             response['data']['sport_association'] == uid(161000),
             volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-unknown-field', 'POST', add_path, token=tokens[160000],
             body={'name': 'Unknown field category', 'unknown': {'ignored': True}, 'vat_management': {}},
             status=200,
             check=lambda status, response: response['data']['name'] == 'Unknown field category',
             volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-deleted-archived', 'POST', add_path, token=tokens[160000],
             body={'name': 'Deleted archived category', 'deleted': True, 'archived': True, 'vat_management': {}},
             status=200,
             check=lambda status, response: response['data']['deleted'] is True and
             response['data']['archived'] is True,
             volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-unicode-name', 'POST', add_path, token=tokens[160000],
             body={'name': 'à' * 255, 'vat_management': {}}, status=200,
             check=lambda status, response: response['data']['name'] == 'à' * 255,
             volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-owner-no-association', 'POST', add_path, token=tokens[160007],
             body={'name': 'No association add', 'vat_management': {}}, status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('add-owner-no-association-malformed-body', 'POST', add_path, token=tokens[160007],
             raw_body='{', status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('add-owner-deleted-association', 'POST', add_path, token=tokens[160012],
             body={'name': 'Deleted association add', 'vat_management': {}}, status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('add-null-optional', 'POST', add_path, token=tokens[160000],
             body={'name': 'Null optionals', 'expense': None, 'archived': None,
                   'type': None, 'vat_management': {}}, status=400)
    add_case('add-list-body', 'POST', add_path, token=tokens[160000], body=[], status=500,
             content_type=None)
    add_case('add-null-body', 'POST', add_path, token=tokens[160000], raw_body='null', status=500)
    add_case('add-malformed-json', 'POST', add_path, token=tokens[160000], raw_body='{', status=400)
    add_case('add-unsupported-media', 'POST', add_path, token=tokens[160000], raw_body='x',
             content_type='text/plain', status=415)
    add_case('add-form', 'POST', add_path, token=tokens[160000],
             raw_body='name=Form+category&vat_management.vat=0&vat_management.deduction=0',
             content_type='application/x-www-form-urlencoded', status=500)
    add_case('add-multipart', 'POST', add_path, token=tokens[160000],
             raw_body='--pc\r\nContent-Disposition: form-data; name="name"\r\n\r\nMultipart category\r\n'
                      '--pc\r\nContent-Disposition: form-data; name="vat_management.vat"\r\n\r\n0\r\n--pc--\r\n',
             content_type='multipart/form-data; boundary=pc', status=500)
    add_case('add-multipart-with-file', 'POST', add_path, token=tokens[160000],
             raw_body='--pc\r\nContent-Disposition: form-data; name="name"\r\n\r\nFile body denied\r\n'
                      '--pc\r\nContent-Disposition: form-data; name="upload"; filename="fixture.txt"\r\n'
                      'Content-Type: text/plain\r\n\r\nsynthetic\r\n--pc--\r\n',
             content_type='multipart/form-data; boundary=pc', status=400)
    add_case('add-duplicate-json', 'POST', add_path, token=tokens[160000],
             raw_body='{"name":"First","name":"Duplicate JSON","vat_management":{}}', status=200,
             check=lambda status, response: response['data']['name'] == 'Duplicate JSON',
             volatile=('payment_category_id', 'creation_date', 'vat_management_id'))
    add_case('add-get', 'GET', add_path, token=tokens[160000], status=405,
             check=lambda status, response: response == {'detail': 'Method "GET" not allowed.'})
    add_case('add-head', 'HEAD', add_path, token=tokens[160000], status=405,
             check=lambda status, response: response == '')
    add_case('add-options', 'OPTIONS', add_path, token=tokens[160000], status=200,
             check=options_contract('Payment Category Add'))
    add_case('add-accept-html', 'POST', add_path, token=tokens[160000],
             body={'name': 'Accept denied', 'vat_management': {}}, headers={'Accept': 'text/html'}, status=406)

    update_path = lambda number: f'/payment/category/{uid(number)}/update'
    delete_path = lambda number: f'/payment/category/{uid(number)}/delete'
    add_case('patch-name-partial', 'PATCH', update_path(170011), token=tokens[160000],
             body={'name': 'Patch renamed', 'creation_date': '2025-01-02T03:04:05Z'}, status=200,
             check=lambda status, response: response['data']['name'] == 'Patch renamed' and
             response['data']['creation_date'] == '2025-01-02T03:04:05Z' and
             response['data']['expense'] is False and
             response['data']['vat_management']['vat_management_id'] == str(vats['patch-name'].pk),
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-empty-noop', 'PATCH', update_path(170019), token=tokens[160000],
             body={}, status=200,
             check=lambda status, response: response['data'] == category_record(categories['patch-invalid']),
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-boolean-zero-false', 'PATCH', update_path(170019), token=tokens[160000],
             body={'expense': 0, 'tax_deductible': 'true', 'archived': 'False', 'type': '2'},
             status=200,
             check=lambda status, response: response['data']['expense'] is False and
             response['data']['tax_deductible'] is True and response['data']['archived'] is False and
             response['data']['type'] == 2,
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-uppercase-uuid', 'PATCH', update_path(170019).replace(uid(170019), uid(170019).upper()),
             token=tokens[160000], body={'name': 'Uppercase patched'}, status=200)
    add_case('patch-null-name', 'PATCH', update_path(170019), token=tokens[160000],
             body={'name': None}, status=400, check=lambda status, response: 'name' in response)
    add_case('patch-empty-name', 'PATCH', update_path(170019), token=tokens[160000],
             body={'name': ''}, status=400, check=lambda status, response: 'name' in response)
    add_case('patch-existing-vat-id', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {'vat_management_id': str(vats['own-archived'].pk), 'vat': 16,
                                      'deduction': 2}}, headers={'Accept-Language': 'it'}, status=400,
             check=lambda status, response: response == {'vat_management': {
                 'vat_management_id': ['vat management con questo vat management id esiste già.']}},
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-vat-new-defaults', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {}}, status=200,
             check=lambda status, response: response['data']['vat_management'] == {
                 'vat_management_id': str(vats['patch-vat'].pk), 'vat': 2, 'deduction': 0},
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-unknown-only-vat', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {'unknown': True}}, status=200,
             check=lambda status, response: response['data']['vat_management'] == {
                 'vat_management_id': str(vats['patch-vat'].pk), 'vat': 2, 'deduction': 0},
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-same-existing-vat-id', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {'vat_management_id': str(vats['patch-vat'].pk)}},
             headers={'Accept-Language': 'it'}, status=400,
             check=lambda status, response: response == {'vat_management': {
                 'vat_management_id': ['vat management con questo vat management id esiste già.']}},
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-new-vat-id', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {'vat_management_id': uid(178001), 'vat': 16, 'deduction': 2}},
             status=200,
             check=lambda status, response: response['data']['vat_management'] == {
                 'vat_management_id': uid(178001), 'vat': 16, 'deduction': 2},
             allow=['PATCH', 'OPTIONS'])
    add_case('patch-vat-create', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {'vat': 16, 'deduction': 2}}, status=200,
             check=lambda status, response: response['data']['vat_management']['vat'] == 16 and
             response['data']['vat_management']['deduction'] == 2,
             allow=['PATCH', 'OPTIONS'], volatile=('vat_management_id',))
    add_case('patch-vat-invalid', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {'vat': 99}}, status=400, check=lambda status, response: 'vat_management' in response)
    add_case('patch-vat-multiple-errors', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': {'vat': 99, 'deduction': 99}}, status=400)
    add_case('patch-vat-null', 'PATCH', update_path(170012), token=tokens[160000],
             body={'vat_management': None}, status=400)
    add_case('patch-archived-category', 'PATCH', update_path(170014), token=tokens[160000],
             body={'name': 'Archived patched'}, status=200,
             check=lambda status, response: response['data']['name'] == 'Archived patched')
    add_case('patch-deleted-category', 'PATCH', update_path(170015), token=tokens[160000],
             body={'name': 'Deleted patched'}, status=200,
             check=lambda status, response: response['data']['name'] == 'Deleted patched' and
             response['data']['deleted'] is True)
    add_case('patch-undelete', 'PATCH', update_path(170015), token=tokens[160000],
             body={'deleted': False}, status=200,
             check=lambda status, response: response['data']['deleted'] is False)
    add_case('patch-unarchive', 'PATCH', update_path(170014), token=tokens[160000],
             body={'archived': False}, status=200,
             check=lambda status, response: response['data']['archived'] is False)
    add_case('patch-same-association', 'PATCH', update_path(170019), token=tokens[160000],
             body={'sport_association': uid(161000)}, status=200,
             check=lambda status, response: response['data']['sport_association'] == uid(161000))
    # tenant_scope denies an existing foreign association before field validation.
    add_case('patch-foreign-association', 'PATCH', update_path(170019), token=tokens[160000],
             body={'sport_association': uid(161001)}, status=403,
             check=lambda status, response: response == {'detail': 'You do not have permission to perform this action.'})
    add_case('patch-null-association', 'PATCH', update_path(170019), token=tokens[160000],
             body={'sport_association': None}, status=400,
             check=lambda status, response: response == {
                 'sport_association': ["L'associazione della categoria non può essere modificata."]})
    add_case('patch-missing-association-id', 'PATCH', update_path(170019), token=tokens[160000],
             body={'sport_association': uid(179997)}, status=400)
    add_case('patch-deleted-association-id', 'PATCH', update_path(170019), token=tokens[160000],
             body={'sport_association': uid(161012)}, status=403)
    add_case('patch-new-category-id', 'PATCH', update_path(170019), token=tokens[160000],
             body={'payment_category_id': uid(177001)}, status=400,
             check=lambda status, response: response == {
                 'payment_category_id': ["L'identificativo della categoria non può essere modificato."]})
    add_case('patch-same-category-id', 'PATCH', update_path(170019), token=tokens[160000],
             body={'payment_category_id': uid(170019)}, status=200)
    add_case('patch-unknown-only', 'PATCH', update_path(170019), token=tokens[160000],
             body={'unknown': {'ignored': True}}, status=200,
             check=lambda status, response: response['msg'] == 'payment category updated')
    add_case('patch-global-not-own', 'PATCH', update_path(170004), token=tokens[160000],
             body={'name': 'Denied global'}, status=404,
             check=lambda status, response: response == {'error': 'payment category not found'})
    add_case('patch-foreign', 'PATCH', update_path(170006), token=tokens[160000],
             body={'name': 'Denied foreign'}, status=404)
    add_case('patch-foreign-changed-pk', 'PATCH', update_path(170006), token=tokens[160000],
             body={'payment_category_id': uid(177002), 'sport_association': uid(161001)}, status=404)
    add_case('patch-foreign-malformed-body', 'PATCH', update_path(170006), token=tokens[160000],
             raw_body='{', status=404,
             check=lambda status, response: response == {'error': 'payment category not found'})
    add_case('patch-missing', 'PATCH', update_path(179999), token=tokens[160000], body={'name': 'Missing'}, status=404)
    add_case('patch-missing-changed-pk', 'PATCH', update_path(179999), token=tokens[160000],
             body={'payment_category_id': uid(177003), 'sport_association': uid(161001)}, status=404)
    add_case('patch-missing-malformed-body', 'PATCH', update_path(179999), token=tokens[160000],
             raw_body='{', status=404,
             check=lambda status, response: response == {'error': 'payment category not found'})
    add_case('patch-invalid-uuid', 'PATCH', '/payment/category/not-a-uuid/update',
             token=tokens[160000], body={'name': 'Invalid'}, status=500)
    add_case('patch-collaborator-update', 'PATCH', update_path(170019), token=tokens[160004],
             body={'name': 'Collaborator patched'}, status=200)
    add_case('patch-collaborator-read-denied', 'PATCH', update_path(170019), token=tokens[160003],
             body={'name': 'Denied'}, status=403)
    add_case('patch-instructor-denied', 'PATCH', update_path(170019), token=tokens[160006],
             body={'name': 'Denied'}, status=403)
    add_case('patch-athlete-denied', 'PATCH', update_path(170019), token=tokens[160002],
             body={'name': 'Denied'}, status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('patch-admin-direct', 'PATCH', update_path(170019), token=tokens['admin'],
             body={'name': 'Denied'}, status=403)
    add_case('patch-anonymous', 'PATCH', update_path(170019), token=None,
             body={'name': 'Denied'}, status=401)
    add_case('patch-malformed-auth', 'PATCH', update_path(170019), token=tokens['malformed'],
             body={'name': 'Denied'}, status=401)
    add_case('patch-inactive', 'PATCH', update_path(170019), token=tokens[160008],
             body={'name': 'Denied'}, status=401)
    add_case('patch-deleted-user', 'PATCH', update_path(170019), token=tokens[160009],
             body={'name': 'Denied'}, status=401)
    add_case('patch-owner-impersonates-athlete', 'PATCH', update_path(170019), token=tokens[160000],
             headers={'X-Impersonation-Id': session_map['owner-athlete']},
             body={'name': 'Denied'}, status=403,
             check=lambda status, response: response == {
                 'detail': 'Operazione non disponibile nel contesto dell’associazione.',
             })
    add_case('patch-expired-impersonation', 'PATCH', update_path(170019), token=tokens[160000],
             headers={'X-Impersonation-Id': uid(169999)},
             body={'name': 'Denied'}, status=403,
             check=lambda status, response: response == {
                 'detail': 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.',
                 'impersonation_invalid': 'True',
             })
    add_case('patch-admin-impersonated', 'PATCH', update_path(170019), token=tokens['admin'],
             headers={'X-Impersonation-Id': session_map['admin-owner']},
             body={'name': 'Admin patched'}, status=200)
    add_case('patch-owner-impersonated-collaborator', 'PATCH', update_path(170019), token=tokens[160000],
             headers={'X-Impersonation-Id': session_map['owner-collaborator-read']},
             body={'name': 'Denied'}, status=403,
             check=lambda status, response: response.get('detail') == 'Missing permission: bookeeping.payments.update')
    add_case('patch-owner-impersonated-collaborator-update', 'PATCH', update_path(170019), token=tokens[160000],
             headers={'X-Impersonation-Id': session_map['owner-collaborator-update']},
             body={'name': 'Owner collaborator patched'}, status=200)
    add_case('patch-owner-no-association-global', 'PATCH', update_path(170004), token=tokens[160007],
             body={'name': 'Denied'}, status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('patch-owner-no-association-changed-pk', 'PATCH', update_path(170004), token=tokens[160007],
             body={'payment_category_id': uid(177004), 'sport_association': uid(161001)}, status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('patch-owner-deleted-association-global', 'PATCH', update_path(170004), token=tokens[160012],
             body={'name': 'Denied'}, status=403,
             check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('patch-form', 'PATCH', update_path(170019), token=tokens[160000],
             raw_body='name=Form+patched', content_type='application/x-www-form-urlencoded', status=200,
             check=lambda status, response: response['data']['name'] == 'Form patched')
    add_case('patch-multipart', 'PATCH', update_path(170019), token=tokens[160000],
             raw_body='--pc\r\nContent-Disposition: form-data; name="name"\r\n\r\nMultipart patched\r\n--pc--\r\n',
             content_type='multipart/form-data; boundary=pc', status=200,
             check=lambda status, response: response['data']['name'] == 'Multipart patched')
    add_case('patch-malformed-json', 'PATCH', update_path(170019), token=tokens[160000], raw_body='{', status=400)
    add_case('patch-unsupported-media', 'PATCH', update_path(170019), token=tokens[160000],
             raw_body='x', content_type='text/plain', status=415)
    add_case('patch-get', 'GET', update_path(170019), token=tokens[160000], status=405)
    add_case('patch-head', 'HEAD', update_path(170019), token=tokens[160000], status=405,
             check=lambda status, response: response == '')
    add_case('patch-options', 'OPTIONS', update_path(170019), token=tokens[160000], status=200,
             check=options_contract('Payment Category Update'))
    add_case('patch-accept-html', 'PATCH', update_path(170019), token=tokens[160000],
             body={'name': 'Denied'}, headers={'Accept': 'text/html'}, status=406)
    add_case('patch-query-ignored', 'PATCH', update_path(170019), token=tokens[160000],
             body={'name': 'Query ignored'}, query='?sport_association=' + uid(161001), status=200)

    add_case('delete-owned', 'DELETE', delete_path(170013), token=tokens[160000],
             query='?sport_association=' + uid(161001),
             status=200, check=lambda status, response: response == {'msg': 'payment category deleted'},
             allow=['DELETE', 'OPTIONS'])
    add_case('delete-referenced', 'DELETE', delete_path(170010), token=tokens[160000], status=200)
    add_case('delete-repeat-first', 'DELETE', delete_path(170016), token=tokens[160000], status=200)
    add_case('delete-repeat-second', 'DELETE', delete_path(170016), token=tokens[160000], status=200)
    add_case('delete-global', 'DELETE', delete_path(170004), token=tokens[160000], status=404,
             check=lambda status, response: response == {'error': 'payment category not found'})
    add_case('delete-foreign', 'DELETE', delete_path(170006), token=tokens[160000], status=404)
    add_case('delete-missing', 'DELETE', delete_path(179998), token=tokens[160000], status=404)
    add_case('delete-invalid-uuid', 'DELETE', '/payment/category/not-a-uuid/delete',
             token=tokens[160000], status=500)
    add_case('delete-collaborator-update', 'DELETE', delete_path(170019), token=tokens[160004], status=200)
    add_case('delete-collaborator-read-denied', 'DELETE', delete_path(170017), token=tokens[160003], status=403)
    add_case('delete-instructor-denied', 'DELETE', delete_path(170017), token=tokens[160006], status=403)
    add_case('delete-athlete-denied', 'DELETE', delete_path(170017), token=tokens[160002], status=403)
    add_case('delete-admin-direct', 'DELETE', delete_path(170017), token=tokens['admin'], status=403)
    add_case('delete-anonymous', 'DELETE', delete_path(170017), token=None, status=401)
    add_case('delete-malformed-auth', 'DELETE', delete_path(170017), token=tokens['malformed'], status=401)
    add_case('delete-inactive', 'DELETE', delete_path(170017), token=tokens[160008], status=401)
    add_case('delete-deleted-user', 'DELETE', delete_path(170017), token=tokens[160009], status=401)
    add_case('delete-owner-no-association-global', 'DELETE', delete_path(170004), token=tokens[160007],
             status=403, check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('delete-owner-deleted-association-global', 'DELETE', delete_path(170004), token=tokens[160012],
             status=403, check=lambda status, response: response == {'error': 'user not authorized'})
    add_case('delete-options', 'OPTIONS', delete_path(170017), token=tokens[160000], status=200,
             check=options_contract('Payment Category Delete'),
             allow=['DELETE', 'OPTIONS'])
    add_case('delete-head', 'HEAD', delete_path(170017), token=tokens[160000], status=405,
             check=lambda status, response: response == '')
    add_case('delete-post', 'POST', delete_path(170017), token=tokens[160000], body={}, status=405)
    add_case('delete-accept-html', 'DELETE', delete_path(170017), token=tokens[160000],
             headers={'Accept': 'text/html'}, status=406)

    # Independent combined-field expectations from real Django serializer
    # probes. All are non-mutating and must preserve the whole managed snapshot.
    add_case('add-missing-name-existing-vat', 'POST', add_path, token=tokens[160000],
             body={'vat_management': {'vat_management_id': uid(171001)}}, status=400)
    add_case('add-duplicate-category-invalid-vat', 'POST', add_path, token=tokens[160000],
             body={'payment_category_id': uid(170001), 'name': 'Denied combined',
                   'vat_management': {'vat': 99, 'deduction': 99}}, status=400)
    add_case('add-existing-vat-invalid-enums', 'POST', add_path, token=tokens[160000],
             body={'name': 'Denied existing VAT', 'vat_management': {
                 'vat_management_id': uid(171001), 'vat': 99, 'deduction': 99}}, status=400)
    add_case('patch-combined-invalid', 'PATCH', update_path(170011), token=tokens[160000],
             body={'payment_category_id': uid(178002), 'sport_association': uid(161001),
                   'name': '', 'vat_management': {
                       'vat_management_id': uid(171001), 'vat': 99, 'deduction': 99}}, status=403)
    add_case('patch-missing-association-invalid-name', 'PATCH', update_path(170011),
             token=tokens[160000], body={'sport_association': uid(179997), 'name': ''}, status=400)
    add_case('patch-changed-id-null-vat', 'PATCH', update_path(170011), token=tokens[160000],
             body={'payment_category_id': uid(178002), 'vat_management': None}, status=400)
    add_case('patch-list-body', 'PATCH', update_path(170011), token=tokens[160000], body=[], status=400)
    add_case('patch-null-scalar-fields', 'PATCH', update_path(170011), token=tokens[160000],
             body={field: None for field in (
                 'payment_category_id', 'name', 'expense', 'tax_deductible', 'archived', 'type', 'deleted',
             )}, status=400)
    add_case('patch-null-nested-vat-fields', 'PATCH', update_path(170011), token=tokens[160000],
             body={'vat_management': {'vat_management_id': None, 'vat': None, 'deduction': None}}, status=400)
    for kind, invalid in (('list', []), ('string', 'invalid')):
        add_case(f'add-vat-{kind}', 'POST', add_path, token=tokens[160000],
                 body={'name': 'Denied nested shape', 'vat_management': invalid}, status=400)
    # A separate invalid VAT ensures accepted numeric names and ISO datetime
    # variants are validated without introducing incidental successful writes.
    for kind, name in (('int', 123), ('float', 1.2), ('exponent', 1e20)):
        add_case(f'add-numeric-name-{kind}-invalid-vat', 'POST', add_path, token=tokens[160000],
                 body={'name': name, 'vat_management': {'vat': 99}}, status=400)
    for kind, value in (('naive', '2021-01-02T03:04:05.120000'), ('space', '2021-01-02 03:04:05')):
        add_case(f'patch-{kind}-datetime-invalid-vat', 'PATCH', update_path(170011), token=tokens[160000],
                 body={'creation_date': value, 'vat_management': {'vat': 99}}, status=400)
    add_case('patch-form-null-association', 'PATCH', update_path(170011), token=tokens[160000],
             raw_body='sport_association=', content_type='application/x-www-form-urlencoded', status=400)
    add_case('patch-nul-name', 'PATCH', update_path(170011), token=tokens[160000],
             body={'name': 'bad\x00name'}, status=400)
    add_case('patch-nul-name-italian', 'PATCH', update_path(170011), token=tokens[160000],
             body={'name': 'bad\x00name'}, headers={'Accept-Language': 'it'}, status=400)
    add_case('patch-surrogate-name', 'PATCH', update_path(170011), token=tokens[160000],
             body={'name': '\ud800'}, status=400)
    for kind, name in (('surrogate-pair', '\U0001f600'), ('replacement-character', '\ufffd')):
        add_case(f'patch-{kind}-invalid-vat', 'PATCH', update_path(170011), token=tokens[160000],
                 body={'name': name, 'vat_management': {'vat': 99}}, status=400)
    add_case('patch-unknown-surrogate-noop', 'PATCH', update_path(170011), token=tokens[160000],
             body={'unknown': '\ud800'}, status=200)
    # These independently declared forms all mean the existing fixture instant.
    # Exact canonical output and whole-state equality prove parsing and no-op
    # audit behavior without adding unrelated mutations to later invariants.
    for kind, value in (
        ('minute', '2026-10-06T00:00'),
        ('basic', '20261006T000000Z'),
        ('compact-offset', '2026-10-06T02:00:00+0200'),
        ('variable-padding', '2026-10-6T0:0:0'),
    ):
        case = add_case(f'patch-datetime-{kind}', 'PATCH', update_path(170019), token=tokens[160000],
                        body={'creation_date': value}, status=200)
        case['expected_creation_date'] = '2026-10-06T00:00:00Z'
        case['no_effect'] = True
    for language in ('en', 'it'):
        for kind, value in (
            ('whitespace', ' 2026-10-06T00:00:00Z '),
            ('utc-underflow', '0001-01-01T00:00:00+01:00'),
            ('utc-overflow', '9999-12-31T23:59:59-01:00'),
        ):
            add_case(f'patch-datetime-{kind}-{language}', 'PATCH', update_path(170019), token=tokens[160000],
                     body={'creation_date': value}, headers={'Accept-Language': language}, status=400)
    case = add_case('patch-python-control-strip-noop', 'PATCH', update_path(170011), token=tokens[160000],
                    body={'name': '\x1c\x1dPatch renamed\x1e\x1f'}, status=200)
    case['expected_name'] = 'Patch renamed'
    case['no_effect'] = True
    add_case('patch-python-control-strip-blank', 'PATCH', update_path(170011), token=tokens[160000],
             body={'name': '\x1c\x1d\x1e\x1f'}, status=400)

    path = work / 'payment-categories.json'
    persistent_fixture = ROOT / 'quality-reports/go-migration/payment-categories/fixture.json'
    fixture = {'cases': cases, 'fixture': {
        'owner': uid(160000), 'foreign_owner': uid(160001), 'athlete': uid(160002),
        'own_association': uid(161000), 'foreign_association': uid(161001),
        'references': {key: str(value) for key, value in references.items()},
        'fixed_add': {'payment_category_id': fixed_category, 'vat_management_id': fixed_vat},
    }}
    fixture_path = path

    def assign_expected_responses():
        states = {
            str(category.pk): category_record(category)
            for category in categories.values()
        }
        expected_by_name = {}

        def coercion(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value.lower() in ('true', '1'):
                    return True
                if value.lower() in ('false', '0'):
                    return False
            return bool(value)

        def new_vat(value):
            value = value or {}
            return {
                'vat_management_id': str(value.get('vat_management_id') or '<uuid>'),
                'vat': int(value.get('vat', 0)),
                'deduction': int(value.get('deduction', 0)),
            }

        def added(body):
            return {
                'payment_category_id': str(body.get('payment_category_id') or '<uuid>'),
                'name': body['name'],
                'creation_date': body.get('creation_date') or '<datetime-micros>',
                'sport_association': uid(161000),
                'expense': coercion(body.get('expense', False)),
                'tax_deductible': coercion(body.get('tax_deductible', False)),
                'archived': coercion(body.get('archived', False)),
                'type': int(body.get('type', 1)),
                'vat_management': new_vat(body['vat_management']),
                'deleted': coercion(body.get('deleted', False)),
            }

        for case in cases:
            if case['status'] >= 400:
                continue
            name = case['name']
            if case['method'] == 'GET' and case['path'] == list_path:
                if name in ('list-owner-no-association', 'list-owner-deleted-association'):
                    visible = [state for state in states.values() if state['sport_association'] is None]
                else:
                    visible = [
                        state for state in states.values()
                        if state['sport_association'] in (None, uid(161000))
                    ]
                expected_by_name[name] = {'data': sorted(
                    visible, key=lambda state: state['creation_date'], reverse=True,
                )}
            elif case['method'] == 'OPTIONS':
                expected_by_name[name] = {
                    'name': {
                        'list-options': 'Payment Category List',
                        'add-options': 'Payment Category Add',
                        'patch-options': 'Payment Category Update',
                        'delete-options': 'Payment Category Delete',
                    }[name],
                    'description': '',
                    'renders': ['application/json'],
                    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'],
                }
            elif case['method'] == 'POST' and case['path'] == add_path:
                body = json.loads(case['raw_body']) if 'raw_body' in case else case['body']
                state = added(body)
                states[state['payment_category_id']] = state
                expected_by_name[name] = {'data': state, 'msg': 'payment category created'}
            elif case['method'] == 'PATCH' and 'raw_body' in case:
                target = next(key for key in states if key in case['path'].lower())
                state = dict(states[target])
                if name == 'patch-form':
                    state['name'] = 'Form patched'
                elif name == 'patch-multipart':
                    state['name'] = 'Multipart patched'
                else:
                    raise AssertionError(f'No explicit PATCH raw-body expectation for {name}')
                states[target] = state
                expected_by_name[name] = {'data': state, 'msg': 'payment category updated'}
            elif case['method'] == 'PATCH':
                target = next(key for key in states if key in case['path'].lower())
                state = dict(states[target])
                body = case['body']
                if name == 'patch-query-ignored':
                    state['name'] = 'Query ignored'
                for field in (
                    'name', 'creation_date', 'sport_association', 'expense', 'tax_deductible',
                    'archived', 'type', 'deleted',
                ):
                    if field not in body:
                        continue
                    value = body[field]
                    if field == 'sport_association':
                        value = str(value) if value is not None else None
                    elif field in ('expense', 'tax_deductible', 'archived', 'deleted'):
                        value = coercion(value)
                    elif field == 'type':
                        value = int(value)
                    if field == 'creation_date' and 'expected_creation_date' in case:
                        value = case['expected_creation_date']
                    if field == 'name' and 'expected_name' in case:
                        value = case['expected_name']
                    state[field] = value
                if 'vat_management' in body:
                    vat = body['vat_management']
                    if vat and any(key in vat for key in ('vat_management_id', 'vat', 'deduction')):
                        current = state['vat_management'] or {'vat_management_id': '<uuid>', 'vat': 0, 'deduction': 0}
                        state['vat_management'] = {
                            'vat_management_id': str(vat.get('vat_management_id') or '<uuid>'),
                            'vat': int(vat.get('vat', current['vat'])),
                            'deduction': int(vat.get('deduction', current['deduction'])),
                        }
                states[target] = state
                expected_by_name[name] = {'data': state, 'msg': 'payment category updated'}
            elif case['method'] == 'DELETE':
                target = next(key for key in states if key in case['path'].lower())
                state = dict(states[target])
                state['deleted'] = True
                states[target] = state
                expected_by_name[name] = {'msg': 'payment category deleted'}
            elif case['method'] == 'HEAD':
                expected_by_name[name] = ''
            else:
                raise AssertionError(f'No explicit successful response expectation for {name}')

        invalid_token = {
            'detail': 'Given token not valid for any token type',
            'code': 'token_not_valid',
            'messages': [{
                'token_class': 'AccessToken', 'token_type': 'access',
                'message': 'Token is invalid',
            }],
        }
        expired_token = {
            **invalid_token,
            'messages': [{
                'token_class': 'AccessToken', 'token_type': 'access',
                'message': 'Token is expired',
            }],
        }
        error_by_name = {
            'list-collaborator-no-read': {'detail': 'Missing permission: bookeeping.payments.read'},
            'list-athlete': {'error': 'user not allowed.'},
            'list-instructor': None,
            'list-admin-direct': {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
            'list-owner-impersonates-athlete': {
                'detail': 'Operazione non disponibile nel contesto dell’associazione.',
            },
            'list-head': '',
            'list-post': {'detail': 'Method "POST" not allowed.'},
            'list-format': {'detail': 'Not found.'},
            'list-accept-html': {'detail': 'Could not satisfy the request Accept header.'},
            'list-anonymous': {'detail': 'Authentication credentials were not provided.'},
            'list-malformed': invalid_token,
            'list-expired': expired_token,
            'list-inactive': {'detail': 'User is inactive', 'code': 'user_inactive'},
            'list-deleted': {'detail': 'User not found', 'code': 'user_not_found'},
            'list-forged-target': {
                'detail': 'Sessione di impersonificazione non valida. Torna al tuo account.',
                'impersonation_invalid': 'True',
            },
            'add-existing-vat-id': {'vat_management': {
                'vat_management_id': ['vat management con questo vat management id esiste già.'],
            }},
            'add-collaborator-read-denied': {'detail': 'Missing permission: bookeeping.payments.update'},
            'add-instructor-denied': None,
            'add-athlete-denied': {'error': 'user not authorized'},
            'add-athlete-malformed-body': {'error': 'user not authorized'},
            'add-admin-direct-denied': {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
            'add-anonymous': {'detail': 'Authentication credentials were not provided.'},
            'add-malformed': invalid_token,
            'add-inactive': {'detail': 'User is inactive', 'code': 'user_inactive'},
            'add-deleted': {'detail': 'User not found', 'code': 'user_not_found'},
            'add-missing-name': {'name': ['This field is required.']},
            'add-null-name': {'name': ['This field may not be null.']},
            'add-empty-name': {'name': ['This field may not be blank.']},
            'add-long-name': {'name': ['Ensure this field has no more than 255 characters.']},
            'add-missing-vat': {'vat_management': ['This field is required.']},
            'add-null-vat': {'vat_management': ['This field may not be null.']},
            'add-invalid-vat-choice': {'vat_management': {'vat': ['"99" is not a valid choice.']}},
            'add-vat-multiple-errors': {'vat_management': {
                'vat': ['"99" is not a valid choice.'],
                'deduction': ['"99" is not a valid choice.'],
            }},
            'add-invalid-type-choice': {'type': ['"3" is not a valid choice.']},
            'add-owner-no-association': {'error': 'user not authorized'},
            'add-owner-no-association-malformed-body': {'error': 'user not authorized'},
            'add-owner-deleted-association': {'error': 'user not authorized'},
            'add-null-optional': {
                'expense': ['This field may not be null.'],
                'archived': ['This field may not be null.'],
                'type': ['This field may not be null.'],
            },
            'add-malformed-json': {
                'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)',
            },
            'add-unsupported-media': {'detail': 'Unsupported media type "text/plain" in request.'},
            'add-form': {'vat_management': ['This field is required.']},
            'add-multipart': {'vat_management': ['This field is required.']},
            'add-multipart-with-file': {'vat_management': ['This field is required.']},
            'add-get': {'detail': 'Method "GET" not allowed.'},
            'add-head': '',
            'add-accept-html': {'detail': 'Could not satisfy the request Accept header.'},
            'patch-null-name': {'name': ['This field may not be null.']},
            'patch-empty-name': {'name': ['This field may not be blank.']},
            'patch-existing-vat-id': {'vat_management': {
                'vat_management_id': ['vat management con questo vat management id esiste già.'],
            }},
            'patch-same-existing-vat-id': {'vat_management': {
                'vat_management_id': ['vat management con questo vat management id esiste già.'],
            }},
            'patch-vat-invalid': {'vat_management': {'vat': ['"99" is not a valid choice.']}},
            'patch-vat-multiple-errors': {'vat_management': {
                'vat': ['"99" is not a valid choice.'],
                'deduction': ['"99" is not a valid choice.'],
            }},
            'patch-vat-null': {'vat_management': ['This field may not be null.']},
            'patch-foreign-association': {'detail': 'You do not have permission to perform this action.'},
            'patch-null-association': {
                'sport_association': ["L'associazione della categoria non può essere modificata."],
            },
            'patch-missing-association-id': {
                'sport_association': [f'Invalid pk "{uid(179997)}" - object does not exist.'],
            },
            'patch-deleted-association-id': {'detail': 'You do not have permission to perform this action.'},
            'patch-new-category-id': {
                'payment_category_id': ["L'identificativo della categoria non può essere modificato."],
            },
            'patch-global-not-own': {'error': 'payment category not found'},
            'patch-foreign': {'error': 'payment category not found'},
            'patch-foreign-changed-pk': {'error': 'payment category not found'},
            'patch-foreign-malformed-body': {'error': 'payment category not found'},
            'patch-missing': {'error': 'payment category not found'},
            'patch-missing-changed-pk': {'error': 'payment category not found'},
            'patch-missing-malformed-body': {'error': 'payment category not found'},
            'patch-collaborator-read-denied': {'detail': 'Missing permission: bookeeping.payments.update'},
            'patch-instructor-denied': None,
            'patch-athlete-denied': {'error': 'user not authorized'},
            'patch-admin-direct': {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
            'patch-anonymous': {'detail': 'Authentication credentials were not provided.'},
            'patch-malformed-auth': invalid_token,
            'patch-inactive': {'detail': 'User is inactive', 'code': 'user_inactive'},
            'patch-deleted-user': {'detail': 'User not found', 'code': 'user_not_found'},
            'patch-owner-impersonates-athlete': {
                'detail': 'Operazione non disponibile nel contesto dell’associazione.',
            },
            'patch-expired-impersonation': {
                'detail': 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.',
                'impersonation_invalid': 'True',
            },
            'patch-owner-impersonated-collaborator': {'detail': 'Missing permission: bookeeping.payments.update'},
            'patch-owner-no-association-global': {'error': 'user not authorized'},
            'patch-owner-no-association-changed-pk': {'error': 'user not authorized'},
            'patch-owner-deleted-association-global': {'error': 'user not authorized'},
            'patch-malformed-json': {
                'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)',
            },
            'patch-unsupported-media': {'detail': 'Unsupported media type "text/plain" in request.'},
            'patch-get': {'detail': 'Method "GET" not allowed.'},
            'patch-head': '',
            'patch-accept-html': {'detail': 'Could not satisfy the request Accept header.'},
            'delete-global': {'error': 'payment category not found'},
            'delete-foreign': {'error': 'payment category not found'},
            'delete-missing': {'error': 'payment category not found'},
            'delete-collaborator-read-denied': {'detail': 'Missing permission: bookeeping.payments.update'},
            'delete-instructor-denied': None,
            'delete-athlete-denied': {'error': 'user not authorized'},
            'delete-admin-direct': {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
            'delete-anonymous': {'detail': 'Authentication credentials were not provided.'},
            'delete-malformed-auth': invalid_token,
            'delete-inactive': {'detail': 'User is inactive', 'code': 'user_inactive'},
            'delete-deleted-user': {'detail': 'User not found', 'code': 'user_not_found'},
            'delete-owner-no-association-global': {'error': 'user not authorized'},
            'delete-owner-deleted-association-global': {'error': 'user not authorized'},
            'delete-head': '',
            'delete-post': {'detail': 'Method "POST" not allowed.'},
            'delete-accept-html': {'detail': 'Could not satisfy the request Accept header.'},
        }
        error_by_name.update({
            'patch-python-control-strip-blank': {'name': ['This field may not be blank.']},
            'patch-form-null-association': {
                'sport_association': ["L'associazione della categoria non può essere modificata."],
            },
            'patch-nul-name': {'name': ['Null characters are not allowed.']},
            'patch-nul-name-italian': {'name': ['I caratteri null non sono ammessi.']},
            'patch-surrogate-name': {'name': ['Surrogate characters are not allowed: U+D800.']},
            'patch-list-body': {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
            'patch-null-scalar-fields': {field: ['This field may not be null.'] for field in (
                'payment_category_id', 'name', 'expense', 'tax_deductible', 'archived', 'type', 'deleted',
            )},
            'patch-null-nested-vat-fields': {'vat_management': {
                field: ['This field may not be null.'] for field in ('vat_management_id', 'vat', 'deduction')
            }},
            'add-vat-list': {'vat_management': {
                'non_field_errors': ['Invalid data. Expected a dictionary, but got list.'],
            }},
            'add-vat-string': {'vat_management': {
                'non_field_errors': ['Invalid data. Expected a dictionary, but got str.'],
            }},
            'add-missing-name-existing-vat': {
                'name': ['This field is required.'],
                'vat_management': {'vat_management_id': ['vat management with this vat management id already exists.']},
            },
            'add-duplicate-category-invalid-vat': {
                'payment_category_id': ['payment category with this payment category id already exists.'],
                'vat_management': {'vat': ['"99" is not a valid choice.'],
                                   'deduction': ['"99" is not a valid choice.']},
            },
            'add-existing-vat-invalid-enums': {
                'vat_management': {'vat_management_id': ['vat management with this vat management id already exists.'],
                                   'vat': ['"99" is not a valid choice.'],
                                   'deduction': ['"99" is not a valid choice.']},
            },
            'patch-combined-invalid': {'detail': 'You do not have permission to perform this action.'},
            'patch-missing-association-invalid-name': {
                'sport_association': [f'Invalid pk "{uid(179997)}" - object does not exist.'],
                'name': ['This field may not be blank.'],
            },
            'patch-changed-id-null-vat': {
                'payment_category_id': ["L'identificativo della categoria non può essere modificato."],
                'vat_management': ['This field may not be null.'],
            },
        })
        for name in (
            'add-numeric-name-int-invalid-vat', 'add-numeric-name-float-invalid-vat',
            'add-numeric-name-exponent-invalid-vat', 'patch-naive-datetime-invalid-vat',
            'patch-space-datetime-invalid-vat',
            'patch-surrogate-pair-invalid-vat', 'patch-replacement-character-invalid-vat',
        ):
            error_by_name[name] = {'vat_management': {'vat': ['"99" is not a valid choice.']}}
        for language in ('en', 'it'):
            invalid_datetime = (
                'Datetime has wrong format. Use one of these formats instead: '
                'YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
                if language == 'en' else
                "L'oggetto di tipo datetime è in un formato errato. Usa uno dei seguenti formati: "
                'YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].'
            )
            error_by_name[f'patch-datetime-whitespace-{language}'] = {'creation_date': [invalid_datetime]}
            for kind in ('utc-underflow', 'utc-overflow'):
                error_by_name[f'patch-datetime-{kind}-{language}'] = {'creation_date': ['Datetime value out of range.']}
        # Role denials without a specialized body all use the view's mutation text.
        for name in ('list-instructor', 'add-instructor-denied', 'patch-instructor-denied', 'delete-instructor-denied'):
            permission = 'bookeeping.payments.read' if name == 'list-instructor' else 'bookeeping.payments.update'
            error_by_name[name] = {'detail': f'Missing permission: {permission}'}
        for case in cases:
            if case.get('response_contains'):
                expected_by_name[case['name']] = case['response']
                continue
            if case['status'] >= 200 and case['name'] not in expected_by_name:
                if case['name'] not in error_by_name:
                    raise AssertionError(f'No explicit expected response for {case["name"]}')
                expected_by_name[case['name']] = error_by_name[case['name']]
        if set(expected_by_name) != {case['name'] for case in cases}:
            raise AssertionError(
                f'Expected-response names differ: missing={set(case["name"] for case in cases) - set(expected_by_name)}, '
                f'extra={set(expected_by_name) - set(case["name"] for case in cases)}'
            )
        for case in cases:
            case['response'] = expected_by_name[case['name']]
            case['allow'] = sorted({
                list_path: ['GET', 'OPTIONS'],
                add_path: ['POST', 'OPTIONS'],
                '/payment/category/<uid>/update': ['PATCH', 'OPTIONS'],
                '/payment/category/<uid>/delete': ['DELETE', 'OPTIONS'],
            }.get(
                case['path'] if case['path'] in (list_path, add_path)
                else '/payment/category/<uid>/update' if case['path'].lower().endswith('/update')
                else '/payment/category/<uid>/delete',
                [],
            ))

    assign_expected_responses()

    def persist_fixture(*, publish=False):
        assert all('_check' not in case for case in cases), 'callable checks must not leak into fixture JSON'
        payload = json.dumps(_jsonable(fixture), indent=2, sort_keys=True) + '\n'
        fixture_path.write_text(payload)
        fixture_path.chmod(0o600)
        if publish:
            persistent_fixture.parent.mkdir(parents=True, exist_ok=True)
            staged = persistent_fixture.with_suffix('.json.tmp')
            staged.write_text(payload)
            staged.chmod(0o600)
            staged.replace(persistent_fixture)

    def reset_fixture_for_replay():
        with disable_auditlog():
            PaymentCategory._base_manager.filter(
                sport_association_id=own_association.pk
            ).exclude(pk__in=initial_categories).delete()
            VatManagement._base_manager.exclude(pk__in={*initial_vats, *original_vat_ids}).delete()
            for category_id, values in initial_categories.items():
                assert PaymentCategory._base_manager.filter(pk=category_id).update(**values) == 1
            for vat_id, values in initial_vats.items():
                assert VatManagement._base_manager.filter(pk=vat_id).update(**values) == 1
            LogEntry.objects.exclude(pk__in=audit_baseline).delete()

    def assert_case_expectation(case, status, response, received):
        if status != case['status']:
            raise AssertionError(f"{case['name']}: expected HTTP {case['status']}, got {status}: {response!r}")
        if case.get('response_contains'):
            if case['response_contains'] not in str(response):
                raise AssertionError(
                    f"{case['name']}: stable server-error body marker missing: {response!r}"
                )
            case.pop('_check', None)
        else:
            from TESTS.contracts.test_payment_categories import normalize
            volatile = set(case.get('volatile', ()))
            if normalize(response, volatile) != normalize(case['response'], volatile):
                raise AssertionError(
                    f"{case['name']}: response differs from explicit expectation: "
                    f"expected {case['response']!r}, got {response!r}"
                )
        if case.get('content_type_contains'):
            content_type = received.get('Content-Type', '').lower()
            if case['content_type_contains'] not in content_type:
                raise AssertionError(
                    f"{case['name']}: expected Content-Type containing "
                    f"{case['content_type_contains']!r}, got {content_type!r}"
                )
        if case.get('response_contains'):
            return
        check = case.get('_check')
        if check is not None:
            try:
                assert check(status, response), case['name']
            except AssertionError as failure:
                raise AssertionError(
                    f"{case['name']}: response contract failed: {response!r}"
                ) from failure
        case.pop('_check', None)

    def execute_declared_cases(base):
        import unittest
        from TESTS.contracts.test_payment_categories import assert_case
        assertions = unittest.TestCase()
        assertions.maxDiff = None
        for case in cases:
            before_http = managed_snapshot() if case['status'] >= 400 or case.get('no_effect') else None
            status, response, received = assert_case(assertions, base, case)
            assert_case_expectation(case, status, response, received)
            if before_http is not None and managed_snapshot() != before_http:
                raise AssertionError(f"{case['name']}: denied/error/no-op HTTP request changed managed database state")
        persist_fixture()

    def category_changed(fixture_name, **expected):
        row = category_record(categories[fixture_name])
        for key, value in expected.items():
            assert row[key] == value, (fixture_name, key, row[key], value)
        return row

    def verify_persistence(expected_remote_addr='198.51.100.91'):
        fixed_category_row = PaymentCategory.objects.get(pk=fixed_category)
        fixed = category_record(fixed_category_row)
        assert fixed['payment_category_id'] == fixed_category, 'fixed add did not persist requested category ID'
        assert fixed['sport_association'] == uid(161000), 'fixed add accepted the foreign tenant'
        assert fixed['vat_management'] == {'vat_management_id': fixed_vat, 'vat': 16, 'deduction': 2}
        by_name = {
            row['name']: row for row in
            PaymentCategory.objects.filter(name__in=[
                'Defaults nested VAT', 'Collaborator category', 'Admin category',
                'Full collaborator category',
                'Coercions', 'Boolean yes', 'Duplicate JSON', 'Unknown field category',
                'Deleted archived category', 'à' * 255,
            ]).values()
        }
        assert set(by_name) == {
            'Defaults nested VAT', 'Collaborator category', 'Admin category',
            'Full collaborator category',
            'Coercions', 'Boolean yes', 'Duplicate JSON', 'Unknown field category',
            'Deleted archived category', 'à' * 255,
        }, by_name
        for name, row in by_name.items():
            persisted = PaymentCategory.objects.select_related('vat_management').get(pk=row['payment_category_id'])
            assert str(persisted.sport_association_id) == str(own_association.pk), (name, 'wrong tenant')
            assert persisted.vat_management is not None, (name, 'missing nested VAT')
        assert by_name['Defaults nested VAT']['vat_management_id'] is not None
        assert not PaymentCategory.objects.filter(name='Reuse VAT').exists()
        for vat_id, expected in initial_vats.items():
            persisted_vat = VatManagement._base_manager.filter(pk=vat_id).values().get()
            persisted_vat.pop('vat_management_id')
            assert persisted_vat == expected, ('existing VAT row changed', str(vat_id), persisted_vat, expected)
        category_changed(
            'patch-name', name='Patch renamed', creation_date='2025-01-02T03:04:05Z',
        )
        category_changed('patch-archived', name='Archived patched', archived=False)
        category_changed('patch-deleted', name='Deleted patched', deleted=False)
        patch_vat = category_changed('patch-vat')
        assert patch_vat['vat_management']['vat'] == 16
        assert patch_vat['vat_management']['deduction'] == 2
        assert patch_vat['vat_management']['vat_management_id'] != str(vats['patch-vat'].pk)
        assert categories['patch-invalid'].refresh_from_db() is None
        assert categories['patch-invalid'].name == 'Query ignored'
        category_changed('delete', deleted=True)
        category_changed('delete-repeat', deleted=True)
        category_changed(
            'tenant-rewrite', sport_association=str(own_association.pk),
            name='Tenant rewrite', deleted=False,
        )
        category_changed('tenant-null', sport_association=str(own_association.pk), name='Tenant null')
        assert categories['patch-invalid'].deleted is True, 'collaborator delete did not soft-delete'
        category_changed('referenced', deleted=True, name='Referenced category')
        payment = Payment._base_manager.get(pk=references['payment_id'])
        service = CampsAndRetreatsPeriodsService.objects.get(pk=references['service_id'])
        owner_refreshed = User.original_objects.get(pk=owner.pk)
        assert payment.payment_category_id == categories['referenced'].pk
        assert service.payment_category_id == categories['referenced'].pk
        assert owner_refreshed.default_payment_category_id == categories['referenced'].pk
        assert owner_refreshed.default_payment_category_courses_id == categories['referenced'].pk
        assert PaymentCategory._base_manager.count() == 28, PaymentCategory._base_manager.count()
        assert VatManagement._base_manager.count() == 30, VatManagement._base_manager.count()

        audit = list(LogEntry.objects.exclude(pk__in=audit_baseline).order_by('id'))
        assert audit, 'successful mutations produced no audit rows'
        assert len(audit) == 43, (
            'unexpected audit count', len(audit),
            [(event.cid, event.content_type.model, event.action) for event in audit],
        )
        add_names = {
            'add-fixed-complete': 'Fixed full category',
            'add-defaults-nested-vat': 'Defaults nested VAT',
            'add-collaborator-update': 'Collaborator category',
            'add-admin-impersonated': 'Admin category',
            'add-collaborator-full': 'Full collaborator category',
            'add-coercions': 'Coercions',
            'add-unknown-field': 'Unknown field category',
            'add-deleted-archived': 'Deleted archived category',
            'add-unicode-name': 'à' * 255,
            'add-boolean-yes-coercion': 'Boolean yes',
            'add-duplicate-json': 'Duplicate JSON',
        }
        two_event_patch = {'patch-new-vat-id', 'patch-vat-create'}
        one_event_patch = {
            'patch-name-partial', 'patch-boolean-zero-false', 'patch-uppercase-uuid',
            'patch-archived-category', 'patch-deleted-category', 'patch-undelete',
            'patch-unarchive', 'patch-collaborator-update', 'patch-admin-impersonated',
            'patch-owner-impersonated-collaborator-update', 'patch-form',
            'patch-multipart', 'patch-query-ignored',
        }
        delete_cids = {
            'delete-owned', 'delete-referenced', 'delete-repeat-first',
            'delete-collaborator-update',
        }
        expected_counts = {
            **{name: 2 for name in add_names},
            **{name: 2 for name in two_event_patch},
            **{name: 1 for name in one_event_patch},
            **{name: 1 for name in delete_cids},
        }
        assert {event.cid for event in audit} == set(expected_counts)
        by_cid = {}
        for event in audit:
            by_cid.setdefault(event.cid, []).append(event)
        assert {cid: len(events) for cid, events in by_cid.items()} == expected_counts
        collab_mutations = {
            'add-collaborator-update', 'patch-collaborator-update', 'delete-collaborator-update',
        }
        admin_mutations = {'add-admin-impersonated', 'patch-admin-impersonated'}
        targets = {
            case['name']: next(
                (str(value) for value in initial_categories if str(value) in case.get('path', '').lower()),
                None,
            )
            for case in cases
            if case['name'] in one_event_patch | two_event_patch | delete_cids
        }
        for cid, events in by_cid.items():
            actor_id = (
                uid(1) if cid in admin_mutations else
                uid(160011) if cid == 'add-collaborator-full' else
                uid(160004) if cid in collab_mutations else
                uid(160000)
            )
            category_events = [event for event in events if event.content_type.model == 'paymentcategory']
            vat_events = [event for event in events if event.content_type.model == 'vatmanagement']
            assert len(category_events) == 1, (cid, 'category audit count')
            assert len(vat_events) == int(cid in add_names or cid in two_event_patch), (cid, 'VAT audit count')
            category_event = category_events[0]
            assert category_event.action == (0 if cid in add_names else 1), (cid, category_event.action)
            if cid in add_names:
                added_category = PaymentCategory._base_manager.get(pk=category_event.object_pk)
                assert added_category.name == add_names[cid]
                assert 'name' in category_event.changes_dict
            else:
                assert category_event.object_pk == targets[cid], (cid, category_event.object_pk, targets[cid])
            if cid in delete_cids:
                assert category_event.changes_dict == {'deleted': ['False', 'True']}, (cid, category_event.changes_dict)
            if cid == 'patch-name-partial':
                assert category_event.changes_dict.get('name') == ['Patch original', 'Patch renamed']
                assert 'creation_date' in category_event.changes_dict
            for vat_event in vat_events:
                assert vat_event.action == 0
                assert VatManagement._base_manager.filter(pk=vat_event.object_pk).exists()
            for event in events:
                assert str(event.actor_id) == actor_id, (cid, event.actor_id, actor_id)
                assert event.actor_email == User.original_objects.get(pk=actor_id).email
                assert event.remote_addr == expected_remote_addr, (cid, event.remote_addr, expected_remote_addr)
                indexes = list(AuditLogIndex.objects.filter(log_entry=event))
                if event.content_type.model == 'paymentcategory':
                    assert len(indexes) == 1, (cid, 'category index count')
                    assert str(indexes[0].sport_association_id) == uid(161000)
                    assert indexes[0].resolution_path == 'direct'
                else:
                    expected_index = actor_id == uid(160000)
                    assert len(indexes) == int(expected_index), (cid, 'VAT index count')
                    if expected_index:
                        assert str(indexes[0].sport_association_id) == uid(161000)
                        assert indexes[0].resolution_path == 'actor.sport_association'
                if cid in admin_mutations:
                    assert event.additional_data == {
                        'impersonation_session': session_map['admin-owner'],
                        'actor_id': uid(1),
                        'target_user_id': uid(160000),
                        'association_id': None,
                    }
                elif cid == 'patch-owner-impersonated-collaborator-update':
                    assert event.additional_data == {
                        'impersonation_session': session_map['owner-collaborator-update'],
                        'actor_id': uid(160000),
                        'target_user_id': uid(160004),
                        'association_id': uid(161000),
                    }
                else:
                    assert event.additional_data is None, (cid, event.additional_data)
        return audit

    def managed_delta(before):
        after = managed_snapshot()
        for label, rows in original.items():
            assert all(row in after[label] for row in rows), f'pre-existing unrelated records changed in {label}'
        changed = {}
        for label in sorted(set(before) | set(after)):
            old, new = before.get(label, []), after.get(label, [])
            if old != new:
                changed[label] = {'before': len(old), 'after': len(new)}
        allowed = {
            'application.PaymentCategory', 'application.VatManagement',
            'auditlog.LogEntry', 'application.AuditLogIndex',
        }
        assert set(changed) <= allowed, f'unexpected managed-table mutations: {changed}'
        return changed

    def query_probe(base):
        token = tokens[160000]
        sizes = (1, 8, 32)
        baseline_count = PaymentCategory.objects.filter(
            Q(sport_association=own_association) | Q(sport_association=None)
        ).count()
        counts = {}
        extra_ids = []
        extra_vat_ids = []
        try:
            for size in sizes:
                target = baseline_count + size
                need = target - PaymentCategory.objects.filter(
                    Q(sport_association=own_association) | Q(sport_association=None)
                ).count()
                with disable_auditlog():
                    for i in range(max(0, need)):
                        vat = VatManagement.objects.create(
                            vat_management_id=uid(175500 + len(extra_vat_ids)), vat=16, deduction=2,
                        )
                        extra_vat_ids.append(vat.pk)
                        row = PaymentCategory.objects.create(
                            payment_category_id=uid(175000 + len(extra_ids)),
                            name=f'Query {size} {i}', sport_association=own_association, vat_management=vat,
                        )
                        extra_ids.append(row.pk)
                with CaptureQueriesContext(connection) as queries:
                    response = APIClient().get(list_path, HTTP_AUTHORIZATION='Bearer ' + token)
                assert response.status_code == 200
                counts[size] = len(queries)
        finally:
            with disable_auditlog():
                PaymentCategory.objects.filter(pk__in=extra_ids).delete()
                VatManagement.objects.filter(pk__in=extra_vat_ids).delete()
        patch_counts = {}
        for size in sizes:
            target = uid(175100 + size)
            extras = [uid(175200 + size * 100 + index) for index in range(size)]
            case_name = f'query-patch-{size}'
            with disable_auditlog():
                PaymentCategory.objects.bulk_create([
                    PaymentCategory(payment_category_id=target, name=f'Query patch target {size}',
                                    sport_association=own_association),
                    *[
                        PaymentCategory(payment_category_id=pk, name=f'Query patch extra {size} {index}',
                                        sport_association=own_association)
                        for index, pk in enumerate(extras)
                    ],
                ])
            try:
                with CaptureQueriesContext(connection) as queries:
                    response = APIClient().patch(
                        f'/payment/category/{target}/update',
                        {'name': f'Query patch changed {size}'},
                        format='json',
                        HTTP_AUTHORIZATION='Bearer ' + token,
                        HTTP_X_CORRELATION_ID=case_name,
                    )
                assert response.status_code == 200
                patch_counts[size] = len(queries)
            finally:
                with disable_auditlog():
                    LogEntry.objects.filter(cid=case_name).delete()
                    PaymentCategory.objects.filter(pk__in=[target, *extras]).delete()
        result = {'list': counts, 'patch': patch_counts}
        if len(set(counts.values())) != 1:
            defects.append({
                'id': 'list-query-growth',
                'observed': counts,
                'expected': 'constant query count as visible category count grows',
            })
        if len(set(patch_counts.values())) != 1:
            defects.append({
                'id': 'patch-query-growth',
                'observed': patch_counts,
                'expected': 'constant query count as unrelated category count grows',
            })
        return result

    def fetch_latest_num(name):
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM pg_class WHERE relname = %s', [name])
            return cursor.fetchone()[0]

    def fault_probes(base):
        observed = {}
        function = 'payment_category_reject_test'
        with connection.cursor() as cursor:
            cursor.execute(f'DROP FUNCTION IF EXISTS {function}() CASCADE')
            cursor.execute(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'synthetic payment-category failure'; END $$"
            )
            cursor.execute(
                f"CREATE TRIGGER payment_category_reject_category BEFORE INSERT ON application_paymentcategory "
                f"FOR EACH ROW WHEN (NEW.name = 'Fault category') EXECUTE FUNCTION {function}()"
            )
        try:
            before_vats = set(VatManagement.objects.values_list('pk', flat=True))
            before_audit = set(LogEntry.objects.values_list('pk', flat=True))
            before_fault = managed_snapshot()
            status, response = Client(base, tokens[160000]).request(
                'POST', add_path,
                {'name': 'Fault category', 'vat_management': {'vat': 16, 'deduction': 2}},
                {'X-Correlation-ID': 'fault-category-insert', 'X-Forwarded-For': '198.51.100.91'},
            )
            assert status == 500, ('fault-category-insert', status, response)
            assert managed_snapshot() == before_fault, 'category INSERT failure did not roll back the complete managed state'
            observed['category-insert'] = {
                'status': status,
                'category_exists': PaymentCategory.objects.filter(name='Fault category').exists(),
                'new_vat_exists': VatManagement.objects.exclude(pk__in=before_vats).exists(),
                'new_audit_exists': LogEntry.objects.exclude(pk__in=before_audit).exists(),
                'new_index_exists': AuditLogIndex.objects.filter(
                    log_entry__in=LogEntry.objects.exclude(pk__in=before_audit)
                ).exists(),
            }
            if any(observed['category-insert'][key] for key in (
                'new_vat_exists', 'new_audit_exists', 'new_index_exists',
            )):
                defects.append({
                    'id': 'add-partial-vat-on-category-failure',
                    'observed': observed['category-insert'],
                    'expected': '500/error with no PaymentCategory or VatManagement row',
                })
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP FUNCTION IF EXISTS {function}() CASCADE')
            with disable_auditlog():
                PaymentCategory.objects.filter(name='Fault category').delete()
                VatManagement.objects.exclude(pk__in=before_vats).delete()
                LogEntry.objects.exclude(pk__in=before_audit).delete()

        audit_target = uid(176000)
        with disable_auditlog():
            PaymentCategory.objects.create(
                payment_category_id=audit_target, name='Audit failure target',
                sport_association=own_association,
            )
        with connection.cursor() as cursor:
            cursor.execute(f'DROP FUNCTION IF EXISTS {function}() CASCADE')
            cursor.execute(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'synthetic payment-category audit failure'; END $$"
            )
            cursor.execute(
                f"CREATE TRIGGER payment_category_reject_audit BEFORE INSERT ON auditlog_logentry "
                f"FOR EACH ROW WHEN (NEW.object_pk = '{audit_target}') EXECUTE FUNCTION {function}()"
            )
        try:
            before_audit = set(LogEntry.objects.values_list('pk', flat=True))
            before_fault = managed_snapshot()
            status, response = Client(base, tokens[160000]).request(
                'PATCH', f'/payment/category/{audit_target}/update',
                {'name': 'Audit failed'}, {'X-Correlation-ID': 'fault-audit-insert'},
            )
            assert status == 500, ('fault-audit-insert', status, response)
            assert managed_snapshot() == before_fault, 'audit INSERT failure did not roll back the complete managed state'
            observed['audit-insert'] = {
                'status': status,
                'name': PaymentCategory._base_manager.get(pk=audit_target).name,
                'new_audit_exists': LogEntry.objects.exclude(pk__in=before_audit).exists(),
            }
            if observed['audit-insert']['name'] != 'Audit failure target' or observed['audit-insert']['new_audit_exists']:
                defects.append({
                    'id': 'update-committed-before-audit',
                    'observed': observed['audit-insert'],
                    'expected': '500/error with category name unchanged',
                })
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP FUNCTION IF EXISTS {function}() CASCADE')
            with disable_auditlog():
                PaymentCategory.objects.filter(pk=audit_target).delete()

        index_target = uid(176001)
        with disable_auditlog():
            PaymentCategory.objects.create(
                payment_category_id=index_target, name='Audit index target',
                sport_association=own_association,
            )
        with connection.cursor() as cursor:
            cursor.execute(f'DROP FUNCTION IF EXISTS {function}() CASCADE')
            cursor.execute(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'synthetic payment-category index failure'; END $$"
            )
            cursor.execute(
                f"CREATE TRIGGER payment_category_reject_index BEFORE INSERT ON application_auditlogindex "
                f"FOR EACH ROW WHEN (NEW.sport_association_id = '{uid(161000)}'::uuid) "
                f"EXECUTE FUNCTION {function}()"
            )
        try:
            before_fault = managed_snapshot()
            status, response = Client(base, tokens[160000]).request(
                'PATCH', f'/payment/category/{index_target}/update',
                {'name': 'Audit index failed'}, {'X-Correlation-ID': 'fault-audit-index'},
            )
            assert status == 500, ('fault-audit-index', status, response)
            assert managed_snapshot() == before_fault, 'audit-index INSERT failure did not roll back the complete managed state'
            event = LogEntry.objects.filter(cid='fault-audit-index').order_by('-id').first()
            observed['audit-index'] = {
                'status': status,
                'name': PaymentCategory._base_manager.get(pk=index_target).name,
                'audit_created': event is not None,
                'index_created': bool(event and AuditLogIndex.objects.filter(log_entry=event).exists()),
            }
            if (
                observed['audit-index']['name'] != 'Audit index target' or
                (observed['audit-index']['status'] < 500 and observed['audit-index']['audit_created'] and
                 not observed['audit-index']['index_created'])
            ):
                defects.append({
                    'id': 'update-succeeds-without-audit-index',
                    'observed': observed['audit-index'],
                    'expected': 'category and audit index commit atomically, or both roll back',
                })
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP FUNCTION IF EXISTS {function}() CASCADE')
            with disable_auditlog():
                LogEntry.objects.filter(cid='fault-audit-index').delete()
                PaymentCategory.objects.filter(pk=index_target).delete()
        matrix = (
            ('add-vat-insert', 'POST', 'vat', False),
            ('add-vat-audit-insert', 'POST', 'vat-audit', False),
            ('add-vat-index-insert', 'POST', 'vat-index', False),
            ('add-category-audit-insert', 'POST', 'category-audit', False),
            ('add-category-index-insert', 'POST', 'category-index', False),
            ('patch-category-update-after-vat', 'PATCH', 'category', False),
            ('patch-vat-insert', 'PATCH', 'vat', False),
            ('patch-vat-audit-insert', 'PATCH', 'vat-audit', False),
            ('patch-vat-index-insert', 'PATCH', 'vat-index', False),
            ('patch-category-audit-after-vat', 'PATCH', 'category-audit', False),
            ('patch-category-index-after-vat', 'PATCH', 'category-index', False),
            ('delete-category-update', 'DELETE', 'category', False),
            ('delete-audit-insert', 'DELETE', 'category-audit', False),
            ('delete-index-insert', 'DELETE', 'category-index', False),
            ('repeat-delete-category-update', 'DELETE', 'category', True),
        )
        for index, (name, method, stage, already_deleted) in enumerate(matrix):
            category_id, vat_id = uid(186000 + index * 2), uid(186001 + index * 2)
            assert not PaymentCategory._base_manager.filter(pk=category_id).exists()
            assert not VatManagement._base_manager.filter(pk=vat_id).exists()
            if method != 'POST':
                with disable_auditlog():
                    PaymentCategory.objects.create(
                        payment_category_id=category_id, name='Matrix original',
                        sport_association=own_association, vat_management=vats['own-active'],
                        deleted=already_deleted,
                    )
            if stage == 'vat':
                table, operation = 'application_vatmanagement', 'INSERT'
                condition = f"NEW.vat_management_id = '{vat_id}'::uuid"
            elif stage == 'category':
                table, operation = 'application_paymentcategory', 'UPDATE'
                condition = f"NEW.payment_category_id = '{category_id}'::uuid"
            else:
                audit_object = vat_id if stage.startswith('vat-') else category_id
                operation = 'INSERT'
                if stage.endswith('-audit'):
                    table = 'auditlog_logentry'
                    condition = f"NEW.object_pk = '{audit_object}'"
                else:
                    table = 'application_auditlogindex'
                    condition = f"EXISTS (SELECT 1 FROM auditlog_logentry WHERE id=NEW.log_entry_id AND object_pk='{audit_object}')"
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN IF {condition} THEN RAISE EXCEPTION 'synthetic matrix failure'; END IF;
                        RETURN NEW; END $$''')
                    cursor.execute(f'''CREATE TRIGGER payment_category_matrix_failure BEFORE {operation}
                        ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()''')
                before_fault = managed_snapshot()
                body = None if method == 'DELETE' else {
                    'name': 'Matrix must roll back',
                    'vat_management': {'vat_management_id': vat_id, 'vat': 8, 'deduction': 1},
                }
                if method == 'POST':
                    body['payment_category_id'] = category_id
                target = add_path if method == 'POST' else f'/payment/category/{category_id}/{"delete" if method == "DELETE" else "update"}'
                status, response = Client(base, tokens[160000]).request(
                    method, target, body, {'X-Correlation-ID': 'fault-' + name},
                )
                assert status == 500, (name, status, response)
                assert managed_snapshot() == before_fault, (name, 'managed state survived a failed atomic mutation')
                observed[name] = {'status': status, 'stage': stage, 'method': method,
                                  'already_deleted': already_deleted, 'exact_managed_rollback': True}
            finally:
                with connection.cursor() as cursor:
                    cursor.execute(f'DROP FUNCTION IF EXISTS {function}() CASCADE')
                with disable_auditlog():
                    LogEntry.objects.filter(object_pk__in=[category_id, vat_id]).delete()
                    PaymentCategory._base_manager.filter(pk=category_id).delete()
                    VatManagement._base_manager.filter(pk=vat_id).delete()
        return observed

    def await_blocked_mutations(blocker_pid):
        deadline = time.monotonic() + 10
        observed = []
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_stat_clear_snapshot()')
                cursor.execute("""SELECT pid, pg_blocking_pids(pid) FROM pg_stat_activity
                    WHERE datname=current_database() AND state='active' AND wait_event_type='Lock'""")
                observed = cursor.fetchall()
            # The second request may wait on the first request's owner lock,
            # while the first waits on our category lock. Prove both are queued
            # in this lock chain before releasing the original transaction.
            chain = {blocker_pid}
            for _ in observed:
                chain.update(pid for pid, blockers in observed if any(parent in chain for parent in blockers))
            waiting = sorted(chain - {blocker_pid})
            if len(waiting) >= 2:
                return waiting
            time.sleep(.01)
        raise AssertionError(f'two mutations did not reach the held lock chain: blocker={blocker_pid}, observed={observed}')

    def concurrency_probe(base):
        target = uid(176100)
        with disable_auditlog():
            PaymentCategory.objects.create(
                payment_category_id=target, name='Concurrent initial',
                sport_association=own_association,
            )
        events = set(LogEntry.objects.values_list('pk', flat=True))
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                with transaction.atomic():
                    PaymentCategory._base_manager.select_for_update().get(pk=target)
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_backend_pid()')
                        blocker_pid = cursor.fetchone()[0]
                    futures = [
                        pool.submit(
                            Client(base, tokens[160000]).request,
                            'PATCH', f'/payment/category/{target}/update',
                            {'name': f'Concurrent writer {index}'},
                            {'X-Correlation-ID': f'concurrency-writer-{index}'},
                        )
                        for index in (1, 2)
                    ]
                    blocked_update_pids = await_blocked_mutations(blocker_pid)
                results = [future.result(timeout=20) for future in futures]
            assert all(status == 200 for status, _ in results), results
            assert [response['data']['name'] for _, response in results] == [
                'Concurrent writer 1', 'Concurrent writer 2',
            ], results
            final_name = PaymentCategory._base_manager.get(pk=target).name
            audits = list(LogEntry.objects.exclude(pk__in=events).filter(object_pk=target).order_by('id'))
            observed = {
                'statuses': [status for status, _ in results],
                'final_name': final_name,
                'audits': [event.changes_dict for event in audits],
                'observed_blocked_backend_pids': blocked_update_pids,
            }
            valid_histories = [
                [
                    {'name': ['Concurrent initial', 'Concurrent writer 1']},
                    {'name': ['Concurrent writer 1', 'Concurrent writer 2']},
                ],
                [
                    {'name': ['Concurrent initial', 'Concurrent writer 2']},
                    {'name': ['Concurrent writer 2', 'Concurrent writer 1']},
                ],
            ]
            if len(audits) != 2 or observed['audits'] not in valid_histories:
                defects.append({
                    'id': 'concurrent-update-history',
                    'observed': observed,
                    'expected': 'two serialized category changes and two non-stale audit changes',
                })
            first = observed
            race_target = uid(176101)
            with disable_auditlog():
                PaymentCategory.objects.create(
                    payment_category_id=race_target, name='Update delete initial',
                    sport_association=own_association,
                )
            race_events = set(LogEntry.objects.values_list('pk', flat=True))
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    with transaction.atomic():
                        PaymentCategory._base_manager.select_for_update().get(pk=race_target)
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            blocker_pid = cursor.fetchone()[0]
                        race_futures = [
                            pool.submit(
                                Client(base, tokens[160000]).request,
                                'PATCH', f'/payment/category/{race_target}/update',
                                {'name': 'Update delete patched'},
                                {'X-Correlation-ID': 'concurrency-update-delete-patch'},
                            ),
                            pool.submit(
                                Client(base, tokens[160000]).request,
                                'DELETE', f'/payment/category/{race_target}/delete', None,
                                {'X-Correlation-ID': 'concurrency-update-delete-delete'},
                            ),
                        ]
                        blocked_update_delete_pids = await_blocked_mutations(blocker_pid)
                    race_results = [future.result(timeout=20) for future in race_futures]
                race_row = PaymentCategory._base_manager.get(pk=race_target)
                race_audits = list(
                    LogEntry.objects.exclude(pk__in=race_events).filter(object_pk=race_target).order_by('id')
                )
                second = {
                    'statuses': [status for status, _ in race_results],
                    'name': race_row.name,
                    'deleted': race_row.deleted,
                    'audits': [event.changes_dict for event in race_audits],
                    'observed_blocked_backend_pids': blocked_update_delete_pids,
                }
                expected_race_changes = [
                    {'name': ['Update delete initial', 'Update delete patched']},
                    {'deleted': ['False', 'True']},
                ]
                exact_race_history = sorted(
                    (event.changes_dict for event in race_audits), key=lambda change: json.dumps(change, sort_keys=True),
                ) == sorted(expected_race_changes, key=lambda change: json.dumps(change, sort_keys=True))
                if (any(status != 200 for status, _ in race_results) or not race_row.deleted or
                        race_row.name != 'Update delete patched' or len(race_audits) != 2 or not exact_race_history):
                    defects.append({
                        'id': 'concurrent-update-delete',
                        'observed': second,
                        'expected': 'both operations serialize, preserve the named update and leave deleted=true with two audits',
                    })
            finally:
                with disable_auditlog():
                    LogEntry.objects.exclude(pk__in=race_events).filter(object_pk=race_target).delete()
                    PaymentCategory.objects.filter(pk=race_target).delete()
            return {'update': first, 'update_delete': second}
        finally:
            with disable_auditlog():
                LogEntry.objects.exclude(pk__in=events).filter(object_pk=target).delete()
                PaymentCategory.objects.filter(pk=target).delete()

    def tenant_bug_probes(base):
        before_foreign = str(categories['tenant-rewrite'].sport_association_id)
        status, response = Client(base, tokens[160000]).request(
            'PATCH', update_path(170017), {'sport_association': uid(161001)},
            {'X-Correlation-ID': 'tenant-rewrite-probe'},
        )
        categories['tenant-rewrite'].refresh_from_db()
        after_foreign = str(categories['tenant-rewrite'].sport_association_id)
        status_null, response_null = Client(base, tokens[160000]).request(
            'PATCH', update_path(170018), {'sport_association': None},
            {'X-Correlation-ID': 'tenant-null-probe'},
        )
        categories['tenant-null'].refresh_from_db()
        after_null = categories['tenant-null'].sport_association_id
        with disable_auditlog():
            PaymentCategory.objects.filter(pk=categories['tenant-rewrite'].pk).update(
                sport_association_id=before_foreign,
            )
            PaymentCategory.objects.filter(pk=categories['tenant-null'].pk).update(
                sport_association_id=own_association.pk,
            )
        malformed_update = Client(base, tokens[160000]).request(
            'PATCH', '/payment/category/not-a-uuid/update', {'name': 'Invalid'},
            {'X-Correlation-ID': 'malformed-uuid-update'},
        )
        malformed_delete = Client(base, tokens[160000]).request(
            'DELETE', '/payment/category/not-a-uuid/delete', None,
            {'X-Correlation-ID': 'malformed-uuid-delete'},
        )
        list_body = Client(base, tokens[160000]).request(
            'POST', add_path, [],
            {'X-Correlation-ID': 'malformed-body-list'},
        )
        null_body = Client(base, tokens[160000]).request(
            'POST', add_path, 'null'.encode(),
            {'Content-Type': 'application/json', 'X-Correlation-ID': 'malformed-body-null'},
        )
        if after_foreign != before_foreign:
            defects.append({
                'id': 'patch-foreign-tenant-rewrite',
                'status': status, 'before': before_foreign, 'after': after_foreign,
                'response': response, 'expected': 'deny or preserve authenticated tenant',
            })
        if after_null is None:
            defects.append({
                'id': 'patch-null-association-globalizes-category',
                'status': status_null, 'before': str(own_association.pk), 'after': None,
                'response': response_null, 'expected': 'deny or preserve authenticated tenant',
            })
        if malformed_update[0] >= 500 or malformed_delete[0] >= 500:
            defects.append({
                'id': 'malformed-uuid-path-500',
                'observed': {
                    'update_status': malformed_update[0],
                    'delete_status': malformed_delete[0],
                },
                'expected': 'known baseline HTML 500, explicitly asserted with no mutation in the declared cases',
            })
        if list_body[0] >= 500 or null_body[0] >= 500:
            defects.append({
                'id': 'malformed-add-object-500',
                'observed': {
                    'list_status': list_body[0],
                    'null_status': null_body[0],
                },
                'expected': 'known baseline HTML 500, explicitly asserted with no mutation in the declared cases',
            })
        return {'foreign': {'status': status, 'after': after_foreign},
                'null': {'status': status_null, 'after': after_null},
                'malformed': {
                    'update_status': malformed_update[0],
                    'delete_status': malformed_delete[0],
                    'responses': [malformed_update[1], malformed_delete[1]],
                },
                'malformed_add_bodies': {
                    'list_status': list_body[0],
                    'null_status': null_body[0],
                    'responses': [list_body[1], null_body[1]],
                }}

    def no_association_probe(base):
        before_vats = set(VatManagement.objects.values_list('pk', flat=True))
        global_update = uid(176200)
        global_delete = uid(176201)
        with disable_auditlog():
            PaymentCategory.objects.bulk_create([
                PaymentCategory(payment_category_id=global_update, name='Ownerless update probe'),
                PaymentCategory(payment_category_id=global_delete, name='Ownerless delete probe'),
            ])
        status, response = Client(base, tokens[160007]).request(
            'POST', add_path, {'name': 'No association add', 'vat_management': {}},
            {'X-Correlation-ID': 'owner-no-association-add'},
        )
        update_status, update_response = Client(base, tokens[160007]).request(
            'PATCH', f'/payment/category/{global_update}/update', {'name': 'Ownerless global patched'},
            {'X-Correlation-ID': 'owner-no-association-update'},
        )
        delete_status, delete_response = Client(base, tokens[160007]).request(
            'DELETE', f'/payment/category/{global_delete}/delete', None,
            {'X-Correlation-ID': 'owner-no-association-delete'},
        )
        global_update_row = PaymentCategory._base_manager.get(pk=global_update)
        global_delete_row = PaymentCategory._base_manager.get(pk=global_delete)
        observed = {
            'add': {'status': status, 'created': PaymentCategory.objects.filter(name='No association add').exists()},
            'update': {
                'status': update_status,
                'name': global_update_row.name,
                'response': update_response,
            },
            'delete': {
                'status': delete_status,
                'deleted': global_delete_row.deleted,
                'response': delete_response,
            },
        }
        if status >= 500:
            defects.append({'id': 'owner-no-association-add-500', 'observed': observed['add'],
                            'expected': 'stable 4xx denial without mutation'})
        if update_status == 200 and global_update_row.name == 'Ownerless global patched':
            defects.append({'id': 'owner-without-association-updates-global', 'observed': observed['update'],
                            'expected': '4xx denial; a missing owner association must not authorize global writes'})
        if delete_status == 200 and global_delete_row.deleted:
            defects.append({'id': 'owner-without-association-deletes-global', 'observed': observed['delete'],
                            'expected': '4xx denial; a missing owner association must not authorize global writes'})
        with disable_auditlog():
            PaymentCategory.objects.filter(
                Q(name='No association add') | Q(pk__in=[global_update, global_delete])
            ).delete()
            VatManagement.objects.exclude(pk__in=before_vats).delete()
        return observed

    fatal_defect_ids = {
        'add-partial-vat-on-category-failure',
        'update-committed-before-audit',
        'update-succeeds-without-audit-index',
        'concurrent-update-history',
        'concurrent-update-delete',
        'patch-foreign-tenant-rewrite',
        'patch-null-association-globalizes-category',
        'owner-no-association-add-500',
        'owner-without-association-updates-global',
        'owner-without-association-deletes-global',
        'malformed-transport-does-not-match-django',
    }

    def require_mutation_invariants():
        failures = [defect for defect in defects if defect.get('id') in fatal_defect_ids]
        if failures:
            raise AssertionError(f'payment-category mutation invariants failed: {failures}')

    django_application = get_asgi_application()
    observed_lifecycle_requests = {}
    observation_lock = threading.Lock()

    async def observed_django_application(scope, receive, send):
        if scope['type'] == 'http':
            cid = next((value.decode('latin1') for key, value in scope.get('headers', [])
                        if key.lower() == b'x-correlation-id'), '')
            if cid.startswith(('payment-category-lifecycle-', 'payment-category-transaction-')):
                with observation_lock:
                    observed_lifecycle_requests.setdefault(cid, []).append({
                        'method': scope['method'], 'path': scope['path'],
                    })
        await django_application(scope, receive, send)

    def django_requests(cid):
        with observation_lock:
            return list(observed_lifecycle_requests.get(cid, []))

    server = uvicorn.Server(uvicorn.Config(
        observed_django_application, host='0.0.0.0' if versioned else '127.0.0.1',
        port=0, lifespan='off', log_level='critical',
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    runtimes = ExitStack()
    go_runtime = None
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Django payment-category ASGI server failed to start')
            time.sleep(.01)
        django_base = f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        before_cases = managed_snapshot()
        execute_declared_cases(django_base)
        contract_count = len(cases)
        audit = verify_persistence()
        delta = managed_delta(before_cases)
        query_counts = query_probe(django_base)
        faults = fault_probes(django_base)
        concurrency = concurrency_probe(django_base)
        tenant = tenant_bug_probes(django_base)
        no_association = no_association_probe(django_base)
        audit_evidence = audit_records(audit)
        persistence_evidence = persistence_records()
        legacy_red = [
            {
                'id': 'malformed-uuid-patch',
                'expected_legacy_status': 500,
                'observed': tenant['malformed']['update_status'],
                'response': tenant['malformed']['responses'][0],
                'mutation': 'none',
            },
            {
                'id': 'malformed-uuid-delete',
                'expected_legacy_status': 500,
                'observed': tenant['malformed']['delete_status'],
                'response': tenant['malformed']['responses'][1],
                'mutation': 'none',
            },
            {
                'id': 'malformed-add-list-body',
                'expected_legacy_status': 500,
                'observed': tenant['malformed_add_bodies']['list_status'],
                'response': tenant['malformed_add_bodies']['responses'][0],
                'mutation': 'none',
            },
            {
                'id': 'malformed-add-null-body',
                'expected_legacy_status': 500,
                'observed': tenant['malformed_add_bodies']['null_status'],
                'response': tenant['malformed_add_bodies']['responses'][1],
                'mutation': 'none',
            },
        ]
        print(
            f'Django: {contract_count} payment-category HTTP cases, {len(audit)} audit rows, '
            f'managed delta {delta}, query counts {query_counts}, defects {len(defects)}',
            flush=True,
        )
        fixture['oracle'] = {
            'backend': 'Django',
            'http_cases': contract_count,
            'audit_rows': len(audit),
            'managed_delta': delta,
            'audit_evidence': audit_evidence,
            'final_records': persistence_evidence,
            'legacy_red': legacy_red,
            'query_counts_by_visible_categories': query_counts,
            'fault_probes': faults,
            'concurrency_probe': concurrency,
            'tenant_probes': tenant,
            'owner_no_association_probe': no_association,
            'defects': list(defects),
            'django_only': not run_go,
        }
        django_defects = list(defects)
        persist_fixture()
        require_mutation_invariants()
        go_result = None
        if run_go:
            reset_fixture_for_replay()
            assert managed_snapshot() == before_cases, 'Django fixture reset is not exact'
            if go_server_factory is None:
                from TESTS.adapters.go.payment_category_server import native_server
                go_server_factory = native_server
            go_runtime = runtimes.enter_context(go_server_factory(
                work, log, tool_env, address, versioned=versioned,
            ))
            go_base = go_runtime['base_url']
            execute_declared_cases(go_base)
            go_audit = verify_persistence()
            go_delta = managed_delta(before_cases)
            go_persistence = persistence_records()
            go_audit_evidence = audit_records(go_audit)
            assert normalize_audit_evidence(go_audit_evidence) == normalize_audit_evidence(audit_evidence), (
                'Go audit content differs from independently asserted Django audit content'
            )
            assert normalize_persistence_evidence(go_persistence, go_audit_evidence) == normalize_persistence_evidence(persistence_evidence, audit_evidence), (
                'Go full persisted category/VAT/reference state differs from Django'
            )
            go_faults = fault_probes(go_base)
            from TESTS.adapters.django.payment_category_transaction_failures import run as transaction_failures
            go_transaction_failures = transaction_failures(
                go_base, owner_id=owner.pk, association_id=own_association.pk,
                token=tokens[160000], snapshot=managed_snapshot, django_requests=django_requests,
                transport='native-go',
                evidence_path=ROOT / 'quality-reports/go-migration/payment-categories/transaction-failures-native-go.json',
            )
            go_concurrency = concurrency_probe(go_base)
            go_tenant = tenant_bug_probes(go_base)
            go_no_association = no_association_probe(go_base)
            if (
                go_tenant['malformed']['update_status'] != tenant['malformed']['update_status'] or
                go_tenant['malformed']['delete_status'] != tenant['malformed']['delete_status'] or
                go_tenant['malformed_add_bodies']['list_status'] != tenant['malformed_add_bodies']['list_status'] or
                go_tenant['malformed_add_bodies']['null_status'] != tenant['malformed_add_bodies']['null_status']
            ):
                defects.append({
                    'id': 'malformed-transport-does-not-match-django',
                    'observed': {
                        'django': {
                            'uuid': tenant['malformed'],
                            'add_body': tenant['malformed_add_bodies'],
                        },
                        'go': {
                            'uuid': go_tenant['malformed'],
                            'add_body': go_tenant['malformed_add_bodies'],
                        },
                    },
                    'expected': 'explicitly reconciled malformed-request transport behavior',
                })
            go_defects = list(defects[len(django_defects):])
            go_result = {
                'backend': 'Go',
                'runtime': go_runtime['metadata'],
                'http_cases': len(cases),
                'audit_rows': len(go_audit),
                'audit_evidence': go_audit_evidence,
                'final_records': go_persistence,
                'managed_delta': go_delta,
                'fault_probes': go_faults,
                'transaction_failure_probes': go_transaction_failures,
                'concurrency_probe': go_concurrency,
                'tenant_probes': go_tenant,
                'owner_no_association_probe': go_no_association,
                'defects': go_defects,
            }
            fixture['oracle']['go'] = go_result
            fixture['oracle']['defects'] = django_defects
            fixture['oracle']['all_defects'] = list(defects)
            persist_fixture()
            require_mutation_invariants()
        lifecycle_results = None
        if lifecycle:
            from TESTS.adapters.django.payment_category_lifecycle import run as run_lifecycle
            lifecycle_results = {}
            backends = ([('Go', go_base)] if run_go else []) + [('Django', django_base)]
            for backend, base in backends:
                lifecycle_results[backend] = run_lifecycle(
                    base, backend, owner_id=owner.pk, association_id=own_association.pk,
                    token=tokens[160000], snapshot=managed_snapshot,
                    django_requests=django_requests,
                    evidence_path=ROOT / f'quality-reports/go-migration/payment-categories/lifecycle-{backend.lower()}.json',
                )
            fixture['oracle']['lifecycle'] = lifecycle_results
            lifecycle_disconnect_match = all(
                result.get('disconnect_semantics_match') is True for result in lifecycle_results.values()
            )
            fixture['oracle']['disconnect_semantics_match'] = lifecycle_disconnect_match
            fixture['oracle']['complete_payment_category_parity'] = False
            fixture['oracle']['lifecycle_remaining_parity_gaps'] = (
                [] if lifecycle_disconnect_match else
                ['Measured HTTP disconnect outcome differs from the authored admitted-mutation behavior.']
            )
            assert all(result['success'] for result in lifecycle_results.values()), (
                'payment-category lifecycle invariants failed; see lifecycle-{go,django}.json',
                {backend: [case for case in result['cases'] if not case['success']]
                 for backend, result in lifecycle_results.items()},
            )
            assert lifecycle_disconnect_match, ('payment-category disconnect semantics differ', lifecycle_results)
        versioned_results = None
        if versioned:
            from TESTS.adapters.caddy import proxy
            django_port = server.servers[0].sockets[0].getsockname()[1]
            with proxy(f'host.docker.internal:{django_port}', go_runtime['proxy_upstream'], log,
                       network=go_runtime.get('network')) as (gateway, caddy_image):
                health_status, _, health_headers = Client(gateway).request('GET', '/healthz', include_headers=True)
                assert health_status == 200
                measured_remote = health_headers.get('X-Fixture-Client-IP')
                assert measured_remote, 'proxy did not independently expose its client address'
                ipaddress.ip_address(measured_remote)
                expected_proxy_audit = [
                    {**event, 'remote_addr': measured_remote} for event in audit_evidence
                ]
                versioned_results = {'caddy_image': caddy_image, 'measured_client_ip': measured_remote, 'routes': {}}
                for route, backend in (('/api/v1', 'Django'), ('/api/v2', 'Go')):
                    reset_fixture_for_replay()
                    assert managed_snapshot() == before_cases, f'{route}: fresh fixture reset is not exact'
                    proxy_base = gateway + route
                    execute_declared_cases(proxy_base)
                    proxy_audit = audit_records(verify_persistence(measured_remote))
                    proxy_persistence = persistence_records()
                    assert normalize_audit_evidence(proxy_audit) == normalize_audit_evidence(expected_proxy_audit), (
                        f'{route}: proxy audit differs beyond independently measured remote_addr'
                    )
                    assert normalize_persistence_evidence(proxy_persistence, proxy_audit) == normalize_persistence_evidence(persistence_evidence, audit_evidence), (
                        f'{route}: proxy persisted state differs from native Django'
                    )
                    proxy_delta = managed_delta(before_cases)
                    proxy_faults = fault_probes(proxy_base)
                    proxy_concurrency = concurrency_probe(proxy_base)
                    require_mutation_invariants()
                    versioned_results['routes'][route] = {
                        'backend': backend, 'http_cases': len(cases), 'audit_rows': len(proxy_audit),
                        'audit_evidence': proxy_audit, 'final_records': proxy_persistence,
                        'managed_delta': proxy_delta, 'fault_probes': proxy_faults,
                        'concurrency_probe': proxy_concurrency,
                    }
                    if backend == 'Go':
                        versioned_results['routes'][route]['transaction_failure_probes'] = transaction_failures(
                            proxy_base, owner_id=owner.pk, association_id=own_association.pk,
                            token=tokens[160000], snapshot=managed_snapshot, django_requests=django_requests,
                            transport='caddy-v2', expected_remote_addr=measured_remote,
                            evidence_path=ROOT / 'quality-reports/go-migration/payment-categories/transaction-failures-caddy-v2.json',
                        )
                    if lifecycle:
                        proxy_lifecycle = run_lifecycle(
                            proxy_base, backend, owner_id=owner.pk, association_id=own_association.pk,
                            token=tokens[160000], snapshot=managed_snapshot,
                            evidence_path=ROOT / f'quality-reports/go-migration/payment-categories/lifecycle-caddy-{route.rsplit("/", 1)[1]}.json',
                            transport='caddy-' + route.rsplit('/', 1)[1], disconnect_only=True,
                            django_requests=django_requests, expected_remote_addr=measured_remote,
                        )
                        versioned_results['routes'][route]['disconnect'] = proxy_lifecycle
                        assert proxy_lifecycle['success'], ('Caddy disconnect invariant failed', proxy_lifecycle)
                        assert proxy_lifecycle['disconnect_semantics_match'], (
                            'Caddy disconnect parity invariant failed', proxy_lifecycle,
                        )
                    print(f'{backend} through shipped Caddy {route}: {len(cases)} HTTP cases and {len(proxy_audit)} exact audit rows passed', flush=True)
                if lifecycle:
                    # Deliberately route v2 to Django in a separate disposable
                    # Caddy. The observer must reject it even though Django can
                    # perform the same category mutation. No production routing
                    # or response headers are changed for the actual workloads.
                    django_upstream = f'host.docker.internal:{django_port}'
                    with proxy(django_upstream, django_upstream, log,
                               network=go_runtime.get('network')) as (wrong_gateway, _):
                        negative_control = run_lifecycle(
                            wrong_gateway + '/api/v2', 'Go', owner_id=owner.pk,
                            association_id=own_association.pk, token=tokens[160000], snapshot=managed_snapshot,
                            evidence_path=ROOT / 'quality-reports/go-migration/payment-categories/lifecycle-caddy-negative-control.json',
                            transport='caddy-v2-negative-control', disconnect_only=True,
                            django_requests=django_requests,
                        )
                    assert not negative_control['success'] and negative_control['cleanup_verified'], negative_control
                    assert len(negative_control['cases']) == 1, negative_control
                    assert negative_control['cases'][0].get('failure_kind') == 'upstream_mismatch', negative_control
                    versioned_results['disconnect_routing_negative_control'] = {
                        'success': True, 'expected_rejection': 'upstream_mismatch',
                        'observed': negative_control,
                    }
                    print('Caddy v2 disconnect routing negative control: PASS (actual Django fallback rejected)', flush=True)
                fixture['oracle']['versioned'] = versioned_results
        return _jsonable({
            'success': True,
            'scope': 'Django payment-category HTTP family; portable fixture ready for Go replay',
            'http_cases': contract_count,
            'audit_rows': len(audit),
            'managed_delta': delta,
            'query_counts': query_counts,
            'concurrency': concurrency,
            'defects': defects,
            'django_only': not run_go,
            'go': go_result,
            'versioned': versioned_results,
            'lifecycle': lifecycle_results,
            'disconnect_semantics_match': (
                all(result.get('disconnect_semantics_match') is True for result in lifecycle_results.values())
                if lifecycle_results else None
            ),
            'complete_payment_category_parity': False,
            'fixture': str(persistent_fixture),
        })
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        runtimes.close()
        for session in sessions:
            cache.delete(session_key(session))
        with disable_auditlog():
            User.original_objects.filter(pk=owner.pk).update(
                default_payment_category=None, default_payment_category_courses=None,
            )
            CampsAndRetreatsPeriodsService.objects.filter(pk=references['service_id']).delete()
            CampsAndRetreatsPeriod.objects.filter(pk=references['period_id']).delete()
            CampsAndRetreats.objects.filter(pk=references['camp_id']).delete()
            Payment._base_manager.filter(pk=references['payment_id']).delete()
            PaymentCategory._base_manager.filter(
                Q(pk__in=initial_categories) |
                Q(pk=fixed_category) |
                Q(name__in=[
                    'Defaults nested VAT', 'Reuse VAT', 'Collaborator category', 'Admin category',
                    'Coercions', 'Duplicate JSON', 'Fault category', 'Audit failure target',
                    'No association add',
                ]) |
                Q(sport_association_id__in=[uid(161000), uid(161001)])
            ).delete()
            VatManagement.objects.exclude(pk__in=original_vat_ids).delete()
            SportAssociation.original_objects.filter(
                pk__in=[uid(161000), uid(161001)]
            ).delete()
            Associate.objects.filter(pk__in=[uid(162000), uid(162001)]).delete()
            LogEntry.objects.exclude(pk__in=audit_baseline).delete()
            User.original_objects.filter(pk__in=seeded_ids).delete()
        assert not thread.is_alive(), 'Django payment-category server did not drain'
        assert managed_snapshot() == original, 'payment-category fixture cleanup changed pre-existing rows'
        if sys.exc_info()[0] is None:
            persist_fixture(publish=True)
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', action='store_true', help='Print serializer/model route metadata without database access')
    options = parser.parse_args()
    if not options.metadata:
        parser.error('service-backed execution is dispatched through TESTS/adapters/django/accounts.py --only payment-categories')
    print(json.dumps(metadata(), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
