"""Generate a human-readable config summary for league reps to verify."""

import sys
from datetime import date, time
from html import escape
from pathlib import Path

from d52sg.config import load_config


def fmt_date(d: date) -> str:
    return d.strftime("%b %-d")


def fmt_date_range(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.strftime('%b %-d')}\u2013{end.strftime('%-d')}"
    return f"{fmt_date(start)}\u2013{fmt_date(end)}"


def fmt_time(t) -> str:
    h = t.hour
    m = t.minute
    suffix = "am" if h < 12 else "pm"
    if h == 0:
        h = 12
    elif h > 12:
        h -= 12
    if m == 0:
        return f"{h}{suffix}"
    return f"{h}:{m:02d}{suffix}"


def fmt_field_slot(fs) -> str:
    s = f"{fs.day.name} {fmt_time(fs.start_time)} @ {fs.field_name}"
    if fs.exclude_dates:
        dates = ", ".join(fmt_date(d) for d in sorted(fs.exclude_dates))
        s += f"  (not: {dates})"
    return s


def _build_report_data(config: dict) -> dict:
    """Extract structured report data from config for both text and HTML."""
    season = config["season"]
    leagues = config["leagues"]
    teams = config["teams"]
    pools = config["pools"]
    ast_groups = config.get("avoid_same_time_groups", [])
    field_info = config.get("field_info", {})

    # league_code -> list of team override descriptions
    league_overrides: dict[str, list[str]] = {}
    league_override_details: dict[str, list[tuple[str, list[str]]]] = {}
    for code, team in teams.items():
        notes = []
        if team.weekday_only:
            notes.append("Weekday games only")
        if team.no_play_days:
            days = ", ".join(d.name for d in team.no_play_days)
            notes.append(f"Cannot play on {days}")
        if notes:
            league_overrides.setdefault(team.league_code, []).append(
                f"{code}: {'; '.join(notes)}"
            )
            league_override_details.setdefault(team.league_code, []).append(
                (code, notes)
            )

    # league_code -> list of avoid-same-time group descriptions
    league_ast: dict[str, list[str]] = {}
    for group in ast_groups:
        group_sorted = sorted(group)
        league_codes = set()
        for t in group_sorted:
            if t in teams:
                league_codes.add(teams[t].league_code)
        for lc in league_codes:
            league_ast.setdefault(lc, []).append(
                ", ".join(group_sorted)
            )

    # Pool membership by league
    league_pools: dict[str, str] = {}
    for pool_name, pool_teams in pools.items():
        for t in pool_teams:
            if t in teams:
                lc = teams[t].league_code
                league_pools[lc] = pool_name.capitalize()

    return {
        "season": season,
        "leagues": leagues,
        "teams": teams,
        "pools": pools,
        "field_info": field_info,
        "league_overrides": league_overrides,
        "league_override_details": league_override_details,
        "league_ast": league_ast,
        "league_pools": league_pools,
    }


def generate_report(config_path: str) -> str:
    """Generate plain-text config report."""
    config = load_config(config_path)
    data = _build_report_data(config)
    season = data["season"]
    leagues = data["leagues"]

    lines = []
    lines.append(f"D52 JUNIORS 54/80 — {season['start_date'].year} SEASON CONFIG")
    lines.append(f"Season: {fmt_date(season['start_date'])} – "
                 f"{fmt_date(season['end_date'])}")
    lines.append(f"Game length: {season['game_length_minutes']} minutes")
    lines.append("")
    lines.append("Please verify your league's information below and report")
    lines.append("any corrections.")
    lines.append("")
    lines.append("=" * 60)

    for code in sorted(leagues.keys()):
        league = leagues[code]
        pool = data["league_pools"].get(code, "?")
        team_count = len(league.teams)

        lines.append("")
        lines.append(f"{league.full_name} ({code}) — {pool} Pool, "
                     f"{team_count} team{'s' if team_count != 1 else ''}")
        lines.append("-" * 60)

        if not league.has_fields:
            lines.append("  Home fields: None (all games at opponent's venue)")
        else:
            if league.weekday_fields:
                lines.append("  Weekday home games:")
                for fs in league.weekday_fields:
                    lines.append(f"    {fmt_field_slot(fs)}")
            else:
                lines.append("  Weekday home games: None (all weekday games away)")

            if league.weekend_fields:
                lines.append("  Weekend home games:")
                for fs in league.weekend_fields:
                    lines.append(f"    {fmt_field_slot(fs)}")
            else:
                lines.append("  Weekend home games: None")

        if league.blackout_ranges:
            ranges = [fmt_date_range(s, e) for s, e in league.blackout_ranges]
            lines.append(f"  Blackout dates: {', '.join(ranges)}")

        if code in data["league_overrides"]:
            lines.append("  Team notes:")
            for note in data["league_overrides"][code]:
                lines.append(f"    {note}")

        if code in data["league_ast"]:
            for group_str in data["league_ast"][code]:
                lines.append(f"  Scheduling note: {group_str} will not be "
                             f"scheduled at the same time")

    lines.append("")
    lines.append("=" * 60)
    lines.append("")
    return "\n".join(lines)


