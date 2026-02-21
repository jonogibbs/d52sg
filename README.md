# d52sg — D52 Schedule Generator

Generates balanced round-robin schedules for the D52 Juniors 54/80 baseball league. Takes a YAML config describing teams, leagues, fields, and blackout dates, and produces human-readable schedules, GameChanger/SIPlay upload CSVs, and HTML visualizations.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+. Runtime dependency: `pyyaml`.

## Usage

### Generate a schedule

```bash
d52sg                              # random seed, reads config.yaml in CWD
d52sg --seed 42                    # reproducible output
d52sg --seed 42 -o spring2026      # write to spring2026/ instead of output/
d52sg custom.yaml --seed 7         # use a different config file
```

Output files written to the output directory:

| File | Contents |
|------|----------|
| `schedule.txt` | Human-readable schedule (week view + per-team view) |
| `schedule.html` | Interactive HTML schedule |
| `gamechanger.csv` | GameChanger/SIPlay upload CSV |
| `stats.txt` | Constraint validation report + balance statistics |

The generator prints a summary of any constraint violations to stdout. Exit code is 0 if valid, 1 if hard violations exist.

### Verify an existing schedule

```bash
d52sg --verify output/gamechanger.csv          # uses config.yaml in CWD
d52sg-verify output/gamechanger.csv config.yaml
```

Re-reads a previously generated GameChanger CSV and runs full constraint validation against the config.

### Generate a config report

```bash
d52sg-config-report                            # prints to stdout
d52sg-config-report config.yaml output/        # writes config_report.txt + config_report.html
```

Produces a summary of the config: leagues, teams, field slots, blackout dates, and pool assignments. Useful for catching config mistakes before generating a schedule.

## Config file

The config file (`config.yaml`) describes the season. Key sections:

```yaml
season:
  name: "D52 Juniors 54/80 Schedule"
  start_date: "2026-03-07"
  end_date: "2026-05-16"
  game_length_minutes: 150
  game_code_prefix: "AP"

leagues:
  ALP:
    full_name: "Alpine/West Menlo"
    teams: 2
    blackout_dates:
      - "2026-04-04:2026-04-12"   # date range
      - "2026-03-21"              # single date
    weekday_fields:
      - day: Tue
        field: "Nealon"
        time: "6pm"
        exclude_dates: [2026-05-05]   # optional per-field exclusions
    weekend_fields:
      - day: Sat
        field: "Nealon"
        time: "12pm"

pools:
  north: [PAC1, PAC2, ...]
  south: [ALP1, ALP2, ...]

team_overrides:
  MA1:
    no_play_days: [Mon, Thu]
  HIL1:
    weekday_only: true
    available_weekends: [2026-04-12, 2026-05-09]
  RAV1:
    gamechanger_name: "Ravenswood"   # override name in CSV output

avoid_same_time_groups:
  - [BRS1, BRS2]    # these teams must not play simultaneously

fields:
  "Nealon":
    map_url: "https://maps.app.goo.gl/..."
```

Teams are named `{LEAGUE_CODE}{N}` (e.g. `ALP1`, `ALP2`, `PAC1`). Leagues with `teams: N` auto-generate team codes; leagues with `teams: [T1, T2]` use explicit codes.

## Scheduling algorithm

The scheduler runs in five phases:

1. **Calendar construction** — builds weekday and weekend slots for each week, marking which teams are available in each slot based on blackouts and overrides
2. **Round assignment** — assigns abstract round-robin rounds to calendar slots (greedy with backtracking)
3. **Field and time assignment** — assigns a concrete field and start time to each game
4. **Home/away balancing** — assigns home/away so each team's counts differ by at most 1
5. **Trimming** — removes excess games if game count spread exceeds 1 (rare safety net)

Intra-pool games (teams from the same pool) are scheduled on weekdays; crossover games (north vs. south) on weekends.

## Constraints enforced

- No team plays more than once per slot block (Mon–Fri or Sat–Sun)
- No team plays on a league blackout date
- No team plays on a `no_play_days` day (e.g. Menlo-Atherton: no Mon/Thu)
- Weekday-only teams do not appear on weekends (except listed `available_weekends`)
- Teams with no home fields (RAV) always appear as the away team
- Teams in `avoid_same_time_groups` do not play at the same time
- Home/away game counts differ by at most 1 per team
- Intra-pool rounds contain only same-pool matchups; crossover rounds only cross-pool matchups
