"""System email configuration, shared by HTTP processes and Celery workers.

Read for each send rather than caching process-local settings. Environment
configuration remains authoritative until the owner explicitly saves an override.
"""
import base64
import hashlib
from copy import copy
from email.utils import formataddr, parseaddr

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django.core.exceptions import ImproperlyConfigured

from .models import InstanceConfiguration


def cipher(secret=None):
    key = hashlib.sha256(('assozeta:instance-email:v1:' + (secret or settings.SECRET_KEY)).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_password(value):
    return cipher().encrypt(value.encode()).decode() if value else ''


def decrypt_password(value):
    if not value:
        return ''
    for secret in [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', [])]:
        try:
            return cipher(secret).decrypt(value.encode()).decode()
        except InvalidToken:
            continue
    raise ImproperlyConfigured('La credenziale email salvata deve essere reinserita.')


def effective_email(config=None):
    config = config if config is not None else InstanceConfiguration.get_config()
    if config and config.self_hosted and config.email_settings:
        return {**config.email_settings, 'password': decrypt_password(config.email_password_encrypted),
                'source': 'instance', 'revision': config.email_revision}
    name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    return {'source': 'environment', 'host': settings.EMAIL_HOST or '',
            'port': int(settings.EMAIL_PORT or 465), 'username': settings.EMAIL_HOST_USER or '',
            'password': settings.EMAIL_HOST_PASSWORD or '',
            'security': 'invalid' if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL else 'tls' if settings.EMAIL_USE_TLS else 'ssl' if settings.EMAIL_USE_SSL else 'none',
            'from_email': address if address and address != 'None' else '', 'sender_name': name,
            'revision': config.email_revision if config else 0}


def public_email(config):
    try:
        value = effective_email(config)
        credential_error = False
    except ImproperlyConfigured:
        value = {**config.email_settings, 'source': 'instance', 'revision': config.email_revision, 'password': ''}
        credential_error = True
    value['password_configured'] = bool(config.email_password_encrypted) if value['source'] == 'instance' else bool(value['password'])
    value.pop('password', None)
    return {**value, 'credential_error': credential_error, 'restart_required': False,
            'application': 'Nuovi invii da API e worker; le connessioni già aperte terminano con la configurazione precedente.'}


def smtp_connection(value):
    if value['security'] not in ('tls', 'ssl', 'none'):
        raise ImproperlyConfigured('Scegli una sola modalità di sicurezza SMTP: TLS o SSL.')
    return SMTPBackend(host=value['host'], port=value['port'], username=value['username'],
                       password=value['password'], use_tls=value['security'] == 'tls',
                       use_ssl=value['security'] == 'ssl', timeout=5, fail_silently=False)


class EmailBackend(BaseEmailBackend):
    """Apply the latest system transport and sender to each batch of emails.

    Association-specific SMTP connections remain separate. Failure to read/decrypt
    saved configuration fails closed rather than silently using stale credentials.
    """
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.transport_options = {key: value for key, value in kwargs.items()
                                  if key in ('host', 'port', 'username', 'password', 'use_tls', 'use_ssl', 'timeout') and value is not None}

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        value = effective_email()
        connection = smtp_connection(value)
        if value['source'] == 'environment':
            for key, option in self.transport_options.items():
                setattr(connection, key, option)
        email_messages = list(email_messages)
        if value['source'] == 'instance':
            email_messages = [copy(message) for message in email_messages]
            for message in email_messages:
                message.from_email = formataddr((value['sender_name'], value['from_email']))
        connection.fail_silently = self.fail_silently
        return connection.send_messages(email_messages)