CSS = """\
body {
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
  color: #222;
  font-size: 14px;
}
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 18px; margin-top: 32px; border-bottom: 2px solid #333; padding-bottom: 4px; }
h3 { font-size: 15px; margin-top: 24px; margin-bottom: 8px; color: #444; }
.subtitle { color: #666; margin-bottom: 4px; }
.intro { color: #666; margin-bottom: 20px; font-style: italic; }
table {
  border-collapse: collapse;
  width: auto;
  margin-bottom: 12px;
  font-size: 13px;
}
th, td {
  padding: 4px 8px;
  text-align: left;
  border-bottom: 1px solid #e0e0e0;
}
th { background: #f5f5f5; font-weight: 600; }
tr:hover { background: #f9f9f9; }
.toc { column-count: 3; margin-bottom: 20px; }
.toc a { text-decoration: none; color: #1a6fb5; }
.toc a:hover { text-decoration: underline; }
.field-link { color: #1a6fb5; }
.note { color: #996600; font-size: 13px; margin-top: 4px; }
.blackout { color: #999; }
.no-fields { color: #999; font-style: italic; }
@media print {
  h2 { page-break-before: always; }
  h2:first-of-type { page-break-before: avoid; }
}
"""


def _fmt_field_html(field_name: str, field_info: dict) -> str:
    """Format a field name, linking to map URL if available."""
    info = field_info.get(field_name, {})
    map_url = info.get("map_url", "")
    escaped = escape(field_name)
    if map_url:
        return (f'<a href="{escape(map_url)}" target="_blank" '
                f'class="field-link">{escaped}</a>')
    return escaped


