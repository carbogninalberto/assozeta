"""Bounded source-authored cascade contracts for collaborator deletion.

These cases exercise representative current-schema DELETE effects through the
real owner-scoped HTTP endpoint. Expected rows, removals, audits, and indexes
are authored here from the source model declarations; they are not derived
from the collector or audit registry executing under test.
"""
from copy import deepcopy
from decimal import Decimal
import unittest
from uuid import UUID

from TESTS.contracts.test_collaborator_delete import assert_case


SERVER_ERROR = (
    '\n<!doctype html>\n<html lang="en">\n<head>\n'
    '  <title>Server Error (500)</title>\n</head>\n'
    '<body>\n  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n'
)


def uid(number):
    assert 750000 <= number <= 759999, ('Fixture ID outside assigned range', number)
    return f'a2000000-0000-4000-8000-{number:012d}'


def run(*, base, owner, foreign, association, person, seed, snapshot,
        cache_snapshot, models, created, report):
    from auditlog.models import LogEntry
    from django.contrib.auth.models import Group as AuthGroup, Permission
    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone
    from rest_framework_simplejwt.tokens import AccessToken

    from application.models import (
        Associate,
        BillingPayment,
        BillingSubscription,
        CollaborationInvites,
        Instructor,
        InstructorHours,
        PreviewAndCustomFeatures,
        SportAssociation,
        User,
        UsersOnboarding,
    )
    from application.models.audit_models import AuditLogIndex
    from communications.models import StaffBoardMessage
    from docmanager.models import Document
    from instance.models import DataRestore
    from notifications.models import NotificationsBroadcast, UserNotificationsBroadcast

    assert len(models) == 128, ('Expected all 128 current-schema managed models', len(models))
    access = AccessToken()
    access['user_id'] = str(owner.pk)
    token = str(access)
    cases = report.setdefault('cascade_cases', [])
    assert not cases, 'Cascade fixture helper must run once per report'

    def bulk_seed(model, pk, **values):
        row = model(pk=pk, **values)
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    def expected_snapshot(before, removals, updates, new_audits, new_indexes):
        result = {}
        for model in models:
            label = model._meta.label
            key = model._meta.pk.attname
            removed = removals.get(label, set())
            rows = [
                deepcopy(row) for row in before[label]
                if row[key] not in removed
            ]
            for row in rows:
                row.update(updates.get(label, {}).get(row[key], {}))
            if model is LogEntry:
                rows.extend(deepcopy(new_audits))
            elif model is AuditLogIndex:
                rows.extend(deepcopy(new_indexes))
            if model in (LogEntry, AuditLogIndex):
                rows.sort(key=lambda row: (
                    row[key].int if isinstance(row[key], UUID) else row[key]
                ))
            result[label] = rows
        return result

    def retained_row(after, model, pk):
        key = model._meta.pk.attname
        return next(
            (row for row in after[model._meta.label] if row[key] == pk),
            None,
        )

    def execute(name, target, removals, expected_audits, *,
                updates=None, retained=(), status=200):
        response = (
            {'msg': 'collaborator deleted.'}
            if status == 200 else SERVER_ERROR
        )
        case = {
            'name': name,
            'path': f'/collaborators/{target.pk}/delete',
            'token': token,
            'status': status,
            'response': response,
            'headers': {'Accept-Language': 'en'},
        }
        updates = updates or {}
        before, before_cache = snapshot(), cache_snapshot()

        assert_case(unittest.TestCase(), base, case)

        after, after_cache = snapshot(), cache_snapshot()
        audits = list(
            LogEntry._base_manager.filter(cid=name).order_by('pk').values()
        )
        expected_identities = sorted(
            (
                ContentType.objects.get_for_model(model).pk,
                str(pk),
            )
            for model, pk in expected_audits
        )
        actual_identities = sorted(
            (row['content_type_id'], row['object_pk']) for row in audits
        )
        assert actual_identities == expected_identities, (
            'Unexpected audit identities', name, actual_identities,
            expected_identities,
        )
        for audit in audits:
            assert audit['action'] == LogEntry.Action.DELETE, audit
            assert str(audit['actor_id']) == str(owner.pk), audit
            assert audit['cid'] == name, audit
            assert audit['changes'], audit
            assert 'password' not in audit['changes'], audit
            assert 'last_login' not in audit['changes'], audit

        audit_ids = [row['id'] for row in audits]
        indexes = list(
            AuditLogIndex._base_manager.filter(
                log_entry_id__in=audit_ids,
            ).values()
        )
        assert len(indexes) == len(expected_audits), (
            'Unexpected audit index count', name, indexes,
        )
        assert {row['log_entry_id'] for row in indexes} == set(audit_ids)
        assert all(
            row['sport_association_id'] == association.pk
            and row['resolution_path'] == 'actor.sport_association'
            for row in indexes
        ), ('Unexpected audit index attribution', name, indexes)

        expected = expected_snapshot(
            before, removals, updates, audits, indexes,
        )
        assert after == expected, ('Unexpected durable cascade effects', name)
        assert after_cache == before_cache, ('Unexpected cache bytes', name)

        retained = (
            (User, owner.pk),
            (User, foreign.pk),
            (SportAssociation, association.pk),
            *retained,
        )
        for model, pk in retained:
            assert retained_row(after, model, pk) is not None, (
                'Expected retained row disappeared', name,
                model._meta.label, pk,
            )
        for label, values in updates.items():
            for pk, changes in values.items():
                model = next(model for model in models if model._meta.label == label)
                row = retained_row(after, model, pk)
                assert row is not None, ('Expected updated row disappeared', name, label, pk)
                assert all(row[field] == value for field, value in changes.items()), (
                    'Unapplied boundary update', name, label, pk,
                )

        entry = {
            'name': name,
            'status': status,
            'target': str(target.pk),
            'expected_removed': {
                label: sorted(str(pk) for pk in pks)
                for label, pks in removals.items() if pks
            },
            'audit_count': len(audits),
            'audit_objects': [
                f'{row["content_type_id"]}:{row["object_pk"]}' for row in audits
            ],
            'audit_indexes': len(indexes),
            'managed_models_verified': len(models),
            'cache_bytes_unchanged': True,
        }
        cases.append(entry)
        print(
            f'{name}: HTTP{status}, audits={len(audits)}, '
            f'indexes={len(indexes)}, models={len(models)}',
            flush=True,
        )

    recursive_target = person(750001, 3, connected_user=owner)
    recursive_child = person(750002, 3, connected_user=recursive_target)
    recursive_target_onboarding = seed(
        UsersOnboarding, 750011, user=recursive_target, create_membership=True,
    )
    recursive_child_onboarding = seed(
        UsersOnboarding, 750012, user=recursive_child, view_settings=True,
    )
    recursive_target_invite = seed(
        CollaborationInvites, 750021, user=recursive_target,
        email='recursive-target-750021@example.test', accepted=True,
        token='synthetic-recursive-target-750021',
    )
    recursive_child_invite = seed(
        CollaborationInvites, 750022, user=recursive_child,
        email='recursive-child-750022@example.test', accepted=False,
        token='synthetic-recursive-child-750022',
    )
    execute(
        'collaborator-cascade-recursive-connected-750001',
        recursive_target,
        {
            'application.User': {recursive_target.pk, recursive_child.pk},
            'application.UsersOnboarding': {
                recursive_target_onboarding.pk, recursive_child_onboarding.pk,
            },
            'application.CollaborationInvites': {
                recursive_target_invite.pk, recursive_child_invite.pk,
            },
        },
        [
            (User, recursive_target.pk),
            (User, recursive_child.pk),
            (UsersOnboarding, recursive_target_onboarding.pk),
            (UsersOnboarding, recursive_child_onboarding.pk),
            (CollaborationInvites, recursive_target_invite.pk),
            (CollaborationInvites, recursive_child_invite.pk),
        ],
        retained=(
            (User, owner.pk),
            (User, foreign.pk),
        ),
    )

    simple_target = person(750003, 3, connected_user=owner)
    simple_document = seed(
        Document, 750051, filename='cascade-synthetic-750051.txt',
        filepath='synthetic/no-upload/750051',
        token=UUID(uid(750053)),
    )
    simple_broadcast = seed(
        NotificationsBroadcast, 750041,
        broadcast_name='cascade-retained-broadcast-750041',
    )
    simple_user_broadcast = seed(
        UserNotificationsBroadcast, 750042,
        user=simple_target, broadcast=simple_broadcast,
    )
    simple_instructor = seed(
        Instructor, 750031, user=simple_target,
        first_name='Cascade', last_name='Instructor',
    )
    simple_hours = seed(
        InstructorHours, 750032, instructor=simple_instructor,
        date=timezone.localdate(), hours=Decimal('1.00'),
        hourly_billing=Decimal('10.00'), document=simple_document,
    )
    simple_instructor_document = Instructor.documents.through(
        id=750052,
        instructor_id=simple_instructor.pk,
        document_id=simple_document.pk,
    )
    Instructor.documents.through._base_manager.bulk_create(
        [simple_instructor_document],
    )
    created.append(
        (Instructor.documents.through, simple_instructor_document.pk),
    )
    execute(
        'collaborator-cascade-document-broadcast-750003',
        simple_target,
        {
            'application.User': {simple_target.pk},
            'application.Instructor': {simple_instructor.pk},
            'application.InstructorHours': {simple_hours.pk},
            'application.Instructor_documents': {
                simple_instructor_document.pk,
            },
            'notifications.UserNotificationsBroadcast': {
                simple_user_broadcast.pk,
            },
        },
        [
            (User, simple_target.pk),
            (Instructor, simple_instructor.pk),
            (InstructorHours, simple_hours.pk),
            (UserNotificationsBroadcast, simple_user_broadcast.pk),
        ],
        retained=(
            (Document, simple_document.pk),
            (NotificationsBroadcast, simple_broadcast.pk),
        ),
    )

    shared_group = bulk_seed(
        AuthGroup, 750061, name='cascade-shared-auth-group-750061',
    )
    shared_permission = bulk_seed(
        Permission, 750062,
        name='Cascade shared permission 750062',
        codename='cascade_shared_permission_750062',
        content_type=ContentType.objects.get_for_model(User),
    )
    shared_feature = seed(
        PreviewAndCustomFeatures, 750063,
        name='Cascade shared feature 750063',
        description='Shared parent must survive user deletion',
    )
    auth_target = person(750004, 3, connected_user=owner)
    through_rows = [
        User.groups.through(
            id=750071, user_id=auth_target.pk, group_id=shared_group.pk,
        ),
        User.groups.through(
            id=750072, user_id=foreign.pk, group_id=shared_group.pk,
        ),
        User.user_permissions.through(
            id=750073, user_id=auth_target.pk,
            permission_id=shared_permission.pk,
        ),
        User.user_permissions.through(
            id=750074, user_id=foreign.pk,
            permission_id=shared_permission.pk,
        ),
        User.preview_and_custom_features.through(
            id=750075, user_id=auth_target.pk,
            previewandcustomfeatures_id=shared_feature.pk,
        ),
        User.preview_and_custom_features.through(
            id=750076, user_id=foreign.pk,
            previewandcustomfeatures_id=shared_feature.pk,
        ),
    ]
    for through_model, through_rows_for_model in (
        (User.groups.through, through_rows[:2]),
        (User.user_permissions.through, through_rows[2:4]),
        (User.preview_and_custom_features.through, through_rows[4:]),
    ):
        through_model._base_manager.bulk_create(through_rows_for_model)
        created.extend((through_model, row.pk) for row in through_rows_for_model)
    execute(
        'collaborator-cascade-shared-auth-750004',
        auth_target,
        {
            'application.User': {auth_target.pk},
            'application.User_groups': {through_rows[0].pk},
            'application.User_user_permissions': {through_rows[2].pk},
            'application.User_preview_and_custom_features': {
                through_rows[4].pk,
            },
        },
        [(User, auth_target.pk)],
        retained=(
            (AuthGroup, shared_group.pk),
            (Permission, shared_permission.pk),
            (PreviewAndCustomFeatures, shared_feature.pk),
            (User.groups.through, through_rows[1].pk),
            (User.user_permissions.through, through_rows[3].pk),
            (User.preview_and_custom_features.through, through_rows[5].pk),
        ),
    )

    null_target = person(750005, 3, connected_user=owner)
    prior_audit = LogEntry.objects.bulk_create([
        LogEntry(
            content_type=ContentType.objects.get_for_model(User),
            object_pk=str(null_target.pk),
            object_repr=f'Prior audit by {null_target.username}',
            action=LogEntry.Action.UPDATE,
            changes={},
            actor=null_target,
        ),
    ])[0]
    staff_message = seed(
        StaffBoardMessage, 750081, sport_association=association,
        author=null_target, content='Retained staff history 750081',
    )
    execute(
        'collaborator-cascade-set-null-750005',
        null_target,
        {'application.User': {null_target.pk}},
        [(User, null_target.pk)],
        updates={
            'auditlog.LogEntry': {prior_audit.pk: {'actor_id': None}},
            'communications.StaffBoardMessage': {
                staff_message.pk: {'author_id': None},
            },
        },
        retained=(
            (LogEntry, prior_audit.pk),
            (StaffBoardMessage, staff_message.pk),
        ),
    )

    billing_subscription_target = person(750006, 3, connected_user=owner)
    billing_subscription = seed(
        BillingSubscription, 750091,
        user=billing_subscription_target, billing_plan=None,
    )
    execute(
        'collaborator-cascade-billing-subscription-protect-750006',
        billing_subscription_target,
        {},
        [],
        status=500,
        retained=(
            (User, billing_subscription_target.pk),
            (BillingSubscription, billing_subscription.pk),
        ),
    )

    billing_payment_target = person(750007, 3, connected_user=owner)
    billing_payment = seed(
        BillingPayment, 750092,
        user=billing_payment_target, amount=Decimal('1.00'),
    )
    execute(
        'collaborator-cascade-billing-payment-protect-750007',
        billing_payment_target,
        {},
        [],
        status=500,
        retained=(
            (User, billing_payment_target.pk),
            (BillingPayment, billing_payment.pk),
        ),
    )

    restore_target = person(750008, 3, connected_user=owner)
    restore = seed(
        DataRestore, 750093,
        owner=restore_target,
        association_id=association.pk,
        state='review',
        stage='completed',
        completed_at=timezone.now(),
        upload_path='',
        sha256='0' * 64,
        backup_sha256='0' * 64,
        version='synthetic-fixture-only',
        preview={},
        media={},
        error='',
        attempts=0,
    )
    assert restore.state not in DataRestore.ACTIVE and restore.stage == 'completed'
    execute(
        'collaborator-cascade-data-restore-protect-750008',
        restore_target,
        {},
        [],
        status=500,
        retained=(
            (User, restore_target.pk),
            (DataRestore, restore.pk),
        ),
    )

    associate_target = person(750009, 3, connected_user=owner)
    standalone_associate = seed(
        Associate, 750094,
        user=associate_target,
        sport_association=association,
        group=None,
    )
    execute(
        'collaborator-cascade-associate-do-nothing-750009',
        associate_target,
        {},
        [],
        status=500,
        retained=(
            (User, associate_target.pk),
            (Associate, standalone_associate.pk),
        ),
    )

    assert len(cases) == 8
    report['cascade_case_count'] = len(cases)
    report['cascade_managed_models_verified'] = len(models)
    report['cascade_cache_bytes_verified'] = True
    report['cascade_scope'] = (
        'Eight representative source-authored HTTP DELETE cases: recursive '
        'connected User invitations/onboarding, simple User children with '
        'retained Document/broadcast parents, shared auth joins, prior-audit '
        'and staff-author SET_NULL, three PROTECT failures, and standalone '
        'Associate deferred-FK failure.'
    )
    report['cascade_remaining_coverage'] = [
        'Broader SportAssociation subscription/payment/invoice/account cascades',
        'Payment and CourseSubscription pre-delete hook interactions',
        'Historical-schema acceptance and complete Django/Go parity',
        'Physical document storage, provider, email, and queued-job effects',
        'PostgreSQL statement counters and aggregate SQL-cost assertions',
    ]
