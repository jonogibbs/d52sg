"""Tests for scheduler.py — calendar building and game assignment."""

from datetime import date, time, timedelta
from d52sg.models import (
    CalendarSlot, DayOfWeek, FieldSlot, League, Matchup, Round, Team,
)
from d52sg.scheduler import build_calendar, _can_host_in_slot


def _make_team(code, league_code, pool="north", **kwargs):
    return Team(code=code, league_code=league_code, pool=pool, **kwargs)


def _make_league(code, teams_list, has_fields=True,
                 weekday_fields=None, weekend_fields=None,
                 blackout_ranges=None, **kwargs):
    return League(
        code=code, full_name=code, teams=teams_list,
        has_fields=has_fields,
        weekday_fields=weekday_fields or [],
        weekend_fields=weekend_fields or [],
        blackout_ranges=blackout_ranges or [],
        **kwargs,
    )


class TestBuildCalendar:
    def test_basic_structure(self):
        # 2 full weeks
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {"L1": _make_league("L1", ["T1"])}

        start = date(2026, 3, 9)   # Monday
        end = date(2026, 3, 22)    # Sunday (2 full weeks)
        slots = build_calendar(start, end, teams, leagues)

        weekday_slots = [s for s in slots if s.slot_type == "weekday"]
        weekend_slots = [s for s in slots if s.slot_type == "weekend"]

        assert len(weekday_slots) == 2
        assert len(weekend_slots) == 2

    def test_weekday_dates(self):
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {"L1": _make_league("L1", ["T1"])}

        start = date(2026, 3, 9)   # Monday
        end = date(2026, 3, 15)    # Sunday
        slots = build_calendar(start, end, teams, leagues)

        wd = [s for s in slots if s.slot_type == "weekday"][0]
        assert len(wd.dates) == 5  # Mon-Fri
        assert wd.dates[0].weekday() == 0  # Monday
        assert wd.dates[-1].weekday() == 4  # Friday

    def test_weekend_dates(self):
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {"L1": _make_league("L1", ["T1"])}

        start = date(2026, 3, 9)
        end = date(2026, 3, 15)
        slots = build_calendar(start, end, teams, leagues)

        we = [s for s in slots if s.slot_type == "weekend"][0]
        assert len(we.dates) == 2  # Sat-Sun
        assert we.dates[0].weekday() == 5  # Saturday

    def test_blackout_excludes_team(self):
        teams = {
            "T1": _make_team("T1", "L1"),
            "T2": _make_team("T2", "L2"),
        }
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               blackout_ranges=[(date(2026, 3, 9), date(2026, 3, 13))]),
            "L2": _make_league("L2", ["T2"]),
        }

        start = date(2026, 3, 9)
        end = date(2026, 3, 15)
        slots = build_calendar(start, end, teams, leagues)

        wd = [s for s in slots if s.slot_type == "weekday"][0]
        assert "T2" in wd.available_teams
        # T1 is blacked out Mon-Fri but might still be available
        # if any weekday in the slot isn't blacked out
        # Blackout is 3/9-3/13 which is Mon-Fri, so all weekdays blacked
        assert "T1" not in wd.available_teams

    def test_weekday_only_team_excluded_from_weekends(self):
        teams = {
            "T1": _make_team("T1", "L1", weekday_only=True),
            "T2": _make_team("T2", "L1"),
        }
        leagues = {"L1": _make_league("L1", ["T1", "T2"])}

        start = date(2026, 3, 9)
        end = date(2026, 3, 15)
        slots = build_calendar(start, end, teams, leagues)

        we = [s for s in slots if s.slot_type == "weekend"][0]
        assert "T1" not in we.available_teams
        assert "T2" in we.available_teams

    def test_weekday_only_with_available_weekend(self):
        teams = {
            "T1": _make_team("T1", "L1", weekday_only=True,
                             available_weekends=[date(2026, 3, 14)]),
        }
        leagues = {"L1": _make_league("L1", ["T1"])}

        start = date(2026, 3, 9)
        end = date(2026, 3, 15)
        slots = build_calendar(start, end, teams, leagues)

        we = [s for s in slots if s.slot_type == "weekend"][0]
        assert "T1" in we.available_teams

    def test_week_numbers_increment(self):
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {"L1": _make_league("L1", ["T1"])}

        start = date(2026, 3, 9)
        end = date(2026, 3, 29)  # 3 weeks
        slots = build_calendar(start, end, teams, leagues)

        weeks = sorted(set(s.week_number for s in slots))
        assert weeks == [1, 2, 3]


