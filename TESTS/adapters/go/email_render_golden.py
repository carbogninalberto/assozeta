"""Capture Django's render_to_string output for the Go e-mail renderer's golden tests.

Run with the migration interpreter from the repository root:
``python -m TESTS.adapters.go.email_render_golden``
Django is set up with ``core.settings`` read from ``selfhost/.env.example``
(no database is opened: ``InstanceConfiguration.get_config`` is replaced by
the authored, unsaved configuration of each case). Every authored context is
rendered with ``render_to_string`` under ``translation.override(language)``
and written with its inputs to
``BE_GO/internal/emailrender/testdata/django_renders.json``, together with
each Django language's DECIMAL_SEPARATOR, the instance_branding palette of
sampled colors (and a digest of the text color over all 2^24 colors), the
linearized channel values and the hover channel table. The Go test re-renders
the same inputs and requires identical bytes.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import struct
import sys

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/emailrender/testdata/django_renders.json'

# Tagged context values: {"t": kind, "v": value}.
def s(v):
    return {'t': 'str', 'v': v}


def dec(v):
    return {'t': 'decimal', 'v': v}


def flt(v):
    return {'t': 'float', 'v': v}


def obj(**items):
    return {'t': 'dict', 'v': items}


NONE = {'t': 'none'}
APP = 'https://app.example.test'
SETTINGS = {'APP_URL': APP, 'WHITELABEL_NAME': 'Bakney Sport', 'IS_WHITELABEL': False}

CONFIGS = {
    'none': None,
    'cloud': {'self_hosted': False, 'primary_color': '#ff0000', 'name': 'Ignored', 'logo_path': 'x.png',
              'updated_at': '2026-03-04T05:06:07.000089Z'},
    'self-default': {'self_hosted': True, 'primary_color': '#351DC2', 'name': 'Polisportiva <Sud>', 'logo_path': '',
                     'updated_at': '2026-01-01T00:00:00Z'},
    'self-logo': {'self_hosted': True, 'primary_color': '#FFD700', 'name': '', 'logo_path': '//media/logo.png',
                  'updated_at': '2026-05-06T07:08:09.123456Z'},
    'self-url-logo': {'self_hosted': True, 'primary_color': 'not-a-color', 'name': 'ASD "Tre"',
                      'logo_path': 'https://cdn.example.test/l.png?a=1&b=2', 'updated_at': '2025-12-31T23:59:59.999999Z'},
    'self-dark': {'self_hosted': True, 'primary_color': '#0A0b0C', 'name': 'Club', 'logo_path': 'logo.svg',
                  'updated_at': '2026-02-03T04:05:06Z'},
}

COLLABORATOR = 'email/account/email_welcome_collaborator_message.html'
PENDING = 'email/payment/pending.html'
RECEIPT = 'email/payment/payment_receipt.html'


def collaborator(denomination, token='0f1e2d3c4b5a69788796a5b4c3d2e1f0', host=APP):
    return {'email': s('a@example.test'), 'token': s(token), 'sport_association': obj(denomination=denomination),
            'app_host': s(host), 'settings': obj(WHITELABEL_NAME=s('Bakney Sport'), IS_WHITELABEL={'t': 'bool', 'v': False})}


def pending(amount, description, enabled, name='Mario Rossi', association=s('ASD Esempio')):
    return {'recipient_name': s(name), 'description': s(description), 'amount': amount, 'application_name': s('Bakney Sport'),
            'application_url': s(APP), 'association_name': association, 'online_payments_enabled': {'t': 'bool', 'v': enabled}}


def receipt(amount, number, description, name='Mario Rossi', association=s('ASD Esempio')):
    return {'recipient_name': s(name), 'amount': amount, 'receipt_number': number, 'payment_date': s('05/03/2026'),
            'description': description, 'download_url': s(APP + '/api/document/retrieve/x?download=true&token=y'),
            'association_name': association}


CASES = [
    ('collaborator-cloud', COLLABORATOR, collaborator(s('ASD Esempio')), 'it', 'cloud'),
    ('collaborator-none-config', COLLABORATOR, collaborator(s("L'Aquila & <Co>")), 'it', 'none'),
    ('collaborator-null-denomination', COLLABORATOR, collaborator(NONE), 'en', 'self-default'),
    ('collaborator-logo', COLLABORATOR, collaborator(s('Società "Uno"'), host='http://h/x?a=1&b=2'), 'it', 'self-logo'),
    ('collaborator-url-logo', COLLABORATOR, collaborator(s('Dark')), 'it', 'self-url-logo'),
    ('pending-it', PENDING, pending(dec('15.50'), "per l'iscrizione ", True), 'it', 'cloud'),
    ('pending-en', PENDING, pending(dec('15.50'), 'per il corso ', False), 'en', 'cloud'),
    ('pending-de-negative', PENDING, pending(dec('-0.05'), 'Quota <b> ', True), 'de', 'self-dark'),
    ('pending-zero', PENDING, pending(dec('0.00'), '', False, name='None None', association=NONE), 'it', 'none'),
    ('pending-big', PENDING, pending(dec('9999999.99'), 'x\ny ', True), 'fr', 'self-logo'),
    ('pending-int', PENDING, pending({'t': 'int', 'v': 12}, 'z ', True), 'it', 'cloud'),
    ('pending-float', PENDING, pending(flt(1e16), 'f ', True), 'it', 'cloud'),
    ('pending-small-float', PENDING, pending(flt(1.5e-05), 'f ', True), 'pt-br', 'cloud'),
    ('pending-plain-float', PENDING, pending(flt(-2.5), 'f ', True), 'es', 'cloud'),
    ('receipt-it', RECEIPT, receipt(flt(10.1 + 0.2), {'t': 'int', 'v': 7}, s('Quota\r\nannuale & <altro>\rfine')), 'it', 'cloud'),
    ('receipt-en', RECEIPT, receipt(flt(30.0 + 20.0), {'t': 'int', 'v': 1234}, s('Ricevuta')), 'en', 'self-default'),
    ('receipt-null-number', RECEIPT, receipt(flt(0.005 + 0.0), NONE, NONE), 'it', 'self-logo'),
    ('receipt-half-up', RECEIPT, receipt(flt(2.675 + 0.0), {'t': 'int', 'v': -3}, s('')), 'nl', 'self-url-logo'),
    ('receipt-negative', RECEIPT, receipt(flt(-0.01 + -0.004), {'t': 'int', 'v': 0}, s("d'x")), 'it', 'self-dark'),
    ('receipt-negative-zero', RECEIPT, receipt(flt(-0.0 + -0.0), {'t': 'int', 'v': 1}, s('-')), 'it', 'cloud'),
    ('receipt-large', RECEIPT, receipt(flt(9999999.99 + 9999999.99), {'t': 'int', 'v': 2}, s('l')), 'en-gb', 'cloud'),
    ('receipt-exponent', RECEIPT, receipt(flt(1e-07), {'t': 'int', 'v': 2}, s('e')), 'it', 'cloud'),
    ('receipt-decimal', RECEIPT, receipt(dec('12.345'), {'t': 'int', 'v': 2}, s('d')), 'it', 'cloud'),
    ('receipt-int', RECEIPT, receipt({'t': 'int', 'v': 40}, {'t': 'int', 'v': 2}, s('i')), 'it', 'cloud'),
    ('receipt-missing', RECEIPT, {'recipient_name': s('Solo nome')}, 'it', 'cloud'),
]


def value(tagged):
    from decimal import Decimal
    kind = tagged['t']
    if kind in ('str', 'bool', 'int'):
        return tagged['v']
    if kind == 'none':
        return None
    if kind == 'decimal':
        return Decimal(tagged['v'])
    if kind == 'float':
        return float(tagged['v'])
    if kind == 'dict':
        return {key: value(item) for key, item in tagged['v'].items()}
    raise ValueError(kind)


def setup():
    import environ
    os.environ.clear()
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='golden-only',
                      JWT_PUBLIC_KEY='golden-only', DJANGO_SETTINGS_MODULE='core.settings', DBHOST='127.0.0.1', DBPORT='1',
                      APP_URL=SETTINGS['APP_URL'], WHITELABEL_NAME=SETTINGS['WHITELABEL_NAME'], IS_WHITELABEL='False')
    sys.path.insert(0, str(ROOT / 'BE'))
    import django
    django.setup()


def configuration(spec):
    from instance.models import InstanceConfiguration
    if spec is None:
        return None
    config = InstanceConfiguration(self_hosted=spec['self_hosted'], primary_color=spec['primary_color'], name=spec['name'],
                                   logo_path=spec['logo_path'])
    config.updated_at = datetime.fromisoformat(spec['updated_at'].replace('Z', '+00:00')).astimezone(timezone.utc)
    return config


def main():
    setup()
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.utils import translation
    from django.utils.formats import get_format
    from application.templatetags import instance_branding
    from instance.models import InstanceConfiguration
    assert settings.APP_URL == APP and settings.WHITELABEL_NAME == SETTINGS['WHITELABEL_NAME'] and settings.IS_WHITELABEL is False
    assert settings.LANGUAGE_CODE == 'it' and not settings.USE_THOUSAND_SEPARATOR

    renders = []
    for name, template, context, language, config in CASES:
        InstanceConfiguration.get_config = classmethod(lambda cls, spec=CONFIGS[config]: configuration(spec))
        with translation.override(language):
            html = render_to_string(template, {key: value(item) for key, item in context.items()})
        renders.append({'name': name, 'template': template, 'context': context, 'language': language, 'config': config,
                        'html': html})
    # The whitelabel logo branch (no self-hosted configuration).
    whitelabel = []
    for config in ('none', 'cloud', 'self-logo'):
        settings.IS_WHITELABEL = True
        try:
            brand = instance_branding._logo(configuration(CONFIGS[config]))
        finally:
            settings.IS_WHITELABEL = False
        whitelabel.append({'config': config, 'logo': brand})

    separators = {}
    for code, _ in settings.LANGUAGES:
        with translation.override(code):
            separators[code] = get_format('DECIMAL_SEPARATOR', code)

    rng = random.Random(20261109)
    colors = sorted({f'#{c:02x}{c:02x}{c:02x}' for c in range(256)} | {f'#{rng.randrange(1 << 24):06x}' for _ in range(3000)}
                    | {'#351dc2', '#FFD700', '#abcdef', '#ABCDEF'})
    palettes = []
    for color in colors:
        config = InstanceConfiguration(self_hosted=True, primary_color=color)
        palettes.append({'color': color, **instance_branding._palette(config)})
    # Text colour over every 24-bit colour: one byte per colour, 1 for black.
    digest = hashlib.sha256()
    linear = []
    for c in range(256):
        v = c / 255
        linear.append(v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
    buffer = bytearray(1 << 16)
    for red in range(256):
        for green in range(256):
            base = green << 8
            for blue in range(256):
                light = instance_branding.luminance(f'#{red:02x}{green:02x}{blue:02x}')
                buffer[base | blue] = 0 if 1.05 / (light + .05) >= (light + .05) / .05 else 1
        digest.update(buffer)
    hover = {str(target): [round(c * .88 + target * .12) for c in range(256)] for target in (0, 255)}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        'django': __import__('django').__version__, 'settings': SETTINGS, 'configs': CONFIGS, 'renders': renders,
        'whitelabel_logo': whitelabel, 'decimal_separators': separators, 'palettes': palettes,
        'foreground_black_sha256': digest.hexdigest(),
        'linear_bits': [struct.unpack('<Q', struct.pack('<d', v))[0] for v in linear], 'hover': hover,
    }, ensure_ascii=False, indent=1) + '\n')
    print(len(renders), 'renders ->', OUTPUT.relative_to(ROOT))


if __name__ == '__main__':
    main()
