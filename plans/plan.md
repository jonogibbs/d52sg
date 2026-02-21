# D52 Scheduling App

## Context

Scheduling a D52 youth baseball league (Juniors 54/80) with 25 teams across 13 leagues, split into North (13) and South (12) pools. Currently done via a complex Google Sheets workbook with pre-computed round-robin tables — works but is labor-intensive, especially around spring break blackouts and partial rounds. The goal is a Python CLI tool that takes a YAML config and produces a balanced schedule plus a SIPlay upload CSV.

## 2026 Season Data

**South (12):** AWM1, AWM2, MA, PA1, PA2, RAV, RWC1, RWC2, RWC3, RWC4, SC1, SC2
**North (13):** PAC1, PAC2, PAC3, HLL, SMA1, SMA2, SMN, FC1, FC2, HMB1, HMB2, BRS1, BRS2

**Leagues and Fields:**

Weekday games = 1 game per field slot. Weekend games = 2-3 games stacked per field (~2.5-3hr each).
Default weekday start time: 5:30pm (varies per field, user will refine).

| Code | Full Name | Teams | WD Slots (=WD cap) | WE Slots (=WE cap) | Fields |
|------|-----------|-------|-------|-------|--------|
| AWM | Alpine/West Menlo | AWM1, AWM2 | 2 (Tue, Thu) | 1 (Sat 12-4pm, 1 game) | Nealon Park |
| BRS | Belmont/Redwood Shores | BRS1, BRS2 | 1 (Wed) | 2 (Sun) | Marlin (WD), Ralston (WE) |
| FC | Foster City | FC1, FC2 | 2 (Tue, Wed) | 1 (Sat) | TBD |
| HMB | Half Moon Bay | HMB1, HMB2 | 1 (Tue) | open (multiple Sat/Sun) | Smith Field |
| HLL | Hillsborough | HLL | 0 | 1 (Sun 10am Sea Cloud) | Sea Cloud (WE only) |
| MA | Menlo-Atherton | MA | 1 (Fri) | 2 (Sat) | Nealon (WD), Burgess (WE) |
| PAC | Pacifica | PAC1, PAC2, PAC3 | 2 (Tue, Thu) | all day Sun | Terra Nova (TBD) |
| PA | Palo Alto | PA1, PA2 | 2 (Mon, Tue) | ~4 (2 Sat, 2 Sun) | El Camino |
| RAV | Ravenswood | RAV | 0 | 0 | **No fields** |
| RWC | Redwood City | RWC1-RWC4 | 3 (Tue, Wed, Thu) | 3 (Sat 10am, 1pm, 4pm) | McGarvey |
| SC | San Carlos | SC1, SC2 | 3 (Tue, Wed, Thu 7pm) | all day Sat | Burton/Madsen |
| SMA | San Mateo American | SMA1, SMA2 | 2 (Tue Los Prados, Thu Harborview) | many (Sat LP 2-6, Sat HV 1-6) | Los Prados, Harborview |
| SMN | San Mateo National | SMN | 1 (Mon or Wed) | 1 (Sat 9am) | Harborview |

**Key special cases:**
- **SMN:** Weekday-only team, ~half game count, crossover only on specific available weekends
- **RAV:** No fields, always plays "home" games at opponent's venue
- **HLL:** No weekday field — all weekday games are away; weekend home at Sea Cloud
- **MA:** Cannot play Mon or Thu (firm constraint on all games, home or away)
- **Unequal pools:** 13 North vs 12 South — one North team gets a bye in crossover each weekend, and/or 2 North teams play each other on a weekend

## Architecture

```
sched/
  plans/              # This plan document
  schedule.py         # Main entry point / CLI
  config.py           # Config loading and validation
  models.py           # Data classes: Team, League, Game, Round, Slot, etc.
  roundrobin.py       # Round-robin generation with shuffle + balance
  scheduler.py        # Main scheduling engine (assigns matchups to calendar slots)
  constraints.py      # Constraint checking and validation
  stats.py            # Statistics and balance reporting
  output.py           # Human-readable and CSV output formatters
  verify.py           # Standalone verifier — ingests output CSV + config, reports violations
  config.yaml         # Season config (user edits yearly)
```

Python 3.10+, standard library only (using `dataclasses`, `datetime`, `csv`, `random`). YAML config via `pyyaml` (single dependency).

## Config File Structure (YAML)

See `config.yaml` — will contain all league/team/field/blackout data.
Key design points for the config:
- `weekday_fields` list = weekday home cap (number of entries)
- `weekend_fields` list = weekend home cap (number of entries)
- `has_fields: false` for RAV (and HLL on weekdays — HLL has weekend-only field)
- `team_overrides` for SMN (weekday_only + available_weekends) and MA (no_play_days: [Mon, Thu])
- Blackout dates as date ranges per league

## Core Algorithm

### Step 1: Round-Robin Generation (`roundrobin.py`)

**Intra-pool (weekday):** Separate round-robin for North (13 teams) and South (12 teams).
- Use circle method: for N teams, generates N-1 rounds (12 rounds for 13 teams with rotating bye, 11 rounds for 12 teams)
- Shuffle: randomly assign teams to positions before generating, then randomly permute round order
- This guarantees every team plays every other team in their pool exactly once, but the ordering looks random

**Crossover (weekend):** North-vs-South pairings.
- 13 North × 12 South = need rounds where each North team plays each South team
- With unequal pools: each round has 12 crossover games + 1 North bye (or 1 intra-North game)
- Generate using a systematic approach then shuffle round order

