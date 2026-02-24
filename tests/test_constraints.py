"""Tests for constraints.py — schedule validation."""

from collections import defaultdict
from datetime import date, time
from d52sg.models import DayOfWeek, FieldSlot, Game, League, Team
from d52sg.constraints import validate_schedule, _slot_block_key


def _make_team(code, league_code, pool="north", **kwargs):
    return Team(code=code, league_code=league_code, pool=pool, **kwargs)


def _make_league(code, teams, **kwargs):
    defaults = dict(full_name=code, has_fields=True,
                    weekday_fields=[], weekend_fields=[])
    defaults.update(kwargs)
    return League(code=code, teams=teams, **defaults)


def _make_game(home, away, d, t=time(17, 30), game_type="intra",
               week=1, host=None, field="Field1"):
    if host is None:
        host = home
    end_t = time(min(t.hour + 2, 23), t.minute)
    return Game(
        home_team=home, away_team=away, host_team=host,
        date=d, start_time=t, end_time=end_t,
        field_name=field, round_number=1,
        game_type=game_type, week_number=week,
    )


class TestSlotBlockKey:
    def test_weekday(self):
        # 2026-03-09 is a Monday
        key = _slot_block_key(date(2026, 3, 9))
        assert key[1] == "weekday"

    def test_weekend(self):
        # 2026-03-14 is a Saturday
        key = _slot_block_key(date(2026, 3, 14))
        assert key[1] == "weekend"

    def test_same_week(self):
        # Mon and Fri of the same week
        mon = _slot_block_key(date(2026, 3, 9))
        fri = _slot_block_key(date(2026, 3, 13))
        assert mon == fri  # same weekday block

    def test_weekend_sat_sun_same_block(self):
        sat = _slot_block_key(date(2026, 3, 14))
        sun = _slot_block_key(date(2026, 3, 15))
        assert sat == sun  # same weekend block


