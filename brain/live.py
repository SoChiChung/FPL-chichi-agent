"""赛中单轮积分与动态排名快照。"""
from datetime import datetime, timedelta, timezone

from brain import api
from brain.market import to_float


def _event_row(entry_history: dict, event_id: int):
    return next((row for row in (entry_history or {}).get("current", [])
                 if row.get("event") == event_id), None)


def _started(fixtures: list, event_id: int) -> bool:
    return any(fixture.get("event") == event_id and fixture.get("started")
               for fixture in fixtures or [])


def _live_points(picks: dict, live_data: dict):
    """Calculate the current XI score from official live player totals."""
    if not isinstance(picks, dict) or not isinstance(live_data, dict):
        return None
    player_points = {
        row.get("id"): to_float((row.get("stats") or {}).get("total_points"))
        for row in live_data.get("elements") or []
        if row.get("id") is not None
    }
    selected = picks.get("picks") or []
    if not selected or not player_points:
        return None
    # Match entry_history.current[].points, which is the gross GW score before
    # transfer costs. The settled history uses that same FPL field.
    total = sum(player_points.get(pick.get("element"), 0.0) *
                to_float(pick.get("multiplier"))
                for pick in selected)
    return round(total, 1)


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
    if now - checked_at >= timedelta(hours=24):
        return None
    return previous


def snapshot(event_id: int, event: dict, fixtures: list, entry_history: dict,
             picks: dict, picks_gw: int, team_id: int,
             previous: dict = None):
    """Return the latest live snapshot, or None before kickoff/after settlement."""
    if not event or event.get("finished") or not _started(fixtures, event_id):
        return None

    row = _event_row(entry_history, event_id) or {}
    points = row.get("points")
    points_source = "fpl_entry_history" if points is not None else None

    if points is None and picks_gw == event_id:
        try:
            calculated = _live_points(picks, api.get_event_live(event_id))
            if calculated is not None:
                points = calculated
                points_source = "fpl_event_live"
        except api.FplApiError:
            # Preserve official entry_history points if the bulk live feed is
            # temporarily unavailable.
            pass

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

    return {
        "gw": event_id,
        "status": "live",
        "points": to_float(points) if points is not None else None,
        "rank": int(rank) if rank is not None else None,
        "points_source": points_source,
        "rank_source": rank_source,
        "rank_checked_at": rank_checked_at,
        "updated_at": now.isoformat(timespec="seconds").replace(
            "+00:00", "Z"),
    }
