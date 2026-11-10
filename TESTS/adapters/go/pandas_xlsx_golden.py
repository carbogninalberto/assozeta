"""Capture pandas.read_excel(file, engine="openpyxl") frames for BE_GO/internal/pandasxlsx.

Run with the migration interpreter (pandas 2.2.3, openpyxl 3.1.5) from the
repository root: ``python -I TESTS/adapters/go/pandas_xlsx_golden.py``
Each workbook is read exactly as get_pandas_df_from_file does for an Excel
document (a binary file object, engine openpyxl, every other option default)
and the outcome recorded: the exception class, or the column labels and, for
every row of DataFrame.iterrows(), each value's Python type, str(), truth
value and whether json.dumps encodes it. Workbooks are openpyxl-written
sheets of authored rows, seeded random workbooks assembled from minimal
parts, and workbooks assembled part by part the way Excel and LibreOffice
write them: shared, inline and formula strings, booleans, errors, ISO 8601
date cells, numbers under built-in and custom date, time and duration
formats, the 1904 date system, missing references, sparse rows, merged
header cells, headers that are not text.
Written to ``BE_GO/internal/pandasxlsx/testdata/pandas_frames.json.gz`` (each
workbook as text, one character per byte, with the stylesheets most of them
share stored once);
``go test ./internal/pandasxlsx`` requires the same outcome for every workbook
the package accepts, and acceptance of every workbook marked ``required``.
``pandas_text_frames.json.gz`` holds the same workbooks read with the view's
text converter (_import_cell_text) on every other text-named column and on
all of them (ReadAll's text columns); ``numeric_texts.json.gz`` what pandas'
maybe_convert_numeric reads in single texts (the tokenizer's float parsing).
"""
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

TESTDATA = Path(__file__).resolve().parents[3] / 'BE_GO/internal/pandasxlsx/testdata'
OUTPUT = TESTDATA / 'pandas_frames.json.gz'
TEXT_OUTPUT = TESTDATA / 'pandas_text_frames.json.gz'
NUMERIC_OUTPUT = TESTDATA / 'numeric_texts.json.gz'
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


def archive(parts, method=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', method) as bundle:
        for name, text in parts:
            bundle.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text.encode('utf-8'), method)
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


# ---------------------------------------------------------------------------
# Workbooks the way spreadsheet applications write them.
# ---------------------------------------------------------------------------

# Number formats by name: a built-in numFmtId or a custom format code.
FORMATS = [
    ('general', 0), ('int', 1), ('fixed', 2), ('thousands', 3), ('percent', 9), ('scientific', 11), ('fraction', 12),
    ('date', 14), ('d-mmm-yy', 15), ('d-mmm', 16), ('mmm-yy', 17), ('h:mm AM/PM', 18), ('h:mm:ss AM/PM', 19), ('h:mm', 20),
    ('h:mm:ss', 21), ('datetime', 22), ('accounting', 37), ('accounting4', 44), ('mm:ss', 45), ('[h]:mm:ss', 46),
    ('mmss.0', 47), ('##0.0E+0', 48), ('text', 49), ('cjk27', 27), ('cjk30', 30), ('cjk36', 36), ('cjk50', 50), ('cjk57', 57),
    ('unknown99', 99), ('dd/mm/yyyy', 'dd/mm/yyyy'), ('iso', 'yyyy-mm-dd'), ('libre-date', 'DD/MM/YY'),
    ('libre-datetime', 'DD/MM/YYYY\\ HH:MM:SS'), ('libre-time', 'HH:MM:SS'), ('locale-date', '[$-410]d\\ mmmm\\ yyyy;@'),
    ('long-date', '[$-F800]dddd\\,\\ mmmm\\ dd\\,\\ yyyy'), ('long-time', '[$-F400]h:mm:ss\\ AM/PM'), ('duration', '[h]:mm:ss'),
    ('duration-ms', '[h]:mm:ss.000'), ('minutes', '[mm]:ss'), ('seconds', '[ss]'), ('hours', '[hh]:mm'), ('hours-only', '[h]'),
    ('upper-duration', '[H]:MM:SS'), ('seconds-fraction', '[s].00'), ('second-section', '0;[h]:mm'), ('first-section', '[h]:mm;0'),
    ('quoted', '0.0"d"'), ('quoted-word', '"data"0'), ('euro', '#,##0.00\\ [$€-410]'), ('red', '[Red]0.00'), ('year', 'yy'),
    ('padded', '0_d'), ('escaped', '\\d0'), ('general-custom', 'General'), ('at', '@'), ('builtin-code', 'mm-dd-yy'),
    ('tenths', 'mm:ss.0'), ('month-day', 'd/m/yy\\ h:mm'), ('colour-duration', '[Blue][h]:mm'), ('elapsed-text', '[h]" ore"'),
]
STYLE = {name: index for index, (name, _) in enumerate(FORMATS)}
DATE_STYLES = ['date', 'dd/mm/yyyy', 'iso', 'libre-date', 'datetime', 'locale-date', 'long-date', 'd-mmm-yy']
TIME_STYLES = ['h:mm', 'h:mm:ss', 'libre-time', 'h:mm AM/PM', 'long-time', 'mm:ss']
DURATION_STYLES = ['[h]:mm:ss', 'duration', 'minutes', 'seconds', 'duration-ms', 'hours']


def stylesheet(formats=FORMATS, overrides=()):
    """cellXfs in the order of ``formats``; ``overrides`` are extra numFmt elements (id, code)."""
    custom, cells, number = [], [], 164
    for _, spec in formats:
        if isinstance(spec, int):
            identifier = spec
        else:
            identifier, number = number, number + 1
            custom.append((identifier, spec))
        cells.append(f'<xf numFmtId="{identifier}" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>')
    custom += list(overrides)
    formats_xml = ''.join(f'<numFmt numFmtId="{identifier}" formatCode="{escape(code, {chr(34): "&quot;"})}"/>' for identifier, code in custom)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<numFmts count="{len(custom)}">{formats_xml}</numFmts>'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            f'<cellXfs count="{len(cells)}">{"".join(cells)}</cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


STYLES2 = stylesheet()
NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
NS_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS_PACKAGE = 'http://schemas.openxmlformats.org/package/2006/relationships'
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
THEME = XML + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office"><a:themeElements/></a:theme>'


def S(text):
    """A shared string cell."""
    return ('s', text)


def N(raw, style='general'):
    """A number cell under a named format."""
    return ('n', str(raw), style)


def B(raw):
    return ('b', str(raw))


def E(text='#N/A'):
    return ('e', text)


def ISO(text):
    return ('d', text)


def FS(text):
    """A formula whose result is text."""
    return ('str', text)


def IS(text):
    return ('is', text)


