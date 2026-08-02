import logging
from functools import partial
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A3, A2
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from application.mcp_server.exporters import format_export_data

logger = logging.getLogger(__name__)


def _header_footer(canvas, doc, title='', footer_text=''):
    """Draw header and footer on every page."""
    canvas.saveState()
    page_width, page_height = doc.pagesize
    x = page_width / 2.0
    # Header
    canvas.setFont('Helvetica-Bold', 9)
    canvas.drawCentredString(x, page_height - 1 * cm, title)
    # Footer
    canvas.setFont('Helvetica', 9)
    if footer_text:
        canvas.drawCentredString(x, 1 * cm, "%s - Pagina %d" % (footer_text, doc.page))
    else:
        canvas.drawCentredString(x, 1 * cm, "Pagina %d" % doc.page)
    canvas.restoreState()


class PDFExporter:
    """Export data as PDF using reportlab, following printing.py patterns."""

    def export(
        self,
        data: list[dict],
        filename: str,
        title: str = '',
        column_labels: dict = None,
        header_text: str = '',
        footer_text: str = '',
        text_before: str = '',
        text_after: str = '',
    ) -> tuple[bytes, str, str]:
        if not data:
            return b'', 'application/pdf', 'pdf'

        column_labels = column_labels or {}
        data = format_export_data(data)

        # Build columns and rows
        columns = list(data[0].keys())
        headers = [column_labels.get(col, col) for col in columns]

        rows = []
        for row in data:
            rows.append([str(row.get(col, '')) if row.get(col) is not None else '' for col in columns])

        # Wrap headers in bold paragraphs for text wrapping
        styles = getSampleStyleSheet()
        header_style = styles['Normal'].clone('header')
        header_style.fontName = 'Helvetica-Bold'
        header_style.fontSize = 8
        cell_style = styles['Normal'].clone('cell')
        cell_style.fontSize = 7

        wrapped_headers = [Paragraph(h, header_style) for h in headers]
        wrapped_rows = [
            [Paragraph(str(cell), cell_style) for cell in row]
            for row in rows
        ]

        table_data = [wrapped_headers] + wrapped_rows
        table = Table(table_data, repeatRows=1)

        style = TableStyle([
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
        ])
        table.setStyle(style)

        # Auto-select page size
        num_cols = len(columns)
        landscape = True
        if num_cols <= 5:
            selected_size = A4
        elif num_cols <= 10:
            selected_size = A3
        else:
            selected_size = A2

        orientation = (selected_size[1], selected_size[0]) if landscape else selected_size
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=orientation,
            rightMargin=30,
            leftMargin=30,
            topMargin=40,
            bottomMargin=30,
        )

        # Build flowables list
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

        on_every_page = partial(_header_footer, title=header_text or title, footer_text=footer_text)
        doc.build(flowables, onFirstPage=on_every_page, onLaterPages=on_every_page)

        pdf_bytes = buffer.getvalue()
        buffer.close()

        return pdf_bytes, 'application/pdf', 'pdf'
