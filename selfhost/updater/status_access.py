"""Current-owner status access while Django is unavailable."""
import base64
import json
import math
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import UUID

from common import compose_command, compose_environment, read_env


class AccessDenied(Exception):
    pass


class OwnershipUnavailable(Exception):
    pass


def decoded(value):
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def authenticate_access_token(bearer, values, clock=time.time):
    try:
        header, payload, signature = bearer.split('.')
        if json.loads(decoded(header)).get('alg') != 'EdDSA':
            raise ValueError()
        claims = json.loads(decoded(payload))
        expires = claims.get('exp')
        not_before = claims.get('nbf', 0)
        now = clock()
        if (claims.get('token_type') != 'access' or
                any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    for value in (expires, not_before)) or expires <= now or not_before > now):
            raise ValueError()
        actor = str(UUID(claims['user_id']))
        public_key = (base64.b64decode(values['JWT_PUBLIC_KEY_B64'], validate=True)
                      if values.get('JWT_PUBLIC_KEY_B64') else values['JWT_PUBLIC_KEY'].replace('\\n', '\n').encode())
        with TemporaryDirectory(prefix='status-auth-') as temporary:
            directory = Path(temporary)
            (directory / 'key').write_bytes(public_key)
            (directory / 'message').write_bytes(f'{header}.{payload}'.encode())
            (directory / 'signature').write_bytes(decoded(signature))
            result = subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(directory / 'key'),
                                     '-rawin', '-in', str(directory / 'message'), '-sigfile', str(directory / 'signature')],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode:
            raise ValueError()
        return actor
    except (ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.SubprocessError):
        raise AccessDenied() from None


def require_current_owner(root, env_file, actor):
    values = read_env(env_file)
    # Read the current database owner, not a cached role or operation actor.
    # A failed migration elsewhere need not prevent this fixed read-only query.
    sql = '''SELECT EXISTS (
        SELECT 1 FROM (SELECT * FROM instance_instanceconfiguration ORDER BY id LIMIT 1) cfg
        JOIN application_sportassociation sa ON cfg.primary_association_id = sa.sport_association_id
        JOIN bakney_user u ON sa.user_id = u.user_id
        WHERE cfg.self_hosted IS TRUE AND u.is_active IS TRUE AND u.user_id = :'actor'
    );'''
    try:
        result = subprocess.run(compose_command(root, env_file) + ['exec', '-T', 'postgres', 'psql',
            '-U', values['DBUSER'], '-d', values['DBNAME'], '-At', '-v', 'ON_ERROR_STOP=1', '-v', f'actor={UUID(actor)}'],
            input=sql, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=compose_environment(root, env_file), timeout=10)
        if result.returncode or result.stdout.strip() not in ('t', 'f'):
            raise OwnershipUnavailable()
        if result.stdout.strip() != 't':
            raise AccessDenied()
    except (KeyError, OSError, subprocess.SubprocessError):
        raise OwnershipUnavailable() from None
