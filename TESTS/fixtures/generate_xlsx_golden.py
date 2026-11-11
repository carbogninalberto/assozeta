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
import importlib.util
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


def load_xlsx_text():
    """application/utils/xlsx_text.py, which imports no Django."""
    path = ROOT / 'BE/application/utils/xlsx_text.py'
    spec = importlib.util.spec_from_file_location('xlsx_text', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


xlsx_safe_frame = load_xlsx_text().xlsx_safe_frame
NAMESPACE = {'xlsx_safe_frame': xlsx_safe_frame, 'pd': pandas, 'pandas': pandas, 'io': io, 'base64': base64, 'tempfile': tempfile, 'BytesIO': io.BytesIO,
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
    xlsx_safe_frame(df).to_excel(writer, sheet_name='Risposte', index=False)
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
# Duplicate names (course/list's tag columns can repeat a fixed column or each other).
excel_utils('eu-dup-empty', ['a', 'b', 'a'], [], [], [])
excel_utils('eu-dup-ints-none', ['n', 'x', 'n'], [I(1), I(2), I(30)], ['5', '6', '7'], [I(3), None, I(4)])
excel_utils('eu-dup-floats', ['f', 'f'], [F(1.0), F(2.5)], [F(3.0), F(4.0)])
excel_utils('eu-dup-many-rows', ['k', 'Nome', 'k', 'k'], *[[f'v{n}' * (n + 1) for n in range(9)] for _ in range(3)], ['assegnato'] * 9)
excel_utils('eu-dup-long', ['Nome', 'Tag', 'Nome'], ['x' * 60, 'y'], ['', 'assegnato'], ['assegnato', ''])
excel_utils('eu-dup-wide-rows', ['a', 'a'], [str(n) for n in range(60)], ['z'] * 60)
excel_utils('eu-dup-27-columns', ['c'] * 27, *[['v'] for _ in range(27)])
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
# Bool columns: only bools become Int64 1/0, bools next to ints or floats take their numeric kind, next to text they stay bool cells.
excel_utils('eu-bool-only', ['b'], [{'bool': True}, {'bool': False}, {'bool': True}])
excel_utils('eu-bool-none', ['b'], [{'bool': True}, None, {'bool': False}])
excel_utils('eu-bool-int', ['b'], [{'bool': True}, I(2), {'bool': False}])
excel_utils('eu-bool-float', ['b'], [{'bool': True}, F(1.5)])
excel_utils('eu-bool-text', ['b', 'n'], [{'bool': True}, 'x', {'bool': False}], [I(1), I(2), I(3)])
excel_utils('eu-bool-numeric-text', ['b'], [{'bool': True}, '5', {'bool': False}])
excel_utils('eu-bool-dup', ['b', 'b'], [{'bool': True}, {'bool': False}], [I(1), I(2)])

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
instructor('ir-rich-text', SCHEMA, [['<r><t>b</t></r>', '<r>a & b</r>', 'x<r>y</r>', '<r>open', '</r>', '04/01/2024 10:00', I(1),
                                      '<r>_x0041_\x01</r>', ' <r>sp</r> ']])

# ---------------------------------------------------------------- subscription/list/export (xlsxwriter, index column)
SIMPLE = ['Nome', 'Cognome', 'Codice fiscale', 'Stato', 'Certificato']
subscription('sub-basic', SIMPLE, [['Mario', 'Rossi', 'RSSMRA', 'Attivo', 'si'], ['Anna', 'Verdi', '', 'Sospeso', 'no']])
subscription('sub-one', SIMPLE, [['Mario', 'Rossi', 'RSSMRA', 'Attivo', 'si']])
subscription('sub-none', SIMPLE, [['Mario', None, None, 'Attivo', ''], [None, 'x', '1', '2', '3']])
subscription('sub-zero', SIMPLE, [])
subscription('sub-many', ['a' * n for n in range(1, 30)], [[f'{r}-{c}' for c in range(29)] for r in range(25)])
subscription('sub-dups', ['x', 'y'], [['same', 'same'], ['same', 'other'], ['other', 'same']])
subscription('sub-specials', SIMPLE, [['=x', 'http://a.b', ' sp', 'a\x02b', '_x0000_'], ['{=1}', 'mailto:a', 'b ', '\n', 'é']])
subscription('sub-rich-text', ['<r>h</r>', 'Cognome', 'Codice fiscale', 'Stato', 'Certificato'],
             [['<r><t>b</t></r>', 'a<r>', '<r>a & b "c"</r>', '<r></r>', '<r>x'], ['<r>', '</r>', '<r><r>n</r></r>', None, '']])
subscription('sub-long', SIMPLE, [['x' * 33000, 'a', 'b', 'c', 'd']])
# Formulas (a leading '=' or a {=...} token), rich strings (<r>...</r> is written raw) and links with anchors, internal: and
# external: targets, as in XlsxWriter's Worksheet.write(). Dynamic array functions write extra parts and stay unported.
subscription('sub-formulas', SIMPLE, [['=1+1', '{=SUM(A1:A2)}', '=', '=SUM("a<b>&c")', '=aSINGLE(1)'],
                                      ['=_xlfn.SUM(1)', '=héSORT(1)', '{=}', '={x}', '=SORT']])
subscription('sub-dynamic', SIMPLE, [['=SEQUENCE(3)', 'a', 'b', 'c', 'd']])
subscription('sub-rich', SIMPLE, [['<r>x</r>', '<r><t>a</t></r>', '<r>&</r>', '<r>', 'x</r>'], ['<r></r>', '<r>\x01</r>', ' <r>x</r>', '<r>x</r> ', 'y']])
subscription('sub-urls', SIMPLE, [['http://a.b/c#frag', 'internal:Sheet2!B2', 'external:./f.xlsx', 'external:\\\\srv/share', 'https://x.y/%41#a b'],
                                  ['external:C:/dir/f.xlsx#S!A1', 'mailto:a@b.it', 'ftp://x/y z<>', 'http://x/a#b#c', 'internal:'],
                                  ['http://' + 'a' * 2080, 'http://x#' + 'b' * 2080, 'mailto:' + 'c' * 33000, 'http://a"b[c]^d`e{f}', 'external:#loc']])
subscription('sub-url-header', ['http://x.y/z#h', 'internal:A1', 'Nome', '=A1', '<r>h</r>'], [['a', 'b', 'c', 'd', 'e']])
subscription('sub-url-same-text', ['a', 'b'], [['http://x.y', 'http://x.y'], ['http://x.y', 'mailto:x@y']])
instructor('ir-urls', SCHEMA, [['internal:A1', 'external:x', '{=A1}', '=SUM(1)', 'http://u/v#w', 'd', I(1), '<r>x</r>', 'e']])

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
module('mod-rich-text', ['<r><t>q</t></r>'] + STANDARD, [['<r>a < b</r>', 'd', I(1), '-', '-', '-', 'x', ''],
                                                          ['<r></r>', '<r>d</r>', I(2), '-', '-', '-', '<r>x</r>', '']])
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

# ---------------------------------------------------------------- saved reports (pandas + openpyxl)
# XLSXExporter (application/mcp_server/exporters/xlsx_exporter.py) and the attendance workbooks
# (application/mcp_server/attendance.py), loaded from the Django sources without Django.
import importlib.util
import re
import sys
import types

for package in ('application', 'application.mcp_server'):
    sys.modules.setdefault(package, types.ModuleType(package))


def django_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'BE' / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


django_module('application.mcp_server.exporters', 'application/mcp_server/exporters/__init__.py')
XLSX_EXPORTER = django_module('application.mcp_server.exporters.xlsx_exporter', 'application/mcp_server/exporters/xlsx_exporter.py').XLSXExporter()
ATTENDANCE = {'io': io, 're': re, 'Alignment': openpyxl.styles.Alignment, '_UNSAFE_SHEET_RE': re.compile(r'[<>:"/\\|?*\x00-\x1f\[\]]')}
for function in ('_col_letter', '_auto_width', '_sanitize_sheet_name', '_export_single_course_xlsx', '_export_multi_course_xlsx',
                 '_export_multi_sheet_xlsx'):
    django_function('application/mcp_server/attendance.py', function, ATTENDANCE)

sys.path.insert(0, str(ROOT))
from TESTS.contracts import xlsx as CONTRACT  # noqa: E402


def same_as_contract(data, spec):
    """The contract's own builder must give the workbook Django's code gives."""
    assert CONTRACT.difference(data, CONTRACT.build(spec), __import__('datetime').datetime(2000, 1, 1).astimezone(),
                               __import__('datetime').datetime(2100, 1, 1).astimezone()) is None, spec
    return data


def sheet_rows(labels, rows):
    keys = [f'k{n}' for n in range(len(labels))]
    return keys, [dict(zip(keys, [decode(value) for value in row])) for row in rows], dict(zip(keys, [decode(label) for label in labels]))


def report_xlsx(name, title, labels, rows, sheet_name=None):
    payload = {'title': title, 'columns': labels, 'rows': rows}
    keys, data, mapping = sheet_rows(labels, rows)

    def build():
        out = XLSX_EXPORTER.export(data, 'file', title=title, column_labels=mapping)[0]
        return same_as_contract(out, {'engine': 'report_sheets', 'sheets': [{'title': sheet_name or title, 'columns': labels, 'rows': rows}]})
    case(name, 'report_xlsx', payload, build)


def report_sheets(name, *sheets, names=None):
    payload = {'sheets': [{'title': title, 'columns': labels, 'rows': rows} for title, labels, rows in sheets]}
    prepared = []
    for title, labels, rows in sheets:
        keys, data, mapping = sheet_rows(labels, rows)
        prepared.append({'data': data, 'title': title, 'column_labels': mapping})

    def build():
        out = XLSX_EXPORTER.export_multi_sheet(prepared, 'file')[0]
        if names:
            same_as_contract(out, {'engine': 'report_sheets', 'sheets': [
                {'title': final, 'columns': labels, 'rows': rows} for final, (_, labels, rows) in zip(names, sheets)]})
        return out
    case(name, 'report_sheets', payload, build)


def matrix(title, dates, rows):
    return {'course': title, 'dates': dates, 'rows': [{'name': person, 'attendance': marks, 'present_count': marks.count('P')}
                                                      for person, marks in rows]}


def attendance(name, mode, *courses):
    payload = {'mode': mode, 'courses': list(courses)}

    def build():
        if mode == 'single':
            out = ATTENDANCE['_export_single_course_xlsx'](courses[0])[0]
        else:
            full = {'multi': True, 'courses': list(courses)}
            out = ATTENDANCE['_export_multi_course_xlsx' if mode == 'flat' else '_export_multi_sheet_xlsx'](full)[0]
        return same_as_contract(out, {'engine': 'attendance', **payload})
    case(name, 'attendance', payload, build)


report_xlsx('rx-basic', 'Tags', ['tag_name', 'creation_date'], [['Nuoto', '15/09/2026 08:30'], ['nuoto agonistico', '01/10/2026 00:00']])
report_xlsx('rx-numbers', 'Carnet', ['Titolo', 'Lezioni', 'Quota', 'Codice', 'Misto', 'Vuoti'],
            [['Dieci', I(10), F(80.0), '00100', I(1), ''], ['Cinque', I(5), F(45.5), '3331234567', 'x', ''], ['', I(0), F(0.0), '12', '', '']])
report_xlsx('rx-floats-integral', 'Quote', ['q', 'r'], [[F(80.0), F(1.5)], [F(45.0), I(2)]])
report_xlsx('rx-numeric-strings', 'N', ['a', 'b', 'c'], [['1', '1.5', ' 7 '], ['2', '2e3', '0x10'], ['-3', '.5', '1_000']])
report_xlsx('rx-long-title', 'T' * 40, ['a'], [['x']], sheet_name='T' * 31)
report_xlsx('rx-title-invalid', 'a/b', ['a'], [['x']])
report_xlsx('rx-title-brackets', 'a[b]', ['a'], [['x']])
report_xlsx('rx-title-quote', "'a'", ['a'], [['x']])
report_xlsx('rx-title-specials', 'A & <B> "c" è', ['a & <b>'], [['x & <y> "z"'], [' spazi ']])
report_xlsx('rx-dup-labels', 'Dup', ['Nome', 'Tag', 'Nome'], [['x' * 60, '1', 'assegnato'], ['y', '2', '']])
report_xlsx('rx-wide', 'Wide', [f'colonna {n}' for n in range(30)], [[str(n) * (n % 5 + 1) for n in range(30)], ['x'] * 30])
report_xlsx('rx-long-values', 'Long', ['a', 'b'], [['x' * 80, I(123456789012)], ['y', I(-5)]])
report_xlsx('rx-formula-error', 'F', ['a'], [['=1+1'], ['#N/A'], ['=']])
report_xlsx('rx-illegal', 'F', ['a'], [['a\x07b']])
report_xlsx('rx-dict', 'F', ['a', 'b'], [[{'dict': [['colore', 'blu']]}, 'x']])
report_xlsx('rx-list', 'F', ['a', 'b'], [[{'list': ['a', 'b']}, 'x']])
report_xlsx('rx-list-empty', 'F', ['a', 'b'], [[{'list': []}, 'x'], ['', 'y']])
report_xlsx('rx-many-rows', 'Righe', ['n', 's'], [[I(n), f'riga {n}'] for n in range(40)])
report_sheets('rs-two', ('Tags', ['Nome'], [['Nuoto'], ['Calcio']]), ('Carnet', ['Titolo', 'Lezioni'], [['Dieci', I(10)]]),
              names=['Tags', 'Carnet'])
report_sheets('rs-dup-titles', ('Tags', ['a'], [['1']]), ('Tags', ['a'], [['x']]), ('Tags', ['b'], [['y']]),
              names=['Tags', 'Tags (2)', 'Tags (3)'])
report_sheets('rs-dup-long', ('L' * 40, ['a'], [['1']]), ('L' * 35, ['a'], [['x']]), names=['L' * 31, 'L' * 27 + ' (2)'])
report_sheets('rs-collision', ('Tags', ['a'], [['1']]), ('Tags (2)', ['b', 'c'], [['x', 'y']]), ('Tags', ['c'], [['z']]))
report_sheets('rs-collision-shorter', ('Tags', ['a', 'b'], [['1', 'x'], ['3', 'y'], ['5', 'z']]),
              ('Tags', ['b', 'c', 'd'], [['p', I(1), ''], ['q', I(2), 'w'], ['r', I(3), '']]), ('Tags (2)', ['una colonna più larga'], [[I(7)]]),
              names=['Tags', 'Tags (2)', 'Tags (2)'])
# Labels that are not text: a number, null and a boolean are written as such; a list or dict raises.
report_xlsx('rx-label-types', 'T', [I(5), F(1.5), None, {'bool': True}, 'testo', F(80.0), I(-12345678901)],
            [['x', 'yy', 'z', I(1), 'w', '', ''], ['una cella più larga', 'w', '', I(2), '', '', '']])
report_xlsx('rx-label-numbers', 'T', [I(5), F(1.5), I(7)], [['x', 'yy', 'z']])
report_xlsx('rx-label-integers', 'T', [I(5), I(70)], [['x', 'yy']])
# Labels that are equal in Python (1, 1.0, True) select the same columns: none of them is converted and their widths
# follow the number of rows. An integer label outside the 64-bit ranges makes an object index, one in the unsigned range
# an unsigned or a float one; a None before the integers reads them as floats.
BIG = 2 ** 70
for label_name, label_set in (
        ('equal-numbers', [I(1), F(1.0), 'c']), ('equal-all-numbers', [I(1), F(1.0)]), ('equal-integers', [I(7), I(7), I(8)]),
        ('equal-bool', [I(1), {'bool': True}, I(0), F(0.0), 'x']), ('equal-false', [{'bool': False}, I(0)]),
        ('big', [I(BIG), I(5), 'c']), ('big-numbers', [I(BIG), I(5)]), ('big-float', [I(BIG), F(1.5)]),
        ('unsigned', [I(2 ** 63), I(5)]), ('unsigned-max', [I(2 ** 64 - 1), I(2 ** 63)]), ('unsigned-negative', [I(2 ** 63), I(-1)]),
        ('unsigned-float', [I(2 ** 63), F(1.5)]), ('none-then-big', [None, I(BIG)]), ('big-then-none', [I(BIG), None]),
        ('none-then-unsigned-negative', [None, I(2 ** 63), I(-1)]), ('big-equal', [I(BIG), I(BIG), 'c']),
        ('big-equal-float', [I(BIG), F(float(BIG))]), ('unsigned-equal-float', [I(2 ** 63 + 1), F(float(2 ** 63))]),
        ('unsigned-not-equal', [I(2 ** 63 + 1), I(2 ** 63)]), ('negative-big', [I(-2 ** 64), 'k']),
        ('beyond-unsigned', [I(2 ** 64), I(2 ** 63)]), ('equal-wide', [I(3), F(3.0), I(3)])):
    report_xlsx('rx-label-' + label_name, 'T', label_set,
                [(['x', '12', 'y', '4', 'z'] * 2)[:len(label_set)], (['zz', '7', '3', '5.5', ''] * 2)[:len(label_set)],
                 (['w' * 30, '8', '1', '6', 'q'] * 2)[:len(label_set)]])
report_xlsx('rx-label-booleans', 'T', [{'bool': True}, {'bool': False}], [['x', 'yy']])
report_xlsx('rx-label-number-boolean', 'T', [F(1.5), {'bool': True}, None], [['x', 'yy', 'z']])
report_xlsx('rx-label-null', 'T', [None, 'b'], [['x', 'yy']])
report_xlsx('rx-label-list', 'T', [{'list': ['a']}, 'b'], [['x', 'y']])
report_xlsx('rx-label-dict', 'T', ['b', {'dict': [['a', 'b']]}], [['x', 'y']])
report_sheets('rs-label-types', ('Tags', [I(0), None], [['1', 'x']]), ('Carnet', ['a', {'bool': False}], [['x', I(3)]]), names=['Tags', 'Carnet'])
report_xlsx('rx-no-columns', 'Tags', [], [[], [], []])
report_sheets('rs-no-columns', ('Tags', [], [[], []]), ('Carnet', ['a'], [['x']]), names=['Tags', 'Carnet'])
report_sheets('rs-case', ('Tags', ['a'], [['1']]), ('tags', ['b'], [['x']]))
report_sheets('rs-empty-title', ('', ['a'], [['1']]))
report_sheets('rs-title-invalid', ('ok', ['a'], [['1']]), ('a:b', ['a'], [['1']]))

DAYS = ['01/09/2026', '08/09/2026', '15/09/2026']
attendance('ax-single', 'single', matrix('Nuoto', DAYS, [('Rossi Mario', ['P', '', 'P']), ('Bianchi Anna', ['', '', ''])]))
attendance('ax-single-long', 'single', matrix('Corso di nuoto agonistico per adulti e ragazzi', DAYS[:1],
                                              [('Cognome molto lungo ' * 3, ['P'])]))
attendance('ax-single-many-dates', 'single', matrix('Nuoto', ['%02d/%02d/2026' % (d % 28 + 1, d // 28 + 1) for d in range(60)],
                                                    [('Rossi Mario', ['P'] * 60)]))
attendance('ax-single-no-rows', 'single', matrix('Nuoto', DAYS, []))
attendance('ax-single-dup-date', 'single', matrix('Nuoto', ['01/09/2026', '01/09/2026', '08/09/2026'], [('Rossi Mario', ['P', '', 'P'])]))
attendance('ax-single-dup-dates', 'single', matrix('Nuoto', ['01/09/2026'] * 4, [('Rossi Mario', ['P', '', 'P', ''])]))
attendance('ax-single-title-invalid', 'single', matrix('Nuoto 1/2', DAYS, [('Rossi Mario', ['P', '', 'P'])]))
attendance('ax-single-specials', 'single', matrix('Città & <sport>', DAYS[:1], [('=Rossi', ['P']), (' Bianchi & C ', [''])]))
attendance('ax-flat', 'flat', matrix('Calcio', DAYS[:2], [('Rossi Mario', ['P', '']), ('Verdi Luca', ['P', 'P'])]),
           matrix('Nuoto', DAYS[1:], [('Bianchi Anna', ['', 'P'])]))
attendance('ax-flat-dup-date', 'flat', matrix('Calcio', ['01/09/2026', '01/09/2026'], [('Rossi Mario', ['P', ''])]),
           matrix('Nuoto', DAYS, [('Bianchi Anna', ['', 'P', 'P'])]))
attendance('ax-flat-no-rows', 'flat', matrix('Calcio', DAYS, []), matrix('Nuoto', DAYS, []))
attendance('ax-flat-one-empty', 'flat', matrix('Calcio', DAYS, []), matrix('Nuoto', DAYS[:1], [('Bianchi Anna', ['P'])]))
attendance('ax-sheets', 'sheets', matrix('Calcio', DAYS[:2], [('Rossi Mario', ['P', ''])]), matrix('Nuoto', DAYS, [('Bianchi Anna', ['', 'P', 'P'])]))
attendance('ax-sheets-names', 'sheets', matrix('Nuoto: A/B [x]', DAYS[:1], [('a', ['P'])]), matrix('Nuoto- A-B -x', DAYS[:1], [('b', [''])]),
           matrix('///', DAYS[:1], [('c', ['P'])]), matrix('N' * 40, DAYS[:1], [('d', ['P'])]), matrix('N' * 33, DAYS[:1], [('e', ['P'])]))
attendance('ax-sheets-no-rows', 'sheets', matrix('Calcio', DAYS, [('Rossi Mario', ['P', '', ''])]), matrix('Nuoto', DAYS, []))
LONG = 'Ginnastica dolce per gli adulti'
attendance('ax-sheets-collision', 'sheets', matrix(LONG[:27] + ' (2)', DAYS, [('Rossi Mario', ['P', '', 'P']), ('Bianchi Anna', ['', 'P', ''])]),
           matrix(LONG + ' A', DAYS[:1], [('Verdi Luca', ['P'])]), matrix(LONG + ' B', DAYS[1:2], [('de Luca Ugo', [''])]))
attendance('ax-sheets-collision-dup-dates', 'sheets', matrix(LONG[:27] + ' (2)', DAYS, [('Rossi Mario', ['P', '', 'P'])]),
           matrix(LONG + ' A', DAYS[:1], [('Verdi Luca', ['P'])]), matrix(LONG + ' B', ['01/09/2026'] * 4, [('de Luca Ugo', ['', 'P', '', ''])]))
attendance('ax-flat-untitled', 'flat', matrix('Calcio', DAYS[:2], [('Rossi Mario', ['P', ''])]), matrix(None, DAYS[1:], [('Bianchi Anna', ['', 'P'])]))
attendance('ax-flat-all-untitled', 'flat', matrix(None, DAYS[:1], [('Rossi Mario', ['P'])]), matrix(None, DAYS[1:], [('Bianchi Anna', ['', 'P'])]))
attendance('ax-sheets-untitled', 'sheets', matrix('Calcio', DAYS[:2], [('Rossi Mario', ['P', ''])]), matrix(None, DAYS[1:], [('Bianchi Anna', ['', 'P'])]))
attendance('ax-single-untitled', 'single', matrix(None, DAYS, [('Rossi Mario', ['P', '', 'P'])]))

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps({'versions': {'pandas': pandas.__version__, 'openpyxl': openpyxl.__version__,
                                           'xlsxwriter': xlsxwriter.__version__},
                              'cases': CASES, 'parts': PARTS}, ensure_ascii=False, indent=0, sort_keys=True) + '\n')
print(len(CASES), 'cases', sum('raises' in item for item in CASES), 'raise', len(PARTS), 'parts')
