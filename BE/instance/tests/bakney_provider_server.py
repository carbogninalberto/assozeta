"""Launch a disposable checkout of the REAL Bakney backend for integration tests.

Mount this file outside that checkout. No product source or protocol is replaced.
The only networking exception is the fixture's private DNS address; its pinned
HTTPS transport still validates the certificate and hostname on port 443.
"""
import os
import json
import socket
import sys
from pathlib import Path

if os.environ.get('SSO_BROWSER_FIXTURE') != '1' or not os.environ.get('DBNAME', '').startswith('sso_browser_'):
    raise RuntimeError('This fixture requires an explicitly isolated database.')

sys.path.insert(0, '/app')
os.environ['DJANGO_SETTINGS_MODULE'] = 'core.settings'
os.environ['CURRENT_HOST'] = 'https://login.bakney.test'
os.environ['SSL_CERT_FILE'] = '/fixture-tls/cert.pem'

# Separate synthetic signing keys: a receiver JWT cannot authenticate to Bakney.
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
os.environ['JWT_SECRET_KEY'] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()).decode()
os.environ['JWT_PUBLIC_KEY'] = key.public_key().public_bytes(serialization.Encoding.PEM,
                                                          serialization.PublicFormat.SubjectPublicKeyInfo).decode()

import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from application.models import User, SportAssociation, Associate
from application.models.pairing_models import HandoffCode, InstancePairing
from application.pairing import protocol
from core.urls import urlpatterns
import pyotp

settings.ROOT_URLCONF = __name__
from django.test.utils import override_settings
fixture_cache = override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
fixture_cache.enable()
settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
settings.SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
settings.DRF_API_LOGGER_DATABASE = False
call_command('migrate', verbosity=0, interactive=False)
ids = json.loads(Path('/fixture-tls/identities.json').read_text())
password = 'synthetic-browser-password'
owner = User.objects.create_user(user_id=ids['owner'], username='sso-browser-owner', password=password,
                                role=User.ASSOCIATION)
association = SportAssociation.objects.create(sport_association_id=ids['association'], user=owner, denomination='Browser Club')
users = {}
for name in ('alice', 'bob'):
    users[name] = User.objects.create_user(user_id=ids[name], username='sso-browser-' + name,
        password=password, role=User.ATHLETE, first_name=name.title())
    Associate.objects.create(user=users[name], sport_association=association, disabled=False, draft=False)
users['alice'].two_fa = True
users['alice'].two_fa_secret = pyotp.random_base32()
users['alice'].save()

original_addresses = protocol.public_addresses
def fixture_addresses(host):
    if host == 'club.assozeta.test':
        return [socket.gethostbyname(host)]
    return original_addresses(host)
protocol.public_addresses = fixture_addresses


@csrf_exempt
def fixture(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        if data.get('reset_throttles'):
            from django.core.cache import cache
            cache.clear()
        if data.get('expire_codes'):
            HandoffCode.objects.update(expires_at=timezone.now())
        if data.get('wrong_pkce'):
            HandoffCode.objects.update(pkce_challenge='A' * 43)
        if 'forwarding' in data:
            # Use the real service for generation invalidation.
            from application.pairing.service import invalidate
            from django.db import transaction
            with transaction.atomic():
                pairing = InstancePairing.objects.select_for_update().get()
                if data['forwarding']:
                    pairing.forwarding = True
                    pairing.save()
                else:
                    invalidate(pairing)
        return JsonResponse({'ok': True})
    return JsonResponse({'password': password, 'otp': pyotp.TOTP(users['alice'].two_fa_secret).now(),
                         'users': {name: user.username for name, user in {'owner': owner, **users}.items()}})


urlpatterns = [path('fixture', fixture), *urlpatterns]

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('core.asgi:application', host='0.0.0.0', port=8000, access_log=False, workers=1)