def generate_html_report(config_path: str) -> str:
    """Generate HTML config report."""
    config = load_config(config_path)
    data = _build_report_data(config)
    season = data["season"]
    leagues = data["leagues"]
    field_info = data["field_info"]
    pools = data["pools"]

    season_name = season.get("name", "D52 Juniors 54/80")
    title = f"{season_name} — Season Config"

    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{escape(title)}</title>")
    parts.append(f"<style>{CSS}</style>")
    parts.append("</head><body>")

    parts.append(f"<h1>{escape(season_name)}</h1>")
    parts.append(f'<p class="subtitle">'
                 f'{fmt_date(season["start_date"])} &ndash; '
                 f'{fmt_date(season["end_date"])} &middot; '
                 f'{season["game_length_minutes"]} minute games</p>')
    parts.append(f'<p class="intro">Please verify your league\'s information '
                 f'below and report any corrections.</p>')

    # Table of contents grouped by pool
    parts.append('<div class="toc">')
    for pool_name in ("north", "south"):
        pool_teams = pools.get(pool_name, [])
        if not pool_teams:
            continue
        pool_leagues = []
        seen = set()
        for tc in pool_teams:
            lc = data["teams"][tc].league_code
            if lc not in seen:
                seen.add(lc)
                pool_leagues.append(lc)
        parts.append(f'<p style="margin-top:8px"><strong>'
                     f'{pool_name.title()} Pool</strong></p>')
        for lc in sorted(pool_leagues):
            league = leagues[lc]
            parts.append(f'<p><a href="#league-{lc}">'
                         f'{escape(league.full_name)}</a></p>')
    parts.append("</div>")

    # Per-league sections
    for code in sorted(leagues.keys()):
        league = leagues[code]
        pool = data["league_pools"].get(code, "?")
        team_count = len(league.teams)
        team_label = "team" if team_count == 1 else "teams"

        parts.append(f'<h2 id="league-{code}">'
                     f'{escape(league.full_name)} ({code})</h2>')
        parts.append(f'<p>{pool} Pool &middot; {team_count} {team_label}: '
                     f'{", ".join(escape(t) for t in sorted(league.teams))}</p>')

        # Fields table
        if not league.has_fields:
            parts.append(f'<p class="no-fields">No home fields &mdash; '
                         f'all games at opponent\'s venue</p>')
        else:
            has_weekday = bool(league.weekday_fields)
            has_weekend = bool(league.weekend_fields)

            if has_weekday or has_weekend:
                parts.append("<table>")
                parts.append("<tr><th></th><th>Day</th><th>Time</th>"
                             "<th>Field</th><th>Excludes</th></tr>")

                if has_weekday:
                    for i, fs in enumerate(league.weekday_fields):
                        label = "Weekday" if i == 0 else ""
                        field_html = _fmt_field_html(fs.field_name, field_info)
                        if fs.exclude_dates:
                            exc = ", ".join(fmt_date(d) for d in sorted(fs.exclude_dates))
                        else:
                            exc = ""
                        parts.append(
                            f"<tr><td><strong>{label}</strong></td>"
                            f"<td>{fs.day.name}</td>"
                            f"<td>{fmt_time(fs.start_time)}</td>"
                            f"<td>{field_html}</td>"
                            f"<td>{escape(exc)}</td></tr>"
                        )
                else:
                    parts.append('<tr><td><strong>Weekday</strong></td>'
                                 '<td colspan="4" class="no-fields">'
                                 'None (all weekday games as visitor)</td></tr>')

                if has_weekend:
                    for i, fs in enumerate(league.weekend_fields):
                        label = "Weekend" if i == 0 else ""
                        field_html = _fmt_field_html(fs.field_name, field_info)
                        if fs.exclude_dates:
                            exc = ", ".join(fmt_date(d) for d in sorted(fs.exclude_dates))
                        else:
                            exc = ""
                        parts.append(
                            f"<tr><td><strong>{label}</strong></td>"
                            f"<td>{fs.day.name}</td>"
                            f"<td>{fmt_time(fs.start_time)}</td>"
                            f"<td>{field_html}</td>"
                            f"<td>{escape(exc)}</td></tr>"
                        )
                else:
                    parts.append('<tr><td><strong>Weekend</strong></td>'
                                 '<td colspan="4" class="no-fields">'
                                 'None</td></tr>')

                parts.append("</table>")

        # Blackout dates
        if league.blackout_ranges:
            ranges = []
            for start, end in league.blackout_ranges:
                if start == end:
                    ranges.append(fmt_date(start))
                else:
                    ranges.append(f"{fmt_date(start)} &ndash; {fmt_date(end)}")
            parts.append(f'<p class="blackout">Blackout: {", ".join(ranges)}</p>')

        # Team overrides
        if code in data["league_override_details"]:
            for team_code, notes in data["league_override_details"][code]:
                for note in notes:
                    parts.append(f'<p class="note">{escape(team_code)}: '
                                 f'{escape(note)}</p>')

        # Avoid same time
        if code in data["league_ast"]:
            for group_str in data["league_ast"][code]:
                parts.append(f'<p class="note">{escape(group_str)} will not '
                             f'be scheduled at the same time</p>')

    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    output_arg = sys.argv[2] if len(sys.argv) > 2 else None

    if output_arg:
        out_dir = Path(output_arg)
        out_dir.mkdir(parents=True, exist_ok=True)

        txt_path = out_dir / "config_report.txt"
        txt_path.write_text(generate_report(config_path))
        print(f"Written: {txt_path}")

        html_path = out_dir / "config_report.html"
        html_path.write_text(generate_html_report(config_path))
        print(f"Written: {html_path}")
    else:
        print(generate_report(config_path))


if __name__ == "__main__":
    main()
