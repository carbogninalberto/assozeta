"""Independently authored profile/settings and profile/integrations HTTP expectations; no Django imports."""
import copy
from datetime import datetime, timezone
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/profile_settings_http.json'
ALLOWED = {'/profile/settings': {'GET', 'POST', 'OPTIONS'}, '/profile/integrations': {'GET', 'PATCH', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def current_year(day, month, now=None):
    """BalanceSheetData.get_range_from_year_and_starting_date(datetime.now(), day, month)[0].year
    with TIME_ZONE='UTC': the start of the balance year that contains now."""
    now = now or datetime.now(timezone.utc)
    return now.year if now >= datetime(now.year, month, day, tzinfo=timezone.utc) else now.year - 1


def resolve(case):
    """Replace the request-time {'$current_year': [day, month]} placeholder."""
    response = case['response']
    settings = response.get('settings') if isinstance(response, dict) else None
    if isinstance(settings, dict) and isinstance(settings.get('current_year'), dict):
        case = copy.deepcopy(case)
        day, month = case['response']['settings']['current_year']['$current_year']
        case['response']['settings']['current_year'] = current_year(day, month)
    return case


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(resolve(case), base_url, token=token, sessions=sessions, allowed=lambda case: ALLOWED[case['path']])
