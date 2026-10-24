"""Qualifying on the sim clock: session time instead of laps.

Same machinery as the race (sim.py): the backend is the only thing that moves
the clock (sim_clock.session_time_utc), the MCP qualifying tools read it on
every call, the clock pauses at decision points while the crew makes a call
in the background, and the Grader runs once the session is over.

The decision model is different:

    our car in the garage -> GO_OUT (on WET / INTERMEDIATE / slick) or WAIT
    our car on track      -> CONTINUE the lap or ABORT it

Decision points come from the weather feed and the running so far:
  - a segment goes green (Q1 / Q2 / Q3) while we're still in
  - rain starts or stops
  - the first car of the segment goes out on a different kind of tire
  - the scheduled time left drops past 6:00 and 3:00 with us in the garage
  - the field's best lap is clearly getting faster with us in the garage

What the real team did is read back once enough session time has passed:
did the driver go out within 2.5 minutes, and on what tire; or did the lap
in progress turn into a representative time or was it abandoned.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta

import mcp_client
import user_db
from team import PitwallTeam

log = logging.getLogger("pitwall.sim_quali")

TICK_REAL_S = 0.5                 # real seconds between clock steps
TIME_LEFT_TRIGGERS_S = (360, 180)  # 6:00 and 3:00 of scheduled time left
EVOLUTION_TRIGGER_S = -1.0         # field's best lap at least this much faster (last 5 min vs 5 before)
MIN_GAP_S = 90                     # session seconds between ordinary decision points
RESOLVE_AFTER_S = 150              # session seconds before the real outcome of a call is read
ABORT_IF_SLOWER = 1.05             # a lap this much slower than the driver's best so far counts as abandoned

WET_CLASS = {"WET": "wet", "INTERMEDIATE": "intermediate"}

_prev: dict | None = None          # the previous tick's qualifying state, for edge triggers
_tasks: set[asyncio.Task] = set()
# Per loaded session: when each segment goes green (the clock's schedule, never given
# to an agent), which green lights we've already called a decision for, and which
# segments have had their "times coming down" call.
_session: dict = {"key": None, "greens": [], "announced": set(), "evolution_called": set()}


def _ts(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _elapsed_s(clock: dict) -> int:
    return int((_ts(clock["session_time_utc"]) - _ts(clock["session_start_utc"])).total_seconds())


def _secs(laptime: str | None) -> float | None:
    if not laptime:
        return None
    m, s = laptime.split(":")
    return int(m) * 60 + float(s)


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# --------------------------------------------------------------------------
# Setup and the clock
# --------------------------------------------------------------------------


async def setup(race_id: int, driver_id: str) -> dict:
    global _prev
    info = await mcp_client.call("get_session_info", {"race_id": race_id, "kind": "qualifying"})
    if info.get("refused"):
        raise ValueError(info["reason"])
    user_db.clear_decisions(race_id, driver_id, "qualifying")
    _prev = None
    _session.update(key=(race_id, driver_id), greens=info.get("segment_starts_utc", []), announced=set(),
                    evolution_called=set())
    return user_db.set_clock(race_id=race_id, driver_id=driver_id, session_kind="qualifying", current_lap=0,
                             total_laps=0, running=0, session_start_utc=info["start_utc"],
                             session_end_utc=info["end_utc"], session_time_utc=info["start_utc"])


def over(clock: dict) -> bool:
    return clock["session_time_utc"] >= clock["session_end_utc"]


async def tick(clock: dict) -> None:
    """One clock step (called by sim._loop while running and nothing is pending)."""
    await asyncio.sleep(TICK_REAL_S)
    clock = user_db.get_clock()
    if not clock["running"] or clock["session_kind"] != "qualifying":
        return
    await _load_session(clock)
    before = _ts(clock["session_time_utc"])
    now = min(before + timedelta(seconds=clock["quali_speed"] * TICK_REAL_S), _ts(clock["session_end_utc"]))
    # stop one second before a segment's green light, so "go out now, on what?" is
    # asked while every car is still in the garage
    green = next(((i + 1, _ts(g)) for i, g in enumerate(_session["greens"])
                  if before < _ts(g) - timedelta(seconds=1) <= now and i + 1 not in _session["announced"]), None)
    if green:
        now = green[1] - timedelta(seconds=1)
    clock = user_db.set_clock(session_time_utc=_iso(now))
    await _resolve_outcomes(clock)
    if over(clock):
        user_db.set_clock(running=0)
        start_grading(clock)
        return
    if green:
        _session["announced"].add(green[0])
        state = await mcp_client.call("get_quali_state", {})
        if _still_in(state, green[0]):
            ask(clock, f"Q{green[0]} is about to go green")
        return
    trigger = await _detect_trigger(clock)
    if trigger:
        ask(clock, trigger)


async def _load_session(clock: dict) -> None:
    """After a backend restart, reload the session schedule."""
    key = (clock["race_id"], clock["driver_id"])
    if _session["key"] != key:
        info = await mcp_client.call("get_session_info", {"race_id": clock["race_id"], "kind": "qualifying"})
        _session.update(key=key, greens=info.get("segment_starts_utc", []), announced=set(), evolution_called=set())
        now = clock["session_time_utc"]
        _session["announced"] = {i + 1 for i, g in enumerate(_session["greens"]) if g <= now}


def _still_in(state: dict, segment: int) -> bool:
    """Is our driver in the segment about to start? Before Q1 everyone is; before
    Q2 / Q3 it's the top 15 / top 10 on the lap times of the segment just run."""
    if segment == 1:
        return True
    me = next((c for c in state.get("order", []) if c["is_my_driver"]), None)
    through = state.get("cars_through")
    return bool(me and me["best"] and through and me["position"] <= through)


