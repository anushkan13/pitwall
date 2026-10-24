"""The sprint: race mode on a short race, with a richer call.

The sprint runs on the race clock and the race loop in sim.py (laps, the same
no-future MCP race tools, the same crew and grounding). What's different:

  * The call: BOX (and which tire: WET / INTERMEDIATE / SOFT / MEDIUM / HARD),
    or stay out and DEFEND, PUSH, or MANAGE the tires.
  * Decision points come sooner and closer together (it's 1/3 of a race), and
    a car within a second ahead or behind is a decision point of its own.
  * What the real driver did: whether they boxed at the end of the next lap is
    read straight from the data, and on what tire. Whether they defended,
    pushed or managed isn't in any timing feed, so it's inferred from the next
    three laps (marked "inferred" everywhere it's shown):
        DEFEND  a car was within 1.0s behind and they kept the place
        PUSH    they gained a place, or took 0.5s+ out of the car ahead
        MANAGE  otherwise
"""

from __future__ import annotations

import asyncio
import logging
import traceback

import mcp_client
import user_db

log = logging.getLogger("pitwall.sim_sprint")

TIRE_CHECK_AGE = {"SOFT": 10, "MEDIUM": 12, "HARD": 14, "INTERMEDIATE": 8, "WET": 8}
TIRE_RECHECK_EVERY = 3
MIN_LAPS_BETWEEN = 2
NO_CALLS_IN_LAST = 2
BATTLE_GAP_S = 1.0          # a car this close ahead or behind is a decision point
MODE_LAPS = 3               # laps used to infer what the real driver was doing
PUSH_GAIN_S = 0.5

CALLS = {"BOX", "DEFEND", "PUSH", "MANAGE"}
TIRES = {"WET", "INTERMEDIATE", "SOFT", "MEDIUM", "HARD"}

_tasks: set[asyncio.Task] = set()


def battle_trigger(cars: list[dict], me: dict) -> str | None:
    """A car within a second ahead or behind: defend, push or manage?"""
    behind = next((c for c in cars if c["position"] == me["position"] + 1), None)
    if me.get("gap_behind_s") is not None and me["gap_behind_s"] <= BATTLE_GAP_S and behind:
        return f"{behind['code']} is within a second behind (P{behind['position']})"
    ahead = next((c for c in cars if c["position"] == me["position"] - 1), None)
    if me.get("gap_ahead_s") is not None and me["gap_ahead_s"] <= BATTLE_GAP_S and ahead:
        return f"We're within a second of {ahead['code']} ahead (P{ahead['position']})"
    return None


def make_call(decision: dict, call: str, tire: str | None) -> dict:
    if call not in CALLS:
        raise ValueError("In the sprint the choices are BOX (with a tire), DEFEND, PUSH or MANAGE.")
    if call == "BOX" and tire not in TIRES:
        raise ValueError("Pick a tire to box for.")
    user_db.update_decision(decision["id"], user_call=call, user_tire=tire if call == "BOX" else None,
                            status="decided")
    return user_db.set_clock(running=1)


# --------------------------------------------------------------------------
# What the real driver did
# --------------------------------------------------------------------------


async def resolve_outcomes(clock: dict) -> None:
    lap_now, total = clock["current_lap"], clock["total_laps"]
    for d in user_db.decisions_for(clock["race_id"], clock["driver_id"], "sprint"):
        actual = d.get("actual") or {}
        if actual.get("action"):
            continue
        target = d["lap"] + 1
        if lap_now < target:
            continue
        laps = (await mcp_client.call("get_driver_laps", {"driver": clock["driver_id"], "from_lap": d["lap"],
                                                          "to_lap": min(d["lap"] + MODE_LAPS + 1, lap_now)})).get("laps", [])
        by_lap = {l["lap"]: l for l in laps}
        nxt = by_lap.get(target)
        if nxt is None:
            user_db.update_decision(d["id"], actual={"action": "OUT", "detail": "retired"}, actual_pitted=0)
            continue
        if nxt["pitted_end_of_lap"]:
            after = by_lap.get(target + 1)
            if after is None and lap_now < target + 1:
                user_db.update_decision(d["id"], actual_pitted=1)  # tire known once the next lap is run
                continue
            tire = after["compound"] if after else None
            user_db.update_decision(d["id"], actual_pitted=1, actual={
                "action": "BOX", "tire": tire, "detail": f"boxed at the end of lap {target}" + (f" for {tire}" if tire else "")})
            continue
        end = min(d["lap"] + MODE_LAPS, total)
        if lap_now < end:
            user_db.update_decision(d["id"], actual_pitted=0)
            continue
        user_db.update_decision(d["id"], actual_pitted=0, actual=await _infer_mode(d["lap"], end, clock["driver_id"]))


async def _infer_mode(lap: int, end: int, driver_id: str) -> dict:
    start = await mcp_client.call("get_race_state", {"lap": lap})
    stop = await mcp_client.call("get_race_state", {"lap": end})
    me0 = next((c for c in start.get("cars", []) if c["driver_id"] == driver_id), None)
    me1 = next((c for c in stop.get("cars", []) if c["driver_id"] == driver_id), None)
    base = {"action": "STAY_OUT", "inferred": True}
    if not me0 or not me1:
        return {**base, "mode": "MANAGE", "detail": "not enough laps to tell"}
    span = f"laps {lap}-{end}"
    if me0.get("gap_behind_s") is not None and me0["gap_behind_s"] <= BATTLE_GAP_S and me1["position"] <= me0["position"]:
        return {**base, "mode": "DEFEND",
                "detail": f"kept P{me1['position']} with a car {me0['gap_behind_s']}s behind ({span})"}
    if me1["position"] < me0["position"]:
        return {**base, "mode": "PUSH", "detail": f"gained places, P{me0['position']} to P{me1['position']} ({span})"}
    a0, a1 = me0.get("gap_ahead_s"), me1.get("gap_ahead_s")
    ahead0 = next((c["code"] for c in start["cars"] if c["position"] == me0["position"] - 1), None)
    ahead1 = next((c["code"] for c in stop["cars"] if c["position"] == me1["position"] - 1), None)
    if a0 is not None and a1 is not None and ahead0 == ahead1 and a0 - a1 >= PUSH_GAIN_S:
        return {**base, "mode": "PUSH", "detail": f"took {round(a0 - a1, 3)}s out of {ahead0} ({span})"}
    return {**base, "mode": "MANAGE", "detail": f"held P{me1['position']} without a close fight or a big gain ({span})"}


