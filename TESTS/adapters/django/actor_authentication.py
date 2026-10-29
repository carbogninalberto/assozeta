"""Explicit JWTAuthentication-stage expectations for the shared account fixture.

The scoped identity resolver and HTTP error rendering are separate acceptance work.
"""
import json
from pathlib import Path


def credential_fixture(directory):
    from django.conf import settings
    from django.test import RequestFactory
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.tokens import AccessToken
    from rest_framework.exceptions import AuthenticationFailed
    from application.models import User
    def uid(n):
        return f'00000000-0000-4000-8000-{n:012d}'
    def token(n, **claims):
        access = AccessToken()
        access['user_id'] = uid(n)
        for key, value in claims.items():
            access[key] = value
        return str(access)
    owner, collaborator = token(1), token(2, role=1, email='forged@example.test', is_superuser=True)
    cases = []
    def add(name, header, actor='', code=''):
        cases.append({'name': name, 'header': header, 'actor_id': actor, 'code': code})
    add('anonymous', '')
    add('other-scheme', 'Basic credential')
    add('scheme-case-sensitive', 'bearer ' + owner)
    add('missing-credential', 'Bearer', code='bad_authorization_header')
    add('extra-fields', 'Bearer ' + owner + ' extra', code='bad_authorization_header')
    add('owner', 'Bearer ' + owner, uid(1))
    add('numeric-uuid-claim', 'Bearer ' + token(1, user_id=int(uid(1).replace('-', ''), 16)), uid(1))
    add('braced-uuid-claim', 'Bearer ' + token(1, user_id='{' + uid(1) + '}'), uid(1))
    add('null-user-claim', 'Bearer ' + token(1, user_id=None), code='user_not_found')
    add('boolean-user-claim', 'Bearer ' + token(1, user_id=True), code='user_not_found')
    add('whitespace', ' \tBearer\t' + owner + ' ', uid(1))
    add('claims-do-not-grant-role', 'Bearer ' + collaborator, uid(2))
    add('inactive', 'Bearer ' + token(3), code='user_inactive')
    add('deleted', 'Bearer ' + token(4), code='user_not_found')
    add('missing-user', 'Bearer ' + token(999), code='user_not_found')
    add('malformed-token', 'Bearer not.a.token', code='token_not_valid')
    add('missing-id', 'Bearer ' + str(AccessToken()), code='token_not_valid')
    add('expired', 'Bearer ' + token(1, exp=1), code='token_not_valid')
    factory = RequestFactory()
    authenticator = JWTAuthentication()
    for case in cases:
        request = factory.get('/profile/info', HTTP_AUTHORIZATION=case['header'])
        actor, code = '', ''
        try:
            result = authenticator.authenticate(request)
            if result:
                account, _ = result
                actor = str(account.pk)
                if actor == uid(2):
                    assert account.role == 3 and not account.is_superuser and account.email == 'SHARED@example.test'
        except AuthenticationFailed as failure:
            code = str(failure.detail.get('code', failure.get_codes().get('detail', '')))
        assert (actor, code) == (case['actor_id'], case['code']), 'Django actor admission: ' + case['name']
    destination = Path(directory) / 'actor-credentials.json'
    destination.write_text(json.dumps({'private': settings.JWT_SECRET_KEY, 'public': settings.JWT_PUBLIC_KEY, 'cases': cases}))
    destination.chmod(0o600)
    return destination, len(cases)
