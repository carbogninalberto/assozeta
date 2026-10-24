"""Request-reference guard contracts over the private identity/resource fixtures.

Explicit service inputs exercise the actual Django guard. Route registration and
HTTP body parsing remain separate contracts; the operation is trusted metadata.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from TESTS.adapters.django.scoped_resources import uid


def prepare(work):
    from django.core.cache import cache
    from rest_framework.exceptions import PermissionDenied
    from application.models import SportAssociation, User
    from application.impersonation_scope import enforce_scoped_request, remember_medical_upload
    from application.mixin import _local
    association = SportAssociation.objects.get(pk=uid(80))
    session = uid(12000)
    cases = []
    resource = 'Risorsa non disponibile per questo utente e associazione.'
    operation = 'Operazione non disponibile nel contesto dell’associazione.'
    credentials = 'Torna al tuo account per gestire le credenziali.'
    medical = 'Certificato non disponibile per questa associazione.'
    def case(name, body=None, *, op='profile_info', method='POST', path=None, query=None, actor=30,
             group='', detail='', bound=True, session_id=session):
        cases.append(dict(name=name, body={} if body is None else body, operation=op, method=method,
            path=path or {}, query=query or {}, actor=uid(actor), role=2 if actor == 30 else 3,
            group=group, association=uid(80) if bound else '', session=session_id, detail=detail))
    # Fixed supported policy, independent of the implementation registry. This
    # checks the complete operation gate, not successful endpoint execution.
    supported_operations = '''manual_sections manual_asset profile_info profile_associates_course
        profile_associates_sport_association profile_settings_tables statistic_athlete_dashboard search_profile
        subscription_list subscription_list_all subscription_info subscription_payments subscription_card
        subscription_update subscription_add subscription_sign subscription_renew subscription_calculate_tax_code
        subscription_attendance subscription_calendar subscription_medical_certificate_upload
        subscription_medical_certificate_set_certificate_expiration subscription_medical_certificate_edit
        subscription_upload_document subscription_delete_document course_list course_overview_add
        course_overview_update course_overview_delete course_installment_make_payment payment_list carnet_list
        carnet_subscription_list stripe_pay stripe_multiple_pay retrieve_document medical_certificate_document
        attendance_day_mark_absent carnet_subscription_topup document_subscription document_subscription_view
        document_invoice document_invoice_view document_subscription_preview'''.split()
    from application.impersonation_scope import ATHLETE_VIEWS
    assert set(supported_operations) == ATHLETE_VIEWS, 'Review changed Django operation policy explicitly'
    for op in supported_operations:
        case('allowed-operation-' + op, {'sport_association': uid(80)} if op == 'subscription_add' else {},
             op=op, path={'username':'identity-8'} if op == 'search_profile' else {})
    case('unbound-does-not-run-guard', {'subscription_id': uid(2013)}, op='profile_update_password', bound=False)
    case('athlete-unknown-operation', op='future_unknown', detail=operation)
    case('athlete-credential-denial-precedes-credential-message', op='profile_update_password', detail=operation)
    for op in ('profile_update_password', 'oauth2_delete_account', 'profile_integrations'):
        case('staff-credential-denied-' + op, op=op, actor=11, detail=credentials)
    case('staff-other-operation-is-not-an-allowlist', op='future_unknown', actor=11)
    case('nested-own', {'payload': [{'subscription_id': uid(2010)}]})
    case('nested-family', {'payload': [{'subscription': {'id': uid(2012)}}]})
    case('nested-stranger', {'payload': [{'subscription_id': uid(2013)}]}, detail=resource)
    case('nested-foreign', {'payload': [{'subscription': {'value': uid(2014)}}]}, detail=resource)
    case('list-alias', {'subscriptions': [uid(2010), uid(2012)]})
    case('list-alias-denies-one-foreign', {'subscriptions': [uid(2010), uid(2014)]}, detail=resource)
    case('nested-value-list', {'subscription': {'value': [uid(2010), uid(2012)]}})
    case('specific-key-precedes-value-id', {'subscription': {'subscription_id': uid(2013), 'value': uid(2010), 'id': uid(2010)}}, detail=resource)
    case('walk-still-checks-ignored-object-children', {'subscription': {'ignored': {'course_id': uid(5013)}}}, detail=resource)
    case('alias-before-suffix', {'subscriptions_id': uid(2014)})
    case('literal-null-empty-boolean-ignored', {'subscription': None, 'course': '', 'payment': True, 'invoice': False})
    case('dict-empty-does-not-become-ignored', {'subscription': {'id': ''}}, detail=resource)
    case('dict-boolean-is-uuid-not-ignored', {'subscription': {'id': True}}, detail=resource)
    case('malformed-id', {'course': "' OR 1=1 --"}, detail=resource)
    case('uppercase-uuid', {'course': uid(5010).upper()})
    case('uuid-urn', {'course': 'urn:uuid:' + uid(5010)})
    case('uuid-integer', {'course': int(uid(5010).replace('-', ''), 16)})
    case('uuid-overflow', {'course': 2**128}, detail=resource)
    case('uuid-negative', {'course': -1}, detail=resource)
    case('resource-query-before-body', {'new_member_info': {'role': 1}}, query={'subscription_id': uid(2013)}, detail=resource)
    case('role-before-later-resource', {'new_member_info': {'role': 1}, 'subscription_id': uid(2013)}, detail='Puoi creare solo account atleta.')
    case('earlier-resource-before-role', {'subscription_id': uid(2013), 'new_member_info': {'role': 1}}, detail=resource)
    case('create-athlete-number', {'new_member_info': {'role': 2}})
    case('create-athlete-string', {'new_member_info': {'role': '2'}})
    case('create-athlete-default', {'new_member_info': {}})
    case('create-athlete-null-denied', {'new_member_info': {'role': None}}, detail='Puoi creare solo account atleta.')
    for method in ('GET', 'HEAD', 'OPTIONS'):
        case('safe-method-ignores-body-' + method, {'subscription_id': uid(2013)}, method=method)
        case('safe-method-checks-query-' + method, query={'subscription_id': uid(2013)}, method=method, detail=resource)
    for op, number in [('subscription_info', 2013), ('document_subscription_view', 2013),
                       ('document_invoice', 4012), ('attendance_day_mark_absent', 9113),
                       ('carnet_subscription_topup', 8113), ('course_installment_make_payment', 7013),
                       ('course_overview_update', 5013), ('profile_associates_course', 5013),
                       ('profile_associates_sport_association', 90)]:
        case('path-uid-' + op, op=op, path={'uid': uid(number)}, detail=resource)
    case('path-uid-unmapped-is-ignored', path={'uid': uid(2013)})
    for key in ('uid_subscription', 'subscriptionId', 'subscription_id'):
        case('path-subscription-' + key, path={key: uid(2013)}, detail=resource)
    case('search-own-casefold', op='search_profile', path={'username': 'IDENTITY-8'})
    case('search-foreign', op='search_profile', path={'username': 'identity-9'}, detail='Associazione non disponibile.')
    case('search-precedes-nested-reference', {'subscription_id': uid(2013)}, op='search_profile', path={'username': 'identity-9'}, detail='Associazione non disponibile.')
    case('add-own-uuid', {'sport_association': uid(80)}, op='subscription_add')
    case('add-own-username', {'sport_association': 'identity-8'}, op='subscription_add')
    case('add-foreign', {'sport_association': uid(90)}, op='subscription_add', detail='Seleziona la tua associazione.')
    case('add-case-sensitive', {'sport_association': 'IDENTITY-8'}, op='subscription_add', detail='Seleziona la tua associazione.')
    case('add-no-association', op='subscription_add', detail='Seleziona la tua associazione.')
    case('association-username', {'sport_association_id': 'identity-8'})
    case('association-username-case-sensitive', {'sport_association_id': 'IDENTITY-8'}, detail=resource)
    case('selected-group-hides-other-group', {'subscription_id': uid(2015)}, group=uid(1001), detail=resource)
    case('foreign-group-does-not-grant', {'subscription_id': uid(2014)}, group=uid(1003), detail=resource)
    case('malformed-group-manager-fallback', {'subscription_id': uid(2015)}, group='malformed')
    case('cross-association-course-parent', {'course_subscription_id': uid(6014)}, detail=resource)
    case('cross-association-carnet-parent', {'carnet_subscription_id': uid(8114)}, detail=resource)
    case('own-medical', {'medical_id': uid(1101)})
    case('family-medical', {'medical_id': uid(1102)})
    case('stranger-medical', {'medical_id': uid(1103)}, detail=medical)
    case('foreign-medical', {'medical_id': uid(1104)}, detail=medical)
    case('invalid-medical', {'medical_id': 'bad'}, detail='Certificato non disponibile.')
    case('empty-medical-ignored', {'medical_id': ''})
    case('medical-object-ignored', {'medical_id': {'id': uid(1104)}})
    upload_request = SimpleNamespace(impersonation_association=association, headers={'X-Impersonation-Id': session})
    remember_medical_upload(upload_request, uid(1104))
    case('newly-uploaded-medical', {'medical_id': uid(1104)})
    case('upload-raw-session-namespace', {'medical_id': uid(1104)}, session_id='urn:uuid:' + session, detail=medical)
    case('upload-raw-medical-namespace', {'medical_id': 'urn:uuid:' + uid(1104)}, detail=medical)
    case('numeric-linked-medical', {'medical_id': int(uid(1101).replace('-', ''), 16)})
    case('boolean-medical-is-a-uuid', {'medical_id': True}, detail=medical)
    case('upload-grant-does-not-cross-session', {'medical_id': uid(1104)}, session_id=uid(12001), detail=medical)
    cache.set(f'impersonation-upload:{session}:{uid(1198)}', uid(90), 3600)
    case('foreign-upload-grant', {'medical_id': uid(1198)}, detail=medical)
    cache.set(f'impersonation-upload:{session}:{uid(1199)}', uid(80), 3600)
    cache.delete(f'impersonation-upload:{session}:{uid(1199)}')
    case('revoked-upload-grant', {'medical_id': uid(1199)}, detail=medical)
    # Existing reference exists before the upload-grant scenario is executed:
    # use a different session to establish the earlier foreign-medical denial.
    next(c for c in cases if c['name'] == 'foreign-medical')['session'] = uid(12001)
    for entry in cases:
        _local.group_id = entry['group']
        request = SimpleNamespace(impersonation_association=association if entry['association'] else None,
            effective_user=User.objects.get(pk=entry['actor']), method=entry['method'],
            resolver_match=SimpleNamespace(func=type(entry['operation'], (), {}), kwargs=entry['path']),
            query_params=entry['query'], data=entry['body'], headers={'X-Impersonation-Id': entry['session']})
        detail = ''
        try:
            enforce_scoped_request(request)
        except PermissionDenied as exc:
            detail = str(exc.detail)
        finally:
            _local.group_id = None
        assert detail == entry['detail'], f'Django scoped request {entry["name"]}: {detail!r}'
    # Composition evidence uses real Django route resolution and issued sessions,
    # before collaborator permission evaluation; not a fake identity selector.
    from application.impersonation import begin, resolve_request_identity
    from django.test import RequestFactory
    from django.urls import resolve
    from rest_framework.parsers import JSONParser
    from rest_framework.request import Request
    owner = User.objects.get(pk=uid(8))
    owner_athlete, _ = begin(owner, uid(30))
    owner_staff, _ = begin(owner, uid(11))
    factory = RequestFactory()
    composed = [
        ('composed-own', '/profile/info', 30, {'subscription_id': uid(2010)}, ''),
        ('composed-foreign', '/profile/info', 30, {'subscription_id': uid(2014)}, resource),
        ('composed-operation-before-reference', '/course/add', 30, {'course_id': uid(5013)}, operation),
        ('composed-reference-before-collaborator-grant', '/course/add', 11, {'course_id': uid(5013)}, resource),
        ('composed-collaborator-grant-after-reference', '/course/add', 11, {'course_id': uid(5010)}, 'Missing permission: association.courses.create'),
        ('composed-credentials-before-collaborator-grant', '/profile/update/password', 11, {}, credentials),
    ]
    for name, route, target, body, expected in composed:
        session_id = owner_athlete if target == 30 else owner_staff
        request = Request(factory.post(route, data=json.dumps(body), content_type='application/json', HTTP_X_IMPERSONATION_ID=session_id), parsers=[JSONParser()])
        request.user = owner
        detail = ''
        try:
            resolve_request_identity(request, owner)
        except PermissionDenied as exc:
            assert not isinstance(exc.detail, dict), 'resource denial mislabeled as invalid session'
            detail = str(exc.detail)
        assert detail == expected, f'Django composed admission {name}: {detail!r}'
        match = resolve(route)
        case(name, body, op=getattr(getattr(match.func, 'cls', None), '__name__', match.func.__name__),
             actor=target, session_id=session_id, detail=expected)
        cases[-1].update(resolve=True, initiator=uid(8), route=route)
    path = Path(work) / 'scoped-requests.json'
    path.write_text(json.dumps(cases)); path.chmod(0o600)
    print(f'Django: {len(cases)} explicit scoped-request guard contracts passed', flush=True)
    return path, len(cases)
