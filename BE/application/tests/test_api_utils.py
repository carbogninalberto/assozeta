from django.test import TestCase
from datetime import datetime, date, timedelta, timezone as dt_timezone
from django.utils import timezone
from unittest.mock import Mock

from application.utils import api_utils
from application.utils.api_utils import (
    BalanceSheetData,
    check_phone_number,
    check_email,
    check_date,
    check_tax_code,
)
from rest_framework.exceptions import ValidationError


class TestBalanceSheetDateRange(TestCase):
    def setUp(self):
        """Set up test data"""
        self.mock_user = Mock(
            custom_end_date=True,
            subscription_end_month=6,
            subscription_end_day=30
        )

    def test_basic_calendar_year(self):
        """Test for calendar year (Jan-Dec) with a mid-year date"""
        test_date = timezone.make_aware(datetime(2024, 6, 15))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 1)

        self.assertEqual(start.year, 2024)
        self.assertEqual(start.month, 1)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 12)
        self.assertEqual(end.day, 31)
        self.assertTrue(timezone.is_aware(start))
        self.assertTrue(timezone.is_aware(end))

    def test_fiscal_year_september(self):
        """Test fiscal year starting in September with date in next calendar year"""
        test_date = timezone.make_aware(datetime(2025, 1, 8))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 9)

        self.assertEqual(start.year, 2024)
        self.assertEqual(start.month, 9)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.year, 2025)
        self.assertEqual(end.month, 8)
        self.assertEqual(end.day, 31)

    def test_with_date_object(self):
        """Test with date object instead of datetime"""
        test_date = date(2024, 6, 15)
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 1)

        self.assertEqual(start.year, 2024)
        self.assertTrue(timezone.is_aware(start))
        self.assertTrue(timezone.is_aware(end))

    def test_custom_end_date(self):
        """Test with custom user end date"""
        test_date = timezone.make_aware(datetime(2024, 3, 15))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(
            test_date, 1, 1, self.mock_user
        )

        self.assertEqual(end.month, 6)
        self.assertEqual(end.day, 30)
        self.assertTrue(timezone.is_aware(start))
        self.assertTrue(timezone.is_aware(end))

    def test_invalid_month(self):
        """Test with invalid month"""
        test_date = timezone.make_aware(datetime(2024, 6, 15))
        with self.assertRaisesMessage(ValueError, "Month must be between 1 and 12"):
            BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 13)

    def test_invalid_day(self):
        """Test with invalid day"""
        test_date = timezone.make_aware(datetime(2024, 6, 15))
        with self.assertRaisesMessage(ValueError, "Day must be between 1 and 31"):
            BalanceSheetData.get_range_from_year_and_starting_date(test_date, 32, 1)

    def test_invalid_date_combination(self):
        """Test with invalid date combination (e.g., February 30)"""
        test_date = timezone.make_aware(datetime(2024, 6, 15))
        with self.assertRaisesRegex(ValueError, "Invalid date combination"):
            BalanceSheetData.get_range_from_year_and_starting_date(test_date, 30, 2)

    def test_leap_year(self):
        """Test behavior during leap years"""
        test_date = timezone.make_aware(datetime(2024, 2, 29))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 1)

        self.assertEqual(start.year, 2024)
        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 12)
        self.assertEqual(end.day, 31)

    def test_timezone_awareness(self):
        """Test that timezone information is preserved"""
        test_date = datetime(2024, 6, 15, tzinfo=dt_timezone.utc)
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 1)

        self.assertTrue(timezone.is_aware(start))
        self.assertTrue(timezone.is_aware(end))
        self.assertEqual(start.tzinfo, end.tzinfo)

    def test_fiscal_year_across_years(self):
        """Test fiscal year that spans across calendar years (e.g., Apr-Mar)"""
        test_date = timezone.make_aware(datetime(2024, 2, 15))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 4)

        self.assertEqual(start.year, 2023)
        self.assertEqual(start.month, 4)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 3)
        self.assertEqual(end.day, 31)

    def test_exact_start_date(self):
        """Test when the given date is exactly the start date"""
        test_date = timezone.make_aware(datetime(2024, 4, 1))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 4)

        self.assertEqual(start.year, 2024)
        self.assertEqual(start.month, 4)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.year, 2025)
        self.assertEqual(end.month, 3)
        self.assertEqual(end.day, 31)

    def test_day_before_fiscal_year(self):
        """Test with date one day before fiscal year start"""
        test_date = timezone.make_aware(datetime(2024, 3, 31))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(test_date, 1, 4)

        self.assertEqual(start.year, 2023)
        self.assertEqual(start.month, 4)
        self.assertEqual(start.day, 1)
        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 3)
        self.assertEqual(end.day, 31)

    def test_custom_end_date_next_year(self):
        """Test custom end date when it falls in the next year"""
        self.mock_user.subscription_end_month = 1
        self.mock_user.subscription_end_day = 15
        test_date = timezone.make_aware(datetime(2024, 12, 1))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(
            test_date, 1, 11, self.mock_user
        )

        self.assertEqual(start.year, 2024)
        self.assertEqual(start.month, 11)
        self.assertEqual(end.year, 2025)
        self.assertEqual(end.month, 1)
        self.assertEqual(end.day, 15)

    def test_user_without_custom_end_date(self):
        """Test with user that doesn't have custom end date"""
        user = Mock(custom_end_date=False)
        test_date = timezone.make_aware(datetime(2024, 6, 15))
        start, end = BalanceSheetData.get_range_from_year_and_starting_date(
            test_date, 1, 1, user
        )

        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 12)
        self.assertEqual(end.day, 31)

    def test_is_valid_uuid(self):
        valid = api_utils.is_valid_uuid('32ce052c-70d4-4f5d-9e6b-32700a4ab0b6')
        assert valid is True
        self.assertRaises(ValidationError, api_utils.is_valid_uuid, '32ce052c70d44f5d9e632700a4ab0b6')

    def test_days_between(self):
        today = datetime.now().date()
        days = api_utils.days_between(today, today + timedelta(days=10))
        assert days == 10
        days = api_utils.days_between(today + timedelta(days=10), today, absolute=True)
        assert days == 10

    def test_get_range_from_year_and_social_period(self):
        """Test all social period scenarios"""
        test_date = timezone.make_aware(datetime(2024, 6, 15))

        # Test SPORT_YEAR_JUN_MAY before June
        early_date = timezone.make_aware(datetime(2024, 5, 15))
        start, end = BalanceSheetData.get_range_from_year_and_social_period(
            early_date, BalanceSheetData.SPORT_YEAR_JUN_MAY)
        self.assertEqual(start.year, 2023)
        self.assertEqual(start.month, 6)
        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 5)

        # Test SPORT_YEAR_JUN_MAY after June
        late_date = timezone.make_aware(datetime(2024, 7, 15))
        start, end = BalanceSheetData.get_range_from_year_and_social_period(
            late_date, BalanceSheetData.SPORT_YEAR_JUN_MAY)
        self.assertEqual(start.year, 2024)
        self.assertEqual(start.month, 6)
        self.assertEqual(end.year, 2025)
        self.assertEqual(end.month, 5)

        # Test SPORT_YEAR_SEP_AUG before September
        early_date = timezone.make_aware(datetime(2024, 8, 15))
        start, end = BalanceSheetData.get_range_from_year_and_social_period(
            early_date, BalanceSheetData.SPORT_YEAR_SEP_AUG)
        self.assertEqual(start.year, 2023)
        self.assertEqual(start.month, 9)
        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 8)

        # Test invalid social period
        with self.assertRaises(ValidationError):
            BalanceSheetData.get_range_from_year_and_social_period(test_date, 999)

    def test_get_range_from_year_and_social_period_in_months(self):
        """Test month range calculations for different periods"""
        test_date = timezone.make_aware(datetime(2024, 6, 15))

        # Test SPORT_YEAR_JUN_MAY
        months, numbers = BalanceSheetData.get_range_from_year_and_social_period_in_months(
            BalanceSheetData.SPORT_YEAR_JUN_MAY)
        self.assertEqual(months[0], 'Giu')
        self.assertEqual(numbers[0], 6)
        self.assertEqual(len(months), 12)
        self.assertEqual(len(numbers), 12)

        # Test SPORT_YEAR_SEP_AUG
        months, numbers = BalanceSheetData.get_range_from_year_and_social_period_in_months(
            BalanceSheetData.SPORT_YEAR_SEP_AUG)
        self.assertEqual(months[0], 'Set')
        self.assertEqual(numbers[0], 9)

        # Test invalid social period
        with self.assertRaises(ValidationError):
            BalanceSheetData.get_range_from_year_and_social_period_in_months(999)

    def test_get_range_from_year_and_starting_date_in_months(self):
        """Test starting date month calculations"""
        # Test starting from January
        months, numbers = BalanceSheetData.get_range_from_year_and_starting_date_in_months(1)
        self.assertEqual(months[0], 'Gen')
        self.assertEqual(numbers[0], 1)
        self.assertEqual(len(months), 12)
        self.assertEqual(len(numbers), 12)

        # Test starting from middle of year
        months, numbers = BalanceSheetData.get_range_from_year_and_starting_date_in_months(7)
        self.assertEqual(months[0], 'Lug')
        self.assertEqual(numbers[0], 7)
        self.assertEqual(numbers[-1], 6)

    def test_validation_functions(self):
        """Test various validation functions"""
        # Test phone number validation
        self.assertEqual(check_phone_number("1234567890"), "1234567890")
        with self.assertRaises(ValidationError):
            check_phone_number("123")
        with self.assertRaises(ValidationError):
            check_phone_number("abc1234567")

        # Test email validation
        self.assertEqual(check_email("test@example.com"), "test@example.com")
        with self.assertRaises(ValidationError):
            check_email("invalid-email")
        with self.assertRaises(ValidationError):
            check_email("@example.com")

        # Test date validation
        self.assertEqual(check_date("2024-01-08"), "2024-01-08")
        self.assertIsNone(check_date("2024/01/08"))
        self.assertIsNone(check_date("invalid-date"))

        # Test tax code validation
        valid_tax_code = "RSSMRA80A01H501U"  # Example Italian tax code
        self.assertEqual(check_tax_code(valid_tax_code), valid_tax_code)
        with self.assertRaises(ValidationError):
            check_tax_code("invalid-tax-code")
        with self.assertRaises(ValidationError):
            check_tax_code("123456")


