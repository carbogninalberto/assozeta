"""Shared integration settings. Every new operation reads the current override."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PROVIDERS = {
    'stripe': {'public_key': 'STRIPE_PUBLIC_KEY', 'secret_key': 'STRIPE_KEY',
               'webhook_secret': 'STRIPE_WEBHOOK_SECRET'},
    'google': {'client_id': 'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY'},
    'apple': {'client_id': 'SOCIAL_AUTH_APPLE_ID_CLIENT'},
}
SECRET_FIELDS = {'stripe': ('secret_key', 'webhook_secret'), 'google': (), 'apple': ()}
UNSET = object()


def cipher(secret):
    key = hashlib.sha256(('assozeta:instance-integrations:v1:' + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value):
    return cipher(settings.SECRET_KEY).encrypt(value.encode()).decode() if value else ''


def decrypt_secret(value):
    if not value:
        return ''
    for secret in [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', [])]:
        try:
            return cipher(secret).decrypt(value.encode()).decode()
        except InvalidToken:
            continue
    raise ImproperlyConfigured('Reinserisci o rimuovi la credenziale salvata dell’integrazione.')


def effective_integration(provider, config=UNSET, decrypt=True):
    if config is UNSET:
        from .models import InstanceConfiguration
        config = InstanceConfiguration.get_config()
    record = (config.integration_settings or {}).get(provider, {}) if config and config.self_hosted else {}
    value = record.get('settings')
    source = 'instance' if value is not None else 'environment'
    if value is None:
        value = {key: getattr(settings, name, '') or '' for key, name in PROVIDERS[provider].items()}
        value['enabled'] = any(value.values())
        if provider == 'google' and config and config.self_hosted:
            value['enabled'] = value['enabled'] and config.get_display_settings().get('login', {}).get('allowOauthLogin', False)
    else:
        value = dict(value)
        if decrypt:
            for key in SECRET_FIELDS[provider]:
                value[key] = decrypt_secret(value.get(key, ''))
    return {**value, 'source': source, 'revision': record.get('revision', 0)}


def public_integration(provider, config):
    value = effective_integration(provider, config, decrypt=False)
    credential_error = False
    for key in SECRET_FIELDS[provider]:
        secret = value.pop(key, '')
        value[key + '_configured'] = bool(secret)
        if value['source'] == 'instance':
            try:
                decrypt_secret(secret)
            except ImproperlyConfigured:
                credential_error = True
    return {**value, 'provider': provider, 'credential_error': credential_error,
            'restart_required': False,
            'webhook_url': settings.APP_URL.rstrip('/') + '/api/stripe/webhook' if provider == 'stripe' else None,
            'authorized_origin': settings.APP_URL if provider == 'google' else None,
            'web_login_available': provider == 'google' if provider != 'stripe' else None}


def provider_client_id(provider):
    value = effective_integration(provider)
    return value['client_id'] if value['enabled'] else ''
