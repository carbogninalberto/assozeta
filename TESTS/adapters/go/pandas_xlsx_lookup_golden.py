"""Capture pandas' lookups of import map values in a sheet's column Index for BE_GO/internal/pandasxlsx.

Run with the migration interpreter (pandas 2.2.3, openpyxl 3.1.5) from the
repository root: ``python -I TESTS/adapters/go/pandas_xlsx_lookup_golden.py``
Each authored header row is written as a one-sheet .xlsx whose numeric cells
carry the raw ``<v>`` text, so a whole number outside int64 and a ``1E+20``
cell are read exactly as written, and whose error cells are ``t="e"``. The
workbook is read as get_pandas_df_from_file reads an imported sheet
(``pandas.read_excel(io.BytesIO(data), engine="openpyxl")``, every other
option default), then for every key of a fixed list of JSON texts the script
records what ``if col in row.keys(): row[col]`` answers on the first row of
distinct text cells 'v0', 'v1', ...: ``"absent"``, ``"raises"`` or the
position of the column whose text it returned. The labels pandas writes with
``json.dumps(frame.columns.tolist())`` are recorded too.
Written to ``BE_GO/internal/pandasxlsx/testdata/pandas_lookups.json``, one
case per header; ``go test ./internal/pandasxlsx`` requires the same labels
and outcomes for every workbook.
"""
import base64
import io
import json
from pathlib import Path
import warnings
import zipfile

import pandas

TESTDATA = Path(__file__).resolve().parents[3] / 'BE_GO/internal/pandasxlsx/testdata'
OUTPUT = TESTDATA / 'pandas_lookups.json'

DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RELS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE = 'http://schemas.openxmlformats.org/package/2006'

# The keys of every lookup, as the JSON texts json.loads reads them: ints, floats, booleans and text.
KEYS = ['100000000000000000000', '1e20', '-100000000000000000000', '9223372036854775813', '9223372036854775808',
        '9.223372036854776e18', '18446744073709551615', '18446744073709551616', '0', '1', '2', '-1', '2.0', '2.5',
        'true', 'false', '"2"', '"x"', '"Nome"']


