import csv
import io
import logging

from application.mcp_server.exporters import format_export_data

logger = logging.getLogger(__name__)

# UTF-8 BOM for Excel compatibility
UTF8_BOM = b'\xef\xbb\xbf'


class CSVExporter:
    """Export data as CSV with UTF-8 BOM for Excel compatibility."""

    def export(
        self,
        data: list[dict],
        filename: str,
        title: str = '',
        column_labels: dict = None,
    ) -> tuple[bytes, str, str]:
        if not data:
            return UTF8_BOM + b'', 'text/csv; charset=utf-8', 'csv'

        column_labels = column_labels or {}
        data = format_export_data(data)

        # Use the keys from the first row as columns
        columns = list(data[0].keys())
        headers = [column_labels.get(col, col) for col in columns]

        output = io.StringIO()
        writer = csv.writer(output, dialect='excel')

        writer.writerow(headers)
        for row in data:
            writer.writerow([row.get(col, '') for col in columns])

        csv_bytes = UTF8_BOM + output.getvalue().encode('utf-8')
        return csv_bytes, 'text/csv; charset=utf-8', 'csv'
