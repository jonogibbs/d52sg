"""Output formatters for D52 scheduling app."""

import csv
from datetime import date, time
from io import StringIO
from pathlib import Path
from d52sg.models import Game


def format_schedule(games: list[Game], teams: dict) -> str:
    """Format schedule as human-readable text, organized by week."""
    lines = []
    lines.append("=" * 80)
    lines.append("D52 JUNIORS 54/80 SCHEDULE")
    lines.append("=" * 80)

    # Group by week
    by_week: dict[int, list[Game]] = {}
    for g in games:
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

    # Per-team schedule
    lines.append("\n" + "=" * 80)
    lines.append("PER-TEAM SCHEDULES")
    lines.append("=" * 80)

    by_team: dict[str, list[Game]] = {}
    for g in games:
        by_team.setdefault(g.home_team, []).append(g)
        by_team.setdefault(g.away_team, []).append(g)

    for team_code in sorted(by_team.keys()):
        team_games = sorted(by_team[team_code], key=lambda g: g.date)
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

    for g in sorted(games, key=lambda x: (x.date, x.start_time)):
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


def write_schedule(games: list[Game], teams: dict,
                   game_length: int, output_prefix: str = "output"):
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
