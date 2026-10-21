"""Explicit identity selection contracts over shared PostgreSQL and private Redis.

These requests have no nested resource IDs. Resource authorization remains a
separate contract; no business endpoint is certified by identity selection alone.
"""
import contextlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time


@contextlib.contextmanager
def identity_fixture(work, log):
    if not shutil.which('redis-server'):
        raise RuntimeError('Identity rehearsal requires private Redis; no skipped checks')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(port), '--save', '',
        '--appendonly', 'no', '--requirepass', 'synthetic-identity-only', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
    try:
        import redis
        raw = redis.Redis(host='127.0.0.1', port=port, db=1, password='synthetic-identity-only', socket_timeout=2)
        deadline = time.monotonic() + 10
        while True:
            try:
                raw.ping(); break
            except redis.ConnectionError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Private identity cache did not start')
                time.sleep(.025)
        from django.test import override_settings
        config = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{port}/1',
                              'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': 'synthetic-identity-only'}}}
        with override_settings(CACHES=config):
            fixture, count = prepare(work)
            yield fixture, f'127.0.0.1:{port}', count
            from django.core.cache import cache
            from application.impersonation import session_key
            cases = json.loads(fixture.read_text())
            revoked = next(case['session'] for case in cases if case['name'] == 'owner-impersonates-member')
            assert cache.get(session_key(revoked)) is None, 'Go session revocation not visible in Django'
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)


