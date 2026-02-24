"""Tests for models.py — data classes and enums."""

from datetime import date, time
from d52sg.models import (
    DayOfWeek, FieldSlot, League, Team, Matchup, Round, Game,
    CalendarSlot, WEEKDAYS, WEEKENDS,
)


class TestDayOfWeek:
    def test_from_str_full(self):
        assert DayOfWeek.from_str("Monday") == DayOfWeek.Mon
        assert DayOfWeek.from_str("Tuesday") == DayOfWeek.Tue
        assert DayOfWeek.from_str("Saturday") == DayOfWeek.Sat

    def test_from_str_short(self):
        assert DayOfWeek.from_str("Mon") == DayOfWeek.Mon
        assert DayOfWeek.from_str("Thu") == DayOfWeek.Thu
        assert DayOfWeek.from_str("Sun") == DayOfWeek.Sun

    def test_from_str_case_insensitive(self):
        assert DayOfWeek.from_str("tue") == DayOfWeek.Tue
        assert DayOfWeek.from_str("FRI") == DayOfWeek.Fri

    def test_is_weekday(self):
        for d in [DayOfWeek.Mon, DayOfWeek.Tue, DayOfWeek.Wed,
                  DayOfWeek.Thu, DayOfWeek.Fri]:
            assert d.is_weekday()
            assert not d.is_weekend()

    def test_is_weekend(self):
        for d in [DayOfWeek.Sat, DayOfWeek.Sun]:
            assert d.is_weekend()
            assert not d.is_weekday()

    def test_weekdays_weekends_constants(self):
        assert len(WEEKDAYS) == 5
        assert len(WEEKENDS) == 2
        assert all(d.is_weekday() for d in WEEKDAYS)
        assert all(d.is_weekend() for d in WEEKENDS)


class TestLeague:
    def test_home_caps(self):
        league = League(
            code="TEST",
            full_name="Test League",
            teams=["T1", "T2"],
            weekday_fields=[
                FieldSlot("F1", DayOfWeek.Tue, time(17, 30)),
                FieldSlot("F1", DayOfWeek.Thu, time(17, 30)),
            ],
            weekend_fields=[
                FieldSlot("F1", DayOfWeek.Sat, time(10, 0)),
            ],
        )
        assert league.weekday_home_cap == 2
        assert league.weekend_home_cap == 1

    def test_home_caps_empty(self):
        league = League(code="X", full_name="X", teams=["X1"])
        assert league.weekday_home_cap == 0
        assert league.weekend_home_cap == 0

    def test_blackout(self):
        league = League(
            code="T", full_name="T", teams=["T1"],
            blackout_ranges=[
                (date(2026, 4, 4), date(2026, 4, 12)),
            ],
        )
        assert not league.is_blacked_out(date(2026, 4, 3))
        assert league.is_blacked_out(date(2026, 4, 4))
        assert league.is_blacked_out(date(2026, 4, 8))
        assert league.is_blacked_out(date(2026, 4, 12))
        assert not league.is_blacked_out(date(2026, 4, 13))

    def test_blackout_multiple_ranges(self):
        league = League(
            code="T", full_name="T", teams=["T1"],
            blackout_ranges=[
                (date(2026, 3, 7), date(2026, 3, 8)),
                (date(2026, 4, 4), date(2026, 4, 12)),
            ],
        )
        assert league.is_blacked_out(date(2026, 3, 7))
        assert not league.is_blacked_out(date(2026, 3, 9))
        assert league.is_blacked_out(date(2026, 4, 10))

    def test_no_blackouts(self):
        league = League(code="T", full_name="T", teams=["T1"])
        assert not league.is_blacked_out(date(2026, 4, 4))


class TestMatchup:
    def test_involves(self):
        m = Matchup("A", "B")
        assert m.involves("A")
        assert m.involves("B")
        assert not m.involves("C")

    def test_opponent(self):
        m = Matchup("A", "B")
        assert m.opponent("A") == "B"
        assert m.opponent("B") == "A"


class TestRound:
    def test_defaults(self):
        r = Round(number=1, matchups=[])
        assert r.round_type == "intra"
        assert r.bye_teams == []


class TestGame:
    def test_defaults(self):
        g = Game(
            home_team="A", away_team="B", host_team="A",
            date=date(2026, 3, 10), start_time=time(17, 30),
            end_time=time(20, 0), field_name="Field1",
            round_number=1,
        )
        assert g.game_type == "intra"
        assert g.week_number == 0


class TestCalendarSlot:
    def test_defaults(self):
        s = CalendarSlot(week_number=1, slot_type="weekday")
        assert s.dates == []
        assert s.available_teams == set()
        assert s.assigned_round is None
        assert s.games == []