# --------------------------------------------------------------------------
# Scorecard
# --------------------------------------------------------------------------


def _label(call: str | None, tire: str | None = None) -> str:
    if call == "BOX":
        return f"BOX → {tire}" if tire else "BOX"
    return call or "-"


def _real_label(a: dict) -> str:
    if a.get("action") == "BOX":
        return _label("BOX", a.get("tire"))
    if a.get("action") == "STAY_OUT":
        return f"STAY OUT ({a.get('mode')}, inferred)"
    return a.get("action") or "-"


def score(decisions: list[dict]) -> dict:
    graded = [d for d in decisions if d["user_call"] and d["strategy_call"] and (d.get("actual") or {}).get("action")
              in ("BOX", "STAY_OUT")]
    n = len(graded)

    def rate(k: int, of: int) -> dict:
        return {"matched": k, "of": of, "pct": round(100 * k / of) if of else None}

    real_box = lambda d: d["actual"]["action"] == "BOX"  # noqa: E731
    s_tire = lambda d: (d.get("evidence") or {}).get("tire_to_fit")  # noqa: E731
    stayed = [d for d in graded if not real_box(d)]
    you_modes = [d for d in stayed if d["user_call"] != "BOX"]
    strat_modes = [d for d in stayed if d["strategy_call"] != "BOX"]
    you_tires = [d for d in graded if real_box(d) and d["user_call"] == "BOX"]
    strat_tires = [d for d in graded if real_box(d) and d["strategy_call"] == "BOX"]
    return {
        "decision_points": n,
        # the call that's in the data: box or stay out
        "you_vs_real": rate(sum((d["user_call"] == "BOX") == real_box(d) for d in graded), n),
        "strategy_vs_real": rate(sum((d["strategy_call"] == "BOX") == real_box(d) for d in graded), n),
        "you_vs_strategy": rate(sum(d["user_call"] == d["strategy_call"] for d in graded), n),
        "your_tire_vs_real": rate(sum(d["user_tire"] == d["actual"].get("tire") for d in you_tires), len(you_tires)),
        "strategy_tire_vs_real": rate(sum(s_tire(d) == d["actual"].get("tire") for d in strat_tires), len(strat_tires)),
        # defend / push / manage against what the next laps suggest
        "your_mode_vs_inferred": rate(sum(d["user_call"] == d["actual"].get("mode") for d in you_modes), len(you_modes)),
        "strategy_mode_vs_inferred": rate(sum(d["strategy_call"] == d["actual"].get("mode") for d in strat_modes),
                                          len(strat_modes)),
        "rows": [{"lap": d["lap"] + 1, "trigger": d["trigger"], "strategy": _label(d["strategy_call"], s_tire(d)),
                  "you": _label(d["user_call"], d["user_tire"]), "real": _real_label(d["actual"]),
                  "real_detail": d["actual"].get("detail"),
                  "strategy_ok": (d["strategy_call"] == "BOX") == real_box(d),
                  "you_ok": (d["user_call"] == "BOX") == real_box(d)} for d in graded],
    }


def start_grading(clock: dict) -> None:
    user_db.save_scorecard(clock["race_id"], clock["driver_id"], "grading", session_kind="sprint")
    t = asyncio.create_task(_grade(clock["race_id"], clock["driver_id"]))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _grade(race_id: int, driver_id: str) -> None:
    from team import PitwallTeam  # imported here: team imports nothing from the sims
    stats = score(user_db.decisions_for(race_id, driver_id, "sprint"))
    try:
        final = await mcp_client.call("get_final_result")
        me = next(r for r in final["classification"] if r["driver_id"] == driver_id)
        stats["real_finish"] = {"pos": me["pos"], "grid": me["grid"], "status": me["status"], "points": me["points"]}
        stats["real_stops"] = final["my_stops"]
        if not stats["rows"]:
            user_db.save_scorecard(race_id, driver_id, "ready", stats=stats, session_kind="sprint",
                                   verdict={"headline": "No decision points were called, so there is nothing to grade."})
            return
        table = "\n".join(f"lap {r['lap']} | {r['trigger']} | Strategy: {r['strategy']} | You: {r['you']} | "
                          f"Real driver: {r['real']} ({r['real_detail']})" for r in stats["rows"])
        rates = "\n".join(f"{k}: {v['matched']}/{v['of']}" for k, v in stats.items()
                          if isinstance(v, dict) and "matched" in v)
        async with PitwallTeam("sprint") as team:
            verdict, tool_log, usage = await team.grade(table, rates, {r["lap"] for r in stats["rows"]})
        user_db.save_scorecard(race_id, driver_id, "ready", stats=stats, session_kind="sprint",
                               verdict={**verdict.model_dump(), "tool_calls": tool_log, "usage": usage})
    except Exception as exc:
        log.error("grader error:\n%s", traceback.format_exc())
        user_db.save_scorecard(race_id, driver_id, "error", stats=stats, session_kind="sprint",
                               error=f"{exc.__class__.__name__}: {exc}")