class TestGenerateReadableUniqueString(TestCase):
    """Tests for generate_readable_unique_string function."""

    def test_default_length(self):
        """Test generating string with default length."""
        result = api_utils.generate_readable_unique_string()
        self.assertEqual(len(result), 8)

    def test_custom_length(self):
        """Test generating string with custom length."""
        result = api_utils.generate_readable_unique_string(length=16)
        self.assertEqual(len(result), 16)

    def test_uppercase_output(self):
        """Test that output is uppercase."""
        result = api_utils.generate_readable_unique_string()
        self.assertEqual(result, result.upper())

    def test_no_confusing_characters(self):
        """Test that confusing characters are excluded."""
        for _ in range(100):  # Test multiple times
            result = api_utils.generate_readable_unique_string()
            self.assertNotIn('l', result.lower())
            self.assertNotIn('1', result)
            self.assertNotIn('0', result)
            self.assertNotIn('i', result.lower())
            self.assertNotIn('o', result.lower())

    def test_uniqueness(self):
        """Test that generated strings are unique."""
        results = set()
        for _ in range(100):
            results.add(api_utils.generate_readable_unique_string())
        self.assertEqual(len(results), 100)


class TestExtractSexFromItalianFiscalCode(TestCase):
    """Tests for extract_sex_from_italian_fiscal_code function."""

    def test_male_fiscal_code(self):
        """Test extracting sex for male fiscal code."""
        result = api_utils.extract_sex_from_italian_fiscal_code("RSSMRA80A01H501U")
        self.assertEqual(result, 'M')

    def test_female_fiscal_code(self):
        """Test extracting sex for female fiscal code."""
        result = api_utils.extract_sex_from_italian_fiscal_code("RSSMRA80A41H501U")
        self.assertEqual(result, 'F')

    def test_female_high_day(self):
        """Test female with high day value (e.g., 71 = 31st)."""
        result = api_utils.extract_sex_from_italian_fiscal_code("RSSMRA80A71H501U")
        self.assertEqual(result, 'F')

    def test_invalid_fiscal_code(self):
        """Test with invalid fiscal code."""
        result = api_utils.extract_sex_from_italian_fiscal_code("ABC")
        self.assertIsNone(result)

    def test_empty_fiscal_code(self):
        """Test with empty fiscal code."""
        result = api_utils.extract_sex_from_italian_fiscal_code("")
        self.assertIsNone(result)


class TestGetDataFromItalianFiscalCode(TestCase):
    """Tests for get_data_from_italian_fiscal_code function."""

    def test_valid_fiscal_code(self):
        """Test with valid fiscal code."""
        result = api_utils.get_data_from_italian_fiscal_code("RSSMRA85M01H501Z")
        self.assertIsNotNone(result)
        if result:
            self.assertIn('birthplace', result)

    def test_invalid_fiscal_code(self):
        """Test with invalid fiscal code."""
        result = api_utils.get_data_from_italian_fiscal_code("INVALID123456789")
        self.assertIsNone(result)

    def test_empty_fiscal_code(self):
        """Test with empty fiscal code."""
        result = api_utils.get_data_from_italian_fiscal_code("")
        self.assertIsNone(result)

    def test_none_fiscal_code(self):
        """Test with None fiscal code."""
        result = api_utils.get_data_from_italian_fiscal_code(None)
        self.assertIsNone(result)