def sheet_cells(grid, strings, *, flavor='plain', cell_refs=True, row_refs=True, first_row=1, gaps=None, formulas=False):
    """sheetData for a grid of cell specs (None: no cell). Returns the XML of the rows."""
    lookup = {text: index for index, text in enumerate(strings)}
    out = []
    number = first_row - 1
    for index, row in enumerate(grid):
        number += 1 + (gaps or {}).get(index, 0)
        inner = ''
        for column, spec in enumerate(row, 1):
            if spec is None:
                continue
            reference = f' r="{letters(column)}{number}"' if cell_refs else ''
            kind = spec[0]
            style = ''
            if kind == 'n':
                style = f' s="{STYLE[spec[2]]}"' if spec[2] != 'general' or flavor == 'libre' else ''
            elif flavor == 'libre':
                style = ' s="0"'
            formula = '<f aca="false">A1</f>' if flavor == 'libre' and formulas else ('<f>A1</f>' if formulas else '')
            if kind == 's':
                if spec[1] not in lookup:
                    lookup[spec[1]] = len(strings)
                    strings.append(spec[1])
                inner += f'<c{reference}{style} t="s"><v>{lookup[spec[1]]}</v></c>'
            elif kind == 'is':
                inner += f'<c{reference}{style} t="inlineStr"><is><t xml:space="preserve">{escape(spec[1])}</t></is></c>'
            elif kind == 'str':
                inner += f'<c{reference}{style} t="str"><f>A1&amp;""</f><v>{escape(spec[1])}</v></c>'
            elif kind == 'n':
                tag = ' t="n"' if flavor == 'libre' else ''
                inner += f'<c{reference}{style}{tag}>{formula}<v>{spec[1]}</v></c>'
            elif kind == 'blank':
                inner += f'<c{reference} s="{STYLE[spec[1]]}"/>'
            elif kind == 'raw':
                inner += f'<c{reference}{spec[1]}>{spec[2]}</c>'
            else:
                inner += f'<c{reference}{style} t="{kind}">{formula}<v>{escape(spec[1])}</v></c>'
        if flavor == 'excel':
            opening = f'<row r="{number}" spans="1:{max(len(row), 1)}" x14ac:dyDescent="0.25">' if row_refs else '<row>'
        elif flavor == 'libre':
            opening = (f'<row r="{number}" customFormat="false" ht="12.8" hidden="false" customHeight="false" outlineLevel="0" '
                       'collapsed="false">') if row_refs else '<row>'
        else:
            opening = f'<row r="{number}">' if row_refs else '<row>'
        out.append(opening + inner + '</row>')
    return ''.join(out), number


