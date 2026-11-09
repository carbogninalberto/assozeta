"""Capture the FatturaPA outputs of BE/application/serializers/invoice_serializers.py (a38 0.1.8) for the Go port's golden test.

Run with the migration interpreter from the repository root:
``python -m TESTS.adapters.go.fatturapa_golden`` (the repository root must be importable). Django is set up with
``core.settings`` read from ``selfhost/.env.example``; no database is opened.

Each case is an unsaved ``CustomerInvoice`` built from authored or seeded random field values. ``lines`` is given as
JSON text and set to ``json.loads(text)``, which is what the jsonb column yields. ``generate_invoice_html``,
``generate_invoice_xml`` and ``generate_invoice_json`` run with ``ProgressivoInvioField.get_construct_default``
patched to a fixed value. For each output the case records whether it raised, else its SHA-256 and length; the result
goes to ``BE_GO/internal/fatturapa/testdata/a38_renders.json``. Cases tagged ``unsupported`` are inputs the Go port
refuses on purpose (the reason is recorded); Go may answer ErrUnsupported only for them.

Set GOLDEN_DUMP=<dir> to also write each output there for diffing.
"""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import random

from TESTS.adapters.go.document_template_golden import setup

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'BE_GO/internal/fatturapa/testdata/a38_renders.json'
PROGRESSIVO = 'A1b2C3'  # replaced per case by a real encoding below

TEXTS = ['ASD Nuoto', 'Società Sportiva Ècole', 'Club & Co <b>', 'O\'Neil "Quote"', '', ' spaced ', 'straße', 'ΑΘΗΝΑ',
         'Line1\nLine2', 'tab\tvalue', 'x' * 60, 'a&amp;b', '<script>alert(1)</script>', 'emoji 🎉 ok', 'cr\rlf']


def base(rng=None):
    return {
        'prefix': 'FPR', 'number': 12, 'fiscal_year': 2026,
        'assignor_prefix_vat_number': 'IT', 'assignor_vat_number': '01234567890', 'assignor_tax_code': '01234567890',
        'assignor_denomination': 'ASD Esempio', 'assignor_fiscal_regime': 'RF01', 'assignor_address': 'Via Roma 1',
        'assignor_postal_code': '00100', 'assignor_city': 'Roma', 'assignor_province': 'RM', 'assignor_country': 'IT',
        'assignor_contact_email': 'info@example.test', 'assignor_contact_phone': '0612345678', 'assignor_fax': None,
        'transferor_prefix_vat_number': 'IT', 'transferor_vat_number': None, 'transferor_tax_code': 'RSSMRA80A01H501U',
        'transferor_denomination': 'Mario Rossi', 'transferor_fiscal_regime': 'RF01', 'transferor_address': 'Via Milano 2',
        'transferor_postal_code': '20100', 'transferor_city': 'Milano', 'transferor_province': 'MI', 'transferor_country': 'IT',
        'transferor_contact_email': 'mario.rossi@pec.example.test', 'transferor_contact_phone': None, 'transferor_fax': None,
        'country_transmitter_prefix': 'IT', 'id_transmitter': None, 'transmitting_format': 'FPR12', 'document_type': 'TD01',
        'currency': 'EUR', 'transmitting_date': '2026-03-15', 'causal': 'Quota associativa',
        'lines': json.dumps([{'description': 'Quota', 'quantity': 1, 'unit_of_measure': 'unita', 'unit_price': 100.0,
                              'vat': 22.0}]),
        'payment_condition': 'TP02', 'payment_modality': 'MP05', 'payment_expiry_date': '2026-04-15',
    }


AUTHORED = []


def case(name, unsupported=None, **changes):
    fields = base()
    for key, val in changes.items():
        fields[key] = json.dumps(val) if key == 'lines' and val is not None and not isinstance(val, str) else val
    AUTHORED.append({'name': name, 'fields': fields, 'unsupported': unsupported})


def line(description='Riga', quantity=1, unit='unita', price=10, vat=22, **extra):
    item = {'description': description, 'quantity': quantity, 'unit_of_measure': unit, 'unit_price': price, 'vat': vat}
    item.update(extra)
    return item


