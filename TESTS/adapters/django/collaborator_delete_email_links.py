"""Source-authored accepted-invitation email-link HTTP contracts.

These cases close the documented changed/case/foreign/pending email gaps for
the current Django deletion reference. Expected retained rows are derived from
each pre-request snapshot plus explicit removals; no collector output is used as
the expected model state.

Duplicate owned accounts with the invitation email are characterized explicitly:
pinned Django ``QuerySet.first()`` adds ``ORDER BY pk`` when needed, so the
lowest-PK account is removed. That deterministic implementation behavior is not
accepted as a safe business rule for an ambiguous accepted-account link.
"""
from copy import deepcopy
import unittest
from uuid import UUID


SUCCESS = {'msg': 'collaborator deleted.'}


def uid(number):
    assert 760000 <= number <= 769999, ('Fixture ID outside assigned range', number)
    return f'a2000000-0000-4000-8000-{number:012d}'


def _sort_key(row, key):
    value = row[key]
    return value.int if isinstance(value, UUID) else value


def authored_snapshot(before, removals, *, primary_keys, log_label, new_logs,
                      index_label, new_indexes):
    """Return the exact expected full snapshot for explicit authored removals."""
    result = {}
    for label, rows in before.items():
        key = primary_keys[label]
        removed = removals.get(label, set())
        expected = [
            deepcopy(row) for row in rows
            if row[key] not in removed
        ]
        result[label] = expected

    for label, rows in result.items():
        if label == log_label:
            rows.extend(deepcopy(new_logs))
        elif label == index_label:
            rows.extend(deepcopy(new_indexes))
        rows.sort(key=lambda row: _sort_key(row, primary_keys[label]))
    return result