def book(grid, *, flavor='plain', date1904=None, styles=STYLES2, merges=(), strings_type=True, styles_name='styles.xml',
         shared_first=(), method=zipfile.ZIP_STORED, **options):
    """A workbook of one sheet. flavor: 'plain' (minimal parts), 'excel' or 'libre' (the parts and attributes they write).

    The parts are stored uncompressed unless ``method`` says otherwise: the corpus file compresses the parts the workbooks share.
    """
    strings = list(shared_first)
    rows, last = sheet_cells(grid, strings, flavor=flavor, **options)
    width = max([len(row) for row in grid] + [1])
    merge_xml = ''
    if merges:
        merge_xml = f'<mergeCells count="{len(merges)}">' + ''.join(f'<mergeCell ref="{ref}"/>' for ref in merges) + '</mergeCells>'
    shared = ''.join(f'<si><t xml:space="preserve">{escape(text)}</t></si>' for text in strings)
    properties = '' if date1904 is None else f' date1904="{date1904}"'
    if flavor == 'excel':
        sheet = (XML + f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}" '
                 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="x14ac" '
                 'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac">'
                 f'<dimension ref="A1:{letters(width)}{max(last, 1)}"/>'
                 '<sheetViews><sheetView tabSelected="1" workbookViewId="0"><selection activeCell="A1" sqref="A1"/></sheetView></sheetViews>'
                 '<sheetFormatPr defaultRowHeight="15" x14ac:dyDescent="0.25"/>'
                 '<cols><col min="1" max="1" width="12.7109375" customWidth="1"/></cols>'
                 f'<sheetData>{rows}</sheetData>{merge_xml}'
                 '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>')
        workbook = (XML + f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
                    '<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="22228"/>'
                    f'<workbookPr{properties} defaultThemeVersion="166925"/>'
                    '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" windowHeight="12225"/></bookViews>'
                    '<sheets><sheet name="Foglio1" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="191029"/></workbook>')
        shared_xml = XML + f'<sst xmlns="{NS_MAIN}" count="{len(strings)}" uniqueCount="{len(strings)}">{shared}</sst>'
        rels = (XML + f'<Relationships xmlns="{NS_PACKAGE}">'
                f'<Relationship Id="rId3" Type="{NS_REL}/styles" Target="{styles_name}"/>'
                f'<Relationship Id="rId2" Type="{NS_REL}/theme" Target="theme/theme1.xml"/>'
                f'<Relationship Id="rId1" Type="{NS_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
                f'<Relationship Id="rId4" Type="{NS_REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
        extra = [('xl/theme/theme1.xml', THEME),
                 ('docProps/app.xml', XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                                            '<Application>Microsoft Excel</Application></Properties>')]
    elif flavor == 'libre':
        sheet = (XML + f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
                 '<sheetPr filterMode="false"><pageSetUpPr fitToPage="false"/></sheetPr>'
                 f'<dimension ref="A1:{letters(width)}{max(last, 1)}"/>'
                 '<sheetViews><sheetView showFormulas="false" showGridLines="true" showRowColHeaders="true" showZeros="true" '
                 'rightToLeft="false" tabSelected="true" showOutlineSymbols="true" defaultGridColor="true" view="normal" '
                 'topLeftCell="A1" colorId="64" zoomScale="100" zoomScaleNormal="100" zoomScalePageLayoutView="100" workbookViewId="0">'
                 '<selection pane="topLeft" activeCell="A1" activeCellId="0" sqref="A1"/></sheetView></sheetViews>'
                 '<sheetFormatPr defaultColWidth="11.53515625" defaultRowHeight="12.8" zeroHeight="false" outlineLevelRow="0" outlineLevelCol="0"/>'
                 f'<sheetData>{rows}</sheetData>{merge_xml}'
                 '<printOptions headings="false" gridLines="false" gridLinesSet="true" horizontalCentered="false" verticalCentered="false"/>'
                 '<pageMargins left="0.7875" right="0.7875" top="1.05277777777778" bottom="1.05277777777778" header="0.7875" footer="0.7875"/>'
                 '<pageSetup paperSize="9" scale="100" fitToWidth="1" fitToHeight="1" pageOrder="downThenOver" orientation="portrait" '
                 'blackAndWhite="false" draft="false" cellComments="none" horizontalDpi="300" verticalDpi="300" copies="1"/>'
                 '<headerFooter differentFirst="false" differentOddEven="false"><oddHeader>&amp;C&amp;A</oddHeader></headerFooter></worksheet>')
        libre_properties = properties or ' date1904="false"'
        workbook = (XML + f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><fileVersion appName="Calc"/>'
                    f'<workbookPr backupFile="false" showObjects="all"{libre_properties}/><workbookProtection/>'
                    '<bookViews><workbookView showHorizontalScroll="true" showVerticalScroll="true" showSheetTabs="true" xWindow="0" '
                    'yWindow="0" windowWidth="16384" windowHeight="8192" tabRatio="500" firstSheet="0" activeTab="0"/></bookViews>'
                    '<sheets><sheet name="Soci" sheetId="1" state="visible" r:id="rId2"/></sheets>'
                    '<calcPr iterateCount="100" refMode="A1" iterate="false" iterateDelta="0.001"/></workbook>')
        shared_xml = XML + f'<sst xmlns="{NS_MAIN}" count="{len(strings)}" uniqueCount="{len(strings)}">{shared}</sst>'
        rels = (XML + f'<Relationships xmlns="{NS_PACKAGE}">'
                f'<Relationship Id="rId1" Type="{NS_REL}/styles" Target="{styles_name}"/>'
                f'<Relationship Id="rId2" Type="{NS_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
                f'<Relationship Id="rId3" Type="{NS_REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
        extra = [('docProps/app.xml', XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                                            '<Application>LibreOffice/7.6.4.1$Linux_X86_64 LibreOffice_project</Application></Properties>')]
    else:
        sheet = XML + f'<worksheet xmlns="{NS_MAIN}"><sheetData>{rows}</sheetData>{merge_xml}</worksheet>'
        workbook = (XML + f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
                    + (f'<workbookPr{properties}/>' if properties else '') + '<sheets><sheet name="Soci" sheetId="1" r:id="rId1"/></sheets></workbook>')
        shared_xml = XML + f'<sst xmlns="{NS_MAIN}">{shared}</sst>'
        rels = (XML + f'<Relationships xmlns="{NS_PACKAGE}">'
                f'<Relationship Id="rId1" Type="{NS_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
                f'<Relationship Id="rId2" Type="{NS_REL}/styles" Target="{styles_name}"/>'
                f'<Relationship Id="rId3" Type="{NS_REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
        extra = []
    overrides = [('/xl/workbook.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'),
                 ('/xl/worksheets/sheet1.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')]
    if styles is not None:
        overrides.append((f'/xl/{styles_name}', 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'))
    if strings_type:
        overrides.append(('/xl/sharedStrings.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml'))
    if flavor == 'excel':
        overrides.append(('/xl/theme/theme1.xml', 'application/vnd.openxmlformats-officedocument.theme+xml'))
    if extra:
        overrides.append(('/docProps/app.xml', 'application/vnd.openxmlformats-officedocument.extended-properties+xml'))
    content_types = (XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     + ''.join(f'<Override PartName="{name}" ContentType="{kind}"/>' for name, kind in overrides) + '</Types>')
    parts = [('[Content_Types].xml', content_types), ('_rels/.rels', ROOT_RELS), ('xl/workbook.xml', workbook),
             ('xl/_rels/workbook.xml.rels', rels), ('xl/worksheets/sheet1.xml', sheet), ('xl/sharedStrings.xml', shared_xml)] + extra
    if styles is not None:
        parts.append((f'xl/{styles_name}', styles))
    return archive(parts, method)


# Columns of cells below a header: each is read alone and next to other columns.
COLUMN_RECIPES = {
    'bools': [B(1), B(0), B(1)],
    'bools-empty': [B(1), None, B(0)],
    'bools-error': [B(1), E('#N/A'), B(0)],
    'bools-int': [B(1), N(5), B(0)],
    'bools-float': [B(1), N('2.5'), B(0)],
    'bools-text': [B(1), S('x'), B(0)],
    'bools-word': [B(1), S('TRUE'), B(0)],
    'bools-word-empty': [B(1), S('false'), None],
    'bools-na': [B(0), S('N/A'), B(1)],
    'bools-date': [B(1), N(45000, 'date')],
    'bool-words': [S('True'), S('false'), S('TRUE')],
    'bool-words-empty': [S('True'), None, S('FALSE')],
    'bool-words-other': [S('True'), S('Vero'), S('FALSE')],
    'bool-words-int': [S('True'), N(1)],
    'bool-odd': [B(2), B('-1'), B('00'), B('+0')],
    'bool-text-value': [('raw', ' t="b"', '<v>TRUE</v>')],
    'errors': [E('#N/A'), E('#DIV/0!'), E('#VALUE!'), E('#REF!'), E('#NAME?'), E('#NUM!'), E('#NULL!')],
    'errors-text': [E('#N/A'), S('x'), E('#DIV/0!')],
    'errors-int': [N(1), E('#DIV/0!'), N(3)],
    'errors-date': [N(45000, 'date'), E('#VALUE!'), N(45001, 'date')],
    'errors-duration': [N('1.5', 'duration'), E('#VALUE!')],
    'error-formula-text': [FS('#N/A'), FS('#DIV/0!'), S('#N/A'), S('#NAME?')],
    'ints': [N(1), N(20100), N(-7)],
    'ints-empty': [N(1), None, N(3)],
    'ints-float': [N(1), N('2.5'), N(3)],
    'ints-text': [N(1), S('x'), N(3)],
    'ints-numeric-text': [N(1), S('00100'), N(3)],
    'ints-float-text': [N(1), S('2.5'), N(3)],
    'ints-exponent': [N('1E2'), N('1.5E3'), N('2.50E+1'), N('1e-0')],
    'floats': [N('1.5'), N('0.1'), N('-2.25'), N('1E-5'), N('1.7976931348623157E+308'), N('4.9E-324'), N('123456789.123456789')],
    'float-repr': [N('1E16'), N('9999999999999998.5'), N('1.5E16'), N('0.0001'), N('0.00001'), N('1.5E-7'), N('123456789012345.6'),
                   N('1E22'), N('1.5E300'), N('2.5')],
    'uint': [N('9223372036854775808'), N(5)],
    'uint-max': [N('18446744073709551615'), N(0)],
    'uint-negative': [N('9223372036854775808'), N(-5)],
    'uint-empty': [N('9223372036854775808'), None, N(5)],
    'uint-na-text': [N('9223372036854775808'), S('NA'), S(''), S('12')],
    'uint-float': [N('9223372036854775808'), N('1.5')],
    'uint-bool': [N('9223372036854775808'), B(1)],
    'uint-text': [N('9223372036854775808'), S('x')],
    'uint-exponent': [N('1E+19'), N(7)],
    'huge': [N('18446744073709551616'), N(5)],
    'huge-exponent': [N('1E+30'), N(5)],
    'huge-negative': [N('-1E+30'), N('-9223372036854775809'), N(5)],
    'huge-text': [N('1E+30'), S('x'), N('123456789012345678901234567890')],
    'huge-empty': [N('12345678901234567890123'), None],
    'int64-edges': [N('9223372036854775807'), N('-9223372036854775808')],
    'long-digits': [N('00000000000000000000012'), N('+15'), N('-0'), N('0.0'), N('-0.0'), N('1.0'), N('100.000')],
    'text-exponent': [S('1e3'), S('2.5E-2'), S('1E+2'), S('7')],
    'text-exponent-bad': [S('1e3'), S('1e'), S('7')],
    'text-long-fraction': [S('1.123456789012345678'), S('0.1234567890123456789'), S('3')],
    'text-long-int': [S('123456789012345678'), S('5')],
    'text-long-int-float': [S('12345678901234567890'), S('1.5')],
    'text-long-zeros': [S('00000000000000000000001'), S('2.5')],
    'text-uint': [S('9223372036854775808'), S('12')],
    'text-uint-negative': [S('9223372036854775808'), S('-12')],
    'text-uint-empty': [S('18446744073709551615'), None, S('12')],
    'text-beyond': [S('18446744073709551616'), S('12')],
    'text-below': [S('-9223372036854775809'), S('12')],
    'text-inf': [S('inf'), S('-Infinity'), S('+INF'), S('12')],
    'text-inf-only': [S('Inf'), S('infinity')],
    'text-nan-words': [S('nan'), S('NaN'), S('-nan'), S('1.5')],
    'text-plus-nan': [S('+nan'), S('1.5')],
    'text-spaces': [S(' 12 '), S('\t7'), S('1.5 '), S(' +3')],
    'text-tiny': [S('1e-400'), S('0.000000000000000000000000001'), S('4.9e-324'), S('1')],
    'text-big': [S('1e308'), S('17976931348623157e292'), S('1')],
    'text-overflow': [S('1e309'), S('1')],
    'text-phone': [S('+393331234567'), S('0039333123456'), S('3331234567')],
    'text-cap': [S('00100'), S('20100'), S('38122')],
    'times': [N('0.5', 'h:mm:ss'), N('0.25', 'h:mm'), N(0, 'libre-time'), N('0.999988426', 'h:mm:ss'), N('0.000011574', 'long-time')],
    'times-empty': [N('0.5', 'h:mm:ss'), None, N('0.75', 'h:mm')],
    'times-rounding': [N('0.9999999999', 'h:mm:ss'), N('0.99999999', 'h:mm:ss'), N('1.0', 'h:mm:ss'), N('0.0', 'h:mm:ss'),
                       N('-0.0', 'h:mm:ss'), N('0.123456789', 'h:mm:ss'), N('1E-9', 'h:mm:ss')],
    'times-date-style': [N('0.5', 'date'), N('0.75', 'datetime'), N(0, 'date')],
    'times-text': [N('0.5', 'h:mm:ss'), S('18:30')],
    'time-over-day': [N('1.5', 'h:mm:ss'), N('2.25', 'h:mm'), N('45000.75', 'libre-time')],
    'dates': [N(45000, 'date'), N(36526, 'dd/mm/yyyy'), N(29221, 'iso'), N(18264, 'libre-date')],
    'dates-empty': [N(45000, 'date'), None, N(36526, 'date')],
    'dates-na-text': [N(45000, 'date'), S('N/A'), S('')],
    'dates-text': [N(45000, 'date'), S('01/02/1990')],
    'dates-int': [N(45000, 'date'), N(45000)],
    'dates-time': [N(45000, 'date'), N('0.5', 'h:mm')],
    'dates-fraction': [N('45000.5', 'datetime'), N('45000.000011574', 'datetime'), N('45000.9999999999', 'datetime'),
                       N('44196.999999', 'date'), N('45000.123456789', 'libre-datetime')],
    'dates-early': [N(1, 'date'), N(2, 'date'), N(59, 'date'), N(60, 'date'), N(61, 'date'), N('59.5', 'datetime'), N('60.5', 'datetime'),
                    N('1.25', 'datetime')],
    'dates-negative': [N(-1, 'date'), N('-1.5', 'datetime'), N('-0.5', 'datetime'), N(-36522, 'date'), N(-693593, 'date')],
    'dates-before-1677': [N(-81000, 'date'), N(45000, 'date')],
    'dates-bounds': [N(132000, 'date'), N(45000, 'date')],
    'dates-far': [N(2958465, 'date'), N('2958465.999', 'datetime')],
    'dates-overflow': [N(2958466, 'date'), N('1E10', 'date'), N(-693594, 'date'), N('1E+300', 'date'), N('123456789012345678901', 'date'),
                       N(45000, 'date')],
    'dates-overflow-only': [N(2958466, 'date'), N(-700000, 'date')],
    'dates-pandas-edge': [N('132313.9911673', 'datetime'), N('132313.9911674', 'datetime')],
    'dates-pandas-low': [N('-81080.99', 'datetime'), N('-81081.2', 'datetime')],
    'durations': [N('1.5', 'duration'), N('0.5', '[h]:mm:ss'), N(0, 'minutes'), N(3, 'seconds'), N('100.123456', 'duration-ms')],
    'durations-empty': [N('1.5', 'duration'), None, N('0.25', 'hours')],
    'durations-negative': [N('-0.5', 'duration'), N('-1.25', 'duration'), N(-3, 'duration')],
    'durations-rounding': [N('3.0000001', 'duration'), N('0.00000001', 'duration'), N('0.000011574', 'duration'), N('0.9999999999', 'duration'),
                           N('1.00000578', 'duration'), N('2.0000057870370', 'duration'), N('99999.99999999', 'duration')],
    'durations-text': [N('1.5', 'duration'), S('36:00:00')],
    'durations-dates': [N('1.5', 'duration'), N(45000, 'date')],
    'durations-styles': [N('1.5', 'hours-only'), N('1.5', 'upper-duration'), N('1.5', 'seconds-fraction'), N('1.5', 'colour-duration'),
                         N('1.5', 'elapsed-text')],
    'duration-sections': [N('1.5', 'second-section'), N('1.5', 'first-section')],
    'iso-dates': [ISO('2024-01-31'), ISO('1990-02-03')],
    'iso-datetimes': [ISO('2024-01-31T10:20:30'), ISO('2024-01-31T10:20:30.123Z'), ISO('2024-01-31T10:20'), ISO('1990-02-03T00:00:00Z')],
    'iso-datetimes-empty': [ISO('2024-01-31T10:20:30'), None],
    'iso-times': [ISO('10:20:30'), ISO('T10:20'), ISO('23:59:59.999'), ISO('00:00:00.001'), ISO('10:20:30.5Z')],
    'iso-mixed': [ISO('2024-01-31'), ISO('2024-01-31T10:20:30'), ISO('10:20:30')],
    'iso-date-empty': [ISO('2024-01-31'), None, S('x')],
    'iso-trailing': [ISO('2024-01-31T'), ISO('2024-01-31Z'), ISO('2024-01-3110:20'), ISO('2024-01-31 10:20'), ISO('2024-01-31xyz')],
    'iso-fractions': [ISO('10:20:30.1'), ISO('10:20:30.12'), ISO('10:20:30.001'), ISO('10:20:30.009'), ISO('10:20:30.57'),
                      ISO('10:20:30.999'), ISO('10:20:30.1234')],
    'iso-durations': [ISO('PT1H30M'), ISO('PT36H'), ISO('PT1.5S'), ISO('PT90M'), ISO('PT0S'), ISO('PT1H2M3.004S'), ISO('PT0.001S'),
                      ISO('PT0.007S'), ISO('PT59.999S')],
    'iso-durations-empty': [ISO('PT1H30M'), None],
    'iso-old': [ISO('0001-01-01'), ISO('0999-12-31T23:59:59'), ISO('9999-12-31T23:59:59.999')],
    'iso-bad-month': [ISO('2024-13-01')],
    'iso-bad-day': [ISO('2023-02-29')],
    'iso-bad-hour': [ISO('24:00:00')],
    'iso-bad-text': [ISO('yesterday')],
    'iso-bad-duration': [ISO('PT')],
    'iso-year-zero': [ISO('0000-01-01')],
    'unknown-type': [('raw', ' t="x"', '<v>abc</v>'), ('raw', ' t=""', '<v>12</v>'), ('raw', ' t="N"', '<v>5</v>')],
    'empty-values': [('raw', ' t="b"', '<v></v>'), ('raw', ' t="e"', '<v/>'), ('raw', ' t="d"', ''), ('raw', ' t="str"', '<v></v>'),
                     ('raw', ' t="inlineStr"', ''), ('raw', ' t="n"', '<v></v>'), S('x')],
    'styled-blank': [('blank', 'date'), S('x'), ('blank', 'duration')],
    'style-outside': [('raw', ' s="999"', '<v>45000</v>'), ('raw', ' s=""', '<v>45000</v>'), ('raw', ' s="-1"', '<v>45000</v>')],
    'formula-results': [('raw', ' t="str"', '<f>A1</f><v>testo</v>'), ('raw', ' t="b"', '<f>TRUE()</f><v>1</v>'),
                        ('raw', ' t="e"', '<f>1/0</f><v>#DIV/0!</v>'), ('raw', '', '<f>1+1</f><v>2</v>'), ('raw', ' t="str"', '<f>""</f><v></v>')],
    'mixed-kinds': [S('x'), N(5), N('2.5'), B(1), E(), N(45000, 'date'), N('0.5', 'h:mm'), N('1.5', 'duration'), ISO('2024-01-31'), None],
    'datetime-objects': [N(45000, 'date'), N(132000, 'date'), None],
    'timedelta-object': [N('1.5', 'duration'), S('x'), None],
    'timedelta-time': [N('1.5', 'duration'), N('0.5', 'h:mm')],
    'na-texts': [S('NA'), S('N/A'), S('null'), S('#N/A'), S('nan'), S('None'), S('<NA>'), S('-'), S('n/a')],
    'trailing-blank-text': [S('x'), S(' '), S('')],
    'blank-text-first': [S(' '), S('x')],
    'inline': [IS('Mario'), IS(''), IS('12'), IS('True')],
}
# The columns a recipe is read next to (None: alone).
COMPANIONS = {
    None: None,
    'text': [S('a'), S('b'), S('c'), S('d'), S('e'), S('f'), S('g'), S('h'), S('i'), S('j')],
    'int': [N(1), N(2), N(3), N(4), N(5), N(6), N(7), N(8), N(9), N(10)],
    'float': [N('0.5'), None, N('1.5'), N(3), None, N(4), N('2.5'), None, N(1), N(2)],
    'date': [N(45000, 'date'), None, N(45002, 'date'), None, None, N(45001, 'date'), None, N(45003, 'date'), None, None],
    'duration': [N('0.5', 'duration'), None, None, N('1.5', 'duration'), None, N(2, 'duration'), None, None, N(1, 'duration'), None],
    'bool': [B(1), B(0), B(1), B(0), B(1), B(0), B(1), B(0), B(1), B(0)],
    'empty': [None, None, None, None, None, None, None, None, None, None],
    'uint': [N('9223372036854775808'), N(1), N(2), N(3), N(4), N(5), N(6), N(7), N(8), N(9)],
}

HEADERS = [
    [S('Nome'), N(2024), N(2025)],
    [N(1), N(2), N(3)],
    [N(1), N('2.5'), N(3)],
    [N(1), N(1), N(1)],
    [N(1), B(1), N('1.5'), B(1)],
    [B(1), B(0)],
    [B(1), B(1), B(0)],
    [B(1), N(0), N(1)],
    [B(1), S('x')],
    [B(1), E()],
    [N(1), E()],
    [E(), E(), E()],
    [E('#DIV/0!'), S('nan'), S('nan.1')],
    [S('x'), E(), None],
    [N(45000, 'date'), N(45001, 'date')],
    [N(45000, 'date'), N(45000, 'date'), N(45000, 'date')],
    [N(45000, 'date'), S('Nome')],
    [N(45000, 'date'), None],
    [N(45000, 'date'), E()],
    [N(45000, 'date'), N(5)],
    [N(45000, 'date'), N(132000, 'date')],
    [N(132000, 'date'), E()],
    [N('45000.5', 'datetime'), N('45000.5', 'datetime')],
    [N('0.5', 'h:mm'), N('0.5', 'h:mm'), N('0.75', 'h:mm:ss')],
    [N('1.5', 'duration'), N('2.5', 'duration')],
    [N('1.5', 'duration'), N('1.5', 'duration'), E()],
    [N('1.5', 'duration'), S('x')],
    [N('1.5', 'duration'), N(45000, 'date')],
    [N('-1.5', 'duration'), N(1, 'duration'), N('0.000011574', 'duration')],
    [ISO('2024-01-31'), ISO('2024-01-31'), ISO('2024-01-31T00:00:00')],
    [ISO('10:20:30'), ISO('PT1H')],
    [N('2.5'), N('2.5'), S('2.5'), S('2.5.1')],
    [N('1E16'), N('0.00001'), N('1.5E-7')],
    [N('9007199254740993'), N(2)],
    [N('1E+30'), N(2)],
    [S('A'), S('A'), S('A.1'), None, S('Unnamed: 3'), S('A')],
    [None, None, S('Unnamed: 0'), None],
    [S('1'), N(1), S('1.1'), N(1)],
    [S('True'), B(1), B(1)],
    [S(''), S(' '), S('NA'), S('nan')],
    [N(0), N('-0.0'), B(0)],
    [N(-5), N(5), N('-5.5')],
    [S('Nome'), None, None, S('Cognome')],
    [N(1), None, N(2)],
    [E(), N('2.5'), N(3)],
    [FS('Totale'), IS('Inline'), S('Shared')],
]


def recipe_books():
    """Each recipe alone and next to each companion, in both date systems for the kinds that depend on it."""
    for name, cells in COLUMN_RECIPES.items():
        for companion, others in COMPANIONS.items():
            grid = [[S('Valore')] + ([S('Altro')] if others else [])]
            for index, spec in enumerate(cells):
                grid.append([spec] + ([others[index]] if others else []))
            yield book(grid)
            if companion in (None, 'text') and any(spec and spec[0] == 'n' and spec[2] != 'general' for spec in cells):
                yield book(grid, date1904='1')
        # The same cells as one row: the row's own inference.
        yield book([[S(f'C{index}') for index in range(len(cells))], cells])
        yield book([[S(f'C{index}') for index in range(len(cells))], cells, cells[::-1]])


def header_books():
    for header in HEADERS:
        width = len(header)
        # A label outside int64 is not required of the reader.
        required = not any(spec and spec[0] == 'n' and spec[1] == '1E+30' for spec in header)
        yield book([header]), required
        yield book([header, [S(f'v{index}') for index in range(width)]]), required
        yield book([header, [N(index) for index in range(width)], [N(index * 2) for index in range(width)]]), required
        yield book([header, [N(45000 + index, 'date') for index in range(width)]], flavor='excel'), required
        yield book([header, [S('x')] * (width + 1)], flavor='libre'), required


def format_books():
    """Every format on a serial with a fraction, on a day fraction and on a whole serial, in both date systems."""
    for name, _ in FORMATS:
        for date1904 in (None, '1'):
            yield book([[S('Valore'), S('Nota')], [N('45000.75', name), S('a')], [N('0.75', name), S('b')], [N(45000, name), S('c')],
                        [N('1.5', name), None]], date1904=date1904)
    # A custom numFmt that redefines a built-in id, and a stylesheet without cell formats.
    yield book([[S('Valore')], [N(45000, 'date')]], styles=stylesheet(overrides=[(14, '0.00')]))
    yield book([[S('Valore')], [N(45000, 'int')]], styles=stylesheet(overrides=[(1, 'dd/mm/yyyy')]))
    yield book([[S('Valore')], [N(45000, 'date')]], styles=stylesheet(formats=[('general', 0)]))


MEMBER_HEADER = [S('Nome'), S('Cognome'), S('Sesso'), S('Codice fiscale'), S('Data di nascita'), S('Indirizzo'), S('CAP'), S('Telefono'),
                 S('Scadenza certificato'), S('Numero tessera'), S('Attivo'), S('Ora corso'), S('Durata'), S('Quota'), S('Note')]


def member_rows(date_style, time_style, duration_style, text=S):
    return [
        MEMBER_HEADER,
        [text('Mario'), text('Rossi'), text('M'), text('RSSMRA80A01H501U'), N(29221, date_style), text('Via Roma 1'), text('00100'),
         text('3330000000'), N(46386, date_style), N(42), B(1), N('0.75', time_style), N('0.0625', duration_style), N('35.5'), None],
        [text('Anna'), text('Bianchi'), text('F'), text('BNCNNA90B43F205X'), N(32907, date_style), text('Via Milano 2'), N(20100),
         None, None, N(43), B(0), N('0.4375', time_style), N('1.5', duration_style), N(40), E('#N/A')],
        [text('Luca'), text('Verdi'), None, None, text('03/02/1990'), None, None, text('02 1234567'), text('2027-01-31'), None, None, None,
         None, E('#DIV/0!'), FS('socio dal 2020')],
    ]


def structure_books():
    for flavor in ('plain', 'excel', 'libre'):
        for date1904 in (None, '1', 'true', '0', 'false'):
            yield book(member_rows('date', 'h:mm', 'duration'), flavor=flavor, date1904=date1904, method=zipfile.ZIP_DEFLATED)
        yield book(member_rows('libre-date', 'libre-time', '[h]:mm:ss'), flavor=flavor, merges=('A1:B1',))
        yield book(member_rows('dd/mm/yyyy', 'h:mm:ss', 'minutes', text=IS), flavor=flavor)
        yield book(member_rows('iso', 'long-time', 'hours'), flavor=flavor, cell_refs=False)
        yield book(member_rows('date', 'h:mm', 'duration'), flavor=flavor, cell_refs=False, row_refs=False)
        yield book(member_rows('date', 'h:mm', 'duration'), flavor=flavor, first_row=3)
        yield book(member_rows('date', 'h:mm', 'duration'), flavor=flavor, gaps={1: 1, 3: 2})
        yield book(member_rows('datetime', 'mm:ss', 'seconds'), flavor=flavor, formulas=True)
        # Merged header cells: only the first cell of a range holds the value.
        yield book([[S('Anagrafica'), ('blank', 'general'), ('blank', 'general'), S('Tessera')],
                    [S('Nome'), S('Cognome'), S('Nascita'), S('Numero')],
                    [S('Mario'), S('Rossi'), N(29221, 'date'), N(42)]], flavor=flavor, merges=('A1:C1',))
        yield book([[S('Anagrafica'), None, None, S('Tessera')], [S('Mario'), S('Rossi'), N(29221, 'date'), N(42)]],
                   flavor=flavor, merges=('A1:C1',))
        yield book([[S('Soci 2026')], [], [S('Nome'), S('Nascita')], [S('Mario'), N(29221, 'date')]], flavor=flavor, merges=('A1:B1',))
        yield book([[N(2024), N(2025), N(45000, 'date')], [B(1), B(0), E()]], flavor=flavor)
        yield book([[S('A'), S('B'), S('C')], [None, None, E()], [S('x')], [None, None, None, B(0)], [], [None, N(0)]], flavor=flavor)
        yield book([[S('A')], [B(1)], [B(0)]], flavor=flavor)
        yield book([[S('A')], [None], [S(' ')], [S('x')], [S('')], [N(0)], [E()], [B(0)]], flavor=flavor)
        yield book([[None], [S(' ')], [S('A')], [S('x')]], flavor=flavor)
        yield book([[S(' ')], [None], [S('x')]], flavor=flavor)
        yield book([[E()], [S('x')]], flavor=flavor)
        yield book([[S('A'), S('B')], [N(1), N(2)], [N(3), N(4)]], flavor=flavor)
        yield book([[S('A'), S('B')], [N(1), B(1)], [N(3), B(0)]], flavor=flavor)
        yield book([[S('A'), S('B')], [ISO('2024-01-31'), ISO('2024-01-31T10:00:00')], [ISO('10:00:00'), ISO('PT1H')]], flavor=flavor)
    members = member_rows('date', 'h:mm', 'duration')
    yield book(members, styles=None)
    yield book(members, styles_name='stili.xml')
    yield book(members, strings_type=False)
    yield book([[IS('A'), IS('B')], [N(45000, 'date'), IS('x')]], strings_type=False)
    yield book(members, shared_first=('non usata', 'x005F_y'))
    yield book([[S('A'), S('B_x005F_C')], [S('a_x000D_b'), S('x005F_')], [IS('a_x000D_b'), IS('c_x005F_d')], [FS('a_x000A_b'), FS('x005F_')]])
    yield book([[S('A'), S('B')], [('raw', ' t="s"', '<v>99</v>'), S('x')]])
    yield book([[S('A'), S('B')], [('raw', '', '<v>abc</v>'), S('x')]])
    yield book([[S('A'), S('B')], [('raw', '', '<v>1,5</v>'), S('x')]])
    yield book([[S('A'), S('B')], [('raw', '', '<v>1e400</v>'), S('x')]])
    yield book([[S('A'), S('B')], [('raw', f' s="{STYLE["date"]}"', '<v>1e400</v>'), S('x')]])
    yield book([[S('A'), S('B')], [('raw', f' s="{STYLE["duration"]}"', '<v>1e400</v>'), S('x')]])
    yield book([[S('A'), S('B')], [('raw', '', '<v>1e-400</v>'), S('x')]])
    yield book([[S('A'), S('B')], [('raw', ' s="x"', '<v>1</v>'), S('x')]])


RANDOM_TEXTS = NAMES + ['True', 'FALSE', 'false', '1e3', '2.5E-2', 'inf', '-Infinity', '9223372036854775808', '18446744073709551616',
                        '1.123456789012345678', '12345678901234567', '00100', ' 7 ', '3.5', '+39333', '1.', '.5', '0x10', '1,5', 'NA', '',
                        'nan', '01/02/1990', '2026-12-31', '18:30', '#N/A', '#DIV/0!', '-9223372036854775809', '1e-320', '+nan', 'TRUE']
RANDOM_NUMBERS = ['5', '100', '3.0', '2.5', '1E2', '0.1', '45000', '45000.5', '0', '-7', '1.5E-2', '20100', '0.5', '0.75', '1', '59', '60',
                  '61', '-1.5', '9223372036854775808', '18446744073709551615', '1E+19', '1E+30', '123456789012', '2958465', '2958466',
                  '132000', '0.999999999', '36526.000011574', '-0.25', '1.0000057', '12345678901234567890', '-80000']
RANDOM_ISO = ['2024-01-31', '2024-01-31T10:20:30', '10:20:30', '1990-02-03T00:00:00.500Z', 'PT1H30M', 'PT2.5S', '23:59', '2300-01-01',
              '2024-02-30', '0001-01-01T00:00:00']
RANDOM_PROFILES = ('text', 'numbers', 'numeric_text', 'bools', 'bool_words', 'errors', 'dates', 'times', 'durations', 'iso', 'mixed',
                   'empty', 'ints', 'serial_styles')
ERRORS = ['#N/A', '#DIV/0!', '#VALUE!', '#REF!', '#NAME?', '#NUM!', '#NULL!']


def random_spec(generator, profile):
    roll = generator.random()
    if roll < 0.12:
        return None
    if roll < 0.17:
        return S(generator.choice(NA_TEXTS))
    if roll < 0.2:
        return E(generator.choice(ERRORS))
    if profile == 'empty':
        return None
    if profile == 'mixed':
        profile = generator.choice(RANDOM_PROFILES[:10] + ('ints', 'serial_styles'))
    if profile == 'text':
        text = generator.choice(RANDOM_TEXTS)
        return generator.choice([S, S, S, IS, FS])(text)
    if profile == 'numbers':
        return N(generator.choice(RANDOM_NUMBERS), generator.choice(['general', 'general', 'fixed', 'text', 'quoted', 'euro']))
    if profile == 'ints':
        return N(generator.choice(['1', '2', '45000', '-3', '0', '7.0', '1E2', '20100']))
    if profile == 'numeric_text':
        return S(generator.choice(NUMERIC_TEXTS + ['1e3', '5E-3', '123456789012345678', '9223372036854775808', '0.12345678901234567890',
                                                   'inf', '-12', '1e-5']))
    if profile == 'bools':
        return B(generator.randint(0, 1))
    if profile == 'bool_words':
        return S(generator.choice(['True', 'False', 'TRUE', 'FALSE', 'true', 'false']))
    if profile == 'errors':
        return E(generator.choice(ERRORS))
    if profile == 'dates':
        return N(generator.choice(DATES + ['0', '0.5', '-5', '1', '60', '2958466', '132000.5']), generator.choice(DATE_STYLES))
    if profile == 'times':
        return N(generator.choice(['0.5', '0.25', '0', '0.999', '0.75', '0.000011574', '0.9999999999', '1.25']), generator.choice(TIME_STYLES))
    if profile == 'durations':
        return N(generator.choice(['0.5', '1.5', '0', '3', '-0.5', '100.123456', '0.000011574', '2.0000001', '36.75']),
                 generator.choice(DURATION_STYLES))
    if profile == 'iso':
        return ISO(generator.choice(RANDOM_ISO))
    return N(generator.choice(RANDOM_NUMBERS), generator.choice([name for name, _ in FORMATS]))


def random_header(generator, column):
    roll = generator.random()
    if roll < 0.62:
        text = generator.choice(['Nome', 'Cognome', 'CAP', 'Data', 'Nome', 'Unnamed: 1', 'Nome.1', 'Note', ' Tel ', 'NA', '2024', '1', 'True',
                                 '2.5', 'nan'])
        return generator.choice([S, S, IS, FS])(text)
    if roll < 0.7:
        return None
    if roll < 0.8:
        return N(generator.choice(['1', '2', '2024', '2.5', '1.0', '0', '-3', '1E2']))
    if roll < 0.85:
        return B(generator.randint(0, 1))
    if roll < 0.9:
        return E(generator.choice(ERRORS))
    if roll < 0.95:
        return N(generator.choice(['45000', '45001', '45000.5', '132000']), generator.choice(DATE_STYLES))
    if roll < 0.975:
        return N(generator.choice(['0.5', '1.5']), generator.choice(DURATION_STYLES + TIME_STYLES))
    return ISO(generator.choice(RANDOM_ISO[:5]))


def random_book(generator):
    width, height = generator.randint(1, 6), generator.randint(0, 7)
    # Frames of one kind are as likely as mixed ones: their rows keep numpy types.
    if generator.random() < 0.35:
        profiles = [generator.choice(RANDOM_PROFILES)] * width
    else:
        profiles = [generator.choice(RANDOM_PROFILES) for _ in range(width)]
    grid = [[random_header(generator, column) for column in range(width)]]
    for _ in range(height):
        grid.append([random_spec(generator, profiles[column]) for column in range(width)])
    gaps = {index: 1 for index in range(len(grid)) if generator.random() < 0.08}
    return book(grid, flavor=generator.choice(['plain', 'plain', 'excel', 'libre']), date1904=generator.choice([None, None, None, '1', '0']),
                cell_refs=generator.random() < 0.9, row_refs=generator.random() < 0.9, gaps=gaps,
                first_row=generator.choice([1, 1, 1, 1, 2]))


T = datetime.time
TD = datetime.timedelta
WRITTEN2 = [
    [['Nome', 'Attivo'], ['Mario', True], ['Anna', False]],
    [['Nome', 'Attivo'], ['Mario', True], ['Anna', None]],
    [['Nome', 'Ora'], ['Mario', T(18, 30)], ['Anna', T(9, 0, 15)]],
    [['Nome', 'Ora'], ['Mario', T(18, 30)], ['Anna', None]],
    [['Nome', 'Durata'], ['Mario', TD(hours=1, minutes=30)], ['Anna', TD(days=2, seconds=5)]],
    [['Nome', 'Durata'], ['Mario', TD(hours=1, minutes=30)], ['Anna', None]],
    [['Nome', 'Durata'], ['Mario', TD(hours=1, minutes=30)], ['Anna', 'un giorno']],
    [['A', 'B'], [1, 2], [3, 4]],
    [['A'], [1], [2]],
    [['A', 'B'], [True, False], [False, True]],
    [['A', 'B'], [True, 1], [False, 2]],
    [['A', 'B'], [1, 2.5], [3, 4]],
    [['Nome', 2024, 2025], ['Mario', 10, 20]],
    [[1, 2, 3], ['a', 'b', 'c']],
    [[D(2024, 1, 1), D(2025, 1, 1)], ['a', 'b']],
    [['Nome', D(2024, 1, 1)], ['Mario', 5]],
    [[True, False], ['a', 'b']],
    [['A', 'B'], ['=1/0', 'x'], ['=NA()', 'y']],
    [['A', 'B'], [D(1850, 5, 6), 'x'], [D(1600, 1, 1), 'y']],
    [['A', 'B'], [D(1600, 1, 1), 'x'], [None, 'y']],
    [['A', 'B'], [D(2020, 1, 1), TD(hours=5)], [None, None], [D(2021, 1, 1), None], [None, TD(days=1)]],
    [['A', 'B', 'C'], [D(2020, 1, 1), TD(hours=5), T(1, 2)], [None, None, None], [None, TD(days=1), None]],
    [['A', 'B'], [T(1, 2, 3), TD(hours=5)], [None, TD(days=1)]],
    [['A', 'B'], [1, 'x'], [2**63, 'y']],
    [['A', 'B'], [1, 5], [2**62, 6]],
    [['A', 'B'], ['x', 1.5], ['y', 1e16], ['z', 1e-5], ['w', 123456.789]],
]


def new_books():
    """(workbook, required) for the families added after the first corpus."""
    for rows in WRITTEN2:
        yield written(rows), True
    for data in recipe_books():
        yield data, True
    for data, required in header_books():
        yield data, required
    for data in format_books():
        yield data, True
    for data in structure_books():
        yield data, True
    # Read by pandas, outside what the reader takes: a shared string counted from the end, a duration beyond Timedelta.
    yield book([[S('A'), S('B')], [('raw', ' t="s"', '<v>-1</v>'), S('x')]]), False
    yield book([[S('A'), S('B')], [('raw', f' s="{STYLE["duration"]}"', '<v>200000</v>'), S('x')]]), False
    generator = random.Random(2026111001)
    for _ in range(RANDOM_BOOKS):
        yield random_book(generator), False


RANDOM_BOOKS = 3000


def value(item):
    try:
        json.dumps(item)
        flags = 'J'
    except TypeError:
        flags = '-'
    flags = ('T' if item else 'F') + flags
    if item is pandas.NaT:
        return ['nat', str(item), flags]
    if isinstance(item, pandas.Timedelta):
        return ['td', str(item), flags]
    if isinstance(item, datetime.timedelta):
        return ['pytd', str(item), flags]
    if isinstance(item, (pandas.Timestamp, datetime.datetime)):
        return ['t', str(item), flags]
    if isinstance(item, datetime.date):
        return ['date', str(item), flags]
    if isinstance(item, datetime.time):
        return ['time', str(item), flags]
    if isinstance(item, numpy.bool_):
        return ['nb', str(item), flags]
    if isinstance(item, bool):
        return ['b', str(item), flags]
    if isinstance(item, str):
        return ['s', item, flags]
    if type(item) is numpy.int64:
        return ['i64', str(item), flags]
    if type(item) is numpy.uint64:
        return ['u64', str(item), flags]
    if type(item) is int:
        return ['i', str(item), flags]
    if type(item) in (float, numpy.float64):
        return ['nan', str(item), flags] if item != item else ['f', str(item), flags]
    return ['other', type(item).__name__, flags]


def import_cell_text(item):
    """The view's converter of the columns read as text (_import_cell_text)."""
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return str(item)
    return item


def outcome(data, text=None):
    """The frame of a workbook; ``text``: the columns read with the text converter."""
    converters = {} if text is None else {'converters': {column: import_cell_text for column in text}}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            frame = pandas.read_excel(io.BytesIO(data), engine='openpyxl', **converters)
    except Exception as error:  # the view answers the 500 page for every one
        return {'error': type(error).__name__}
    labels = [value(label) for label in frame.columns.tolist()]
    rows = [[value(row.iloc[index]) for index in range(len(labels))] for _, row in frame.iterrows()]
    result = {'labels': labels, 'rows': rows}
    if any(item[0] == 'other' for row in rows + [labels] for item in row):
        result['other'] = True
    return result


def numeric_texts():
    """Texts and what maybe_convert_numeric makes of each: an int, a float or an error; and the float it is next to a float."""
    generator = random.Random(2026111002)
    texts = ['', ' ', '0', '-0', '+0', '1', '-1', '12', ' 12', '12 ', '\t12\n', '\x0b12\x0c', '12\xa0', '1 2', '1_000', '0x10', '１２', '--1',
             '+-1', '+', '-', '.', '1.', '.5', '-.5', '+.5', '1.5.', '1.e2', '.e2', '+.5e-3', '1e', '1e+', '1e-', '1 e5', '1e 5', 'e5',
             '1e5', '1E5', '1e+5', '1e-5', '1e05', '1e0000000002', '1e00000000000000002', '1e0000000000000000000000002', '1e308',
             '1e309', '1E400', '1e-400', '1e-308', '1e-309', '1e-323', '1e-324', '1e-616', '1e-617', '1e-700', '5e-324', '4.9e-324',
             '17976931348623157e292', '1.7976931348623157e308', '1.7976931348623159e308', '2e308', '0e999', '0e-999', '0.0e400',
             'inf', 'Inf', 'INF', 'iNf', '+inf', '-inf', 'infinity', 'Infinity', '-INFINITY', '+infinity', ' inf', 'inf ', 'infinit',
             'infinityy', 'in', '++inf', 'nan', 'NaN', '+nan', '-nan', 'NAN', 'nan ', 'True', 'true', '1,5', '1.5e', '1.5E+', '1d5', '1f',
             '0.1', '0.2', '0.3', '2.5', '0.1234567890123456', '0.12345678901234567', '0.123456789012345678', '0.1234567890123456789',
             '1.123456789012345678', '123456789012345678', '1234567890123456789', '12345678901234567890', '123456789012345678901',
             '1234567890123456789012', '00000000000000000000001', '0000000000000000001', '00000000000000000', '000000000000000001.5',
             '0.000000000000000000000000001', '0.00000000000000001', '0.0000000000000001', '9223372036854775807',
             '9223372036854775808', '-9223372036854775808', '-9223372036854775809', '18446744073709551615', '18446744073709551616',
             '9007199254740993', '9007199254740992', '9007199254740991', '99999999999999999', '9999999999999999', '999999999999999',
             '0.3e1', '3e-1', '1e22', '1e23', '8.5e-320', '123e-10', '00100', '+39333', '0039', '1.0', '100.000', '-0.0', '1e1000',
             '1e-1000', '1e99999999', '1e999999999', '1e9999999999', '1e99999999999999999', '1e-99999999999999999', 'é', '1é', '٣', '1٣']
    digits = '0123456789'
    for _ in range(6000):
        kind = generator.random()
        whole = ''.join(generator.choice(digits) for _ in range(generator.choice([0, 1, 1, 2, 3, 5, 8, 12, 15, 16, 17, 18, 19, 20, 22, 25])))
        fraction = ''.join(generator.choice(digits) for _ in range(generator.choice([0, 1, 2, 3, 5, 8, 12, 15, 16, 17, 18, 19, 22])))
        text = generator.choice(['', '', '', '-', '+']) + whole
        if kind < 0.75:
            text += '.' + fraction if generator.random() < 0.8 else fraction
        if kind > 0.4:
            text += generator.choice('eE') + generator.choice(['', '', '-', '+']) + str(generator.choice(
                [0, 1, 2, 5, 10, 15, 16, 17, 20, 22, 23, 50, 100, 290, 300, 305, 308, 309, 310, 320, 323, 324, 330, 400, 616, 617]))
        if generator.random() < 0.05:
            text = generator.choice([' ', '\t', '']) + text + generator.choice([' ', '\n', ''])
        texts.append(text)
    for _ in range(1500):
        texts.append(str(generator.choice([generator.randint(-10**6, 10**6), generator.randint(-2**64, 2**65),
                                           generator.randint(2**63 - 3, 2**63 + 3), generator.randint(2**64 - 3, 2**64 + 3),
                                           generator.randint(-2**63 - 3, -2**63 + 3)])))
    for _ in range(1500):
        texts.append(repr(generator.choice([generator.random(), generator.uniform(-1e6, 1e6), generator.random() * 10 ** generator.randint(-30, 30),
                                            generator.uniform(0, 100)])))
    from pandas._libs import lib
    cases = []
    for text in dict.fromkeys(texts):
        case = [text]
        try:
            result, _ = lib.maybe_convert_numeric(numpy.array([text], dtype=object), set(), False)
            case += ['int' if result.dtype.kind in 'iu' else 'float', str(result[0])]
        except (ValueError, TypeError):
            case += ['error', '']
        try:
            result, _ = lib.maybe_convert_numeric(numpy.array([text, 0.5], dtype=object), set(), False)
            case.append(str(result[0]))
        except (ValueError, TypeError):
            case.append('')
        cases.append(case)
    return cases


def pieces(data, blobs):
    """The bytes as text (one character per byte), each occurrence of a blob replaced by its index."""
    out = [data]
    for index, blob in enumerate(blobs):
        split = []
        for piece in out:
            if isinstance(piece, int):
                split.append(piece)
                continue
            parts = piece.split(blob)
            for position, part in enumerate(parts):
                if position:
                    split.append(index)
                if part:
                    split.append(part)
        out = split
    return [piece if isinstance(piece, int) else piece.decode('latin-1') for piece in out]


def compressed(payload):
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
    return buffer.getvalue()


def main():
    generator = random.Random(20261110)
    inputs = [(written(rows), True) for rows in WRITTEN] + [(data, False) for data in handmade()]
    for _ in range(1500):
        inputs.append((random_workbook(generator), False))
    inputs += list(new_books())
    # The parts most workbooks share are written once.
    blobs = [STYLES2.encode(), STYLES.encode()]
    cases, texts = [], []
    for data, required in inputs:
        case = {'input': pieces(data, blobs), **outcome(data)}
        if required and 'error' not in case and 'other' not in case:
            case['required'] = True
        if 'labels' in case:
            # The same workbook with the text converter on every other column named by text, and on all of them.
            names = [label[1] for label in case['labels'] if label[0] == 's']
            for text in dict.fromkeys((tuple(names[::2]), tuple(names))):
                read = {'case': len(cases), 'text': list(text), **outcome(data, text)}
                if required and 'error' not in read and 'other' not in read:
                    read['required'] = True
                texts.append(read)
        cases.append(case)
    versions = {'pandas': pandas.__version__, 'openpyxl': openpyxl.__version__, 'numpy': numpy.__version__}
    OUTPUT.write_bytes(compressed({**versions, 'blobs': [blob.decode('latin-1') for blob in blobs], 'cases': cases}))
    TEXT_OUTPUT.write_bytes(compressed({**versions, 'cases': texts}))
    numbers = numeric_texts()
    NUMERIC_OUTPUT.write_bytes(compressed({**versions, 'cases': numbers}))
    kinds = {}
    for case in cases:
        kind = 'error:' + case['error'] if 'error' in case else 'other' if 'other' in case else 'frame'
        kinds[kind] = kinds.get(kind, 0) + 1
    print(len(cases), 'cases', kinds, sum(1 for case in cases if case.get('required')), 'required', OUTPUT.stat().st_size, 'bytes;',
          len(texts), 'text readings', sum('rows' in case for case in texts), 'frames', TEXT_OUTPUT.stat().st_size, 'bytes;',
          len(numbers), 'numeric texts', NUMERIC_OUTPUT.stat().st_size, 'bytes')


if __name__ == '__main__':
    main()