def authored():
    case('base')
    case('no-causal', causal=None)
    case('empty-causal', causal='')
    case('long-causal', causal='c' * 201)
    case('causal-200', causal='ç' * 200)
    case('special-text', assignor_denomination='Club & Co <b> "x" \'y\'', causal='a < b & c > d',
         lines=[line(description='Kit & maglia <XL> "rosso"')])
    case('vat-transferor', transferor_vat_number='09876543210', transferor_tax_code=None)
    case('no-vat-no-taxcode', transferor_tax_code=None)
    case('transferor-prefix-empty', transferor_prefix_vat_number='', transferor_vat_number='09876543210')
    case('assignor-no-vat', assignor_vat_number=None)
    case('assignor-empty-vat', assignor_vat_number='')
    case('assignor-prefix-long', assignor_prefix_vat_number='ITA')
    case('assignor-tax-code-empty', assignor_tax_code='')
    case('assignor-no-tax-code', assignor_tax_code=None)
    case('assignor-denomination-none', assignor_denomination=None)
    case('assignor-denomination-empty', assignor_denomination='')
    case('transferor-denomination-none', transferor_denomination=None)
    case('regime-bad', assignor_fiscal_regime='RF99')
    case('regime-rf19', assignor_fiscal_regime='RF19')
    case('province-empty', assignor_province='')
    case('transferor-province-empty', transferor_province='')
    case('cap-empty', assignor_postal_code='')
    case('cap-short', transferor_postal_code='123')
    case('country-empty', assignor_country='')
    case('address-empty', assignor_address='', assignor_city='')
    case('address-long', transferor_address='v' * 61)
    case('no-transferor-email', transferor_contact_email=None, id_transmitter='ABCDEFG')
    case('empty-transferor-email', transferor_contact_email='')
    case('short-pec', transferor_contact_email='a@b.itx')
    case('pec-7-ok-contatti', transferor_contact_email='ab@c.it')
    case('assignor-no-contacts', assignor_contact_email=None, assignor_contact_phone=None, assignor_fax=None)
    case('assignor-fax', assignor_fax='0611111111')
    case('assignor-phone-short', assignor_contact_phone='123')
    case('assignor-email-empty', assignor_contact_email='')
    case('transmitter-id', id_transmitter='XYZ1234')
    case('transmitter-prefix-bad', country_transmitter_prefix='ITA')
    case('doc-type-bad', document_type='TD99')
    case('doc-type-td24', document_type='TD24')
    case('currency-usd', currency='USD')
    case('format-fpa-ignored', transmitting_format='FPA12')
    case('no-transmitting-date', transmitting_date=None)
    case('numero-none', prefix=None, number=None, fiscal_year=None)
    case('numero-number-only', prefix=None, fiscal_year=None)
    case('numero-long', prefix='PREFIXLONG', number=123456789)
    case('condition-none', payment_condition=None)
    case('condition-bad', payment_condition='TP04')
    case('modality-none', payment_modality=None)
    case('modality-mp22', payment_modality='MP22')
    case('lines-null', lines=None)
    case('lines-empty', lines=[])
    case('lines-dict', lines='{"description": "x"}')
    case('lines-string', lines='"abc"')
    case('lines-number', lines='5')
    case('lines-item-string', lines=['abc'])
    case('lines-item-null', lines=[None])
    case('lines-item-list', lines=[[1, 2]])
    for key in ('description', 'quantity', 'unit_of_measure', 'unit_price', 'vat'):
        item = line()
        del item[key]
        case(f'line-missing-{key}', lines=[item])
    case('line-extra-key', lines=[line(extra='ignored')])
    case('description-none', lines=[line(description=None)])
    case('description-int', lines=[line(description=42)])
    case('description-float', lines=[line(description=1.5)])
    case('description-bool', lines=[line(description=True)])
    case('description-1000', lines=[line(description='d' * 1000)])
    case('description-1001', lines=[line(description='d' * 1001)])
    case('description-list', 'str() of a list', lines=[line(description=['a'])])
    case('description-dict', 'str() of a dict', lines=[line(description={'a': 1})])
    case('unit-none-quantity', lines=[line(unit=None)])
    case('unit-none-quantity-none', lines=[line(unit=None, quantity=None)])
    case('quantity-none-unit', lines=[line(quantity=None)])
    case('unit-long', lines=[line(unit='u' * 11)])
    case('unit-int', lines=[line(unit=5)])
    case('price-none', lines=[line(price=None)])
    case('price-none-quantity-none', lines=[line(price=None, quantity=None, unit=None)])
    case('price-string', lines=[line(price='19.99')])
    case('price-string-bad', lines=[line(price='abc')])
    case('price-string-inf', lines=[line(price='Infinity')])
    case('price-string-nan', lines=[line(price='nan')])
    case('price-string-exp', lines=[line(price='1e2')])
    case('price-string-space', 'Decimal() whitespace stripping', lines=[line(price=' 5 ')])
    case('price-string-underscore', 'Decimal() underscore grouping', lines=[line(price='1_000')])
    case('price-string-empty', lines=[line(price='')])
    case('price-list', lines=[line(price=[1])])
    case('price-dict', lines=[line(price={'a': 1})])
    case('price-bool', lines=[line(price=True)])
    case('price-false', lines=[line(price=False)])
    case('price-negative', lines=[line(price=-10)])
    case('price-negative-zero', lines=[line(price=-0.0)])
    case('price-tiny', lines=[line(price=1e-9)])
    case('price-huge', lines=[line(price=1e30)])
    case('price-big-int', lines=[line(price=10 ** 20)])
    case('price-many-digits', lines=[line(price='1234567890123.123456789')])
    case('quantity-float', lines=[line(quantity=0.1, price=19.99)])
    case('quantity-precise', lines=[line(quantity='1.2300', price='3.333')])
    case('quantity-tiny', lines=[line(quantity=1e-9)])
    case('quantity-zero', lines=[line(quantity=0)])
    case('quantity-negative-zero', lines=[line(quantity=-0.0)])
    case('quantity-bool', lines=[line(quantity=False)])
    case('ties-half-up', lines=[line(price=0.125, quantity=1), line(price=0.005, quantity=3), line(price=2.675, quantity=1)])
    case('ties-strings', lines=[line(price='0.125', quantity='1'), line(price='0.005', quantity='3', vat='10'),
                                line(price='2.675', vat='4')])
    case('tie-imposta', lines=[line(price='0.25', vat=10)])
    case('tie-imposta-neg', lines=[line(price='-0.25', vat=10)])
    case('vat-zero', lines=[line(vat=0)])
    case('vat-zero-string', lines=[line(vat='0')])
    case('vat-empty-string', lines=[line(vat='')])
    case('vat-none', lines=[line(vat=None)])
    case('vat-false', lines=[line(vat=False)])
    case('vat-100', lines=[line(vat=100)])
    case('vat-100.5', lines=[line(vat=100.5)])
    case('vat-1000', lines=[line(vat=1000)])
    case('vat-negative', lines=[line(vat=-22)])
    case('vat-half', lines=[line(vat=0.005)])
    case('vat-tiny', lines=[line(vat=0.004)])
    case('vat-list-truthy', lines=[line(vat=[1])])
    case('vat-dict-empty', lines=[line(vat={})])
    case('mixed-rates', lines=[line(vat=22, price=10), line(vat=4, price=3.5), line(vat=10, price='7.77', quantity=3),
                               line(vat=22.0, price=1), line(vat='5', price=0.1, quantity=7), line(vat=4.0, price=2)])
    case('many-lines', lines=[line(description=f'Riga {n}', price=n * 1.1, quantity=n % 4 + 1, vat=[4, 5, 10, 22][n % 4])
                              for n in range(40)])
    case('cancelling-lines', lines=[line(price=10), line(price=-10)])
    case('negative-total', lines=[line(price=-5, vat=10)])
    case('rounding-product', 'context rounding beyond 28 digits',
         lines=[line(price='999.99999999', quantity='99999999999.99999999')])
    case('rounding-product-negative', 'context rounding beyond 28 digits',
         lines=[line(price='-99999999999.9999999', quantity='9999999.99999999')])
    case('date-y1000', transmitting_date='1000-01-01', payment_expiry_date='1000-12-31')
    case('date-y999', 'year before 1000 (strftime padding)', transmitting_date='0999-01-01')
    case('unicode-text', assignor_denomination='ΑΘΗΝΑ straße 🎉', assignor_city='Città', causal='Ünïcödé 🎉',
         lines=[line(description='Ñandú 🎉\nnuova riga\ttab')])
    case('control-chars', causal='a\x01b\x7fc', lines=[line(description='x\ry')])
    case('denomination-81', transferor_denomination='d' * 81)
    case('denomination-80-astral', transferor_denomination='🎉' * 80)


