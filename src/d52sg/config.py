"""Config loading and validation for D52 scheduling app."""

from datetime import date, time, timedelta
from pathlib import Path

import yaml

from d52sg.models import DayOfWeek, FieldSlot, League, Team


def parse_time(s: str) -> time:
    """Parse time strings like '5:30pm', '10am', '17:00'."""
    s = s.strip()
    s_lower = s.lower()

    is_pm = s_lower.endswith("pm")
    is_am = s_lower.endswith("am")

    # Strip am/pm suffix
    s_clean = s_lower
    if is_pm:
        s_clean = s_clean[:-2].strip()
    elif is_am:
        s_clean = s_clean[:-2].strip()

    if ":" in s_clean:
        parts = s_clean.split(":")
        h = int(parts[0])
        m = int(parts[1])
    else:
        h = int(s_clean)
        m = 0

    if is_pm and h < 12:
        h += 12
    elif is_am and h == 12:
        h = 0

    return time(h, m)


def parse_date(s: str) -> date:
    """Parse date string YYYY-MM-DD."""
    parts = s.strip().split("-")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def parse_date_range(s: str) -> tuple[date, date]:
    """Parse 'YYYY-MM-DD:YYYY-MM-DD' into (start, end) dates."""
    parts = s.split(":")
    return parse_date(parts[0]), parse_date(parts[1])


def load_config(path: str | Path) -> dict:
    """Load and validate config YAML, returning structured data.

    Returns dict with:
    - season: {start_date, end_date, game_length_minutes}
    - teams: dict[code -> Team]
    - leagues: dict[code -> League]
    - pools: {north: [codes], south: [codes]}
    """
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)

    # Season
    season = {
        "start_date": parse_date(str(raw["season"]["start_date"])),
        "end_date": parse_date(str(raw["season"]["end_date"])),
        "game_length_minutes": raw["season"].get("game_length_minutes", 150),
        "name": raw["season"].get("name", ""),
        "game_code_prefix": raw["season"].get("game_code_prefix", "G"),
    }

    # Pools
    pools = {
        "north": list(raw["pools"]["north"]),
        "south": list(raw["pools"]["south"]),
    }
    all_team_codes = set(pools["north"] + pools["south"])

    # Leagues
    leagues: dict[str, League] = {}
    team_to_league: dict[str, str] = {}

    for code, ldata in raw["leagues"].items():
        teams_val = ldata.get("teams", [])
        if isinstance(teams_val, int):
            # Auto-generate team names: BRS1, BRS2, ... for teams: 2
            teams = [f"{code}{i}" for i in range(1, teams_val + 1)]
        else:
            teams = list(teams_val)
        for t in teams:
            if t not in all_team_codes:
                print(f"Warning: team {t} in league {code} not found in any pool")
            team_to_league[t] = code

        weekday_fields = []
        for fd in ldata.get("weekday_fields", []):
            exclude = [parse_date(str(d)) for d in fd.get("exclude_dates", [])]
            weekday_fields.append(FieldSlot(
                field_name=fd["field"],
                day=DayOfWeek.from_str(fd["day"]),
                start_time=parse_time(str(fd.get("time", "5:30pm"))),
                exclude_dates=exclude,
            ))

        weekend_fields = []
        for fd in ldata.get("weekend_fields", []):
            exclude = [parse_date(str(d)) for d in fd.get("exclude_dates", [])]
            weekend_fields.append(FieldSlot(
                field_name=fd["field"],
                day=DayOfWeek.from_str(fd["day"]),
                start_time=parse_time(str(fd.get("time", "10am"))),
                exclude_dates=exclude,
            ))

        blackout_ranges = []
        for br in ldata.get("blackout_dates", []):
            s = str(br)
            if ":" in s:
                blackout_ranges.append(parse_date_range(s))
            else:
                d = parse_date(s)
                blackout_ranges.append((d, d))

        leagues[code] = League(
            code=code,
            full_name=ldata.get("full_name", code),
            teams=teams,
            has_fields=bool(weekday_fields or weekend_fields),
            weekday_fields=weekday_fields,
            weekend_fields=weekend_fields,
            blackout_ranges=blackout_ranges,
        )

    # Teams
    overrides = raw.get("team_overrides", {})
    teams: dict[str, Team] = {}

    for pool_name in ("north", "south"):
        for code in pools[pool_name]:
            ovr = overrides.get(code, {})
            no_play = [DayOfWeek.from_str(d) for d in ovr.get("no_play_days", [])]
            avail_we = [parse_date(str(d)) for d in ovr.get("available_weekends", [])]

            teams[code] = Team(
                code=code,
                league_code=team_to_league.get(code, "UNKNOWN"),
                pool=pool_name,
                weekday_only=ovr.get("weekday_only", False),
                available_weekends=avail_we,
                no_play_days=no_play,
                gamechanger_name=ovr.get("gamechanger_name", ""),
            )

    # Avoid-same-time groups
    avoid_same_time_groups: list[frozenset[str]] = []
    for group in raw.get("avoid_same_time_groups", []):
        avoid_same_time_groups.append(frozenset(group))

    # Validate
    errors = []
    for code in all_team_codes:
        if code not in teams:
            errors.append(f"Team {code} in pools but not constructed")
        if code not in team_to_league:
            errors.append(f"Team {code} in pools but not in any league")

    for group in avoid_same_time_groups:
        for t in group:
            if t not in all_team_codes:
                errors.append(
                    f"Team {t} in avoid_same_time_groups but not in any pool"
                )

    if errors:
        print("Config validation errors:")
        for e in errors:
            print(f"  {e}")

    # Fields (map URLs etc.)
    field_info: dict[str, dict] = {}
    for name, fdata in raw.get("fields", {}).items():
        field_info[name] = {
            "map_url": fdata.get("map_url", ""),
        }

    return {
        "season": season,
        "teams": teams,
        "leagues": leagues,
        "pools": pools,
        "avoid_same_time_groups": avoid_same_time_groups,
        "field_info": field_info,
    }
