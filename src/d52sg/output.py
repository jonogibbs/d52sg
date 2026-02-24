"""Output formatters for D52 scheduling app."""

import csv
from datetime import date, time
from io import StringIO
from pathlib import Path
from d52sg.models import Game


def format_schedule(games: list[Game], teams: dict) -> str:
    """Format schedule as human-readable text, organized by week."""
    scheduled = [g for g in games if not g.unscheduled]
    unscheduled = [g for g in games if g.unscheduled]

    lines = []
    lines.append("=" * 80)
    lines.append("D52 JUNIORS 54/80 SCHEDULE")
    lines.append("=" * 80)

    # Group by week
    by_week: dict[int, list[Game]] = {}
    for g in scheduled:
        by_week.setdefault(g.week_number, []).append(g)

    for week_num in sorted(by_week.keys()):
        week_games = sorted(by_week[week_num], key=lambda g: (g.date, g.start_time))
        lines.append(f"\n--- WEEK {week_num} ---")

        # Group by date within week
        by_date: dict[date, list[Game]] = {}
        for g in week_games:
            by_date.setdefault(g.date, []).append(g)

        for d in sorted(by_date.keys()):
            day_name = d.strftime("%A")
            lines.append(f"\n  {day_name} {d.strftime('%m/%d/%Y')}")
            for g in sorted(by_date[d], key=lambda x: x.start_time):
                start = g.start_time.strftime("%-I:%M%p").lower()
                host_note = ""
                if g.host_team != g.home_team:
                    host_note = f" (at {g.host_team})"
                game_type = "X" if g.game_type == "crossover" else " "
                lines.append(
                    f"    [{game_type}] {start:>7}  {g.home_team:<6} vs {g.away_team:<6}  "
                    f"@ {g.field_name}{host_note}"
                )

    # Unscheduled games section
    if unscheduled:
        lines.append(f"\n{'=' * 80}")
        lines.append(f"UNSCHEDULED GAMES ({len(unscheduled)})")
        lines.append("=" * 80)
        for g in unscheduled:
            gt = "X" if g.game_type == "crossover" else " "
            lines.append(
                f"  [{gt}] {g.home_team:<6} vs {g.away_team:<6}  (Week {g.week_number} {'WD' if g.slot_type == 'weekday' else 'WE'})"
            )

    # Per-team schedule
    lines.append("\n" + "=" * 80)
    lines.append("PER-TEAM SCHEDULES")
    lines.append("=" * 80)

    by_team: dict[str, list[Game]] = {}
    for g in scheduled:
        by_team.setdefault(g.home_team, []).append(g)
        by_team.setdefault(g.away_team, []).append(g)

    # Also track unscheduled per team
    unsched_by_team: dict[str, list[Game]] = {}
    for g in unscheduled:
        unsched_by_team.setdefault(g.home_team, []).append(g)
        unsched_by_team.setdefault(g.away_team, []).append(g)

    all_team_codes = sorted(set(list(by_team.keys()) + list(unsched_by_team.keys())))
    for team_code in all_team_codes:
        team_games = sorted(by_team.get(team_code, []), key=lambda g: g.date)
        lines.append(f"\n{team_code}:")
        for i, g in enumerate(team_games, 1):
            is_home = g.home_team == team_code
            opponent = g.away_team if is_home else g.home_team
            h_a = "H" if is_home else "V"
            day = g.date.strftime("%a %m/%d")
            start = g.start_time.strftime("%-I:%M%p").lower()
            gt = "X" if g.game_type == "crossover" else " "
            lines.append(
                f"  {i:>2}. {day} {start:>7} {h_a} vs {opponent:<6} "
                f"@ {g.field_name} [{gt}]"
            )
        for g in unsched_by_team.get(team_code, []):
            opponent = g.away_team if g.home_team == team_code else g.home_team
            gt = "X" if g.game_type == "crossover" else " "
            lines.append(
                f"      UNSCHEDULED    vs {opponent:<6}  (Week {g.week_number} {'WD' if g.slot_type == 'weekday' else 'WE'}) [{gt}]"
            )

    return "\n".join(lines)


