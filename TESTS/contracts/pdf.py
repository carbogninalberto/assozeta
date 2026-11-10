"""Opt-in PDF parity for family contracts (pdf-exports.md); no Django imports.

A reportlab document is never byte-reproducible as a whole: the Info
dictionary holds the creation time, the trailer /ID is a digest of that time,
and the page streams are deflated (the compressed bytes depend on the zlib
build). The standard is therefore the decoded document:

- the same header, the same objects in the same order and the same trailer,
  byte for byte, once every stream is decoded (ASCII85, then Flate when the
  object names it) and its /Length is set aside;
- /CreationDate and /ModDate equal each other, are written in UTC (the
  servers' zone; the expected document is built in the contract's own zone)
  and fall within the request window (``before`` truncated to the second,
  ``after``);
- the two halves of the trailer /ID are the same 32 hex digits;
- the cross-reference table lists every object at its real offset and
  ``startxref`` points at the table (checked on each file, since the offsets
  follow the compressed lengths).
- an embedded JPEG is compared by its bytes, its dictionary and the places
  it is used, not by its object name: reportlab names it /FormXob.<md5 of the
  RGB pixels PIL decodes>, which another JPEG decoder does not reproduce bit
  for bit, so the names are numbered by first appearance on both sides.

An authored expectation names the document by the inputs the view hands
reportlab, as ``{'$pdf': spec}``:

- ``{'engine': 'data_table', 'columns': [...], 'rows': [[value, ...], ...],
  'title': str, 'align': [...]|None, 'header': str|None, 'is_a3': bool,
  'landscape': bool, 'additional': [[key, text], ...]|None}``:
  application.utils.printing.generate_pdf_from_data(rows, columns, ...).
- ``{'engine': 'report_table', 'headers': [str, ...], 'rows': [[str, ...],
  ...], 'title': str, 'footer_text': str, 'text_before': str, 'text_after':
  str}``: the saved-report PDFExporter (application.mcp_server.exporters
  .pdf_exporter) from its headers and its rows of str() values on; title is
  ``header_text or title``.
- ``{'engine': 'attendance', 'courses': [{'course': str, 'dates': [str, ...],
  'rows': [{'name': str, 'attendance': [str, ...], 'present_count': int},
  ...]}, ...]}``: application.mcp_server.attendance
  .export_attendance_matrix_pdf for those course matrices.

Values are JSON strings and nulls, or the typed values of
TESTS/contracts/xlsx.py. The expected document is built here with the same
library (reportlab 4.2.5, pure Python) and compared with the response.
"""
import base64
import re
import zlib
from datetime import datetime, timezone
from functools import partial
from io import BytesIO

from TESTS.contracts.xlsx import decode

__all__ = ('build', 'normalize', 'difference', 'matches', 'matches_bytes', 'resolve', 'Malformed')

HEADER = b'%PDF-1.4\n%\x93\x8c\x8b\x9e ReportLab Generated PDF document http://www.reportlab.com\n'
OBJECT = re.compile(rb'(\d+) 0 obj\n')
LENGTH = re.compile(rb'/Length (\d+)(?=[ \n])')
FILTER = re.compile(rb'/Filter \[ ([A-Za-z0-9/ ]+) \]')
DATES = re.compile(rb"/CreationDate \(D:(\d{14}[+-]\d\d'\d\d')\)(.*?)/ModDate \(D:(\d{14}[+-]\d\d'\d\d')\)", re.S)
IMAGE = re.compile(rb'/FormXob\.[0-9a-f]{32}(?![0-9a-f])')
TRAILER = re.compile(
    rb'trailer\n<<\n/ID \n\[<([0-9a-f]{32})><([0-9a-f]{32})>\]\n'
    rb'% ReportLab generated PDF document -- digest \(http://www\.reportlab\.com\)\n\n'
    rb'/Info (\d+) 0 R\n/Root (\d+) 0 R\n/Size (\d+)\n>>\nstartxref\n(\d+)\n%%EOF\n\Z')


class Malformed(ValueError):
    """The bytes are not a document of the shape reportlab writes."""


