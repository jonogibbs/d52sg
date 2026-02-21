# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install (dev mode):**
```bash
pip install -e .
```

**Generate a config report:**
```bash
d52sg-config-report                        # prints to stdout (uses config.yaml)
d52sg-config-report config.yaml output/   # writes config_report.txt + config_report.html
```

**Generate a schedule:**
```bash
d52sg                              # random seed, default config.yaml
d52sg --seed 42 -o my_output       # reproducible, named output dir
d52sg custom.yaml --seed 7         # custom config
```

**Verify an existing schedule:**
```bash
d52sg --verify output/gamechanger.csv
d52sg-verify output/gamechanger.csv config.yaml
```

**Run tests:**
```bash
pytest -v                                  # all tests with details
pytest -q                                  # quiet
pytest tests/test_integration.py -v        # single file
```

## Architecture

The tool generates round-robin baseball schedules from a YAML config and writes multiple output formats (`.txt`, `.html`, `gamechanger.csv`, `stats.txt`).

**Data flow:**
1. `config.py` — parses `config.yaml` into teams, leagues, pools, field slots, blackout ranges
2. `roundrobin.py` — generates abstract round-robin pairings (intra-pool via circle method; crossover for north vs. south)
3. `scheduler.py` — the core engine; assigns abstract rounds to concrete dates/fields in 5 phases:
   - Build a calendar of weekday and weekend slots with team availability
   - Assign rounds to calendar slots (greedy + backtracking)
   - Assign fields and times to each game
   - Balance home/away counts
   - Trim excess games if imbalance exceeds threshold
4. `constraints.py` — validates the final game list against all hard constraints; returns violations + warnings
5. `stats.py` — computes per-team and per-league statistics from the game list
6. `output.py` / `output_html.py` / `config_report.py` — format and write the outputs

**Key models** (`models.py`): `Team`, `League`, `FieldSlot`, `Matchup`, `Round`, `Game`, `CalendarSlot`.

**Config structure** (`config.yaml`):
- `season` — date range, game length, code prefix
- `leagues` — per-league teams, blackout date ranges, weekday/weekend field slots
- `pools` — `north`/`south` team lists (drives intra vs. crossover scheduling)
- `team_overrides` — per-team flags: `weekday_only`, `no_play_days`, `available_weekends`, `gamechanger_name`
- `avoid_same_time_groups` — pairs/groups that must not play simultaneously
- `fields` — map URLs per field name

**Special cases to be aware of:**
- Teams with no fields (RAV) always play away; the opponent hosts
- Weekday-only teams (Hillsborough) only appear on weekdays unless listed in `available_weekends`
- Unequal pool sizes produce rotating byes in crossover rounds
- `exclude_dates` on individual field slots blocks specific dates for that field only

**Plans directory** (`plans/`): `plan.md` documents the full algorithm design; `v2.md` describes planned no-pools and multi-division enhancements.
