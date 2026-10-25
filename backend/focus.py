"""The driver-focus panel and the pre-session read.

Driver focus is assembled in code from the same no-future MCP tools the agents
use (current lap and earlier only), so every number on it comes straight from
the data; no model writes any of it. Live speed isn't in the data, so pace is
read from lap times.

The pre-session read is written by the previewer agent once per session and
followed driver, from pre-session data only, and stored in the user DB.
"""

from __future__ import annotations

import asyncio
import logging
import re
import traceback
from statistics import median

import mcp_client
import user_db

log = logging.getLogger("pitwall.focus")

TIRE = {"SOFT": "softs", "MEDIUM": "mediums", "HARD": "hards", "INTERMEDIATE": "intermediates", "WET": "full wets"}
# race control messages worth showing in a driver's panel
NOTABLE = re.compile(r"PENALTY|INVESTIGAT|NOTED|DELETED|BLACK AND WHITE|WARNING|REPRIMAND|DISQUALIF|STOP AND GO|DRIVE THROUGH")


def _lap(s: float) -> str:
    m = int(s // 60)
    return f"{m}:{s - m * 60:06.3f}" if m else f"{s:.3f}s"


def _plain(msg: str) -> str:
    """A race control message in plain words for a newcomer (the original is shown too)."""
    m = msg.upper()
    if "PENALTY SERVED" in m:
        return "Served a penalty."
    if pen := re.search(r"(\d+) SECOND TIME PENALTY", m):
        return f"Given a {pen.group(1)}-second time penalty: it's added to their race time, or served at their next stop."
    if "DRIVE THROUGH" in m:
        return "Given a drive-through penalty: they must drive down the pit lane without stopping."
    if "NO FURTHER" in m or "REVIEWED NO FURTHER" in m:
        return "The stewards looked at an incident and took no action."
    if "UNDER INVESTIGATION" in m or "WILL BE INVESTIGATED" in m:
        return "The stewards are investigating an incident involving them."
    if "NOTED" in m:
        return "Race control noted an incident involving them; the stewards may look at it."
    if "DELETED" in m:
        return "A lap time was deleted for going beyond the track limits (the white lines)."
    if "BLACK AND WHITE" in m:
        return "Shown the black-and-white flag: a warning for track limits or driving standards."
    return "A race control message about them."


def _notes(rc: dict, code: str) -> list[dict]:
    msgs = (rc.get("driver_messages") or {}).get("messages") or []
    return [{"when": f"lap {m['lap']}" if m.get("lap") is not None else (f"{m['at']} in" if m.get("at") else ""),
             "message": m["message"], "plain": _plain(m["message"])}
            for m in msgs if NOTABLE.search(m["message"].upper())]


def _flags(rc: dict) -> dict:
    t = rc.get("track_status") or {}
    return {"summary": t.get("summary"),
            # there's no car-position data, so we can't tell which sector a car is in
            "note": ("Yellow flags are out in some sectors. The data doesn't show where each car is on the lap, "
                     "so it can't say whether this driver is in one.") if t.get("yellow_sectors") else None}


async def driver_focus(code: str | None) -> dict:
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        return {"refused": True, "reason": "Load a session first."}
    if clock["session_kind"] == "qualifying":
        return await _quali_focus(code)
    return await _race_focus(code, clock)


async def _race_focus(code: str | None, clock: dict) -> dict:
    state = await mcp_client.call("get_race_state", {})
    cars = state.get("cars", [])
    car = next((c for c in cars if (c["code"] == code if code else c["is_my_driver"])), None)
    if car is None:
        out = next((r for r in state.get("retired", []) if r["code"] == code), None)
        return {"code": code, "out": True, "text": f"{code} is out of the race (last lap {out['last_lap']})." if out
                else f"No running car {code}."}
    code, lap = car["code"], state.get("lap", 0)
    rc = await mcp_client.call("get_race_control", {"driver": code})
    if state.get("phase") == "grid":
        return {"code": code, "team": car.get("team"), "phase": "grid", "position": car["position"], "lap": 0,
                "total_laps": clock["total_laps"], "text": f"{code} lines up P{car['position']} on the grid.",
                "notes": _notes(rc, code), "flags": _flags(rc)}
    ahead = next((c for c in cars if c["position"] == car["position"] - 1), None)
    behind = next((c for c in cars if c["position"] == car["position"] + 1), None)
    laps = (await mcp_client.call("get_driver_laps", {"driver": code, "from_lap": max(1, lap - 4)})).get("laps", [])
    tires = await mcp_client.call("get_tire_info", {"driver": code})
    neutral = set(rc.get("neutralised_laps") or [])
    recent = [{"lap": l["lap"], "time_s": l["lap_time_s"], "neutralised": l["lap"] in neutral,
               "pit": l["pitted_end_of_lap"] or l["out_lap"]} for l in laps]
    pace = ((tires.get("current_set") or {}).get("recent_pace") or {})
    window = set(range(max(1, lap - 5), lap + 1))
    distorted = bool(window & neutral) or any(r["pit"] for r in recent[-4:])
    direction = pace.get("direction")
    # the last three laps at racing speed, if they're the latest three laps run
    tail = recent[-3:]
    clean_tail = len(tail) == 3 and not any(r["neutralised"] or r["pit"] or not r["time_s"] for r in tail)
    if distorted and clean_tail:
        d_s = round(tail[-1]["time_s"] - tail[0]["time_s"], 3)
        direction = "steady" if abs(d_s) < 0.3 else ("worsening" if d_s > 0 else "improving")
        span = f"from {_lap(tail[0]['time_s'])} to {_lap(tail[-1]['time_s'])} over the last 3 green-flag laps"
        pace_text = ({"steady": f"Lap times steady since the neutralised laps: {span}.",
                      "worsening": f"Getting slower since the neutralised laps: {span}.",
                      "improving": f"Getting faster since the neutralised laps: {span}."}[direction])
        pace = {"change_s": d_s}
        distorted = False
    elif direction in (None, "unknown"):
        pace_text = "Not enough laps on this set yet to read a pace trend."
    elif distorted:
        pace_text = ("Recent laps include a safety car, VSC or a pit stop, so the lap times don't show true pace "
                     "right now.")
    elif direction == "steady":
        pace_text = f"Lap times steady: within {abs(pace['change_s'])}s over the last 3 laps compared with the 3 before."
    else:
        word = "faster" if direction == "improving" else "slower"
        pace_text = (f"Getting {word}: about {abs(pace['change_s'])}s {'quicker' if word == 'faster' else 'slower'} "
                     f"per lap over the last 3 laps than the 3 before"
                     + (" (the tires are wearing)" if word == "slower" else "") + ".")
    clean = [r["time_s"] for r in recent if r["time_s"] and not r["neutralised"] and not r["pit"]]
    comp = car.get("compound")
    return {
        "code": code, "team": car.get("team"), "phase": "racing",
        "position": car["position"], "lap": lap, "total_laps": clock["total_laps"],
        "compound": comp, "tire_age": car.get("tire_age"), "stops": car.get("stops"),
        "pitted_end_of_lap": car.get("pitted_end_of_lap"), "out_lap": car.get("out_lap"),
        "ahead": ahead and {"code": ahead["code"], "gap_s": car.get("gap_ahead_s")},
        "behind": behind and {"code": behind["code"], "gap_s": car.get("gap_behind_s")},
        "recent_laps": recent, "typical_lap_s": round(median(clean), 3) if clean else None,
        "pace": {"direction": None if distorted else direction, "change_s": pace.get("change_s"), "text": pace_text},
        "text": (f"{code} is P{car['position']} after lap {lap} of {clock['total_laps']}, on {car.get('tire_age')}-lap-old "
                 f"{TIRE.get(comp or '', (comp or 'unknown').lower())}"
                 + (f", {car['stops']} stop{'s' if car['stops'] != 1 else ''} so far." if car.get("stops") else
                    ", no stops yet.")),
        "notes": _notes(rc, code), "flags": _flags(rc),
    }


async def _quali_focus(code: str | None) -> dict:
    q = await mcp_client.call("get_quali_state", {})
    order = q.get("order", [])
    car = next((c for c in order if (c["code"] == code if code else c["is_my_driver"])), None)
    if car is None:
        out = next((e for e in q.get("eliminated", []) if e["code"] == code), None)
        return {"code": code, "out": True,
                "text": f"{code} was knocked out in {out['out_in']}" + (f" with a {out['best']}." if out.get("best") else ".")
                if out else f"No car {code} in this segment."}
    code = car["code"]
    rc = await mcp_client.call("get_race_control", {"driver": code})
    laps = (await mcp_client.call("get_quali_driver", {"driver": code})).get("laps", [])
    seg = q.get("segment")
    cut = q.get("cars_through")
    margin = None
    if cut and car.get("best"):
        line = next((c for c in order if c["position"] == cut), None)
        if line and line.get("best") and line["code"] != code:
            margin = {"line_code": line["code"], "line_time": line["best"], "inside": car["position"] <= cut}
    status = {"garage": "in the garage", "out lap": "on an out lap (warming the tires)", "on a lap": "on a timed lap"}.get(
        car["status"], car["status"])
    comp = car.get("compound")
    return {
        "code": code, "phase": "qualifying", "segment": seg, "position": car["position"], "best": car.get("best"),
        "best_compound": car.get("best_compound"), "status": car["status"], "compound": comp,
        "tire_age": car.get("tire_age"), "laps_this_segment": car.get("laps_this_segment"),
        "cut": cut and {"cars_through": cut, "inside": car["position"] <= cut and bool(car.get("best")), **(margin or {})},
        "recent_laps": [{"lap": l.get("lap"), "time": l.get("time") or l.get("lap_time"), "compound": l.get("compound"),
                         "segment": l.get("segment")} for l in laps[-5:]],
        "text": (f"{code} is " + (f"P{car['position']} in {seg} with a {car['best']}" if car.get("best") and seg
                                  else "yet to set a time") + f", {status}"
                 + (f" on {TIRE.get(comp or '', (comp or '').lower())}." if comp else ".")),
        "notes": _notes(rc, code), "flags": _flags(rc),
    }


# --------------------------------------------------------------------------
# The AI's pre-session read
# --------------------------------------------------------------------------

_writing: set[tuple] = set()
_tasks: set[asyncio.Task] = set()


def ensure_preview(clock: dict, force: bool = False) -> None:
    """Write the pre-session read for this session and followed driver, once."""
    key = (clock["race_id"], clock["driver_id"], clock["session_kind"])
    if clock["race_id"] is None or key in _writing:
        return
    have = user_db.get_preview(*key)
    if have and have["status"] == "ready" and not force:
        return
    user_db.save_preview(*key, "writing")
    _writing.add(key)
    t = asyncio.create_task(_write(key))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _write(key: tuple) -> None:
    from team import PitwallTeam  # team imports nothing from here
    try:
        async with PitwallTeam(key[2]) as team:
            read = await team.preview()
        clock = user_db.get_clock()
        if (clock["race_id"], clock["driver_id"], clock["session_kind"]) != key:
            user_db.save_preview(*key, "error", error="The session changed while the read was being written.")
            return
        user_db.save_preview(*key, "ready", read=read.model_dump())
    except Exception as exc:
        log.error("preview error:\n%s", traceback.format_exc())
        user_db.save_preview(*key, "error", error=f"{exc.__class__.__name__}: {exc}")
    finally:
        _writing.discard(key)


async def preview(clock: dict) -> dict:
    data = await mcp_client.call("get_session_preview", {})
    key = (clock["race_id"], clock["driver_id"], clock["session_kind"])
    row = user_db.get_preview(*key)
    if row is None or (row["status"] == "writing" and key not in _writing):  # none yet, or lost to a restart
        ensure_preview(clock)
        row = user_db.get_preview(*key)
    return {"data": data, "read": row}
