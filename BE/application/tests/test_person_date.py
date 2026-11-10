"""A person's date text is read with the day first unless it starts with the year."""
from datetime import date

from unittest import TestCase

from application.utils.person_dates import person_date


class PersonDateTests(TestCase):
    def read(self, text):
        return person_date(text).date()

    def test_day_comes_first(self):
        self.assertEqual(self.read('03/02/1990'), date(1990, 2, 3))
        self.assertEqual(self.read('3/2/1990'), date(1990, 2, 3))
        self.assertEqual(self.read('03-02-1990'), date(1990, 2, 3))

    def test_a_second_number_that_is_no_month_is_the_day(self):
        self.assertEqual(self.read('03/25/1990'), date(1990, 3, 25))

    def test_a_text_starting_with_the_year_is_year_month_day(self):
        self.assertEqual(self.read('1990-02-03'), date(1990, 2, 3))
        self.assertEqual(self.read('1990-02-03T10:20:30'), date(1990, 2, 3))

    def test_text_without_a_date_still_raises(self):
        with self.assertRaises(ValueError):
            self.read('')
