"""赛中单轮实时积分、自动替补与动态排名快照。"""
from datetime import datetime, timedelta, timezone

from brain import api
from brain.market import to_float

MIN_FORMATION = {"DEF": 3, "MID": 2, "FWD": 1}


def _event_row(entry_history: dict, event_id: int):
    return next((row for row in (entry_history or {}).get("current", [])
                 if row.get("event") == event_id), None)


def _started(fixtures: list, event_id: int) -> bool:
    return any(fixture.get("event") == event_id and fixture.get("started")
               for fixture in fixtures or [])


def _fixture_progress(fixtures: list, event_id: int) -> dict:
    gw_fixtures = [f for f in (fixtures or []) if f.get("event") == event_id]
    started_count = sum(1 for f in gw_fixtures if f.get("started"))
    finished_count = sum(
        1 for f in gw_fixtures
        if f.get("finished") or f.get("finished_provisional")
    )
    team_fixtures = {}
    for f in gw_fixtures:
        for side in ("team_h", "team_a"):
            tid = f.get(side)
            if tid is not None:
                team_fixtures.setdefault(tid, []).append(f)
    return {
        "total": len(gw_fixtures),
        "started": started_count,
        "finished": finished_count,
        "team_fixtures": team_fixtures,
    }


def _team_state(team_id, team_fixtures: dict) -> tuple:
    """Return (any_started, all_finished) for a club in this GW."""
    if team_id is None:
        return False, False
    rows = team_fixtures.get(team_id)
    if not rows:
        # Blank GW: club has 0 scheduled matches, so this player will never play.
        return True, True
    any_started = any(bool(f.get("started")) for f in rows)
    all_finished = all(
        bool(f.get("finished") or f.get("finished_provisional")) for f in rows
    )
    return any_started, all_finished


def _live_points(picks: dict, live_data: dict, fixtures: list = None,
                 event_id: int = None, players_map: dict = None):
    """Calculate live GW score from /event/{gw}/live/ with auto-subs & VC fallback.

    Returns (gross_points, meta_dict) when full details are requested via _live_breakdown,
    or gross_points float here for backward compatibility.
    """
    result = _live_breakdown(picks, live_data, fixtures, event_id, players_map)
    return result["points"] if result is not None else None


