"""Small, explicit v1 wire contract. No token-selected hosts or verification keys."""
import base64
import hashlib
import hmac
import json
import re
import uuid
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

VERSION = 'bakney-pairing-v1'
PREFIX = '/bakney/v1'
CALLBACK = PREFIX + '/callback'
COOKIE = '__Host-assozeta-sso'
OPAQUE = re.compile(r'^[A-Za-z0-9_-]{32,128}$')


class SSOError(Exception):
    def __init__(self, code='invalid_pairing', status=400):
        self.code, self.status = code, status
        super().__init__(code)


def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def development_http_allowed():
    return settings.DEBUG and settings.ASSOZETA_DEPLOYMENT_MODE == 'development'


def browser_cookie(pairing):
    secure = not (development_http_allowed() and urlsplit(pairing.origin).scheme == 'http')
    return (COOKIE if secure else 'assozeta-sso-dev'), secure


def normalize_origin(value):
    try:
        if not isinstance(value, str) or any(char.isspace() for char in value) or '\\' in value or '%' in value:
            raise ValueError
        parts = urlsplit(value)
        schemes = ('http', 'https') if development_http_allowed() else ('https',)
        if (parts.scheme not in schemes or not parts.hostname or parts.username is not None or parts.password is not None
                or parts.path not in ('', '/') or parts.query or parts.fragment):
            raise ValueError
        port = parts.port
        if port == 0:
            raise ValueError
        host = parts.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = '[' + host + ']'
        default_port = 443 if parts.scheme == 'https' else 80
        return parts.scheme + '://' + host + (f':{port}' if port and port != default_port else '')
    except (ValueError, UnicodeError, TypeError):
        raise SSOError('configuration_required', 409) from None


def authority():
    value = settings.BAKNEY_SSO_API_BASE.rstrip('/')
    parts = urlsplit(value)
    base = normalize_origin(parts.scheme + '://' + parts.netloc)
    if parts.query or parts.fragment or not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', parts.path):
        raise SSOError('configuration_required', 409)
    return base + parts.path


def ui_origin():
    return normalize_origin(settings.BAKNEY_SSO_UI_ORIGIN)


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def origin():
    value = normalize_origin(settings.APP_URL)
    if development_http_allowed():
        return value
    parts = urlsplit(value)
    # Bakney accepts HTTPS DNS origins on port 443 only.
    host = parts.hostname
    if (parts.port or not host or '.' not in host or host.replace('.', '').isdigit()
            or host.endswith('.') or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)):
        raise SSOError('configuration_required', 409)
    return value


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


@sensitive_variables()
def signature(secret, phase, binding):
    """Bakney CONTRACT.md: decoded 32-byte key, ordered ASCII fields, LF separators."""
    fields = [VERSION, phase] + [str(binding[k]) for k in (
        'pairing_id', 'association_id', 'instance_id', 'origin', 'callback_uri',
        'generation', 'nonce', 'expires_at')]
    return hmac.new(base64.urlsafe_b64decode(secret + '='), '\n'.join(fields).encode('ascii'), hashlib.sha256).hexdigest()


@sensitive_variables()
def upstream(pairing, action, payload=None, *, revocation=False):
    """Configured API base, verified TLS when HTTPS, no redirects or ambient credentials."""
    if not (revocation and action == 'disconnect') and pairing.authority != authority():
        raise SSOError('configuration_required', 409)
    parts = urlsplit(pairing.authority)
    if normalize_origin(parts.scheme + '://' + parts.netloc) + parts.path != pairing.authority:
        raise SSOError('configuration_required', 409)
    if action not in ('status', 'token', 'disconnect'):
        raise ValueError('Unknown SSO action')
    if not pairing.remote_pairing_id:
        raise SSOError('invalid_pairing', 409)
    body = {**(payload or {}), 'pairing_id': str(pairing.remote_pairing_id),
            'secret': decrypt(pairing.secret_encrypted)}
    if action != 'token':
        body['action'] = 'disconnect' if action == 'disconnect' else 'status'
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(
                pairing.authority + '/pairing/v1/' + ('token' if action == 'token' else 'status'),
                json=body, timeout=(3, 8), allow_redirects=False, stream=True,
                headers={'Accept': 'application/json'},
            ) as response:
                if response.status_code != 200:
                    if response.status_code == 401:
                        raise SSOError('pairing_rejected', 403)
                    if response.status_code in (400, 403, 409, 410):
                        raise SSOError('handoff_rejected', 400)
                    raise SSOError('bakney_unavailable', 503)
                raw = b''
                for chunk in response.iter_content(8192):
                    raw += chunk
                    if len(raw) > 8192:
                        raise SSOError('invalid_response', 502)
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get('protocol') != VERSION:
                    raise SSOError('invalid_response', 502)
                return result
    except (requests.RequestException, ValueError, UnicodeError):
        raise SSOError('bakney_unavailable', 503) from None
