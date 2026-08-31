from application.views.google_views import calendar_datetime_in_rome


def test_calendar_datetime_in_rome_applies_summer_and_winter_offsets():
    summer = calendar_datetime_in_rome('2026-08-27T13:00:00.000Z')
    winter = calendar_datetime_in_rome('2026-12-10T14:00:00.000Z')

    assert summer.isoformat() == '2026-08-27T15:00:00+02:00'
    assert winter.isoformat() == '2026-12-10T15:00:00+01:00'