# --------------------------------------------------------------------------
# Decision points
# --------------------------------------------------------------------------


async def _detect_trigger(clock: dict) -> str | None:
    global _prev
    now = await mcp_client.call("get_quali_state", {})
    prev, _prev = (_prev or {"segment": None}), now
    if now.get("refused") or prev.get("refused"):
        return None
    me = now.get("my_status")
    if me in ("eliminated", "unknown"):
        return None
    seg = now.get("segment")

    # always worth a call (a green light we already stopped for isn't asked twice)
    if seg and seg != prev.get("segment") and now.get("running") and int(seg[1]) not in _session["announced"]:
        return f"{seg} is green"
    w, pw = now.get("weather") or {}, prev.get("weather")
    if pw is not None:
        if w.get("raining") and not pw.get("raining"):
            return "Rain has started"
        if pw.get("raining") and not w.get("raining"):
            return "Rain has stopped"
    if not now.get("running"):
        return None

    # ordinary decision points, spaced out
    past = user_db.decisions_for(clock["race_id"], clock["driver_id"], "qualifying")
    if past and _elapsed_s(clock) - past[-1]["lap"] < MIN_GAP_S:
        return None

    # first car of this segment out on a different kind of tire
    def classes(state: dict) -> set[str]:
        return {WET_CLASS.get(c, "slick") for c in (state.get("on_track") or {}).get("by_compound", {})}
    if seg == prev.get("segment"):
        new = classes(now) - classes(prev)
        seen = {WET_CLASS.get(o["compound"], "slick") for o in prev.get("order", [])
                if o["compound"] and o["laps_this_segment"]}
        new -= seen
        if new:
            cls = sorted(new)[0]
            car = next((o for o in now["order"] if o["status"] != "garage"
                        and WET_CLASS.get(o["compound"], "slick") == cls), None)
            if car and not car["is_my_driver"]:
                return f"{car['code']} has gone out on {car['compound']} tires, the first car on {cls}s this segment"

    if me == "garage":
        left, pleft = now.get("scheduled_time_left_s"), prev.get("scheduled_time_left_s")
        for mark in TIME_LEFT_TRIGGERS_S:
            if left is not None and pleft is not None and left <= mark < pleft:
                return f"{mark // 60}:00 of scheduled time left in {seg} and we're in the garage"
        evo = now.get("track_evolution") or {}
        if (evo.get("direction") == "improving" and (evo.get("change_s") or 0) <= EVOLUTION_TRIGGER_S
                and seg not in _session["evolution_called"]):
            _session["evolution_called"].add(seg)
            return "The field's lap times are coming down"
    return None


