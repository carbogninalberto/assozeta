"""Small, explicit v1 wire contract. No token-selected hosts or verification keys."""
import base64
import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

VERSION = 1
PREFIX = '/api/instance/sso/v1'
CALLBACK = PREFIX + '/callback'
UPSTREAM = '/api/selfhost/v1'
COOKIE = '__Host-assozeta-sso'
OPAQUE = re.compile(r'^[A-Za-z0-9_-]{32,128}$')


class SSOError(Exception):
    def __init__(self, code='invalid_pairing', status=400):
        self.code, self.status = code, status
        super().__init__(code)


def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def https_origin(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.path not in ('', '/') or parts.query or parts.fragment):
            raise ValueError
        port = parts.port
        host = parts.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = '[' + host + ']'
        return 'https://' + host + (f':{port}' if port and port != 443 else '')
    except (ValueError, UnicodeError, TypeError):
        raise SSOError('configuration_required', 409) from None


def authority():
    return https_origin(settings.BAKNEY_SSO_AUTHORITY)


def origin():
    return https_origin(settings.APP_URL)


def cipher(key):
    raw = hashlib.sha256(('assozeta:bakney-sso:v1:' + key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


@sensitive_variables()
def encrypt(value):
    return cipher(settings.SECRET_KEY).encrypt(value.encode()).decode()


@sensitive_variables()
def decrypt(value):
    for key in [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', [])]:
        try:
            return cipher(key).decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            continue
    raise SSOError('configuration_required', 409)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')


@sensitive_variables()
def signature(secret, purpose, payload):
    # The 43-character base64url secret is used as ASCII bytes, without decoding.
    message = b'assozeta-bakney-sso:v1:' + purpose.encode('ascii') + b'\n' + canonical(payload)
    return hmac.new(secret.encode('ascii'), message, hashlib.sha256).hexdigest()


@sensitive_variables()
def upstream(pairing, action, payload=None, *, revocation=False):
    """Fixed authority, verified TLS, no redirects, no environment proxy/netrc."""
    if not (revocation and action == 'disconnect') and pairing.authority != authority():
        raise SSOError('configuration_required', 409)
    if https_origin(pairing.authority) != pairing.authority:
        raise SSOError('configuration_required', 409)
    if action not in ('status', 'acknowledge', 'redeem', 'disconnect'):
        raise ValueError('Unknown SSO action')
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(
                pairing.authority + UPSTREAM + '/' + action,
                auth=(str(pairing.pairing_id), decrypt(pairing.secret_encrypted)),
                json={'protocol': VERSION, **(payload or {})},
                timeout=(3, 8), allow_redirects=False, stream=True,
                headers={'Accept': 'application/json'},
            ) as response:
                if response.status_code != 200:
                    if response.status_code in (401, 403, 404):
                        raise SSOError('pairing_rejected', 403)
                    if response.status_code in (400, 409, 410):
                        raise SSOError('handoff_rejected', 400)
                    raise SSOError('bakney_unavailable', 503)
                raw = b''
                for chunk in response.iter_content(8192):
                    raw += chunk
                    if len(raw) > 32768:
                        raise SSOError('invalid_response', 502)
                result = json.loads(raw)
                if not isinstance(result, dict) or type(result.get('protocol')) is not int or result['protocol'] != VERSION:
                    raise SSOError('invalid_response', 502)
                return result
    except (requests.RequestException, ValueError, UnicodeError):
        raise SSOError('bakney_unavailable', 503) from None
