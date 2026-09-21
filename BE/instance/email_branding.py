"""Runtime branding shared by account emails and the public instance config."""
from urllib.parse import urljoin

from django.conf import settings

from .models import InstanceConfiguration
from .serializers import DEFAULT_LOGO_URL, InstanceConfigSerializer


def email_branding():
    config = InstanceConfiguration.get_config()
    oem = InstanceConfigSerializer(config).get_oem(config) if config else {
        'name': settings.WHITELABEL_NAME, 'logo': DEFAULT_LOGO_URL,
    }
    return {
        'brand_name': oem['name'],
        'brand_logo_url': urljoin(settings.APP_URL + '/', oem['logo']),
        'app_host': settings.APP_URL,
    }
