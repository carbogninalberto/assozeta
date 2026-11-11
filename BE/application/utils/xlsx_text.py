"""Cell text for workbooks written with the xlsxwriter engine.

xlsxwriter copies a shared string that starts with ``<r>`` and ends with ``</r>``
into the workbook as markup (its rich string form). User text can look like
that, so such values are written as one rich-string run whose text is escaped:
the cell then shows the original characters.
"""
from xml.sax.saxutils import escape


def _is_rich_looking(value):
    return isinstance(value, str) and value.startswith('<r>') and value.endswith('</r>')


def xlsx_cell_text(value):
    """The value to hand to xlsxwriter so the cell shows `value` as text."""
    if _is_rich_looking(value):
        return '<r><t xml:space="preserve">' + escape(value) + '</t></r>'
    return value


def xlsx_safe_frame(dataframe):
    """A copy of `dataframe` whose string cells and column labels are safe to write."""
    safe = dataframe.copy()
    safe.columns = [xlsx_cell_text(label) for label in safe.columns]
    for position in range(safe.shape[1]):
        column = safe.iloc[:, position]
        if column.dtype == object and any(_is_rich_looking(v) for v in column):
            safe.isetitem(position, column.map(xlsx_cell_text))
    return safe
