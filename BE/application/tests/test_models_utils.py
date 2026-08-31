"""
Tests for application.models.utils module.
"""
from django.test import TestCase

from application.models.utils import (
    get_nested_attr,
    filter_mentions,
    extract_values,
    get_default_additional_sections,
    get_default_configuration,
    get_default_enabled_for,
    get_default_stripe_methods,
    DEFAULT_ADDITIONAL_SECTIONS
)


class GetNestedAttrTests(TestCase):
    """Tests for get_nested_attr function."""

    def test_get_nested_attr_simple(self):
        """Test getting a simple attribute."""
        class Obj:
            name = "Test"

        result = get_nested_attr(Obj(), "name")
        self.assertEqual(result, "Test")

    def test_get_nested_attr_nested(self):
        """Test getting a nested attribute."""
        class Inner:
            value = "nested_value"

        class Outer:
            inner = Inner()

        result = get_nested_attr(Outer(), "inner.value")
        self.assertEqual(result, "nested_value")

    def test_get_nested_attr_dict(self):
        """Test getting attribute from dict."""
        obj = {"name": "Test", "nested": {"value": "nested_value"}}
        result = get_nested_attr(obj, "name")
        self.assertEqual(result, "Test")

    def test_get_nested_attr_none_obj(self):
        """Test with None object."""
        result = get_nested_attr(None, "name")
        self.assertEqual(result, "")

    def test_get_nested_attr_empty_path(self):
        """Test with empty attribute path."""
        class Obj:
            name = "Test"

        result = get_nested_attr(Obj(), "")
        self.assertEqual(result, "")

    def test_get_nested_attr_nonexistent(self):
        """Test with non-existent attribute."""
        class Obj:
            name = "Test"

        result = get_nested_attr(Obj(), "nonexistent")
        self.assertEqual(result, "")

    def test_get_nested_attr_deep_nested(self):
        """Test getting deeply nested attribute."""
        class Level3:
            value = "deep_value"

        class Level2:
            level3 = Level3()

        class Level1:
            level2 = Level2()

        result = get_nested_attr(Level1(), "level2.level3.value")
        self.assertEqual(result, "deep_value")

    def test_get_nested_attr_none_in_chain(self):
        """Test with None in the attribute chain."""
        class Outer:
            inner = None

        result = get_nested_attr(Outer(), "inner.value")
        self.assertEqual(result, "")


class FilterMentionsTests(TestCase):
    """Tests for filter_mentions function."""

    def test_filter_mentions_empty_content(self):
        """Test with empty HTML content."""
        result = filter_mentions("")
        self.assertEqual(result, "")

    def test_filter_mentions_none_content(self):
        """Test with None content."""
        result = filter_mentions(None)
        self.assertEqual(result, "")

    def test_filter_mentions_no_context(self):
        """Test with HTML but no context objects."""
        html = '<p>Hello <span class="mention" data-type="mention" key="user.name">@name</span></p>'
        result = filter_mentions(html)
        self.assertIn("Hello", result)

    def test_filter_mentions_simple_replacement(self):
        """Test simple mention replacement."""
        html = '<span class="mention" data-type="mention" key="user.name">@name</span>'
        context = {"user": {"name": "John"}}
        result = filter_mentions(html, context)
        self.assertIn("John", result)

    def test_filter_mentions_object_attribute(self):
        """Test replacement with object attribute."""
        class User:
            name = "Jane"

        html = '<span class="mention" data-type="mention" key="user.name">@name</span>'
        context = {"user": User()}
        result = filter_mentions(html, context)
        self.assertIn("Jane", result)

    def test_filter_mentions_nested_attribute(self):
        """Test replacement with nested attribute."""
        class Profile:
            email = "test@example.com"

        class User:
            profile = Profile()

        html = '<span class="mention" data-type="mention" key="user.profile.email">@email</span>'
        context = {"user": User()}
        result = filter_mentions(html, context)
        self.assertIn("test@example.com", result)

    def test_filter_mentions_multiple_mentions(self):
        """Test multiple mention replacements."""
        html = '''
        <p>Name: <span class="mention" data-type="mention" key="user.first_name">@first</span>
        Last: <span class="mention" data-type="mention" key="user.last_name">@last</span></p>
        '''
        context = {"user": {"first_name": "John", "last_name": "Doe"}}
        result = filter_mentions(html, context)
        self.assertIn("John", result)
        self.assertIn("Doe", result)

    def test_filter_mentions_date_conversion(self):
        """Test date format conversion (YYYY-MM-DD to DD/MM/YYYY)."""
        html = '<span class="mention" data-type="mention" key="user.birth_date">@date</span>'
        context = {"user": {"birth_date": "1990-05-15"}}
        result = filter_mentions(html, context)
        self.assertIn("15/05/1990", result)

    def test_filter_mentions_none_value(self):
        """Test that None values are replaced with empty string."""
        class User:
            name = None

        html = '<span class="mention" data-type="mention" key="user.name">@name</span>'
        context = {"user": User()}
        result = filter_mentions(html, context)
        self.assertNotIn("None", result)

    def test_filter_mentions_missing_key(self):
        """Test span without key attribute."""
        html = '<span class="mention" data-type="mention">@name</span>'
        context = {"user": {"name": "John"}}
        result = filter_mentions(html, context)
        self.assertIsNotNone(result)

    def test_filter_mentions_replaces_legacy_aliases_in_text_nodes(self):
        html = (
            '<p>@nome @cognome — @dataodierna — @listacorsi</p>'
            '<a href="/@nome">Profilo @nome</a>'
            '<script>const token = "@nome";</script>'
        )
        context = {
            'associate': {'first_name': 'Giulia', 'last_name': 'Rossi'},
            'other': {'today': '31/08/2026', 'courses_list': 'Yoga, Pilates'},
        }

        result = filter_mentions(html, context)

        self.assertIn('Giulia Rossi — 31/08/2026 — Yoga, Pilates', result)
        self.assertIn('href="/@nome"', result)
        self.assertIn('Profilo Giulia', result)
        self.assertIn('const token = "@nome"', result)

    def test_filter_mentions_does_not_replace_alias_inside_email_or_unknown_command(self):
        result = filter_mentions(
            '<p>segreteria@nome.it @sconosciuto @nome.</p>',
            {'associate': {'first_name': 'Luca'}},
        )

        self.assertIn('segreteria@nome.it', result)
        self.assertIn('@sconosciuto', result)
        self.assertIn('Luca.', result)


