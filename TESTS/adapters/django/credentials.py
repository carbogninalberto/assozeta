"""Bidirectional Django/Go credential tests. No customer secrets or database access.

Run with BE/requirements.txt and argon2-cffi installed. All keys, tokens and
password hashes exist only in private temporary files deleted at exit. Persistent
reports contain case names, dependency versions and pass/fail counts only.
"""
import contextlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
NOW = 1893456000  # 2030-01-01T00:00:00Z


def main():
    output = ROOT / 'quality-reports/go-migration/credentials.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'success': False, 'phase': 'started; no passing evidence yet'}) + '\n')
    import environ
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    # Keep caller PATH/toolchain but never inherit application/provider credentials.
    tool_environment = {name: value for name, value in os.environ.items()
                        if name in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    os.environ.clear()
    os.environ.update(tool_environment)
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                      JWT_SECRET_KEY=private, JWT_PUBLIC_KEY=public, DJANGO_SETTINGS_MODULE='core.settings',
                      DBHOST='127.0.0.1', DBPORT='1', STORE_ON_DISK='True')
    import django
    from django.db.backends.base.base import BaseDatabaseWrapper
    from freezegun import freeze_time
    with patch.object(BaseDatabaseWrapper, 'ensure_connection', side_effect=RuntimeError('Credential tests prohibit database access')), patch('environ.Env.read_env'):
        # Silence only expected audit-resolver startup warnings; tests themselves
        # run without suppressing errors or assertions.
        with contextlib.redirect_stderr(open(os.devnull, 'w')):
            django.setup()
        from application.services.jwt_token_service import JWTTokenService
        from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
        from django.contrib.auth.hashers import (PBKDF2PasswordHasher, PBKDF2SHA1PasswordHasher,
            BCryptSHA256PasswordHasher, ScryptPasswordHasher, Argon2PasswordHasher, check_password)
        import jwt
        import bcrypt
        import argon2.low_level
        user = SimpleNamespace(user_id='7b23bb0d-27a3-471a-bb05-2e9be79d52fb', role=2, email='member@example.test')
        with freeze_time('2030-01-01T00:00:00Z', ignore=['subprocess']):
            pair = JWTTokenService.generate_tokens_for_user(user)
            tokens = [{'name': 'django-access', 'token': pair['access_token'], 'kind': 'access', 'valid': True},
                      {'name': 'django-refresh', 'token': pair['refresh_token'], 'kind': 'refresh', 'valid': True}]
            base = {'token_type': 'access', 'exp': NOW + 60, 'iat': NOW, 'jti': 'test-jti'}
            for name, changes, remove, valid in [
                ('expired', {'exp': NOW - 1}, (), False), ('expiry-boundary', {'exp': NOW}, (), False),
                ('future-iat', {'iat': NOW + 1}, (), False), ('future-nbf', {'nbf': NOW + 1}, (), False),
                ('missing-exp', {}, ('exp',), False), ('missing-jti', {}, ('jti',), False),
                ('wrong-type', {'token_type': 'refresh'}, (), False),
                ('numeric-jti', {'jti': 1}, (), False), ('numeric-sub', {'sub': 1}, (), False),
                ('string-exp', {'exp': str(NOW + 60)}, (), False),
                ('missing-iat-identity-issuer-audience', {}, ('iat',), True),
                ('different-issuer-audience', {'iss': 'legacy', 'aud': ['legacy']}, (), True),
                ('empty-jti', {'jti': ''}, (), True),
                ('integer-string-iat', {'iat': str(NOW)}, (), True),
                ('fractional-numeric-date', {'exp': NOW + 30.75}, (), True),
            ]:
                claims = {**base, **changes}
                for name_to_remove in remove:
                    claims.pop(name_to_remove, None)
                tokens.append({'name': name, 'token': jwt.encode(claims, private, algorithm='EdDSA'), 'kind': 'access', 'valid': valid})
            other = Ed25519PrivateKey.generate()
            tokens += [
                {'name': 'foreign-key', 'token': jwt.encode(base, other, algorithm='EdDSA'), 'kind': 'access', 'valid': False},
                {'name': 'HS256-confusion', 'token': jwt.encode(base, b'synthetic-hmac-key-at-least-32-bytes', algorithm='HS256'), 'kind': 'access', 'valid': False},
                {'name': 'malformed', 'token': 'invalid.token', 'kind': 'access', 'valid': False},
            ]
            from TESTS.adapters.django.jwt_document import append_cases
            document_cases = append_cases(tokens, base, key, NOW)
            for case in tokens:
                try:
                    (AccessToken if case['kind'] == 'access' else RefreshToken)(case['token'])
                    accepted = True
                except Exception:
                    accepted = False
                assert accepted == case['valid'], 'Django token contract: ' + case['name']
                if case.get('normalized'):
                    result=JWTTokenService.refresh_access_token(case['token'])
                    assert result is not None and result['refresh_token']==case['normalized'],'Django refresh normalization: '+case['name']
            original = RefreshToken(pair['refresh_token'])
            original['iat'] = NOW - 600
            original['custom'] = {'number': 9007199254740993}
            refresh = str(original)
            django_refreshed = JWTTokenService.refresh_access_token(refresh)
            assert django_refreshed is not None
            django_access = dict(AccessToken(django_refreshed['access_token']).payload)
            assert django_access['iat'] == NOW and django_access['exp'] == NOW + 14400
            assert dict(RefreshToken(django_refreshed['refresh_token']).payload) == dict(original.payload)

            passwords = []
            def add_password(name, password, encoded, valid=True):
                if encoded.startswith('$2'):
                    accepted = bcrypt.checkpw(password.encode(), encoded.encode())
                else:
                    accepted = check_password(password, encoded)
                assert accepted == valid, 'Django password contract: ' + name
                passwords.append({'name': name, 'password': password, 'encoded': encoded, 'valid': valid})
            for hasher in [PBKDF2PasswordHasher(), PBKDF2SHA1PasswordHasher(), BCryptSHA256PasswordHasher(), ScryptPasswordHasher(), Argon2PasswordHasher()]:
                password = 'Django-credential-è-密碼'
                encoded = hasher.encode(password, hasher.salt())
                add_password(hasher.algorithm, password, encoded)
                add_password(hasher.algorithm + '-wrong', 'wrong-' + password, encoded, False)
            for kind in (argon2.low_level.Type.I, argon2.low_level.Type.ID, argon2.low_level.Type.D):
                for revision in (16, 19):
                    encoded = 'argon2' + argon2.low_level.hash_secret(b'legacy-argon', b'fixture-salt',
                        time_cost=2, memory_cost=32, parallelism=2, hash_len=32, type=kind, version=revision).decode()
                    add_password(f'argon2-{kind.name}-v{revision}', 'legacy-argon', encoded)
                    add_password(f'argon2-{kind.name}-v{revision}-wrong', 'wrong', encoded, False)
                    if revision == 16:
                        add_password(f'argon2-{kind.name}-implicit-v16', 'legacy-argon', encoded.replace('$v=16', ''))
            raw = bcrypt.hashpw(b'raw-bcrypt', bcrypt.gensalt(rounds=4)).decode()
            add_password('legacy-raw-bcrypt', 'raw-bcrypt', raw)
            for prefix in ('2a', '2b', '2x', '2y'):
                add_password('legacy-raw-' + prefix, 'raw-bcrypt', '$' + prefix + raw[3:])
            add_password('legacy-raw-bcrypt-wrong', 'wrong', raw, False)
            unicode_raw = bcrypt.hashpw('legacy-è-密碼'.encode(), bcrypt.gensalt(rounds=4)).decode()
            for prefix in ('2a', '2b', '2x', '2y'):
                add_password('legacy-unicode-' + prefix, 'legacy-è-密碼', '$' + prefix + unicode_raw[3:])
            truncated = bcrypt.hashpw(b'a' * 72, bcrypt.gensalt(rounds=4)).decode()
            add_password('legacy-bcrypt-72-byte-truncation', 'a' * 72 + 'ignored', truncated)
            add_password('unusable', 'anything', '!synthetic-unusable', False)
            with tempfile.TemporaryDirectory(prefix='az-credentials-') as directory:
                work = Path(directory)
                fixture = work / 'fixture.json'
                fixture.write_text(json.dumps({'now': NOW, 'private': private, 'public': public,
                                               'tokens': tokens, 'refresh': refresh, 'passwords': passwords}))
                fixture.chmod(0o600)
                token_result, password_result = work / 'tokens.json', work / 'passwords.json'
                # No private application environment is inherited by Go tooling.
                subprocess.run(['go', 'test', '-race', '-tags=interop', './pkg/jwt', './pkg/crypto'],
                    cwd=ROOT / 'BE_GO', check=True, timeout=180,
                    env={**tool_environment, 'ASSOZETA_CREDENTIAL_FIXTURE': str(fixture),
                         'ASSOZETA_TOKEN_RESULT': str(token_result), 'ASSOZETA_PASSWORD_RESULT': str(password_result)})
                results = json.loads(token_result.read_text())
                for label, result in results.items():
                    assert set(result) == {'access_token', 'refresh_token', 'expires_in', 'token_type'}
                    assert result['expires_in'] == 14400 and result['token_type'] == 'Bearer'
                    access = dict(AccessToken(result['access_token']).payload)
                    renewed = dict(RefreshToken(result['refresh_token']).payload)
                    assert access['user_id'] == user.user_id and access['role'] == 2 and access['email'] == user.email
                    assert access['exp'] == NOW + 14400 and access['jti'] != renewed['jti']
                    assert access['iss'] == 'https://bakney.com' and access['aud'] == 'bakney'
                    assert len(access['jti']) == 32
                    if label == 'refreshed':
                        assert renewed == dict(original.payload)
                        assert {k: v for k, v in access.items() if k != 'jti'} == {k: v for k, v in django_access.items() if k != 'jti'}
                    else:
                        assert renewed['exp'] == NOW + 2592000 and access['iat'] == NOW
                    # Go-issued refresh is accepted by the real Django service.
                    assert JWTTokenService.refresh_access_token(result['refresh_token']) is not None
                for entry in json.loads(password_result.read_text()):
                    assert check_password(entry['password'], entry['encoded'])
                    assert not check_password('wrong-' + entry['password'], entry['encoded'])
                    assert entry['encoded'].startswith('pbkdf2_sha256$1000000$')
    report = {'success': True, 'jwt_document_cases': document_cases, 'django_to_go_tokens': len(tokens), 'django_to_go_passwords': len(passwords),
              'go_to_django_token_pairs': len(results), 'go_to_django_passwords': 3,
              'dependencies': {name: version(name) for name in ('Django', 'djangorestframework-simplejwt', 'PyJWT', 'bcrypt', 'argon2-cffi')},
              'database_access': 'prohibited', 'customer_credentials': 'none; temporary synthetic credentials removed'}
    output = ROOT / 'quality-reports/go-migration/credentials.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
