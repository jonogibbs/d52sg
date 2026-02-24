"""Main scheduling engine for D52 scheduling app.

Five phases:
1. Generate round-robin matchups (roundrobin.py)
2. Placement — assign rounds to calendar slots (no deferrals in non-blackout weeks)
3. Field assignment — home/away + field/time within each slot
4. Global optimization — flip home/away to balance across full schedule
5. Trim — remove excess games only if spread > 1 (safety net, rarely needed)

Core principle: every game in a non-blackout CalendarSlot plays. Only
blackout-affected matchups get deferred. Home team = host team is the strong
default; the only exceptions are structurally fieldless teams.
"""

from datetime import date, time, timedelta
from collections import defaultdict
import random

from d52sg.models import (
    CalendarSlot, DayOfWeek, Game, League, Matchup, Round, Team,
    WEEKDAYS, WEEKENDS,
)


def build_calendar(start_date: date, end_date: date,
                   teams: dict[str, Team],
                   leagues: dict[str, League]) -> list[CalendarSlot]:
    """Build calendar slots from start to end date.

    Each week gets two slots: weekday (Mon-Fri) and weekend (Sat-Sun).
    Teams are marked available based on league blackout dates and team overrides.
    """
    slots = []
    week_num = 1

    # Find the Monday of or before start_date
    current = start_date
    if current.weekday() != 0:
        current = current - timedelta(days=current.weekday())

    while current <= end_date:
        # Weekday slot: Mon-Fri of this week
        weekday_dates = []
        for offset in range(5):
            d = current + timedelta(days=offset)
            if start_date <= d <= end_date:
                weekday_dates.append(d)

        if weekday_dates:
            available = set()
            for code, team in teams.items():
                league = leagues[team.league_code]
                has_available_day = any(
                    not league.is_blacked_out(d) for d in weekday_dates
                )
                if has_available_day:
                    available.add(code)

            slots.append(CalendarSlot(
                week_number=week_num,
                slot_type="weekday",
                dates=weekday_dates,
                available_teams=available,
            ))

        # Weekend slot: Sat-Sun of this week
        weekend_dates = []
        for offset in range(5, 7):
            d = current + timedelta(days=offset)
            if start_date <= d <= end_date:
                weekend_dates.append(d)

        if weekend_dates:
            available = set()
            for code, team in teams.items():
                if team.weekday_only:
                    if not any(d in team.available_weekends for d in weekend_dates):
                        continue
                league = leagues[team.league_code]
                # Check: at least one non-blacked-out day exists
                non_bo_days = [d for d in weekend_dates
                               if not league.is_blacked_out(d)]
                if not non_bo_days:
                    continue
                # Check: at least one non-blacked-out day has a matching
                # field slot (either from this team's league or any opponent's).
                # If the team's league has no weekend fields at all, they can
                # still play away at an opponent's field — so availability is
                # not restricted by own-field presence. Only restrict if the
                # team is blacked out on ALL weekend dates.
                available.add(code)

            slots.append(CalendarSlot(
                week_number=week_num,
                slot_type="weekend",
                dates=weekend_dates,
                available_teams=available,
            ))

        current += timedelta(days=7)
        week_num += 1

    return slots


# ---------------------------------------------------------------------------
# Phase 2: Assign rounds to calendar slots
# ---------------------------------------------------------------------------

def _can_host_in_slot(team_code: str, slot: CalendarSlot,
                      teams: dict[str, Team],
                      leagues: dict[str, League]) -> bool:
    """Check if a team's league has a usable field in this calendar slot.

    Checks that at least one field slot's day-of-week matches an available
    date in the calendar slot.
    """
    team = teams[team_code]
    league = leagues[team.league_code]
    if not league.has_fields:
        return False
    is_weekend = slot.slot_type == "weekend"
    fields = league.weekend_fields if is_weekend else league.weekday_fields
    if not fields:
        return False
    # Need at least one date that matches a field slot's day
    field_days = {f.day for f in fields}
    for d in slot.dates:
        dow = DayOfWeek(d.weekday())
        if dow not in field_days:
            continue
        if dow in team.no_play_days:
            continue
        if league.is_blacked_out(d):
            continue
        return True
    return False


