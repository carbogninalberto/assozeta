from django.core.exceptions import ImproperlyConfigured
from instance.integration_configuration import effective_integration
import hashlib


def stripe_public_key():
    value = effective_integration('stripe', decrypt=False)
    return value['public_key'] if value['enabled'] else ''


def stripe_secret_key():
    value = effective_integration('stripe')
    return value['secret_key'] if value['enabled'] else ''


def stripe_webhook_secret():
    value = effective_integration('stripe')
    return value['webhook_secret'] if value['enabled'] else ''


def stripe_direct_credentials_configured():
    try:
        value = effective_integration('stripe')
    except ImproperlyConfigured:
        # A broken credential must disable payments without blocking owner login.
        return False
    return bool(value['enabled'] and value['secret_key'] and value['public_key'])


def stripe_request_options():
    key = stripe_secret_key()
    if not key:
        # An empty api_key would fall back to the SDK's process-global environment key.
        raise ImproperlyConfigured('Stripe non è configurato o è disabilitato.')
    return {'api_key': key}


def stripe_cache_identity(key):
    # Account/key changes must never reuse cached financial data from the old account.
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def online_payments_available(sport_association):
    owner = sport_association.user
    return bool(owner.online_payments and stripe_direct_credentials_configured())