def _live_breakdown(picks: dict, live_data: dict, fixtures: list = None,
                    event_id: int = None, players_map: dict = None):
    """Compute gross live score, auto-subs, captain promotion, and per-player breakdown."""
    if not isinstance(picks, dict) or not isinstance(live_data, dict):
        return None
    elements = live_data.get("elements") or []
    selected = sorted(picks.get("picks") or [], key=lambda x: x.get("position", 99))
    if not selected or not elements:
        return None

    stats_by_id = {}
    for row in elements:
        pid = row.get("id")
        if pid is None:
            continue
        st = row.get("stats") or {}
        stats_by_id[pid] = {
            "points": to_float(st.get("total_points")),
            "minutes": int(to_float(st.get("minutes"))),
        }
    if not stats_by_id:
        return None

    progress = _fixture_progress(fixtures or [], event_id) if event_id is not None else {
        "total": 0, "started": 0, "finished": 0, "team_fixtures": {},
    }
    team_fixtures = progress["team_fixtures"]
    has_player_meta = bool(players_map and fixtures and event_id is not None)

    active_chip = str(picks.get("active_chip") or "").strip().lower()
    is_bench_boost = active_chip in ("bboost", "bench_boost") or all(
        to_float(pk.get("multiplier")) >= 1 for pk in selected if pk.get("position", 0) > 11
    )

    eff_mult = {pk.get("element"): to_float(pk.get("multiplier")) for pk in selected}
    auto_sub_flag = {pk.get("element"): None for pk in selected}

    # 1. Captain -> Vice-Captain promotion when captain played 0 mins and club finished GW
    cap_pick = next((pk for pk in selected if pk.get("is_captain")), None)
    vice_pick = next((pk for pk in selected if pk.get("is_vice_captain")), None)
    captain_promoted = False
    if has_player_meta and cap_pick and vice_pick:
        cap_id = cap_pick.get("element")
        vice_id = vice_pick.get("element")
        cap_info = (players_map or {}).get(cap_id) or {}
        _, cap_done = _team_state(cap_info.get("team_id"), team_fixtures)
        cap_mins = stats_by_id.get(cap_id, {}).get("minutes", 0)
        vice_info = (players_map or {}).get(vice_id) or {}
        _, vice_done = _team_state(vice_info.get("team_id"), team_fixtures)
        vice_mins = stats_by_id.get(vice_id, {}).get("minutes", 0)
        if cap_mins == 0 and cap_done and (vice_mins > 0 or not vice_done):
            cap_mult = max(2.0, to_float(cap_pick.get("multiplier"), 2.0))
            eff_mult[cap_id] = 0.0
            eff_mult[vice_id] = cap_mult
            captain_promoted = True

    # 2. Automatic substitutions for 0-minute starters whose clubs finished all GW fixtures
    if has_player_meta and not is_bench_boost:
        starters = [pk for pk in selected if pk.get("position", 99) <= 11]
        bench = [pk for pk in selected if pk.get("position", 99) > 11]

        pos_of = lambda pid: ((players_map or {}).get(pid) or {}).get("pos")
        tid_of = lambda pid: ((players_map or {}).get(pid) or {}).get("team_id")

        active_xi = []
        sub_outs = []
        for pk in starters:
            pid = pk.get("element")
            mins = stats_by_id.get(pid, {}).get("minutes", 0)
            _, all_done = _team_state(tid_of(pid), team_fixtures)
            if mins == 0 and all_done and pos_of(pid) in ("GKP", "DEF", "MID", "FWD"):
                sub_outs.append(pid)
            else:
                active_xi.append(pid)

        used_bench = set()
        # Goalkeeper auto-sub
        for out_id in list(sub_outs):
            if pos_of(out_id) != "GKP":
                continue
            gk_sub = next(
                (b.get("element") for b in bench
                 if pos_of(b.get("element")) == "GKP"
                 and stats_by_id.get(b.get("element"), {}).get("minutes", 0) > 0),
                None,
            )
            if gk_sub is not None:
                eff_mult[out_id] = 0.0
                if eff_mult.get(gk_sub, 0.0) < 1.0:
                    eff_mult[gk_sub] = 1.0
                auto_sub_flag[out_id] = "out"
                auto_sub_flag[gk_sub] = "in"
                used_bench.add(gk_sub)
                active_xi.append(gk_sub)
                sub_outs.remove(out_id)

        # Outfield auto-subs preserving DEF>=3, MID>=2, FWD>=1
        outfield_bench = [
            b.get("element") for b in bench
            if pos_of(b.get("element")) in ("DEF", "MID", "FWD")
        ]
        remaining_outs = [pid for pid in sub_outs if pos_of(pid) != "GKP"]
        for idx, out_id in enumerate(remaining_outs):
            later_outs = remaining_outs[idx + 1:]
            for cand_id in outfield_bench:
                if cand_id in used_bench:
                    continue
                if stats_by_id.get(cand_id, {}).get("minutes", 0) <= 0:
                    continue
                # Check if a valid formation remains achievable with cand_id + active_xi
                current_counts = {"DEF": 0, "MID": 0, "FWD": 0}
                for pid in active_xi + [cand_id]:
                    p = pos_of(pid)
                    if p in current_counts:
                        current_counts[p] += 1
                avail_pool = [
                    b_id for b_id in outfield_bench
                    if b_id not in used_bench and b_id != cand_id
                    and stats_by_id.get(b_id, {}).get("minutes", 0) > 0
                ]
                slots_left = len(later_outs)
                # Maximum possible count per position from remaining bench/unreplaced slots
                feasible = True
                deficit_sum = 0
                for pos_key, min_req in MIN_FORMATION.items():
                    deficit = max(0, min_req - current_counts[pos_key])
                    deficit_sum += deficit
                    max_add = sum(1 for b_id in avail_pool if pos_of(b_id) == pos_key) + sum(
                        1 for o_id in later_outs if pos_of(o_id) == pos_key
                    )
                    if current_counts[pos_key] + max_add < min_req:
                        feasible = False
                        break
                if not feasible or deficit_sum > slots_left:
                    continue
                eff_mult[out_id] = 0.0
                if eff_mult.get(cand_id, 0.0) < 1.0:
                    eff_mult[cand_id] = 1.0
                auto_sub_flag[out_id] = "out"
                auto_sub_flag[cand_id] = "in"
                used_bench.add(cand_id)
                active_xi.append(cand_id)
                break

    total = sum(
        stats_by_id.get(pk.get("element"), {}).get("points", 0.0) *
        eff_mult.get(pk.get("element"), 0.0)
        for pk in selected
    )

    player_live = {}
    played_active = 0
    total_active = 0
    for pk in selected:
        pid = pk.get("element")
        mult = eff_mult.get(pk.get("element"), 0.0)
        st = stats_by_id.get(pid, {"points": 0.0, "minutes": 0})
        tid = ((players_map or {}).get(pid) or {}).get("team_id") if players_map else None
        started_flag, finished_flag = _team_state(tid, team_fixtures) if has_player_meta else (
            st["minutes"] > 0, False
        )
        if mult > 0:
            total_active += 1
            if started_flag or st["minutes"] > 0:
                played_active += 1
        player_live[str(pid)] = {
            "points": round(st["points"], 1),
            "effective_points": round(st["points"] * mult, 1),
            "minutes": st["minutes"],
            "multiplier": mult,
            "auto_sub": auto_sub_flag.get(pid),
            "started": bool(started_flag or st["minutes"] > 0),
            "finished": bool(finished_flag),
        }

    return {
        "points": round(total, 1),
        "played_count": played_active,
        "total_active": total_active or (15 if is_bench_boost else 11),
        "captain_promoted": captain_promoted,
        "fixtures_started": progress["started"],
        "fixtures_finished": progress["finished"],
        "fixtures_total": progress["total"],
        "players": player_live,
    }


