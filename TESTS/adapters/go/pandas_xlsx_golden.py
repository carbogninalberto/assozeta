"""Capture pandas.read_excel(file, engine="openpyxl") frames for BE_GO/internal/pandasxlsx.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/pandas_xlsx_golden.py``
Each workbook is read exactly as get_pandas_df_from_file does for an Excel
document (a binary file object, engine openpyxl, every other option default)
and the outcome recorded: the exception class, or the columns and, for every
row of DataFrame.iterrows(), each value's Python type and text. Workbooks are
openpyxl-written sheets of authored rows plus seeded random workbooks
assembled from minimal parts (shared, inline and formula strings, styled
numbers, missing references, row gaps, booleans and errors).
Written to ``BE_GO/internal/pandasxlsx/testdata/pandas_frames.json.gz``;
``go test ./internal/pandasxlsx`` requires the same outcome for every workbook
the package accepts, and acceptance of every workbook marked ``required``.
"""
import base64
import datetime
import gzip
import io
import json
from pathlib import Path
import random
import warnings
import zipfile
from xml.sax.saxutils import escape

import numpy
import openpyxl
import pandas

ROOT = Path(__file__).resolve().parents[3]
TEXT_OUTPUT = Path(__file__).resolve().parents[3] / 'BE_GO/internal/pandasxlsx/testdata/pandas_text_frames.json.gz'
OUTPUT = ROOT / 'BE_GO/internal/pandasxlsx/testdata/pandas_frames.json.gz'
D = datetime.datetime

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                 '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                 '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                 '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                 '</Types>')
ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
             '</Relationships>')
WORKBOOK = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Soci" sheetId="1" r:id="rId1"/></sheets></workbook>')
WORKBOOK_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                 '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="/xl/sharedStrings.xml"/>'
                 '</Relationships>')
# cellXfs: 0 General, 1 date, 2 dd/mm/yyyy, 3 date and time, 4 0.00, 5 text, 6 [h]:mm:ss, 7 a quoted d, 8 a locale date.
STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<numFmts count="4"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/><numFmt numFmtId="165" formatCode="[h]:mm:ss"/>'
          '<numFmt numFmtId="166" formatCode="0.0&quot;d&quot;"/><numFmt numFmtId="167" formatCode="[$-410]d\\ mmmm\\ yyyy;@"/></numFmts>'
          '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="9"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="167" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
SHEET_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')


def letters(column):
    text = ''
    while column:
        column, rest = divmod(column - 1, 26)
        text = chr(65 + rest) + text
    return text


def archive(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, text in parts:
            bundle.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text.encode('utf-8'), zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def minimal(rows, *, dimension=None, row_refs=True, cell_refs=True, strings=None, sheet=None, workbook=WORKBOOK, styles=STYLES):
    """rows: [(number or None, [(column or None, attributes text, inner xml)])]."""
    table = list(strings or [])
    body = []
    for number, cells in rows:
        opening = f'<row r="{number}">' if number is not None and row_refs else '<row>'
        inner = ''
        for column, attributes, content in cells:
            reference = f' r="{letters(column)}{number}"' if column is not None and number is not None and cell_refs else ''
            inner += f'<c{reference}{attributes}>{content}</c>'
        body.append(opening + inner + '</row>')
    data = SHEET_HEAD + (f'<dimension ref="{dimension}"/>' if dimension else '') + '<sheetData>' + ''.join(body) + '</sheetData></worksheet>'
    shared = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + ''.join(table) + '</sst>')
    parts = [('[Content_Types].xml', CONTENT_TYPES), ('_rels/.rels', ROOT_RELS), ('xl/workbook.xml', workbook),
             ('xl/_rels/workbook.xml.rels', WORKBOOK_RELS), ('xl/worksheets/sheet1.xml', sheet if sheet is not None else data),
             ('xl/sharedStrings.xml', shared)]
    if styles is not None:
        parts.append(('xl/styles.xml', styles))
    return archive(parts)


class Builder:
    def __init__(self):
        self.strings = []

    def shared(self, text, rich=False):
        if rich and len(text) > 1:
            item = f'<si><r><t xml:space="preserve">{escape(text[:1])}</t></r><r><rPr><b/></rPr><t xml:space="preserve">{escape(text[1:])}</t></r></si>'
        else:
            item = f'<si><t xml:space="preserve">{escape(text)}</t></si>'
        self.strings.append(item)
        return (' t="s"', f'<v>{len(self.strings) - 1}</v>')

    @staticmethod
    def inline(text):
        return (' t="inlineStr"', f'<is><t xml:space="preserve">{escape(text)}</t></is>')

    @staticmethod
    def formula(text):
        return (' t="str"', f'<f>A1&amp;""</f><v>{escape(text)}</v>')

    @staticmethod
    def number(raw, style=0):
        return (f' s="{style}"' if style else '', f'<v>{raw}</v>')