def assign_rounds_to_slots(
    weekday_slots: list[CalendarSlot],
    weekend_slots: list[CalendarSlot],
    intra_north_rounds: list[Round],
    intra_south_rounds: list[Round],
    crossover_rounds: list[Round],
    teams: dict[str, Team],
    leagues: dict[str, League],
    pools: dict[str, list[str]],
) -> tuple[list[CalendarSlot], list[CalendarSlot], dict]:
    """Assign rounds to calendar slots — placement only, no balancing.

    Non-blackout slots: every matchup plays unconditionally.
    Blackout slots: defer only matchups where a team is blacked out.
    Idle teams in blackout slots are filled from deferred/overflow/ad-hoc.
    Game count balancing happens post-assign_games in schedule().

    Returns (weekday_slots, weekend_slots, bye_counts).
    Each slot gets ._pending_matchups: list of (Matchup, round_number, source).
    """
    bye_counts: dict[str, int] = defaultdict(int)

    # Track which teams already have a game in each slot
    weekday_teams_in_slot: dict[int, set[str]] = {
        i: set() for i in range(len(weekday_slots))
    }
    weekend_teams_in_slot: dict[int, set[str]] = {
        i: set() for i in range(len(weekend_slots))
    }

    # Deferred matchups that need rescheduling (blackout deferrals)
    deferred_weekday: list[tuple[Matchup, int]] = []
    deferred_weekend: list[tuple[Matchup, int]] = []

    # Overflow round matchups (safe ad-hoc source — never assigned to a slot)
    overflow_weekday: list[tuple[Matchup, int]] = []
    overflow_weekend: list[tuple[Matchup, int]] = []

    all_team_codes = set(teams.keys())

    def _has_blackouts(slot):
        """True if this slot has any blacked-out teams."""
        return len(slot.available_teams) < len(all_team_codes)

    def _score_round(rnd, slot):
        """Count how many matchups from this round have both teams available."""
        count = 0
        for m in rnd.matchups:
            if m.team_a in slot.available_teams and m.team_b in slot.available_teams:
                count += 1
        return count

    def _place_round(rnd, slot, slot_idx, teams_in_slot_map, slot_matchups,
                     deferred_list):
        """Place a round's matchups into a slot.

        Non-blackout slots: ALL matchups placed unconditionally.
        Blackout slots: defer matchups where a team is blacked out.
        """
        has_bo = _has_blackouts(slot)
        for m in rnd.matchups:
            ta, tb = m.team_a, m.team_b
            if ta in teams_in_slot_map[slot_idx] or tb in teams_in_slot_map[slot_idx]:
                deferred_list.append((m, rnd.number))
                continue
            if has_bo and (ta not in slot.available_teams
                           or tb not in slot.available_teams):
                deferred_list.append((m, rnd.number))
                continue
            slot_matchups.append((m, rnd.number, "round"))
            teams_in_slot_map[slot_idx].add(ta)
            teams_in_slot_map[slot_idx].add(tb)
        for t in rnd.bye_teams:
            bye_counts[t] += 1

    # ---- Step 1: Assign weekday rounds to slots ----
    # Blackout slots first (fewest available teams), then non-blackout.
    unassigned_north = list(range(len(intra_north_rounds)))
    unassigned_south = list(range(len(intra_south_rounds)))

    weekday_order = sorted(range(len(weekday_slots)),
                           key=lambda si: len(weekday_slots[si].available_teams))

    for si in weekday_order:
        slot = weekday_slots[si]
        slot_matchups: list[tuple[Matchup, int]] = []

        if unassigned_north:
            best_ni = max(unassigned_north,
                          key=lambda ni: _score_round(
                              intra_north_rounds[ni], slot))
            rnd = intra_north_rounds[best_ni]
            unassigned_north.remove(best_ni)
            _place_round(rnd, slot, si, weekday_teams_in_slot,
                         slot_matchups, deferred_weekday)

        if unassigned_south:
            best_si_ = max(unassigned_south,
                           key=lambda si_: _score_round(
                               intra_south_rounds[si_], slot))
            rnd = intra_south_rounds[best_si_]
            unassigned_south.remove(best_si_)
            _place_round(rnd, slot, si, weekday_teams_in_slot,
                         slot_matchups, deferred_weekday)

        slot._pending_matchups = slot_matchups

    # Overflow rounds — safe ad-hoc source
    for ni in unassigned_north:
        rnd = intra_north_rounds[ni]
        for m in rnd.matchups:
            overflow_weekday.append((m, rnd.number))
        for t in rnd.bye_teams:
            bye_counts[t] += 1

    for si_ in unassigned_south:
        rnd = intra_south_rounds[si_]
        for m in rnd.matchups:
            overflow_weekday.append((m, rnd.number))
        for t in rnd.bye_teams:
            bye_counts[t] += 1

    # ---- Step 1b: Assign weekend (crossover) rounds to slots ----
    unassigned_cross = list(range(len(crossover_rounds)))

    weekend_order = sorted(range(len(weekend_slots)),
                           key=lambda si: len(weekend_slots[si].available_teams))

    for si in weekend_order:
        slot = weekend_slots[si]
        slot_matchups: list[tuple[Matchup, int]] = []

        if unassigned_cross:
            scored = [(
                _score_round(crossover_rounds[xi], slot),
                xi
            ) for xi in unassigned_cross]
            best_score, best_xi = max(scored)

            if best_score >= 1:
                rnd = crossover_rounds[best_xi]
                unassigned_cross.remove(best_xi)
                _place_round(rnd, slot, si, weekend_teams_in_slot,
                             slot_matchups, deferred_weekend)

        slot._pending_matchups = slot_matchups

    for xi in unassigned_cross:
        rnd = crossover_rounds[xi]
        for m in rnd.matchups:
            overflow_weekend.append((m, rnd.number))
        for t in rnd.bye_teams:
            bye_counts[t] += 1

    # ---- Step 2: Fill idle teams in blackout slots ----
    # After round assignment, some available teams in blackout slots have
    # no game (their opponent was blacked out). Fill them.

    # 2a. Pull from deferred list — find deferred matchups involving idle teams
    # Prioritize by targeting idle teams specifically, not just iterating deferred.
    def _fill_from_deferred(deferred, slots, teams_in_slot_map):
        still_deferred = list(deferred)
        any_placed = True
        while any_placed:
            any_placed = False
            for si, slot in enumerate(slots):
                idle = (slot.available_teams - teams_in_slot_map[si])
                if not idle:
                    continue
                # Find a deferred matchup involving an idle team
                for di in range(len(still_deferred)):
                    matchup, rnum = still_deferred[di]
                    ta, tb = matchup.team_a, matchup.team_b
                    if ta not in idle and tb not in idle:
                        continue
                    if ta in teams_in_slot_map[si] or tb in teams_in_slot_map[si]:
                        continue
                    if ta not in slot.available_teams or tb not in slot.available_teams:
                        continue
                    slot._pending_matchups.append((matchup, rnum, "deferred"))
                    teams_in_slot_map[si].add(ta)
                    teams_in_slot_map[si].add(tb)
                    still_deferred.pop(di)
                    any_placed = True
                    break
        return still_deferred

    # Remaining deferred matchups available as "safe ad-hoc" source
    remaining_deferred_weekday: list[tuple[Matchup, int]] = []
    remaining_deferred_weekend: list[tuple[Matchup, int]] = []

    if deferred_weekday:
        print(f"  {len(deferred_weekday)} weekday matchups deferred, rescheduling...")
        still = _fill_from_deferred(deferred_weekday, weekday_slots,
                                    weekday_teams_in_slot)
        if still:
            print(f"  {len(still)} weekday matchups could not be rescheduled")
            remaining_deferred_weekday = still
        else:
            print(f"  All weekday deferrals rescheduled")

    if deferred_weekend:
        print(f"  {len(deferred_weekend)} weekend matchups deferred, rescheduling...")
        still = _fill_from_deferred(deferred_weekend, weekend_slots,
                                    weekend_teams_in_slot)
        if still:
            print(f"  {len(still)} weekend matchups could not be rescheduled")
            remaining_deferred_weekend = still
        else:
            print(f"  All weekend deferrals rescheduled")

    # 2b. Fill idle teams from remaining deferred pool (safe ad-hoc),
    #     then truly invent pairings only as last resort.
    # Build global matchup counts from all placed matchups to avoid duplicates
    global_matchup_counts: dict[tuple[str, str], int] = defaultdict(int)
    for slot in weekday_slots + weekend_slots:
        if hasattr(slot, '_pending_matchups'):
            for m, _, _src in slot._pending_matchups:
                key = (min(m.team_a, m.team_b), max(m.team_a, m.team_b))
                global_matchup_counts[key] += 1

    def _lookup_safe_pool(team_a, team_b):
        """Check if a pairing exists in the safe pool. If found, remove it
        and return (round_number, "safe_adhoc"). Otherwise return None."""
        key = (min(team_a, team_b), max(team_a, team_b))
        for pool in (safe_pool_weekday, safe_pool_weekend):
            for pi, (pm, prnum) in enumerate(pool):
                pk = (min(pm.team_a, pm.team_b), max(pm.team_a, pm.team_b))
                if pk == key:
                    pool.pop(pi)
                    return (prnum, "safe_adhoc")
        return None

    def _fill_idle_from_pool(slots_list, teams_in_slot_map, deferred_pool):
        """Fill idle teams using remaining deferred matchups (safe ad-hoc).

        These are real round-robin pairings from overflow rounds that were
        never assigned to a slot, so they're guaranteed novel.
        Requires at least one team to be idle in the slot.
        """
        filled = 0
        still_available = list(deferred_pool)
        any_placed = True
        while any_placed:
            any_placed = False
            for si, slot in enumerate(slots_list):
                idle = set(
                    t for t in slot.available_teams
                    if t not in teams_in_slot_map[si]
                )
                if not idle:
                    continue
                i = 0
                while i < len(still_available):
                    m, rnum = still_available[i]
                    ta, tb = m.team_a, m.team_b
                    # At least one team must be idle
                    if ta not in idle and tb not in idle:
                        i += 1
                        continue
                    # Neither team can already be playing
                    if ta in teams_in_slot_map[si] or tb in teams_in_slot_map[si]:
                        i += 1
                        continue
                    # Both must be available in this slot
                    if ta not in slot.available_teams or tb not in slot.available_teams:
                        i += 1
                        continue
                    slot._pending_matchups.append((m, rnum, "safe_adhoc"))
                    teams_in_slot_map[si].add(ta)
                    teams_in_slot_map[si].add(tb)
                    idle.discard(ta)
                    idle.discard(tb)
                    key = (min(ta, tb), max(ta, tb))
                    global_matchup_counts[key] += 1
                    still_available.pop(i)
                    filled += 1
                    any_placed = True
                    break  # re-scan idle for this slot
        return filled, still_available

    def _invent_games(slots_list, teams_in_slot_map, slot_type):
        """Last resort: invent truly novel pairings for remaining idle teams."""
        invented = 0
        for si, slot in enumerate(slots_list):
            idle = sorted(
                t for t in slot.available_teams
                if t not in teams_in_slot_map[si]
            )
            if len(idle) < 2:
                continue

            # Build candidate pairs, preferring novel matchups
            if slot_type == "weekend":
                idle_north = [t for t in idle if teams[t].pool == "north"]
                idle_south = [t for t in idle if teams[t].pool == "south"]
                cross_candidates = []
                for tn in idle_north:
                    for ts in idle_south:
                        key = (min(tn, ts), max(tn, ts))
                        cross_candidates.append((global_matchup_counts[key], tn, ts))
                cross_candidates.sort()
                used = set()
                pairs = []
                for _, tn, ts in cross_candidates:
                    if tn not in used and ts not in used:
                        pairs.append((tn, ts))
                        used.add(tn)
                        used.add(ts)
                remaining = [t for t in idle if t not in used]
                same_candidates = []
                for i, t1 in enumerate(remaining):
                    for t2 in remaining[i + 1:]:
                        key = (min(t1, t2), max(t1, t2))
                        same_candidates.append((global_matchup_counts[key], t1, t2))
                same_candidates.sort()
                for _, t1, t2 in same_candidates:
                    if t1 not in used and t2 not in used:
                        pairs.append((t1, t2))
                        used.add(t1)
                        used.add(t2)
            else:
                pairs = []
                used = set()
                for pool_group in ("north", "south"):
                    pool_idle = [t for t in idle if teams[t].pool == pool_group]
                    candidates = []
                    for i, t1 in enumerate(pool_idle):
                        for t2 in pool_idle[i + 1:]:
                            key = (min(t1, t2), max(t1, t2))
                            candidates.append((global_matchup_counts[key], t1, t2))
                    candidates.sort()
                    for _, t1, t2 in candidates:
                        if t1 not in used and t2 not in used:
                            pairs.append((t1, t2))
                            used.add(t1)
                            used.add(t2)

            for ta, tb in pairs:
                m = Matchup(ta, tb)
                safe_source = _lookup_safe_pool(ta, tb)
                if safe_source:
                    slot._pending_matchups.append((m, safe_source[0], safe_source[1]))
                else:
                    slot._pending_matchups.append((m, 0, "adhoc"))
                teams_in_slot_map[si].add(ta)
                teams_in_slot_map[si].add(tb)
                key = (min(ta, tb), max(ta, tb))
                global_matchup_counts[key] += 1
                invented += 1
        return invented

    # First: fill idle teams from remaining deferrals + overflow (safe ad-hoc)
    # Remaining deferrals go first (higher priority — from assigned rounds),
    # then overflow rounds (never assigned to any slot).
    safe_pool_weekday = remaining_deferred_weekday + overflow_weekday
    safe_pool_weekend = remaining_deferred_weekend + overflow_weekend
    wd_safe, safe_pool_weekday = _fill_idle_from_pool(
        weekday_slots, weekday_teams_in_slot, safe_pool_weekday)
    we_safe, safe_pool_weekend = _fill_idle_from_pool(
        weekend_slots, weekend_teams_in_slot, safe_pool_weekend)

    # Then: truly invent pairings only for still-idle teams
    wd_invented = _invent_games(weekday_slots, weekday_teams_in_slot, "weekday")
    we_invented = _invent_games(weekend_slots, weekend_teams_in_slot, "weekend")

    if wd_safe or we_safe:
        print(f"  Safe ad-hoc (from deferred/overflow): "
              f"{wd_safe} weekday + {we_safe} weekend")
    if wd_invented or we_invented:
        print(f"  Invented ad-hoc (novel pairings): "
              f"{wd_invented} weekday + {we_invented} weekend")

    # ---- Step 3: Bye equalizer — swap high-BYE teams into games ----
    # If a team has 2+ BYEs while others have 0, swap the high-BYE team
    # into an existing game in a slot where they're idle, replacing one
    # of the two participants who has 0 BYEs. The displaced team takes
    # the BYE instead. This runs before assign_games so the swapped
    # matchup gets proper field/time/H-A assignment.

    def _fix_byes(all_slots, all_teams_in_slot):
        # Count matchups per team
        team_matchup_count: dict[str, int] = defaultdict(int)
        for slot_list in (weekday_slots, weekend_slots):
            for slot in slot_list:
                if not hasattr(slot, '_pending_matchups'):
                    continue
                for m, *_ in slot._pending_matchups:
                    team_matchup_count[m.team_a] += 1
                    team_matchup_count[m.team_b] += 1

        regular = [t for t in teams if not teams[t].weekday_only]
        if not regular:
            return 0

        # Target = most common count among regular teams
        count_freq: dict[int, int] = defaultdict(int)
        for t in regular:
            count_freq[team_matchup_count.get(t, 0)] += 1
        target = max(count_freq, key=count_freq.get)

        high_bye = sorted(
            (t for t in regular if team_matchup_count.get(t, 0) < target),
            key=lambda t: team_matchup_count.get(t, 0),
        )
        if not high_bye:
            return 0

        swaps = 0
        for bye_team in list(high_bye):
            needed = target - team_matchup_count.get(bye_team, 0)
            for _ in range(needed):
                best_swap = None
                best_score = None

                # Find slots where bye_team is available but idle
                for si, slot in enumerate(all_slots):
                    if bye_team not in slot.available_teams:
                        continue
                    if bye_team in all_teams_in_slot[si]:
                        continue

                    # Look at each game in this slot for swap candidates
                    for mi, entry in enumerate(slot._pending_matchups):
                        matchup, rnum = entry[0], entry[1]
                        ta, tb = matchup.team_a, matchup.team_b
                        # Try swapping out ta or tb
                        for swap_out, keep in [(ta, tb), (tb, ta)]:
                            # swap_out must have 0 BYEs (at target)
                            if team_matchup_count.get(swap_out, 0) < target:
                                continue
                            # swap_out must not be a high-bye team itself
                            if swap_out in high_bye:
                                continue
                            # bye_team plays against keep
                            new_key = (min(bye_team, keep), max(bye_team, keep))
                            new_count = global_matchup_counts.get(new_key, 0)
                            # Prefer swaps that don't create duplicate matchups
                            # (new_count == 0 is ideal)
                            # Also check pool compatibility for game type
                            if slot.slot_type == "weekend":
                                # weekends: prefer cross-pool
                                same_pool = (teams[bye_team].pool == teams[keep].pool)
                                pool_penalty = 1 if same_pool else 0
                            else:
                                # weekdays: must be same pool (intra)
                                if teams[bye_team].pool != teams[keep].pool:
                                    continue
                                pool_penalty = 0

                            score = (new_count, pool_penalty)
                            if best_score is None or score < best_score:
                                best_score = score
                                best_swap = (si, mi, swap_out, keep)

                if best_swap is None:
                    break

                si, mi, swap_out, keep = best_swap
                slot = all_slots[si]
                old_entry = slot._pending_matchups[mi]
                old_matchup = old_entry[0]

                # Replace the matchup
                new_matchup = Matchup(bye_team, keep)
                new_key = (min(bye_team, keep), max(bye_team, keep))
                safe_source = _lookup_safe_pool(bye_team, keep)
                if safe_source:
                    slot._pending_matchups[mi] = (new_matchup, safe_source[0], safe_source[1])
                else:
                    slot._pending_matchups[mi] = (new_matchup, 0, "adhoc")

                # Update tracking
                all_teams_in_slot[si].discard(swap_out)
                all_teams_in_slot[si].add(bye_team)

                # Update matchup counts
                old_key = (min(old_matchup.team_a, old_matchup.team_b),
                           max(old_matchup.team_a, old_matchup.team_b))
                global_matchup_counts[old_key] -= 1
                new_key = (min(bye_team, keep), max(bye_team, keep))
                global_matchup_counts[new_key] += 1

                # Update per-team counts
                team_matchup_count[swap_out] -= 1
                team_matchup_count[bye_team] += 1

                swaps += 1
                print(f"    Swap: {bye_team} replaces {swap_out} "
                      f"in W{slot.week_number}-{slot.slot_type} "
                      f"(vs {keep})")

        return swaps

    # Build unified slot + tracking lists for the fixer
    all_slots_combined = weekday_slots + weekend_slots
    all_teams_in_slot_combined = {}
    for i, _ in enumerate(weekday_slots):
        all_teams_in_slot_combined[i] = weekday_teams_in_slot[i]
    for i, _ in enumerate(weekend_slots):
        all_teams_in_slot_combined[len(weekday_slots) + i] = weekend_teams_in_slot[i]

    bye_swaps = _fix_byes(all_slots_combined, all_teams_in_slot_combined)
    if bye_swaps:
        print(f"  Bye equalizer: {bye_swaps} swaps")

    # ---- Step 4: Enforce max 1 bye per slot ----
    # After all the above, check each slot for idle available teams.
    # If >1 team is idle in a slot, pair them up with ad-hoc matchups.
    # Allow cross-pool pairing on weekdays when needed to avoid multiple byes.
    def _enforce_max_one_bye(slots_list, teams_in_slot_map, slot_type):
        extra_invented = 0
        for si, slot in enumerate(slots_list):
            idle = sorted(
                t for t in slot.available_teams
                if t not in teams_in_slot_map[si]
            )
            if len(idle) <= 1:
                continue
            # First pass: prefer same-pool pairings
            pairs = []
            used = set()
            candidates = []
            for i, t1 in enumerate(idle):
                for t2 in idle[i + 1:]:
                    key = (min(t1, t2), max(t1, t2))
                    same_pool = teams[t1].pool == teams[t2].pool
                    # Prefer same-pool (0) over cross-pool (1)
                    pool_penalty = 0 if same_pool else 1
                    candidates.append((pool_penalty, global_matchup_counts[key],
                                       t1, t2))
            candidates.sort()
            for _, _, t1, t2 in candidates:
                if t1 not in used and t2 not in used:
                    pairs.append((t1, t2))
                    used.add(t1)
                    used.add(t2)
            for ta, tb in pairs:
                m = Matchup(ta, tb)
                safe_source = _lookup_safe_pool(ta, tb)
                if safe_source:
                    slot._pending_matchups.append((m, safe_source[0], safe_source[1]))
                else:
                    slot._pending_matchups.append((m, 0, "adhoc"))
                teams_in_slot_map[si].add(ta)
                teams_in_slot_map[si].add(tb)
                key = (min(ta, tb), max(ta, tb))
                global_matchup_counts[key] += 1
                extra_invented += 1
        return extra_invented

    wd_extra = _enforce_max_one_bye(weekday_slots, weekday_teams_in_slot,
                                     "weekday")
    we_extra = _enforce_max_one_bye(weekend_slots, weekend_teams_in_slot,
                                     "weekend")
    if wd_extra or we_extra:
        print(f"  Bye enforcement (max 1 per slot): "
              f"{wd_extra} weekday + {we_extra} weekend extra matchups")

    # ---- Step 5: Equalize slot-level byes (spread ≤ 1) ----
    # Compute per-team slot byes and try swaps to reduce spread.
    def _compute_slot_byes():
        """Count idle-slot byes per team (excluding blackout/weekday-only)."""
        slot_bye_counts: dict[str, int] = defaultdict(int)
        for slots_list, tis_map in [(weekday_slots, weekday_teams_in_slot),
                                     (weekend_slots, weekend_teams_in_slot)]:
            for si, slot in enumerate(slots_list):
                for t in slot.available_teams:
                    if t not in tis_map[si]:
                        slot_bye_counts[t] += 1
        return slot_bye_counts

    def _equalize_slot_byes():
        """Swap matchups to reduce bye spread to ≤ 1."""
        swaps = 0
        for _pass in range(20):  # limit iterations
            bye_counts = _compute_slot_byes()
            all_byes = [bye_counts.get(t, 0) for t in teams]
            mn, mx = min(all_byes), max(all_byes)
            if mx - mn <= 1:
                break

            high_bye_teams = [t for t in teams if bye_counts.get(t, 0) == mx]
            low_bye_teams = [t for t in teams if bye_counts.get(t, 0) == mn]

            found = False
            for bye_team in high_bye_teams:
                if found:
                    break
                # Find a slot where bye_team is idle
                for slots_list, tis_map in [(weekday_slots, weekday_teams_in_slot),
                                             (weekend_slots, weekend_teams_in_slot)]:
                    if found:
                        break
                    for si, slot in enumerate(slots_list):
                        if found:
                            break
                        if bye_team not in slot.available_teams:
                            continue
                        if bye_team in tis_map[si]:
                            continue
                        # bye_team is idle here. Find a low-bye team to swap out
                        for mi, entry in enumerate(slot._pending_matchups):
                            matchup = entry[0]
                            if found:
                                break
                            for swap_out, keep in [(matchup.team_a, matchup.team_b),
                                                    (matchup.team_b, matchup.team_a)]:
                                if swap_out not in low_bye_teams:
                                    continue
                                # Check: can bye_team play keep?
                                new_key = (min(bye_team, keep), max(bye_team, keep))
                                # Do the swap
                                new_matchup = Matchup(bye_team, keep)
                                safe_source = _lookup_safe_pool(bye_team, keep)
                                if safe_source:
                                    slot._pending_matchups[mi] = (new_matchup, safe_source[0], safe_source[1])
                                else:
                                    slot._pending_matchups[mi] = (new_matchup, 0, "adhoc")
                                tis_map[si].discard(swap_out)
                                tis_map[si].add(bye_team)
                                old_key = (min(matchup.team_a, matchup.team_b),
                                           max(matchup.team_a, matchup.team_b))
                                global_matchup_counts[old_key] -= 1
                                global_matchup_counts[new_key] += 1
                                swaps += 1
                                found = True
                                break
            if not found:
                break
        return swaps

    bye_eq_swaps = _equalize_slot_byes()
    if bye_eq_swaps:
        bye_counts = _compute_slot_byes()
        all_byes = [bye_counts.get(t, 0) for t in teams]
        print(f"  Slot-bye equalizer: {bye_eq_swaps} swaps "
              f"(byes: {min(all_byes)}-{max(all_byes)})")

    # No balancing here — game count trimming happens post-assign_games

    # Report game counts for diagnostics
    wd_game_counts: dict[str, int] = defaultdict(int)
    we_game_counts: dict[str, int] = defaultdict(int)
    for slot in weekday_slots:
        for m, *_ in slot._pending_matchups:
            wd_game_counts[m.team_a] += 1
            wd_game_counts[m.team_b] += 1
    for slot in weekend_slots:
        for m, *_ in slot._pending_matchups:
            we_game_counts[m.team_a] += 1
            we_game_counts[m.team_b] += 1

    all_codes = list(teams.keys())
    regular_teams = [t for t in teams if not teams[t].weekday_only]

    wd_counts = sorted(set(wd_game_counts.get(t, 0) for t in all_codes))
    we_counts = sorted(set(we_game_counts.get(t, 0) for t in regular_teams))
    print(f"  Weekday matchups per team: {wd_counts}")
    print(f"  Weekend matchups per team: {we_counts}")

    return weekday_slots, weekend_slots, dict(bye_counts)


