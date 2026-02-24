"""Tests for config.py — parsing and loading."""

from datetime import date, time
from d52sg.config import parse_time, parse_date, parse_date_range, load_config
from d52sg.models import DayOfWeek


class TestParseTime:
    def test_am(self):
        assert parse_time("10am") == time(10, 0)
        assert parse_time("9am") == time(9, 0)

    def test_pm(self):
        assert parse_time("5pm") == time(17, 0)
        assert parse_time("12pm") == time(12, 0)
        assert parse_time("1pm") == time(13, 0)

    def test_with_minutes(self):
        assert parse_time("5:30pm") == time(17, 30)
        assert parse_time("10:15am") == time(10, 15)

    def test_24hour(self):
        assert parse_time("17:00") == time(17, 0)
        assert parse_time("9:30") == time(9, 30)

    def test_midnight(self):
        assert parse_time("12am") == time(0, 0)

    def test_case_insensitive(self):
        assert parse_time("5PM") == time(17, 0)
        assert parse_time("10AM") == time(10, 0)

    def test_whitespace(self):
        assert parse_time("  5:30pm  ") == time(17, 30)


class TestParseDate:
    def test_basic(self):
        assert parse_date("2026-03-07") == date(2026, 3, 7)
        assert parse_date("2026-12-31") == date(2026, 12, 31)

    def test_whitespace(self):
        assert parse_date(" 2026-03-07 ") == date(2026, 3, 7)


class TestParseDateRange:
    def test_basic(self):
        start, end = parse_date_range("2026-04-04:2026-04-12")
        assert start == date(2026, 4, 4)
        assert end == date(2026, 4, 12)


class TestLoadConfig:
    def test_loads_real_config(self):
        config = load_config("config.yaml")

        assert "season" in config
        assert "teams" in config
        assert "leagues" in config
        assert "pools" in config

    def test_season(self):
        config = load_config("config.yaml")
        season = config["season"]

        assert season["start_date"] == date(2026, 3, 7)
        assert season["end_date"] == date(2026, 5, 16)
        assert season["game_length_minutes"] == 150

    def test_pools(self):
        config = load_config("config.yaml")
        pools = config["pools"]

        assert "north" in pools
        assert "south" in pools
        assert len(pools["north"]) == 12
        assert len(pools["south"]) == 12
        assert "PAC1" in pools["north"]
        assert "RWC1" in pools["south"]

    def test_teams(self):
        config = load_config("config.yaml")
        teams = config["teams"]

        assert len(teams) == 24
        assert "SMN1" in teams
        assert teams["SMN1"].weekday_only is True
        assert teams["SMN1"].league_code == "SMN"

        assert "MA1" in teams
        assert DayOfWeek.Mon in teams["MA1"].no_play_days
        assert DayOfWeek.Thu in teams["MA1"].no_play_days

        assert "RAV1" in teams
        assert teams["RAV1"].pool == "south"

    def test_leagues(self):
        config = load_config("config.yaml")
        leagues = config["leagues"]

        assert "BRS" in leagues
        assert "FC" in leagues
        assert "RWC" in leagues

    def test_avoid_same_time_groups(self):
        config = load_config("config.yaml")
        groups = config["avoid_same_time_groups"]
        assert len(groups) == 2
        assert frozenset(["BRS1", "BRS2"]) in groups
        assert frozenset(["FC1", "FC2"]) in groups

    def test_rav_no_fields(self):
        config = load_config("config.yaml")
        leagues = config["leagues"]

        assert leagues["RAV"].has_fields is False
        assert leagues["RAV"].weekday_fields == []
        assert leagues["RAV"].weekend_fields == []

    def test_hil_no_weekday_fields(self):
        config = load_config("config.yaml")
        leagues = config["leagues"]

        assert leagues["HIL"].has_fields is True
        assert leagues["HIL"].weekday_fields == []
        assert len(leagues["HIL"].weekend_fields) == 1

    def test_blackout_dates(self):
        config = load_config("config.yaml")
        leagues = config["leagues"]

        # "Everyone else" group: 4/4-4/12
        assert leagues["BRS"].is_blacked_out(date(2026, 4, 8))
        assert not leagues["BRS"].is_blacked_out(date(2026, 3, 30))

        # SMA/SMN/FC group: 3/28-4/5
        assert leagues["FC"].is_blacked_out(date(2026, 3, 30))
        assert not leagues["FC"].is_blacked_out(date(2026, 4, 10))

    def test_field_slots(self):
        config = load_config("config.yaml")
        leagues = config["leagues"]

        rwc = leagues["RWC"]
        assert len(rwc.weekday_fields) == 3
        assert len(rwc.weekend_fields) == 3
        assert rwc.weekday_home_cap == 3
        assert rwc.weekend_home_cap == 3

    def test_all_teams_in_pools_have_leagues(self):
        config = load_config("config.yaml")
        teams = config["teams"]
        for code, team in teams.items():
            assert team.league_code in config["leagues"], (
                f"Team {code} has league_code {team.league_code} "
                f"not in leagues"
            )