def rnd_amount(rng):
    kind = rng.random()
    if kind < .3:
        return rng.randint(-3, 500)
    if kind < .6:
        return round(rng.uniform(-50, 500), rng.choice([1, 2, 3, 4]))
    if kind < .75:
        return rng.choice([0.1, 0.2, 0.3, 19.99, 33.333, 2.675, 0.125, 0.005, 1e-9, 1.005, 99.995, 0.015, 7.7])
    if kind < .9:
        return str(round(rng.uniform(0, 300), rng.choice([0, 1, 2, 3, 5, 8, 9])))
    return rng.choice([True, False, '12', '0.50', '-3.25', '100.000000001', 1000000, '0.125', '-0.005'])


def rnd_vat(rng):
    if rng.random() < .05:
        return rng.choice([1000, '0', 0.004])
    return rng.choice([4, 5, 10, 22, 22.0, '22', '10.0', 0, None, '', 100, 100.5, 4.5, 0.5, '8.875', 21.999, True, -10])


def rnd_text(rng, none=0.1):
    if rng.random() < none:
        return None
    return rng.choice(TEXTS)


def random_case(rng, number):
    fields = base()
    lines = []
    for _ in range(rng.choice([1, 1, 2, 3, 5, 8])):
        item = {'description': rnd_text(rng, .02) if rng.random() < .3 else f'Servizio {rng.randint(1, 99)}',
                'quantity': rnd_amount(rng) if rng.random() < .6 else rng.choice([1, 2, 3]),
                'unit_of_measure': rng.choice(['unita', 'ore', 'pz', '', 'x' * 10]),
                'unit_price': rnd_amount(rng) if rng.random() < .97 else None, 'vat': rnd_vat(rng)}
        if rng.random() < .15:
            item['quantity'] = item['unit_of_measure'] = None
        elif rng.random() < .03:
            item[rng.choice(['quantity', 'unit_of_measure'])] = None
        if rng.random() < .03:
            del item[rng.choice(list(item))]
        lines.append(item)
    fields['lines'] = json.dumps(lines)
    for key in ('assignor_denomination', 'transferor_denomination', 'assignor_address', 'transferor_city', 'causal'):
        if rng.random() < .25:  # address and city are NOT NULL columns
            fields[key] = rnd_text(rng, 0 if key in ('assignor_address', 'transferor_city') else .03)
    if rng.random() < .3:
        fields['transferor_vat_number'] = rng.choice(['09876543210', '', None])
    if rng.random() < .2:
        fields['transferor_tax_code'] = rng.choice([None, 'RSSMRA80A01H501U', '12345'])
    if rng.random() < .15:
        fields['transferor_contact_email'] = rng.choice([None, '', 'x@y.it', 'pec@pec.example.test'])
    if rng.random() < .15:
        fields['assignor_contact_phone'] = rng.choice([None, '1234', '0612345678', '+39 06 1234567'])
    if rng.random() < .1:
        fields['assignor_province'] = rng.choice(['', 'R', 'TO'])
    if rng.random() < .2:
        fields['number'] = rng.choice([None, 1, 99999])
        fields['prefix'] = rng.choice([None, 'A', 'FPR'])
    if rng.random() < .2:
        fields['payment_modality'] = rng.choice(['MP01', 'MP08', 'MP23', None])
        fields['payment_condition'] = rng.choice(['TP01', 'TP03', None])
    if rng.random() < .2:
        fields['transmitting_date'] = rng.choice(['2025-12-31', '2026-02-28', None])
        fields['payment_expiry_date'] = rng.choice(['2027-01-01', '2026-02-01'])
    return {'name': f'random-{number}', 'fields': fields, 'unsupported': None}


