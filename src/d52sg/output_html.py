"""HTML schedule formatter for D52 scheduling app."""

from collections import defaultdict
from datetime import date, time
from html import escape
from pathlib import Path

from d52sg.models import Game


def _fmt_time(t: time) -> str:
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


def _fmt_date(d: date) -> str:
    return d.strftime("%a %b %-d")


def _fmt_date_short(d: date) -> str:
    return d.strftime("%a %-m/%-d")


# Emoji for game type
CROSS_EMOJI = "\U0001F697"  # red car
INTRA_EMOJI = "\U0001F6B6\u200D\u2642\uFE0F"  # walking man


def _round_label(game: Game, pools: dict) -> str:
    """Round label with pool prefix and source indicator.

    - Regular round game: n1, s4, x10
    - Deferred (blackout recovery): n1, s4, x10 (same label, different slot)
    - Safe ad-hoc (from overflow round): n1*, s4*, x10*
    - Truly ad-hoc (invented pairing): AH
    """
    source = game.game_source
    if source == "adhoc" or (game.round_number == 0 and not source):
        return "AH"
    if game.game_type == "crossover":
        label = f"x{game.round_number}"
    else:
        north = set(pools.get("north", []))
        prefix = "n" if game.home_team in north else "s"
        label = f"{prefix}{game.round_number}"
    if source == "safe_adhoc":
        label += "*"
    return label


def _fmt_field(field_name: str, field_info: dict) -> str:
    """Format a field name, linking to map URL if available."""
    info = field_info.get(field_name, {})
    map_url = info.get("map_url", "")
    escaped = escape(field_name)
    if map_url:
        return (f'<a href="{escape(map_url)}" target="_blank" '
                f'style="color:#1a6fb5">{escaped}</a>')
    return escaped


CSS = """\
body {
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  max-width: 960px;
  margin: 0 auto;
  padding: 20px;
  color: #222;
  font-size: 14px;
}
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 18px; margin-top: 32px; border-bottom: 2px solid #333; padding-bottom: 4px; }
h3 { font-size: 15px; margin-top: 24px; margin-bottom: 8px; color: #444; }
h4 { font-size: 14px; margin-top: 20px; margin-bottom: 6px; }
.subtitle { color: #666; margin-bottom: 20px; }
.legend { color: #666; margin-bottom: 16px; font-size: 13px; }
table {
  border-collapse: collapse;
  width: 100%;
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
.home { font-weight: 600; }
.away { }
.game-type { text-align: center; }
.day-header {
  background: #eef3f8;
  font-weight: 600;
  padding: 6px 8px;
}
.toc { column-count: 3; margin-bottom: 20px; }
.toc a { text-decoration: none; color: #1a6fb5; }
.toc a:hover { text-decoration: underline; }
@media print {
  .toc { column-count: 2; }
  h2 { page-break-before: always; }
  h2:first-of-type { page-break-before: avoid; }
}
pre {
  background: #f5f5f5;
  padding: 16px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.5;
}
.stat-table { width: auto; }
.stat-table td, .stat-table th { padding: 3px 6px; text-align: right; }
.stat-table td:first-child, .stat-table th:first-child { text-align: left; }
.flag { background: #f8d7da; font-weight: 600; }
.valid { color: #080; font-weight: 600; }
.invalid { color: #c00; font-weight: 600; }
.error-item { color: #c00; }
.warn-item { color: #996600; }
.matrix-cell { text-align: center; font-size: 12px; }
.matrix-header { font-size: 11px; text-align: center; }
.side-by-side { display: flex; gap: 24px; flex-wrap: wrap; }
.side-by-side > div { flex: 1; min-width: 300px; }
.side-by-side table { width: auto; }
.bye { color: #999; font-style: italic; }
.blackout { color: #999; font-style: italic; }
"""


