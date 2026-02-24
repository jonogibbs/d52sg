#!/usr/bin/env python3
"""D52 Juniors 54/80 Schedule Builder.

Generate mode (default):
    python schedule.py [config.yaml] [--seed N] [-o PREFIX]

    Generates a schedule from the YAML config and writes:
      {PREFIX}_schedule.txt   - Human-readable week-by-week + per-team schedule
      {PREFIX}_gamechanger.csv - Upload CSV for GameChanger/SIPlay
      {PREFIX}_stats.txt      - Validation report + statistics

Verify mode:
    python schedule.py --verify <schedule.csv> [config.yaml]

    Re-imports a GameChanger CSV and checks all constraints against config.
    Exit code 0 if valid, 1 if violations found.

Standalone verifier (same as --verify but separate entry point):
    python verify.py <schedule.csv> [config.yaml]

Examples:
    python schedule.py                          # default config, random seed
    python schedule.py --seed 42 -o spring2026  # reproducible, custom prefix
    python schedule.py --verify output_gamechanger.csv
    python schedule.py custom.yaml --seed 7     # alternate config file
"""

import argparse
import sys
from pathlib import Path

from d52sg.config import load_config
from d52sg.scheduler import schedule
from d52sg.constraints import validate_schedule, format_validation_report
from d52sg.stats import compute_stats, format_stats_report
from d52sg.output import write_schedule
from d52sg.output_html import write_schedule_html


def main():
    parser = argparse.ArgumentParser(
        description="D52 Juniors 54/80 Schedule Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Output files (generate mode):
  {prefix}/schedule.txt     Human-readable schedule (week view + per-team)
  {prefix}/schedule.html    HTML schedule (for emailing to league reps)
  {prefix}/gamechanger.csv  GameChanger/SIPlay upload CSV
  {prefix}/stats.txt        Validation report + balance statistics

Exit codes:
  0  Schedule valid (or generation succeeded with no hard violations)
  1  Constraint violations found, or generation error
""",
    )
    parser.add_argument(
        "config", nargs="?", default="config.yaml",
        help="Path to config YAML file (default: config.yaml)"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducible schedules. Try a few seeds and "
             "pick the one with the fewest warnings."
    )
    parser.add_argument(
        "--output-prefix", "-o", default="output",
        help="Output directory for generated files (default: output/)"
    )
    parser.add_argument(
        "--verify", metavar="CSV",
        help="Verify an existing GameChanger CSV instead of generating"
    )
    args = parser.parse_args()

    config_path = args.config
    if not Path(config_path).exists():
        print(f"Error: config file {config_path} not found")
        sys.exit(1)

    print(f"Loading config from {config_path}...")
    config = load_config(config_path)

    if args.verify:
        # Verification mode
        from verify import parse_csv_schedule
        print(f"Verifying schedule from {args.verify}...")
        games = parse_csv_schedule(args.verify, config)
        print(f"Loaded {len(games)} games")

        result = validate_schedule(
            games, config["teams"], config["leagues"], config["pools"],
            avoid_same_time_groups=config.get("avoid_same_time_groups"),
        )
        print(format_validation_report(result))

        stats = compute_stats(
            games, config["teams"], config["leagues"], config["pools"]
        )
        print("\n" + format_stats_report(
            stats, config["teams"], config["leagues"], config["pools"]
        ))
        sys.exit(0 if result["valid"] else 1)

    # Generation mode
    print(f"Generating schedule (seed={args.seed})...")
    games = schedule(config, seed=args.seed)

    if not games:
        print("Error: no games were scheduled!")
        sys.exit(1)

    # Validate
    print("\nValidating...")
    result = validate_schedule(
        games, config["teams"], config["leagues"], config["pools"],
        avoid_same_time_groups=config.get("avoid_same_time_groups"),
    )
    report = format_validation_report(result)
    print(report)

    # Stats
    stats = compute_stats(
        games, config["teams"], config["leagues"], config["pools"]
    )
    stats_text = format_stats_report(
        stats, config["teams"], config["leagues"], config["pools"]
    )
    print("\n" + stats_text)

    # Write outputs
    print("\nWriting output files...")
    write_schedule(
        games, config["teams"],
        config["season"]["game_length_minutes"],
        output_prefix=args.output_prefix,
        game_code_prefix=config["season"].get("game_code_prefix", "G"),
    )
    write_schedule_html(
        games, config["teams"], config["leagues"],
        output_prefix=args.output_prefix,
        field_info=config.get("field_info"),
        pools=config["pools"],
        validation_result=result,
        stats=stats,
        season_name=config["season"].get("name", ""),
        game_code_prefix=config["season"].get("game_code_prefix", "G"),
    )

    # Write stats
    stats_path = Path(args.output_prefix) / "stats.txt"
    stats_path.write_text(report + "\n\n" + stats_text)
    print(f"Written: {stats_path}")

    if result["valid"]:
        print("\nSchedule generated successfully!")
    else:
        print(f"\nSchedule has {len(result['errors'])} constraint violations.")
        print("Review errors above and adjust config or seed.")


if __name__ == "__main__":
    main()
