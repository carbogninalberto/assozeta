from typing import Protocol


class BaseExporter(Protocol):
    """Protocol for data exporters."""

    def export(
        self,
        data: list[dict],
        filename: str,
        title: str = '',
        column_labels: dict = None,
    ) -> tuple[bytes, str, str]:
        """Export data to a specific format.

        Args:
            data: List of row dicts (from QueryBuilder).
            filename: Base filename (without extension).
            title: Optional title for the export.
            column_labels: Optional mapping of field_name -> display label.

        Returns:
            Tuple of (file_bytes, content_type, extension).
        """
        ...
