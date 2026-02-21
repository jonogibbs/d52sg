"""Data models for D52 scheduling app."""

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from typing import Optional


class DayOfWeek(Enum):
    Mon = 0
    Tue = 1
    Wed = 2
    Thu = 3
    Fri = 4
    Sat = 5
    Sun = 6

    @classmethod
    def from_str(cls, s: str) -> "DayOfWeek":
        return cls[s[:3].capitalize()]

    def is_weekday(self) -> bool:
        return self.value < 5

    def is_weekend(self) -> bool:
        return self.value >= 5


WEEKDAYS = [DayOfWeek.Mon, DayOfWeek.Tue, DayOfWeek.Wed, DayOfWeek.Thu, DayOfWeek.Fri]
WEEKENDS = [DayOfWeek.Sat, DayOfWeek.Sun]


@dataclass
class FieldSlot:
    """A specific field/time that a league can use for home games."""
    field_name: str
    day: DayOfWeek
    start_time: time
    exclude_dates: list[date] = field(default_factory=list)


@dataclass
class League:
    """A local league with one or more teams."""
    code: str
    full_name: str
    teams: list[str]
    has_fields: bool = True
    weekday_fields: list[FieldSlot] = field(default_factory=list)
    weekend_fields: list[FieldSlot] = field(default_factory=list)
    blackout_ranges: list[tuple[date, date]] = field(default_factory=list)

    @property
    def weekday_home_cap(self) -> int:
        """Max home games this league can host per weekday round."""
        return len(self.weekday_fields)

    @property
    def weekend_home_cap(self) -> int:
        """Max home games this league can host per weekend round."""
        return len(self.weekend_fields)

    def is_blacked_out(self, d: date) -> bool:
        return any(start <= d <= end for start, end in self.blackout_ranges)


@dataclass
class Team:
    """A team in the league."""
    code: str
    league_code: str
    pool: str  # "north" or "south"
    weekday_only: bool = False
    available_weekends: list[date] = field(default_factory=list)
    no_play_days: list[DayOfWeek] = field(default_factory=list)
    gamechanger_name: str = ""


@dataclass
class Matchup:
    """A pairing of two teams (no home/away yet)."""
    team_a: str
    team_b: str

    def involves(self, team_code: str) -> bool:
        return team_code in (self.team_a, self.team_b)

    def opponent(self, team_code: str) -> str:
        if team_code == self.team_a:
            return self.team_b
        return self.team_a


@dataclass
class Round:
    """A set of matchups where each team plays at most once."""
    number: int
    matchups: list[Matchup]
    round_type: str = "intra"  # "intra" or "crossover"
    bye_teams: list[str] = field(default_factory=list)


@dataclass
class Game:
    """A fully scheduled game with date, time, field, and home/away."""
    home_team: str
    away_team: str
    host_team: str  # whose field is used (usually = home, but not always)
    date: date
    start_time: time
    end_time: time
    field_name: str
    round_number: int
    game_type: str = "intra"  # "intra" or "crossover"
    week_number: int = 0


@dataclass
class CalendarSlot:
    """A scheduling slot: one weekday period or one weekend period in a week."""
    week_number: int
    slot_type: str  # "weekday" or "weekend"
    dates: list[date] = field(default_factory=list)  # available dates in this slot
    available_teams: set[str] = field(default_factory=set)
    assigned_round: Optional[int] = None
    games: list[Game] = field(default_factory=list)
