"""Literal brand colors for transactional email clients (no CSS variables)."""
import re
from django import template
from instance.defaults import DEFAULT_PRIMARY_COLOR
from instance.models import InstanceConfiguration

register = template.Library()


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


@register.simple_tag
def instance_brand_palette():
    config = InstanceConfiguration.get_config()
    color = config.primary_color if config and config.self_hosted else DEFAULT_PRIMARY_COLOR
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', color or ''):
        color = DEFAULT_PRIMARY_COLOR
    color = color.lower()
    light = luminance(color)
    foreground = '#ffffff' if 1.05 / (light + .05) >= (light + .05) / .05 else '#000000'
    target = 0 if foreground == '#ffffff' else 255
    hover = '#' + ''.join(f'{round(int(color[i:i+2], 16) * .88 + target * .12):02x}' for i in (1, 3, 5))
    return {'primary': color, 'hover': hover, 'foreground': foreground}
