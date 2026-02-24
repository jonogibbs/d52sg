"""Standalone verifier for D52 scheduling app.

Can validate a schedule by reading a CSV file + config.yaml.
Usage: python verify.py [schedule_csv] [config_yaml]
"""

import csv
import sys
from datetime import date, time
from pathlib import Path

from d52sg.config import load_config
from d52sg.constraints import validate_schedule, format_validation_report
from d52sg.models import Game, DayOfWeek
from d52sg.stats import compute_stats, format_stats_report


def parse_csv_schedule(csv_path: str, config: dict) -> list[Game]:
    """Parse a GameChanger CSV or human-readable CSV back into Game objects."""
    games = []
    teams = config["teams"]

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Try GameChanger format
            if "Start_Date" in row and "Team1_ID" in row:
                start_date_str = row.get("Start_Date", "").strip()
                if not start_date_str:
                    continue

                home = row.get("Team1_ID", "").strip()
                away = row.get("Team2_ID", "").strip()
                if not home or not away:
                    continue

                # Parse date (M/D/YY format)
                parts = start_date_str.split("/")
                if len(parts) == 3:
                    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if y < 100:
                        y += 2000
                    game_date = date(y, m, d)
                else:
                    continue

                # Parse time
                time_str = row.get("Start_Time", "17:30").strip()
                if ":" in time_str:
                    tp = time_str.split(":")
                    game_time = time(int(tp[0]), int(tp[1]))
                else:
                    game_time = time(17, 30)

                end_str = row.get("End_Time", "20:00").strip()
                if ":" in end_str:
                    tp = end_str.split(":")
                    end_time = time(int(tp[0]), int(tp[1]))
                else:
                    end_time = time(20, 0)

                # Determine game type
                home_pool = teams[home].pool if home in teams else None
                away_pool = teams[away].pool if away in teams else None
                game_type = "crossover" if home_pool != away_pool else "intra"

                # Calculate week number
                season_start = config["season"]["start_date"]
                delta = (game_date - season_start).days
                week_num = max(1, delta // 7 + 1)

                games.append(Game(
                    home_team=home,
                    away_team=away,
                    host_team=home,  # assume home = host from CSV
                    date=game_date,
                    start_time=game_time,
                    end_time=end_time,
                    field_name="",
                    round_number=0,
                    game_type=game_type,
                    week_number=week_num,
                ))

    return games


def main():
    if len(sys.argv) < 2:
        print("Usage: python verify.py <schedule.csv> [config.yaml]")
        print("  Validates a schedule CSV against constraints in config.")
        sys.exit(1)

    csv_path = sys.argv[1]
    config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"

    if not Path(csv_path).exists():
        print(f"Error: {csv_path} not found")
        sys.exit(1)
    if not Path(config_path).exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    print(f"Loading config from {config_path}...")
    config = load_config(config_path)

    print(f"Parsing schedule from {csv_path}...")
    games = parse_csv_schedule(csv_path, config)
    print(f"Loaded {len(games)} games")

    if not games:
        print("No games found in CSV. Check the format.")
        sys.exit(1)

    # Validate
    result = validate_schedule(
        games, config["teams"], config["leagues"], config["pools"],
        avoid_same_time_groups=config.get("avoid_same_time_groups"),
    )
    print(format_validation_report(result))

    # Stats
    stats = compute_stats(
        games, config["teams"], config["leagues"], config["pools"]
    )
    print("\n" + format_stats_report(
        stats, config["teams"], config["leagues"], config["pools"]
    ))


if __name__ == "__main__":
    main()