def ask(clock: dict, trigger: str) -> int:
    """Pause the clock and get a call from the crew in the background."""
    user_db.set_clock(running=0)
    decision_id = user_db.new_decision(clock["race_id"], clock["driver_id"], _elapsed_s(clock), trigger,
                                       session_kind="qualifying", at_time_utc=clock["session_time_utc"])
    _spawn(_run_team(decision_id, trigger))
    return decision_id


async def _run_team(decision_id: int, trigger: str) -> None:
    state = await mcp_client.call("get_quali_state", {})
    if trigger.endswith("is about to go green"):
        when = f"{trigger.split()[0]} green light"
    else:
        when = (f"{state.get('segment')}, {state.get('elapsed')} in" if state.get("running")
                else state.get("phase") or "before the session")
    user_db.update_decision(decision_id, actual={"pending": True, "status_at_call": state.get("my_status"),
                                                 "when": when})
    try:
        async with PitwallTeam("qualifying") as team:
            result = await team.decide(trigger, 0, when=when)
        s = result.strategy
        user_db.update_decision(
            decision_id, status="ready", strategy_call=s.call, strategy_reason=s.reason,
            engineer_message=result.message,
            evidence={**s.model_dump(exclude={"call", "reason"}), "when": when,
                      "rival_watch": result.rivals.model_dump() if result.rivals else None,
                      "tool_calls": result.tool_log, "grounding_rejections": result.rejections,
                      "grounding_cleanups": result.cleanups,
                      "usage": result.usage})
    except Exception as exc:
        log.error("team error:\n%s", traceback.format_exc())
        user_db.update_decision(decision_id, status="error", error=f"{exc.__class__.__name__}: {exc}")


def make_call(decision: dict, call: str, tire: str | None) -> dict:
    status = (decision.get("actual") or {}).get("status_at_call")
    allowed = {"garage": {"GO_OUT", "WAIT"}}.get(status, {"CONTINUE", "ABORT"})
    if call not in allowed:
        raise ValueError(f"Our car is {status}: the choices are {' or '.join(sorted(allowed))}.")
    if call == "GO_OUT" and not tire:
        raise ValueError("Pick a tire to go out on.")
    user_db.update_decision(decision["id"], user_call=call, user_tire=tire if call == "GO_OUT" else None,
                            status="decided")
    return user_db.set_clock(running=1)


# --------------------------------------------------------------------------
# What the real team did
# --------------------------------------------------------------------------


async def _resolve_outcomes(clock: dict) -> None:
    now_s = _elapsed_s(clock)
    pending = [d for d in user_db.decisions_for(clock["race_id"], clock["driver_id"], "qualifying")
               if (d.get("actual") or {}).get("pending") and now_s >= d["lap"] + RESOLVE_AFTER_S]
    if not pending:
        return
    laps = (await mcp_client.call("get_quali_driver", {"driver": clock["driver_id"]})).get("laps", [])
    for d in pending:
        t, status = d["lap"], d["actual"].get("status_at_call")
        actual = {**d["actual"], "pending": False}
        if status == "garage":
            went = next((l for l in laps if t < l["started_at_s"] <= t + RESOLVE_AFTER_S), None)
            actual.update({"action": "GO_OUT", "tire": went["compound"],
                           "detail": f"went out {went['started_at_s'] - t}s later on {went['compound']}"}
                          if went else {"action": "WAIT", "tire": None,
                                        "detail": f"stayed in for the next {RESOLVE_AFTER_S // 60}:{RESOLVE_AFTER_S % 60:02d}"})
        else:
            cur = next((l for l in reversed(laps) if l["started_at_s"] <= t), None)
            target = cur
            if cur and cur["out_lap"]:
                target = next((l for l in laps if l["started_at_s"] > cur["started_at_s"]), None)
            best_before = min((_secs(l["time"]) for l in laps
                               if l["time"] and l["started_at_s"] < (cur or {}).get("started_at_s", t)), default=None)
            tt = _secs(target["time"]) if target else None
            if tt is None or (best_before and tt > best_before * ABORT_IF_SLOWER):
                actual.update({"action": "ABORT", "detail": "no representative time from that run"
                               + (f" ({target['time']})" if target and target["time"] else "")})
            else:
                actual.update({"action": "CONTINUE", "detail": f"completed it: {target['time']} on {target['compound']}"})
        user_db.update_decision(d["id"], actual=actual, actual_pitted=None)


