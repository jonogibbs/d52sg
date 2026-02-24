"""Statistics and balance reporting for D52 scheduling app."""

from collections import defaultdict
from d52sg.models import Game, DayOfWeek


def compute_stats(games: list[Game], teams: dict, leagues: dict,
                  pools: dict) -> dict:
    """Compute comprehensive statistics for a schedule.

    Returns dict with all stats needed for validation and reporting.
    """
    scheduled_games = [g for g in games if not g.unscheduled]
    unscheduled_games = [g for g in games if g.unscheduled]

    all_teams = sorted(teams.keys())
    north = set(pools["north"])
    south = set(pools["south"])

    # Per-team counts
    home_counts = defaultdict(int)
    away_counts = defaultdict(int)
    hosted_counts = defaultdict(int)
    home_not_hosting = defaultdict(int)  # home team but not host (playing "away")
    weekday_home = defaultdict(int)
    weekday_away = defaultdict(int)
    weekend_home = defaultdict(int)
    weekend_away = defaultdict(int)
    total_games = defaultdict(int)

    # Day-of-week distribution
    day_counts = defaultdict(lambda: defaultdict(int))  # team -> day -> count

    # Matchup matrix
    matchup_counts = defaultdict(lambda: defaultdict(int))  # team -> opponent -> count

    # League home caps per week
    league_home_per_week = defaultdict(lambda: defaultdict(int))

    # Games per week per team
    games_per_week = defaultdict(lambda: defaultdict(int))

    for game in scheduled_games:
        h = game.home_team
        a = game.away_team
        is_weekend = game.date.weekday() >= 5

        home_counts[h] += 1
        away_counts[a] += 1
        hosted_counts[game.host_team] += 1
        if game.host_team != h:
            home_not_hosting[h] += 1
        total_games[h] += 1
        total_games[a] += 1

        if is_weekend:
            weekend_home[h] += 1
            weekend_away[a] += 1
        else:
            weekday_home[h] += 1
            weekday_away[a] += 1

        dow = DayOfWeek(game.date.weekday())
        day_counts[h][dow.name] += 1
        day_counts[a][dow.name] += 1

        matchup_counts[h][a] += 1
        matchup_counts[a][h] += 1

        h_league = teams[h].league_code
        week_key = f"W{game.week_number}_{game.game_type}"
        league_home_per_week[h_league][week_key] += 1

        games_per_week[h][game.week_number] += 1
        games_per_week[a][game.week_number] += 1

    # Per-team unscheduled game counts, and which (week, slot_type) they affect
    unsched_per_team: dict[str, int] = defaultdict(int)
    unsched_team_slots: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for g in unscheduled_games:
        unsched_per_team[g.home_team] += 1
        unsched_per_team[g.away_team] += 1
        st = g.slot_type if g.slot_type else "weekend"
        unsched_team_slots[g.home_team].add((g.week_number, st))
        unsched_team_slots[g.away_team].add((g.week_number, st))

    # Blackout and bye counts per team
    # Determine all (week_number, slot_type) combos from games
    all_week_slots: set[tuple[int, str]] = set()
    team_week_slots: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for game in scheduled_games:
        slot_type = "weekend" if game.date.weekday() >= 5 else "weekday"
        ws = (game.week_number, slot_type)
        all_week_slots.add(ws)
        team_week_slots[game.home_team].add(ws)
        team_week_slots[game.away_team].add(ws)
    # Include slots that only have unscheduled games
    for t, slots in unsched_team_slots.items():
        all_week_slots.update(slots)

    blackout_counts = {}
    bye_counts = {}
    for t in all_teams:
        team_obj = teams[t]
        league = leagues[team_obj.league_code]
        # Count slots where team is blacked out
        bo = 0
        byes = 0
        for wk, st in all_week_slots:
            # Collect ALL dates in this (week, slot_type) from scheduled games
            slot_dates = [g.date for g in scheduled_games
                          if g.week_number == wk
                          and ("weekend" if g.date.weekday() >= 5 else "weekday") == st]
            if not slot_dates:
                continue
            unique_dates = sorted(set(slot_dates))
            # Weekday-only teams are "blacked out" from weekend slots
            if st == "weekend" and team_obj.weekday_only:
                if not any(d in team_obj.available_weekends for d in unique_dates):
                    bo += 1
                    continue
            # A team is blacked out if ALL dates in the slot are blacked out
            if all(league.is_blacked_out(d) for d in unique_dates):
                bo += 1
            elif (wk, st) not in team_week_slots[t]:
                # Team didn't play in this slot — only count as bye if
                # they don't have an unscheduled game here
                if (wk, st) not in unsched_team_slots.get(t, set()):
                    byes += 1
        blackout_counts[t] = bo
        bye_counts[t] = byes

    # Field slot utilization: (field, day_of_week, time) x (week, weekday/weekend)
    # Each cell = number of games using that physical slot in that scheduling week
    field_slot_usage = defaultdict(lambda: defaultdict(int))
    for game in scheduled_games:
        slot_type = "WE" if game.date.weekday() >= 5 else "WD"
        week_slot = (game.week_number, slot_type)
        dow = DayOfWeek(game.date.weekday())
        field_slot = (game.field_name, dow.name, game.start_time)
        field_slot_usage[field_slot][week_slot] += 1

    return {
        "all_teams": all_teams,
        "north": north,
        "south": south,
        "home_counts": dict(home_counts),
        "away_counts": dict(away_counts),
        "hosted_counts": dict(hosted_counts),
        "home_not_hosting": dict(home_not_hosting),
        "weekday_home": dict(weekday_home),
        "weekday_away": dict(weekday_away),
        "weekend_home": dict(weekend_home),
        "weekend_away": dict(weekend_away),
        "total_games": dict(total_games),
        "day_counts": {k: dict(v) for k, v in day_counts.items()},
        "matchup_counts": {k: dict(v) for k, v in matchup_counts.items()},
        "league_home_per_week": {k: dict(v) for k, v in league_home_per_week.items()},
        "games_per_week": {k: dict(v) for k, v in games_per_week.items()},
        "blackout_counts": blackout_counts,
        "bye_counts": bye_counts,
        "unsched_per_team": dict(unsched_per_team),
        "field_slot_usage": {k: dict(v) for k, v in field_slot_usage.items()},
        "unscheduled_count": len(unscheduled_games),
        "unscheduled_games": unscheduled_games,
    }


