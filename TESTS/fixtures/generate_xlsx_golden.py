"""Produce the golden workbooks of BE_GO/internal/xlsxexport from Django's own code (xlsx-exports.md).

Every workbook here is built by the code Django runs, for many inputs:
- ``get_excel_base64`` (application/utils/excel_utils.py, pandas + openpyxl):
  payment, invoice and printing exports;
- ``generate_xlsx_report_from_data`` (application/utils/instructors_utils.py,
  pandas + xlsxwriter): instructor/report;
- ``_export_to_excel`` (application/views/subscriptions_views.py, pandas +
  xlsxwriter with the index column): subscription/list/export;
- ``modules_response_export``'s inline workbook (application/views/modules_views.py,
  pandas + xlsxwriter, sheet 'Risposte'), copied line for line below;
- ``export_all_data``'s inline openpyxl Workbook (application/views/profile_views.py),
  copied line for line below.

The functions are taken from the Django sources with ``ast`` (no Django
setup), so a change in Django changes the golden output. The output stores
each case's typed input, whether Django returned/raised, and the workbook as
its zip entries (name, compression method, flags, external attributes,
SHA-256 of the uncompressed part); the parts are stored once by digest.

Run with the migration interpreter (pandas 2.2.3, openpyxl 3.1.5,
XlsxWriter 3.2.2, no lxml): ``$GO_MIGRATION_PYTHON -I TESTS/fixtures/generate_xlsx_golden.py``.
"""
import ast
import base64
import hashlib
import io
import json
import logging
import random
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Optional, Union

import openpyxl
import pandas
import xlsxwriter

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'BE_GO/internal/xlsxexport/testdata/golden.json'
assert (pandas.__version__, openpyxl.__version__, xlsxwriter.__version__) == ('2.2.3', '3.1.5', '3.2.2')
assert not openpyxl.LXML


def django_function(relative, name, namespace):
    """The named top-level function of a Django source file, compiled in namespace."""
    path = ROOT / 'BE' / relative
    tree = ast.parse(path.read_text())
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[name]


NAMESPACE = {'pd': pandas, 'pandas': pandas, 'io': io, 'base64': base64, 'tempfile': tempfile, 'BytesIO': io.BytesIO,
             'logger': logging.getLogger('golden'), 'List': List, 'Any': Any, 'Union': Union, 'Optional': Optional}
get_excel_base64 = django_function('application/utils/excel_utils.py', 'get_excel_base64', NAMESPACE)
generate_xlsx_report_from_data = django_function('application/utils/instructors_utils.py', 'generate_xlsx_report_from_data', NAMESPACE)
_export_to_excel = django_function('application/views/subscriptions_views.py', '_export_to_excel', NAMESPACE)


def module_export(rows, cols):
    """modules_response_export's workbook (modules_views.py), line for line."""
    df = pandas.DataFrame(rows)
    df.columns = cols
    f = io.BytesIO()
    writer = pandas.ExcelWriter(f, engine='xlsxwriter')
    df.to_excel(writer, sheet_name='Risposte', index=False)
    worksheet = writer.sheets['Risposte']
    for i, col in enumerate(df.columns):
        column_len = len(col) + 10
        worksheet.set_column(i, i, column_len)
    writer.close()
    return f.getvalue()


