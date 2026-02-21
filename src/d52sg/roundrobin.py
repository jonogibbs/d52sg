"""Round-robin schedule generation with shuffle for D52 scheduling app."""

import random
from d52sg.models import Matchup, Round


def generate_round_robin(teams: list[str], seed: int | None = None) -> list[Round]:
    """Generate a full round-robin schedule using the circle method.

    For N teams: N-1 rounds if even, N-1 rounds with byes if odd.
    Teams are shuffled before generation, and rounds are shuffled after,
    so the schedule looks random while remaining balanced.

    Returns list of Rounds, each containing N/2 matchups (and 1 bye if odd).
    """
    rng = random.Random(seed)

    # Shuffle team order so the circle method doesn't produce predictable patterns
    shuffled = list(teams)
    rng.shuffle(shuffled)

    n = len(shuffled)
    if n < 2:
        return []

    # For odd number of teams, add a dummy for byes
    use_dummy = n % 2 == 1
    if use_dummy:
        shuffled.append("__BYE__")
        n += 1

    # Circle method: fix position 0, rotate the rest
    rounds = []
    for r in range(n - 1):
        matchups = []
        bye_teams = []
        for i in range(n // 2):
            t1 = shuffled[i]
            t2 = shuffled[n - 1 - i]
            if t1 == "__BYE__":
                bye_teams.append(t2)
            elif t2 == "__BYE__":
                bye_teams.append(t1)
            else:
                matchups.append(Matchup(t1, t2))

        rounds.append(Round(
            number=r + 1,
            matchups=matchups,
            round_type="intra",
            bye_teams=bye_teams,
        ))

        # Rotate: keep position 0 fixed, shift others
        shuffled = [shuffled[0]] + [shuffled[-1]] + shuffled[1:-1]

    # Shuffle round order
    rng.shuffle(rounds)

    # Re-number after shuffle
    for i, rnd in enumerate(rounds):
        rnd.number = i + 1

    # Randomly flip home/away within each matchup for additional randomness
    for rnd in rounds:
        for m in rnd.matchups:
            if rng.random() < 0.5:
                m.team_a, m.team_b = m.team_b, m.team_a

    return rounds


def generate_crossover(north: list[str], south: list[str],
                       seed: int | None = None) -> list[Round]:
    """Generate crossover rounds pairing North vs South teams.

    Every North team must play every South team exactly once.
    This requires max(len(north), len(south)) rounds.
    In each round, min(len(north), len(south)) crossover games are played.
    Extra teams from the larger pool get rotating byes.

    Uses a Latin square approach: create an N×M assignment matrix where
    entry (i,j) = round in which north[i] plays south[j].
    """
    rng = random.Random(seed)

    n_north = len(north)
    n_south = len(south)

    if n_north == 0 or n_south == 0:
        return []

    # Shuffle both pools
    north_shuffled = list(north)
    south_shuffled = list(south)
    rng.shuffle(north_shuffled)
    rng.shuffle(south_shuffled)

    # Build assignment: north[i] plays south[j] in round (i + j) % num_rounds
    # This guarantees no conflicts (no two matchups in the same round share a team)
    num_rounds = max(n_north, n_south)

    round_matchups: dict[int, list[Matchup]] = {r: [] for r in range(num_rounds)}
    round_byes: dict[int, list[str]] = {r: [] for r in range(num_rounds)}

    for i in range(n_north):
        for j in range(n_south):
            r = (i + j) % num_rounds
            round_matchups[r].append(Matchup(north_shuffled[i], south_shuffled[j]))

    # Figure out byes: teams not playing in each round
    for r in range(num_rounds):
        playing = set()
        for m in round_matchups[r]:
            playing.add(m.team_a)
            playing.add(m.team_b)
        for t in north_shuffled + south_shuffled:
            if t not in playing:
                round_byes[r].append(t)

    rounds = []
    for r in range(num_rounds):
        rounds.append(Round(
            number=r + 1,
            matchups=round_matchups[r],
            round_type="crossover",
            bye_teams=round_byes[r],
        ))

    # Shuffle round order
    rng.shuffle(rounds)
    for i, rnd in enumerate(rounds):
        rnd.number = i + 1

    # Randomly flip home/away within each matchup
    for rnd in rounds:
        for m in rnd.matchups:
            if rng.random() < 0.5:
                m.team_a, m.team_b = m.team_b, m.team_a

    return rounds


def verify_round_robin(rounds: list[Round], teams: list[str]) -> dict:
    """Verify a round-robin schedule is valid and complete.

    Returns dict with:
    - valid: bool
    - errors: list of error strings
    - matchup_counts: dict of (team_a, team_b) -> count
    - games_per_team: dict of team -> game count
    """
    errors = []
    matchup_counts: dict[tuple[str, str], int] = {}
    games_per_team: dict[str, int] = {t: 0 for t in teams}

    for rnd in rounds:
        teams_in_round = set()
        for m in rnd.matchups:
            # Check for duplicate teams in a round
            if m.team_a in teams_in_round:
                errors.append(f"Round {rnd.number}: {m.team_a} appears twice")
            if m.team_b in teams_in_round:
                errors.append(f"Round {rnd.number}: {m.team_b} appears twice")
            teams_in_round.add(m.team_a)
            teams_in_round.add(m.team_b)

            # Track matchups (normalize order)
            key = tuple(sorted([m.team_a, m.team_b]))
            matchup_counts[key] = matchup_counts.get(key, 0) + 1
            games_per_team[m.team_a] = games_per_team.get(m.team_a, 0) + 1
            games_per_team[m.team_b] = games_per_team.get(m.team_b, 0) + 1

    # Check every pair plays exactly once
    for i, t1 in enumerate(teams):
        for t2 in teams[i + 1:]:
            key = tuple(sorted([t1, t2]))
            count = matchup_counts.get(key, 0)
            if count != 1:
                errors.append(f"{t1} vs {t2}: played {count} times (expected 1)")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "matchup_counts": matchup_counts,
        "games_per_team": games_per_team,
    }


def verify_crossover(rounds: list[Round], north: list[str],
                     south: list[str]) -> dict:
    """Verify crossover rounds: every north team plays every south team exactly once."""
    errors = []
    matchup_counts: dict[tuple[str, str], int] = {}
    games_per_team: dict[str, int] = {t: 0 for t in north + south}

    for rnd in rounds:
        teams_in_round = set()
        for m in rnd.matchups:
            if m.team_a in teams_in_round:
                errors.append(f"Round {rnd.number}: {m.team_a} appears twice")
            if m.team_b in teams_in_round:
                errors.append(f"Round {rnd.number}: {m.team_b} appears twice")
            teams_in_round.add(m.team_a)
            teams_in_round.add(m.team_b)

            key = tuple(sorted([m.team_a, m.team_b]))
            matchup_counts[key] = matchup_counts.get(key, 0) + 1
            games_per_team[m.team_a] = games_per_team.get(m.team_a, 0) + 1
            games_per_team[m.team_b] = games_per_team.get(m.team_b, 0) + 1

    # Every north-south pair should play exactly once
    for n_team in north:
        for s_team in south:
            key = tuple(sorted([n_team, s_team]))
            count = matchup_counts.get(key, 0)
            if count != 1:
                errors.append(f"{n_team} vs {s_team}: played {count} times (expected 1)")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "matchup_counts": matchup_counts,
        "games_per_team": games_per_team,
    }
