import base64
import logging
import tempfile
from typing import List, Union, Any

import pandas as pd

logger = logging.getLogger(__name__)


def get_excel_base64(*data: List[Any], columns: List[str]) -> Union[bytes, None]:
    """
    Creates an Excel file with proper number formatting and returns it as base64.
    Numbers are properly formatted as numeric cells in Excel.

    Args:
        *data: Variable number of column data lists
        columns: List of column names

    Returns:
        bytes: Base64 encoded Excel file or None if error occurs
    """
    try:
        # Create DataFrame from the data
        df = pd.DataFrame(list(zip(*data)), columns=columns)

        # Convert numeric strings to float/int where appropriate
        for col in df.columns:
            # Try to convert to numeric, but only if the entire column can be converted
            try:
                # First check if all non-empty values in the column are numeric
                numeric_mask = pd.to_numeric(df[col].dropna(), errors='coerce').notna()
                if numeric_mask.all():
                    # If they are all numeric, convert the column
                    df[col] = pd.to_numeric(df[col], errors='coerce')

                    # If all numbers are integers, convert to int
                    if df[col].dropna().apply(lambda x: float(x).is_integer()).all():
                        df[col] = df[col].astype('Int64')  # Using Int64 to handle NaN values
            except (ValueError, TypeError):
                continue

        # Create Excel writer with number format
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as temp_excel:
            logger.debug(f"Writing excel to {temp_excel.name}")

            # Create Excel writer with the engine specification
            with pd.ExcelWriter(
                    temp_excel.name,
                    engine='openpyxl',
                    mode='w'
            ) as writer:
                # Write DataFrame to Excel
                df.to_excel(writer, index=False)

                # Get the worksheet
                worksheet = writer.sheets['Sheet1']

                # Set column widths based on content
                for idx, col in enumerate(df.columns):
                    max_length = max(
                        df[col].astype(str).apply(len).max(),
                        len(str(col))
                    )
                    worksheet.column_dimensions[chr(65 + idx)].width = min(max_length + 2, 50)

            # Read the file and encode to base64
            temp_excel.seek(0)
            output = base64.b64encode(temp_excel.read())
            return output

    except Exception as e:
        logger.error(f"Error while writing to Excel file: {e}")
        return None