def archive(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, text in parts:
            bundle.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text.encode('utf-8'), zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def letter(column):
    text = ''
    while column:
        column, rest = divmod(column - 1, 26)
        text = chr(65 + rest) + text
    return text


def header_cell(column, item):
    """One header cell: ('n', raw text) a number, ('s', text) a str, ('b', flag) a boolean, ('e', text) an error."""
    kind, value = item
    reference = f'{letter(column)}1'
    if kind == 'n':
        return f'<c r="{reference}"><v>{value}</v></c>'
    if kind == 's':
        return f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
    if kind == 'b':
        return f'<c r="{reference}" t="b"><v>{int(value)}</v></c>'
    return f'<c r="{reference}" t="e"><v>{value}</v></c>'


def workbook(header):
    """A one-sheet .xlsx: the authored header row and one row of distinct text cells 'v0', 'v1', ..."""
    head = ''.join(header_cell(column, item) for column, item in enumerate(header, 1))
    body = ''.join(f'<c r="{letter(column)}2" t="inlineStr"><is><t>v{column - 1}</t></is></c>'
                   for column in range(1, len(header) + 1))
    return archive([
        ('[Content_Types].xml', f'{DECLARATION}<Types xmlns="{PACKAGE}/content-types">'
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/>'
         f'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
         '<Override PartName="/xl/worksheets/sheet1.xml" '
         'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
         '</Types>'),
        ('_rels/.rels', f'{DECLARATION}<Relationships xmlns="{PACKAGE}/relationships">'
         f'<Relationship Id="rId1" Type="{RELS}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        ('xl/workbook.xml', f'{DECLARATION}<workbook xmlns="{MAIN}" xmlns:r="{RELS}">'
         '<sheets><sheet name="Soci" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        ('xl/_rels/workbook.xml.rels', f'{DECLARATION}<Relationships xmlns="{PACKAGE}/relationships">'
         f'<Relationship Id="rId1" Type="{RELS}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'),
        ('xl/worksheets/sheet1.xml', f'{DECLARATION}<worksheet xmlns="{MAIN}"><sheetData>'
         f'<row r="1">{head}</row><row r="2">{body}</row></sheetData></worksheet>'),
    ])


def outcome(frame, key):
    """What ``if key in row.keys(): row[key]`` answers for the frame's first row."""
    if key not in frame.columns:
        return 'absent'
    row = frame.iloc[0]
    try:
        value = row[key]
    except Exception:
        return 'raises'
    # The first row holds 'v0', 'v1', ...: the text names the column it came from.
    return int(value[1:])


B = 10 ** 20  # 100000000000000000000, beyond uint64
U = 2 ** 63 + 5  # 9223372036854775813, within uint64 but not int64
NAN = ('e', '#N/A')

# The authored header rows: numbers only outside int64 and uint64, a float64 Index made of them, the objects an
# out-of-range mix keeps, the duplicate naming, and the kinds that were already read.
HEADERS = [
    [('n', '100000000000000000000'), ('n', '2')],  # [B, 2]: objects, a whole-number key is a label
    [('n', '100000000000000000000'), ('s', 'Nome')],  # [B, 'Nome']
    [('n', '100000000000000000000'), ('b', True)],  # [B, True]
    [('n', '100000000000000000000'), ('n', '2.5')],  # [B, 2.5]
    [('n', '100000000000000000000'), ('n', '2'), ('n', '2.5')],  # [B, 2, 2.5]
    [('n', '100000000000000000000'), ('n', '2.5'), ('s', 'x')],  # [B, 2.5, 'x']
    [('n', '100000000000000000000'), ('b', True), ('n', '2.5')],  # [B, True, 2.5]
    [('n', '9223372036854775813'), ('n', '-1'), ('n', '2.5')],  # [U, -1, 2.5]
    [('n', '-100000000000000000000'), ('n', '2')],  # [-B, 2]
    [('s', 'x'), ('n', '100000000000000000000'), ('n', '2.5')],  # ['x', B, 2.5]
    [NAN, ('n', '100000000000000000000')],  # [nan, B]
    [('n', '9223372036854775813'), ('n', '2')],  # [U, 2]: a uint64 Index
    [('n', '9223372036854775813')],  # [U]
    [('n', '9223372036854775808'), ('n', '0')],  # [2 ** 63, 0]
    [('n', '18446744073709551615'), ('n', '2')],  # [2 ** 64 - 1, 2]
    [('n', '18446744073709551616'), ('n', '2')],  # [2 ** 64, 2]: objects
    [('n', '9223372036854775813'), ('n', '2.5')],  # [U, 2.5]: a float64 Index
    [('n', '9223372036854775813'), ('n', '2'), ('n', '2.5')],  # [U, 2, 2.5]
    [('n', '9223372036854775813'), ('b', True)],  # [U, True]
    [('n', '9223372036854775813'), ('s', 'x')],  # [U, 'x']
    [('n', '100000000000000000000'), ('n', '100000000000000000000')],  # [B, B]
    [('n', '100000000000000000000'), ('n', '9223372036854775813')],  # [B, U]
    [('n', '1E+20'), ('n', '2')],  # the same as [B, 2]: an integral float cell is an int
    [('n', '1'), ('n', '2'), ('n', '3')],
    [('n', '0.5'), ('n', '2')],
    [('b', True), ('b', False)],
    [('s', 'x'), ('b', False), ('b', True)],
    [('s', 'Nome'), ('n', '5'), ('b', True), ('n', '0.5')],
]

cases = []
for header in HEADERS:
    data = workbook(header)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        frame = pandas.read_excel(io.BytesIO(data), engine='openpyxl')
        labels = json.dumps(frame.columns.tolist())
        outcomes = {text: outcome(frame, json.loads(text)) for text in KEYS}
    cases.append({'workbook': base64.b64encode(data).decode(), 'labels': labels, 'outcomes': outcomes})

OUTPUT.write_text(json.dumps({'cases': cases}, indent=1) + '\n')
print(len(cases), 'cases')
