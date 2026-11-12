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

- ``{'engine': 'report_sheets', 'sheets': [{'title': name, 'columns': [label,
  ...], 'rows': [[value, ...], ...]}, ...]}``: the saved-report XLSXExporter
  (application.mcp_server.exporters.xlsx_exporter) after format_export_data:
  one DataFrame per sheet with its columns renamed to the labels, the numeric
  coercion, ``to_excel(index=False, sheet_name=title)`` through openpyxl and
  the column widths;
- ``{'engine': 'attendance', 'mode': 'single'|'flat'|'sheets', 'courses':
  [{'course': str, 'dates': [...], 'rows': [{'name': str, 'attendance': [...],
  'present_count': int}, ...]}, ...]}``: application.mcp_server.attendance
  .export_attendance_matrix_xlsx (one course, every course in one sheet, one
  sheet per course).

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


def _rich_run(value):
    """xlsxwriter writes text shaped like <r>...</r> as markup; the exports hand it over as one escaped run."""
    if isinstance(value, str) and value.startswith('<r>') and value.endswith('</r>'):
        return '<r><t xml:space="preserve">' + value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') + '</t></r>'
    return value


def _xlsxwriter(sheet, columns, rows, index, widths):
    import pandas as pd
    df = pd.DataFrame([[decode(value) for value in row] for row in rows])
    df.columns = columns
    df.columns = [_rich_run(label) for label in df.columns]
    for position in range(df.shape[1]):
        if df.iloc[:, position].dtype == object:
            df.isetitem(position, df.iloc[:, position].map(_rich_run))
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


def _report_sheets(sheets):
    """XLSXExporter.export / export_multi_sheet from the formatted rows on, call for call."""
    import pandas as pd
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl', mode='w') as writer:
        for sheet in sheets:
            labels = [decode(label) for label in sheet['columns']]  # a label may be a typed value
            df = pd.DataFrame([[decode(value) for value in row] for row in sheet['rows']], columns=list(range(len(labels))))
            df = df.rename(columns=dict(enumerate(labels)))
            for col in df.columns:
                try:
                    numeric_mask = pd.to_numeric(df[col].dropna(), errors='coerce').notna()
                    if numeric_mask.all() and not df[col].dropna().empty:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                        if df[col].dropna().apply(lambda x: float(x).is_integer()).all():
                            df[col] = df[col].astype('Int64')
                except (ValueError, TypeError):
                    continue
            df.to_excel(writer, index=False, sheet_name=sheet['title'])
            worksheet = writer.sheets[sheet['title']]
            for idx, col in enumerate(df.columns):
                max_length = max(df[col].astype(str).apply(len).max(), len(str(col)))
                col_letter = chr(65 + idx) if idx < 26 else chr(64 + idx // 26) + chr(65 + idx % 26)
                worksheet.column_dimensions[col_letter].width = min(max_length + 2, 50)
    return out.getvalue()


def _attendance_letter(idx):
    result = ''
    while idx >= 0:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
    return result


def _attendance_width(ws, df, dates):
    from openpyxl.styles import Alignment
    ws.column_dimensions['A'].width = 6
    name_width = max(df['Nome e Cognome'].astype(str).apply(len).max(), len('Nome e Cognome'))
    ws.column_dimensions['B'].width = min(name_width + 2, 40)
    vertical = Alignment(textRotation=90, horizontal='center', vertical='center')
    for idx in range(len(dates)):
        col_letter = _attendance_letter(idx + 2)
        ws.column_dimensions[col_letter].width = 5
        ws[f'{col_letter}1'].alignment = vertical
    ws.column_dimensions[_attendance_letter(len(dates) + 2)].width = 10


def _attendance_frame(matrix):
    import pandas as pd
    data = []
    for idx, row in enumerate(matrix['rows'], start=1):
        entry = {'#': idx, 'Nome e Cognome': row['name']}
        for i, date_label in enumerate(matrix['dates']):
            entry[date_label] = row['attendance'][i] if i < len(row['attendance']) else ''
        entry['Presenze'] = row.get('present_count', 0)
        data.append(entry)
    return pd.DataFrame(data)


def _attendance(mode, courses):
    """application.mcp_server.attendance.export_attendance_matrix_xlsx, call for call."""
    import pandas as pd
    from openpyxl.styles import Alignment
    out = io.BytesIO()
    if mode == 'single':
        matrix, = courses
        df = _attendance_frame(matrix)
        sheet_name = matrix.get('course', 'Presenze')[:31]
        with pd.ExcelWriter(out, engine='openpyxl', mode='w') as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name)
            _attendance_width(writer.sheets[sheet_name], df, matrix['dates'])
    elif mode == 'flat':
        ordered, seen = [], set()
        for matrix in courses:
            for d in matrix['dates']:
                if d not in seen:
                    ordered.append(d)
                    seen.add(d)
        data = []
        for matrix in courses:
            course_dates = matrix['dates']
            for idx, row in enumerate(matrix['rows'], start=1):
                entry = {'#': idx, 'Corso': matrix['course'], 'Nome e Cognome': row['name']}
                for d in ordered:
                    if d in course_dates:
                        d_idx = course_dates.index(d)
                        entry[d] = row['attendance'][d_idx] if d_idx < len(row['attendance']) else ''
                    else:
                        entry[d] = ''
                entry['Presenze'] = row.get('present_count', 0)
                data.append(entry)
        df = pd.DataFrame(data)
        with pd.ExcelWriter(out, engine='openpyxl', mode='w') as writer:
            df.to_excel(writer, index=False, sheet_name='Presenze')
            ws = writer.sheets['Presenze']
            ws.column_dimensions['A'].width = 6
            corso_width = max(df['Corso'].astype(str).apply(len).max(), len('Corso'))
            ws.column_dimensions['B'].width = min(corso_width + 2, 40)
            name_width = max(df['Nome e Cognome'].astype(str).apply(len).max(), len('Nome e Cognome'))
            ws.column_dimensions['C'].width = min(name_width + 2, 40)
            vertical = Alignment(textRotation=90, horizontal='center', vertical='center')
            for idx in range(len(ordered)):
                col_letter = _attendance_letter(idx + 3)
                ws.column_dimensions[col_letter].width = 5
                ws[f'{col_letter}1'].alignment = vertical
            ws.column_dimensions[_attendance_letter(len(ordered) + 3)].width = 10
    else:
        used_names = {}
        with pd.ExcelWriter(out, engine='openpyxl', mode='w') as writer:
            for matrix in courses:
                df = _attendance_frame(matrix)
                sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]]', '-', matrix['course'] or 'Foglio')
                sanitized = re.sub(r'-+', '-', sanitized).strip().strip('-')
                sheet_name = (sanitized or 'Foglio')[:31]
                if sheet_name in used_names:
                    used_names[sheet_name] += 1
                    suffix = f' ({used_names[sheet_name]})'
                    sheet_name = sheet_name[:31 - len(suffix)] + suffix
                else:
                    used_names[sheet_name] = 1
                df.to_excel(writer, index=False, sheet_name=sheet_name)
                _attendance_width(writer.sheets[sheet_name], df, matrix['dates'])
    return out.getvalue()


def build(spec):
    """The expected workbook bytes of a '$xlsx' spec."""
    engine = spec['engine']
    if engine == 'report_sheets':
        return _report_sheets(spec['sheets'])
    if engine == 'attendance':
        return _attendance(spec['mode'], spec['courses'])
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
