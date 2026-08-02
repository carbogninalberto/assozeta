"""
Attendance matrix builder.

Builds a pivot table of attendance data: associates (rows) x dates (columns),
with "P" for present and empty for absent.
Supports single-course and all-courses mode.
Optionally exports the matrix as XLSX or PDF.
"""
import io
import logging
import re
from datetime import datetime
from functools import partial

from django.apps import apps
from openpyxl.styles import Alignment

logger = logging.getLogger(__name__)


def _parse_date(value: str):
    """Parse a date string (YYYY-MM-DD) to a date object."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _resolve_courses(sport_association_id, course_id=None, course_name=None):
    """Resolve which course(s) to process.

    Returns:
        (list[Course], error_dict | None)
    """
    Course = apps.get_model('application', 'Course')

    if course_id:
        try:
            course = Course.objects.get(
                pk=course_id,
                sport_association_id=sport_association_id,
            )
            return [course], None
        except Course.DoesNotExist:
            return [], {'error': f'Corso non trovato con ID: {course_id}'}

    if course_name:
        courses = Course.objects.filter(
            sport_association_id=sport_association_id,
            title__icontains=course_name,
            deleted=False,
        )
        if courses.count() == 0:
            return [], {'error': f'Nessun corso trovato con nome: {course_name}'}
        if courses.count() > 1:
            return [], {
                'error': 'Trovati piu\' corsi con quel nome. Specifica meglio.',
                'matches': [
                    {'course_id': str(c.pk), 'title': c.title}
                    for c in courses[:10]
                ],
            }
        return [courses.first()], None

    # No course specified -> all courses with attendance data
    AttendanceRegistry = apps.get_model('application', 'AttendanceRegistry')
    course_ids_with_attendance = (
        AttendanceRegistry.objects.filter(
            course__sport_association_id=sport_association_id,
            course__deleted=False,
        )
        .values_list('course_id', flat=True)
        .distinct()
    )
    courses = list(
        Course.objects.filter(
            pk__in=course_ids_with_attendance,
            deleted=False,
        ).order_by('title')
    )
    if not courses:
        return [], {'error': 'Nessun corso con registro presenze trovato.'}
    return courses, None


def _build_single_course_matrix(course, date_from_obj, date_to_obj):
    """Build the attendance matrix for a single course.

    Returns:
        dict with course, dates, rows, summary — or dict with 'error'.
    """
    CourseSubscription = apps.get_model('application', 'CourseSubscription')
    AttendanceRegistry = apps.get_model('application', 'AttendanceRegistry')
    AttendanceDay = apps.get_model('application', 'AttendanceDay')

    registries = AttendanceRegistry.objects.filter(course=course)
    if not registries.exists():
        return None  # Skip silently in multi-course mode

    days_qs = AttendanceDay.objects.filter(
        attendance_registry__in=registries,
    ).order_by('date')

    if date_from_obj:
        days_qs = days_qs.filter(date__date__gte=date_from_obj)
    if date_to_obj:
        days_qs = days_qs.filter(date__date__lte=date_to_obj)

    days = list(days_qs.values(
        'attendance_day_id', 'date', 'title', 'attendees', 'expected_absences',
    ))

    if not days:
        return None

    # Get enrolled people
    subscriptions = list(
        CourseSubscription.objects.filter(
            course=course,
            deleted=False,
        ).select_related(
            'subscription__associate',
        ).values(
            'course_subscription_id',
            'subscription__associate__first_name',
            'subscription__associate__last_name',
        )
    )

    cs_names = {}
    for sub in subscriptions:
        cs_id = str(sub['course_subscription_id'])
        first = sub.get('subscription__associate__first_name') or ''
        last = sub.get('subscription__associate__last_name') or ''
        cs_names[cs_id] = f'{last} {first}'.strip() or cs_id

    # Build date labels and presence sets
    date_labels = []
    for day in days:
        dt = day['date']
        if hasattr(dt, 'strftime'):
            label = dt.strftime('%d/%m/%Y')
        else:
            label = str(dt)[:10]
        date_labels.append(label)

    day_present_sets = []
    for day in days:
        attendees = day.get('attendees') or []
        present_ids = set()
        for att in attendees:
            if isinstance(att, dict):
                cs_id = str(att.get('course_subscription_id', ''))
            else:
                cs_id = str(att)
            if cs_id:
                present_ids.add(cs_id)
        day_present_sets.append(present_ids)

    sorted_cs_ids = sorted(cs_names.keys(), key=lambda cid: cs_names[cid].lower())

    rows = []
    for cs_id in sorted_cs_ids:
        attendance = []
        present_count = 0
        for present_set in day_present_sets:
            if cs_id in present_set:
                attendance.append('P')
                present_count += 1
            else:
                attendance.append('')
        rows.append({
            'name': cs_names[cs_id],
            'course_subscription_id': cs_id,
            'attendance': attendance,
            'present_count': present_count,
            'total_days': len(days),
        })

    return {
        'course': course.title,
        'course_id': str(course.pk),
        'dates': date_labels,
        'rows': rows,
        'summary': {
            'total_dates': len(days),
            'total_enrolled': len(sorted_cs_ids),
        },
    }


def build_attendance_matrix(
    sport_association_id: str,
    course_id: str = None,
    course_name: str = None,
    date_from: str = None,
    date_to: str = None,
) -> dict:
    """Build attendance matrix for one course, or all courses if none specified.

    Returns:
        Single-course: dict with course, dates, rows, summary.
        Multi-course:  dict with 'multi': True, 'courses': [matrix, ...].
    """
    courses, error = _resolve_courses(sport_association_id, course_id, course_name)
    if error:
        return error

    d_from = _parse_date(date_from)
    d_to = _parse_date(date_to)

    if len(courses) == 1:
        result = _build_single_course_matrix(courses[0], d_from, d_to)
        if result is None:
            return {
                'error': 'Nessuna giornata di presenza trovata per questo corso nel periodo indicato.',
                'course': courses[0].title,
            }
        return result

    # Multi-course mode
    matrices = []
    for course in courses:
        matrix = _build_single_course_matrix(course, d_from, d_to)
        if matrix is not None:
            matrices.append(matrix)

    if not matrices:
        return {'error': 'Nessun dato di presenza trovato per nessun corso nel periodo indicato.'}

    total_enrolled = sum(m['summary']['total_enrolled'] for m in matrices)
    total_dates = sum(m['summary']['total_dates'] for m in matrices)

    return {
        'multi': True,
        'courses': matrices,
        'summary': {
            'total_courses': len(matrices),
            'total_enrolled': total_enrolled,
            'total_dates': total_dates,
        },
    }


def export_attendance_matrix_xlsx(
    matrix: dict, title: str = '', multi_sheet: bool = False,
) -> tuple[bytes, str]:
    """Export an attendance matrix (single or multi-course) to XLSX bytes."""
    import pandas as pd

    if matrix.get('multi'):
        if multi_sheet:
            return _export_multi_sheet_xlsx(matrix, title)
        return _export_multi_course_xlsx(matrix, title)
    return _export_single_course_xlsx(matrix, title)


def _export_single_course_xlsx(matrix: dict, title: str = '') -> tuple[bytes, str]:
    """Export a single-course matrix to XLSX."""
    import pandas as pd

    dates = matrix['dates']
    rows = matrix['rows']
    course_title = matrix.get('course', 'Presenze')

    data = []
    for idx, row in enumerate(rows, start=1):
        entry = {'#': idx, 'Nome e Cognome': row['name']}
        for i, date_label in enumerate(dates):
            entry[date_label] = row['attendance'][i] if i < len(row['attendance']) else ''
        entry['Presenze'] = row.get('present_count', 0)
        data.append(entry)

    df = pd.DataFrame(data)

    buf = io.BytesIO()
    sheet_name = (title or course_title)[:31]
    with pd.ExcelWriter(buf, engine='openpyxl', mode='w') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        _auto_width(writer.sheets[sheet_name], df, dates)

    buf.seek(0)
    return buf.read(), f'Presenze - {course_title}.xlsx'


def _export_multi_course_xlsx(matrix: dict, title: str = '') -> tuple[bytes, str]:
    """Export multi-course matrix to a single XLSX: Corso | Nome | dates... | Presenze."""
    import pandas as pd

    # Collect all unique date labels across all courses, in chronological order
    all_dates_ordered = []
    seen_dates = set()
    for course_matrix in matrix['courses']:
        for d in course_matrix['dates']:
            if d not in seen_dates:
                all_dates_ordered.append(d)
                seen_dates.add(d)

    # Build flat rows with Corso column
    data = []
    for course_matrix in matrix['courses']:
        course_title = course_matrix['course']
        course_dates = course_matrix['dates']

        for idx, row in enumerate(course_matrix['rows'], start=1):
            entry = {
                '#': idx,
                'Corso': course_title,
                'Nome e Cognome': row['name'],
            }
            # Map this course's dates into the global date columns
            for d in all_dates_ordered:
                if d in course_dates:
                    d_idx = course_dates.index(d)
                    entry[d] = row['attendance'][d_idx] if d_idx < len(row['attendance']) else ''
                else:
                    entry[d] = ''
            entry['Presenze'] = row.get('present_count', 0)
            data.append(entry)

    df = pd.DataFrame(data)

    buf = io.BytesIO()
    sheet_name = (title or 'Presenze')[:31]
    with pd.ExcelWriter(buf, engine='openpyxl', mode='w') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets[sheet_name]

        # Column widths
        # A = Progressivo
        ws.column_dimensions['A'].width = 6
        # B = Corso
        corso_width = max(df['Corso'].astype(str).apply(len).max(), len('Corso'))
        ws.column_dimensions['B'].width = min(corso_width + 2, 40)
        # C = Nome e Cognome
        name_width = max(df['Nome e Cognome'].astype(str).apply(len).max(), len('Nome e Cognome'))
        ws.column_dimensions['C'].width = min(name_width + 2, 40)
        # Date columns start at index 3
        vertical = Alignment(textRotation=90, horizontal='center', vertical='center')
        for idx in range(len(all_dates_ordered)):
            col_letter = _col_letter(idx + 3)
            ws.column_dimensions[col_letter].width = 5
            ws[f'{col_letter}1'].alignment = vertical
        # Presenze column
        totals_col = _col_letter(len(all_dates_ordered) + 3)
        ws.column_dimensions[totals_col].width = 10

    buf.seek(0)
    return buf.read(), 'Presenze - tutti i corsi.xlsx'


_UNSAFE_SHEET_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\[\]]')


def _sanitize_sheet_name(name: str) -> str:
    """Sanitize a string for use as an Excel sheet name (max 31 chars)."""
    sanitized = _UNSAFE_SHEET_RE.sub('-', name or 'Foglio')
    sanitized = re.sub(r'-+', '-', sanitized).strip().strip('-')
    return (sanitized or 'Foglio')[:31]


def _export_multi_sheet_xlsx(matrix: dict, title: str = '') -> tuple[bytes, str]:
    """Export multi-course matrix to XLSX with one sheet per course."""
    import pandas as pd

    buf = io.BytesIO()
    used_names: dict[str, int] = {}

    with pd.ExcelWriter(buf, engine='openpyxl', mode='w') as writer:
        for course_matrix in matrix['courses']:
            course_title = course_matrix['course']
            dates = course_matrix['dates']
            rows = course_matrix['rows']

            # Build DataFrame (same layout as single-course)
            data = []
            for idx, row in enumerate(rows, start=1):
                entry = {'#': idx, 'Nome e Cognome': row['name']}
                for i, date_label in enumerate(dates):
                    entry[date_label] = row['attendance'][i] if i < len(row['attendance']) else ''
                entry['Presenze'] = row.get('present_count', 0)
                data.append(entry)

            df = pd.DataFrame(data)

            # Deduplicate sheet names
            sheet_name = _sanitize_sheet_name(course_title)
            if sheet_name in used_names:
                used_names[sheet_name] += 1
                suffix = f' ({used_names[sheet_name]})'
                sheet_name = sheet_name[:31 - len(suffix)] + suffix
            else:
                used_names[sheet_name] = 1

            df.to_excel(writer, index=False, sheet_name=sheet_name)
            _auto_width(writer.sheets[sheet_name], df, dates)

    buf.seek(0)
    return buf.read(), 'Presenze - tutti i corsi.xlsx'


def _auto_width(ws, df, dates):
    """Set column widths for a single-course sheet."""
    # A = Progressivo
    ws.column_dimensions['A'].width = 6
    # B = Nome e Cognome
    name_width = max(
        df['Nome e Cognome'].astype(str).apply(len).max(),
        len('Nome e Cognome'),
    )
    ws.column_dimensions['B'].width = min(name_width + 2, 40)
    # Date columns start at C (index 2)
    vertical = Alignment(textRotation=90, horizontal='center', vertical='center')
    for idx in range(len(dates)):
        col_letter = _col_letter(idx + 2)
        ws.column_dimensions[col_letter].width = 5
        ws[f'{col_letter}1'].alignment = vertical
    # Presenze column
    totals_col = _col_letter(len(dates) + 2)
    ws.column_dimensions[totals_col].width = 10


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel column letter(s)."""
    result = ''
    while idx >= 0:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
    return result