class TestCanHostInSlot:
    def test_team_with_fields(self):
        teams = {
            "T1": _make_team("T1", "L1"),
        }
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekday_fields=[FieldSlot("F1", DayOfWeek.Tue, time(17, 30))]),
        }
        slot = CalendarSlot(
            week_number=1, slot_type="weekday",
            dates=[date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11),
                   date(2026, 3, 12), date(2026, 3, 13)],
        )
        assert _can_host_in_slot("T1", slot, teams, leagues)

    def test_team_no_fields(self):
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {"L1": _make_league("L1", ["T1"], has_fields=False)}
        slot = CalendarSlot(
            week_number=1, slot_type="weekday",
            dates=[date(2026, 3, 9)],
        )
        assert not _can_host_in_slot("T1", slot, teams, leagues)

    def test_no_weekday_fields(self):
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekend_fields=[FieldSlot("F1", DayOfWeek.Sat, time(10, 0))]),
        }
        slot = CalendarSlot(
            week_number=1, slot_type="weekday",
            dates=[date(2026, 3, 9)],
        )
        assert not _can_host_in_slot("T1", slot, teams, leagues)

    def test_weekend_fields_on_weekend_slot(self):
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekend_fields=[FieldSlot("F1", DayOfWeek.Sat, time(10, 0))]),
        }
        slot = CalendarSlot(
            week_number=1, slot_type="weekend",
            dates=[date(2026, 3, 14), date(2026, 3, 15)],
        )
        assert _can_host_in_slot("T1", slot, teams, leagues)

    def test_blacked_out_cannot_host(self):
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekday_fields=[FieldSlot("F1", DayOfWeek.Tue, time(17, 30))],
                               blackout_ranges=[(date(2026, 3, 9), date(2026, 3, 13))]),
        }
        slot = CalendarSlot(
            week_number=1, slot_type="weekday",
            dates=[date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11),
                   date(2026, 3, 12), date(2026, 3, 13)],
        )
        assert not _can_host_in_slot("T1", slot, teams, leagues)

    def test_no_play_day_respected(self):
        teams = {
            "T1": _make_team("T1", "L1", no_play_days=[DayOfWeek.Mon,
                                                         DayOfWeek.Tue,
                                                         DayOfWeek.Wed,
                                                         DayOfWeek.Thu,
                                                         DayOfWeek.Fri]),
        }
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekday_fields=[FieldSlot("F1", DayOfWeek.Tue, time(17, 30))]),
        }
        slot = CalendarSlot(
            week_number=1, slot_type="weekday",
            dates=[date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11),
                   date(2026, 3, 12), date(2026, 3, 13)],
        )
        assert not _can_host_in_slot("T1", slot, teams, leagues)

    def test_weekend_day_must_match(self):
        """A Saturday field slot should NOT work on a Sunday-only slot."""
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekend_fields=[FieldSlot("F1", DayOfWeek.Sat, time(10, 0))]),
        }
        # Only Sunday available
        slot = CalendarSlot(
            week_number=1, slot_type="weekend",
            dates=[date(2026, 3, 15)],  # Sunday
        )
        assert not _can_host_in_slot("T1", slot, teams, leagues)

    def test_weekend_day_matches(self):
        """A Saturday field slot works when Saturday is available."""
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekend_fields=[FieldSlot("F1", DayOfWeek.Sat, time(10, 0))]),
        }
        slot = CalendarSlot(
            week_number=1, slot_type="weekend",
            dates=[date(2026, 3, 14), date(2026, 3, 15)],  # Sat + Sun
        )
        assert _can_host_in_slot("T1", slot, teams, leagues)

    def test_weekday_day_must_match(self):
        """A Tuesday field slot should NOT work on a Thursday-only slot."""
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekday_fields=[FieldSlot("F1", DayOfWeek.Tue, time(17, 30))]),
        }
        # Only Thursday available
        slot = CalendarSlot(
            week_number=1, slot_type="weekday",
            dates=[date(2026, 3, 12)],  # Thursday
        )
        assert not _can_host_in_slot("T1", slot, teams, leagues)

    def test_weekday_day_matches(self):
        """A Tuesday field slot works when Tuesday is available."""
        teams = {"T1": _make_team("T1", "L1")}
        leagues = {
            "L1": _make_league("L1", ["T1"],
                               weekday_fields=[FieldSlot("F1", DayOfWeek.Tue, time(17, 30))]),
        }
        slot = CalendarSlot(
            week_number=1, slot_type="weekday",
            dates=[date(2026, 3, 10), date(2026, 3, 12)],  # Tue + Thu
        )
        assert _can_host_in_slot("T1", slot, teams, leagues)
