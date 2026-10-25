"""The sim clock: steps a race lap by lap and stops at decision points.

The clock row lives in the user DB (user_db.sim_clock). This module is the
only thing that moves it. Each tick:

  1. current_lap += 1 (the MCP tools' view of "now" moves with it)
  2. any earlier decision whose lap has now passed gets its real outcome
  3. the new lap is checked for a decision point; if there is one the clock
     pauses and the agent team is asked for a call in the background

Everything about the race is read through the MCP tools, so the clock is
held to the same no-future rule as the agents.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

import mcp_client
import sim_quali
import sim_sprint
import user_db
from team import PitwallTeam

log = logging.getLogger("pitwall.sim")

# Tire age at which a stint becomes worth a look, then every RECHECK laps after.
TIRE_CHECK_AGE = {"SOFT": 14, "MEDIUM": 20, "HARD": 28, "INTERMEDIATE": 18, "WET": 15}
TIRE_RECHECK_EVERY = 6
# No two automatic decision points closer than this, unless the weather turns.
MIN_LAPS_BETWEEN = 3
# No automatic decision points this close to the flag.
NO_CALLS_IN_LAST = 3

_task: asyncio.Task | None = None
_agent_task: asyncio.Task | None = None


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def start_loop() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop_loop() -> None:
    if _task:
        _task.cancel()


async def _loop() -> None:
    while True:
        try:
            clock = user_db.get_clock()
            if not clock["running"] or clock["race_id"] is None or pending_decision(clock):
                await asyncio.sleep(0.2)
                continue
            if clock["session_kind"] == "qualifying":
                await sim_quali.tick(clock)
                continue
            await asyncio.sleep(clock["seconds_per_lap"])
            clock = user_db.get_clock()
            if not clock["running"] or pending_decision(clock):
                continue
            await _advance(clock)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("sim loop error:\n%s", traceback.format_exc())
            user_db.set_clock(running=0)
            await asyncio.sleep(1)


async def _advance(clock: dict) -> None:
    lap = clock["current_lap"] + 1
    clock = user_db.set_clock(current_lap=lap, running=int(lap < clock["total_laps"]))
    await _resolve_outcomes(clock)
    if lap >= clock["total_laps"]:
        start_grading(clock)
        return
    trigger = await _detect_trigger(clock)
    if trigger:
        ask(clock, trigger)


# --------------------------------------------------------------------------
# Decision points
# --------------------------------------------------------------------------


def pending_decision(clock: dict) -> dict | None:
    if clock["race_id"] is None:
        return None
    for d in reversed(user_db.decisions_for(clock["race_id"], clock["driver_id"], clock["session_kind"])):
        if d["user_call"] is None and d["status"] in ("thinking", "ready"):
            return d
    return None


async def _detect_trigger(clock: dict) -> str | None:
    lap, me_id, kind = clock["current_lap"], clock["driver_id"], clock["session_kind"]
    sprint = kind == "sprint"
    tire_age = sim_sprint.TIRE_CHECK_AGE if sprint else TIRE_CHECK_AGE
    recheck = sim_sprint.TIRE_RECHECK_EVERY if sprint else TIRE_RECHECK_EVERY
    min_gap = sim_sprint.MIN_LAPS_BETWEEN if sprint else MIN_LAPS_BETWEEN
    if lap < 2 or lap > clock["total_laps"] - (sim_sprint.NO_CALLS_IN_LAST if sprint else NO_CALLS_IN_LAST):
        return None
    now = await mcp_client.call("get_race_state", {"lap": lap})
    before = await mcp_client.call("get_race_state", {"lap": lap - 1})
    cars = now.get("cars", [])
    me = next((c for c in cars if c["is_my_driver"]), None)
    if me is None:
        return None  # out of the race
    if me["pitted_end_of_lap"] or me["out_lap"]:
        return None  # just been in

    # weather turning is always worth a call
    w_now, w_before = now.get("weather") or {}, before.get("weather") or {}
    if w_now.get("raining") and not w_before.get("raining"):
        return "Rain has started"
    if w_before.get("raining") and not w_now.get("raining"):
        return "Rain has stopped; the track may start drying"

    past = user_db.decisions_for(clock["race_id"], me_id, kind)
    if past and lap - past[-1]["lap"] < min_gap:
        return None

    pos = me["position"]
    for c in cars:
        if c["pitted_end_of_lap"] and c["position"] in (pos - 1, pos + 1):
            where = "ahead" if c["position"] < pos else "behind"
            return f"{c['code']}, the car directly {where} (P{c['position']}), just boxed"
    boxed = [c["code"] for c in cars if c["pitted_end_of_lap"] and not c["is_my_driver"]]
    if len(boxed) >= 3:
        return f"{len(boxed)} cars boxed this lap ({', '.join(boxed)})"

    threshold = tire_age.get(me["compound"] or "", 20)
    age = me["tire_age"] or 0
    if age >= threshold and (age - threshold) % recheck == 0:
        return f"Our {(me['compound'] or 'tires').lower()} tires are {age} laps old"
    if sprint:
        return sim_sprint.battle_trigger(cars, me)
    return None


def ask(clock: dict, trigger: str) -> int:
    """Pause the clock and get a strategy call in the background."""
    global _agent_task
    user_db.set_clock(running=0)
    decision_id = user_db.new_decision(clock["race_id"], clock["driver_id"], clock["current_lap"], trigger,
                                       session_kind=clock["session_kind"])
    _agent_task = asyncio.create_task(_run_team(decision_id, trigger, clock["current_lap"], clock["session_kind"]))
    return decision_id


async def _run_team(decision_id: int, trigger: str, lap: int, kind: str = "race") -> None:
    try:
        async with PitwallTeam(kind) as team:
            result = await team.decide(trigger, lap)
        s = result.strategy
        user_db.update_decision(
            decision_id, status="ready", strategy_call=s.call, strategy_reason=s.reason,
            engineer_message=result.message,
            evidence={**s.model_dump(exclude={"call", "reason"}),
                      "rival_watch": result.rivals.model_dump() if result.rivals else None,
                      "tool_calls": result.tool_log,
                      "grounding_rejections": result.rejections,
                      "grounding_cleanups": result.cleanups,
                      "jargon": result.jargon,
                      "usage": result.usage})
    except Exception as exc:
        log.error("team error:\n%s", traceback.format_exc())
        user_db.update_decision(decision_id, status="error", error=f"{exc.__class__.__name__}: {exc}")


def continue_watching() -> dict:
    """The user has read the explanation: the replay carries on. Nobody makes a call;
    what the real team did is filled in once that lap has been played."""
    clock = user_db.get_clock()
    d = pending_decision(clock)
    if d is None:
        raise ValueError("There is no key moment waiting.")
    if d["status"] == "thinking":
        raise ValueError("The explanation is still being written.")
    user_db.update_decision(d["id"], user_call="WATCHED", status="decided")
    return user_db.set_clock(running=1)


def make_call(call: str, tire: str | None = None) -> dict:
    clock = user_db.get_clock()
    d = pending_decision(clock)
    if d is None:
        raise ValueError("There is no decision waiting for a call.")
    if clock["session_kind"] == "qualifying":
        return sim_quali.make_call(d, call, tire)
    if clock["session_kind"] == "sprint":
        return sim_sprint.make_call(d, call, tire)
    if call not in ("BOX", "STAY_OUT"):
        raise ValueError("In the race the choices are BOX or STAY_OUT.")
    user_db.update_decision(d["id"], user_call=call, status="decided")
    return user_db.set_clock(running=1)  # back to racing


async def _resolve_outcomes(clock: dict) -> None:
    """Once lap N+1 is complete, record whether the real driver boxed then."""
    if clock["session_kind"] == "sprint":
        return await sim_sprint.resolve_outcomes(clock)
    for d in user_db.decisions_for(clock["race_id"], clock["driver_id"]):
        target = d["lap"] + 1
        if d["actual_pitted"] is None and clock["current_lap"] >= target:
            laps = await mcp_client.call("get_driver_laps",
                                         {"driver": clock["driver_id"], "from_lap": target, "to_lap": target})
            rows = laps.get("laps", [])
            if rows:
                pitted = bool(rows[0]["pitted_end_of_lap"])
                d["actual_pitted"] = int(pitted)
                user_db.update_decision(d["id"], actual_pitted=int(pitted), actual={
                    "action": "BOX" if pitted else "STAY_OUT",
                    "detail": (f"boxed at the end of lap {target}" if pitted else
                               f"stayed out through lap {target} on {rows[0].get('compound') or 'the same'} tires")})
        # the new tire only shows on the lap after the stop, so it waits for that lap
        a = d.get("actual") or {}
        if d["actual_pitted"] == 1 and not a.get("tire") and clock["current_lap"] >= target + 1:
            nxt = (await mcp_client.call("get_driver_laps", {"driver": clock["driver_id"], "from_lap": target + 1,
                                                             "to_lap": target + 1})).get("laps", [])
            if nxt and nxt[0].get("compound"):
                user_db.update_decision(d["id"], actual={"action": "BOX", "tire": nxt[0]["compound"],
                                                         "detail": f"boxed at the end of lap {target} for "
                                                                   f"{nxt[0]['compound']} tires"})


# --------------------------------------------------------------------------
# End-of-race scorecard
# --------------------------------------------------------------------------

_grade_task: asyncio.Task | None = None


def _label(call: str | None) -> str:
    return {"BOX": "BOX", "STAY_OUT": "STAY OUT"}.get(call or "", "-")


def score(decisions: list[dict]) -> dict:
    """Match rates over the decision points that have a user call and a known real outcome."""
    # key moments whose real outcome is known (the user makes no calls; "you" is kept for old sessions)
    graded = [d for d in decisions if d["strategy_call"] and d["actual_pitted"] is not None]
    real = lambda d: "BOX" if d["actual_pitted"] else "STAY_OUT"  # noqa: E731
    n = len(graded)

    def rate(k: int) -> dict:
        return {"matched": k, "of": n, "pct": round(100 * k / n) if n else None}

    return {
        "decision_points": n,
        "you_vs_real": rate(sum(d["user_call"] == real(d) for d in graded)),
        "strategy_vs_real": rate(sum(d["strategy_call"] == real(d) for d in graded)),
        "you_vs_strategy": rate(sum(d["user_call"] == d["strategy_call"] for d in graded)),
        "rows": [{"lap": d["lap"] + 1, "trigger": d["trigger"], "strategy": d["strategy_call"],
                  "reason": d["strategy_reason"], "you": d["user_call"], "real": real(d)} for d in graded],
    }


def start_grading(clock: dict) -> None:
    if clock["session_kind"] == "qualifying":
        return sim_quali.start_grading(clock)
    if clock["session_kind"] == "sprint":
        return sim_sprint.start_grading(clock)
    global _grade_task
    user_db.save_scorecard(clock["race_id"], clock["driver_id"], "grading")
    _grade_task = asyncio.create_task(_grade(clock["race_id"], clock["driver_id"]))


async def _grade(race_id: int, driver_id: str) -> None:
    decisions = user_db.decisions_for(race_id, driver_id)
    stats = score(decisions)
    try:
        final = await mcp_client.call("get_final_result")
        me = next(r for r in final["classification"] if r["driver_id"] == driver_id)
        stats["real_finish"] = {"pos": me["pos"], "grid": me["grid"], "status": me["status"],
                                "points": me["points"]}
        stats["real_stops"] = final["my_stops"]
        if not stats["rows"]:
            user_db.save_scorecard(race_id, driver_id, "ready", stats=stats,
                                   verdict={"headline": "No key moments came up for this driver, so there's nothing to recap.", "moments": []})
            return
        table = "\n".join(f"lap {r['lap']} | {r['trigger']} | The data leaned: {_label(r['strategy'])} "
                          f"({r['reason']}) | Real team: {_label(r['real'])}" for r in stats["rows"])
        rates = "\n".join(f"{k}: {v['matched']}/{v['of']}" for k, v in stats.items()
                          if isinstance(v, dict) and "matched" in v)
        async with PitwallTeam() as team:
            verdict, tool_log, usage = await team.grade(table, rates, {r["lap"] for r in stats["rows"]})
        user_db.save_scorecard(race_id, driver_id, "ready", stats=stats,
                               verdict={**verdict.model_dump(), "tool_calls": tool_log, "usage": usage})
    except Exception as exc:
        log.error("grader error:\n%s", traceback.format_exc())
        user_db.save_scorecard(race_id, driver_id, "error", stats=stats, error=f"{exc.__class__.__name__}: {exc}")
