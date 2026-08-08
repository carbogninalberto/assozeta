import unittest
import application.templatetags.format as tags


class TestTemplateTags(unittest.TestCase):

    def test_format_template_tag(self):
        formatted_string = tags.format('100.00', '€ {}')
        assert formatted_string == '€ 100,00'