NAMES = ['Mario', 'Anna', 'Rossi', 'Bianchi', 'Via Roma 1', 'Città', 'x_y', 'M', 'F', 'RSSMRA80A01H501U', 'a@example.test', 'àèì', '-',
         '333 1234567', '0461/123456', 'Roma ', ' Milano']
NUMERIC_TEXTS = ['00100', '12', ' 7 ', '3.5', '+39333', '0012.50', '20100', '-4', '1.', '.5', '7\t', '123456789012345', '0.1', '2.25']
ODD_TEXTS = ['1e3', '1,5', 'inf', '12 34', '1_000', '+', '.', 'True', 'false', 'nan ', '0x10', '--1', '1.2.3', 'Infinity', '9223372036854775808',
             '1.123456789012345678', '5E-3']
NA_TEXTS = ['', 'NA', 'N/A', 'nan', 'null', 'None', 'NULL', '#N/A', 'n/a', 'NaN', '<NA>']
DATE_TEXTS = ['01/02/1990', '1990-02-01', '31/12/2026', '5.3.2001', '2026-12-31 00:00:00']
NUMBERS = ['5', '100', '3.0', '2.5', '1E2', '0.1', '45000', '45000.5', '36526', '61', '123456789012', '-7', '0', '1.5E-2', '20100']
DATES = ['45000', '36526', '29221', '45000.5', '61', '59', '44196.999999', '18264', '2', '132000', '109500.25', '46386']
PROFILES = ('names', 'numeric_text', 'numbers', 'dates', 'date_text', 'mixed', 'empty', 'numbers_text', 'dates_far')


def random_cell(generator, builder, profile):
    roll = generator.random()
    if roll < 0.12:
        return None
    if roll < 0.2:
        return builder.shared(generator.choice(NA_TEXTS))
    if profile == 'empty':
        return None
    if profile == 'mixed':
        profile = generator.choice(PROFILES[:5] + ('odd', 'special'))
    if profile == 'names':
        text = generator.choice(NAMES)
        kind = generator.random()
        if kind < 0.6:
            return builder.shared(text, rich=generator.random() < 0.2)
        return builder.inline(text) if kind < 0.85 else builder.formula(text)
    if profile == 'numeric_text':
        return builder.shared(generator.choice(NUMERIC_TEXTS))
    if profile == 'numbers_text':
        if generator.random() < 0.5:
            return builder.shared(generator.choice(NUMERIC_TEXTS))
        return builder.number(generator.choice(NUMBERS), generator.choice([0, 0, 4, 5, 7]))
    if profile == 'numbers':
        return builder.number(generator.choice(NUMBERS), generator.choice([0, 0, 4, 5, 7]))
    if profile == 'dates':
        return builder.number(generator.choice(DATES), generator.choice([1, 2, 3, 8]))
    if profile == 'dates_far':
        return builder.number(generator.choice(DATES + ['140000', '2958465']), generator.choice([1, 2]))
    if profile == 'date_text':
        return builder.shared(generator.choice(DATE_TEXTS))
    if profile == 'odd':
        return builder.shared(generator.choice(ODD_TEXTS))
    choice = generator.random()
    if choice < 0.3:
        return (' t="b"', f'<v>{generator.randint(0, 1)}</v>')
    if choice < 0.5:
        return (' t="e"', '<v>#DIV/0!</v>')
    if choice < 0.7:
        return builder.number(generator.choice(['0.5', '0', '0.25']), 1)
    if choice < 0.85:
        return builder.number('1.5', 6)
    return (' s="1"', '')


def random_workbook(generator):
    builder = Builder()
    width, height = generator.randint(1, 5), generator.randint(0, 6)
    profiles = [generator.choice(PROFILES) for _ in range(width)]
    rows = []
    headers = ['Nome', 'Cognome', 'CAP', 'Data', 'Nome', '', 'Unnamed: 1', 'Nome.1', 'Note', ' Tel ', 'NA', '2024']
    cells = []
    for column in range(1, width + 1):
        if generator.random() < 0.9:
            text = generator.choice(headers)
            cells.append((column,) + (builder.shared(text) if text else ('', '')))
    number = generator.choice([1, 1, 1, 1, 2])
    rows.append((number, cells))
    for _ in range(height):
        number += generator.choice([1, 1, 1, 1, 1, 2])
        cells = []
        for column in range(1, width + 1):
            cell = random_cell(generator, builder, profiles[column - 1])
            if cell is not None:
                cells.append((column,) + cell)
        rows.append((number, cells))
    return minimal(rows, strings=builder.strings, dimension=generator.choice([None, None, 'A1:C3', 'B2:D4']),
                   row_refs=generator.random() < 0.9, cell_refs=generator.random() < 0.9)