def _stream(dictionary, data, position):
    """(decoded stream, position after 'endstream\\nendobj\\n') of the stream starting at position."""
    length = LENGTH.search(dictionary)
    filters = FILTER.search(dictionary)
    if not length or not filters:
        raise Malformed('stream without /Length or /Filter')
    end = position + int(length.group(1))
    raw = data[position:end]
    if data[end:end + 17] != b'endstream\nendobj\n':
        raise Malformed('stream /Length does not end at endstream')
    names = filters.group(1).split()
    if names[:1] != [b'/ASCII85Decode'] or not raw.endswith(b'~>'):
        raise Malformed(f'stream filters {names}')
    try:
        decoded = base64.a85decode(b'<~' + raw, adobe=True)
        for name in names[1:]:
            if name == b'/FlateDecode':
                decoded = zlib.decompress(decoded)
            elif name != b'/DCTDecode':
                raise Malformed(f'stream filter {name}')
    except (ValueError, zlib.error) as error:
        raise Malformed(f'stream: {error}') from None
    return decoded, end + 17


def normalize(data):
    """(parts, stamp, digest) of a document: the header, one entry per object
    with its stream decoded and the lengths, dates, offsets and digest set
    aside, and the trailer; stamp is the date text (YYYYMMDDhhmmss+hh'mm').
    Malformed when the file is not self-consistent."""
    if not isinstance(data, (bytes, bytearray)) or not data.startswith(HEADER):
        raise Malformed('header')
    parts = [HEADER]
    position = len(HEADER)
    offsets = []
    stamp = None
    while True:
        match = OBJECT.match(data, position)
        if not match:
            break
        if int(match.group(1)) != len(offsets) + 1:
            raise Malformed(f'object {match.group(1)!r} out of order')
        offsets.append(position)
        start = match.end()
        marker = data.find(b'>>\nstream\n', start)
        end = data.find(b'endobj\n', start)
        if end < 0:
            raise Malformed('object without endobj')
        if 0 <= marker < end:
            dictionary = data[start:marker + 3]
            decoded, position = _stream(dictionary, data, marker + 10)
            body = LENGTH.sub(b'/Length -', dictionary) + b'stream\n' + decoded + b'endstream\n'
        else:
            body = data[start:end]
            position = end + 7
            dates = DATES.search(body)
            if dates:
                if stamp is not None or dates.group(1) != dates.group(3):
                    raise Malformed('dates')
                stamp = dates.group(1).decode()
                body = body[:dates.start(1)] + b'-' + body[dates.end(1):dates.start(3)] + b'-' + body[dates.end(3):]
        parts.append(match.group(0) + body)
    if not offsets or stamp is None:
        raise Malformed('no objects or no dates')
    table = b'xref\n0 %d\n0000000000 65535 f \n' % (len(offsets) + 1) + b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    if data[position:position + len(table)] != table:
        raise Malformed('cross-reference table does not list the objects at their offsets')
    trailer = TRAILER.match(data, position + len(table))
    if not trailer:
        raise Malformed('trailer')
    if trailer.group(1) != trailer.group(2) or int(trailer.group(5)) != len(offsets) + 1 or int(trailer.group(6)) != position:
        raise Malformed('trailer /ID, /Size or startxref')
    parts.append(b'/Info %s /Root %s' % (trailer.group(3), trailer.group(4)))
    names = {}
    for part in parts:
        for name in IMAGE.findall(part):
            names.setdefault(name, b'/FormXob.#%d' % len(names))
    if names:
        parts = [IMAGE.sub(lambda match: names[match.group(0)], part) for part in parts]
    return parts, stamp, trailer.group(1)


# --- the documents Django builds ------------------------------------------------------------------------------


def _header_footer(canvas, doc, title, footer=None):
    from reportlab.lib.units import cm
    canvas.saveState()
    page_width, page_height = doc.pagesize
    x = page_width / 2.0
    canvas.setFont('Helvetica-Bold', 9)
    canvas.drawCentredString(x, page_height - 1 * cm, title)
    canvas.setFont('Helvetica', 9)
    canvas.drawCentredString(x, 1 * cm, ('%s - Pagina %d' % (footer, doc.page)) if footer else 'Pagina %d' % doc.page)
    canvas.restoreState()


