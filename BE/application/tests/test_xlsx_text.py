"""Cell text that looks like xlsxwriter's rich string markup is written as text."""
import io
import zipfile

import pandas
from django.test import SimpleTestCase
from openpyxl import load_workbook

from application.utils.xlsx_text import xlsx_cell_text, xlsx_safe_frame

LOOKS_RICH = '<r><t>bold</t></r>'
TRICKY = '<r>a & b < c "d"</r>'


def write(frame, **kwargs):
    buffer = io.BytesIO()
    writer = pandas.ExcelWriter(buffer, engine='xlsxwriter')
    frame.to_excel(writer, sheet_name='S', index=False, **kwargs)
    writer.close()
    return buffer.getvalue()


class XlsxTextTests(SimpleTestCase):
    def test_plain_text_is_unchanged(self):
        for value in ('abc', '<r>open only', 'close only</r>', '', None, 5):
            self.assertIs(xlsx_cell_text(value), value)

    def test_markup_looking_text_is_stored_as_text(self):
        frame = pandas.DataFrame({LOOKS_RICH: [LOOKS_RICH, TRICKY, 'x', None]})
        data = write(xlsx_safe_frame(frame))
        sheet = load_workbook(io.BytesIO(data))['S']
        self.assertEqual(sheet['A1'].value, LOOKS_RICH)
        self.assertEqual(sheet['A2'].value, LOOKS_RICH)
        self.assertEqual(sheet['A3'].value, TRICKY)
        self.assertEqual(sheet['A4'].value, 'x')
        shared = zipfile.ZipFile(io.BytesIO(data)).read('xl/sharedStrings.xml').decode()
        self.assertNotIn('<t>bold</t>', shared)
        self.assertIn('&lt;t&gt;bold&lt;/t&gt;', shared)

    def test_without_the_helper_the_library_writes_the_text_as_markup(self):
        data = write(pandas.DataFrame({'a': [LOOKS_RICH]}))
        shared = zipfile.ZipFile(io.BytesIO(data)).read('xl/sharedStrings.xml').decode()
        self.assertIn('<si><r><t>bold</t></r></si>', shared)

    def test_input_frame_is_not_modified(self):
        frame = pandas.DataFrame({'a': [LOOKS_RICH]})
        xlsx_safe_frame(frame)
        self.assertEqual(frame['a'][0], LOOKS_RICH)
