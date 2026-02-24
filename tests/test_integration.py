"""Integration test — full end-to-end schedule generation and validation."""

from d52sg.config import load_config
from d52sg.scheduler import schedule
from d52sg.constraints import validate_schedule
from d52sg.stats import compute_stats


class TestEndToEnd:
    def test_generate_and_validate(self):
        """Generate a schedule with seed 42 and check key properties."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)

        assert len(games) > 0

        teams = config["teams"]
        leagues = config["leagues"]
        pools = config["pools"]

        result = validate_schedule(games, teams, leagues, pools,
                                   avoid_same_time_groups=config.get("avoid_same_time_groups"))
        assert result["valid"], (
            f"Validation failed: {result['errors']}"
        )

    def test_no_team_plays_twice_in_slot_block(self):
        """No team should play more than once per weekday or weekend block."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)

        from d52sg.constraints import _slot_block_key
        from collections import defaultdict
        counts = defaultdict(lambda: defaultdict(int))
        for g in games:
            if g.unscheduled:
                continue
            skey = _slot_block_key(g.date)
            counts[g.home_team][skey] += 1
            counts[g.away_team][skey] += 1

        for team, slot_counts in counts.items():
            for skey, count in slot_counts.items():
                assert count <= 1, (
                    f"{team} plays {count} games in week {skey[0]} {skey[1]}"
                )

    def test_no_blackout_violations(self):
        """No team should play on a blackout date."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)
        teams = config["teams"]
        leagues = config["leagues"]

        for g in games:
            if g.unscheduled:
                continue
            h_league = leagues[teams[g.home_team].league_code]
            a_league = leagues[teams[g.away_team].league_code]
            assert not h_league.is_blacked_out(g.date), (
                f"{g.home_team} plays on blackout {g.date}"
            )
            assert not a_league.is_blacked_out(g.date), (
                f"{g.away_team} plays on blackout {g.date}"
            )

    def test_no_play_day_violations(self):
        """No team plays on a day it's excluded from."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)
        teams = config["teams"]
        from d52sg.models import DayOfWeek

        for g in games:
            if g.unscheduled:
                continue
            dow = DayOfWeek(g.date.weekday())
            assert dow not in teams[g.home_team].no_play_days, (
                f"{g.home_team} plays on {dow.name} ({g.date})"
            )
            assert dow not in teams[g.away_team].no_play_days, (
                f"{g.away_team} plays on {dow.name} ({g.date})"
            )

    def test_weekday_only_respected(self):
        """Weekday-only teams only play on weekdays."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)
        teams = config["teams"]
        from d52sg.models import DayOfWeek

        for g in games:
            if g.unscheduled:
                continue
            for t in [g.home_team, g.away_team]:
                if teams[t].weekday_only:
                    dow = DayOfWeek(g.date.weekday())
                    assert dow.is_weekday() or g.date in teams[t].available_weekends, (
                        f"{t} (weekday-only) plays on {dow.name} ({g.date})"
                    )

    def test_fields_belong_to_team_leagues(self):
        """Every game's field must belong to the home or away team's league."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)
        teams = config["teams"]
        leagues = config["leagues"]

        # Build field -> league mapping
        league_fields = {}
        for lcode, league in leagues.items():
            fields = set()
            for fs in league.weekday_fields + league.weekend_fields:
                fields.add(fs.field_name)
            league_fields[lcode] = fields

        for g in games:
            if g.unscheduled:
                continue
            h_fields = league_fields.get(teams[g.home_team].league_code, set())
            a_fields = league_fields.get(teams[g.away_team].league_code, set())
            assert g.field_name in h_fields or g.field_name in a_fields, (
                f"Game {g.home_team} vs {g.away_team} on {g.date} uses "
                f"field {g.field_name} from neither team's league"
            )

    def test_home_equals_host_mostly(self):
        """Home = host for the vast majority of games."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)
        teams = config["teams"]
        leagues = config["leagues"]

        mismatches = 0
        for g in games:
            if g.unscheduled:
                continue
            if g.home_team != g.host_team:
                # Only acceptable for structurally fieldless teams
                home_league = leagues[teams[g.home_team].league_code]
                if home_league.has_fields:
                    mismatches += 1

        # Should be very few (ideally 0)
        assert mismatches <= 5, f"{mismatches} non-structural home!=host games"

    def test_stats_report_runs(self):
        """Stats computation and formatting should not crash."""
        config = load_config("config.yaml")
        games = schedule(config, seed=42)
        teams = config["teams"]
        leagues = config["leagues"]
        pools = config["pools"]

        stats = compute_stats(games, teams, leagues, pools)
        assert "all_teams" in stats
        assert len(stats["all_teams"]) == 24

        from d52sg.stats import format_stats_report
        report = format_stats_report(stats, teams, leagues, pools)
        assert "HOME/VISITOR BALANCE" in report
        assert "MATCHUP MATRIX" in report

    def test_deterministic_with_seed(self):
        """Same seed produces same schedule."""
        config = load_config("config.yaml")
        games1 = schedule(config, seed=42)
        games2 = schedule(config, seed=42)

        assert len(games1) == len(games2)
        for g1, g2 in zip(games1, games2):
            assert g1.home_team == g2.home_team
            assert g1.away_team == g2.away_team
            assert g1.date == g2.date

    def test_multiple_seeds_valid(self):
        """Schedule validates across multiple seeds."""
        config = load_config("config.yaml")
        teams = config["teams"]

        for seed in [1, 3, 7, 42]:
            games = schedule(config, seed=seed)
            result = validate_schedule(
                games, teams, config["leagues"], config["pools"]
            )
            assert result["valid"], (
                f"Seed {seed}: {result['errors']}"
            )
