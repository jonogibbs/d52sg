"""Convert an editable schedule CSV to GameChanger CSV format.

Usage: d52sg-convert <schedule_edit.csv> [config.yaml] [-o output.csv]
"""

import argparse
import csv
import re
import sys
from datetime import date, time, datetime, timedelta
from pathlib import Path

from d52sg.config import load_config
from d52sg.models import Game
from d52sg.output import format_gamechanger_csv


def _parse_time_12h(s: str) -> time:
    """Parse '5:00pm', '10:00am', '1:30pm' etc. into a time object."""
    s = s.strip().lower()
    m = re.match(r'^(\d{1,2}):(\d{2})(am|pm)$', s)
    if not m:
        raise ValueError(f"Cannot parse time: {s!r}")
    hour, minute, period = int(m.group(1)), int(m.group(2)), m.group(3)
    if period == "am" and hour == 12:
        hour = 0
    elif period == "pm" and hour != 12:
        hour += 12
    return time(hour, minute)


def parse_editable_csv(csv_path: str, config: dict) -> list[Game]:
    """Parse an editable CSV (Game,Date,Day,Time,Home,Away,Field) into Games."""
    teams = config["teams"]
    leagues = config["leagues"]
    season_start = config["season"]["start_date"]
    game_length = config["season"]["game_length_minutes"]

    # Infer the year from season config
    season_year = season_start.year

    # Build field -> league lookup for host_team determination
    field_to_leagues: dict[str, list[str]] = {}
    for lcode, league in leagues.items():
        for fs in league.weekday_fields + league.weekend_fields:
            field_to_leagues.setdefault(fs.field_name, []).append(lcode)

    games = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            home = row["Home"].strip()
            away = row["Away"].strip()
            field_name = row["Field"].strip()

            if not home or not away:
                continue

            # Parse date (M/D format, infer year from season)
            date_str = row["Date"].strip()
            parts = date_str.split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                # If the month is before the season start month, it might
                # be in the next year (e.g., season starts in Oct, game in Jan)
                game_date = date(season_year, month, day)
                if game_date < season_start - timedelta(days=30):
                    game_date = date(season_year + 1, month, day)
            elif len(parts) == 3:
                month, day, yr = int(parts[0]), int(parts[1]), int(parts[2])
                if yr < 100:
                    yr += 2000
                game_date = date(yr, month, day)
            else:
                print(f"Warning: cannot parse date {date_str!r}, skipping row")
                continue

            # Parse time
            time_str = row["Time"].strip()
            try:
                start_time = _parse_time_12h(time_str)
            except ValueError as e:
                print(f"Warning: {e}, skipping row")
                continue

            # Compute end time
            start_dt = datetime.combine(game_date, start_time)
            end_dt = start_dt + timedelta(minutes=game_length)
            end_time = end_dt.time()

            # Determine game type from pool membership
            home_pool = teams[home].pool if home in teams else None
            away_pool = teams[away].pool if away in teams else None
            game_type = "crossover" if home_pool != away_pool else "intra"

            # Compute week number
            delta = (game_date - season_start).days
            week_num = max(1, delta // 7 + 1)

            # Determine host_team from field ownership
            host_team = home  # default
            if field_name and home in teams:
                home_league = teams[home].league_code
                home_fields = set()
                for fs in (leagues[home_league].weekday_fields +
                           leagues[home_league].weekend_fields):
                    home_fields.add(fs.field_name)
                if field_name not in home_fields and away in teams:
                    away_league = teams[away].league_code
                    away_fields = set()
                    for fs in (leagues[away_league].weekday_fields +
                               leagues[away_league].weekend_fields):
                        away_fields.add(fs.field_name)
                    if field_name in away_fields:
                        host_team = away

            games.append(Game(
                home_team=home,
                away_team=away,
                host_team=host_team,
                date=game_date,
                start_time=start_time,
                end_time=end_time,
                field_name=field_name,
                round_number=0,
                game_type=game_type,
                week_number=week_num,
            ))

    return games


def main():
    parser = argparse.ArgumentParser(
        description="Convert editable schedule CSV to GameChanger CSV",
    )
    parser.add_argument(
        "csv", help="Path to the editable schedule CSV (schedule_edit.csv)"
    )
    parser.add_argument(
        "config", nargs="?", default="config.yaml",
        help="Path to config YAML file (default: config.yaml)"
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output path for GameChanger CSV (default: gamechanger.csv "
             "in same directory as input)"
    )
    args = parser.parse_args()

    csv_path = args.csv
    if not Path(csv_path).exists():
        print(f"Error: {csv_path} not found")
        sys.exit(1)

    config_path = args.config
    if not Path(config_path).exists():
        print(f"Error: config file {config_path} not found")
        sys.exit(1)

    print(f"Loading config from {config_path}...")
    config = load_config(config_path)

    print(f"Parsing schedule from {csv_path}...")
    games = parse_editable_csv(csv_path, config)
    print(f"Loaded {len(games)} games")

    if not games:
        print("No games found in CSV. Check the format.")
        sys.exit(1)

    # Convert to GameChanger format
    gc_csv = format_gamechanger_csv(
        games, config["season"]["game_length_minutes"],
        teams=config["teams"],
    )

    # Write output
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = Path(csv_path).parent / "gamechanger.csv"

    out_path.write_text(gc_csv)
    print(f"Written: {out_path} ({len(games)} games)")


if __name__ == "__main__":
    main()