# ---------------------------------------------------------------------------
# Phase 3: Unified home/away + field/time assignment
# ---------------------------------------------------------------------------

def _get_field_candidates(
    host_code: str,
    other_code: str,
    slot: CalendarSlot,
    teams: dict[str, Team],
    leagues: dict[str, League],
    used_field_slots: set[tuple[str, str, str]],
) -> list[tuple[date, time, str]]:
    """Get available (date, time, field_name) options if host_code hosts."""
    host_league = leagues[teams[host_code].league_code]
    if not host_league.has_fields:
        return []
    is_weekend = slot.slot_type == "weekend"
    fields = host_league.weekend_fields if is_weekend else host_league.weekday_fields
    if not fields:
        return []

    host_team = teams[host_code]
    other_team = teams[other_code]

    # All fields are league-level, no team-specific ordering needed
    seen = set()
    deduped = []
    for f in fields:
        key = (f.field_name, f.day, f.start_time)
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    results = []
    for fs in deduped:
        for d in slot.dates:
            dow = DayOfWeek(d.weekday())
            # Field slot must match the actual day of week
            if dow != fs.day:
                continue
            if d in fs.exclude_dates:
                continue
            if dow in host_team.no_play_days or dow in other_team.no_play_days:
                continue
            if host_league.is_blacked_out(d):
                continue
            other_league = leagues[other_team.league_code]
            if other_league.is_blacked_out(d):
                continue
            fkey = (fs.field_name, str(d), str(fs.start_time))
            if fkey in used_field_slots:
                continue
            results.append((d, fs.start_time, fs.field_name))

    return results