def format_gamechanger_csv(games: list[Game], game_length: int,
                           teams: dict | None = None) -> str:
    """Format schedule as GameChanger upload CSV."""
    output = StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Start_Date", "Start_Time", "End_Date", "End_Time",
        "Title", "Description", "Location", "Location_URL",
        "Location_Details", "All_Day_Event", "Event_Type", "Tags",
        "Team1_ID", "Team1_Division_ID", "Team1_Is_Home",
        "Team2_ID", "Team2_Division_ID", "Team2_Name",
        "Custom_Opponent", "Event_ID", "Game_ID",
        "Affects_Standings", "Points_Win", "Points_Loss",
        "Points_Tie", "Points_OT_Win", "Points_OT_Loss",
        "Division_Override",
    ])

    def _gc_name(code: str) -> str:
        if teams and code in teams and teams[code].gamechanger_name:
            return teams[code].gamechanger_name
        return code

    for g in sorted((g for g in games if not g.unscheduled),
                     key=lambda x: (x.date, x.start_time)):
        start_date = g.date.strftime("%-m/%-d/%y")
        start_time = f"{g.start_time.hour}:{g.start_time.minute:02d}"
        end_date = start_date
        end_time = f"{g.end_time.hour}:{g.end_time.minute:02d}"

        writer.writerow([
            start_date, start_time, end_date, end_time,
            "", "", "", "", "", "", "", "",
            _gc_name(g.home_team), "", "",
            _gc_name(g.away_team), "", "",
            "", "", "",
            "", "", "", "", "", "", "",
        ])

    return output.getvalue()


def _fmt_time_12h(t: time) -> str:
    """Format a time as 12-hour with am/pm (e.g., '5:00pm', '10:00am')."""
    h = t.hour
    m = t.minute
    suffix = "am" if h < 12 else "pm"
    if h == 0:
        h = 12
    elif h > 12:
        h -= 12
    if m == 0:
        return f"{h}:{m:02d}{suffix}"
    return f"{h}:{m:02d}{suffix}"


def format_editable_csv(games: list[Game],
                        game_code_prefix: str = "G") -> str:
    """Format schedule as a human-friendly editable CSV.

    Columns: Game, Date, Day, Time, Home, Away, Field
    """
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Game", "Date", "Day", "Time", "Home", "Away", "Field"])

    scheduled = sorted((g for g in games if not g.unscheduled),
                       key=lambda g: (g.date, g.start_time, g.home_team))

    for i, g in enumerate(scheduled, 1):
        code = f"{game_code_prefix}{i}"
        date_str = g.date.strftime("%-m/%-d")
        day_str = g.date.strftime("%a")
        time_str = _fmt_time_12h(g.start_time)
        writer.writerow([code, date_str, day_str, time_str,
                         g.home_team, g.away_team, g.field_name])

    return output.getvalue()


def write_schedule(games: list[Game], teams: dict,
                   game_length: int, output_prefix: str = "output",
                   game_code_prefix: str = "G"):
    """Write all output files into {output_prefix}/ directory."""
    out_dir = Path(output_prefix)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Human-readable schedule
    schedule_text = format_schedule(games, teams)
    schedule_path = out_dir / "schedule.txt"
    schedule_path.write_text(schedule_text)
    print(f"Written: {schedule_path}")

    # GameChanger CSV
    csv_text = format_gamechanger_csv(games, game_length, teams=teams)
    csv_path = out_dir / "gamechanger.csv"
    csv_path.write_text(csv_text)
    print(f"Written: {csv_path}")

    # Editable CSV
    edit_csv = format_editable_csv(games, game_code_prefix=game_code_prefix)
    edit_path = out_dir / "schedule_edit.csv"
    edit_path.write_text(edit_csv)
    print(f"Written: {edit_path}")
