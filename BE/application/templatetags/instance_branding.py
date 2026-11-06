"""Instance branding for transactional email clients (literal colors, absolute URLs)."""
import re
from django import template
from django.conf import settings
from instance.defaults import DEFAULT_PRIMARY_COLOR
from instance.models import InstanceConfiguration

register = template.Library()

BAKNEY_LOGO_URL = 'https://bakney.com/images/logo.png'
BAKNEY_SITE_URL = 'https://bakney.com'


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


def _palette(config):
    color = config.primary_color if config and config.self_hosted else DEFAULT_PRIMARY_COLOR
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', color or ''):
        color = DEFAULT_PRIMARY_COLOR
    color = color.lower()
    light = luminance(color)
    foreground = '#ffffff' if 1.05 / (light + .05) >= (light + .05) / .05 else '#000000'
    target = 0 if foreground == '#ffffff' else 255
    hover = '#' + ''.join(f'{round(int(color[i:i+2], 16) * .88 + target * .12):02x}' for i in (1, 3, 5))
    return {'primary': color, 'hover': hover, 'foreground': foreground}


def _logo(config):
    """Header logo: the instance's uploaded logo when self-hosted, Bakney's otherwise.

    Self-hosted instances without an uploaded logo get no image (the default
    brand logo is SVG, which most email clients do not render); the template
    shows the instance name instead.
    """
    if config and config.self_hosted:
        path = config.logo_path or ''
        if not path:
            return None
        if not re.match(r'https?://', path):
            path = f'{settings.APP_URL}/{path.lstrip("/")}'
        if config.updated_at:
            path = f'{path}?v={config.updated_at.strftime("%Y%m%d%H%M%S%f")}'
        return {'url': path, 'href': settings.APP_URL}
    if settings.IS_WHITELABEL:
        return None
    return {'url': BAKNEY_LOGO_URL, 'href': BAKNEY_SITE_URL}


@register.simple_tag
def instance_brand_palette():
    return _palette(InstanceConfiguration.get_config())


@register.simple_tag
def instance_email_brand():
    config = InstanceConfiguration.get_config()
    self_hosted = bool(config and config.self_hosted)
    return {
        **_palette(config),
        'app_name': settings.WHITELABEL_NAME,
        'name': (config.name if self_hosted and config.name else settings.WHITELABEL_NAME),
        'self_hosted': self_hosted,
        'logo': _logo(config),
    }
