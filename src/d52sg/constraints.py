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

    # Track per-team stats
    home_counts = defaultdict(int)
    away_counts = defaultdict(int)
    games_per_slot = defaultdict(lambda: defaultdict(int))  # team -> slot_block_key -> count
    matchup_counts = defaultdict(lambda: defaultdict(int))


    # Track same-league same-time conflicts
    # (kept for potential future use)

    # Track per-team (date, field) for avoid-same-day-different-field checks
    team_date_field: dict[str, list[tuple]] = defaultdict(list)

    for game in games:
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

    # Check: game count balance (weekday and weekend separately)
    # Categorize by actual day of week, not game_type (ad-hoc same-pool
    # games on weekends have game_type="intra" but are weekend games).
    wd_counts: dict[str, int] = defaultdict(int)
    we_counts: dict[str, int] = defaultdict(int)
    for game in games:
        if game.date.weekday() < 5:
            wd_counts[game.home_team] += 1
            wd_counts[game.away_team] += 1
        else:
            we_counts[game.home_team] += 1
            we_counts[game.away_team] += 1

    regular_teams = [t for t in teams if not teams[t].weekday_only]
    all_team_codes = list(teams.keys())

    if all_team_codes:
        wd_vals = [wd_counts.get(t, 0) for t in all_team_codes]
        if max(wd_vals) - min(wd_vals) > 1:
            warnings.append(
                f"Weekday game count spread: "
                f"{min(wd_vals)}-{max(wd_vals)} (overflow rounds)"
            )
    if regular_teams:
        we_vals = [we_counts.get(t, 0) for t in regular_teams]
        if max(we_vals) - min(we_vals) > 1:
            errors.append(
                f"Weekend game count spread too wide: "
                f"{min(we_vals)}-{max(we_vals)} (max spread 1)"
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