def format_schedule_html(games: list[Game], teams: dict, leagues: dict,
                         field_info: dict | None = None,
                         pools: dict | None = None,
                         validation_result: dict | None = None,
                         stats: dict | None = None,
                         season_name: str = "",
                         game_code_prefix: str = "G") -> str:
    """Generate a full HTML schedule document."""
    field_info = field_info or {}
    pools = pools or {}
    title = season_name or "D52 Juniors 54/80 Schedule"

    scheduled = [g for g in games if not g.unscheduled]
    unscheduled = [g for g in games if g.unscheduled]

    # Assign sequential game codes (G1, G2, ...) sorted by date/time
    sorted_games = sorted(scheduled, key=lambda g: (g.date, g.start_time,
                                                 g.home_team))
    game_codes: dict[int, str] = {}  # id(game) -> code string
    for i, g in enumerate(sorted_games, 1):
        game_codes[id(g)] = f"{game_code_prefix}{i}"

    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{escape(title)}</title>")
    parts.append(f"<style>{CSS}</style>")
    parts.append("</head><body>")

    parts.append(f"<h1>{escape(title)}</h1>")

    if scheduled:
        first = min(g.date for g in scheduled)
        last = max(g.date for g in scheduled)
        count_str = f'{len(scheduled)} games'
        if unscheduled:
            count_str += f' + {len(unscheduled)} unscheduled'
        parts.append(f'<p class="subtitle">{first.strftime("%B %-d")} &ndash; '
                     f'{last.strftime("%B %-d, %Y")} &middot; '
                     f'{count_str}</p>')

    parts.append(f'<p class="legend">'
                 f'{CROSS_EMOJI} = crossover (north vs south) &nbsp; '
                 f'{INTRA_EMOJI} = intra-pool &nbsp; '
                 f'\U0001F3E0 = hosting</p>')

    # Table of contents
    parts.append('<h2 id="toc">Contents</h2>')
    parts.append('<div class="toc">')

    # Group leagues by pool for the TOC
    for pool_name in ("north", "south"):
        pool_teams = pools.get(pool_name, [])
        if not pool_teams:
            continue
        pool_leagues = []
        seen = set()
        for tc in pool_teams:
            lc = teams[tc].league_code
            if lc not in seen:
                seen.add(lc)
                pool_leagues.append(lc)
        parts.append(f'<p style="margin-top:8px"><strong>'
                     f'{pool_name.title()} Pool</strong></p>')
        for lc in sorted(pool_leagues):
            league = leagues[lc]
            parts.append(f'<p><a href="#league-{lc}">'
                         f'{escape(league.full_name)}</a></p>')

    # Fallback if no pools provided — flat list
    if not pools:
        for league_code in sorted(leagues.keys()):
            league = leagues[league_code]
            parts.append(f'<p><a href="#league-{league_code}">'
                         f'{escape(league.full_name)}</a></p>')

    if unscheduled:
        parts.append(f'<p style="margin-top:8px">'
                     f'<a href="#unscheduled" style="color:#c00">'
                     f'Unscheduled Games ({len(unscheduled)})</a></p>')

    if validation_result or stats:
        parts.append(f'<p style="margin-top:8px">'
                     f'<a href="#stats">Schedule Statistics</a></p>')

    parts.append("</div>")

    # --- Master schedule ---
    parts.append('<h2 id="master">Full Schedule</h2>')

    # Group games by (week_number, slot) where slot is weekday or weekend
    all_team_codes = sorted(teams.keys())
    by_slot: dict[tuple[int, str], list[Game]] = defaultdict(list)
    week_numbers = set()
    for g in scheduled:
        slot_type = "weekend" if g.date.weekday() >= 5 else "weekday"
        by_slot[(g.week_number, slot_type)].append(g)
        week_numbers.add(g.week_number)

    # Index unscheduled games by (week_number, slot_type)
    unsched_by_slot: dict[tuple[int, str], list[Game]] = defaultdict(list)
    unsched_teams_by_slot: dict[tuple[int, str], set[str]] = defaultdict(set)
    for g in unscheduled:
        st = g.slot_type if g.slot_type else "weekend"
        unsched_by_slot[(g.week_number, st)].append(g)
        unsched_teams_by_slot[(g.week_number, st)].add(g.home_team)
        unsched_teams_by_slot[(g.week_number, st)].add(g.away_team)
        week_numbers.add(g.week_number)

    for week_num in sorted(week_numbers):
        for slot_type, slot_label in [("weekday", "Weekday"),
                                      ("weekend", "Weekend")]:
            key = (week_num, slot_type)
            slot_games = sorted(by_slot.get(key, []),
                                key=lambda g: (g.date, g.start_time))
            if not slot_games:
                continue

            # Date range for this sub-header
            dates = sorted({g.date for g in slot_games})
            if len(dates) == 1:
                date_range = dates[0].strftime("%-m/%-d")
            else:
                date_range = (f"{dates[0].strftime('%-m/%-d')}"
                              f"&ndash;{dates[-1].strftime('%-m/%-d')}")

            parts.append(f"<h3>Week {week_num} &mdash; {slot_label} "
                         f"({date_range})</h3>")
            parts.append("<table>")
            parts.append("<tr><th>Game</th><th></th><th>Time</th><th>Home</th>"
                         "<th></th><th>Visitor</th><th>Field</th>"
                         "<th>R#</th></tr>")

            prev_date = None
            for g in slot_games:
                if g.date != prev_date:
                    day_label = g.date.strftime("%A %-m/%-d")
                    parts.append(f'<tr><td colspan="8" class="day-header">'
                                 f'{day_label}</td></tr>')
                    prev_date = g.date

                gtype_emoji = CROSS_EMOJI if g.game_type == "crossover" else INTRA_EMOJI
                host_note = ""
                if g.host_team != g.home_team:
                    host_note = f' <span style="color:red">(at {escape(g.host_team)})</span>'

                field_html = _fmt_field(g.field_name, field_info)

                rnd_label = _round_label(g, pools)
                rnd_style = ' style="color:#999"' if g.round_number == 0 else ""

                gcode = game_codes.get(id(g), "")

                parts.append(
                    f"<tr>"
                    f"<td>{gcode}</td>"
                    f'<td class="game-type">{gtype_emoji}</td>'
                    f"<td>{_fmt_time(g.start_time)}</td>"
                    f'<td class="home">{escape(g.home_team)}</td>'
                    f"<td>vs</td>"
                    f"<td>{escape(g.away_team)}</td>"
                    f"<td>{field_html}{host_note}</td>"
                    f"<td{rnd_style}>{rnd_label}</td>"
                    f"</tr>"
                )

            # Unscheduled games in this slot (shown in red)
            slot_unsched = unsched_by_slot.get(key, [])
            for g in slot_unsched:
                gtype_emoji = CROSS_EMOJI if g.game_type == "crossover" else INTRA_EMOJI
                parts.append(
                    f'<tr style="background:#f8d7da">'
                    f"<td></td>"
                    f'<td class="game-type">{gtype_emoji}</td>'
                    f"<td>UNSCHED</td>"
                    f'<td class="home">{escape(g.home_team)}</td>'
                    f"<td>vs</td>"
                    f"<td>{escape(g.away_team)}</td>"
                    f'<td colspan="2"></td>'
                    f"</tr>"
                )

            # BYE / Blackout rows: teams not playing in this slot
            playing = set()
            for g in slot_games:
                playing.add(g.home_team)
                playing.add(g.away_team)
            # Teams with unscheduled games are NOT on bye
            unsched_here = unsched_teams_by_slot.get(key, set())
            not_playing = sorted(t for t in all_team_codes
                                 if t not in playing and t not in unsched_here)
            blackout_teams = []
            weekday_only_teams = []
            bye_teams = []
            for t in not_playing:
                if slot_type == "weekend" and teams[t].weekday_only:
                    weekday_only_teams.append(t)
                else:
                    lc = teams[t].league_code
                    league = leagues[lc]
                    if any(league.is_blacked_out(d) for d in dates):
                        blackout_teams.append(t)
                    else:
                        bye_teams.append(t)
            if blackout_teams:
                parts.append(f'<tr class="blackout"><td colspan="8">'
                             f'Blackout: {", ".join(blackout_teams)}</td></tr>')
            if weekday_only_teams:
                parts.append(f'<tr class="blackout"><td colspan="8">'
                             f'Weekdays Only: {", ".join(weekday_only_teams)}'
                             f'</td></tr>')
            if bye_teams:
                parts.append(f'<tr class="bye"><td colspan="8">'
                             f'BYE: {", ".join(bye_teams)}</td></tr>')

            parts.append("</table>")

    # --- Unscheduled games ---
    if unscheduled:
        parts.append(f'<h2 id="unscheduled" style="color:#c00">'
                     f'Unscheduled Games ({len(unscheduled)})</h2>')
        parts.append('<p>These games could not be assigned a field/time.</p>')
        parts.append("<table>")
        parts.append("<tr><th></th><th>Home</th><th></th>"
                     "<th>Visitor</th><th>Week</th></tr>")
        for g in unscheduled:
            gtype_emoji = CROSS_EMOJI if g.game_type == "crossover" else INTRA_EMOJI
            parts.append(
                f'<tr style="background:#f8d7da">'
                f'<td class="game-type">{gtype_emoji}</td>'
                f'<td class="home">{escape(g.home_team)}</td>'
                f"<td>vs</td>"
                f"<td>{escape(g.away_team)}</td>"
                f"<td>W{g.week_number} {'WD' if g.slot_type == 'weekday' else 'WE'}</td>"
                f"</tr>"
            )
        parts.append("</table>")

    # --- Per-league schedules ---
    by_team: dict[str, list[Game]] = defaultdict(list)
    for g in scheduled:
        by_team[g.home_team].append(g)
        by_team[g.away_team].append(g)

    # Track unscheduled per team
    unsched_by_team: dict[str, list[Game]] = defaultdict(list)
    for g in unscheduled:
        unsched_by_team[g.home_team].append(g)
        unsched_by_team[g.away_team].append(g)

    # Build set of (week_number, slot_type) that exist in the schedule
    all_slots_set = {(g.week_number,
                      "weekend" if g.date.weekday() >= 5 else "weekday")
                     for g in scheduled}
    # Include slots that only have unscheduled games
    for g in unscheduled:
        st = g.slot_type if g.slot_type else "weekend"
        all_slots_set.add((g.week_number, st))
    all_slots = sorted(all_slots_set)
    # Date range per slot for display
    slot_dates: dict[tuple[int, str], list[date]] = defaultdict(list)
    for g in scheduled:
        st = "weekend" if g.date.weekday() >= 5 else "weekday"
        slot_dates[(g.week_number, st)].append(g.date)

    for league_code in sorted(leagues.keys()):
        league = leagues[league_code]
        parts.append(f'<h2 id="league-{league_code}">'
                     f'{escape(league.full_name)} ({league_code})</h2>')

        # Show blackout dates for this league
        if league.blackout_ranges:
            bo_parts = []
            for start, end in league.blackout_ranges:
                if start == end:
                    bo_parts.append(start.strftime("%-m/%-d"))
                else:
                    bo_parts.append(f"{start.strftime('%-m/%-d')}"
                                    f"&ndash;{end.strftime('%-m/%-d')}")
            parts.append(f'<p class="blackout">Blackout: {", ".join(bo_parts)}</p>')

        for team_code in sorted(league.teams):
            team_games = sorted(by_team.get(team_code, []),
                                key=lambda g: (g.date, g.start_time))

            home_count = sum(1 for g in team_games if g.home_team == team_code)
            away_count = len(team_games) - home_count

            parts.append(f"<h4>{escape(team_code)} &mdash; "
                         f"{len(team_games)} games "
                         f"({home_count}H / {away_count}V)</h4>")
            parts.append("<table>")
            parts.append("<tr><th>Game</th><th>#</th><th>Week</th><th>Date</th><th>Time</th>"
                         "<th>H/V</th><th>Host</th><th>Opponent</th>"
                         "<th>Field</th><th>R#</th></tr>")

            # Index this team's games by (week, slot_type) for BYE detection
            team_slot_games: dict[tuple[int, str], Game] = {}
            for g in team_games:
                st = "weekend" if g.date.weekday() >= 5 else "weekday"
                team_slot_games[(g.week_number, st)] = g

            # Index unscheduled games by (week, slot_type) for this team
            team_unsched_slot: dict[tuple[int, str], list[Game]] = defaultdict(list)
            for g in unsched_by_team.get(team_code, []):
                st = g.slot_type if g.slot_type else "weekend"
                team_unsched_slot[(g.week_number, st)].append(g)

            game_num = 0
            for wk, st in all_slots:
                slot_label = "WD" if st == "weekday" else "WE"
                g = team_slot_games.get((wk, st))

                if g:
                    game_num += 1
                    is_home = g.home_team == team_code
                    is_host = g.host_team == team_code
                    opponent = g.away_team if is_home else g.home_team
                    ha = "Home" if is_home else "Visitor"
                    ha_cls = "home" if is_home else "away"
                    host_emoji = "\U0001F3E0" if is_host else ""
                    rnd_label = _round_label(g, pools)
                    rnd_style = ' style="color:#999"' if g.round_number == 0 else ""
                    field_html = _fmt_field(g.field_name, field_info)

                    gcode = game_codes.get(id(g), "")

                    parts.append(
                        f"<tr>"
                        f"<td>{gcode}</td>"
                        f"<td>{game_num}</td>"
                        f"<td>W{wk} {slot_label}</td>"
                        f"<td>{_fmt_date_short(g.date)}</td>"
                        f"<td>{_fmt_time(g.start_time)}</td>"
                        f'<td class="{ha_cls}">{ha}</td>'
                        f"<td>{host_emoji}</td>"
                        f"<td>{escape(opponent)}</td>"
                        f"<td>{field_html}</td>"
                        f"<td{rnd_style}>{rnd_label}</td>"
                        f"</tr>"
                    )
                elif (wk, st) in team_unsched_slot:
                    # Unscheduled game in this slot — show in red
                    for ug in team_unsched_slot[(wk, st)]:
                        opponent = (ug.away_team if ug.home_team == team_code
                                    else ug.home_team)
                        gtype_emoji = (CROSS_EMOJI if ug.game_type == "crossover"
                                       else INTRA_EMOJI)
                        parts.append(
                            f'<tr style="background:#f8d7da">'
                            f'<td></td><td></td>'
                            f'<td>W{wk} {slot_label}</td>'
                            f'<td colspan="2">UNSCHED</td>'
                            f'<td></td><td>{gtype_emoji}</td>'
                            f'<td>{escape(opponent)}</td>'
                            f'<td colspan="2"></td></tr>'
                        )
                else:
                    # No game — check if blacked out or weekday-only
                    dates_in_slot = slot_dates.get((wk, st), [])
                    is_blackout = any(league.is_blacked_out(d)
                                      for d in dates_in_slot)
                    if is_blackout:
                        parts.append(
                            f'<tr class="blackout">'
                            f'<td></td><td></td><td>W{wk} {slot_label}</td>'
                            f'<td colspan="7">Blackout</td></tr>'
                        )
                    elif st == "weekend" and teams[team_code].weekday_only:
                        parts.append(
                            f'<tr class="blackout">'
                            f'<td></td><td></td><td>W{wk} {slot_label}</td>'
                            f'<td colspan="7">Weekdays Only</td></tr>'
                        )
                    else:
                        parts.append(
                            f'<tr class="bye">'
                            f'<td></td><td></td><td>W{wk} {slot_label}</td>'
                            f'<td colspan="7">BYE</td></tr>'
                        )

            parts.append("</table>")

    # --- Statistics & Validation ---
    if stats:
        all_teams = stats["all_teams"]

        parts.append('<h2 id="stats">Schedule Statistics</h2>')

        # Home/Away Balance table
        def _hz(v):
            """Format int for HTML, suppressing zeros to empty string."""
            return str(v) if v else ""

        parts.append("<h3>Season Balance</h3>")
        parts.append('<table class="stat-table">')
        parts.append("<tr><th>Team</th><th>Home</th><th>Visitor</th>"
                     "<th>Host</th><th>H-Away</th><th>Total</th><th>Diff</th>"
                     "<th>WD-H</th><th>WD-V</th>"
                     "<th>WE-H</th><th>WE-V</th>"
                     "<th>BO</th><th>BYE</th><th>UNS</th></tr>")
        for t in all_teams:
            h = stats["home_counts"].get(t, 0)
            a = stats["away_counts"].get(t, 0)
            hosted = stats["hosted_counts"].get(t, 0)
            hnh = stats.get("home_not_hosting", {}).get(t, 0)
            tot = stats["total_games"].get(t, 0)
            diff = h - a
            wdh = stats["weekday_home"].get(t, 0)
            wda = stats["weekday_away"].get(t, 0)
            weh = stats["weekend_home"].get(t, 0)
            wea = stats["weekend_away"].get(t, 0)
            bo = stats.get("blackout_counts", {}).get(t, 0)
            bye = stats.get("bye_counts", {}).get(t, 0)
            uns = stats.get("unsched_per_team", {}).get(t, 0)
            flag_cls = ' class="flag"' if abs(diff) > 1 else ""
            diff_str = f"+{diff}" if diff > 0 else str(diff) if diff else ""
            parts.append(
                f"<tr><td><strong>{escape(t)}</strong></td>"
                f"<td>{_hz(h)}</td><td>{_hz(a)}</td><td>{_hz(hosted)}</td>"
                f"<td>{_hz(hnh)}</td><td>{_hz(tot)}</td>"
                f"<td{flag_cls}>{diff_str}</td>"
                f"<td>{_hz(wdh)}</td><td>{_hz(wda)}</td>"
                f"<td>{_hz(weh)}</td><td>{_hz(wea)}</td>"
                f"<td>{_hz(bo)}</td><td>{_hz(bye)}</td><td>{_hz(uns)}</td></tr>"
            )
        parts.append("</table>")

        # Matchup Matrix
        parts.append("<h3>Matchup Matrix</h3>")
        parts.append('<table class="stat-table">')
        parts.append('<tr><th></th>' +
                     ''.join(f'<th class="matrix-header">{escape(t)}</th>'
                             for t in all_teams) + '</tr>')
        for t1 in all_teams:
            row = f'<tr><td><strong>{escape(t1)}</strong></td>'
            for t2 in all_teams:
                if t1 == t2:
                    row += '<td class="matrix-cell" style="color:#ccc">&ndash;</td>'
                else:
                    c = stats["matchup_counts"].get(t1, {}).get(t2, 0)
                    if c == 1:
                        style = ' style="background:#d4edda"'
                    elif c > 1:
                        style = ' style="background:#f8d7da"'
                    else:
                        style = ""
                    row += f'<td class="matrix-cell"{style}>{c}</td>'
            parts.append(row + "</tr>")
        parts.append("</table>")

        # Games per day of week + Games per week — side by side
        max_week = max(
            (max(wk.keys()) for wk in stats["games_per_week"].values() if wk),
            default=0
        )

        parts.append('<div class="side-by-side">')

        # Left: Games per day of week
        parts.append("<div>")
        parts.append("<h3>Games per Day of Week</h3>")
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        parts.append('<table class="stat-table">')
        parts.append("<tr><th>Team</th>" +
                     "".join(f"<th>{d}</th>" for d in days) + "</tr>")
        for t in all_teams:
            row = f"<tr><td><strong>{escape(t)}</strong></td>"
            for d in days:
                c = stats["day_counts"].get(t, {}).get(d, 0)
                row += f"<td>{c}</td>"
            parts.append(row + "</tr>")
        parts.append("</table>")
        parts.append("</div>")

        # Right: Games per week
        if max_week > 0:
            parts.append("<div>")
            parts.append("<h3>Games per Week</h3>")
            parts.append('<table class="stat-table">')
            parts.append("<tr><th>Team</th>" +
                         "".join(f"<th>W{w}</th>" for w in range(1, max_week + 1)) +
                         "</tr>")
            for t in all_teams:
                row = f"<tr><td><strong>{escape(t)}</strong></td>"
                for w in range(1, max_week + 1):
                    c = stats["games_per_week"].get(t, {}).get(w, 0)
                    if c > 3:
                        row += f'<td style="background:#f8d7da">{c}</td>'
                    else:
                        row += f"<td>{c}</td>"
                parts.append(row + "</tr>")
            parts.append("</table>")
            parts.append("</div>")

        parts.append("</div>")

        # Field Slot Utilization grid
        field_slot_usage = stats.get("field_slot_usage", {})
        if field_slot_usage:
            parts.append("<h3>Field Slot Utilization</h3>")

            # Collect all week-slots and sort them
            all_week_slots = set()
            for ws_dict in field_slot_usage.values():
                all_week_slots.update(ws_dict.keys())
            week_slot_cols = sorted(all_week_slots)

            # Sort field slots: by field name, then day order, then time
            day_order = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3,
                         "Fri": 4, "Sat": 5, "Sun": 6}
            field_slots = sorted(field_slot_usage.keys(),
                                 key=lambda fs: (fs[0], day_order.get(fs[1], 9), fs[2]))

            parts.append('<table class="stat-table">')
            # Header row
            hdr = "<tr><th>Field</th><th>Day</th><th>Time</th>"
            for wk, st in week_slot_cols:
                hdr += f'<th class="matrix-header">W{wk}<br>{st}</th>'
            hdr += "</tr>"
            parts.append(hdr)

            for fs in field_slots:
                field_name, dow, start_time = fs
                row = (f"<tr><td>{escape(field_name)}</td>"
                       f"<td>{dow}</td>"
                       f"<td>{_fmt_time(start_time)}</td>")
                for ws in week_slot_cols:
                    c = field_slot_usage[fs].get(ws, 0)
                    if c == 0:
                        row += '<td class="matrix-cell" style="color:#ccc">0</td>'
                    elif c == 1:
                        row += f'<td class="matrix-cell" style="background:#d4edda">{c}</td>'
                    else:
                        row += f'<td class="matrix-cell" style="background:#f8d7da">{c}</td>'
                parts.append(row + "</tr>")
            parts.append("</table>")

    # --- Validation errors & warnings ---
    if validation_result:
        parts.append('<h2 id="report">Validation Report</h2>')
        if validation_result["valid"]:
            parts.append('<p class="valid">VALID &mdash; '
                         'no hard constraint violations</p>')
        else:
            n = len(validation_result["errors"])
            parts.append(f'<p class="invalid">INVALID &mdash; '
                         f'{n} violation{"s" if n != 1 else ""}</p>')

        if validation_result["errors"]:
            parts.append(f"<h3>Errors ({len(validation_result['errors'])})</h3>")
            parts.append("<ul>")
            for e in validation_result["errors"]:
                parts.append(f'<li class="error-item">{escape(e)}</li>')
            parts.append("</ul>")

        if validation_result["warnings"]:
            parts.append(f"<h3>Warnings ({len(validation_result['warnings'])})</h3>")
            parts.append("<ul>")
            for w in validation_result["warnings"]:
                parts.append(f'<li class="warn-item">{escape(w)}</li>')
            parts.append("</ul>")

    parts.append("</body></html>")
    return "\n".join(parts)


def write_schedule_html(games: list[Game], teams: dict, leagues: dict,
                        output_prefix: str = "output",
                        field_info: dict | None = None,
                        pools: dict | None = None,
                        validation_result: dict | None = None,
                        stats: dict | None = None,
                        season_name: str = "",
                        game_code_prefix: str = "G"):
    """Write schedule.html into the output directory."""
    out_dir = Path(output_prefix)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "schedule.html"
    html_path.write_text(format_schedule_html(
        games, teams, leagues, field_info,
        pools=pools,
        validation_result=validation_result,
        stats=stats,
        season_name=season_name,
        game_code_prefix=game_code_prefix,
    ))
    print(f"Written: {html_path}")