# --------------------------------------------------------------------------
# Scorecard
# --------------------------------------------------------------------------


def _label(call: str | None, tire: str | None = None) -> str:
    base = {"GO_OUT": "GO OUT", "WAIT": "WAIT", "CONTINUE": "CONTINUE", "ABORT": "ABORT"}.get(call or "", "-")
    return f"{base} on {tire}" if call == "GO_OUT" and tire else base


def score(decisions: list[dict]) -> dict:
    graded = [d for d in decisions if d["user_call"] and d["strategy_call"] and (d.get("actual") or {}).get("action")]
    n = len(graded)

    def rate(k: int, of: int | None = None) -> dict:
        of = n if of is None else of
        return {"matched": k, "of": of, "pct": round(100 * k / of) if of else None}

    real = lambda d: d["actual"]["action"]  # noqa: E731
    s_tire = lambda d: (d.get("evidence") or {}).get("tire_to_fit")  # noqa: E731
    both_out = [d for d in graded if real(d) == "GO_OUT" and d["user_call"] == "GO_OUT"]
    strat_out = [d for d in graded if real(d) == "GO_OUT" and d["strategy_call"] == "GO_OUT"]
    return {
        "decision_points": n,
        "you_vs_real": rate(sum(d["user_call"] == real(d) for d in graded)),
        "strategy_vs_real": rate(sum(d["strategy_call"] == real(d) for d in graded)),
        "you_vs_strategy": rate(sum(d["user_call"] == d["strategy_call"] for d in graded)),
        "your_tire_vs_real": rate(sum(d["user_tire"] == d["actual"]["tire"] for d in both_out), len(both_out)),
        "strategy_tire_vs_real": rate(sum(s_tire(d) == d["actual"]["tire"] for d in strat_out), len(strat_out)),
        "rows": [{"n": i + 1, "lap": i + 1, "when": d["actual"].get("when"), "trigger": d["trigger"],
                  "strategy": _label(d["strategy_call"], s_tire(d)), "you": _label(d["user_call"], d["user_tire"]),
                  "real": _label(real(d), d["actual"].get("tire")), "real_detail": d["actual"].get("detail"),
                  "strategy_ok": d["strategy_call"] == real(d), "you_ok": d["user_call"] == real(d)}
                 for i, d in enumerate(graded)],
    }


def start_grading(clock: dict) -> None:
    user_db.save_scorecard(clock["race_id"], clock["driver_id"], "grading", session_kind="qualifying")
    _spawn(_grade(clock["race_id"], clock["driver_id"]))


async def _grade(race_id: int, driver_id: str) -> None:
    stats = score(user_db.decisions_for(race_id, driver_id, "qualifying"))
    try:
        final = await mcp_client.call("get_quali_final")
        me = next(r for r in final["classification"] if r["driver_id"] == driver_id)
        stats["real_finish"] = {"pos": str(me["position"]), "q1": me["q1"], "q2": me["q2"], "q3": me["q3"]}
        stats["real_best"] = final["my_best_lap"]
        if not stats["rows"]:
            user_db.save_scorecard(race_id, driver_id, "ready", stats=stats, session_kind="qualifying",
                                   verdict={"headline": "No decision points were called, so there is nothing to grade."})
            return
        table = "\n".join(f"#{r['n']} | {r['when']} | {r['trigger']} | Strategy: {r['strategy']} | You: {r['you']} | "
                          f"Real team: {r['real']} ({r['real_detail']})" for r in stats["rows"])
        rates = "\n".join(f"{k}: {v['matched']}/{v['of']}" for k, v in stats.items()
                          if isinstance(v, dict) and "matched" in v)
        async with PitwallTeam("qualifying") as team:
            verdict, tool_log, usage = await team.grade(table, rates, {r["n"] for r in stats["rows"]})
        user_db.save_scorecard(race_id, driver_id, "ready", stats=stats, session_kind="qualifying",
                               verdict={**verdict.model_dump(), "tool_calls": tool_log, "usage": usage})
    except Exception as exc:
        log.error("grader error:\n%s", traceback.format_exc())
        user_db.save_scorecard(race_id, driver_id, "error", stats=stats, session_kind="qualifying",
                               error=f"{exc.__class__.__name__}: {exc}")
