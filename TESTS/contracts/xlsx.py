"""Opt-in XLSX parity for family contracts (xlsx-exports.md); no Django imports.

A workbook is never byte-reproducible as a whole: docProps/core.xml holds the
creation and modification times and the zip entries hold local time stamps.
The standard is therefore part by part:

- the same zip entry names in the same order;
- per entry the same compression method and general purpose flags;
- byte-identical uncompressed parts, except the two ``dcterms:created`` /
  ``dcterms:modified`` values of docProps/core.xml, which must fall within the
  request window (``before`` truncated to the second, ``after``);
- zip entry time stamps and the compressed bytes themselves are free.

An authored expectation names the workbook by the inputs the view hands its
library, as ``{'$xlsx': spec}``:

- ``{'engine': 'excel_utils', 'columns': [...], 'data': [[value, ...], ...]}``:
  application.utils.excel_utils.get_excel_base64(*data, columns=columns);
- ``{'engine': 'xlsxwriter', 'sheet': name, 'columns': [...], 'rows': [[...]],
  'index': bool, 'widths': [...]}``: pandas.DataFrame(rows) with those columns,
  ``to_excel(writer, sheet_name=sheet, index=index)`` through xlsxwriter and
  ``set_column(i, i, widths[i])``;
- ``{'engine': 'openpyxl_rows', 'sheets': [[title, [[str, ...], ...]], ...]}``:
  openpyxl.Workbook() without its default sheet and the rows appended;
  ``'stream': True`` saves it to a write-only stream (wb.save(HttpResponse)),
  where zipfile writes each entry with a data descriptor (flag 0x08).

Values are JSON strings and nulls, or ``{'int': text}``, ``{'float': repr}``,
``{'decimal': text}``, ``{'bool': b}``, ``{'list': [...]}``, ``{'dict': [[k, v], ...]}``.
The expected workbook is built here with the same libraries (pandas 2.2.3,
openpyxl 3.1.5 without lxml, XlsxWriter 3.2.2) and compared with the response.
"""
import base64
import io
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal

__all__ = ('decode', 'build', 'parts', 'difference', 'matches', 'resolve')

STAMP = re.compile(rb'(<dcterms:(created|modified) xsi:type="dcterms:W3CDTF">)(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)(</dcterms:\2>)')


def decode(value):
    """The Python value of a typed JSON value."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list):
        return [decode(item) for item in value]
    (kind, raw), = value.items()
    return {'int': int, 'float': float, 'decimal': Decimal, 'bool': bool,
            'list': lambda items: [decode(item) for item in items],
            'dict': lambda pairs: {key: decode(item) for key, item in pairs}}[kind](raw)


def _excel_utils(columns, data):
    import pandas as pd
    df = pd.DataFrame(list(zip(*[[decode(value) for value in column] for column in data])), columns=columns)
    for col in df.columns:
        try:
            numeric = pd.to_numeric(df[col].dropna(), errors='coerce').notna()
            if numeric.all():
                df[col] = pd.to_numeric(df[col], errors='coerce')
                if df[col].dropna().apply(lambda x: float(x).is_integer()).all():
                    df[col] = df[col].astype('Int64')
        except (ValueError, TypeError):
            continue
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl', mode='w') as writer:
        df.to_excel(writer, index=False)
        sheet = writer.sheets['Sheet1']
        for idx, col in enumerate(df.columns):
            longest = max(df[col].astype(str).apply(len).max(), len(str(col)))
            sheet.column_dimensions[chr(65 + idx)].width = min(longest + 2, 50)
    return out.getvalue()


def _xlsxwriter(sheet, columns, rows, index, widths):
    import pandas as pd
    df = pd.DataFrame([[decode(value) for value in row] for row in rows])
    df.columns = columns
    out = io.BytesIO()
    writer = pd.ExcelWriter(out, engine='xlsxwriter')
    df.to_excel(writer, sheet_name=sheet, index=index)
    worksheet = writer.sheets[sheet]
    for i, width in enumerate(widths):
        worksheet.set_column(i, i, width)
    writer.close()
    return out.getvalue()


class _Unseekable:
    """A write-only stream like Django's HttpResponse: zipfile writes data descriptors (flag 0x08)."""

    def __init__(self):
        self.parts = []

    def write(self, data):
        self.parts.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def getvalue(self):
        return b''.join(self.parts)