def _data_table(columns, rows, title='', scale=3.5, align=None, header=None, is_a3=False, landscape=True, additional=None):
    """application.utils.printing.generate_pdf_from_data, call for call."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A2, A3, A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Table, TableStyle

    rows = [[decode(value) for value in row] for row in rows]
    if align is not None and isinstance(align, list):
        if len(align) != len(columns):
            raise Exception('Align must have the same length as columns')
        for item in align:
            if item not in ['LEFT', 'CENTER', 'RIGHT']:
                raise Exception("Align must be one of ['LEFT', 'CENTER', 'RIGHT']")
    assert all(len(row) == len(columns) for row in rows), 'Row lengths do not match columns.'
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.white),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ])
    if align is not None:
        for index, alignment in enumerate(align):
            style.add('ALIGN', (index, 0), (index, -1), alignment.upper())
    data = [columns] + rows
    table = Table(data)
    table.setStyle(style)
    widths = [max(len(str(word)) * (11 / 12) for word in column) / scale for column in zip(*data)]
    widths = [max(width, 1) * cm for width in widths]
    table._argW = widths
    selected = A2
    for size in (A4, A3, A2):
        if sum(widths) <= (size[1] if landscape else size[0]) - 60:
            selected = size
            break
    if is_a3 and selected == A4:
        selected = A3
    orientation = (selected[1], selected[0]) if landscape else (selected[0], selected[1])
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=orientation, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = [table]
    if header is not None:
        image = re.search(r'src="data:image/jpeg;base64,([^"]+)"', header)
        found = re.search(r'<h1[^>]*>(.*?)</h1>', header)
        title_text = found.group(1) if found else 'Associazione Sportiva'
        found = re.search(r'<h2[^>]*>(.*?)</h2>', header)
        subtitle_text = found.group(1) if found else 'Bilancio'
        found = re.search(r'<h3[^>]*>(.*?)</h3>', header)
        approve_text = found.group(1) if found else 'Approvato il __/__/____'
        styles = getSampleStyleSheet()
        logo = None
        if image:
            try:
                logo = Image(BytesIO(base64.b64decode(image.group(1))))
                logo.drawHeight = 100
                logo.drawWidth = logo.drawHeight * (logo.imageWidth / logo.imageHeight)
            except Exception:
                logo = None
        if logo is None:
            logo = Paragraph('[LOGO]', styles['Normal'])
        title_style = ParagraphStyle('Title', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=20, leading=24,
                                     spaceBefore=30, spaceAfter=10)
        subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'], fontName='Helvetica', fontSize=16, leading=20,
                                        spaceAfter=10)
        approve_style = ParagraphStyle('Subtitle', parent=styles['Normal'], fontName='Helvetica', fontSize=16, leading=20)
        cell = [Paragraph(title_text, title_style), Paragraph(subtitle_text, subtitle_style), Paragraph(approve_text, approve_style)]
        header_table = Table([['', logo, cell, '']], colWidths=[A2[1] / 4, A2[1] / 8, (A2[1] / 8) * 3, A2[1] / 4])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, 0), 20),
            ('ALIGN', (0, 0), (0, 0), 'RIGHT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('ALIGN', (2, 0), (2, 0), 'LEFT'),
            ('ALIGN', (3, 0), (3, 0), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 20),
        ]))
        elements = [header_table, table]
    for _, text in additional or []:
        styles = getSampleStyleSheet()
        elements.append(Paragraph(text, ParagraphStyle('Subtitle', parent=styles['Normal'], fontName='Helvetica',
                                                       alignment=TA_CENTER, fontSize=11, leading=11, spaceBefore=10)))
    page = partial(_header_footer, title=title)
    doc.build(elements, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


def _report_table(headers, rows, title='', footer_text='', text_before='', text_after=''):
    """application.mcp_server.exporters.pdf_exporter.PDFExporter.export, call for call."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A2, A3, A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    header_style = styles['Normal'].clone('header')
    header_style.fontName = 'Helvetica-Bold'
    header_style.fontSize = 8
    cell_style = styles['Normal'].clone('cell')
    cell_style.fontSize = 7
    table_data = [[Paragraph(h, header_style) for h in headers]] + [[Paragraph(str(cell), cell_style) for cell in row] for row in rows]
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f0f0')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
    ]))
    selected = A4 if len(headers) <= 5 else A3 if len(headers) <= 10 else A2
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(selected[1], selected[0]), rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=30)
    flowables = []
    body_style = styles['Normal'].clone('body')
    body_style.fontSize = 9
    body_style.leading = 12
    if text_before:
        flowables.append(Paragraph(text_before, body_style))
        flowables.append(Spacer(1, 0.5 * cm))
    flowables.append(table)
    if text_after:
        flowables.append(Spacer(1, 0.5 * cm))
        flowables.append(Paragraph(text_after, body_style))
    page = partial(_header_footer, title=title, footer=footer_text)
    doc.build(flowables, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


def _attendance(courses):
    """application.mcp_server.attendance.export_attendance_matrix_pdf, call for call."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A2, A3, A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    class VerticalText(Flowable):
        def __init__(self, text, font_name='Helvetica-Bold', font_size=7):
            Flowable.__init__(self)
            self.text = text
            self.font_name = font_name
            self.font_size = font_size
            self.width = font_size + 2
            self.height = stringWidth(text, font_name, font_size) + 4

        def wrap(self, avail_width, avail_height):
            return self.width, self.height

        def draw(self):
            c = self.canv
            c.saveState()
            c.rotate(90)
            c.setFont(self.font_name, self.font_size)
            c.drawString(2, -self.font_size / 3.0, self.text)
            c.restoreState()

    styles = getSampleStyleSheet()

    def course_table(matrix):
        header_style = styles['Normal'].clone('att_header')
        header_style.fontName = 'Helvetica-Bold'
        header_style.fontSize = 7
        header_style.alignment = 1
        cell_style = styles['Normal'].clone('att_cell')
        cell_style.fontSize = 7
        cell_style.alignment = 1
        name_style = styles['Normal'].clone('att_name')
        name_style.fontSize = 7
        name_style.alignment = 0
        dates = matrix['dates']
        data = [[Paragraph('#', header_style), Paragraph('Nome e Cognome', header_style)] + [VerticalText(d) for d in dates]
                + [Paragraph('Presenze', header_style)]]
        for index, row in enumerate(matrix['rows'], start=1):
            data.append([Paragraph(str(index), cell_style), Paragraph(row['name'], name_style)]
                        + [Paragraph(att, cell_style) for att in row['attendance']]
                        + [Paragraph(str(row.get('present_count', 0)), cell_style)])
        table = Table(data, colWidths=[1.0 * cm, 5.5 * cm] + [1.0 * cm] * len(dates) + [1.8 * cm], repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f0f0')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 1), (1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 7),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 14),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return table

    columns = max(len(c['dates']) for c in courses) + 3
    base = A4 if columns <= 15 else A3 if columns <= 25 else A2
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(base[1], base[0]), rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=30)
    flowables = []
    title_style = styles['Heading2'].clone('course_title')
    title_style.fontSize = 12
    title_style.spaceAfter = 8
    for index, matrix in enumerate(courses):
        if len(courses) > 1:
            flowables.append(Paragraph(matrix.get('course', 'Presenze'), title_style))
            flowables.append(Spacer(1, 0.3 * cm))
        flowables.append(course_table(matrix))
        if index < len(courses) - 1:
            flowables.append(PageBreak())
    page = partial(_header_footer, title='Presenze')
    doc.build(flowables, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


ENGINES = {'data_table': _data_table, 'report_table': _report_table, 'attendance': _attendance}


def build(spec):
    """The expected document bytes of a '$pdf' spec."""
    arguments = dict(spec)
    return ENGINES[arguments.pop('engine')](**arguments)


def difference(expected, actual, before, after):
    """None when actual meets the standard against expected, else a short reason."""
    try:
        want, _, _ = normalize(expected)
    except Malformed as error:
        return f'expected document: {error}'
    try:
        got, stamp, _ = normalize(actual)
    except Malformed as error:
        return f'document: {error}'
    if not stamp.endswith("+00'00'"):
        return f'dates {stamp} not in UTC'
    moment = datetime.strptime(stamp[:14], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    low = before.astimezone(timezone.utc).replace(microsecond=0)
    if not low <= moment <= after.astimezone(timezone.utc):
        return f'dates {moment} outside [{low}, {after}]'
    if len(want) != len(got):
        return f'{len(got) - 2} objects, expected {len(want) - 2}'
    for number, (wanted, received) in enumerate(zip(want, got)):
        if wanted != received:
            index = next((i for i, (a, b) in enumerate(zip(wanted, received)) if a != b), min(len(wanted), len(received)))
            return f'part {number} at byte {index}: {wanted[max(0, index - 80):index + 80]!r} != {received[max(0, index - 80):index + 80]!r}'
    return None


def matches_bytes(spec, actual, before, after):
    """None when the document bytes meet the standard for spec."""
    return difference(build(spec), actual, before, after)


def matches(spec, encoded, before, after):
    """None when the base64 document (str) meets the standard for spec."""
    if not isinstance(encoded, str):
        return 'not a base64 string'
    try:
        actual = base64.b64decode(encoded, validate=True)
    except ValueError:
        return 'not base64'
    return matches_bytes(spec, actual, before, after)


def resolve(expected, actual, before, after):
    """expected with every {'$pdf': spec} replaced by the actual value at the
    same place when that value meets the standard; the reason otherwise."""
    if isinstance(expected, dict):
        if set(expected) == {'$pdf'}:
            reason = matches(expected['$pdf'], actual, before, after)
            return (actual, None) if reason is None else (expected, 'pdf ' + reason)
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
