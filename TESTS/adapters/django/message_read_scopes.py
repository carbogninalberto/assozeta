"""Private setup and strict observations for the bounded message authorization slice.

Imports are inert. Backend imports and credential minting occur only in seed_fixture.
Read requests are permitted no durable/session/audit effects; setup is not management acceptance.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from TESTS.contracts import test_message_read_scopes as contract
from TESTS.adapters.django import message_reads as shared

ROOT = Path(__file__).resolve().parents[3]
SESSION_SPECS = {
    'admin_owner': ('admin', 'owner', None),
    'admin_foreign': ('admin', 'foreign_owner', None),
    'admin_full': ('admin', 'full_collaborator', None),
    'owner_full': ('owner', 'full_collaborator', 'own_association'),
    'admin_restricted': ('admin', 'restricted_collaborator', None),
    'owner_restricted': ('owner', 'restricted_collaborator', 'own_association'),
    'admin_athlete': ('admin', 'athlete', None),
    'owner_athlete': ('owner', 'athlete', 'own_association'),
    'expired': ('admin', 'owner', None),
    'revoked': ('admin', 'owner', None),
    'ineligible': ('admin', 'admin', None),
    'forged': ('admin', 'owner', None),
    'association_mismatch': ('owner', 'full_collaborator', 'foreign_association'),
    'owner_owner': ('owner', 'foreign_owner', 'own_association'),
}


class StateFailure(shared.AcceptanceStateError):
    pass


class CacheFailure(shared.AcceptanceStateError):
    pass


def failure_diagnostic(error):
    """Never serialize exception text, args, bodies, paths, headers or credentials."""
    code = 'unknown'
    if isinstance(error, contract.ResponseFailure) and error.code in contract.HTTP_CHECKS:
        code = error.code
    elif isinstance(error, StateFailure):
        code = 'state'
    elif isinstance(error, CacheFailure):
        code = 'cache'
    elif isinstance(error, shared.AttributionError):
        code = 'attribution'
    result = {'check': code}
    status = contract.safe_status(getattr(error, 'actual_status', None))
    if status is not None:
        result['status'] = status
    return result


def runtime_manifest(source, tokens, sessions):
    converted = copy.deepcopy(source)
    converted['source_sha256'] = contract.source_digest(source)
    for case in converted['cases']:
        case['token'] = tokens[case.pop('actor')]
        for header, value in case['headers'].items():
            if value.startswith('$session:'):
                case['headers'][header] = sessions[value.removeprefix('$session:')]
    contract.validate_runtime(converted, source)
    return converted


def cleanup_steps(steps):
    """Attempt every independent cleanup; no exception text leaves private execution."""
    results = {}
    for name, operation in steps:
        try:
            results[name] = operation() is not False
        except BaseException:
            results[name] = False
    return {'results': results, 'verified': bool(results) and all(results.values())}


def snapshot_state(apps):
    state = shared.snapshot_models(apps)
    contract.require(len(state) == 128, 'state.requires-128-models')
    return state


def verify_read(*, backend, method, path, status, observed, cid, before_state,
                after_state, before_cache, after_cache, contrasts):
    # Keep all three checks independent, including after an HTTP/assertion failure.
    errors = []
    for code, failure, operation in (
        ('state', StateFailure, lambda: shared.assert_state_unchanged(before_state, after_state)),
        ('cache', CacheFailure, lambda: shared.assert_cache_unchanged(before_cache, after_cache, contrasts)),
        ('attribution', shared.AttributionError, lambda: shared.assert_attribution(backend, method, path, status, observed, cid)),
    ):
        try:
            operation()
        except BaseException as error:
            safe = failure(code)
            safe.actual_status = contract.safe_status(status)
            errors.append(safe)
    if errors:
        raise errors[0]
    return {'state': True, 'cache': True, 'attribution': True}


def observed_case(case, *, label, backend, base_url, observer, snapshot_state,
                  snapshot_cache, contrasts, request=contract.assert_case):
    before_state, before_cache = snapshot_state(), snapshot_cache()
    cursor = observer.cursor()
    status = None
    try:
        status = request(case, base_url)
    except BaseException as error:
        status = contract.safe_status(getattr(error, 'actual_status', None))
        raise
    finally:
        # Snapshot both domains even when one observation itself fails.
        observations = {}
        failures = []
        for name, operation in (('state', snapshot_state), ('cache', snapshot_cache)):
            try:
                observations[name] = operation()
            except BaseException as error:
                failures.append(error)
        if failures:
            raise failures[0]
        checks = verify_read(backend=backend, method=case['method'], path=case['path'],
            status=status, observed=observer.delta(cursor), cid=case['name'],
            before_state=before_state, after_state=observations['state'],
            before_cache=before_cache, after_cache=observations['cache'], contrasts=contrasts)
    return {'case': case['name'], 'status': status, 'check': case['check'],
            'checks': {'response': True, **checks}, 'models': len(before_state),
            'state_sha256': shared.digest(before_state), 'cache_sha256': before_cache.digest}


def run_workload(label, backend, base_url, fixture, apps, raw, observer, *, workloads=None):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    workload = {'label': label, 'success': False, 'cases': []}
    if workloads is not None:
        workloads.append(workload)
    contract.validate_runtime(fixture['runtime'])
    try:
        shared.assert_state_unchanged(fixture['baseline_state'], snapshot_state(apps))
    except BaseException:
        raise StateFailure('state') from None
    try:
        shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    except BaseException:
        raise CacheFailure('cache') from None
    for case in fixture['runtime']['cases']:
        try:
            workload['cases'].append(observed_case(case, label=label, backend=backend,
                base_url=base_url, observer=observer, snapshot_state=lambda: snapshot_state(apps),
                snapshot_cache=lambda: snapshot_historical_read_cache(raw), contrasts=fixture['contrasts']))
        except BaseException as error:
            workload['cases'].append({'case': case['name'], **failure_diagnostic(error)})
            raise
    workload['success'] = True
    return workload


def restore_cache(raw, original):
    """Restore every original TYPE/DUMP/absolute expiry, removing all newly created keys."""
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    current = snapshot_historical_read_cache(raw)
    for key in current.entries.keys() - original.entries.keys():
        raw.delete(key)
    for key, entry in original.entries.items():
        if current.entries.get(key) == entry:
            continue
        if entry.expires_at_ms == -1:
            raw.restore(key, 0, entry.serialized_value, replace=True)
        else:
            contract.require(entry.expires_at_ms > int(time.time() * 1000), 'cleanup.original-key-expired')
            raw.restore(key, entry.expires_at_ms, entry.serialized_value, replace=True, absttl=True)
    contract.require(snapshot_historical_read_cache(raw).entries == original.entries, 'cleanup.cache-restored')
    return True


def seed_fixture(apps, private_work, raw):
    """Reuse the authored message seed; add only this slice's identities/references.

    Caller captures original cache/state and always restores them, including when this
    function raises. An outer transaction rolls back all seed rows on partial failure.
    """
    from auditlog.context import disable_auditlog
    from django.core.cache import cache
    from django.db import transaction
    from application.models import Group, SportAssociation, User
    from application.impersonation import session_key, SESSION_TTL
    from communications.models import Message, MessageTransaction
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache

    source = contract.load_source()
    ids = source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = shared.seed_fixture(apps, private_work, raw)
        created = fixture['created']

        def seed(model, label, **values):
            contract.require(not model._base_manager.filter(pk=ids[label]).exists(), 'fixture.collision')
            row = model(pk=ids[label], **values)
            model._base_manager.bulk_create([row])
            created.append((model, row.pk))
            return row

        admin = seed(User, 'admin', username='message-scopes-admin', email='message-scopes-admin@example.test',
                     role=User.ASSOCIATION, is_superuser=True, password='!synthetic')
        association = seed(SportAssociation, 'admin_association', user=admin,
                           denomination='Message scope original administrator', tax_code='71000000001')
        original_message = seed(Message, 'admin_message', sport_association=association, type='EMAIL',
                                message='Original actor private body', subject='original-actor')
        Message._base_manager.filter(pk=original_message.pk).update(created_at=datetime(2026, 10, 23, 9, tzinfo=timezone.utc))
        original_delivery = seed(MessageTransaction, 'admin_delivery', message=original_message, recipient='first@example.test')
        MessageTransaction._base_manager.filter(pk=original_delivery.pk).update(sent_on=datetime(2026, 10, 23, 13, tzinfo=timezone.utc))
        seed(Group, 'own_group', name='Scope own group', sport_association_id=ids['own_association'])
        seed(Group, 'foreign_group', name='Scope foreign group', sport_association_id=ids['foreign_association'])
        User._base_manager.filter(pk=ids['restricted_collaborator']).update(
            collaborator_permissions=['association.communication.messages.read'])
        # Same recipient, different parent ownership; equality is never a tenant relation.
        MessageTransaction._base_manager.filter(pk=ids['foreign_delivery']).update(recipient='first@example.test')
        tokens = fixture['tokens'].copy()
        for name in ('admin', 'foreign_owner'):
            token = AccessToken()
            token['user_id'] = ids[name]
            tokens[name] = str(token)
        sessions = {label: str(uuid4()) for label in SESSION_SPECS}
        for label, (actor, target, boundary) in SESSION_SPECS.items():
            key = session_key(sessions[label])
            raw_key = cache.make_key(key)
            contract.require(not raw.exists(raw_key), 'fixture.session-collision')
            if label == 'forged':
                continue
            record = {'actor_id': ids[actor], 'target_id': ids[target],
                      'association_id': ids[boundary] if boundary else None}
            cache.set(key, record, SESSION_TTL)
            contract.require(cache.get(key) == record and raw.type(raw_key) == b'string', 'fixture.session-serializer')
            if label == 'expired':
                raw.pexpireat(raw_key, int(time.time() * 1000) - 1)
            if label == 'revoked':
                cache.delete(key)
            if label in ('expired', 'revoked'):
                contract.require(not raw.exists(raw_key), 'fixture.session-absent')
        fixture['authored'] = source
        fixture['runtime'] = runtime_manifest(source, tokens, sessions)
        fixture['tokens'], fixture['sessions'] = tokens, sessions
        fixture['runtime_path'] = private_work / 'message_read_scopes-runtime.json'
        fixture['runtime_path'].write_text(json.dumps(fixture['runtime'], ensure_ascii=False))
        fixture['runtime_path'].chmod(0o600)
        fixture['baseline_state'] = snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


def remove_private_fixture(path):
    if path.exists():
        shutil.rmtree(path)
    return not path.exists()
