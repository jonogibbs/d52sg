"""Tests for roundrobin.py — round-robin generation and verification."""

from d52sg.roundrobin import (
    generate_round_robin,
    generate_crossover,
    verify_round_robin,
    verify_crossover,
)


class TestGenerateRoundRobin:
    def test_even_teams(self):
        teams = ["A", "B", "C", "D"]
        rounds = generate_round_robin(teams, seed=1)
        # 4 teams => 3 rounds, 2 games each
        assert len(rounds) == 3
        for r in rounds:
            assert len(r.matchups) == 2
            assert r.bye_teams == []

    def test_odd_teams(self):
        teams = ["A", "B", "C", "D", "E"]
        rounds = generate_round_robin(teams, seed=1)
        # 5 teams + dummy = 6, N-1 = 5 rounds, 2 games + 1 bye each
        assert len(rounds) == 5
        for r in rounds:
            assert len(r.matchups) == 2
            assert len(r.bye_teams) == 1

    def test_every_pair_plays_once_even(self):
        teams = [f"T{i}" for i in range(6)]
        rounds = generate_round_robin(teams, seed=42)
        result = verify_round_robin(rounds, teams)
        assert result["valid"], result["errors"]

    def test_every_pair_plays_once_odd(self):
        teams = [f"T{i}" for i in range(7)]
        rounds = generate_round_robin(teams, seed=42)
        result = verify_round_robin(rounds, teams)
        assert result["valid"], result["errors"]

    def test_13_teams(self):
        """Match the actual North pool size."""
        teams = [f"N{i}" for i in range(13)]
        rounds = generate_round_robin(teams, seed=42)
        assert len(rounds) == 13  # 13 teams + dummy = 14, N-1 = 13 rounds
        result = verify_round_robin(rounds, teams)
        assert result["valid"], result["errors"]
        # Each team plays 12 games
        for t in teams:
            assert result["games_per_team"][t] == 12

    def test_12_teams(self):
        """Match the actual South pool size."""
        teams = [f"S{i}" for i in range(12)]
        rounds = generate_round_robin(teams, seed=42)
        assert len(rounds) == 11
        result = verify_round_robin(rounds, teams)
        assert result["valid"], result["errors"]
        for t in teams:
            assert result["games_per_team"][t] == 11

    def test_no_team_plays_twice_in_round(self):
        teams = [f"T{i}" for i in range(10)]
        rounds = generate_round_robin(teams, seed=99)
        for r in rounds:
            seen = set()
            for m in r.matchups:
                assert m.team_a not in seen, f"{m.team_a} plays twice in round {r.number}"
                assert m.team_b not in seen, f"{m.team_b} plays twice in round {r.number}"
                seen.add(m.team_a)
                seen.add(m.team_b)

    def test_deterministic_with_seed(self):
        teams = ["A", "B", "C", "D", "E", "F"]
        r1 = generate_round_robin(teams, seed=7)
        r2 = generate_round_robin(teams, seed=7)
        for a, b in zip(r1, r2):
            assert len(a.matchups) == len(b.matchups)
            for ma, mb in zip(a.matchups, b.matchups):
                assert ma.team_a == mb.team_a
                assert ma.team_b == mb.team_b

    def test_different_seeds_differ(self):
        teams = ["A", "B", "C", "D", "E", "F"]
        r1 = generate_round_robin(teams, seed=1)
        r2 = generate_round_robin(teams, seed=2)
        # Very unlikely to be identical with different seeds
        first_matchups_1 = [(m.team_a, m.team_b) for m in r1[0].matchups]
        first_matchups_2 = [(m.team_a, m.team_b) for m in r2[0].matchups]
        assert first_matchups_1 != first_matchups_2

    def test_two_teams(self):
        rounds = generate_round_robin(["A", "B"], seed=1)
        assert len(rounds) == 1
        assert len(rounds[0].matchups) == 1

    def test_one_team(self):
        rounds = generate_round_robin(["A"], seed=1)
        assert rounds == []

    def test_empty(self):
        rounds = generate_round_robin([], seed=1)
        assert rounds == []


