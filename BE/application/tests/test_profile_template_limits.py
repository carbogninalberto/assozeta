from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from application.views.profile_views import (
    PROFILE_TEMPLATE_MAX_HTML_DEPTH,
    PROFILE_TEMPLATE_MAX_TEXT_LENGTH,
    ProfileTemplateTextRejected,
    _html_depth,
    _prettify_template_text,
)


class TestProfileTemplateTextLimits(SimpleTestCase):
    def test_helper_matches_prettify_for_normal_document(self):
        text = '<p>uno <strong>due</strong></p><ul><li>tre</li></ul>'

        self.assertEqual(
            _prettify_template_text(text, 'regulation'),
            BeautifulSoup(text, features='html.parser').prettify(),
        )

    def test_length_at_limit_is_accepted(self):
        text = 'a' * PROFILE_TEMPLATE_MAX_TEXT_LENGTH

        self.assertEqual(
            _prettify_template_text(text, 'demand'),
            BeautifulSoup(text, features='html.parser').prettify(),
        )

    def test_length_over_limit_is_rejected(self):
        with self.assertRaises(ProfileTemplateTextRejected) as context:
            _prettify_template_text('a' * (PROFILE_TEMPLATE_MAX_TEXT_LENGTH + 1), 'demand')

        self.assertEqual(context.exception.field, 'demand')
        self.assertEqual(
            context.exception.message,
            f'Il testo è troppo lungo (massimo {PROFILE_TEMPLATE_MAX_TEXT_LENGTH} caratteri).',
        )

    def test_depth_at_limit_is_accepted(self):
        text = '<b>' * PROFILE_TEMPLATE_MAX_HTML_DEPTH + 'x'

        self.assertEqual(
            _prettify_template_text(text, 'regulation'),
            BeautifulSoup(text, features='html.parser').prettify(),
        )

    def test_depth_over_limit_is_rejected(self):
        text = '<b>' * (PROFILE_TEMPLATE_MAX_HTML_DEPTH + 1)

        with self.assertRaises(ProfileTemplateTextRejected) as context:
            _prettify_template_text(text, 'additional_sections')

        self.assertEqual(context.exception.field, 'additional_sections')
        self.assertEqual(
            context.exception.message,
            f'Il testo contiene troppi livelli annidati (massimo {PROFILE_TEMPLATE_MAX_HTML_DEPTH}).',
        )

    def test_html_depth_of_nested_and_sibling_tags(self):
        soup = BeautifulSoup('<p><b>x</b></p><i>y</i>', features='html.parser')

        self.assertEqual(_html_depth(soup), 2)

    def test_html_depth_of_empty_document(self):
        soup = BeautifulSoup('', features='html.parser')

        self.assertEqual(_html_depth(soup), 0)

    def test_non_string_value_matches_beautifulsoup(self):
        try:
            expected = BeautifulSoup(5, features='html.parser').prettify()
        except Exception as error:
            with self.assertRaises(type(error)):
                _prettify_template_text(5, 'regulation')
        else:
            self.assertEqual(_prettify_template_text(5, 'regulation'), expected)