def _recent_rank_snapshot(previous: dict, event_id: int, now: datetime):
    if not previous or previous.get("gw") != event_id:
        return None
    checked = previous.get("rank_checked_at")
    try:
        checked_at = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if previous.get("rank") is not None and to_float(previous.get("rank")) <= 0:
        return None
    if now - checked_at >= timedelta(hours=2):
        return None
    return previous


def snapshot(event_id: int, event: dict, fixtures: list, entry_history: dict,
             picks: dict, picks_gw: int, team_id: int,
             previous: dict = None, players_map: dict = None):
    """Return the latest live snapshot during active play or pre-data_checked settlement."""
    if not event or not _started(fixtures, event_id):
        return None
    # Once FPL has fully finalized and checked the GW (finished & data_checked),
    # official settled fields in history.json take over.
    if event.get("finished") and event.get("data_checked", True):
        return None

    row = _event_row(entry_history, event_id) or {}
    points = None
    points_source = None
    breakdown = None

    # Always prefer real-time /event/{gw}/live/ player totals!
    # FPL sets entry_history.current[].points = 0 at kickoff and only refreshes it
    # once overnight, so checking `points is None` previously skipped live scoring!
    if picks is not None and picks_gw == event_id:
        try:
            breakdown = _live_breakdown(
                picks, api.get_event_live(event_id),
                fixtures=fixtures, event_id=event_id, players_map=players_map,
            )
            if breakdown is not None:
                points = breakdown["points"]
                points_source = "fpl_event_live"
        except api.FplApiError:
            pass

    if points is None and row.get("points") is not None:
        points = row.get("points")
        points_source = "fpl_entry_history"

    transfer_cost = int(to_float(
        ((picks or {}).get("entry_history") or {}).get(
            "event_transfers_cost", row.get("event_transfers_cost", 0))
    ))
    net_points = round(to_float(points) - transfer_cost, 1) if points is not None else None

    rank = row.get("rank")
    if rank is not None and to_float(rank) <= 0:
        rank = None
    rank_source = "fpl_provisional" if rank is not None else None
    now = datetime.now(timezone.utc)
    rank_checked_at = None
    if rank is None:
        cached = _recent_rank_snapshot(previous, event_id, now)
        if cached:
            rank = cached.get("rank")
            rank_source = cached.get("rank_source")
            rank_checked_at = cached.get("rank_checked_at")
        else:
            rank_checked_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
            try:
                estimate = api.get_live_rank_estimate(team_id)
                try:
                    estimate_gw = int(estimate.get("curgw"))
                    estimate_rank = int(estimate.get("GWrank"))
                except (TypeError, ValueError):
                    estimate_gw, estimate_rank = None, None
                if estimate_gw == event_id and estimate_rank and estimate_rank > 0:
                    rank = estimate_rank
                    rank_source = "livefpl_estimate"
            except api.FplApiError:
                pass

    out = {
        "gw": event_id,
        "status": "provisional_finished" if event.get("finished") else "live",
        "points": to_float(points) if points is not None else None,
        "net_points": net_points,
        "transfer_cost": transfer_cost,
        "active_chip": (picks or {}).get("active_chip") if picks_gw == event_id else None,
        "rank": int(rank) if rank is not None else None,
        "points_source": points_source,
        "rank_source": rank_source,
        "rank_checked_at": rank_checked_at,
        "updated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    if breakdown:
        out.update({
            "played_count": breakdown["played_count"],
            "total_active": breakdown["total_active"],
            "captain_promoted": breakdown["captain_promoted"],
            "fixtures_started": breakdown["fixtures_started"],
            "fixtures_finished": breakdown["fixtures_finished"],
            "fixtures_total": breakdown["fixtures_total"],
            "players": breakdown["players"],
        })
    return out
