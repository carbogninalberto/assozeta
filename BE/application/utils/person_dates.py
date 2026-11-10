"""Reading a person's date text (birth dates of members and tutors)."""
import re

import dateutil.parser

# A text that starts with the year: year, month, day.
YEAR_FIRST = re.compile(r'\s*\d{4}\D')


def person_date(value):
    """dateutil's reading of a date text, with the day first unless the text starts with the year.

    Member forms and the import store birth dates as DD/MM/YYYY; '03/02/1990' is 3 February.
    A value that is not a string is passed on unchanged (dateutil raises TypeError).
    """
    day_first = isinstance(value, str) and YEAR_FIRST.match(value) is None
    return dateutil.parser.parse(value, dayfirst=day_first)