def build(fields):
    from application.models.invoices_models import CustomerInvoice
    values = dict(fields)
    for key in ('transmitting_date', 'payment_expiry_date'):
        if values[key] is not None:
            values[key] = date.fromisoformat(values[key])
    values['lines'] = None if values['lines'] is None else json.loads(values['lines'])
    return CustomerInvoice(**values)


def encode(ts, sequence):
    import a38.fields
    return a38.fields.ProgressivoInvioField()._encode_b56((ts << 16) + sequence, 10)


def main():
    dump = os.environ.get('GOLDEN_DUMP')  # setup() clears the environment
    setup()
    import a38.fields
    from application.serializers import invoice_serializers as serializers

    authored()
    rng = random.Random(20261109)
    cases = AUTHORED + [random_case(rng, n) for n in range(500)]
    progressivi = [[ts, seq, encode(ts, seq)] for ts, seq in
                   [(0, 0), (0, 1), (1, 0), (1700000000, 0), (1700000000, 5), (1794614400, 65535), (2 ** 38 - 1, 3)]]
    original = a38.fields.ProgressivoInvioField.get_construct_default
    results = []
    for number, item in enumerate(cases):
        progressivo = encode(1794614400 + number, number % 7)
        a38.fields.ProgressivoInvioField.get_construct_default = lambda self, value=progressivo: value
        outputs = {}
        for kind, function in (('html', serializers.generate_invoice_html), ('xml', serializers.generate_invoice_xml),
                               ('json', serializers.generate_invoice_json)):
            try:
                text = function(build(item['fields']))
            except Exception as error:  # every exception is the view's 500
                outputs[kind] = {'raised': type(error).__name__}
                continue
            data = text.encode('utf-8')
            outputs[kind] = {'sha256': hashlib.sha256(data).hexdigest(), 'length': len(data)}
            if dump:
                Path(dump).mkdir(parents=True, exist_ok=True)
                (Path(dump) / f'{item["name"]}.{kind}').write_bytes(data)
        results.append({**item, 'progressivo': progressivo, 'outputs': outputs})
    a38.fields.ProgressivoInvioField.get_construct_default = original
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'progressivi': progressivi, 'cases': results}, ensure_ascii=False, separators=(',', ':')) + '\n')
    raised = sum(1 for r in results if 'raised' in r['outputs']['html'])
    print(f'{len(results)} cases, {len(results) - raised} rendered, {raised} raised, '
          f'{sum(1 for r in results if r["unsupported"])} tagged unsupported')


if __name__ == '__main__':
    main()
