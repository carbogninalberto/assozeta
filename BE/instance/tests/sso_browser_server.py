"""Disposable browser fixture, explicitly launched by the SSO browser harness."""
import os

if os.environ.get('SSO_BROWSER_FIXTURE') != '1' or not os.environ.get('DBNAME', '').startswith('sso_browser_'):
    raise RuntimeError('This fixture requires an explicitly isolated database.')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
import django
django.setup()

import json
from django.conf import settings
from django.core.management import call_command
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
import requests

from application.models import User, SportAssociation, Associate, Instructor
from application.services.jwt_token_service import JWTTokenService
from instance.models import InstanceConfiguration
from instance.sso.models import BakneyPairing, BakneyLogin
from core.urls import urlpatterns

# Trust the harness CA, rather than disabling certificate verification.
class FixtureSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.verify = '/fixture-tls/cert.pem'


requests.Session = FixtureSession
settings.ROOT_URLCONF = __name__
settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
call_command('migrate', verbosity=0, interactive=False)
owner, _ = User.objects.get_or_create(username='sso-browser-owner', defaults={'role': User.ASSOCIATION})
association, _ = SportAssociation.objects.get_or_create(user=owner, defaults={'denomination': 'Browser Club'})
config, _ = InstanceConfiguration.objects.get_or_create(defaults={
    'domain': 'club.assozeta.test:5443', 'name': 'Browser Club', 'primary_association': association,
    'setup_provenance': 'import',
})
users = {}
for name in ('alice', 'bob'):
    user, _ = User.objects.get_or_create(username='sso-browser-' + name, defaults={'role': User.ATHLETE, 'first_name': name.title()})
    Associate.objects.get_or_create(user=user, sport_association=association)
    users[name] = user


@csrf_exempt
def fixture(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        if data.get('reset'):
            BakneyLogin.objects.all().delete()
            BakneyPairing.objects.all().delete()
            cache.clear()
        if 'eligible' in data:
            users['alice'].is_active = data['eligible']
            users['alice'].save(update_fields=['is_active'])
        return JsonResponse({'ok': True})
    return JsonResponse({
        'association_id': str(association.pk),
        'owner': {**JWTTokenService.build_login_response(owner, JWTTokenService.generate_tokens_for_user(owner)), 'user_id': str(owner.pk)},
        'users': {name: {**JWTTokenService.build_login_response(user, JWTTokenService.generate_tokens_for_user(user)),
                        'user_id': str(user.pk), 'username': user.username, 'first_name': user.first_name}
                  for name, user in users.items()},
    })


urlpatterns = [path('fixture', fixture), *urlpatterns]

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('core.asgi:application', host='0.0.0.0', port=8000, access_log=False)