# ────────────────────────────────────────────────────────────────────
# PDF export
# ────────────────────────────────────────────────────────────────────

def _attendance_header_footer(canvas, doc, title=''):
    """Draw header (course title) and footer (page number) on every page."""
    canvas.saveState()
    page_width, page_height = doc.pagesize
    x = page_width / 2.0
    canvas.setFont('Helvetica-Bold', 9)
    canvas.drawCentredString(x, page_height - 1 * _CM, title)
    canvas.setFont('Helvetica', 9)
    canvas.drawCentredString(x, 1 * _CM, "Pagina %d" % doc.page)
    canvas.restoreState()


# Lazy imports for reportlab (only loaded when PDF is actually requested)
_CM = None
_REPORTLAB_LOADED = False


def _ensure_reportlab():
    global _CM, _REPORTLAB_LOADED
    if not _REPORTLAB_LOADED:
        from reportlab.lib.units import cm
        _CM = cm
        _REPORTLAB_LOADED = True


def _build_pdf_table_for_course(matrix_course: dict, styles):
    """Build a reportlab Table for a single course matrix."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph, Flowable
    from reportlab.pdfbase.pdfmetrics import stringWidth

    class VerticalText(Flowable):
        """Draws text rotated 90 degrees (bottom-to-top)."""

        def __init__(self, text, font_name='Helvetica-Bold', font_size=7):
            Flowable.__init__(self)
            self.text = text
            self.font_name = font_name
            self.font_size = font_size
            tw = stringWidth(text, font_name, font_size)
            self.width = font_size + 2
            self.height = tw + 4

        def wrap(self, avail_width, avail_height):
            return self.width, self.height

        def draw(self):
            c = self.canv
            c.saveState()
            c.rotate(90)
            c.setFont(self.font_name, self.font_size)
            c.drawString(2, -self.font_size / 3.0, self.text)
            c.restoreState()

    dates = matrix_course['dates']
    rows_data = matrix_course['rows']

    header_style = styles['Normal'].clone('att_header')
    header_style.fontName = 'Helvetica-Bold'
    header_style.fontSize = 7
    header_style.alignment = 1  # CENTER

    cell_style = styles['Normal'].clone('att_cell')
    cell_style.fontSize = 7
    cell_style.alignment = 1

    name_style = styles['Normal'].clone('att_name')
    name_style.fontSize = 7
    name_style.alignment = 0  # LEFT

    # Header row: # | Nome e Cognome | date1 | date2 | ... | Presenze
    header_row = [
        Paragraph('#', header_style),
        Paragraph('Nome e Cognome', header_style),
    ]
    for d in dates:
        header_row.append(VerticalText(d))
    header_row.append(Paragraph('Presenze', header_style))

    table_data = [header_row]

    for idx, row in enumerate(rows_data, start=1):
        table_row = [
            Paragraph(str(idx), cell_style),
            Paragraph(row['name'], name_style),
        ]
        for att in row['attendance']:
            table_row.append(Paragraph(att, cell_style))
        table_row.append(Paragraph(str(row.get('present_count', 0)), cell_style))
        table_data.append(table_row)

    # Column widths: narrow for #, wider for name, narrow for dates, medium for totals
    num_col = 1.0 * _CM
    name_col = 5.5 * _CM
    date_col = 1.0 * _CM
    total_col = 1.8 * _CM
    col_widths = [num_col, name_col] + [date_col] * len(dates) + [total_col]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
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


def export_attendance_matrix_pdf(matrix: dict, title: str = '') -> tuple[bytes, str]:
    """Export attendance matrix as a single PDF (one page per course for multi-course)."""
    from reportlab.lib.pagesizes import A4, A3, A2
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

    _ensure_reportlab()
    styles = getSampleStyleSheet()

    if matrix.get('multi'):
        courses = matrix['courses']
    else:
        courses = [matrix]

    # Determine page size from the max number of columns across all courses
    max_cols = max(len(c['dates']) for c in courses) + 3  # +3 for #, name, presenze
    if max_cols <= 15:
        base_size = A4
    elif max_cols <= 25:
        base_size = A3
    else:
        base_size = A2
    page_size = (base_size[1], base_size[0])  # landscape

    buffer = io.BytesIO()

    pdf_title = title or 'Presenze'
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=30,
        leftMargin=30,
        topMargin=40,
        bottomMargin=30,
    )

    flowables = []
    title_style = styles['Heading2'].clone('course_title')
    title_style.fontSize = 12
    title_style.spaceAfter = 8

    for i, course_matrix in enumerate(courses):
        course_title = course_matrix.get('course', 'Presenze')

        if len(courses) > 1:
            flowables.append(Paragraph(course_title, title_style))
            flowables.append(Spacer(1, 0.3 * _CM))

        table = _build_pdf_table_for_course(course_matrix, styles)
        flowables.append(table)

        # Page break between courses (not after the last one)
        if i < len(courses) - 1:
            flowables.append(PageBreak())

    on_page = partial(
        _attendance_header_footer,
        title=pdf_title,
    )
    doc.build(flowables, onFirstPage=on_page, onLaterPages=on_page)

    pdf_bytes = buffer.getvalue()
    buffer.close()

    if matrix.get('multi'):
        filename = 'Presenze - tutti i corsi.pdf'
    else:
        course_name = matrix.get('course', 'Presenze')
        filename = f'Presenze - {course_name}.pdf'

    return pdf_bytes, filename