class ExtractValuesTests(TestCase):
    """Tests for extract_values function."""

    def test_extract_values_empty_keys(self):
        """Test with empty keys list."""
        result = extract_values([], {"user": {"name": "John"}})
        self.assertEqual(result, {})

    def test_extract_values_none_keys(self):
        """Test with None keys."""
        result = extract_values(None, {"user": {"name": "John"}})
        self.assertEqual(result, {})

    def test_extract_values_none_context(self):
        """Test with None context."""
        result = extract_values(["user.name"], None)
        self.assertEqual(result, {})

    def test_extract_values_simple_dict(self):
        """Test extracting values from dict context."""
        keys = ["user.name", "user.email"]
        context = {"user": {"name": "John", "email": "john@example.com"}}
        result = extract_values(keys, context)
        self.assertEqual(result.get("user.name"), "John")
        self.assertEqual(result.get("user.email"), "john@example.com")

    def test_extract_values_object_attribute(self):
        """Test extracting values from object attributes."""
        class User:
            name = "Jane"
            email = "jane@example.com"

        keys = ["user.name"]
        context = {"user": User()}
        result = extract_values(keys, context)
        self.assertEqual(result.get("user.name"), "Jane")

    def test_extract_values_nested_attribute(self):
        """Test extracting nested attribute values."""
        class Profile:
            email = "nested@example.com"

        class User:
            profile = Profile()

        keys = ["user.profile.email"]
        context = {"user": User()}
        result = extract_values(keys, context)
        self.assertEqual(result.get("user.profile.email"), "nested@example.com")

    def test_extract_values_missing_object(self):
        """Test with missing object in context."""
        keys = ["user.name"]
        context = {"team": {"name": "Test Team"}}
        result = extract_values(keys, context)
        self.assertNotIn("user.name", result)

    def test_extract_values_single_part_key(self):
        """Test with single part key (should be skipped)."""
        keys = ["name"]
        context = {"user": {"name": "John"}}
        result = extract_values(keys, context)
        self.assertEqual(result, {})

    def test_extract_values_none_value_excluded(self):
        """Test that None values are excluded from result."""
        class User:
            name = None

        keys = ["user.name"]
        context = {"user": User()}
        result = extract_values(keys, context)
        self.assertNotIn("user.name", result)


class DefaultValuesTests(TestCase):
    """Tests for default value getter functions."""

    def test_get_default_additional_sections(self):
        """Test getting default additional sections."""
        result = get_default_additional_sections()
        self.assertEqual(result, DEFAULT_ADDITIONAL_SECTIONS)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_get_default_additional_sections_structure(self):
        """Test structure of default additional sections."""
        result = get_default_additional_sections()
        for section in result:
            self.assertIn("show_to_members", section)
            self.assertIn("show_to_both", section)
            self.assertIn("show_to_athletes", section)
            self.assertIn("name", section)
            self.assertIn("text", section)

    def test_get_default_configuration(self):
        """Test getting default configuration."""
        result = get_default_configuration()
        self.assertIsInstance(result, dict)
        self.assertIn("mandatory_phone", result)
        self.assertIn("mandatory_email", result)
        self.assertIn("mandatory_signature", result)
        self.assertFalse(result["mandatory_phone"])
        self.assertFalse(result["mandatory_email"])
        self.assertFalse(result["mandatory_signature"])

    def test_get_default_enabled_for(self):
        """Test getting default enabled for list."""
        result = get_default_enabled_for()
        self.assertIsInstance(result, list)
        self.assertIn("associate", result)
        self.assertIn("associate-membership", result)
        self.assertIn("membership", result)

    def test_get_default_stripe_methods(self):
        """Test getting default stripe methods."""
        result = get_default_stripe_methods()
        self.assertIsInstance(result, list)
        self.assertIn("card", result)
        self.assertIn("sepa_debit", result)