def written(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


MEMBERS = [
    ['Nome', 'Cognome', 'Sesso', 'Codice fiscale', 'Data di nascita', 'Indirizzo', 'CAP', 'Telefono', 'Scadenza certificato', 'Numero tessera'],
    ['Mario', 'Rossi', 'M', 'RSSMRA80A01H501U', D(1980, 1, 1), 'Via Roma 1', '00100', '3330000000', '31/12/2026', 42],
    ['Anna', 'Bianchi', 'F', 'BNCNNA90B43F205X', D(1990, 2, 3), 'Via Milano 2', '20100', None, None, 43],
    ['Luca', 'Verdi', None, None, None, None, None, '02 1234567', '2027-01-31', None],
]
WRITTEN = [
    MEMBERS,
    [['Nome', 'Cap', 'Tel'], ['Mario', '00100', '333'], ['Anna', '20100', 'x']],
    [['Nome', 'Cap'], ['Mario', 100], ['Anna', None]],
    [['Nome', 'Cap'], ['Mario', 100], ['Anna', 200]],
    [['Nome', 'Cap'], ['Mario', 1.5], ['Anna', 2.0]],
    [['Nome', 'Nascita'], ['Mario', D(1980, 1, 5)], ['Anna', D(1990, 2, 3, 4, 5, 6)]],
    [['Nome', 'Nascita'], ['Mario', D(1980, 1, 5)], ['Anna', None]],
    [['Nome', 'Nascita'], ['Mario', datetime.date(1980, 1, 5)], ['Anna', datetime.date(1990, 2, 3)]],
    [['Nome', 'Nascita'], ['Mario', D(1980, 1, 5)], ['Anna', '03/02/1990']],
    [['Nome', 'Nascita'], ['Mario', D(1980, 1, 5)], ['Anna', 5]],
    [['Nome', 'Nascita'], ['Mario', D(2999, 12, 31)], ['Anna', None], ['Ugo', D(2020, 1, 1)]],
    [['Nome', 'Nascita'], ['Mario', D(2262, 4, 11)], ['Anna', None]],
    [['Nome', 'Nascita'], ['Mario', D(2262, 4, 12)], ['Anna', None]],
    [['Nome', 'Nascita'], ['Mario', D(1980, 1, 5, 10, 30, 15, 250000)], ['Anna', None]],
    [['Nome', 'B'], ['NA', 'N/A'], ['null', 'nan'], ['None', '-'], ['', ' ']],
    [['Nome', 'B'], ['Mario', 1], ['Anna', 'x'], ['Y', 2.5]],
    [['Nome', 'B'], [None, None], ['Mario', 'x'], [None, None]],
    [['Nome', 'B', None], ['Mario', 'x', None]],
    [['Nome', 'B', 'C'], ['Mario'], ['x', 'y', 'z', 'w']],
    [['A', 'B'], [1, 2.5], [3, None]],
    [['A', 'B'], [1, 2], [3, 4]],
    [],
    [['A', 'B']],
    [['A', 'B'], ['x', 3.0], ['y', 4.0]],
    [[None, None], ['A', 'B'], ['x', 'y']],
    [['A', 'B'], ['x', '12'], ['y', 5], ['z', 'abc']],
    [['A', 'B'], ['x', '12'], ['y', None]],
    [['A', 'B'], ['x', '+39333'], ['y', '0039']],
    [['A', 'A', 'A.1', '', 'Unnamed: 3', 'A'], ['x', 'y', 'z', 'w', 'v', 'u']],
    [['A'], ['x'], ['y']],
    [['A'], [1], [2]],
    [['A', 'B'], [D(2020, 1, 1), D(2021, 1, 1)], [None, D(2022, 5, 6)]],
    [['A', 'B'], [D(2020, 1, 1), 5], [None, 6]],
    [['Nome', 5], ['Mario', 1]],
    [['Nome', 'Data'], ['Mario', True], ['Anna', False]],
    [['Nome', 'Data'], ['Mario', datetime.time(1, 2, 3)]],
]
REQUIRED = len(WRITTEN)


def handmade():
    builder = Builder()
    name, tax = builder.shared('Nome'), builder.shared('Data')
    mario = builder.shared('Mario', rich=True)
    plain = [(1, [(1,) + name, (2,) + tax]), (2, [(1,) + mario, (2,) + builder.number('45000', 1)])]
    yield minimal(plain, strings=builder.strings)
    yield minimal(plain, strings=builder.strings, row_refs=False, cell_refs=False)
    yield minimal(plain, strings=builder.strings, dimension='B2:C3')
    yield minimal(plain, strings=builder.strings, styles=None)
    yield minimal(plain, strings=builder.strings, workbook=WORKBOOK.replace('<sheets>', '<workbookPr date1904="1"/><sheets>'))
    yield minimal([(3, plain[0][1]), (1, plain[1][1]), (5, plain[1][1])], strings=builder.strings)
    yield minimal([(1, [(2,) + name, (1,) + tax]), (2, [(3,) + mario])], strings=builder.strings)
    yield minimal([(1, [(1,) + name]), (2, [(1,) + builder.shared('a_x000D_b')])], strings=builder.strings + [])
    yield minimal([(1, [(1,) + name, (2,) + tax]), (2, [(1, ' t="s"', ''), (2, '', '<v></v>')])], strings=builder.strings)
    yield minimal([], strings=[])
    yield b'Nome;Cognome\nMario;Rossi\n'
    yield b''
    yield archive([('mimetype', 'application/vnd.oasis.opendocument.spreadsheet'), ('content.xml', '<office/>')])
    yield b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 504


def value(item):
    if item is pandas.NaT:
        return ['nat']
    if isinstance(item, (pandas.Timestamp, datetime.datetime)):
        return ['t', str(item)]
    if isinstance(item, bool) or isinstance(item, numpy.bool_):
        return ['other', type(item).__name__]
    if isinstance(item, str):
        return ['s', item]
    if isinstance(item, numpy.integer):
        return ['i', str(int(item))]  # the import takes its Python number (item())
    if isinstance(item, int):
        return ['i', str(item)]
    if isinstance(item, float):
        return ['nan'] if item != item else ['f', repr(float(item))]
    return ['other', type(item).__name__]


def outcome(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            frame = pandas.read_excel(io.BytesIO(data), engine='openpyxl')
    except Exception as error:  # the view answers the 500 page for every one
        return {'error': type(error).__name__}
    columns = frame.columns.tolist()
    if not all(isinstance(column, str) for column in columns):
        return {'other': 'columns'}
    return {'columns': columns, 'rows': [[value(row[column]) for column in columns] for _, row in frame.iterrows()]}


def import_cell_text(item):
    """The view's converter of the columns read as text (_import_cell_text)."""
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return str(item)
    return item


def text_outcome(data, plain):
    """The frame read with the text converter on every other column of the plain reading."""
    text = plain['columns'][::2]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            frame = pandas.read_excel(io.BytesIO(data), engine='openpyxl', converters={column: import_cell_text for column in text})
    except Exception as error:
        return {'text': text, 'error': type(error).__name__}
    columns = frame.columns.tolist()
    if not all(isinstance(column, str) for column in columns):
        return {'text': text, 'other': 'columns'}
    return {'text': text, 'columns': columns, 'rows': [[value(row[column]) for column in columns] for _, row in frame.iterrows()]}


def packed(payload):
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
    return buffer.getvalue()


def main():
    generator = random.Random(20261110)
    inputs = [written(rows) for rows in WRITTEN] + list(handmade())
    for _ in range(1500):
        inputs.append(random_workbook(generator))
    cases = [{'input': base64.b64encode(data).decode(), **outcome(data)} for data in inputs]
    for index in range(REQUIRED):
        cases[index]['required'] = 'error' not in cases[index] and 'other' not in cases[index] and not any(
            item[0] == 'other' for row in cases[index]['rows'] for item in row)
    payload = json.dumps({'pandas': pandas.__version__, 'openpyxl': openpyxl.__version__, 'cases': cases}, ensure_ascii=False,
                         separators=(',', ':')).encode()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as stream:
        stream.write(payload)
    OUTPUT.write_bytes(buffer.getvalue())
    kinds = {}
    for case in cases:
        kind = 'error:' + case['error'] if 'error' in case else 'other' if 'other' in case else 'frame'
        kinds[kind] = kinds.get(kind, 0) + 1
    print(len(cases), 'cases', kinds, len(buffer.getvalue()), 'bytes')
    # The same workbooks read with the text converter on every other column (ReadText).
    texts = [{'input': case['input'], **text_outcome(base64.b64decode(case['input']), case)} for case in cases if 'columns' in case]
    assert len(texts) >= 500, len(texts)
    TEXT_OUTPUT.write_bytes(packed({'pandas': pandas.__version__, 'openpyxl': openpyxl.__version__, 'cases': texts}))
    print(len(texts), 'text cases', sum('rows' in case for case in texts), 'frames')


if __name__ == '__main__':
    main()
