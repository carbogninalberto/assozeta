"""Capture Django's document-template renders and filter_mentions output for the Go ports' golden tests.

Run with the migration interpreter from the repository root:
``python -I -m TESTS.adapters.go.document_template_golden`` (``-I`` off when run as a module: the
repository root must be importable). Django is set up with ``core.settings`` read from
``selfhost/.env.example``; no database is opened.

Templates: every document template (BE/templates/document/...) is rendered with
``render_to_string`` under ``translation.override(language)`` for authored contexts plus
seeded random ones. Context values are tagged ({"t": kind, "v": value}); objects are plain
Python objects read by attribute, "call" values are zero-argument callables (model methods
and properties), and an attribute an object lacks is simply not set. Each render's SHA-256
and length go to ``BE_GO/internal/djtemplate/testdata/django_renders.json`` with its inputs,
together with Django's WEEKDAYS and MONTHS in Italian and English.

Mentions: ``application.models.utils.filter_mentions`` runs on authored HTML (and random
compositions of supported fragments) with tagged context objects; the outputs go to
``BE_GO/internal/mentions/testdata/django_mentions.json``.

Set GOLDEN_DUMP=<dir> to also write each render's HTML there for diffing.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
RENDERS = ROOT / 'BE_GO/internal/djtemplate/testdata/django_renders.json'
MENTIONS = ROOT / 'BE_GO/internal/mentions/testdata/django_mentions.json'


# --- tagged values -----------------------------------------------------------------------------
def s(v):
    return {'t': 'str', 'v': v}


def safe(v):
    return {'t': 'safe', 'v': v}


def b(v):
    return {'t': 'bool', 'v': v}


def i(v):
    return {'t': 'int', 'v': v}


def big(v):
    """An integer beyond int64, by its decimal text (json.loads gives int)."""
    return {'t': 'bigint', 'v': v}


def flt(v):
    return {'t': 'float', 'v': v}


def flt_text(v):
    """A float by the text float() reads (inf, -inf, -0.0, 1e-320)."""
    return {'t': 'float_text', 'v': v}


def dec(v):
    return {'t': 'decimal', 'v': v}


def d(v):
    return {'t': 'date', 'v': v}


def dt(v):
    return {'t': 'datetime', 'v': v}


def lst(*items):
    return {'t': 'list', 'v': list(items)}


def dct(*pairs):
    return {'t': 'dict', 'v': [[k, v] for k, v in pairs]}


def obj(**attrs):
    return {'t': 'obj', 'v': [[k, v] for k, v in attrs.items()]}


def call(v):
    return {'t': 'call', 'v': v}


NONE = {'t': 'none'}


class Obj:
    pass


def value(tagged):
    from django.utils.safestring import mark_safe
    from decimal import Decimal
    kind = tagged['t']
    v = tagged.get('v')
    if kind == 'none':
        return None
    if kind == 'str':
        return v
    if kind == 'safe':
        return mark_safe(v)
    if kind in ('bool', 'int', 'float'):
        return v
    if kind == 'bigint':
        return int(v)
    if kind == 'float_text':
        return float(v)
    if kind == 'decimal':
        return Decimal(v)
    if kind == 'date':
        return date.fromisoformat(v)
    if kind == 'datetime':
        return datetime.fromisoformat(v)
    if kind == 'list':
        return [value(item) for item in v]
    if kind == 'dict':
        return {k: value(item) for k, item in v}
    if kind == 'obj':
        result = Obj()
        for k, item in v:
            setattr(result, k, value(item))
        return result
    if kind == 'call':
        inner = value(v)
        return lambda: inner
    raise ValueError(kind)


# --- pools -------------------------------------------------------------------------------------
TEXTS = ['Mario', 'Rossi', 'Società Sportiva Ècole', 'Club & Co <b>', 'O\'Neil "Quote"', '', ' spaced ', 'straße',
         'ΑΘΗΝΑ', 'çà è ì ò ù', 'Line1\nLine2', 'tab\tvalue', 'x' * 40, 'RSSMRA80A01H501U', '00100', 'Via Roma 1',
         '+39 333 1234567', 'mario.rossi@example.test', '<script>alert(1)</script>', 'a&amp;b']
HTML = ['<p>Header <b>bold</b></p>', '<p><br></p>', '<p></p>', '', '<div style="color:red">Ciao &amp; addio</div>',
        '<p>Riga1<br>Riga2</p>', 'plain text & more', '<img src="data:image/png;base64,AAAA"/>',
        'riga uno\nriga <due>\r\ntre\rquattro', 'Nota: 5 > 3 & "ok"']


def rnd_text(rng, none=True):
    if none and rng.random() < .15:
        return NONE
    return s(rng.choice(TEXTS))


def rnd_html(rng):
    if rng.random() < .15:
        return NONE
    return s(rng.choice(HTML))


def rnd_date(rng):
    roll = rng.random()
    if roll < .15:
        return NONE
    if roll < .2:
        return s('____________________________')
    return d(date(rng.randint(1940, 2024), rng.randint(1, 12), rng.randint(1, 28)).isoformat())


def rnd_datetime(rng):
    if rng.random() < .15:
        return NONE
    return dt(datetime(rng.randint(2000, 2030), rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59),
                       rng.randint(0, 59), rng.choice([0, 123456]), tzinfo=timezone.utc).isoformat())


def rnd_number(rng):
    return rng.choice([i(0), i(25), i(-3), flt(12.5), flt(0.125), flt(2.675), flt(-0.001), flt(1e16), dec('10.50'), dec('0.125'),
                       dec('-1.005'), dec('1234567.89'), dec('0'), dec('2.5E+1'), b(True)])


def person(rng, full_name_attr='get_full_name'):
    first, last = rnd_text(rng), rnd_text(rng)
    full = s(rng.choice(TEXTS))
    attrs = dict(tax_code=rnd_text(rng), sex=rng.choice([s('M'), s('F'), s('A'), NONE, s('')]), born_date=rnd_date(rng),
                 born_city=rnd_text(rng), address=rnd_text(rng), address_city=rnd_text(rng), address_cap=rnd_text(rng),
                 phone=rnd_text(rng), email=rnd_text(rng), first_name=first, last_name=last)
    attrs[full_name_attr] = call(full) if full_name_attr == 'get_full_name' else full
    return attrs


def association(rng, sections=True):
    attrs = dict(logo=rnd_text(rng), denomination=rnd_text(rng), regulation=rnd_html(rng), demand=rnd_html(rng),
                 address_city=rnd_text(rng), address=rnd_text(rng), address_cap=rnd_text(rng), tax_code=rnd_text(rng),
                 federation=rnd_text(rng), enroll_number=rnd_text(rng))
    if sections:
        roll = rng.random()
        if roll < .1:
            attrs['additional_sections'] = lst()
        elif roll < .15:
            attrs['additional_sections'] = NONE
        else:
            items = []
            for _ in range(rng.randint(1, 3)):
                show = lst(*[i(n) for n in (1, 2, 3) if rng.random() < .6])
                pairs = [('name', rnd_text(rng)), ('text', rnd_html(rng))]
                if rng.random() < .85:
                    pairs.append(('show_to', show))
                items.append(dct(*pairs))
            attrs['additional_sections'] = lst(*items)
    return attrs


def subscription_context(rng):
    associate = person(rng)
    associate['is_minor_now'] = b(rng.random() < .5)
    associate['main_tutor'] = obj(**person(rng)) if rng.random() < .7 else NONE
    roll = rng.random()
    if roll < .3:
        fields = NONE
    elif roll < .4:
        fields = lst()
    else:
        fields = lst(*[dct(('props', dct(('label', rnd_text(rng)), ('value', rnd_text(rng))))) for _ in range(rng.randint(1, 3))])
    signature = rng.random() < .3
    subscription = obj(sport_association=obj(**association(rng)), creation_date=rnd_datetime(rng),
                       type=rng.choice([i(1), i(2), i(3), NONE, i(4)]), role=rng.choice([i(n) for n in range(1, 9)] + [NONE]),
                       associate=obj(**associate), additional_fields=fields, has_signature=b(signature),
                       get_signature_base64=call(s('data:image/png;base64,iVBORw0KGgo=') if signature else NONE))
    roll = rng.random()
    if roll < .4:
        payment_data = NONE
    elif roll < .5:
        payment_data = dct()
    else:
        payment_data = dct(('subscription_data', dct(('name', rnd_text(rng)),
                                                     ('subscription_fee', rng.choice([i(30), flt(25.5), flt(0.005), dec('12.345'), s('30')])))))
    preview = rng.random() < .3
    regulation = lst(*[i(n) for n in (1, 2, 3) if rng.random() < .6])
    demand = lst(*[i(n) for n in (1, 2, 3) if rng.random() < .6])
    return {'uid': rng.choice([NONE, s('a1000000-0000-4000-8000-000000000001')]), 'subscription': subscription,
            'payment_data': payment_data, 'show_regulation_to': regulation, 'show_demand_to': demand,
            'is_preview': b(preview), 'header': rnd_html(rng), 'footer': rng.choice([s(''), s('<p>Footer</p>'), s('<p></p>')])}


def invoice_context(rng):
    associate_attrs = person(rng)
    associate_attrs['is_minor'] = b(rng.random() < .5)
    associate_attrs['tutor'] = obj(**person(rng)) if rng.random() < .6 else NONE
    has_associate = rng.random() < .7
    supplier = obj(name=rnd_text(rng), address=rnd_text(rng), tax_code=rnd_text(rng)) if rng.random() < .5 else NONE
    category = obj(name=rng.choice([s('Quota'), s(''), s('Corsi & Attività'), NONE])) if rng.random() < .8 else NONE
    payment = obj(payment_date=rnd_datetime(rng), associate=obj(**associate_attrs) if has_associate else NONE, supplier=supplier,
                  payment_category=category, description=rnd_text(rng), notes=rnd_html(rng),
                  subject=rng.choice([i(0), i(1), i(2), i(3)]))
    fees = [dec('0.00'), dec('10.00'), dec('25.50'), dec('100.005')]
    invoice = obj(sport_association=obj(logo=rnd_text(rng), denomination=rnd_text(rng)), number=rng.choice([i(7), NONE, i(0)]),
                  activity_fee=rng.choice(fees), membership_fee=rng.choice(fees), description=rnd_html(rng))
    roll = rng.random()
    if roll < .4:
        categories = NONE
    elif roll < .5:
        categories = dct()
    else:
        categories = dct(*[(name, rnd_number(rng)) for name in rng.sample(['quota', 'corso (1)', 'Tessera', 'kit & maglia'],
                                                                         rng.randint(1, 3))])
    roll = rng.random()
    if roll < .4:
        custom = NONE
    else:
        custom = dct(('type_of_associate', rng.choice([s('scuola-asc'), s('asd-o-ssd'), s('altro')])),
                     ('is_multiple', b(rng.random() < .3)), ('denomination', rnd_text(rng)), ('city', rnd_text(rng)),
                     ('province', rnd_text(rng)), ('address', rnd_text(rng)), ('cap', rnd_text(rng)), ('country', rnd_text(rng)),
                     ('tax_code', rnd_text(rng)), ('email', rnd_text(rng)))
    sub = obj(type=rng.choice([i(1), i(2), i(3)])) if rng.random() < .7 else NONE
    return {'uid': s('a1000000-0000-4000-8000-000000000002'), 'extra_text_invoices': rnd_html(rng),
            'total_amount': rng.choice([dec('35.50'), dec('0.00'), dec('125.005')]), 'body_style': rng.choice([s(''), s('opacity:0.7;')]),
            'payment': payment, 'course_name': rng.choice([s('Nuoto'), NONE, s('Quota')]),
            'enumerate_invoices': rng.choice([b(True), b(False), NONE]), 'payment_categories': categories, 'custom_data': custom,
            'stamp_free': b(rng.random() < .5), 'invoice': invoice, 'payment_method': rng.choice([s('Contanti'), s('Bonifico SEPA')]),
            'sub': sub, 'sub_memebership': NONE, 'sub_period': rng.choice([s('2026'), s('25/26'), s('')]),
            'sub_membership_period': rng.choice([s('2026'), s('')]), 'override_label': b(rng.random() < .5),
            'got_plan_name': rng.choice([s('-'), s('Piano A')]), 'got_membership_plan_name': rng.choice([s('-'), s('Tessera B')]),
            'got_plan': b(rng.random() < .5), 'got_membership_plan': b(rng.random() < .5), 'header': rnd_html(rng),
            'footer': rng.choice([s(''), s('<p>Footer</p>'), s('<br><br><b>RICEVUTA ANNULLATA (non è più valida)</b>')])}


def compensation_context(rng):
    instructor = obj(is_volunteer=b(rng.random() < .4), first_name=rnd_text(rng, none=False), last_name=rnd_text(rng, none=False),
                     tax_code=rnd_text(rng), address=rnd_text(rng), civic_number=rnd_text(rng), address_city=rnd_text(rng),
                     born_date=rnd_date(rng), born_city=rnd_text(rng), born_province=rnd_text(rng), phone=rnd_text(rng),
                     email=rnd_text(rng), study_title=rnd_text(rng), role=rnd_text(rng), stipulated_contract_in=rnd_date(rng))
    signature = obj(signature=rnd_text(rng)) if rng.random() < .5 else NONE
    payment = obj(sport_association=obj(**association(rng, sections=False)), instructor=instructor,
                  amount=rng.choice([dec('80.00'), dec('0.00'), dec('1234.5'), dec('0.005'), dec('99.995')]),
                  payment_date=rnd_datetime(rng), signature=signature, notes=rnd_html(rng))
    return {'payment': payment, 'from_date': rnd_date(rng), 'to_date': rnd_date(rng)}


def medical_context(rng):
    associate = person(rng, full_name_attr='full_name')
    associate['is_minor_now'] = b(rng.random() < .5)
    if rng.random() < .3:
        associate['tutor'] = obj(full_name=rnd_text(rng))
    meta = rng.choice([NONE, dct(), dct(('first_affiliation', b(True)), ('renewal', b(False))),
                       dct(('first_affiliation', NONE), ('renewal', b(True)))])
    appointment = obj(date=rnd_date(rng), sport=rnd_text(rng))
    return {'medical_appointment': appointment, 'sport_association': obj(**association(rng, sections=False)),
            'associate': obj(**associate), 'meta': meta}


def model_context(rng):
    return {'template': rnd_html(rng), 'header': rnd_html(rng), 'footer': rnd_html(rng)}


# --- authored JSON-column values ---------------------------------------------------------------
# The JSON columns read with json.loads (subscription.additional_fields,
# association.additional_sections and additional_fields, subscription.payment_meta,
# medical_appointment.meta) can hold every JSON value; these contexts render
# each of them through the expressions the document templates use.
def field(label, value):
    return dct(('props', dct(('label', label), ('value', value))))


def section(name, text, show_to):
    return dct(('name', name), ('text', text), ('show_to', show_to))


def authored_subscription(additional_fields=NONE, sections=None, subscription_type=None, is_preview=False,
                          payment_data=NONE):
    subscription = obj(
        sport_association=obj(logo=s(''), denomination=s('ASD Autore & <Figli>'), regulation=NONE, demand=NONE,
                             address_city=s('Torino'), address=s('Via Roma 1'), address_cap=s('10100'),
                             tax_code=s('01234567890'), federation=s('FIDAL'), enroll_number=s('ab-12'),
                             additional_sections=lst() if sections is None else sections),
        creation_date=dt('2026-03-04T10:00:00+00:00'), type=subscription_type, role=i(1),
        associate=obj(first_name=s('Autore'), last_name=s('Rossi'), tax_code=s('RSSMRA80A01L219X'), sex=s('M'),
                      born_date=d('1980-01-01'), born_city=s('Torino'), address=s('Via Po 2'), address_city=s('Torino'),
                      address_cap=s('10123'), phone=s('011 123'), email=s('mario@example.test'),
                      get_full_name=call(s('Autore Rossi')), is_minor_now=b(False)),
        additional_fields=additional_fields, has_signature=b(False), get_signature_base64=call(NONE))
    return {'uid': s('a1000000-0000-4000-8000-000000985046'), 'subscription': subscription,
            'payment_data': payment_data, 'show_regulation_to': lst(i(1), i(3)), 'show_demand_to': lst(i(2)),
            'is_preview': b(is_preview), 'header': s('<p>Intestazione</p>'), 'footer': s('')}


def authored_medical(meta):
    associate = obj(first_name=s('Autore'), last_name=s('Rossi'), tax_code=s('RSSMRA80A01L219X'), sex=s('M'),
                    born_date=d('1980-01-01'), born_city=s('Torino'), address=s('Via Po 2'), address_city=s('Torino'),
                    address_cap=s('10123'), phone=s('011 123'), email=s('mario@example.test'),
                    full_name=s('Autore Rossi'), is_minor_now=b(False))
    return {'medical_appointment': obj(date=d('2026-05-06'), sport=s('Calcio & corsa')),
            'sport_association': obj(logo=s('data:image/png;base64,QUJD'), denomination=s('ASD Autore')),
            'associate': associate, 'meta': meta}


JSON_FIELDS = [
    field(s('Intero grande'), big('123456789012345678901234567890')),
    field(s('Intero negativo'), big('-123456789012345678901234567890')),
    field(s('Decimale'), flt(1.5)),
    field(s('Subnormale'), flt(1e-320)),
    field(s('Infinito'), flt_text('inf')),
    field(s('Zero negativo'), flt(-0.0)),
    field(s('Vero'), b(True)),
    field(s('Nullo'), NONE),
    field(s('Lista'), lst(i(1), s('due'), NONE, flt(1.5), big('12345678901234567890123'))),
    field(s('Mappa'), dct(('chiave', s('valore')), ('numero', i(7)), ('lista', lst(b(False))))),
    field(s('Virgolette'), s('O\'Neil "Quote" \\ fine')),
    field(s('Es'), s('ß ŉ ǰ ﬁ')),
    field(s('Greco'), s('ΑΘΗΝΑ ΟΔΥΣΣΕΥΣ ς')),
    field(s('Sigma'), s('Σοφός')),
    # Elements that are not field dicts: field.props is missing.
    s('testo'),
    i(3),
]
LABEL_FIELDS = [
    field(big('123456789012345678901234567890'), NONE),
    field(big('-123456789012345678901234567890'), NONE),
    field(lst(s('x'), i(2)), NONE),
    field(dct(('a', i(1))), NONE),
    field(flt(1.5), NONE),
    field(flt(1e-320), NONE),
    field(flt(1e300), NONE),
    field(flt(-1.5e300), NONE),
    field(flt(1e-200), NONE),
    field(flt(1.2345678901234567e-200), NONE),
    field(flt(1e-180), NONE),
    field(flt_text('inf'), NONE),
    field(flt_text('-inf'), NONE),
    field(flt(-0.0), NONE),
    field(b(True), NONE),
    field(NONE, NONE),
    field(i(5), NONE),
]
SECTIONS = [
    section(s('Sezione stringa'), s('<p>Testo &amp; <b>grassetto</b></p>'), s('12')),
    section(s('Sezione lista'), NONE,
            lst(i(9), big('123456789012345678901234567890'), flt_text('inf'), s('2'), NONE, b(True), lst(i(1)),
                dct(('a', i(1))), i(2))),
    section(big('-12345678901234567890'), lst(i(1), dct(('k', b(True)))), dct(('1', i(1)), ('2', i(2)))),
    section(big('12345678901234567890'), lst(i(1), s('due')), lst(i(2))),
    section(flt_text('inf'), flt(1e-320), lst(i(2))),
    section(dct(('a', s('b'))), flt(-0.0), lst(i(2))),
    section(NONE, b(True), lst(i(2))),
    section(lst(s('x'), s('ß')), big('9' * 40), i(2)),
    section(s('Solo membri'), s(''), b(True)),
    dct(('name', s('Senza show_to')), ('text', s('testo'))),
    s('stringa'),
]
PAYMENT_DATA = [
    dct(('subscription_data', dct(('name', s('Annuale <b>')), ('subscription_fee', big('12345678901234567890'))))),
    dct(('subscription_data', dct(('name', s('Enorme')), ('subscription_fee', big('1' + '0' * 400))))),
    dct(('subscription_data', dct(('name', s('Ridotta')), ('subscription_fee', flt(12.5))))),
    dct(('subscription_data', dct(('name', s('Infinita')), ('subscription_fee', flt_text('inf'))))),
    dct(('subscription_data', dct(('name', s('Negativa')), ('subscription_fee', flt(-0.0))))),
    dct(('subscription_data', dct(('name', dct(('a', s('b')))), ('subscription_fee', flt(1.5))))),
    dct(('subscription_data', dct(('name', lst(s('x'), i(1))), ('subscription_fee', b(True))))),
    dct(('subscription_data', dct(('name', s('Testo')), ('subscription_fee', s('30'))))),
    lst(i(1)),
    big('12345678901234567890'),
    dct(),
]
MEDICAL_METAS = [
    dct(('first_affiliation', big('123456789012345678901234567890')), ('renewal', big('-12345678901234567890'))),
    dct(('first_affiliation', flt(1e-320)), ('renewal', flt(-0.0))),
    dct(('first_affiliation', flt_text('inf')), ('renewal', flt_text('-inf'))),
    dct(('first_affiliation', lst()), ('renewal', lst(i(1), dct(('a', NONE))))),
    dct(('first_affiliation', dct()), ('renewal', dct(('k', s('v'))))),
    dct(('first_affiliation', s('')), ('renewal', s('no'))),
    dct(('first_affiliation', NONE), ('renewal', NONE)),
    dct(),
    big('42'),
    s('testo'),
    lst(i(1)),
    NONE,
]


TEMPLATES = [
    ('document/application/subscription.html', subscription_context, ('it', 'en')),
    ('document/application/subscription_classic.html', subscription_context, ('it', 'en')),
    ('document/application/invoice.html', invoice_context, ('it', 'en')),
    ('document/application/invoice_classic.html', invoice_context, ('it', 'en')),
    ('document/application/compensation.html', compensation_context, ('it', 'en')),
    ('document/application/model.html', model_context, ('it', 'en')),
    ('document/medical-appointments/agonistica.html', medical_context, ('it', 'en')),
    ('document/medical-appointments/non-agonistica.html', medical_context, ('it', 'en')),
]
PER_TEMPLATE = 40

# Authored renders of the JSON-column values through every expression the
# document templates use (the seeded renders above keep their contexts).
AUTHORED = []
for language, template in (('it', 'document/application/subscription.html'),
                           ('en', 'document/application/subscription_classic.html')):
    base = template.rsplit('/', 1)[1].removesuffix('.html')
    AUTHORED.append((f'authored-json-fields-{base}', template, language,
                     authored_subscription(additional_fields=lst(*JSON_FIELDS), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-labels-{base}', template, language,
                     authored_subscription(additional_fields=lst(*LABEL_FIELDS), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-fields-dict-{base}', template, language,
                     authored_subscription(additional_fields=dct(('ß', s('x')), ("O'Neil", NONE), ('ǰ', lst(i(1)))),
                                           subscription_type=i(2))))
    AUTHORED.append((f'authored-json-fields-string-{base}', template, language,
                     authored_subscription(additional_fields=s('aßŉǰ"'), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-fields-number-{base}', template, language,
                     authored_subscription(additional_fields=big('12345678901234567890123'), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-fields-float-{base}', template, language,
                     authored_subscription(additional_fields=flt(1.5), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-fields-bool-{base}', template, language,
                     authored_subscription(additional_fields=b(True), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-fields-empty-{base}', template, language,
                     authored_subscription(additional_fields=lst(), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-sections-{base}', template, language,
                     authored_subscription(sections=lst(*SECTIONS), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-sections-preview-{base}', template, language,
                     authored_subscription(sections=lst(*SECTIONS), subscription_type=NONE, is_preview=True)))
    AUTHORED.append((f'authored-json-sections-string-{base}', template, language,
                     authored_subscription(sections=s('ab'), subscription_type=i(2))))
    AUTHORED.append((f'authored-json-sections-number-{base}', template, language,
                     authored_subscription(sections=big('12'), subscription_type=i(2))))
    for index, payment_data in enumerate(PAYMENT_DATA):
        AUTHORED.append((f'authored-json-payment-{index}-{base}', template, language,
                         authored_subscription(subscription_type=i(2), payment_data=payment_data)))
for index, meta in enumerate(MEDICAL_METAS):
    AUTHORED.append((f'authored-json-meta-{index}', 'document/medical-appointments/agonistica.html', 'it',
                     authored_medical(meta)))


# --- mentions ----------------------------------------------------------------------------------
def mention(key, label='x', extra=''):
    return f'<span class="mention" data-type="mention" key="{key}"{extra}>@{label}</span>'


MENTION_CASES = [
    ('none', None), ('empty', ''), ('plain', 'testo semplice'), ('paragraph', '<p>Ciao</p>'),
    ('mention-denomination', f'<p>{mention("sport_association.denomination")}</p>'),
    ('mention-nested-user', f'<p>Scrivi a {mention("sport_association.user.email")}.</p>'),
    ('mention-associate-date', f'<p>Nato il {mention("associate.born_date")}</p>'),
    ('mention-other-today', f'<p>{mention("other.today")} {mention("other.courses_list")}</p>'),
    ('mention-unknown-object', f'<p>{mention("team.name")}</p>'),
    ('mention-no-dot', f'<p>{mention("denomination")}</p>'),
    ('mention-empty-path', f'<p>{mention("associate.")}</p>'),
    ('mention-missing-key', '<p><span class="mention" data-type="mention">@nome</span></p>'),
    ('mention-empty-key', '<p><span class="mention" data-type="mention" key="">@nome resto</span></p>'),
    ('mention-no-data-type', '<p><span class="mention" key="associate.first_name">@nome</span> @nome</p>'),
    ('mention-extra-class', f'<p><span class="foo  mention" data-type="mention" key="associate.last_name">x</span></p>'),
    ('mention-class-joined', '<p><span class="mention" data-type="mention" key="associate.tax_code" id="m1">x</span></p>'),
    ('mention-value-escape', f'<p>{mention("associate.address")}</p>'),
    ('mention-value-alias', f'<p>{mention("associate.notes")}</p>'),
    ('mention-strip-date-like', f'<p>{mention("associate.phone")}</p>'),
    ('alias-simple', '<p>Gentile @nome @cognome,</p>'),
    ('alias-case', '<p>@NOME @Cognome @CodiceFiscale @cf</p>'),
    ('alias-longest', '<p>@codicefiscaletutore @nometutore @emailtutore @telefonotutore @cognometutore</p>'),
    ('alias-boundaries', '<p>mail@nome x@@nome _@nome é@nome @nomex @nome_ @nome1 @nome. (@nome)</p>'),
    ('alias-other', '<p>@dataodierna - @listacorsi - @numeroricevuta @dataricevuta @importototale</p>'),
    ('alias-unknown-objects', '<p>@importoattivita @importoiscrizione</p>'),
    ('alias-in-mention-parent', '<p><span class="mention">@nome</span><span class="x">@nome</span></p>'),
    ('alias-accented-follow', '<p>@nomeè @capì @cap!</p>'),
    ('entities', '<p>&amp; &lt;b&gt; &quot;q&quot; &apos;a&apos; a&nbsp;b &#39; &#x27; &#233; &#X1F600;</p>'),
    ('bare-ampersand', '<p>Rossi & Figli &; fine</p>'),
    ('attributes-sorted', '<p style="color:red" class="b  a" id="x" data-z="1">t</p>'),
    ('attributes-quotes', '<p title=\'say "hi"\' data-a="it&apos;s" data-b=\'both " and &apos;\'>q</p>'),
    ('attributes-empty', '<input disabled type="checkbox"><p class="">c</p>'),
    ('attributes-uppercase', '<P CLASS="Big" Data-Type="X">u</P>'),
    ('attribute-entities', '<a href="https://x.test/?a=1&amp;b=2" title="a &lt; b &gt; c">l</a>'),
    ('void-elements', '<p>a<br/>b<br />c<br>d<br>e<hr><img src="j.png"/><img src="i.png" alt=""></p>'),
    ('self-closing', '<p/><span/>after<div class="d"/>'),
    ('whitespace', '<p>  </p>\n<p>\n  \n</p>\t<b> x </b>\r\n<i>\t</i>'),
    ('whitespace-nbsp', '<p>&nbsp;</p><p> &nbsp; </p><p> </p>'),
    ('unclosed', '<p>Ciao <b>mondo'),
    ('nested', '<div><ul><li>uno</li><li><b>due</b> <i>tre</i></li></ul><table><tr><td>c</td></tr></table></div>'),
    ('unicode', '<p>Città Società Ελλάδα 東京 😀 ‘quotes’ – dash</p>'),
    ('mention-inside-mention', '<p><span class="mention" data-type="mention" key="associate.first_name">'
                               '<span class="mention" data-type="mention" key="associate.last_name">x</span></span></p>'),
    ('mixed', '<h1>Modulo</h1><p>Io sottoscritto @nome @cognome, ' + mention('associate.tax_code') +
              ', socio di ' + mention('sport_association.denomination') + ' (@dataodierna)</p>'),
    ('text-gt', '<p>a > b</p>'),
    # TipTap's CodeBlock: whitespace-only strings inside <pre> are kept (bs4's preserve_whitespace_tags).
    ('pre-code-block', '<p>Prima</p><pre><code>const a = 1;\n\n  if (a &lt; 2) {\n    b();\n  }\n</code></pre><p>Dopo</p>'),
    ('pre-language', '<pre><code class="language-python">def f():\n    return @nome\n</code></pre>'),
    ('pre-whitespace-only', '<pre>   </pre><pre>\n\t\n</pre><pre><code>  </code>\n<b> </b></pre><p>  </p><p>\n </p>'),
    ('pre-leading-newline', '<pre>\nriga\n</pre>'),
    ('pre-mention', '<pre><code>  ' + mention('associate.first_name') + '\n  @cognome  \n</code></pre>'),
    ('pre-nested', '<div><pre><span> </span>\n<i>\n</i></pre> <span> </span></div>'),
    ('pre-unclosed', '<p>x</p><pre><code>  \n  '),
    ('pre-uppercase', '<PRE>  <CODE>  </CODE>  </PRE>'),
]


def mention_context(rng):
    def text():
        return rng.choice([s('Mario'), s('  Rossi  '), s('A & B <c>'), s('2026-03-04'), s('@nome'), s('Ελλάδα'), NONE,
                           i(42), b(True), dec('10.50'), d('1980-01-02'), dt('2026-03-04T05:06:07.000089+00:00'), s('')])
    roll = rng.random()
    context = {}
    if roll < .9:
        context['sport_association'] = obj(denomination=text(), tax_code=text(), user=obj(email=text()) if rng.random() < .8 else NONE)
    if rng.random() < .8:
        context['associate'] = obj(first_name=text(), last_name=text(), tax_code=text(), born_date=text(), born_city=text(),
                                   address=text(), address_city=text(), address_cap=text(), nationality=text(), email=text(),
                                   phone=text(), notes=text())
    if rng.random() < .6:
        context['main_tutor'] = obj(first_name=text(), last_name=text(), tax_code=text(), email=text(), phone=text())
    if rng.random() < .5:
        context['invoice'] = obj(number=text(), creation_date=text(), activity_fee=text(), membership_fee=text())
    other = [('today', s('09/11/2026'))]
    if rng.random() < .7:
        other.append(('courses_list', rng.choice([s('Nuoto, Yoga'), s(''), s('A & B')])))
    context['other'] = dct(*other)
    return context


FRAGMENTS = ['<p>', '</p>', 'testo ', ' ', '\n', '@nome', '@cognome', '@cf', '&amp;', '&nbsp;', '<br>', '<b>', '</b>',
             mention('associate.first_name'), mention('sport_association.denomination'), mention('other.today'),
             '<span class="mention">@email</span>', 'é', '@', '@@nome', '<i>x</i>']


def random_markup(rng):
    parts, open_tags = [], []
    for _ in range(rng.randint(1, 12)):
        piece = rng.choice(FRAGMENTS)
        if piece in ('<p>', '<b>'):
            open_tags.append(piece[1:-1])
        elif piece in ('</p>', '</b>'):
            if not open_tags or open_tags[-1] != piece[2:-1]:
                continue
            open_tags.pop()
        parts.append(piece)
    while open_tags and rng.random() < .7:
        parts.append(f'</{open_tags.pop()}>')
    return ''.join(parts)


def setup():
    import environ
    os.environ.clear()
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='golden-only',
                      JWT_PUBLIC_KEY='golden-only', DJANGO_SETTINGS_MODULE='core.settings', DBHOST='127.0.0.1', DBPORT='1')
    sys.path.insert(0, str(ROOT / 'BE'))
    import logging
    logging.disable(logging.CRITICAL)
    import django
    django.setup()


def main():
    setup()
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.utils import translation
    from django.utils.dates import MONTHS, WEEKDAYS
    from application.models.utils import filter_mentions
    assert settings.TIME_ZONE == 'UTC' and settings.USE_TZ and not settings.USE_THOUSAND_SEPARATOR

    dump = os.environ.get('GOLDEN_DUMP')
    rng = random.Random(20261109)
    renders = []
    for template, builder, languages in TEMPLATES:
        for number in range(PER_TEMPLATE):
            context = builder(rng)
            language = languages[number % len(languages)]
            name = f'{template.rsplit("/", 1)[1]}-{number}'
            with translation.override(language):
                try:
                    html = render_to_string(template, {key: value(item) for key, item in context.items()})
                except Exception as error:  # the 500 page: Go must fail as well
                    html, failure = None, f'{type(error).__name__}: {error}'
                else:
                    failure = None
            entry = {'name': name, 'template': template, 'language': language, 'context': context}
            if html is None:
                entry['error'] = failure
            else:
                raw = html.encode()
                entry.update(sha256=hashlib.sha256(raw).hexdigest(), length=len(raw))
                if dump:
                    Path(dump, name + '.html').write_bytes(raw)
            renders.append(entry)
    for name, template, language, context in AUTHORED:
        with translation.override(language):
            try:
                html = render_to_string(template, {key: value(item) for key, item in context.items()})
            except Exception as error:  # the 500 page: Go must fail as well
                html, failure = None, f'{type(error).__name__}: {error}'
            else:
                failure = None
        entry = {'name': name, 'template': template, 'language': language, 'context': context}
        if html is None:
            entry['error'] = failure
        else:
            raw = html.encode()
            entry.update(sha256=hashlib.sha256(raw).hexdigest(), length=len(raw))
            if dump:
                Path(dump, name + '.html').write_bytes(raw)
        renders.append(entry)
    names = {}
    for language in ('it', 'en'):
        with translation.override(language):
            names[language] = {'weekdays': [str(WEEKDAYS[n]) for n in range(7)],
                               'months': [str(MONTHS[n]) for n in range(1, 13)]}
    RENDERS.parent.mkdir(parents=True, exist_ok=True)
    RENDERS.write_text(json.dumps({'django': __import__('django').__version__, 'names': names, 'renders': renders},
                                  ensure_ascii=False, separators=(',', ':')) + '\n')

    cases = []
    inputs = list(MENTION_CASES) + [(f'random-{n}', random_markup(rng)) for n in range(120)]
    for name, html in inputs:
        for variant in range(3):
            context = mention_context(rng)
            output = filter_mentions(html, context_objects={key: value(item) for key, item in context.items()})
            cases.append({'name': f'{name}-{variant}', 'html': html, 'context': context, 'output': output})
    MENTIONS.parent.mkdir(parents=True, exist_ok=True)
    MENTIONS.write_text(json.dumps({'bs4': __import__('bs4').__version__, 'cases': cases}, ensure_ascii=False, separators=(',', ':')) + '\n')
    print(len(renders), 'renders,', len(cases), 'mention cases')


if __name__ == '__main__':
    main()