def format_stats_report(stats: dict, teams: dict, leagues: dict,
                        pools: dict) -> str:
    """Format statistics into a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("SCHEDULE STATISTICS")
    lines.append("=" * 70)

    all_teams = stats["all_teams"]

    # Home/Away Balance
    def _z(v, width=5, plus=False):
        """Format an integer, suppressing zeros to blank."""
        if v == 0:
            return " " * width
        if plus:
            return f"{v:>+{width}}"
        return f"{v:>{width}}"

    def _z3(v):
        return _z(v, width=3)

    lines.append("\n--- SEASON BALANCE ---")
    lines.append(f"{'Team':<8} {'Home':>5} {'Vis':>5} {'Host':>5} {'H-Aw':>5} {'Total':>5} {'Diff':>5}  "
                 f"{'WD-H':>5} {'WD-V':>5} {'WE-H':>5} {'WE-V':>5}  "
                 f"{'BO':>3} {'BYE':>3} {'UNS':>3}")
    lines.append("-" * 92)
    for t in all_teams:
        h = stats["home_counts"].get(t, 0)
        a = stats["away_counts"].get(t, 0)
        hosted = stats["hosted_counts"].get(t, 0)
        hnh = stats.get("home_not_hosting", {}).get(t, 0)
        tot = stats["total_games"].get(t, 0)
        diff = h - a
        wdh = stats["weekday_home"].get(t, 0)
        wda = stats["weekday_away"].get(t, 0)
        weh = stats["weekend_home"].get(t, 0)
        wea = stats["weekend_away"].get(t, 0)
        bo = stats.get("blackout_counts", {}).get(t, 0)
        bye = stats.get("bye_counts", {}).get(t, 0)
        uns = stats.get("unsched_per_team", {}).get(t, 0)
        flag = " ***" if abs(diff) > 1 else ""
        lines.append(f"{t:<8} {_z(h)} {_z(a)} {_z(hosted)} {_z(hnh)} {_z(tot)} {_z(diff, plus=True)}  "
                     f"{_z(wdh)} {_z(wda)} {_z(weh)} {_z(wea)}  "
                     f"{_z3(bo)} {_z3(bye)} {_z3(uns)}{flag}")

    # Matchup Matrix
    lines.append("\n--- MATCHUP MATRIX ---")
    header = f"{'':>8}"
    for t in all_teams:
        header += f" {t:>5}"
    lines.append(header)
    lines.append("-" * (8 + 6 * len(all_teams)))
    for t1 in all_teams:
        row = f"{t1:>8}"
        for t2 in all_teams:
            if t1 == t2:
                row += "     -"
            else:
                c = stats["matchup_counts"].get(t1, {}).get(t2, 0)
                row += f" {c:>5}"
        lines.append(row)

    # Day of Week Distribution
    lines.append("\n--- GAMES PER DAY OF WEEK ---")
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    header = f"{'Team':<8}"
    for d in days:
        header += f" {d:>4}"
    lines.append(header)
    lines.append("-" * (8 + 5 * len(days)))
    for t in all_teams:
        row = f"{t:<8}"
        for d in days:
            c = stats["day_counts"].get(t, {}).get(d, 0)
            row += f" {c:>4}"
        lines.append(row)

    # Games per week
    lines.append("\n--- GAMES PER WEEK ---")
    max_week = max(
        (max(wk.keys()) for wk in stats["games_per_week"].values() if wk),
        default=0
    )
    if max_week > 0:
        header = f"{'Team':<8}"
        for w in range(1, max_week + 1):
            header += f" W{w:>2}"
        lines.append(header)
        for t in all_teams:
            row = f"{t:<8}"
            for w in range(1, max_week + 1):
                c = stats["games_per_week"].get(t, {}).get(w, 0)
                row += f" {c:>3}"
            lines.append(row)

    return "\n".join(lines)
