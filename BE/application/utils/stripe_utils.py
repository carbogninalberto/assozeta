from django.conf import settings


def stripe_public_key():
    return getattr(settings, 'STRIPE_PUBLIC_KEY', '') or ''


def stripe_secret_key():
    return getattr(settings, 'STRIPE_KEY', '') or ''


def stripe_webhook_secret():
    return getattr(settings, 'STRIPE_WEBHOOK_SECRET', '') or ''


def stripe_direct_credentials_configured():
    return bool(stripe_secret_key() and stripe_public_key())


def online_payments_available(sport_association):
    owner = sport_association.user
    return bool(owner.online_payments and stripe_direct_credentials_configured())
