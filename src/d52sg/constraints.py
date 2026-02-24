"""Constraint validation for D52 scheduling app.

Can validate either in-memory game list or re-imported CSV.
"""

from collections import defaultdict
from datetime import date, timedelta
from d52sg.models import DayOfWeek, Game


def _slot_block_key(d: date) -> tuple[int, str]:
    """Return (iso_week, 'weekday'|'weekend') for grouping games into slot blocks.

    A team can play at most once per slot block.
    """
    iso_year, iso_week, iso_day = d.isocalendar()
    block = "weekend" if d.weekday() >= 5 else "weekday"
    return (iso_week, block)


def validate_schedule(games: list[Game], teams: dict, leagues: dict,
                      pools: dict,
                      avoid_same_time_groups: list[frozenset] | None = None,
                      ) -> dict:
    """Validate a schedule against all constraints.

    Returns dict with:
    - valid: bool (True if no hard constraint violations)
    - errors: list of hard constraint violations
    - warnings: list of soft constraint issues
    """
    errors = []
    warnings = []

    north = set(pools["north"])
    south = set(pools["south"])

    # Separate scheduled vs unscheduled games
    scheduled_games = [g for g in games if not g.unscheduled]
    unscheduled_games = [g for g in games if g.unscheduled]

    for g in unscheduled_games:
        slot_label = ""
        if g.slot_type:
            slot_label = " WD" if g.slot_type == "weekday" else " WE"
        errors.append(
            f"UNSCHEDULED: {g.home_team} vs {g.away_team} "
            f"(week {g.week_number}{slot_label})"
        )

    # Track per-team stats
    home_counts = defaultdict(int)
    away_counts = defaultdict(int)
    games_per_slot = defaultdict(lambda: defaultdict(int))  # team -> slot_block_key -> count
    matchup_counts = defaultdict(lambda: defaultdict(int))

    # Track per-team (date, field) for avoid-same-day-different-field checks
    team_date_field: dict[str, list[tuple]] = defaultdict(list)

    # Build set of valid fields per league for Rule 1 checking
    league_fields: dict[str, set[str]] = {}
    for lcode, league in leagues.items():
        fields = set()
        for fs in league.weekday_fields + league.weekend_fields:
            fields.add(fs.field_name)
        league_fields[lcode] = fields

    for game in scheduled_games:
        h = game.home_team
        a = game.away_team

        if h not in teams:
            errors.append(f"Unknown home team: {h}")
            continue
        if a not in teams:
            errors.append(f"Unknown away team: {a}")
            continue

        home_counts[h] += 1
        away_counts[a] += 1

        # Check: no team plays twice in the same slot block (Mon-Fri or Sat-Sun)
        skey = _slot_block_key(game.date)
        games_per_slot[h][skey] += 1
        games_per_slot[a][skey] += 1

        # Track matchups
        key_ha = (h, a) if h < a else (a, h)
        matchup_counts[key_ha[0]][key_ha[1]] += 1

        # Check blackout dates
        h_league = leagues[teams[h].league_code]
        a_league = leagues[teams[a].league_code]
        if h_league.is_blacked_out(game.date):
            errors.append(
                f"{h} plays on blackout date {game.date} "
                f"(league {h_league.code})"
            )
        if a_league.is_blacked_out(game.date):
            errors.append(
                f"{a} plays on blackout date {game.date} "
                f"(league {a_league.code})"
            )

        # Check no-play-days
        dow = DayOfWeek(game.date.weekday())
        if dow in teams[h].no_play_days:
            errors.append(f"{h} plays on {dow.name} ({game.date}) — no-play day")
        if dow in teams[a].no_play_days:
            errors.append(f"{a} plays on {dow.name} ({game.date}) — no-play day")

        # Check weekday-only teams on weekends
        if dow.is_weekend():
            if teams[h].weekday_only:
                if game.date not in teams[h].available_weekends:
                    errors.append(
                        f"{h} (weekday-only) plays on weekend {game.date} "
                        f"without it being an available weekend"
                    )
            if teams[a].weekday_only:
                if game.date not in teams[a].available_weekends:
                    errors.append(
                        f"{a} (weekday-only) plays on weekend {game.date} "
                        f"without it being an available weekend"
                    )

        # Check game type vs pool membership
        if game.game_type == "intra":
            if not (h in north and a in north) and not (h in south and a in south):
                warnings.append(
                    f"Intra-pool game {h} vs {a} has teams from different pools"
                )
        elif game.game_type == "crossover":
            if (h in north and a in north) or (h in south and a in south):
                warnings.append(
                    f"Crossover game {h} vs {a} has teams from same pool"
                )

        # Rule 1: field must belong to home or away team's league
        if game.field_name:
            h_fields = league_fields.get(teams[h].league_code, set())
            a_fields = league_fields.get(teams[a].league_code, set())
            if game.field_name not in h_fields and game.field_name not in a_fields:
                errors.append(
                    f"Game {h} vs {a} on {game.date} uses field "
                    f"{game.field_name} which belongs to neither team's league"
                )

        # Track per-team (date, field) for avoid-same-day checks
        team_date_field[h].append((game.date, game.field_name))
        team_date_field[a].append((game.date, game.field_name))

    # Check: no team plays twice in same slot block (Mon-Fri or Sat-Sun)
    for team, slot_counts in games_per_slot.items():
        for skey, count in slot_counts.items():
            if count > 1:
                week, block = skey
                errors.append(
                    f"{team} plays {count} games in week {week} {block}"
                )

    # Check: home/away balance within 1
    for t in teams:
        h = home_counts.get(t, 0)
        a = away_counts.get(t, 0)
        if abs(h - a) > 1:
            errors.append(
                f"{t} home/away imbalance: {h}H/{a}A (diff={h-a})"
            )

    # Check: avoid_same_time groups — same date + different field is a warning
    ast_groups = avoid_same_time_groups or []
    checked_pairs: set[tuple[str, str, date]] = set()
    for group in ast_groups:
        group_sorted = sorted(group)
        for i, t1 in enumerate(group_sorted):
            for t2 in group_sorted[i + 1:]:
                # Build date -> set of fields for each team
                t1_dates: dict[date, set[str]] = defaultdict(set)
                for d, f in team_date_field.get(t1, []):
                    t1_dates[d].add(f)
                t2_dates: dict[date, set[str]] = defaultdict(set)
                for d, f in team_date_field.get(t2, []):
                    t2_dates[d].add(f)
                for d in t1_dates:
                    if d in t2_dates:
                        # Same date — check if all games are at same field
                        all_fields = t1_dates[d] | t2_dates[d]
                        if len(all_fields) > 1:
                            warnings.append(
                                f"Teams {{{t1}, {t2}}} play same day "
                                f"{d} at different fields "
                                f"{sorted(all_fields)} "
                                f"(avoid_same_time group)"
                            )

    # Rule 3: max 1 team with a BYE per slot
    # BYE = team was available but not assigned a game. Blackout != bye.
    # Teams with unscheduled games in a slot are NOT on bye — they were
    # assigned a game that couldn't be placed on a field.
    # Group scheduled games by (week_number, weekday|weekend) slot
    slot_teams: dict[tuple[int, str], set[str]] = defaultdict(set)
    slot_dates: dict[tuple[int, str], list[date]] = defaultdict(list)
    for game in scheduled_games:
        block = "weekend" if game.date.weekday() >= 5 else "weekday"
        skey = (game.week_number, block)
        slot_teams[skey].add(game.home_team)
        slot_teams[skey].add(game.away_team)
        slot_dates[skey].append(game.date)

    # Track which teams have unscheduled games per slot
    unsched_slot_teams: dict[tuple[int, str], set[str]] = defaultdict(set)
    for game in unscheduled_games:
        block = game.slot_type if game.slot_type else "weekend"
        skey = (game.week_number, block)
        unsched_slot_teams[skey].add(game.home_team)
        unsched_slot_teams[skey].add(game.away_team)
        # Ensure the slot exists in slot_teams/slot_dates even if it
        # has no scheduled games (so Rule 4 iterates over it)
        if skey not in slot_teams:
            slot_teams[skey] = set()

    for skey, playing in slot_teams.items():
        week, block = skey
        dates = slot_dates.get(skey, [])
        if not dates:
            continue
        # Determine which teams were available in this slot
        available = set()
        for t in teams:
            team_obj = teams[t]
            league = leagues[team_obj.league_code]
            # Skip weekday-only teams for weekend slots
            if block == "weekend" and team_obj.weekday_only:
                if not any(d in team_obj.available_weekends for d in dates):
                    continue
            # Skip blacked-out teams
            if all(league.is_blacked_out(d) for d in dates):
                continue
            available.add(t)
        # Exclude teams with unscheduled games — they're not on bye
        bye_teams = available - playing - unsched_slot_teams.get(skey, set())
        if len(bye_teams) > 1:
            errors.append(
                f"Week {week} {block}: {len(bye_teams)} teams have byes "
                f"({', '.join(sorted(bye_teams))}), max is 1"
            )

    # Rule 4: bye spread <= 1 (only non-blackout byes count)
    # A bye = team was available in a slot but had no game (scheduled or unscheduled)
    team_bye_counts: dict[str, int] = defaultdict(int)
    for skey in slot_teams:
        week, block = skey
        dates = slot_dates.get(skey, [])
        if not dates:
            continue
        playing = slot_teams[skey]
        unsched_in_slot = unsched_slot_teams.get(skey, set())
        for t in teams:
            if t in playing or t in unsched_in_slot:
                continue
            team_obj = teams[t]
            league = leagues[team_obj.league_code]
            if block == "weekend" and team_obj.weekday_only:
                if not any(d in team_obj.available_weekends for d in dates):
                    continue
            if all(league.is_blacked_out(d) for d in dates):
                continue
            # This team was available but didn't play — it's a bye
            team_bye_counts[t] += 1

    if team_bye_counts:
        min_byes = min(team_bye_counts.get(t, 0) for t in teams)
        max_byes = max(team_bye_counts.get(t, 0) for t in teams)
        if max_byes - min_byes > 1:
            over_teams = [
                f"{t}({team_bye_counts.get(t, 0)})"
                for t in sorted(teams)
                if team_bye_counts.get(t, 0) > min_byes + 1
            ]
            errors.append(
                f"Bye spread {max_byes - min_byes} exceeds limit of 1: "
                f"min={min_byes}, max={max_byes}. "
                f"Over limit: {', '.join(over_teams)}"
            )

    # Check: matchup coverage — flag any pair that played 2+ times
    all_team_list = sorted(teams.keys())
    for i, t1 in enumerate(all_team_list):
        for t2 in all_team_list[i + 1:]:
            count = matchup_counts.get(t1, {}).get(t2, 0)
            if count > 1:
                # Determine if same-pool or cross-pool
                both_north = t1 in north and t2 in north
                both_south = t1 in south and t2 in south
                if both_north or both_south:
                    label = "Intra-pool pair"
                else:
                    label = "Cross-pool pair"
                warnings.append(
                    f"{label} {t1} vs {t2} played {count} times"
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def format_validation_report(result: dict) -> str:
    """Format validation results as text."""
    lines = []
    lines.append("=" * 60)
    lines.append("SCHEDULE VALIDATION REPORT")
    lines.append("=" * 60)

    if result["valid"]:
        lines.append("\nRESULT: VALID (no hard constraint violations)")
    else:
        lines.append(f"\nRESULT: INVALID ({len(result['errors'])} violations)")

    if result["errors"]:
        lines.append(f"\n--- ERRORS ({len(result['errors'])}) ---")
        for e in result["errors"]:
            lines.append(f"  ERROR: {e}")

    if result["warnings"]:
        lines.append(f"\n--- WARNINGS ({len(result['warnings'])}) ---")
        for w in result["warnings"]:
            lines.append(f"  WARN: {w}")

    return "\n".join(lines)