def run(*, base, owner, foreign, association, person, seed, snapshot,
        cache_snapshot, models, created, report):
    """Execute deterministic actual-HTTP cases through the supplied fixture."""
    from auditlog.models import LogEntry
    from django.contrib.contenttypes.models import ContentType
    from rest_framework_simplejwt.tokens import AccessToken

    from application.models import CollaborationInvites, User
    from application.models.audit_models import AuditLogIndex
    from TESTS.contracts.test_collaborator_delete import assert_case

    assert len(models) == 128, ('Expected all 128 managed models', len(models))
    assert not report.get('email_link_cases'), (
        'Email-link fixture helper must run once per report',
    )
    cases = report.setdefault('email_link_cases', [])
    cases_at_start = len(created)

    access = AccessToken()
    access['user_id'] = str(owner.pk)
    token = str(access)

    def expected_snapshot(before, removals, new_logs, new_indexes):
        expected = authored_snapshot(
            before, removals,
            primary_keys={
                model._meta.label: model._meta.pk.attname
                for model in models
            },
            log_label=LogEntry._meta.label,
            new_logs=new_logs,
            index_label=AuditLogIndex._meta.label,
            new_indexes=new_indexes,
        )

        return expected

    def retained(after, model, pk):
        key = model._meta.pk.attname
        return any(
            row[key] == pk
            for row in after[model._meta.label]
        )

    def execute(name, invite, removed_users, expected_audits, retained_users,
                *, characterization_only=False):
        case = {
            'name': name,
            'path': f'/collaborators/{invite.pk}/delete',
            'token': token,
            'status': 200,
            'response': SUCCESS,
            'headers': {'Accept-Language': 'en'},
        }
        before = snapshot()
        before_cache = cache_snapshot()
        assert not LogEntry._base_manager.filter(cid=name).exists(), (
            'Case correlation ID already has audit rows', name,
        )

        assert_case(unittest.TestCase(), base, case)

        after = snapshot()
        after_cache = cache_snapshot()
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
            (row['content_type_id'], row['object_pk'])
            for row in audits
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
        assert len(indexes) == len(audits), (
            'Unexpected audit index count', name, indexes,
        )
        assert {row['log_entry_id'] for row in indexes} == set(audit_ids), (
            'Audit/index identity mismatch', name, indexes,
        )
        assert all(
            row['sport_association_id'] == association.pk
            and row['resolution_path'] == 'actor.sport_association'
            for row in indexes
        ), ('Unexpected audit index attribution', name, indexes)

        removals = {
            User._meta.label: set(removed_users),
            CollaborationInvites._meta.label: {invite.pk},
        }
        expected = expected_snapshot(
            before, removals, new_logs=audits, new_indexes=indexes,
        )
        assert after == expected, (
            'Unexpected durable email-link deletion effect', name,
        )
        assert after_cache == before_cache, ('Unexpected cache bytes', name)

        retained_rows = [(User, owner.pk), (User, foreign.pk)]
        retained_rows.extend((User, pk) for pk in retained_users)
        for model, pk in retained_rows:
            assert retained(after, model, pk), (
                'Expected retained account disappeared', name, pk,
            )

        cases.append({
            'name': name,
            'invite': str(invite.pk),
            'classification': (
                'characterization_only'
                if characterization_only else 'current_schema_contract'
            ),
            'removed': {
                label: sorted(str(pk) for pk in pks)
                for label, pks in removals.items()
            },
            'retained_users': sorted(str(pk) for pk in retained_users),
            'audits': [
                f'{row["content_type_id"]}:{row["object_pk"]}'
                for row in audits
            ],
            'audit_indexes': len(indexes),
            'managed_models_verified': len(models),
            'cache_bytes_unchanged': True,
        })
        print(
            f'{name}: HTTP200, removals={sum(map(len, removals.values()))}, '
            f'audits={len(audits)}, indexes={len(indexes)}, models={len(models)}',
            flush=True,
        )

    # 1. An owned account changed away from the invitation's email. There is no
    # durable user key on the invitation, so email lookup no longer finds it.
    changed = person(
        764001, User.COLLABORATOR, connected_user=owner,
        email='changed-new-764001@example.test',
    )
    changed_invite = seed(
        CollaborationInvites, 765001, user=owner,
        email='original-764001@example.test', accepted=True,
        token='synthetic-email-link-765001',
    )
    execute(
        'collaborator-delete-email-changed-764001',
        changed_invite,
        removed_users=(),
        expected_audits=((CollaborationInvites, changed_invite.pk),),
        retained_users=(changed.pk,),
    )

    # 2. The old invitation email still has an owned match while a second owned
    # account now has the new email. Only the deterministic old-email match dies.
    old_match = person(
        764002, User.COLLABORATOR, connected_user=owner,
        email='shared-old-764002@example.test',
    )
    new_match = person(
        764003, User.COLLABORATOR, connected_user=owner,
        email='shared-new-764003@example.test',
    )
    old_new_invite = seed(
        CollaborationInvites, 765002, user=owner,
        email=old_match.email, accepted=True,
        token='synthetic-email-link-765002',
    )
    execute(
        'collaborator-delete-email-old-new-owner-764002',
        old_new_invite,
        removed_users=(old_match.pk,),
        expected_audits=(
            (User, old_match.pk),
            (CollaborationInvites, old_new_invite.pk),
        ),
        retained_users=(new_match.pk,),
    )

    # 3. Exact-case equality is the positive current-schema match.
    exact = person(
        764006, User.COLLABORATOR, connected_user=owner,
        email='CaseExact-764006@Example.Test',
    )
    exact_invite = seed(
        CollaborationInvites, 765004, user=owner,
        email=exact.email, accepted=True,
        token='synthetic-email-link-765004',
    )
    execute(
        'collaborator-delete-email-exact-case-764006',
        exact_invite,
        removed_users=(exact.pk,),
        expected_audits=(
            (User, exact.pk),
            (CollaborationInvites, exact_invite.pk),
        ),
        retained_users=(),
    )

    # 4. PostgreSQL equality is case-sensitive for these current-schema values.
    different_case = person(
        764007, User.COLLABORATOR, connected_user=owner,
        email='case-different-764007@example.test',
    )
    different_case_invite = seed(
        CollaborationInvites, 765005, user=owner,
        email='Case-Different-764007@Example.Test', accepted=True,
        token='synthetic-email-link-765005',
    )
    execute(
        'collaborator-delete-email-case-mismatch-764007',
        different_case_invite,
        removed_users=(),
        expected_audits=((CollaborationInvites, different_case_invite.pk),),
        retained_users=(different_case.pk,),
    )

    # 5. Accepted but no owned account has the invitation email.
    missing_invite = seed(
        CollaborationInvites, 765006, user=owner,
        email='missing-account-764009@example.test', accepted=True,
        token='synthetic-email-link-765006',
    )
    execute(
        'collaborator-delete-email-accepted-no-match-765006',
        missing_invite,
        removed_users=(),
        expected_audits=((CollaborationInvites, missing_invite.pk),),
        retained_users=(),
    )

    # 6. A same-email account under another owner is outside the scoped match.
    foreign_same_email = person(
        764008, User.COLLABORATOR, connected_user=foreign,
        email='foreign-boundary-764008@example.test',
    )
    foreign_invite = seed(
        CollaborationInvites, 765007, user=owner,
        email=foreign_same_email.email, accepted=True,
        token='synthetic-email-link-765007',
    )
    execute(
        'collaborator-delete-email-foreign-same-email-764008',
        foreign_invite,
        removed_users=(),
        expected_audits=((CollaborationInvites, foreign_invite.pk),),
        retained_users=(foreign_same_email.pk,),
    )

    # 7. Pending invitations never enter accepted-account lookup.
    pending_match = person(
        764009, User.COLLABORATOR, connected_user=owner,
        email='pending-match-764009@example.test',
    )
    pending_invite = seed(
        CollaborationInvites, 765008, user=owner,
        email=pending_match.email, accepted=False,
        token='synthetic-email-link-765008',
    )
    assert pending_invite.accepted is False
    execute(
        'collaborator-delete-email-pending-keeps-account-764009',
        pending_invite,
        removed_users=(),
        expected_audits=((CollaborationInvites, pending_invite.pk),),
        retained_users=(pending_match.pk,),
    )

    # Duplicate owned accounts characterize Django's explicit lowest-PK first().
    # The deterministic implementation result does not resolve which business
    # account the accepted invitation was intended to represent.
    duplicate_a = person(
        764004, User.COLLABORATOR, connected_user=owner,
        email='duplicate-owned-764004@example.test',
    )
    duplicate_b = person(
        764005, User.COLLABORATOR, connected_user=owner,
        email=duplicate_a.email,
    )
    duplicate_invite = seed(
        CollaborationInvites, 765003, user=owner,
        email=duplicate_a.email, accepted=True,
        token='synthetic-email-link-765003',
    )
    duplicate_query = User.objects.filter(
        email=duplicate_invite.email,
        connected_user=owner,
    )
    duplicates = tuple(duplicate_query.values_list('pk', flat=True))
    assert set(duplicates) == {duplicate_a.pk, duplicate_b.pk}, duplicates
    first_duplicate = duplicate_query.first()
    assert first_duplicate is not None
    assert first_duplicate.pk == min(duplicate_a.pk, duplicate_b.pk), (
        'Pinned Django QuerySet.first() no longer selects lowest PK',
        first_duplicate.pk,
    )
    duplicate_case = 'collaborator-delete-email-duplicate-lowest-pk-764004'
    execute(
        duplicate_case,
        duplicate_invite,
        removed_users=(first_duplicate.pk,),
        expected_audits=(
            (User, first_duplicate.pk),
            (CollaborationInvites, duplicate_invite.pk),
        ),
        retained_users=(duplicate_b.pk,),
        characterization_only=True,
    )
    report['email_link_reference_gaps'] = [{
        'id': 'ambiguous-owned-email-link',
        'status': 'unresolved',
        'invite': str(duplicate_invite.pk),
        'matching_accounts': sorted(str(pk) for pk in duplicates),
        'observed_contract': (
            'Pinned Django QuerySet.first() adds ORDER BY pk for this unordered '
            'queryset, so current HTTP characterization removes the lowest-PK '
            'owned account and retains the other.'
        ),
        'intended_safe_contract': (
            'Unresolved: the accepted invitation has no durable account '
            'identity. Require one, reject ambiguity, or explicitly review '
            'lowest-PK selection as the business rule.'
        ),
        'characterization_case': duplicate_case,
        'http_characterization_claimed': True,
        'safe_acceptance_claimed': False,
        'reason': 'Email and owner scope do not identify which duplicate account accepted this invitation.',
    }]
    report['email_link_case_count'] = len(cases)
    report['email_link_managed_models_verified'] = len(models)
    report['email_link_cache_bytes_verified'] = True
    report['email_link_http_cases_executed'] = len(cases)
    report['email_link_scope'] = (
        'Eight deterministic current-schema email-link cases: seven contract '
        'cases and one explicit lowest-PK characterization for duplicate owned '
        'matches. Duplicate business identity remains unresolved.'
    )
    report['email_link_characterization_case_count'] = 1
    assert len(cases) == 8
    assert len(created) - cases_at_start == 17, (
        'Unexpected email-link fixture registration count',
        len(created) - cases_at_start,
    )
    return {
        'cases': len(cases),
        'unresolved_reference_gaps': len(report['email_link_reference_gaps']),
    }