def export_all_data(sheets):
    """export_all_data's workbook (profile_views.py): remove the default sheet, append rows, save."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        sheet = wb.create_sheet(title=title)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# Typed JSON values: str and None as themselves; {'int': text}, {'float': repr},
# {'decimal': text}, {'bool': b}; lists and dicts (module responses) as {'list': [...]}, {'dict': [[k, v], ...]}.
def decode(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list):
        return [decode(item) for item in value]
    (kind, raw), = value.items()
    return {'int': int, 'float': float, 'decimal': Decimal, 'bool': bool,
            'list': lambda items: [decode(item) for item in items],
            'dict': lambda pairs: {key: decode(item) for key, item in pairs}}[kind](raw)


def I(n):
    return {'int': str(n)}


def F(x):
    return {'float': repr(float(x))}


def D(text):
    return {'decimal': text}


PARTS = {}


def entries(data):
    archive = zipfile.ZipFile(io.BytesIO(data))
    out = []
    for info in archive.infolist():
        content = archive.read(info)
        digest = hashlib.sha256(content).hexdigest()
        PARTS[digest] = content.decode()
        out.append({'name': info.filename, 'method': info.compress_type, 'flags': info.flag_bits,
                    'external': info.external_attr, 'create_system': info.create_system,
                    'create_version': info.create_version, 'extract_version': info.extract_version, 'sha256': digest})
    return out


CASES = []


def case(name, kind, payload, build):
    try:
        data = build()
    except Exception as error:  # Django raises: the view's 500
        CASES.append({'name': name, 'kind': kind, 'input': payload, 'raises': type(error).__name__})
        return
    if data is None:  # get_excel_base64 logged and returned None: the views raise
        CASES.append({'name': name, 'kind': kind, 'input': payload, 'raises': 'None'})
        return
    if kind == 'excel_utils':
        data = base64.b64decode(data)
    elif kind == 'subscription':
        data = base64.b64decode(data)
    CASES.append({'name': name, 'kind': kind, 'input': payload, 'entries': entries(data)})


def excel_utils(name, columns, *data):
    payload = {'columns': columns, 'data': [list(column) for column in data]}
    case(name, 'excel_utils', payload, lambda: get_excel_base64(*[decode(column) for column in data], columns=columns))


def instructor(name, schema, rows):
    payload = {'schema': [[key, label] for key, label in schema.items()], 'rows': rows}
    data = [{key: decode(value) for key, value in zip(schema, row)} for row in rows]
    case(name, 'instructor', payload, lambda: generate_xlsx_report_from_data(data, schema))


def subscription(name, cols, rows):
    payload = {'columns': cols, 'rows': rows}

    def build():
        df = pandas.DataFrame([decode(row) for row in rows])
        df.columns = cols
        return _export_to_excel(df, cols)
    case(name, 'subscription', payload, build)


def module(name, cols, rows):
    payload = {'columns': cols, 'rows': rows}
    case(name, 'module', payload, lambda: module_export([decode(row) for row in rows], cols))


def all_data(name, sheets):
    payload = {'sheets': [[title, rows] for title, rows in sheets]}
    case(name, 'all_data', payload, lambda: export_all_data([(title, [decode(row) for row in rows]) for title, rows in sheets]))


# ---------------------------------------------------------------- get_excel_base64
PAYMENT_COLUMNS = ['Informazioni intestatario', 'Stato', 'Modalità di pagamento', 'Data creazione', 'Data pagamento',
                   'Numero ricevuta', 'Attività', 'Causali aggiuntive', 'Causale', 'Conto', 'Tipo', 'Corso', 'Importo', 'Note']
excel_utils('eu-basic', ['x', 'y', 'z', 'w', 'n'], ['a', 'bé', 'c'], [D('1.50'), D('2'), None], ['1', '2', 'x'], [None, None, None],
            ['3', '4', '5'])
excel_utils('eu-empty-rows', ['a', 'bb', 'ccc'], [], [], [])
excel_utils('eu-one-row', ['Nome'], ['Mario'])
excel_utils('eu-payments', PAYMENT_COLUMNS,
            ['Mario Rossi', 'None None', None], ['Pagato', 'In Attesa', 'Pagato'], ['-', 'card', 'cash'],
            ['2024-01-02', '2024-02-03', '2024-03-04'], ['2024-01-05', '', '2024-03-04'], [I(1), None, ''],
            ['Iscrizione', 'Corso', 'Altro'], ['', 'Cat A,Cat B', ''], ['-', 'Quota', '-'], ['Cassa', '-', 'Banca'],
            ['Entrata', 'Uscita', 'Entrata'], ['', 'Nuoto', ''], [D('10.00'), D('25.50'), D('0.00')], [None, 'nota', ''])
excel_utils('eu-ints-all', ['n'], [I(1), I(2), I(3)])
excel_utils('eu-ints-none', ['n'], [I(1), None, I(3)])
excel_utils('eu-invoice-numbers', ['numero'], [I(12), I(7), None])
excel_utils('eu-decimals-int', ['quota'], [D('10.00'), D('20'), D('0.000')])
excel_utils('eu-decimals-frac', ['quota'], [D('10.10'), D('20'), D('-3.333')])
excel_utils('eu-decimals-none', ['quota'], [D('10.10'), None, D('1e2')])
excel_utils('eu-decimal-mixed-str', ['quota'], [D('10.10'), '-', D('1')])
excel_utils('eu-floats', ['saldo'], [F(1.5), F(-2.25), F(1e16), F(0.1), F(123456789.123)])
excel_utils('eu-floats-int', ['saldo'], [F(1.0), F(-2.0), F(0.0)])
excel_utils('eu-floats-dash', ['Saldo progressivo'], [F(1.5), '-', F(3.0)])
excel_utils('eu-float-big', ['x'], [F(1e20), F(2.5)])
excel_utils('eu-float-tiny', ['x'], [F(1e-7), F(5e-324)])
excel_utils('eu-numeric-strings', ['a', 'b', 'c', 'd', 'e', 'f'],
            ['1', ' 2', '3 '], ['1.5', '.5', '1.'], ['1e3', '1E3', '+4'], ['-0', '00012', '\t7\n'], ['1', '2.5', '3'],
            ['12345678901', '1', '2'])
excel_utils('eu-not-numeric', ['a', 'b', 'c', 'd', 'e', 'f', 'g'],
            ['1', 'x'], ['1e', '2'], ['1_000', '2'], ['0x10', '1'], ['1,5', '2'], ['nan', '1'], ['', '1'])
excel_utils('eu-inf-strings', ['a', 'b', 'c'], ['inf', '1'], ['-Infinity', '2'], ['iNf', '+inf'])
excel_utils('eu-inf-floats', ['a'], [F(float('inf')), F(1.0)])
excel_utils('eu-long-digits', ['a', 'b', 'c'], ['99999999999999999999999', '1'], ['3.14159265358979323846', '1'],
            ['-9223372036854775809', '1'])
excel_utils('eu-big-int-strings', ['a'], ['12345678901234567', '1'])
excel_utils('eu-exponents', ['a', 'b'], ['1e400', '1'], ['1e-400', '2'])
excel_utils('eu-whitespace', ['a', 'b', 'c'], [' lead', 'trail ', ' both '], ['\tx', 'y\n', 'a b'], ['\xa0nbsp', 'x　', ''])
excel_utils('eu-escapes', ['a & b', '<c>', 'd"e'], ['x & y', '<tag>', 'q"uote\'s'], ['a>b', 'c<d', 'e&f'], ['&amp;', '"', "'"])
excel_utils('eu-unicode', ['città', '日本', '😀'], ['perché', 'naïve', 'Ωmega'], ['日本語', '中文', '한국어'], ['😀x', '𝔘', 'é'])
excel_utils('eu-formula', ['a', 'b'], ['=SUM(1)', '='], ['=1+1', 'x'])
excel_utils('eu-error-codes', ['a'], ['#N/A', '#DIV/0!', '#REF'])
excel_utils('eu-illegal', ['a'], ['x\x01y', 'ok'])
excel_utils('eu-newlines', ['a'], ['line1\nline2', 'cr\r\nlf', 'tab\tx'])
excel_utils('eu-long-string', ['a'], ['x' * 32770, 'y'])
excel_utils('eu-width-cap', ['h'], ['z' * 80, 'short'])
excel_utils('eu-width-header', ['a very long header name here'], ['x', 'y'])
excel_utils('eu-many-columns', [f'c{n}' for n in range(26)], *[[f'v{n}', I(n)] for n in range(26)])
excel_utils('eu-27-columns', [f'c{n}' for n in range(27)], *[[f'v{n}'] for n in range(27)])
excel_utils('eu-zip-short', ['a', 'b'], ['1', '2', '3'], ['x'])
excel_utils('eu-dup-columns', ['a', 'a'], ['1', '2'], ['x', 'y'])
excel_utils('eu-columns-mismatch', ['a', 'b', 'c'], ['1'], ['2'])
excel_utils('eu-all-none', ['a', 'b'], [None, None], ['x', None])
excel_utils('eu-mixed-int-str', ['a'], [I(1), '2', 'x'])
excel_utils('eu-mixed-int-numstr', ['a'], [I(1), '2', '3.5'])
excel_utils('eu-str-none', ['a'], ['5', None, '6'])
excel_utils('eu-float-none', ['a'], [F(1.5), None])
excel_utils('eu-neg-zero', ['a', 'b'], [F(-0.0), F(1.0)], [D('-0.00'), D('1')])
excel_utils('eu-decimal-repr', ['a'], [D('1E+2'), D('0.000001'), 'x'])
excel_utils('eu-int-huge', ['a'], [I(12345678901234567), I(1)])
excel_utils('eu-int-huge-none', ['a'], [I(12345678901234567), None])
excel_utils('eu-many-rows', ['n', 's'], [I(n) for n in range(40)], [f'row {n}' for n in range(40)])

# Seeded random numeric-looking columns: pandas.to_numeric's parser, the Int64/float64
# choice, float formatting and the str() widths.
RANDOM = random.Random(20261109)


def random_value():
    pick = RANDOM.random()
    if pick < 0.55:
        return ''.join(RANDOM.choice('0123456789.eE+- ') for _ in range(RANDOM.randint(1, 9)))
    if pick < 0.65:
        return D(f'{RANDOM.randint(-99999, 99999)}.{RANDOM.randint(0, 99):02d}')
    if pick < 0.75:
        return F(RANDOM.choice([RANDOM.uniform(-1e6, 1e6), RANDOM.randint(-1000, 1000), RANDOM.uniform(-1, 1) * 10 ** RANDOM.randint(-8, 20)]))
    if pick < 0.85:
        return I(RANDOM.randint(-10 ** 12, 10 ** 12))
    if pick < 0.92:
        return None
    return RANDOM.choice(['', '-', 'x', ' 7 ', '1e5', '0.1', '-0'])


for n in range(40):
    width = RANDOM.randint(1, 5)
    excel_utils(f'eu-random-{n:02d}', [f'col {c}' for c in range(width)],
                *[[random_value() for _ in range(RANDOM.randint(1, 6))] for _ in range(width)])

# ---------------------------------------------------------------- instructor/report (xlsxwriter)
SCHEMA = {'first_name': 'Nome', 'last_name': 'Cognome', 'course': 'Corso', 'title': 'Titolo', 'description': 'Descrizione',
          'date': 'Data', 'attendees_count': 'Numero di partecipanti', 'attendees': 'Partecipanti',
          'expected_absences': 'Assenti previsti'}
instructor('ir-empty', SCHEMA, [])
instructor('ir-one', SCHEMA, [['Luca', 'Bianchi', 'Nuoto', 'Lezione 1', '', '02/01/2024 18:00', I(2), 'Anna Verdi, Bo Neri', '']])
instructor('ir-many', SCHEMA, [
    ['Luca', 'Bianchi', 'Nuoto', 'Lezione 1', 'desc & <x>', '02/01/2024 18:00', I(2), 'Anna Verdi, Bo Neri', 'Atleta cancellato'],
    ['Luca', 'Bianchi', 'Tennis avanzato per adulti', 'L2', 'una descrizione molto lunga ' * 3, '03/01/2024 09:30', I(0), '', ''],
    ['Zoe', 'Neri', 'Nuoto', 'Lezione 1', '', '04/01/2024 10:00', I(12), 'A B, C D, E F', 'G H'],
])
instructor('ir-strings', SCHEMA, [['  Luca', 'Bianchi ', 'città', '=1+1', 'http://example.com', '04/01/2024 10:00', I(1),
                                    'x\x01y', '_x0041_']])
instructor('ir-mailto', SCHEMA, [['a', 'b', 'mailto:x@example.com', '{=SUM(1)}', 'ftp://h', 'd', I(3), 'internal:x', 'e']])
instructor('ir-numeric-looking', SCHEMA, [['1', '2', '3.5', '1e3', 'inf', 'd', I(-1), '-', '']])
instructor('ir-unicode-width', SCHEMA, [['日本語の名前', 'é' * 30, '😀😀', 't', 'd', 'x', I(100000), 'a', 'b']])

# ---------------------------------------------------------------- subscription/list/export (xlsxwriter, index column)
SIMPLE = ['Nome', 'Cognome', 'Codice fiscale', 'Stato', 'Certificato']
subscription('sub-basic', SIMPLE, [['Mario', 'Rossi', 'RSSMRA', 'Attivo', 'si'], ['Anna', 'Verdi', '', 'Sospeso', 'no']])
subscription('sub-one', SIMPLE, [['Mario', 'Rossi', 'RSSMRA', 'Attivo', 'si']])
subscription('sub-none', SIMPLE, [['Mario', None, None, 'Attivo', ''], [None, 'x', '1', '2', '3']])
subscription('sub-zero', SIMPLE, [])
subscription('sub-many', ['a' * n for n in range(1, 30)], [[f'{r}-{c}' for c in range(29)] for r in range(25)])
subscription('sub-dups', ['x', 'y'], [['same', 'same'], ['same', 'other'], ['other', 'same']])
subscription('sub-specials', SIMPLE, [['=x', 'http://a.b', ' sp', 'a\x02b', '_x0000_'], ['{=1}', 'mailto:a', 'b ', '\n', 'é']])
subscription('sub-long', SIMPLE, [['x' * 33000, 'a', 'b', 'c', 'd']])

# ---------------------------------------------------------------- modules/<id>/export (xlsxwriter, 'Risposte')
STANDARD = ['Data creazione', 'Progressivo risposta', 'Posizione in coda', 'Utente', 'Pagamento', 'Approvato', 'Allegati']
module('mod-basic', ['Nome', 'Età'] + STANDARD, [
    ['Mario', I(30), '02/01/2024 10:00', I(1), '-', 'Mario Rossi', 'Pagato', 'Approvata', 'a.pdf,b.pdf'],
    ['Anna', '-', '03/01/2024 11:00', I(2), I(4), '-', '-', 'Non approvata', ''],
])
module('mod-types', ['bool', 'float', 'list', 'dict', 'none'] + STANDARD, [
    [{'bool': True}, F(1.5), {'list': ['a', I(1)]}, {'dict': [['k', 'v'], ['n', None]]}, None, 'd', I(1), '-', '-', '-', 'Approvata', ''],
    [{'bool': False}, F(2.0), {'list': []}, {'dict': []}, None, 'd', I(2), '-', '-', '-', 'Approvata', ''],
])
module('mod-int-none', ['n'] + STANDARD, [[I(5), 'd', I(1), '-', '-', '-', 'x', ''], [None, 'd', I(2), '-', '-', '-', 'x', '']])
module('mod-mixed', ['m'] + STANDARD, [[I(5), 'd', I(1), '-', '-', '-', 'x', ''], [{'bool': True}, 'd', I(2), '-', '-', '-', 'x', ''],
                                       ['s', 'd', I(3), '-', '-', '-', 'x', '']])
module('mod-dup-labels', ['Firma', 'Firma'] + STANDARD, [['a', 'b', 'd', I(1), '-', '-', '-', 'x', '']])
module('mod-strings', ['q'] + STANDARD, [['http://x.y/z', 'd', I(1), '-', '-', '-', 'x', ''], ['=A1', 'd', I(2), '-', '-', '-', 'x', '']])
module('mod-floats', ['f'] + STANDARD, [[F(0.1), 'd', I(1), '-', '-', '-', 'x', ''], [F(1e16), 'd', I(2), '-', '-', '-', 'x', ''],
                                        [F(1e-5), 'd', I(3), '-', '-', '-', 'x', ''], [F(123456.75), 'd', I(4), '-', '-', '-', 'x', '']])

# ---------------------------------------------------------------- export-all-data (openpyxl)
all_data('all-empty', [('Iscrizioni', [['a', 'b']]), ('Pagamenti', [['c']])])
all_data('all-basic', [('Iscrizioni', [['status_flag', 'type', 'date'], ['Attivo', 'Socio', '2024-01-01 10:00:00+00:00'],
                                       ['Sospeso', '-', '-', 'tutor1', 'tutor2']]),
                       ('Pagamenti', [['amount', 'course_name'], ['10.00', '-'], ['=x', '#N/A']])])
all_data('all-specials', [('Iscrizioni', [['h'], [' lead'], ['a & <b>'], ['x' * 32800], ['città 😀']]), ('Pagamenti', [['h']])])
all_data('all-illegal', [('Iscrizioni', [['h'], ['a\x07b']]), ('Pagamenti', [['h']])])
all_data('all-wide', [('Iscrizioni', [[f'h{n}' for n in range(60)], [str(n) for n in range(60)]]), ('Pagamenti', [[]])])

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps({'versions': {'pandas': pandas.__version__, 'openpyxl': openpyxl.__version__,
                                           'xlsxwriter': xlsxwriter.__version__},
                              'cases': CASES, 'parts': PARTS}, ensure_ascii=False, indent=0, sort_keys=True) + '\n')
print(len(CASES), 'cases', sum('raises' in item for item in CASES), 'raise', len(PARTS), 'parts')
