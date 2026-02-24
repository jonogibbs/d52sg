#!/usr/bin/env python3
"""Scan seeds to find schedules with no duplicate matchups and no unscheduled games.

Usage: d52sg-scan [config.yaml] [-n MAX_SEED]
"""

import argparse
import csv
import io
import sys
from pathlib import Path

from d52sg.config import load_config
from d52sg.scheduler import schedule
from d52sg.constraints import validate_schedule


def scan_seed(config: dict, seed: int) -> dict:
    """Run a single seed and return summary info."""
    # Suppress scheduler's verbose output
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        games = schedule(config, seed=seed)
    finally:
        sys.stdout = old_stdout

    if not games:
        return {"seed": seed, "ok": False, "error": "no games"}

    scheduled = [g for g in games if not g.unscheduled]
    unscheduled = [g for g in games if g.unscheduled]

    # Check for duplicate matchups
    from collections import defaultdict
    matchup_counts = defaultdict(int)
    for g in scheduled:
        key = (min(g.home_team, g.away_team), max(g.home_team, g.away_team))
        matchup_counts[key] += 1
    duplicates = {k: v for k, v in matchup_counts.items() if v > 1}

    return {
        "seed": seed,
        "ok": len(duplicates) == 0 and len(unscheduled) == 0,
        "games": len(scheduled),
        "unscheduled": len(unscheduled),
        "duplicates": len(duplicates),
        "duplicate_pairs": duplicates,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scan seeds to find schedules with no duplicates "
                    "and no unscheduled games",
    )
    parser.add_argument(
        "config", nargs="?", default="config.yaml",
        help="Path to config YAML file (default: config.yaml)"
    )
    parser.add_argument(
        "-n", "--max-seed", type=int, default=100,
        help="Maximum seed to try (default: 100, scans 0..N-1)"
    )
    args = parser.parse_args()

    config_path = args.config
    if not Path(config_path).exists():
        print(f"Error: config file {config_path} not found")
        sys.exit(1)

    config = load_config(config_path)
    max_seed = args.max_seed

    print(f"Scanning seeds 0..{max_seed - 1} using {config_path}...")
    print(f"{'Seed':>6}  {'Games':>5}  {'Unsched':>7}  {'Dupes':>5}  Result")
    print("-" * 50)

    good_seeds = []
    for seed in range(max_seed):
        result = scan_seed(config, seed)
        status = "OK" if result["ok"] else "FAIL"
        unsched = result.get("unscheduled", "?")
        dupes = result.get("duplicates", "?")
        games = result.get("games", "?")
        print(f"{seed:>6}  {games:>5}  {unsched:>7}  {dupes:>5}  {status}", flush=True)
        if result["ok"]:
            good_seeds.append(seed)

    print("-" * 50)
    if good_seeds:
        print(f"\nGood seeds ({len(good_seeds)}/{max_seed}): "
              f"{', '.join(str(s) for s in good_seeds)}")
    else:
        print(f"\nNo good seeds found in 0..{max_seed - 1}")

    sys.exit(0 if good_seeds else 1)


if __name__ == "__main__":
    main()