class TestGenerateCrossover:
    def test_equal_pools(self):
        north = ["N1", "N2", "N3"]
        south = ["S1", "S2", "S3"]
        rounds = generate_crossover(north, south, seed=1)
        assert len(rounds) == 3
        result = verify_crossover(rounds, north, south)
        assert result["valid"], result["errors"]

    def test_unequal_pools(self):
        """13 North vs 12 South — match actual pool sizes."""
        north = [f"N{i}" for i in range(13)]
        south = [f"S{i}" for i in range(12)]
        rounds = generate_crossover(north, south, seed=42)
        assert len(rounds) == 13  # max(13, 12)
        result = verify_crossover(rounds, north, south)
        assert result["valid"], result["errors"]

    def test_every_pair_plays_once(self):
        north = ["N1", "N2", "N3", "N4"]
        south = ["S1", "S2", "S3"]
        rounds = generate_crossover(north, south, seed=42)
        result = verify_crossover(rounds, north, south)
        assert result["valid"], result["errors"]
        # Each north team plays 3 games (one vs each south)
        for t in north:
            assert result["games_per_team"][t] == 3
        # Each south team plays 4 games (one vs each north)
        for t in south:
            assert result["games_per_team"][t] == 4

    def test_no_team_plays_twice_in_round(self):
        north = [f"N{i}" for i in range(5)]
        south = [f"S{i}" for i in range(4)]
        rounds = generate_crossover(north, south, seed=42)
        for r in rounds:
            seen = set()
            for m in r.matchups:
                assert m.team_a not in seen
                assert m.team_b not in seen
                seen.add(m.team_a)
                seen.add(m.team_b)

    def test_byes_with_unequal_pools(self):
        north = ["N1", "N2", "N3"]
        south = ["S1", "S2"]
        rounds = generate_crossover(north, south, seed=1)
        # Some rounds should have byes
        all_byes = []
        for r in rounds:
            all_byes.extend(r.bye_teams)
        assert len(all_byes) > 0

    def test_deterministic(self):
        north = ["N1", "N2"]
        south = ["S1", "S2"]
        r1 = generate_crossover(north, south, seed=5)
        r2 = generate_crossover(north, south, seed=5)
        for a, b in zip(r1, r2):
            for ma, mb in zip(a.matchups, b.matchups):
                assert ma.team_a == mb.team_a
                assert ma.team_b == mb.team_b

    def test_empty_north(self):
        assert generate_crossover([], ["S1"], seed=1) == []

    def test_empty_south(self):
        assert generate_crossover(["N1"], [], seed=1) == []


class TestVerifyRoundRobin:
    def test_detects_missing_matchup(self):
        from d52sg.models import Matchup, Round
        # 3 teams should have 3 pairs, but only provide 2
        rounds = [
            Round(1, [Matchup("A", "B")]),
            Round(2, [Matchup("A", "C")]),
            # Missing B vs C
        ]
        result = verify_round_robin(rounds, ["A", "B", "C"])
        assert not result["valid"]
        assert any("B vs C" in e or "C vs B" in e for e in result["errors"])

    def test_detects_duplicate_matchup(self):
        from d52sg.models import Matchup, Round
        rounds = [
            Round(1, [Matchup("A", "B")]),
            Round(2, [Matchup("A", "C")]),
            Round(3, [Matchup("B", "C")]),
            Round(4, [Matchup("A", "B")]),  # duplicate
        ]
        result = verify_round_robin(rounds, ["A", "B", "C"])
        assert not result["valid"]

    def test_detects_team_playing_twice_in_round(self):
        from d52sg.models import Matchup, Round
        rounds = [
            Round(1, [Matchup("A", "B"), Matchup("A", "C")]),
        ]
        result = verify_round_robin(rounds, ["A", "B", "C"])
        assert not result["valid"]
        assert any("A" in e and "twice" in e for e in result["errors"])