def prepare(work):
    from django.core.cache import cache
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    from rest_framework.request import Request
    from rest_framework.exceptions import PermissionDenied
    from auditlog.context import disable_auditlog
    from application.models import User, SportAssociation, Associate
    from application.impersonation import begin, resolve_request_identity, session_key
    from instance.models import InstanceConfiguration
    def uid(n):
        return f'00000000-0000-4000-8000-{n:012d}'
    def account(n, role=2, **kwargs):
        return User.objects.create(user_id=uid(n), username=f'identity-{n}', email=f'identity-{n}@example.test',
                                   role=role, password='!identity-only', **kwargs)
    with disable_auditlog():
        owner = account(8, 1)
        foreign = account(9, 1)
        member = account(10)
        staff = account(11, 3, connected_user=owner, collaborator_role=3, collaborator_permissions=['association.courses.read'])
        account(12, 3, connected_user_id=uid(3))  # disabled owner
        account(13, 3)  # orphan
        account(14, 3, connected_user=owner, collaborator_role=1)
        foreign_member = account(15)
        account(16, 1)  # no association
        account(17, 3, connected_user=owner, collaborator_role=3, collaborator_permissions={'association.courses.read': False})
        account(18, 3, connected_user=owner, collaborator_role=3, collaborator_permissions=None)
        account(19, 3, connected_user=owner, collaborator_role=3, collaborator_permissions=True)
        account(20, 3, connected_user=owner, collaborator_role=1, collaborator_permissions=True)
        account(21, 3, connected_user=owner, collaborator_role=3, collaborator_permissions=False)
        association = SportAssociation.objects.create(sport_association_id=uid(80), user=owner, denomination='Identity owner')
        other = SportAssociation.objects.create(sport_association_id=uid(90), user=foreign, denomination='Identity foreign')
        Associate.objects.create(associate_id=uid(81), sport_association=association, user=member, first_name='Member')
        Associate.objects.create(associate_id=uid(91), sport_association=other, user=foreign_member, first_name='Foreign')
        InstanceConfiguration.objects.create(domain='identity.example.test', name='Identity fixture', primary_association=association)
    admin = User.objects.get(pk=uid(1))
    owner_member, _ = begin(owner, member.pk)
    owner_staff, _ = begin(owner, staff.pk)
    admin_foreign, _ = begin(admin, foreign.pk)
    admin_staff, _ = begin(admin, staff.pk)
    # Persisted records deliberately no longer eligible or incorrectly bound.
    for n, actor, target, tenant in [(200, 8, 15, 80), (201, 1, 3, None), (202, 8, 10, 90), (203, 1, 1, None), (204, 1, 13, None), (205, 1, 12, None), (206, 1, 16, None), (207, 8, 2, 80), (208, 8, 8, 80)]:
        cache.set(session_key(uid(n)), {'actor_id': uid(actor), 'target_id': uid(target), 'association_id': uid(tenant) if tenant else None}, 3600)
    cases = []
    def case(name, actor, path='/profile/info', method='GET', session='', target='', detail='', invalid=False,
             effective=None, scope=None, association='', collaborator=False, impersonating=False):
        actor_id = uid(actor) if actor else ''
        cases.append(dict(name=name, actor=actor_id, path=path, method=method, session=session, target=target,
            detail=detail, invalid=invalid, effective=uid(effective) if effective else actor_id,
            scope=uid(scope) if scope else actor_id, association=uid(association) if association else '',
            collaborator=collaborator, impersonating=impersonating))
    missing = 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.'
    unavailable = 'Utente non disponibile per l’impersonificazione.'
    select = 'Seleziona un utente da impersonificare per accedere a questa sezione.'
    case('anonymous', None)
    case('owner', 8)
    case('member', 10)
    case('collaborator', 11, scope=8, collaborator=True)
    case('collaborator-read', 11, '/course/list', scope=8, collaborator=True)
    case('collaborator-write-denied', 11, '/course/add', 'POST', detail='Missing permission: association.courses.create')
    case('full-collaborator', 14, '/unmapped', scope=8, collaborator=True)
    case('disabled-owner', 12, detail='Associazione del collaboratore non disponibile.')
    case('missing-owner', 13, detail='Associazione del collaboratore non disponibile.')
    case('dictionary-grants-use-keys', 17, '/course/list', scope=8, collaborator=True)
    case('null-grants-denied', 18, '/course/list', detail='Missing permission: association.courses.read')
    case('manual-exemption-before-grant-parsing', 19, '/manuale/sections', scope=8, collaborator=True)
    case('full-role-before-grant-parsing', 20, '/unmapped', scope=8, collaborator=True)
    case('false-grants-denied', 21, '/course/list', detail='Missing permission: association.courses.read')
    case('administrator-profile', 1)
    case('administrator-business-denied', 1, '/course/list', detail=select)
    case('administrator-manual', 1, '/manuale/sections')
    case('administrator-manual-head-denied', 1, '/manuale/sections', 'HEAD', detail=select)
    case('administrator-export-primary', 1, '/association/export/all', scope=8)
    case('administrator-control-ignores-target', 1, '/administration/impersonation', session='invalid', target=uid(9))
    case('owner-control-ignores-target', 8, '/association/impersonation', session='invalid', target=uid(9))
    case('collaborator-control-keeps-actor', 11, '/association/impersonation')
    case('owner-administration-requires-exit', 8, '/instance/status', session=owner_member, detail='Esci dall’impersonificazione per accedere all’amministrazione.')
    case('owner-impersonates-member', 8, session=owner_member, effective=10, scope=10, association=80, impersonating=True)
    case('owner-impersonates-staff', 8, session=owner_staff, effective=11, scope=8, association=80, collaborator=True, impersonating=True)
    case('owner-cannot-lend-grants', 8, '/course/add', 'POST', session=owner_staff, detail='Missing permission: association.courses.create')
    case('administrator-impersonates-foreign-owner', 1, session=admin_foreign, effective=9, scope=9, impersonating=True)
    case('administrator-export-selected-owner', 1, '/association/export/all', session=admin_foreign, effective=9, scope=9, impersonating=True)
    case('administrator-impersonates-staff', 1, session=admin_staff, effective=11, scope=8, collaborator=True, impersonating=True)
    case('administrator-cannot-lend-grants', 1, '/course/add', 'POST', session=admin_staff, detail='Missing permission: association.courses.create')
    case('administrator-orphan-target', 1, session=uid(204), detail=unavailable, invalid=True)
    case('administrator-disabled-owner-target', 1, session=uid(205), detail=unavailable, invalid=True)
    case('administrator-owner-without-association-target', 1, session=uid(206), detail=unavailable, invalid=True)
    case('foreign-collaborator-not-eligible', 8, session=uid(207), detail=unavailable, invalid=True)
    case('owner-self-target-not-eligible', 8, session=uid(208), detail=unavailable, invalid=True)
    case('forged-target-without-session', 8, target=uid(9), detail='Sessione di impersonificazione non valida. Torna al tuo account.', invalid=True)
    case('expired-or-revoked', 8, session=uid(999), detail=missing, invalid=True)
    case('foreign-actor-session', 9, session=owner_member, detail=missing, invalid=True)
    case('mismatched-target-header', 8, session=owner_member, target=uid(15), detail=missing, invalid=True)
    case('foreign-association-binding', 8, session=uid(202), detail=missing, invalid=True)
    case('foreign-member-not-eligible', 8, session=uid(200), detail=unavailable, invalid=True)
    case('inactive-target-not-eligible', 1, session=uid(201), detail=unavailable, invalid=True)
    case('administrator-target-not-eligible', 1, session=uid(203), detail=unavailable, invalid=True)
    case('collaborator-cannot-initiate', 11, session=owner_member, detail='Solo il titolare può impersonare utenti.', invalid=True)
    case('owner-without-association', 16, session=owner_member, detail='Associazione non disponibile.', invalid=True)
    case('anonymous-cannot-impersonate', None, session=owner_member, detail='Accesso non consentito.', invalid=True)
    factory = RequestFactory()
    for entry in cases:
        actor = User.objects.get(pk=entry['actor']) if entry['actor'] else AnonymousUser()
        request = Request(factory.generic(entry['method'], entry['path'], HTTP_X_IMPERSONATION_ID=entry['session'], HTTP_USER_ID=entry['target']))
        request.user = actor
        detail, invalid = '', False
        try:
            resolve_request_identity(request, actor)
        except PermissionDenied as denied:
            detail = denied.detail
            if isinstance(detail, dict):
                invalid = str(detail.get('impersonation_invalid', '')).lower() == 'true'
                detail = detail['detail']
            detail = str(detail)
        assert (detail, invalid) == (entry['detail'], entry['invalid']), f"Django identity {entry['name']}: {(detail, invalid)!r}"
        if not detail:
            def account_id(user):
                return str(user.pk) if user and user.is_authenticated else ''
            actual = (account_id(request.original_user), account_id(request.effective_user), account_id(request.user),
                      request.collaborator, request.impersonating,
                      str(request.impersonation_association.pk) if request.impersonation_association else '')
            expected = (entry['actor'], entry['effective'], entry['scope'], entry['collaborator'], entry['impersonating'], entry['association'])
            assert actual == expected, f"Django identity fields {entry['name']}: {actual!r}"
    # Reuse a previously authenticated actor to prove current eligibility is
    # checked again after persisted state changes, not only at session issuance.
    transitions = [
        (User.objects.filter(pk=owner.pk), 'is_active', False, True, 'Account non disponibile.'),
        (User.original_objects.filter(pk=owner.pk), 'deleted', True, False, 'Account non disponibile.'),
        (User.objects.filter(pk=member.pk), 'is_active', False, True, unavailable),
        (User.original_objects.filter(pk=member.pk), 'deleted', True, False, unavailable),
        (SportAssociation.original_objects.filter(pk=association.pk), 'deleted', True, False, 'Associazione non disponibile.'),
        (Associate.objects.all_objects().filter(pk=uid(81)), 'deleted', True, False, unavailable),
    ]
    for queryset, field, changed, original, expected in transitions:
        try:
            assert queryset.update(**{field: changed}) == 1
            request = Request(factory.get('/profile/info', HTTP_X_IMPERSONATION_ID=owner_member))
            request.user = owner
            try:
                resolve_request_identity(request, owner)
            except PermissionDenied as denied:
                assert str(denied.detail['detail']) == expected
                assert str(denied.detail['impersonation_invalid']).lower() == 'true'
            else:
                raise AssertionError('Django retained revoked identity eligibility')
        finally:
            queryset.update(**{field: original})
    configuration = InstanceConfiguration.get_config()
    for field, changed, original in [('self_hosted', False, True), ('primary_association_id', None, association.pk)]:
        try:
            InstanceConfiguration.objects.filter(pk=configuration.pk).update(**{field: changed})
            request = Request(factory.get('/association/export/all'))
            request.user = admin
            try:
                resolve_request_identity(request, admin)
            except PermissionDenied as denied:
                assert str(denied.detail) == 'Nessuna associazione principale configurata per l’export.'
            else:
                raise AssertionError('Django export admitted an unavailable primary scope')
        finally:
            InstanceConfiguration.objects.filter(pk=configuration.pk).update(**{field: original})
    path = Path(work) / 'identity.json'
    path.write_text(json.dumps(cases)); path.chmod(0o600)
    print(f'Django: {len(cases)} explicit identity selection contracts and 8 eligibility/configuration transitions passed', flush=True)
    return path, len(cases)