def _openpyxl_rows(sheets, stream=False):
    import openpyxl
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets:
        sheet = book.create_sheet(title=title)
        for row in rows:
            sheet.append(row)
    out = _Unseekable() if stream else io.BytesIO()
    book.save(out)
    return out.getvalue()


def build(spec):
    """The expected workbook bytes of a '$xlsx' spec."""
    engine = spec['engine']
    if engine == 'excel_utils':
        return _excel_utils(spec['columns'], spec['data'])
    if engine == 'xlsxwriter':
        return _xlsxwriter(spec['sheet'], spec['columns'], spec['rows'], spec['index'], spec['widths'])
    if engine == 'openpyxl_rows':
        return _openpyxl_rows(spec['sheets'], spec.get('stream', False))
    raise ValueError(engine)


def parts(data):
    """[(name, compress_type, flag_bits, bytes)] of a workbook, in zip order."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    return [(info.filename, info.compress_type, info.flag_bits, archive.read(info)) for info in archive.infolist()]


def _times(core):
    stamps = [datetime.strptime(match.group(3).decode(), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
              for match in STAMP.finditer(core)]
    return STAMP.sub(rb'\1-\4', core), stamps


def difference(expected, actual, before, after):
    """None when actual meets the standard against expected, else a short reason."""
    try:
        want, got = parts(expected), parts(actual)
    except (zipfile.BadZipFile, ValueError) as error:
        return f'zip: {error}'
    if [item[:3] for item in want] != [item[:3] for item in got]:
        return f'entries: {[item[:3] for item in want]} != {[item[:3] for item in got]}'
    low = before.astimezone(timezone.utc).replace(microsecond=0)
    for (name, _, _, wanted), (_, _, _, received) in zip(want, got):
        if name == 'docProps/core.xml':
            wanted, _ = _times(wanted)
            received, stamps = _times(received)
            if len(stamps) != 2 or not all(low <= stamp <= after.astimezone(timezone.utc) for stamp in stamps):
                return f'core.xml times {stamps} outside [{low}, {after}]'
        if wanted != received:
            index = next((i for i, (a, b) in enumerate(zip(wanted, received)) if a != b), min(len(wanted), len(received)))
            return f'{name} at byte {index}: {wanted[max(0, index - 60):index + 60]!r} != {received[max(0, index - 60):index + 60]!r}'
    return None


def matches(spec, encoded, before, after):
    """None when the base64 workbook (str) meets the standard for spec."""
    if not isinstance(encoded, str):
        return 'not a base64 string'
    try:
        actual = base64.b64decode(encoded, validate=True)
    except ValueError:
        return 'not base64'
    return difference(build(spec), actual, before, after)


def resolve(expected, actual, before, after):
    """expected with every {'$xlsx': spec} replaced by the actual value at the
    same place when that value meets the standard; the reason otherwise."""
    if isinstance(expected, dict):
        if set(expected) == {'$xlsx'}:
            reason = matches(expected['$xlsx'], actual, before, after)
            return (actual, None) if reason is None else (expected, 'xlsx ' + reason)
        out = {}
        for key, item in expected.items():
            out[key], reason = resolve(item, actual.get(key) if isinstance(actual, dict) else None, before, after)
            if reason:
                return expected, reason
        return out, None
    if isinstance(expected, list):
        out = []
        for i, item in enumerate(expected):
            value, reason = resolve(item, actual[i] if isinstance(actual, list) and i < len(actual) else None, before, after)
            if reason:
                return expected, reason
            out.append(value)
        return out, None
    return expected, None