def _try_rearrange_fields(
    ta: str, tb: str,
    slot: CalendarSlot,
    teams: dict[str, Team],
    leagues: dict[str, League],
    current_games: list[Game],
    used_field_slots: set[tuple[str, str, str]],
    game_length: int,
) -> list[tuple]:
    """Try to free up a field for (ta, tb) by moving another game.

    Looks at fields that WOULD work for ta/tb if they weren't occupied,
    checks if the occupying game can move to an alternative field from
    its own teams' leagues, and if so performs the swap.

    Returns a list of candidate tuples (home, away, host, date, time, field)
    or empty list if no rearrangement works.
    """
    # Collect all (field_name, date_str, time_str) that would work for ta/tb
    needed = []
    for proposed_home, proposed_away in [(ta, tb), (tb, ta)]:
        host_league = leagues[teams[proposed_home].league_code]
        if not host_league.has_fields:
            continue
        is_weekend = slot.slot_type == "weekend"
        fields = host_league.weekend_fields if is_weekend else host_league.weekday_fields
        for fs in fields:
            for d in slot.dates:
                dow = DayOfWeek(d.weekday())
                if dow != fs.day or d in fs.exclude_dates:
                    continue
                if dow in teams[proposed_home].no_play_days:
                    continue
                if dow in teams[proposed_away].no_play_days:
                    continue
                if host_league.is_blacked_out(d):
                    continue
                other_league = leagues[teams[proposed_away].league_code]
                if other_league.is_blacked_out(d):
                    continue
                fkey = (fs.field_name, str(d), str(fs.start_time))
                if fkey in used_field_slots:
                    needed.append((fkey, proposed_home, proposed_away))

    for fkey, proposed_home, proposed_away in needed:
        # Find the game occupying this field slot
        blocker = None
        blocker_idx = None
        for gi, g in enumerate(current_games):
            if g.unscheduled:
                continue
            gkey = (g.field_name, str(g.date), str(g.start_time))
            if gkey == fkey:
                blocker = g
                blocker_idx = gi
                break
        if blocker is None:
            continue

        # Can the blocker move to a different field (from its own teams' leagues)?
        # Keep fkey in the used set — the blocker must move to a genuinely
        # different slot, not back to its current one.
        temp_used = set(used_field_slots)
        # Try host team's fields first, then the other team's fields
        alt_fields = _get_field_candidates(
            blocker.host_team,
            blocker.away_team if blocker.host_team == blocker.home_team else blocker.home_team,
            slot, teams, leagues, temp_used,
        )
        if not alt_fields:
            other_team = blocker.away_team if blocker.host_team == blocker.home_team else blocker.home_team
            alt_fields = _get_field_candidates(
                other_team, blocker.host_team,
                slot, teams, leagues, temp_used,
            )
        if not alt_fields:
            continue

        alt_d, alt_t, alt_fname = alt_fields[0]
        # Move the blocker
        used_field_slots.discard(fkey)
        used_field_slots.add((alt_fname, str(alt_d), str(alt_t)))

        start_min = alt_t.hour * 60 + alt_t.minute
        end_min = start_min + game_length
        end_h = min(end_min // 60, 23)
        end_m = end_min % 60 if end_min // 60 < 24 else 59

        current_games[blocker_idx] = Game(
            home_team=blocker.home_team,
            away_team=blocker.away_team,
            host_team=blocker.host_team,
            date=alt_d,
            start_time=alt_t,
            end_time=time(end_h, end_m),
            field_name=alt_fname,
            round_number=blocker.round_number,
            game_type=blocker.game_type,
            week_number=blocker.week_number,
            game_source=blocker.game_source,
        )

        # Return the freed field as a candidate for ta/tb
        fname, dstr, tstr = fkey
        freed_date = date.fromisoformat(dstr)
        freed_time = time.fromisoformat(tstr)
        return [(proposed_home, proposed_away, proposed_home,
                 freed_date, freed_time, fname)]

    return []


def assign_games(
    slots: list[CalendarSlot],
    teams: dict[str, Team],
    leagues: dict[str, League],
    game_length: int,
    avoid_same_time_groups: list[frozenset] | None = None,
) -> list[CalendarSlot]:
    """Jointly assign home/away, host, field, and time for all matchups.

    For each matchup we pick (home, away, host, date, time, field) together.
    Rules:
    - home = host is the strong default
    - If only one team can host in this slot, they are home AND host
    - If both can host, pick based on cumulative home/away balance
    - Teams that structurally can't host (RAV, HLL weekdays) are away
      when their opponent can host, but still get balanced home counts
      via games where they DO host (RAV: never, so RAV is always away;
      HLL: weekends only)
    - League home cap: prefer not exceeding, but don't drop games for it
    - Same-league time conflicts: hard avoid for 2-team leagues, soft for others
    """
    ast_groups = avoid_same_time_groups or []
    # Build lookup: team -> set of groups it belongs to
    team_ast_groups: dict[str, list[frozenset]] = defaultdict(list)
    for group in ast_groups:
        for t in group:
            team_ast_groups[t].append(group)

    home_counts: dict[str, int] = defaultdict(int)
    away_counts: dict[str, int] = defaultdict(int)
    # Track league home games per slot for cap checking
    league_home_per_slot: dict[int, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for slot in slots:
        if not hasattr(slot, '_pending_matchups') or not slot._pending_matchups:
            slot.games = []
            continue

        is_weekend = slot.slot_type == "weekend"
        used_field_slots: set[tuple[str, str, str]] = set()
        # (date, start_time) -> set of team codes playing at that time
        time_teams: dict[tuple, set[str]] = defaultdict(set)
        # team_code -> {date: set of field_names} for same-day-different-field checks
        team_day_fields: dict[str, dict] = defaultdict(lambda: defaultdict(set))

        games = []

        # Sort matchups: most constrained first (fewest field candidates)
        matchup_list = list(slot._pending_matchups)

        def _matchup_flexibility(item):
            m = item[0]
            ta, tb = m.team_a, m.team_b
            count = 0
            for ha, aa in [(ta, tb), (tb, ta)]:
                count += len(_get_field_candidates(
                    ha, aa, slot, teams, leagues, set()))
            return count

        matchup_list.sort(key=_matchup_flexibility)

        for entry in matchup_list:
            matchup, rnum, source = entry[0], entry[1], entry[2] if len(entry) > 2 else "round"
            ta, tb = matchup.team_a, matchup.team_b
            ta_league = leagues[teams[ta].league_code]
            tb_league = leagues[teams[tb].league_code]

            # Build all candidate assignments: (home, away, host, date, time, field)
            candidates = []

            for proposed_home, proposed_away in [(ta, tb), (tb, ta)]:
                home_league_code = teams[proposed_home].league_code

                # Option A: home team hosts (the default / preferred)
                home_fields = _get_field_candidates(
                    proposed_home, proposed_away, slot, teams, leagues,
                    used_field_slots,
                )
                for d, t, fname in home_fields:
                    candidates.append((
                        proposed_home, proposed_away, proposed_home,
                        d, t, fname,
                    ))

                # Option B: away team hosts — when home team can't host,
                # use the away team's field and make THEM home instead.
                # The home/away optimization pass will rebalance later.
                if not home_fields:
                    for d, t, fname in _get_field_candidates(
                        proposed_away, proposed_home, slot, teams, leagues,
                        used_field_slots,
                    ):
                        candidates.append((
                            proposed_away, proposed_home, proposed_away,
                            d, t, fname,
                        ))

            if not candidates:
                # Try rearranging other games in this slot to free a field
                candidates = _try_rearrange_fields(
                    ta, tb, slot, teams, leagues, games,
                    used_field_slots, game_length,
                )

            if not candidates:
                # Truly unschedulable — diagnose why
                reasons = []
                for tc in (ta, tb):
                    tc_league = leagues[teams[tc].league_code]
                    is_weekend = slot.slot_type == "weekend"
                    tc_fields = tc_league.weekend_fields if is_weekend else tc_league.weekday_fields
                    if not tc_league.has_fields or not tc_fields:
                        reasons.append(f"{tc}: no {slot.slot_type} fields")
                    else:
                        # Check if blacked out on all field days
                        field_days = {f.day for f in tc_fields}
                        bo_days = []
                        for d in slot.dates:
                            dow = DayOfWeek(d.weekday())
                            if dow in field_days and tc_league.is_blacked_out(d):
                                bo_days.append(str(d))
                        if bo_days:
                            reasons.append(f"{tc}: blacked out on {', '.join(bo_days)}")
                        else:
                            # Fields exist but all occupied
                            reasons.append(f"{tc}: all fields occupied")
                reason_str = "; ".join(reasons)
                print(f"  UNSCHEDULED: {ta} vs {tb} "
                      f"(week {slot.week_number} {slot.slot_type}) — {reason_str}")
                games.append(Game(
                    home_team=ta,
                    away_team=tb,
                    host_team=ta,
                    date=date.min,
                    start_time=time(0, 0),
                    end_time=time(0, 0),
                    field_name="UNSCHEDULED",
                    round_number=rnum,
                    game_type=("crossover"
                               if teams[ta].pool != teams[tb].pool
                               else "intra"),
                    week_number=slot.week_number,
                    slot_type=slot.slot_type,
                    unscheduled=True,
                    game_source=source,
                ))
                continue

            # Score each candidate
            scored = []
            for home, away, host, d, t, fname in candidates:
                score = 0.0

                # 1) Home/away balance: prefer the assignment that reduces
                #    imbalance. Compute what the diff would be after this game.
                home_new_diff = (home_counts[home] + 1) - away_counts[home]
                away_new_diff = home_counts[away] - (away_counts[away] + 1)
                # We want both diffs close to 0
                balance_cost = abs(home_new_diff) + abs(away_new_diff)
                score += balance_cost * 10

                # 2) Home != host penalty (emergency hatch)
                if home != host:
                    score += 1000

                # 3) League home cap: penalize if this would exceed
                slot_id = id(slot)
                cur_league_home = league_home_per_slot[slot_id].get(
                    teams[home].league_code, 0
                )
                cap = (leagues[teams[home].league_code].weekend_home_cap
                       if is_weekend
                       else leagues[teams[home].league_code].weekday_home_cap)
                if cap > 0 and cur_league_home >= cap:
                    score += 50

                # 4) Same-time conflict (same-league soft penalty)
                tkey = (d, t)
                existing = time_teams.get(tkey, set())
                for et in existing:
                    if teams[home].league_code == teams[et].league_code:
                        score += 5
                    if teams[away].league_code == teams[et].league_code:
                        score += 5

                # 5) Avoid-same-day-different-field (hard penalty for groups)
                for tc in (home, away):
                    for group in team_ast_groups.get(tc, []):
                        for other in group:
                            if other == tc:
                                continue
                            other_fields = team_day_fields[other].get(d, set())
                            if other_fields and fname not in other_fields:
                                # Same day, different field — coaches can't travel
                                score += 10000

                # 5) Small tiebreaker: random to avoid systematic bias
                score += random.random() * 0.1

                scored.append((score, home, away, host, d, t, fname))

            scored.sort()
            _, home, away, host, game_date, game_time, field_name = scored[0]

            # Update tracking
            home_counts[home] += 1
            away_counts[away] += 1
            used_field_slots.add((field_name, str(game_date), str(game_time)))
            time_teams[(game_date, game_time)].add(home)
            time_teams[(game_date, game_time)].add(away)
            team_day_fields[home][game_date].add(field_name)
            team_day_fields[away][game_date].add(field_name)
            league_home_per_slot[id(slot)][teams[home].league_code] += 1

            start_min = game_time.hour * 60 + game_time.minute
            end_min = start_min + game_length
            end_h = min(end_min // 60, 23)
            end_m = end_min % 60 if end_min // 60 < 24 else 59

            games.append(Game(
                home_team=home,
                away_team=away,
                host_team=host,
                date=game_date,
                start_time=game_time,
                end_time=time(end_h, end_m),
                field_name=field_name,
                round_number=rnum,
                game_type=("crossover"
                           if teams[home].pool != teams[away].pool
                           else "intra"),
                week_number=slot.week_number,
                game_source=source,
            ))

        slot.games = games

    # --- Rescue pass: try to place UNSCHEDULED games in other slots ---
    # Collect all unscheduled games from all slots
    all_unscheduled = []
    for slot in slots:
        remaining = []
        for g in slot.games:
            if g.unscheduled:
                all_unscheduled.append((g, slot))
            else:
                remaining.append(g)
        slot.games = remaining

    if all_unscheduled:
        # Build per-slot used_field_slots index
        slot_used: dict[int, set] = {}
        slot_team_set: dict[int, set] = {}
        for slot in slots:
            used = set()
            playing = set()
            for g in slot.games:
                used.add((g.field_name, str(g.date), str(g.start_time)))
                playing.add(g.home_team)
                playing.add(g.away_team)
            slot_used[id(slot)] = used
            slot_team_set[id(slot)] = playing

        rescued = 0
        still_unscheduled = []
        for unsched_game, orig_slot in all_unscheduled:
            ta = unsched_game.home_team
            tb = unsched_game.away_team
            placed = False

            # Try every slot (same type preferred, then different type)
            for prefer_same_type in [True, False]:
                if placed:
                    break
                for slot in slots:
                    if placed:
                        break
                    if prefer_same_type:
                        if slot.slot_type != orig_slot.slot_type:
                            continue
                    else:
                        if slot.slot_type == orig_slot.slot_type:
                            continue

                    # Both teams must be available in this slot
                    if ta not in slot.available_teams or tb not in slot.available_teams:
                        continue
                    # Neither team should already play in this slot
                    playing = slot_team_set[id(slot)]
                    if ta in playing or tb in playing:
                        continue

                    used = slot_used[id(slot)]
                    # Try both home/away orientations
                    candidates = []
                    for proposed_home, proposed_away in [(ta, tb), (tb, ta)]:
                        # Option A: proposed_home hosts
                        for d, t, fname in _get_field_candidates(
                            proposed_home, proposed_away, slot, teams, leagues,
                            used,
                        ):
                            candidates.append((proposed_home, proposed_away,
                                               proposed_home, d, t, fname))
                        # Option B: proposed_away hosts (swap home designation)
                        if not candidates:
                            for d, t, fname in _get_field_candidates(
                                proposed_away, proposed_home, slot, teams,
                                leagues, used,
                            ):
                                candidates.append((proposed_away, proposed_home,
                                                   proposed_away, d, t, fname))

                    # Try rearranging existing games to free a field
                    if not candidates:
                        candidates = _try_rearrange_fields(
                            ta, tb, slot, teams, leagues, slot.games,
                            used, game_length,
                        )

                    if not candidates:
                        continue

                    # Pick best candidate (simplest: first one)
                    home, away, host, game_date, game_time, field_name = candidates[0]
                    start_min = game_time.hour * 60 + game_time.minute
                    end_min = start_min + game_length
                    end_h = min(end_min // 60, 23)
                    end_m = end_min % 60 if end_min // 60 < 24 else 59

                    new_game = Game(
                        home_team=home,
                        away_team=away,
                        host_team=host,
                        date=game_date,
                        start_time=game_time,
                        end_time=time(end_h, end_m),
                        field_name=field_name,
                        round_number=unsched_game.round_number,
                        game_type=("crossover"
                                   if teams[home].pool != teams[away].pool
                                   else "intra"),
                        week_number=slot.week_number,
                        game_source=unsched_game.game_source,
                    )
                    slot.games.append(new_game)
                    used.add((field_name, str(game_date), str(game_time)))
                    slot_team_set[id(slot)].add(ta)
                    slot_team_set[id(slot)].add(tb)
                    home_counts[home] += 1
                    away_counts[away] += 1
                    rescued += 1
                    placed = True

            if not placed:
                # Still can't place — keep as unscheduled in original slot
                still_unscheduled.append((unsched_game, orig_slot))

        for unsched_game, orig_slot in still_unscheduled:
            orig_slot.games.append(unsched_game)

        if rescued:
            print(f"  Rescued {rescued} unscheduled games by moving to other slots")
        if still_unscheduled:
            print(f"  {len(still_unscheduled)} games remain UNSCHEDULED")

    # --- Iterative home/away optimization via flips ---
    # Flip home/away on games to improve balance, maintaining home=host.
    # Skip teams that structurally can never host (e.g., RAV has no fields).
    all_game_refs = []
    for slot in slots:
        for gi in range(len(slot.games)):
            all_game_refs.append((slot, gi))

    # Teams that can never host at all (no fields) are structurally stuck
    # at 0 home games. Exclude them from imbalance calculations.
    never_host_teams = set()
    for t in teams:
        league = leagues[teams[t].league_code]
        if not league.has_fields:
            never_host_teams.add(t)

    def fixable_imbalance():
        return sum(
            max(0, abs(home_counts.get(t, 0) - away_counts.get(t, 0)) - 1)
            for t in teams if t not in never_host_teams
        )

    def team_diff(t):
        return home_counts.get(t, 0) - away_counts.get(t, 0)

    print(f"  Pre-optimization imbalance: {fixable_imbalance()} "
          f"(excluding {len(never_host_teams)} fieldless teams)")

    # Build index: team -> list of (slot, gi) for games involving that team
    team_game_idx: dict[str, list[tuple]] = defaultdict(list)
    for slot, gi in all_game_refs:
        game = slot.games[gi]
        team_game_idx[game.home_team].append((slot, gi))
        team_game_idx[game.away_team].append((slot, gi))

    def try_flip(slot, gi, allow_visitor_hosts=False) -> bool:
        """Try flipping home/away on a single game. Returns True if successful.

        If allow_visitor_hosts is True, the flip can keep the original field
        and mark the game as visitor-hosts (home_team != host_team) when the
        new home team can't host at their own field.
        """
        game = slot.games[gi]
        new_home = game.away_team
        new_away = game.home_team

        # Try to find a field for the new host
        temp_used = set()
        for gj, g2 in enumerate(slot.games):
            if gj != gi:
                temp_used.add((g2.field_name, str(g2.date), str(g2.start_time)))

        can_host = _can_host_in_slot(new_home, slot, teams, leagues)
        new_fields = []
        if can_host:
            new_fields = _get_field_candidates(
                new_home, new_away, slot, teams, leagues, temp_used,
            )

        if not new_fields and not allow_visitor_hosts:
            return False

        if new_fields:
            # Build date->team->fields map for this slot (excluding current game)
            slot_time_teams: dict[tuple, set[str]] = defaultdict(set)
            slot_day_fields: dict[str, dict] = defaultdict(lambda: defaultdict(set))
            for gj, g2 in enumerate(slot.games):
                if gj != gi:
                    slot_time_teams[(g2.date, g2.start_time)].add(g2.home_team)
                    slot_time_teams[(g2.date, g2.start_time)].add(g2.away_team)
                    slot_day_fields[g2.home_team][g2.date].add(g2.field_name)
                    slot_day_fields[g2.away_team][g2.date].add(g2.field_name)

            # Score field candidates — avoid same-day-different-field conflicts
            scored_fields = []
            for d, t, fname in new_fields:
                conflict = 0
                tkey = (d, t)
                for et in slot_time_teams.get(tkey, set()):
                    if teams[new_home].league_code == teams[et].league_code:
                        conflict += 5
                    if teams[new_away].league_code == teams[et].league_code:
                        conflict += 5
                # Check avoid-same-day-different-field for group members
                for tc in (new_home, new_away):
                    for group in team_ast_groups.get(tc, []):
                        for other in group:
                            if other == tc:
                                continue
                            other_fields = slot_day_fields[other].get(d, set())
                            if other_fields and fname not in other_fields:
                                conflict += 10000
                # Prefer same date as original game
                date_pref = 0 if d == game.date else 1
                scored_fields.append((conflict, date_pref, d, t, fname))

            scored_fields.sort()
            _, _, new_date, new_time, new_fname = scored_fields[0]
            new_host = new_home
        else:
            # Visitor-hosts fallback: keep original field/time, just flip home/away
            new_date = game.date
            new_time = game.start_time
            new_fname = game.field_name
            new_host = new_away  # original home team, now away, still hosting

        start_min = new_time.hour * 60 + new_time.minute
        end_min = start_min + game_length
        end_h = min(end_min // 60, 23)
        end_m = end_min % 60 if end_min // 60 < 24 else 59

        # Apply the flip
        home_counts[game.home_team] -= 1
        away_counts[game.away_team] -= 1
        home_counts[new_home] += 1
        away_counts[new_away] += 1

        slot.games[gi] = Game(
            home_team=new_home,
            away_team=new_away,
            host_team=new_host,
            date=new_date,
            start_time=new_time,
            end_time=time(end_h, end_m),
            field_name=new_fname,
            round_number=game.round_number,
            game_type=game.game_type,
            week_number=game.week_number,
            slot_type=game.slot_type,
            unscheduled=game.unscheduled,
            game_source=game.game_source,
        )
        return True

    iteration = 0
    stuck_teams: set[str] = set()  # teams we've tried and failed to fix

    for iteration in range(2000):
        if fixable_imbalance() == 0:
            break

        # Find most imbalanced fixable team
        worst_team = None
        worst_diff = 0
        for t in teams:
            if t in never_host_teams or t in stuck_teams:
                continue
            diff = team_diff(t)
            if abs(diff) > abs(worst_diff):
                worst_diff = diff
                worst_team = t

        if worst_team is None or abs(worst_diff) <= 1:
            break

        # Strategy 1: Simple single-game flip
        flipped = False
        game_refs = list(team_game_idx[worst_team])
        random.shuffle(game_refs)

        for slot, gi in game_refs:
            game = slot.games[gi]

            # Skip unscheduled games — they have no field to flip
            if game.unscheduled:
                continue

            # Only flip games where worst_team is on the wrong side
            if worst_diff > 0 and game.home_team != worst_team:
                continue
            if worst_diff < 0 and game.away_team != worst_team:
                continue

            # Check the other team won't get worse
            other = game.away_team if worst_diff > 0 else game.home_team
            if other in never_host_teams:
                continue
            other_old = abs(team_diff(other))
            # After flip: other gains ±2 in diff (gains 1 home, loses 1 away or vice versa)
            if worst_diff > 0:
                other_new = abs(team_diff(other) + 2)  # other becomes home
            else:
                other_new = abs(team_diff(other) - 2)  # other becomes away

            if other_new > 1 and other_new > other_old:
                continue

            if try_flip(slot, gi):
                flipped = True
                break

        if flipped:
            stuck_teams.discard(worst_team)
            continue

        # Strategy 2: Two-game swap — find a pair of games to flip
        # that together improve balance for all involved teams.
        # Look for: game1 where worst_team is home, game2 where the
        # displaced team from game1 is away in another game that can flip.
        if worst_diff > 0:
            # worst_team has too many home. Need to flip a game where
            # worst_team is home. If the other team (becomes new home)
            # would get too many home games, also flip one of THEIR
            # home games.
            for slot1, gi1 in game_refs:
                game1 = slot1.games[gi1]
                if game1.unscheduled:
                    continue
                if game1.home_team != worst_team:
                    continue

                other = game1.away_team
                if other in never_host_teams:
                    continue
                # After flipping game1: other gets +1 home, -1 away
                other_new_diff = team_diff(other) + 2
                if abs(other_new_diff) <= 1:
                    # Simple flip works for other too
                    if try_flip(slot1, gi1):
                        flipped = True
                        break
                    continue

                # other would become imbalanced — find a game where other
                # is home that we can also flip to compensate
                other_games = list(team_game_idx[other])
                random.shuffle(other_games)
                for slot2, gi2 in other_games:
                    if (slot2, gi2) == (slot1, gi1):
                        continue
                    game2 = slot2.games[gi2]
                    if game2.unscheduled:
                        continue
                    if game2.home_team != other:
                        continue
                    third = game2.away_team
                    if third in never_host_teams:
                        continue
                    third_new_diff = team_diff(third) + 2
                    if abs(third_new_diff) > 1 and abs(third_new_diff) > abs(team_diff(third)):
                        continue

                    # Try both flips
                    if try_flip(slot2, gi2):
                        if try_flip(slot1, gi1):
                            flipped = True
                            break
                        else:
                            # Undo the second flip
                            try_flip(slot2, gi2)
                if flipped:
                    break

        elif worst_diff < 0:
            # worst_team has too many away. Need to flip a game where
            # worst_team is away (making them home).
            for slot1, gi1 in game_refs:
                game1 = slot1.games[gi1]
                if game1.unscheduled:
                    continue
                if game1.away_team != worst_team:
                    continue

                other = game1.home_team
                if other in never_host_teams:
                    continue
                other_new_diff = team_diff(other) - 2
                if abs(other_new_diff) <= 1:
                    if try_flip(slot1, gi1):
                        flipped = True
                        break
                    continue

                # other would become too negative — find a game where
                # other is away that we can flip
                other_games = list(team_game_idx[other])
                random.shuffle(other_games)
                for slot2, gi2 in other_games:
                    if (slot2, gi2) == (slot1, gi1):
                        continue
                    game2 = slot2.games[gi2]
                    if game2.unscheduled:
                        continue
                    if game2.away_team != other:
                        continue
                    third = game2.home_team
                    if third in never_host_teams:
                        continue
                    third_new_diff = team_diff(third) - 2
                    if abs(third_new_diff) > 1 and abs(third_new_diff) > abs(team_diff(third)):
                        continue

                    if try_flip(slot2, gi2):
                        if try_flip(slot1, gi1):
                            flipped = True
                            break
                        else:
                            try_flip(slot2, gi2)
                if flipped:
                    break

        if not flipped:
            stuck_teams.add(worst_team)
            if len(stuck_teams) >= len([t for t in teams
                                         if t not in never_host_teams
                                         and abs(team_diff(t)) > 1]):
                break

    print(f"  Post-optimization imbalance: {fixable_imbalance()} "
          f"(after {iteration + 1} iterations)")

    # --- Second pass: visitor-hosts fallback for remaining imbalances ---
    # With visitor-hosts, even structurally fieldless teams (like RAV1) can
    # be designated "home" while playing at the opponent's field. This pass
    # fixes ALL remaining imbalances, including fieldless teams.
    def vh_imbalance():
        return sum(
            max(0, abs(home_counts.get(t, 0) - away_counts.get(t, 0)) - 1)
            for t in teams
        )

    if vh_imbalance() > 0:
        vh_flips = 0
        vh_stuck: set[str] = set()
        for _vh_iter in range(500):
            if vh_imbalance() == 0:
                break

            worst_team = None
            worst_diff = 0
            for t in teams:
                if t in vh_stuck:
                    continue
                diff = team_diff(t)
                if abs(diff) > abs(worst_diff):
                    worst_diff = diff
                    worst_team = t

            if worst_team is None or abs(worst_diff) <= 1:
                break

            game_refs = list(team_game_idx[worst_team])
            random.shuffle(game_refs)
            flipped = False

            # Strategy 1: Single flip with visitor-hosts
            for slot, gi in game_refs:
                game = slot.games[gi]
                if game.unscheduled:
                    continue
                if worst_diff > 0 and game.home_team != worst_team:
                    continue
                if worst_diff < 0 and game.away_team != worst_team:
                    continue

                other = game.away_team if worst_diff > 0 else game.home_team
                # In visitor-hosts mode, allow flipping even with fieldless
                # teams — the field stays the same, only home/away designation
                # changes. But check the other team's balance wouldn't worsen
                # unless they're structurally fieldless (already imbalanced).
                if other not in never_host_teams:
                    other_old = abs(team_diff(other))
                    if worst_diff > 0:
                        other_new = abs(team_diff(other) + 2)
                    else:
                        other_new = abs(team_diff(other) - 2)
                    if other_new > 1 and other_new > other_old:
                        continue

                if try_flip(slot, gi, allow_visitor_hosts=True):
                    flipped = True
                    vh_flips += 1
                    break

            # Strategy 2: Two-game swap with visitor-hosts
            if not flipped and worst_diff > 0:
                for slot1, gi1 in game_refs:
                    game1 = slot1.games[gi1]
                    if game1.home_team != worst_team:
                        continue
                    other = game1.away_team
                    other_new_diff = team_diff(other) + 2
                    if abs(other_new_diff) <= 1 or other in never_host_teams:
                        if try_flip(slot1, gi1, allow_visitor_hosts=True):
                            flipped = True
                            vh_flips += 1
                            break
                        continue
                    # Find a compensating flip for other
                    other_games = list(team_game_idx[other])
                    random.shuffle(other_games)
                    for slot2, gi2 in other_games:
                        if (slot2, gi2) == (slot1, gi1):
                            continue
                        game2 = slot2.games[gi2]
                        if game2.home_team != other:
                            continue
                        third = game2.away_team
                        if third not in never_host_teams:
                            third_new_diff = team_diff(third) + 2
                            if abs(third_new_diff) > 1 and abs(third_new_diff) > abs(team_diff(third)):
                                continue
                        if try_flip(slot2, gi2, allow_visitor_hosts=True):
                            if try_flip(slot1, gi1, allow_visitor_hosts=True):
                                flipped = True
                                vh_flips += 2
                                break
                            else:
                                try_flip(slot2, gi2, allow_visitor_hosts=True)
                    if flipped:
                        break

            elif not flipped and worst_diff < 0:
                for slot1, gi1 in game_refs:
                    game1 = slot1.games[gi1]
                    if game1.away_team != worst_team:
                        continue
                    other = game1.home_team
                    other_new_diff = team_diff(other) - 2
                    if abs(other_new_diff) <= 1 or other in never_host_teams:
                        if try_flip(slot1, gi1, allow_visitor_hosts=True):
                            flipped = True
                            vh_flips += 1
                            break
                        continue
                    other_games = list(team_game_idx[other])
                    random.shuffle(other_games)
                    for slot2, gi2 in other_games:
                        if (slot2, gi2) == (slot1, gi1):
                            continue
                        game2 = slot2.games[gi2]
                        if game2.away_team != other:
                            continue
                        third = game2.home_team
                        if third not in never_host_teams:
                            third_new_diff = team_diff(third) - 2
                            if abs(third_new_diff) > 1 and abs(third_new_diff) > abs(team_diff(third)):
                                continue
                        if try_flip(slot2, gi2, allow_visitor_hosts=True):
                            if try_flip(slot1, gi1, allow_visitor_hosts=True):
                                flipped = True
                                vh_flips += 2
                                break
                            else:
                                try_flip(slot2, gi2, allow_visitor_hosts=True)
                    if flipped:
                        break

            if not flipped:
                vh_stuck.add(worst_team)
                if len(vh_stuck) >= len([t for t in teams
                                         if abs(team_diff(t)) > 1]):
                    break

        if vh_flips:
            print(f"  Visitor-hosts fallback: {vh_flips} flips "
                  f"(imbalance now {vh_imbalance()})")

    # Report balance
    imb_teams = {t for t in teams
                 if abs(home_counts.get(t, 0) - away_counts.get(t, 0)) > 1}
    if imb_teams:
        print(f"  {len(imb_teams)} teams still imbalanced:")
        for t in sorted(imb_teams):
            print(f"    {t}: {home_counts.get(t,0)}H / {away_counts.get(t,0)}A")
    else:
        print("  All fixable teams balanced within 1")

    if never_host_teams:
        print(f"  Structurally fieldless (always away): "
              f"{', '.join(sorted(never_host_teams))}")

    # Report home-plays-away games (excluding structurally fieldless teams)
    home_away_games = []
    for slot in slots:
        for game in slot.games:
            if game.home_team != game.host_team:
                home_league = leagues[teams[game.home_team].league_code]
                # Structurally fieldless is expected, not an emergency
                if home_league.has_fields and (
                    slot.slot_type == "weekend"
                    or home_league.weekday_fields
                ):
                    home_away_games.append(game)

    if home_away_games:
        print(f"  WARNING: {len(home_away_games)} games where home team "
              f"plays away (non-structural):")
        for g in home_away_games:
            print(f"    {g.home_team} (home) at {g.host_team}'s "
                  f"{g.field_name} on {g.date}")

    return slots


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def schedule(config: dict, seed: int | None = None) -> list[Game]:
    """Generate a complete schedule.

    Returns sorted list of Games.
    """
    from d52sg.roundrobin import generate_round_robin, generate_crossover

    if seed is not None:
        random.seed(seed)

    season = config["season"]
    teams = config["teams"]
    leagues = config["leagues"]
    pools = config["pools"]

    north = pools["north"]
    south = pools["south"]

    # Phase 1: Round-robin matchups
    intra_north = generate_round_robin(north, seed=seed)
    intra_south = generate_round_robin(
        south, seed=(seed + 1) if seed is not None else None
    )
    # Filter weekday-only teams from crossover — they only play intra-pool
    # on weekdays. With 12 N × 12 S this gives 12 rounds of 12 games, 0 byes.
    crossover_north = [t for t in north if not teams[t].weekday_only]
    crossover = generate_crossover(
        crossover_north, south, seed=(seed + 2) if seed is not None else None
    )

    print(f"  Generated {len(intra_north)} north rounds, "
          f"{len(intra_south)} south rounds, "
          f"{len(crossover)} crossover rounds")

    # Phase 2: Calendar + round assignment
    all_slots = build_calendar(
        season["start_date"], season["end_date"], teams, leagues
    )
    weekday_slots = [s for s in all_slots if s.slot_type == "weekday"]
    weekend_slots = [s for s in all_slots if s.slot_type == "weekend"]

    print(f"  Calendar: {len(weekday_slots)} weekday slots, "
          f"{len(weekend_slots)} weekend slots")

    weekday_slots, weekend_slots, bye_counts = assign_rounds_to_slots(
        weekday_slots, weekend_slots,
        intra_north, intra_south, crossover,
        teams, leagues, pools,
    )

    # Report byes
    if bye_counts:
        min_bye = min(bye_counts.values()) if bye_counts else 0
        max_bye = max(bye_counts.values()) if bye_counts else 0
        print(f"  Byes: min={min_bye}, max={max_bye}")
        if max_bye - min_bye > 1:
            print(f"  Warning: bye spread > 1")
            for t in sorted(bye_counts, key=bye_counts.get, reverse=True):
                if bye_counts[t] > min_bye + 1:
                    print(f"    {t}: {bye_counts[t]} byes")

    # Count matchups
    all_assigned = weekday_slots + weekend_slots
    total_matchups = sum(
        len(slot._pending_matchups) for slot in all_assigned
        if hasattr(slot, '_pending_matchups')
    )
    print(f"  Total matchups: {total_matchups}")

    # Phase 3: Unified home/away + field/time assignment
    print("  Assigning home/away and fields...")
    assign_games(all_assigned, teams, leagues, season["game_length_minutes"],
                 avoid_same_time_groups=config.get("avoid_same_time_groups"))

    # Collect games
    all_games = []
    for slot in all_assigned:
        all_games.extend(slot.games)

    scheduled = [g for g in all_games if not g.unscheduled]
    unscheduled = [g for g in all_games if g.unscheduled]

    # Post-assign diagnostics (skip unscheduled games)
    wd_post: dict[str, int] = defaultdict(int)
    we_post: dict[str, int] = defaultdict(int)
    for g in scheduled:
        if g.date.weekday() < 5:
            wd_post[g.home_team] += 1
            wd_post[g.away_team] += 1
        else:
            we_post[g.home_team] += 1
            we_post[g.away_team] += 1

    all_team_codes = list(teams.keys())
    wd_vals = [wd_post.get(t, 0) for t in all_team_codes]
    wd_spread = max(wd_vals) - min(wd_vals) if wd_vals else 0
    if wd_spread > 1:
        print(f"  Weekday game count spread: {min(wd_vals)}-{max(wd_vals)}")

    all_games.sort(key=lambda g: (g.date, g.start_time))

    print(f"  Total games scheduled: {len(scheduled)}")
    if unscheduled:
        print(f"  UNSCHEDULED games: {len(unscheduled)}")
    return all_games