**Home/away is NOT pre-assigned in tables.** Assigned at scheduling time based on cumulative balance — this is what fixes the partial-round problem.

### Step 2: Calendar Construction (`scheduler.py`)

1. From start_date to end_date, build list of calendar weeks
2. Each week has a weekday slot (Mon-Fri) and a weekend slot (Sat-Sun)
3. Mark blackout dates per league → per team
4. For each slot, compute which teams are available

### Step 3: Round-to-Slot Assignment (the hard part)

**Weekday rounds (intra-pool):**
- For each weekday slot, we need to assign a round from the intra-pool round-robin
- Constraint: all teams in the round's matchups must be available that week
- During spring break: group teams by blackout pattern; assign rounds where paired teams share the same availability window
- If a round can't fully fit (some teams blacked out), split it: play available matchups now, defer the rest

**Weekend rounds (crossover):**
- Same logic but for crossover matchups
- SMN skips weekend rounds (except available weekends)
- With 13 N / 12 S, one North team is unmatched each weekend — either bye or plays another North team

**Algorithm:** Greedy assignment with backtracking:
1. Sort calendar slots by "most constrained first" (fewest available teams)
2. For each slot, find best-fitting unassigned round (most matchups playable)
3. Assign it; defer any unplayable games within the round to a later slot
4. Track deferred games and schedule them in the next available slot for both teams

### Step 4: Home/Away Assignment

Home team and host team are separate concepts:
- **Home team** = recorded as home for balance/standings (always balanced within 1)
- **Host team** = whose field the game is played on (usually = home, but not always)

For each game, after matchup and calendar slot are determined:
1. Check cumulative home/away balance for both teams
2. Assign the team with fewer home games as home (tie-break: random)
3. Check league home cap for that round — if exceeded, flip
4. Track weekday and weekend home counts separately

Then assign hosting (field/time):
- If home team can host (has a field slot for this day type) → they host
- If not (RAV never, HLL on weekdays) → away team hosts
- This can also be used as an escape valve to resolve scheduling conflicts

### Step 5: Time Slot Assignment

1. Home team's league determines the field and time
2. If home team has no field → use away team's field/time
3. Soft constraint: avoid scheduling same-league teams at the same time
   - For 2-team leagues: hard constraint (always possible)
   - For 3-4 team leagues: minimize, treat as soft
   - For 4+ team leagues (RWC has 4): best effort
4. MA: never schedule on Mon/Thu regardless of home/away
5. SMN: skip weekend slots unless in available_weekends list

## Output

### 1. Human-Readable Schedule (`schedule_2026.txt`)
- Week-by-week view: date, time, field, home team vs away team
- Per-team season view: all games for each team in date order
- Markdown formatted for easy sharing

### 2. Statistics Report (`stats_2026.txt`)
- Home/away counts per team (weekday, weekend, combined) — diff should be 0 or 1
- Matchup matrix: who played whom, how many times
- Games per day-of-week per team
- League home cap usage per round
- Total games per team
- Flags for any constraint violations

### 3. SIPlay Upload CSV (`upload_2026.csv`)
Format matching the existing Upload tab:
```
Start_Date,Start_Time,End_Date,End_Time,Title,Description,Location,Location_URL,
Location_Details,All_Day_Event,Event_Type,Tags,Team1_ID,Team1_Division_ID,
Team1_Is_Home,Team2_ID,Team2_Division_ID,Team2_Name,Custom_Opponent,Event_ID,
Game_ID,Affects_Standings,Points_Win,Points_Loss,Points_Tie,Points_OT_Win,
Points_OT_Loss,Division_Override
```
- Team1 = home team, Team2 = away team
- Start/End times from field config + game_length_minutes
- Most fields blank (matches existing pattern)

## Implementation Order

1. **`models.py`** — dataclasses for Team, League, Game, Round, CalendarSlot
2. **`config.py`** — YAML loader, validation, build objects from config
3. **`config.yaml`** — real 2026 data from the registration form and contacts sheet
4. **`roundrobin.py`** — circle method with shuffle, separate intra-pool and crossover generators
5. **`scheduler.py`** — calendar builder, round-to-slot assignment with blackout handling, home/away assignment, time slot assignment
6. **`constraints.py`** — validation functions (check all hard constraints, report soft constraint violations)
7. **`stats.py`** — home/away balance, matchup matrix, day-of-week distribution, total games
8. **`output.py`** — human-readable formatter, SIPlay CSV formatter
9. **`verify.py`** — standalone verifier that reads output CSV + config.yaml and re-checks all constraints
10. **`schedule.py`** — CLI entry point tying it all together

## Verification

The verifier (`verify.py`) can run in two modes:
1. **Post-generation:** automatically runs after schedule generation to validate
2. **Standalone:** ingests a CSV (the human-readable or SIPlay output) so you can hand-edit the schedule and re-verify

Both modes check:
- Every team plays every other team in pool exactly once (or report if not enough rounds)
- Home/away diff is 0 or 1 for every team
- No team plays on a blackout date
- No team plays twice in the same weekday or weekend slot
- SMN has no weekend games (except available weekends)
- MA has no Mon/Thu games
- RAV home games are hosted by opponent
- League home caps not exceeded per round
- Same-league teams rarely play at same time (report violations)
- Total games per team within expected range

The standalone mode reads the schedule CSV + the config YAML (for constraint definitions) and produces the same stats/violation report.
