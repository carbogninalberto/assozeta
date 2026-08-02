import io
import logging
import tempfile

import pandas as pd

from application.mcp_server.exporters import format_export_data

logger = logging.getLogger(__name__)


class XLSXExporter:
    """Export data as XLSX using pandas + openpyxl, following excel_utils.py patterns."""

    def export(
        self,
        data: list[dict],
        filename: str,
        title: str = '',
        column_labels: dict = None,
    ) -> tuple[bytes, str, str]:
        if not data:
            return b'', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'

        column_labels = column_labels or {}
        data = format_export_data(data)

        # Build DataFrame
        columns = list(data[0].keys())
        df = pd.DataFrame(data, columns=columns)

        # Rename columns using labels
        rename_map = {col: column_labels.get(col, col) for col in columns}
        df = df.rename(columns=rename_map)

        # Convert numeric strings to numbers
        for col in df.columns:
            try:
                numeric_mask = pd.to_numeric(df[col].dropna(), errors='coerce').notna()
                if numeric_mask.all() and not df[col].dropna().empty:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                    if df[col].dropna().apply(lambda x: float(x).is_integer()).all():
                        df[col] = df[col].astype('Int64')
            except (ValueError, TypeError):
                continue

        with tempfile.NamedTemporaryFile(suffix='.xlsx') as tmp:
            with pd.ExcelWriter(tmp.name, engine='openpyxl', mode='w') as writer:
                df.to_excel(writer, index=False, sheet_name=title[:31] if title else 'Sheet1')
                worksheet = writer.sheets[title[:31] if title else 'Sheet1']

                # Auto-width columns
                for idx, col in enumerate(df.columns):
                    max_length = max(
                        df[col].astype(str).apply(len).max(),
                        len(str(col))
                    )
                    col_letter = chr(65 + idx) if idx < 26 else chr(64 + idx // 26) + chr(65 + idx % 26)
                    worksheet.column_dimensions[col_letter].width = min(max_length + 2, 50)

            tmp.seek(0)
            xlsx_bytes = tmp.read()

        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return xlsx_bytes, content_type, 'xlsx'

    def export_multi_sheet(
        self,
        sheets: list[dict],
        filename: str,
    ) -> tuple[bytes, str, str]:
        """Export multiple datasets as separate sheets in one XLSX file.

        Args:
            sheets: List of dicts with keys: data, title, column_labels.
            filename: Base filename (without extension).

        Returns:
            (xlsx_bytes, content_type, extension)
        """
        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        if not sheets:
            return b'', content_type, 'xlsx'

        buf = io.BytesIO()
        used_names = {}

        with pd.ExcelWriter(buf, engine='openpyxl', mode='w') as writer:
            for sheet in sheets:
                data = sheet.get('data', [])
                if not data:
                    continue

                column_labels = sheet.get('column_labels') or {}
                raw_title = sheet.get('title', 'Sheet')

                # Deduplicate sheet names (Excel limit: 31 chars)
                sheet_name = raw_title[:31]
                if sheet_name in used_names:
                    used_names[sheet_name] += 1
                    suffix = f' ({used_names[sheet_name]})'
                    sheet_name = raw_title[:31 - len(suffix)] + suffix
                else:
                    used_names[sheet_name] = 1

                data = format_export_data(data)
                columns = list(data[0].keys())
                df = pd.DataFrame(data, columns=columns)

                rename_map = {col: column_labels.get(col, col) for col in columns}
                df = df.rename(columns=rename_map)

                # Convert numeric strings to numbers
                for col in df.columns:
                    try:
                        numeric_mask = pd.to_numeric(df[col].dropna(), errors='coerce').notna()
                        if numeric_mask.all() and not df[col].dropna().empty:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                            if df[col].dropna().apply(lambda x: float(x).is_integer()).all():
                                df[col] = df[col].astype('Int64')
                    except (ValueError, TypeError):
                        continue

                df.to_excel(writer, index=False, sheet_name=sheet_name)
                worksheet = writer.sheets[sheet_name]

                # Auto-width columns
                for idx, col in enumerate(df.columns):
                    max_length = max(
                        df[col].astype(str).apply(len).max(),
                        len(str(col))
                    )
                    col_letter = chr(65 + idx) if idx < 26 else chr(64 + idx // 26) + chr(65 + idx % 26)
                    worksheet.column_dimensions[col_letter].width = min(max_length + 2, 50)

        xlsx_bytes = buf.getvalue()
        buf.close()
        return xlsx_bytes, content_type, 'xlsx'