class TestValidateSchedule:
    def _simple_setup(self):
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
            "C": _make_team("C", "L2"),
            "D": _make_team("D", "L2"),
        }
        leagues = {
            "L1": _make_league("L1", ["A", "B"]),
            "L2": _make_league("L2", ["C", "D"]),
        }
        pools = {"north": ["A", "B", "C", "D"], "south": []}
        return teams, leagues, pools

    def test_valid_schedule(self):
        teams, leagues, pools = self._simple_setup()
        games = [
            _make_game("A", "B", date(2026, 3, 10), week=1),
            _make_game("C", "D", date(2026, 3, 10), week=1),
            _make_game("B", "A", date(2026, 3, 17), week=2),
            _make_game("D", "C", date(2026, 3, 17), week=2),
        ]
        # Home/away: A=1H/1A, B=1H/1A, C=1H/1A, D=1H/1A
        result = validate_schedule(games, teams, leagues, pools)
        # Might still have warnings (unplayed pairs) but no errors
        ha_errors = [e for e in result["errors"] if "imbalance" in e]
        assert len(ha_errors) == 0

    def test_home_away_imbalance(self):
        teams, leagues, pools = self._simple_setup()
        games = [
            _make_game("A", "B", date(2026, 3, 10), week=1),
            _make_game("A", "B", date(2026, 3, 17), week=2),
            # A: 2H/0A, B: 0H/2A
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert not result["valid"]
        assert any("imbalance" in e for e in result["errors"])

    def test_same_slot_block_violation(self):
        teams, leagues, pools = self._simple_setup()
        # A plays twice in the same weekday block
        games = [
            _make_game("A", "B", date(2026, 3, 9), week=1),   # Monday
            _make_game("A", "C", date(2026, 3, 11), week=1),  # Wednesday
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert any("plays 2 games" in e for e in result["errors"])

    def test_blackout_violation(self):
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
        }
        leagues = {
            "L1": _make_league("L1", ["A", "B"],
                               blackout_ranges=[(date(2026, 4, 4), date(2026, 4, 12))]),
        }
        pools = {"north": ["A", "B"], "south": []}
        games = [
            _make_game("A", "B", date(2026, 4, 8), week=5),
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert any("blackout" in e for e in result["errors"])

    def test_no_play_day_violation(self):
        teams = {
            "A": _make_team("A", "L1", no_play_days=[DayOfWeek.Mon]),
            "B": _make_team("B", "L1"),
        }
        leagues = {"L1": _make_league("L1", ["A", "B"])}
        pools = {"north": ["A", "B"], "south": []}
        # 2026-03-09 is a Monday
        games = [_make_game("A", "B", date(2026, 3, 9), week=1)]
        result = validate_schedule(games, teams, leagues, pools)
        assert any("no-play day" in e for e in result["errors"])

    def test_weekday_only_on_weekend(self):
        teams = {
            "A": _make_team("A", "L1", weekday_only=True),
            "B": _make_team("B", "L1"),
        }
        leagues = {"L1": _make_league("L1", ["A", "B"])}
        pools = {"north": ["A", "B"], "south": []}
        # 2026-03-14 is a Saturday
        games = [_make_game("A", "B", date(2026, 3, 14), week=1,
                            game_type="crossover")]
        result = validate_schedule(games, teams, leagues, pools)
        assert any("weekday-only" in e for e in result["errors"])

    def test_avoid_same_time_error(self):
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
            "C": _make_team("C", "L2"),
            "D": _make_team("D", "L2"),
        }
        leagues = {
            "L1": _make_league("L1", ["A", "B"]),
            "L2": _make_league("L2", ["C", "D"]),
        }
        pools = {"north": ["A", "B", "C", "D"], "south": []}
        ast_groups = [frozenset(["A", "B"])]
        # A and B from same group play same day at different fields
        games = [
            _make_game("A", "C", date(2026, 3, 10), time(17, 30), week=1,
                       field="FieldX"),
            _make_game("B", "D", date(2026, 3, 10), time(18, 0), week=1,
                       field="FieldY"),
        ]
        result = validate_schedule(games, teams, leagues, pools,
                                   avoid_same_time_groups=ast_groups)
        assert any("avoid_same_time" in w for w in result["warnings"])

    def test_avoid_same_day_same_field_ok(self):
        """Same day, same field is allowed for avoid_same_time groups."""
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
            "C": _make_team("C", "L2"),
            "D": _make_team("D", "L2"),
        }
        leagues = {
            "L1": _make_league("L1", ["A", "B"]),
            "L2": _make_league("L2", ["C", "D"]),
        }
        pools = {"north": ["A", "B", "C", "D"], "south": []}
        ast_groups = [frozenset(["A", "B"])]
        # A and B play same day at the SAME field — OK
        games = [
            _make_game("A", "C", date(2026, 3, 10), time(10, 0), week=1,
                       field="SharedField"),
            _make_game("B", "D", date(2026, 3, 10), time(13, 0), week=1,
                       field="SharedField"),
        ]
        result = validate_schedule(games, teams, leagues, pools,
                                   avoid_same_time_groups=ast_groups)
        assert not any("avoid_same_time" in e for e in result["errors"])

    def test_same_time_no_error_without_group(self):
        """Same-league same-time without avoid_same_time_groups is not an error."""
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
            "C": _make_team("C", "L2"),
            "D": _make_team("D", "L2"),
        }
        leagues = {
            "L1": _make_league("L1", ["A", "B"]),
            "L2": _make_league("L2", ["C", "D"]),
        }
        pools = {"north": ["A", "B", "C", "D"], "south": []}
        # No avoid_same_time_groups — no error or warning
        games = [
            _make_game("A", "C", date(2026, 3, 10), time(17, 30), week=1),
            _make_game("B", "D", date(2026, 3, 10), time(17, 30), week=1),
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert not any("avoid_same_time" in e for e in result["errors"])
        assert not any("same time" in w for w in result["warnings"])

    def test_game_count_balance(self):
        """Weekday game count spread > 1 should be a warning."""
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
            "C": _make_team("C", "L1"),
        }
        leagues = {"L1": _make_league("L1", ["A", "B", "C"])}
        pools = {"north": ["A", "B", "C"], "south": []}
        # A plays 3 weekday games, C plays 1
        games = [
            _make_game("A", "B", date(2026, 3, 9), week=1),
            _make_game("A", "C", date(2026, 3, 16), week=2),
            _make_game("A", "B", date(2026, 3, 23), week=3),
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert any("game count spread" in w for w in result["warnings"])

    def test_field_belongs_to_team_league(self):
        """Field from a third league should produce an error."""
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L2"),
        }
        leagues = {
            "L1": _make_league("L1", ["A"],
                               weekday_fields=[FieldSlot("Field1", DayOfWeek.Tue, time(17, 30))]),
            "L2": _make_league("L2", ["B"],
                               weekday_fields=[FieldSlot("Field2", DayOfWeek.Tue, time(17, 30))]),
            "L3": _make_league("L3", [],
                               weekday_fields=[FieldSlot("Field3", DayOfWeek.Tue, time(17, 30))]),
        }
        pools = {"north": ["A", "B"], "south": []}
        # Game uses Field3 which belongs to L3, not L1 or L2
        games = [
            _make_game("A", "B", date(2026, 3, 10), week=1, field="Field3"),
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert any("neither team's league" in e for e in result["errors"])

    def test_field_from_own_league_ok(self):
        """Field from home or away team's league is valid."""
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L2"),
        }
        leagues = {
            "L1": _make_league("L1", ["A"],
                               weekday_fields=[FieldSlot("Field1", DayOfWeek.Tue, time(17, 30))]),
            "L2": _make_league("L2", ["B"],
                               weekday_fields=[FieldSlot("Field2", DayOfWeek.Tue, time(17, 30))]),
        }
        pools = {"north": ["A", "B"], "south": []}
        games = [
            _make_game("A", "B", date(2026, 3, 10), week=1, field="Field1"),
            _make_game("B", "A", date(2026, 3, 17), week=2, field="Field2"),
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert not any("neither team's league" in e for e in result["errors"])

    def test_unscheduled_game_error(self):
        """Unscheduled games should produce errors (schedule is invalid)."""
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
        }
        leagues = {"L1": _make_league("L1", ["A", "B"])}
        pools = {"north": ["A", "B"], "south": []}
        games = [
            Game(home_team="A", away_team="B", host_team="A",
                 date=date.min, start_time=time(0, 0), end_time=time(0, 0),
                 field_name="UNSCHEDULED", round_number=1,
                 game_type="intra", week_number=1, unscheduled=True),
        ]
        result = validate_schedule(games, teams, leagues, pools)
        assert not result["valid"]
        assert any("UNSCHEDULED" in e for e in result["errors"])

    def test_weekend_spread_no_error(self):
        """Weekend game count spread should NOT be an error."""
        teams = {
            "A": _make_team("A", "L1"),
            "B": _make_team("B", "L1"),
            "C": _make_team("C", "L1"),
        }
        leagues = {"L1": _make_league("L1", ["A", "B", "C"])}
        pools = {"north": ["A", "B", "C"], "south": []}
        # A plays 2 weekend games, C plays 0 — spread > 1
        games = [
            _make_game("A", "B", date(2026, 3, 14), week=1,
                       game_type="crossover"),
            _make_game("A", "C", date(2026, 3, 21), week=2,
                       game_type="crossover"),
        ]
        result = validate_schedule(games, teams, leagues, pools)
        # No "Weekend game count spread" error
        assert not any("Weekend" in e and "spread" in e
                       for e in result["errors"])
