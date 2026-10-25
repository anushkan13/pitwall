"""Pitwall MCP server.

The single doorway between the agent team and the race data. Two databases,
both opened read-only from here:

    data/races.db         the baked race data (scripts/bake_race.py)
    data/pitwall_user.db  the sim clock, written only by the backend

The no-future rule lives here, not in any prompt. Every race tool reads the
sim clock on every call and only ever answers from laps the race has already
completed:

  * The race and the current lap come from the clock. No tool takes a race id
    or lets the caller move the clock, so an agent cannot widen its own view.
  * A tool asked about a later lap refuses outright (refused=True) and returns
    nothing from that lap. Ranges are clipped to the current lap.
  * The decision being made is always "box at the end of the NEXT lap", so a
    stop recorded on the current lap shows that the car went in, but its
    durations (which finish during the next lap) are withheld.
  * The final classification in races.db is never read by these tools;
    retirements are inferred from a car simply having no lap yet.

Run directly (stdio transport, which is what the backend and Claude use):

    .venv/bin/python mcp_server/server.py
"""

from __future__ import annotations

import functools
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from fastmcp import FastMCP

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
RACES_DB = DATA_DIR / "races.db"
USER_DB = DATA_DIR / "pitwall_user.db"

mcp = FastMCP("pitwall")


# --------------------------------------------------------------------------
# Connections and the clock
# --------------------------------------------------------------------------


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _q(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _ro(RACES_DB)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class NoClock(Exception):
    pass


def _clock(kind: str | None = "race") -> dict:
    """The live sim clock. Raises NoClock if no session is loaded, or if the
    loaded session is not `kind` (None = any). Race tools refuse during
    qualifying, so Saturday can never read Sunday's laps."""
    if not USER_DB.exists():
        raise NoClock("No race is loaded. Start one from the app first.")
    conn = _ro(USER_DB)
    try:
        row = conn.execute(
            """SELECT race_id, driver_id, current_lap, total_laps, session_kind, session_time_utc,
                      session_start_utc, session_end_utc FROM sim_clock WHERE id = 1"""
        ).fetchone()
    except sqlite3.OperationalError:
        try:  # a user DB from before qualifying mode
            row = conn.execute(
                "SELECT race_id, driver_id, current_lap, total_laps, 'race' AS session_kind FROM sim_clock WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    finally:
        conn.close()
    if row is None or row["race_id"] is None:
        raise NoClock("No race is loaded. Start one from the app first.")
    clock = dict(row)
    # the race tools serve the sprint too: a sprint is a short race
    if kind == "race" and clock["session_kind"] == "sprint":
        return clock
    if kind and clock["session_kind"] != kind:
        raise NoClock(f"This is a {clock['session_kind']} session; that tool is for the {kind} only. "
                      f"Use the {clock['session_kind']} tools.")
    return clock


def _refuse(reason: str, **extra) -> dict:
    return {"refused": True, "reason": reason, **extra}


def _guarded(fn):
    """Turn a missing clock into a refusal instead of an exception."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NoClock as exc:
            return _refuse(str(exc))
    return wrapper


SESSION_CODE = {"race": 1, "qualifying": 2, "sprint": 3}


def _sid(race_id: int, kind: str = "race") -> int:
    """session_id: race_id * 10 + 1 race / 2 qualifying / 3 sprint."""
    return race_id * 10 + SESSION_CODE.get(kind, 1)


def _ms_to_s(ms: int | None) -> float | None:
    return round(ms / 1000, 3) if ms is not None else None


def _resolve_driver(race_id: int, driver: str, kind: str = "race") -> sqlite3.Row | None:
    """Accept a code ('NOR'), driver id ('norris'), car number ('4') or surname."""
    key = str(driver).strip().lower()
    for row in _q("""
            SELECT r.driver_id, d.code, d.first_name, d.last_name, r.car_number, t.name AS team
            FROM results r JOIN drivers d USING (driver_id) JOIN teams t USING (team_id)
            WHERE r.session_id = ?""", (_sid(race_id, "sprint" if kind == "sprint" else "race"),)):
        if key in {row["driver_id"], (row["code"] or "").lower(), str(row["car_number"]),
                   row["last_name"].lower()}:
            return row
    return None


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool
def ping() -> dict:
    """Health check. Returns ok, the server name, and the current time."""
    return {
        "ok": True,
        "server": "pitwall",
        "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "data_dir_exists": DATA_DIR.exists(),
    }


@mcp.tool
def list_races() -> list[dict]:
    """Every baked race: id, season, round, name, date. No results."""
    return [dict(r) for r in _q(
        "SELECT race_id, season, round, name, date FROM races ORDER BY season, round")]


@mcp.tool
def get_entry_list(race_id: int, session: str = "race") -> dict:
    """Pre-race information: who is entered, for which team, and where they
    start on the grid. Nothing about how the race went.

    Args:
        race_id: season * 100 + round.
        session: "race" (default) or "sprint" for the sprint grid.
    """
    rows = _q("""
        SELECT r.driver_id, d.code, d.first_name || ' ' || d.last_name AS name,
               t.name AS team, r.car_number, r.grid
        FROM results r JOIN drivers d USING (driver_id) JOIN teams t USING (team_id)
        WHERE r.session_id = ? ORDER BY CASE WHEN r.grid = 0 THEN 99 ELSE r.grid END""",
        (_sid(race_id, "sprint" if session == "sprint" else "race"),))
    race = _q("SELECT name, date, circuit_name FROM races WHERE race_id = ?", (race_id,))
    if not race:
        return _refuse(f"No race {race_id} in races.db.")
    total = _q("SELECT total_laps FROM sessions WHERE session_id = ?",
               (_sid(race_id, "sprint" if session == "sprint" else "race"),))
    if not total:
        return _refuse(f"No {session} session for race {race_id}.")
    return {**dict(race[0]), "race_id": race_id, "scheduled_laps": total[0]["total_laps"],
            "entries": [dict(r) for r in rows]}


@mcp.tool
@_guarded
def get_race_state(lap: int | None = None) -> dict:
    """The running order at the end of a lap: position, gap to the car ahead
    and to the leader, tire compound and age, pit stops so far, and whether
    the car pitted at the end of that lap. Plus the weather on track.

    Args:
        lap: Which completed lap to describe. Defaults to the current lap.
             Anything after the current lap is refused.
    """
    clock = _clock()
    current = clock["current_lap"]
    lap = current if lap is None else int(lap)
    if lap > current:
        return _refuse(f"Lap {lap} has not happened yet. The race is on lap {current}; "
                       f"ask about lap {current} or earlier.", current_lap=current)
    if lap < 0:
        return _refuse("Lap must be 0 (the grid) or later.", current_lap=current)
    sid = _sid(clock["race_id"], clock["session_kind"])

    base = {"race_id": clock["race_id"], "lap": lap, "current_lap": current,
            "total_laps": clock["total_laps"], "laps_remaining": clock["total_laps"] - lap,
            "my_driver": clock["driver_id"]}

    if lap == 0:
        grid = _q("""
            SELECT r.driver_id, d.code, t.name AS team, r.grid
            FROM results r JOIN drivers d USING (driver_id) JOIN teams t USING (team_id)
            WHERE r.session_id = ? ORDER BY CASE WHEN r.grid = 0 THEN 99 ELSE r.grid END""", (sid,))
        return {**base, "phase": "grid",
                "cars": [{**dict(g), "position": i + 1, "is_my_driver": g["driver_id"] == clock["driver_id"]}
                         for i, g in enumerate(grid)]}

    rows = _q("""
        SELECT l.driver_id, d.code, t.name AS team, l.position, l.lap_time_ms, l.gap_ahead_ms,
               l.gap_leader_ms, l.compound, l.tire_age, l.stint, l.pit_in, l.pit_out,
               (SELECT COUNT(*) FROM pitstops p WHERE p.session_id = l.session_id
                  AND p.driver_id = l.driver_id AND p.lap <= ? AND p.pit_lane_pass = 0) AS stops
        FROM laps l JOIN drivers d USING (driver_id)
        JOIN results r ON r.session_id = l.session_id AND r.driver_id = l.driver_id
        JOIN teams t ON t.team_id = r.team_id
        WHERE l.session_id = ? AND l.lap = ? ORDER BY l.position""", (lap, sid, lap))
    cars = [{
        "position": r["position"], "driver_id": r["driver_id"], "code": r["code"], "team": r["team"],
        "gap_ahead_s": _ms_to_s(r["gap_ahead_ms"]), "gap_leader_s": _ms_to_s(r["gap_leader_ms"]),
        "last_lap_s": _ms_to_s(r["lap_time_ms"]), "compound": r["compound"], "tire_age": r["tire_age"],
        "stops": r["stops"], "pitted_end_of_lap": bool(r["pit_in"]), "out_lap": bool(r["pit_out"]),
        "is_my_driver": r["driver_id"] == clock["driver_id"],
    } for r in rows]
    for i, c in enumerate(cars):  # gap to the car behind, for undercut/overcut reasoning
        c["gap_behind_s"] = cars[i + 1]["gap_ahead_s"] if i + 1 < len(cars) else None

    running = {c["driver_id"] for c in cars}
    out = _q("""SELECT l.driver_id, d.code, MAX(l.lap) AS last_lap FROM laps l JOIN drivers d USING (driver_id)
                WHERE l.session_id = ? AND l.lap < ? GROUP BY l.driver_id""", (sid, lap))
    retired = [{"driver_id": o["driver_id"], "code": o["code"], "last_lap": o["last_lap"]}
               for o in out if o["driver_id"] not in running]

    compounds: dict[str, int] = {}
    for c in cars:
        compounds[c["compound"] or "UNKNOWN"] = compounds.get(c["compound"] or "UNKNOWN", 0) + 1

    return {**base, "phase": "racing", "cars": cars, "retired": retired,
            "field_compounds": compounds, "weather": _weather_at(sid, lap)}


def _weather_at(sid: int, lap: int) -> dict | None:
    """Latest weather reading taken before the leader finished `lap`."""
    end = _q("""SELECT strftime('%Y-%m-%dT%H:%M:%fZ', lap_start_utc, '+' || (lap_time_ms / 1000.0) || ' seconds') AS t
                FROM laps WHERE session_id = ? AND lap = ? AND position = 1""", (sid, lap))
    if not end or end[0]["t"] is None:
        return None
    readings = _q("""SELECT time_utc, air_temp_c, track_temp_c, rainfall, humidity_pct FROM weather
                     WHERE session_id = ? AND time_utc <= ? ORDER BY time_utc DESC LIMIT 6""", (sid, end[0]["t"]))
    if not readings:
        return None
    now = dict(readings[0])
    now["raining"] = bool(now.pop("rainfall"))
    now["rain_in_last_5_min"] = any(r["rainfall"] for r in readings[:6])
    return now


@mcp.tool
@_guarded
def get_driver_laps(driver: str, from_lap: int = 1, to_lap: int | None = None) -> dict:
    """One driver's laps so far: position, lap time, gaps, tire compound and
    age, and pit in/out flags.

    Args:
        driver: Three-letter code ('NOR'), driver id ('norris'), car number or surname.
        from_lap: First lap to include (default 1).
        to_lap: Last lap to include. Defaults to, and is clipped to, the current lap.
    """
    clock = _clock()
    current = clock["current_lap"]
    d = _resolve_driver(clock["race_id"], driver, clock["session_kind"])
    if d is None:
        return _refuse(f"No driver '{driver}' in this race.")
    clipped = to_lap is not None and to_lap > current
    to_lap = current if to_lap is None or clipped else int(to_lap)
    rows = _q("""SELECT lap, position, lap_time_ms, gap_ahead_ms, gap_leader_ms, compound, tire_age,
                        stint, pit_in, pit_out FROM laps
                 WHERE session_id = ? AND driver_id = ? AND lap BETWEEN ? AND ? ORDER BY lap""",
              (_sid(clock["race_id"], clock["session_kind"]), d["driver_id"], int(from_lap), to_lap))
    return {
        "driver_id": d["driver_id"], "code": d["code"], "team": d["team"], "current_lap": current,
        **({"note": f"Clipped to lap {current}; later laps have not happened yet."} if clipped else {}),
        "laps": [{"lap": r["lap"], "position": r["position"], "lap_time_s": _ms_to_s(r["lap_time_ms"]),
                  "gap_ahead_s": _ms_to_s(r["gap_ahead_ms"]), "gap_leader_s": _ms_to_s(r["gap_leader_ms"]),
                  "compound": r["compound"], "tire_age": r["tire_age"], "stint": r["stint"],
                  "pitted_end_of_lap": bool(r["pit_in"]), "out_lap": bool(r["pit_out"])} for r in rows],
    }


@mcp.tool
@_guarded
def get_tire_info(driver: str) -> dict:
    """A driver's tire history so far: each stint's compound, laps run, and
    pace (median of the first and last three clean laps; pace_change_s > 0
    means slower now), plus the set they are on now with its recent_pace
    direction (improving / steady / worsening over the last 3 clean laps vs
    the 3 before).

    Args:
        driver: Three-letter code ('NOR'), driver id ('norris'), car number or surname.
    """
    clock = _clock()
    current = clock["current_lap"]
    d = _resolve_driver(clock["race_id"], driver, clock["session_kind"])
    if d is None:
        return _refuse(f"No driver '{driver}' in this race.")
    rows = _q("""SELECT lap, lap_time_ms, compound, tire_age, stint, pit_in, pit_out FROM laps
                 WHERE session_id = ? AND driver_id = ? AND lap <= ? ORDER BY lap""",
              (_sid(clock["race_id"], clock["session_kind"]), d["driver_id"], current))
    stints: list[dict] = []
    for r in rows:
        if not stints or stints[-1]["stint"] != r["stint"]:
            stints.append({"stint": r["stint"], "compound": r["compound"], "first_lap": r["lap"],
                           "_clean": []})
        s = stints[-1]
        s["last_lap"], s["laps_run"], s["tire_age_now"] = r["lap"], r["lap"] - s["first_lap"] + 1, r["tire_age"]
        if r["lap"] > 1 and not r["pit_in"] and not r["pit_out"] and r["lap_time_ms"]:
            s["_clean"].append(r["lap_time_ms"])
    recent = None
    for s in stints:
        clean = s.pop("_clean")
        if len(clean) >= 4:
            first, last = median(clean[:3]), median(clean[-3:])
            s["pace_first_laps_s"], s["pace_last_laps_s"] = _ms_to_s(first), _ms_to_s(last)
            s["pace_change_s"] = _ms_to_s(last - first)  # + = slower now than at the start of the stint
        if s is stints[-1]:
            recent = _pace_trend(clean)
    return {
        "driver_id": d["driver_id"], "code": d["code"], "current_lap": current,
        "current_set": ({"compound": stints[-1]["compound"], "tire_age": stints[-1]["tire_age_now"],
                         "recent_pace": recent} if stints else None),
        "compounds_used": sorted({s["compound"] for s in stints if s["compound"]}),
        "stints": stints,
    }


# A change in median lap time smaller than this counts as steady.
PACE_STEADY_S = 0.3


def _pace_trend(clean_ms: list[int]) -> dict:
    """Direction of pace on the current set: median of the last 3 clean laps
    against the 3 clean laps before them."""
    if len(clean_ms) < 6:
        return {"direction": "unknown", "why": f"only {len(clean_ms)} clean laps on this set; need 6"}
    last, before = median(clean_ms[-3:]), median(clean_ms[-6:-3])
    change = (last - before) / 1000
    direction = "steady" if abs(change) < PACE_STEADY_S else ("worsening" if change > 0 else "improving")
    return {"direction": direction, "last_3_median_s": _ms_to_s(last), "previous_3_median_s": _ms_to_s(before),
            "change_s": round(change, 3), "note": "change_s > 0 means slower (losing pace)"}


@mcp.tool
@_guarded
def get_pit_log() -> dict:
    """Every pit stop so far, oldest first: who, which lap, the tires before
    and after, and how long it took. A stop on the current lap shows the car
    went in; its times and new tires appear once the next lap is complete.
    Pit-lane passes (no tire change, e.g. behind the safety car) are marked."""
    clock = _clock()
    current = clock["current_lap"]
    sid = _sid(clock["race_id"], clock["session_kind"])
    rows = _q("""
        SELECT p.driver_id, d.code, p.stop, p.lap, p.pit_lane_ms, p.stationary_ms, p.pit_lane_pass,
               (SELECT compound FROM laps WHERE session_id = p.session_id AND driver_id = p.driver_id
                  AND lap = p.lap) AS compound_before,
               (SELECT compound FROM laps WHERE session_id = p.session_id AND driver_id = p.driver_id
                  AND lap = p.lap + 1 AND p.lap + 1 <= ?) AS compound_after
        FROM pitstops p JOIN drivers d USING (driver_id)
        WHERE p.session_id = ? AND p.lap <= ? ORDER BY p.lap, p.driver_id""", (current, sid, current))
    stops = []
    for r in rows:
        done = r["lap"] < current
        stops.append({
            "driver_id": r["driver_id"], "code": r["code"], "stop": r["stop"], "lap": r["lap"],
            "kind": "pit_lane_pass" if r["pit_lane_pass"] else "stop",
            "compound_before": r["compound_before"], "compound_after": r["compound_after"],
            "pit_lane_s": _ms_to_s(r["pit_lane_ms"]) if done else None,
            "stationary_s": _ms_to_s(r["stationary_ms"]) if done else None,
        })
    return {"current_lap": current, "stops": stops}


# Within this many seconds, a car is in undercut range (either way).
UNDERCUT_RANGE_S = 3.0
# Laps back used to judge whether a gap is closing.
TREND_LAPS = 3


def _order_at(sid: int, lap: int) -> dict[str, sqlite3.Row]:
    return {r["driver_id"]: r for r in _q("""
        SELECT l.driver_id, d.code, l.position, l.gap_leader_ms, l.compound, l.tire_age, l.pit_in, l.pit_out
        FROM laps l JOIN drivers d USING (driver_id) WHERE l.session_id = ? AND l.lap = ?""", (sid, lap))}


@mcp.tool
@_guarded
def get_rivals(window: int = 2) -> dict:
    """The cars fighting our driver right now: everyone within `window`
    positions, plus anyone within undercut range. For each: the time gap now
    and three laps ago (is it closing?), their tires against ours, when they
    last stopped, and whether they are in undercut range. Also an estimate of
    what a stop costs here and which cars we would drop behind if we boxed.

    Args:
        window: How many positions either side to include (default 2).
    """
    clock = _clock()
    lap, sid = clock["current_lap"], _sid(clock["race_id"], clock["session_kind"])
    if lap < 1:
        return _refuse("The race has not started yet.", current_lap=lap)
    now = _order_at(sid, lap)
    me = now.get(clock["driver_id"])
    if me is None:
        return {"current_lap": lap, "my_driver": clock["driver_id"], "out_of_race": True, "rivals": []}
    then_lap = max(lap - TREND_LAPS, 1)
    then = _order_at(sid, then_lap)
    me_then = then.get(clock["driver_id"])

    last_stop = {r["driver_id"]: r["lap"] for r in _q(
        """SELECT driver_id, MAX(lap) AS lap FROM pitstops WHERE session_id = ? AND lap <= ?
           AND pit_lane_pass = 0 GROUP BY driver_id""", (sid, lap))}
    # what a stop costs: median pit-lane time of completed stops so far (none on the current lap)
    lane = sorted(r["pit_lane_ms"] for r in _q(
        """SELECT pit_lane_ms FROM pitstops WHERE session_id = ? AND lap < ? AND pit_lane_pass = 0
           AND pit_lane_ms IS NOT NULL""", (sid, lap)))
    pit_loss_ms = lane[len(lane) // 2] if lane else None

    def gap_to(row, ref) -> int | None:  # + means `row` is behind `ref`
        if row is None or ref is None or row["gap_leader_ms"] is None or ref["gap_leader_ms"] is None:
            return None
        return row["gap_leader_ms"] - ref["gap_leader_ms"]

    rivals = []
    for d, r in now.items():
        if d == clock["driver_id"]:
            continue
        gap = gap_to(r, me)
        near = abs(r["position"] - me["position"]) <= window
        if not near and (gap is None or abs(gap) > UNDERCUT_RANGE_S * 1000):
            continue
        gap_then = gap_to(then.get(d), me_then)
        rel = "ahead" if r["position"] < me["position"] else "behind"
        dist_now = abs(gap) if gap is not None else None
        dist_then = abs(gap_then) if gap_then is not None else None
        rivals.append({
            "code": r["code"], "driver_id": d, "position": r["position"], "relation": rel,
            "gap_s": _ms_to_s(dist_now),
            f"gap_{TREND_LAPS}_laps_ago_s": _ms_to_s(dist_then),
            "gap_change_per_lap_s": (_ms_to_s((dist_now - dist_then) / (lap - then_lap))
                                     if dist_now is not None and dist_then is not None and lap > then_lap else None),
            "compound": r["compound"], "tire_age": r["tire_age"],
            "tire_age_vs_ours": (r["tire_age"] - me["tire_age"]) if r["tire_age"] is not None
                                and me["tire_age"] is not None else None,
            "last_stop_lap": last_stop.get(d),
            "pitted_end_of_this_lap": bool(r["pit_in"]),
            "in_undercut_range": dist_now is not None and dist_now <= UNDERCUT_RANGE_S * 1000,
        })
    rivals.sort(key=lambda x: x["position"])

    would_drop_behind = []
    if pit_loss_ms is not None:
        for d, r in now.items():
            g = gap_to(r, me)
            if d != clock["driver_id"] and g is not None and 0 < g < pit_loss_ms:
                would_drop_behind.append({"code": r["code"], "position": r["position"], "gap_s": _ms_to_s(g)})
        would_drop_behind.sort(key=lambda x: x["position"])

    return {
        "current_lap": lap, "my_driver": clock["driver_id"], "my_position": me["position"],
        "my_compound": me["compound"], "my_tire_age": me["tire_age"],
        "gap_change_note": "gap_change_per_lap_s < 0 means the gap is shrinking",
        "undercut_range_s": UNDERCUT_RANGE_S,
        "pit_loss_estimate_s": _ms_to_s(pit_loss_ms),
        "if_we_box_now_we_rejoin_behind": would_drop_behind,
        "rivals": rivals,
    }


@mcp.tool
def get_final_result() -> dict:
    """The real final classification and our driver's whole race: finishing
    order with points and status, our driver's stops, and our position on
    every lap. Only for grading after the chequered flag: refused while the
    race is still running."""
    try:
        clock = _clock()
    except NoClock as exc:
        return _refuse(str(exc))
    if clock["current_lap"] < clock["total_laps"]:
        return _refuse(f"The race is still running (lap {clock['current_lap']} of {clock['total_laps']}). "
                       "The final result is only available after the chequered flag.")
    sid = _sid(clock["race_id"], clock["session_kind"])
    me = clock["driver_id"]
    classification = [dict(r) for r in _q("""
        SELECT r.position_text AS pos, d.code, d.driver_id, t.name AS team, r.grid, r.status, r.points
        FROM results r JOIN drivers d USING (driver_id) JOIN teams t USING (team_id)
        WHERE r.session_id = ? ORDER BY r.position_order""", (sid,))]
    stops = [dict(r) for r in _q("""
        SELECT p.stop, p.lap, p.pit_lane_pass,
               (SELECT compound FROM laps WHERE session_id = p.session_id AND driver_id = p.driver_id AND lap = p.lap) AS from_tire,
               (SELECT compound FROM laps WHERE session_id = p.session_id AND driver_id = p.driver_id AND lap = p.lap + 1) AS to_tire
        FROM pitstops p WHERE p.session_id = ? AND p.driver_id = ? ORDER BY p.stop""", (sid, me))]
    positions = [r["position"] for r in _q(
        "SELECT position FROM laps WHERE session_id = ? AND driver_id = ? ORDER BY lap", (sid, me))]
    return {"race_id": clock["race_id"], "my_driver": me, "classification": classification,
            "my_stops": stops, "my_position_by_lap": positions}


# --------------------------------------------------------------------------
# Qualifying
#
# The qualifying clock is session TIME (sim_clock.session_time_utc), not laps.
# Every qualifying tool sees only what had happened by then:
#   * a lap is visible once it has started; its time only once it is complete
#   * weather readings up to now
#   * Q1 / Q2 / Q3 are found from the running so far (a 5-minute lull in lap
#     starts ends a segment); time left is against the scheduled length, since
#     a future red flag can't be known
#   * eliminations are worked out from lap times at the moment the next
#     segment starts. Deleted lap times aren't in the data, so this is the
#     lap-time order, not the official one
# The official qualifying classification is only read by get_quali_final,
# which refuses until the session is over.
# --------------------------------------------------------------------------

SEGMENT_GAP_S = 300
SEGMENT_LENGTH_S = {1: 18 * 60, 2: 15 * 60, 3: 12 * 60}
SEGMENT_CARS_THROUGH = {1: 15, 2: 10}
# Quali laps with no time (out/in laps) count as on track for this long.
UNTIMED_LAP_S = 150
EVOLUTION_WINDOW_S = 300
EVOLUTION_STEADY_S = 0.3
EVOLUTION_MIN_LAPS = 3
EVOLUTION_REPRESENTATIVE = 1.07


def _ts(t: str | None):
    return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None


def _laptime(ms: int | None) -> str | None:
    if ms is None:
        return None
    m, rest = divmod(ms, 60000)
    return f"{m}:{rest / 1000:06.3f}"


def _mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _quali_view(clock: dict) -> dict:
    """Everything the qualifying tools may know at session time `now`."""
    sid = clock["race_id"] * 10 + 2
    now = _ts(clock["session_time_utc"])
    start = _ts(clock["session_start_utc"])
    rows = _q("""SELECT l.driver_id, d.code, l.lap, l.lap_start_utc, l.lap_time_ms, l.compound, l.tire_age, l.pit_out
                 FROM laps l JOIN drivers d USING (driver_id)
                 WHERE l.session_id = ? AND l.lap_start_utc IS NOT NULL AND l.lap_start_utc <= ?
                 ORDER BY l.lap_start_utc""", (sid, clock["session_time_utc"]))
    entrants = {r["driver_id"]: r["code"] for r in _q(
        """SELECT q.driver_id, d.code FROM qualifying_results q JOIN drivers d USING (driver_id)
           WHERE q.session_id = ?""", (sid,))}

    # segments from the running so far
    seg_starts, prev = [], None
    for r in rows:
        t = _ts(r["lap_start_utc"])
        if prev is None or (t - prev).total_seconds() > SEGMENT_GAP_S:
            seg_starts.append(t)
        prev = t
    seg_starts = seg_starts[:3]

    def segment_of(t) -> int:
        return max((i + 1 for i, st in enumerate(seg_starts) if t >= st), default=0)

    by_driver: dict[str, list] = {}
    for r in rows:
        by_driver.setdefault(r["driver_id"], []).append(r)

    laps = []  # visible laps with completion state
    for d, rs in by_driver.items():
        for i, r in enumerate(rs):
            st = _ts(r["lap_start_utc"])
            nxt = _ts(rs[i + 1]["lap_start_utc"]) if i + 1 < len(rs) else None
            if r["lap_time_ms"] is not None:
                end = st + timedelta(milliseconds=r["lap_time_ms"])
            else:
                end = nxt if nxt and (nxt - st).total_seconds() < 600 else st + timedelta(seconds=UNTIMED_LAP_S)
            done = end <= now
            laps.append({"driver_id": d, "code": r["code"], "lap": r["lap"], "start": st, "end": end, "done": done,
                         "time_ms": r["lap_time_ms"] if done else None, "compound": r["compound"],
                         "tire_age": r["tire_age"], "out_lap": bool(r["pit_out"]), "segment": segment_of(st)})

    seg = segment_of(now) if seg_starts else 0
    last_activity = max((l["start"] for l in laps), default=None)
    running = bool(last_activity and (now - last_activity).total_seconds() <= SEGMENT_GAP_S)

    def bests(segment: int, before=None) -> dict[str, dict]:
        out = {}
        for l in laps:
            if l["segment"] == segment and l["time_ms"] and (before is None or l["end"] <= before):
                b = out.get(l["driver_id"])
                if b is None or l["time_ms"] < b["time_ms"]:
                    out[l["driver_id"]] = l
        return out

    # who is still in: rank each finished segment at the moment the next one started
    alive = set(entrants)
    eliminated = []
    for k in range(1, seg):
        b = bests(k, before=seg_starts[k])
        ranked = sorted((d for d in alive), key=lambda d: (b[d]["time_ms"] if d in b else 10 ** 9, d))
        through = SEGMENT_CARS_THROUGH[k]
        for d in ranked[through:]:
            eliminated.append({"driver_id": d, "code": entrants[d], "out_in": f"Q{k}",
                               "best": _laptime(b[d]["time_ms"]) if d in b else None})
        alive = set(ranked[:through])

    cur_best = bests(seg) if seg else {}
    cars = []
    for d in alive:
        mine = [l for l in laps if l["driver_id"] == d]
        on = next((l for l in reversed(mine) if l["start"] <= now < l["end"]), None)
        latest = mine[-1] if mine else None
        status = "garage" if on is None else ("out lap" if on["out_lap"] else "on a lap")
        b = cur_best.get(d)
        cars.append({
            "driver_id": d, "code": entrants[d], "status": status,
            "compound": latest["compound"] if latest else None,
            "tire_age": latest["tire_age"] if latest else None,
            "best_ms": b["time_ms"] if b else None, "best": _laptime(b["time_ms"]) if b else None,
            "best_compound": b["compound"] if b else None,
            "best_set_at": _mmss((b["end"] - seg_starts[seg - 1]).total_seconds()) if b else None,
            "laps_this_segment": sum(1 for l in mine if l["segment"] == seg),
            "is_my_driver": d == clock["driver_id"],
        })
    cars.sort(key=lambda c: (c["best_ms"] is None, c["best_ms"] or 0, c["code"]))
    for i, c in enumerate(cars):
        c["position"] = i + 1

    # track evolution: field best lap completed in the last 5 min vs the 5 min before
    # Only laps from the current segment, so a break's in-laps never count,
    # and only windows with a few timed laps in them.
    # Representative laps only: no out laps, and nothing slower than 107% of the
    # segment's best so far (cool-down laps, in-laps, aborted laps).
    seg_times = [l["time_ms"] for l in laps if l["time_ms"] and l["segment"] == seg and not l["out_lap"]]
    rep_limit = min(seg_times) * EVOLUTION_REPRESENTATIVE if seg_times else 0

    def window_best(a, b):
        ts = [l for l in laps if l["time_ms"] and l["segment"] == seg and not l["out_lap"]
              and l["time_ms"] <= rep_limit and a < l["end"] <= b]
        return min(ts, key=lambda l: l["time_ms"]) if len(ts) >= EVOLUTION_MIN_LAPS else None
    w = timedelta(seconds=EVOLUTION_WINDOW_S)
    recent, before = window_best(now - w, now), window_best(now - 2 * w, now - w)
    evolution = {"window_minutes": EVOLUTION_WINDOW_S // 60,
                 "recent_best": ({"code": recent["code"], "time": _laptime(recent["time_ms"]),
                                  "compound": recent["compound"]} if recent else None),
                 "previous_best": ({"code": before["code"], "time": _laptime(before["time_ms"]),
                                    "compound": before["compound"]} if before else None)}
    if recent and before:
        ch = (recent["time_ms"] - before["time_ms"]) / 1000
        evolution["change_s"] = round(ch, 3)
        evolution["direction"] = ("steady" if abs(ch) < EVOLUTION_STEADY_S else
                                  "improving" if ch < 0 else "worsening")
        evolution["note"] = "change_s < 0 means the field's best lap got faster"
    else:
        evolution["direction"] = "unknown"

    # weather up to now
    wx = _q("""SELECT time_utc, air_temp_c, track_temp_c, rainfall, humidity_pct FROM weather
               WHERE session_id = ? AND time_utc <= ? ORDER BY time_utc DESC LIMIT 12""",
            (sid, clock["session_time_utc"]))
    weather = None
    if wx:
        last_rain = _q("""SELECT MAX(time_utc) AS t FROM weather WHERE session_id = ? AND time_utc <= ?
                          AND rainfall = 1""", (sid, clock["session_time_utc"]))[0]["t"]
        weather = {"time_utc": wx[0]["time_utc"], "raining": bool(wx[0]["rainfall"]),
                   "track_temp_c": wx[0]["track_temp_c"], "air_temp_c": wx[0]["air_temp_c"],
                   "humidity_pct": wx[0]["humidity_pct"],
                   "rain_readings_last_10": sum(r["rainfall"] for r in wx[:10]),
                   "minutes_since_rain": (round((now - _ts(last_rain)).total_seconds() / 60, 1)
                                          if last_rain else None),
                   "track_temp_change_10_min": (round(wx[0]["track_temp_c"] - wx[min(9, len(wx) - 1)]["track_temp_c"], 1)
                                                if len(wx) > 1 else None)}

    seg_elapsed = (now - seg_starts[seg - 1]).total_seconds() if seg else 0
    return {
        "sid": sid, "now": now, "laps": laps, "seg_starts": seg_starts, "entrants": entrants,
        "segment": seg, "running": running, "seg_elapsed": seg_elapsed, "cars": cars,
        "eliminated": eliminated, "evolution": evolution, "weather": weather,
        "session_elapsed": (now - start).total_seconds() if start else None,
    }


def _segment_block(v: dict) -> dict:
    seg = v["segment"]
    if not seg:
        return {"segment": None, "phase": "before the session", "running": False}
    length = SEGMENT_LENGTH_S[seg]
    if not v["running"]:
        return {"segment": f"Q{seg}", "running": False,
                "phase": f"no running since Q{seg} (break or stoppage)", "elapsed": _mmss(v["seg_elapsed"]),
                "scheduled_length": _mmss(length), "scheduled_time_left": None, "scheduled_time_left_s": None,
                "cars_through": SEGMENT_CARS_THROUGH.get(seg),
                "note": "no laps started for 5 minutes: a break between segments or a stoppage"}
    return {"segment": f"Q{seg}", "running": v["running"],
            "phase": f"Q{seg} running" if v["running"] else f"no running since Q{seg} (break or stoppage)",
            "elapsed": _mmss(v["seg_elapsed"]), "scheduled_length": _mmss(length),
            "scheduled_time_left": _mmss(length - v["seg_elapsed"]),
            "scheduled_time_left_s": max(0, int(length - v["seg_elapsed"])),
            "cars_through": SEGMENT_CARS_THROUGH.get(seg),
            "note": "time left is against the scheduled length; a later red flag can't be known"}


@mcp.tool
@_guarded
def get_quali_state() -> dict:
    """Qualifying right now: which segment (Q1/Q2/Q3) and its scheduled time
    left, the order on best lap times this segment, each car's status
    (garage / out lap / on a lap) and tires, who has been eliminated, how the
    track is evolving (the field's best lap in the last 5 minutes against the 5
    before), and the weather. Only what has happened by the current session time."""
    clock = _clock("qualifying")
    v = _quali_view(clock)
    on_track = [c for c in v["cars"] if c["status"] != "garage"]
    return {
        "session_time_utc": clock["session_time_utc"], "my_driver": clock["driver_id"],
        "session_elapsed": _mmss(v["session_elapsed"] or 0),
        **_segment_block(v),
        "order": [{k: c[k] for k in ("position", "code", "best", "best_compound", "best_set_at", "status",
                                     "compound", "tire_age", "laps_this_segment", "is_my_driver")} for c in v["cars"]],
        "on_track": {"count": len(on_track), "cars": [c["code"] for c in on_track],
                     "by_compound": {k: sum(1 for c in on_track if c["compound"] == k)
                                     for k in sorted({c["compound"] for c in on_track if c["compound"]})}},
        "eliminated": v["eliminated"],
        "track_evolution": v["evolution"],
        "weather": v["weather"],
        "my_status": next((c["status"] for c in v["cars"] if c["is_my_driver"]),
                          "eliminated" if any(e["driver_id"] == clock["driver_id"] for e in v["eliminated"])
                          else "unknown"),
    }


@mcp.tool
@_guarded
def get_quali_driver(driver: str) -> dict:
    """One driver's qualifying so far: every lap started, with the segment,
    when it started (session time), its time once complete, tire compound and
    age, and whether it was an out lap.

    Args:
        driver: Three-letter code, driver id or surname.
    """
    clock = _clock("qualifying")
    d = _resolve_driver(clock["race_id"], driver, clock["session_kind"])
    if d is None:
        return _refuse(f"No driver '{driver}' in this session.")
    v = _quali_view(clock)
    start = _ts(clock["session_start_utc"]) or (v["seg_starts"][0] if v["seg_starts"] else v["now"])
    mine = [l for l in v["laps"] if l["driver_id"] == d["driver_id"]]
    return {"code": d["code"], "session_elapsed": _mmss(v["session_elapsed"] or 0),
            "laps": [{"lap": l["lap"], "segment": f"Q{l['segment']}" if l["segment"] else None,
                      "started_at": _mmss((l["start"] - start).total_seconds()),
                      "started_at_s": round((l["start"] - start).total_seconds()),
                      "time": _laptime(l["time_ms"]), "complete": l["done"], "compound": l["compound"],
                      "tire_age": l["tire_age"], "out_lap": l["out_lap"]} for l in mine]}


@mcp.tool
@_guarded
def get_quali_rivals(window: int = 2) -> dict:
    """The cars around our driver in this segment's order, the elimination
    line and our margin to it, who is on track now and on what tires, and who
    has set a faster lap in the last 3 minutes.

    Args:
        window: How many places either side of us to include (default 2).
    """
    clock = _clock("qualifying")
    v = _quali_view(clock)
    seg = v["segment"]
    me = next((c for c in v["cars"] if c["is_my_driver"]), None)
    if me is None:
        return {"my_driver": clock["driver_id"], "still_in": False,
                "eliminated": [e for e in v["eliminated"] if e["driver_id"] == clock["driver_id"]]}
    cut = SEGMENT_CARS_THROUGH.get(seg)
    cut_car = v["cars"][cut - 1] if cut and len(v["cars"]) >= cut else None
    margin = None
    if cut and me["best_ms"] is not None and cut_car and cut_car["best_ms"] is not None:
        if me["position"] <= cut:
            nxt = v["cars"][cut] if len(v["cars"]) > cut else None
            margin = (round((nxt["best_ms"] - me["best_ms"]) / 1000, 3) if nxt and nxt["best_ms"] else None)
        else:
            margin = round((me["best_ms"] - cut_car["best_ms"]) / 1000, 3) * -1
    recent = [l for l in v["laps"] if l["time_ms"] and l["segment"] == seg
              and (v["now"] - l["end"]).total_seconds() <= 180]
    rivals = []
    for c in v["cars"]:
        if c["is_my_driver"]:
            continue
        near = abs(c["position"] - me["position"]) <= window
        if not near and c["status"] == "garage":
            continue
        gap = (round((c["best_ms"] - me["best_ms"]) / 1000, 3)
               if c["best_ms"] is not None and me["best_ms"] is not None else None)
        rivals.append({"code": c["code"], "position": c["position"],
                       "relation": "ahead" if c["position"] < me["position"] else "behind",
                       "gap_s": abs(gap) if gap is not None else None,
                       "best": c["best"], "best_compound": c["best_compound"], "status": c["status"],
                       "compound": c["compound"], "tire_age": c["tire_age"],
                       "improved_last_3_min": any(l["driver_id"] == c["driver_id"] for l in recent)})
    return {
        "my_driver": clock["driver_id"], "still_in": True, "segment": f"Q{seg}" if seg else None,
        "my_position": me["position"], "my_best": me["best"], "my_status": me["status"],
        "cut": ({"cars_through": cut, "car_on_the_line": cut_car["code"] if cut_car else None,
                 "line_time": cut_car["best"] if cut_car else None,
                 "my_margin_s": margin,
                 "margin_note": "positive = inside the cut by that much; negative = outside it"} if cut else None),
        "rivals": rivals,
    }


@mcp.tool
def get_session_info(race_id: int, kind: str = "qualifying") -> dict:
    """For the backend's clock only (no agent is given this): a session's
    scheduled start and when its running ends."""
    code = {"race": 1, "qualifying": 2, "sprint": 3}.get(kind)
    sid = race_id * 10 + code if code else None
    row = _q("SELECT start_utc FROM sessions WHERE session_id = ?", (sid,))
    if not row:
        return _refuse(f"No {kind} session for race {race_id}.")
    last = _q("""SELECT lap_start_utc, lap_time_ms FROM laps WHERE session_id = ? AND lap_start_utc IS NOT NULL
                 ORDER BY lap_start_utc DESC LIMIT 1""", (sid,))
    if not last:
        return _refuse(f"No laps for the {kind} session of race {race_id}.")
    end = _ts(last[0]["lap_start_utc"]) + timedelta(milliseconds=last[0]["lap_time_ms"] or UNTIMED_LAP_S * 1000)
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"  # noqa: E731
    # when each segment goes green, like the session schedule a pit wall has
    starts, prev = [], None
    for r in _q("SELECT lap_start_utc FROM laps WHERE session_id = ? AND lap_start_utc IS NOT NULL "
                "ORDER BY lap_start_utc", (sid,)):
        t = _ts(r["lap_start_utc"])
        if prev is None or (t - prev).total_seconds() > SEGMENT_GAP_S:
            starts.append(fmt(t))
        prev = t
    return {"race_id": race_id, "kind": kind, "start_utc": fmt(_ts(row[0]["start_utc"])),
            "end_utc": fmt(end + timedelta(seconds=30)),
            "segment_starts_utc": starts[:3] if kind == "qualifying" else []}


@mcp.tool
def get_quali_final() -> dict:
    """The official qualifying classification (Q1/Q2/Q3 times) and our
    driver's best lap: its time, tire and when in the session it was set.
    Only for grading: refused until the session is over."""
    try:
        clock = _clock("qualifying")
    except NoClock as exc:
        return _refuse(str(exc))
    if clock["session_time_utc"] < clock["session_end_utc"]:
        return _refuse("Qualifying is still running. The final classification is only available after "
                       "the chequered flag.")
    v = _quali_view(clock)
    sid = v["sid"]
    classification = [{**dict(r), "q1": _laptime(r["q1_ms"]), "q2": _laptime(r["q2_ms"]), "q3": _laptime(r["q3_ms"])}
                      for r in _q("""SELECT q.position, d.code, d.driver_id, t.name AS team, q.q1_ms, q.q2_ms, q.q3_ms
                                     FROM qualifying_results q JOIN drivers d USING (driver_id)
                                     JOIN teams t USING (team_id) WHERE q.session_id = ? ORDER BY q.position""", (sid,))]
    mine = [l for l in v["laps"] if l["driver_id"] == clock["driver_id"] and l["time_ms"]]
    best = min(mine, key=lambda l: l["time_ms"]) if mine else None
    by_seg = {}
    for l in mine:
        k = f"Q{l['segment']}"
        if k not in by_seg or l["time_ms"] < by_seg[k]["time_ms"]:
            by_seg[k] = l
    fmt = lambda l: {"time": _laptime(l["time_ms"]), "compound": l["compound"], "segment": f"Q{l['segment']}",  # noqa: E731
                     "set_at_in_segment": _mmss((l["end"] - v["seg_starts"][l["segment"] - 1]).total_seconds()),
                     "lap": l["lap"]}
    return {"classification": classification, "my_driver": clock["driver_id"],
            "my_best_lap": fmt(best) if best else None,
            "my_best_by_segment": {k: fmt(l) for k, l in sorted(by_seg.items())},
            "my_runs": [{"segment": f"Q{l['segment']}", "lap": l["lap"], "time": _laptime(l["time_ms"]),
                         "compound": l["compound"]} for l in mine]}


# --------------------------------------------------------------------------
# Ask AI: lap records, the season so far, and the rules file
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Race control: flags, safety car, DRS, incidents, penalties
#
# Messages are timestamped, so the no-future rule is a time cutoff: in a race or
# sprint, the moment the leader finished the current lap; in qualifying, the
# session clock. Nothing sent after that is returned, or used for the status.
# --------------------------------------------------------------------------

RC_NOISE = re.compile(r"^(?:AWNINGS MAY BE USED|CLEAR IN TRACK SECTOR)|\bBLUE FLAG\b")
RC_CODES = re.compile(r"\(([A-Z]{3})\)")


def _rc_cutoff(clock: dict, sid: int) -> tuple[str | None, str]:
    if clock["session_kind"] == "qualifying":
        return clock["session_time_utc"], "the current session time"
    lap = clock["current_lap"]
    if lap == 0:
        start = _q("SELECT MIN(lap_start_utc) AS t FROM laps WHERE session_id = ? AND lap = 1", (sid,))
        return (start[0]["t"] if start else None), "the start (the cars are on the grid)"
    end = _q("""SELECT strftime('%Y-%m-%dT%H:%M:%fZ', lap_start_utc, '+' || (lap_time_ms / 1000.0) || ' seconds') AS t
                FROM laps WHERE session_id = ? AND lap = ? AND position = 1""", (sid, lap))
    return (end[0]["t"] if end else None), f"the end of lap {lap}"


def _track_status(msgs: list[sqlite3.Row]) -> dict:
    """Replay the messages in order to get the state of the track now."""
    neutral, red, drs, yellow = None, False, None, set()
    for m in msgs:
        text, flag, scope = m["message"].upper(), m["flag"], m["scope"]
        if m["category"] == "SafetyCar":
            if "VIRTUAL SAFETY CAR DEPLOYED" in text:
                neutral = "virtual safety car"
            elif "VIRTUAL SAFETY CAR ENDING" in text or "VSC ENDING" in text:
                neutral = "virtual safety car ending"
            elif "SAFETY CAR DEPLOYED" in text:
                neutral = "safety car"
            elif "SAFETY CAR IN THIS LAP" in text:
                neutral = "safety car coming in this lap"
        elif flag == "RED":
            red = True
        elif flag in ("CLEAR", "GREEN") and scope == "Track":
            neutral, yellow = None, set()
            if flag == "GREEN" or "PIT EXIT OPEN" in text:
                red = False
        elif flag in ("YELLOW", "DOUBLE YELLOW") and scope == "Sector" and m["sector"] is not None:
            yellow.add((m["sector"], flag.lower()))
        elif flag == "CLEAR" and scope == "Sector":
            yellow = {y for y in yellow if y[0] != m["sector"]}
        elif m["category"] == "SessionStatus" and "STARTED" in text:
            red = False
        if m["category"] == "Drs" or re.fullmatch(r"DRS (?:ENABLED|DISABLED)", text):
            drs = "enabled" if "ENABLED" in text else "disabled"
    yellows = sorted(yellow)
    if red:
        summary = "red flag: the session is stopped"
    elif neutral:
        summary = neutral + (" (the field is slowed and can't overtake)" if neutral in ("safety car", "virtual safety car") else "")
    elif yellows:
        summary = "yellow flags in " + ", ".join(f"sector {s}" + (" (double)" if f == "double yellow" else "") for s, f in yellows)
    else:
        summary = "green: no flags out"
    return {"summary": summary, "red_flag": red, "safety_car": neutral, "drs": drs,
            "yellow_sectors": [{"sector": s, "flag": f} for s, f in yellows]}


@mcp.tool
@_guarded
def get_race_control(last_laps: int = 3, last_minutes: int = 6, driver: str | None = None) -> dict:
    """Race control's messages so far: flags (yellow, red, green), the safety car
    and virtual safety car, DRS enabled or disabled, incidents noted, investigations
    and penalties, deleted lap times. Plus the track status right now, worked out
    from them. Works in every session; only messages already sent are returned.

    Args:
        last_laps: Race / sprint: how many recent laps of messages to list.
        last_minutes: Qualifying: how many recent minutes of messages to list.
        driver: Optional three-letter code: also list the latest messages about
                that car this session (penalties, investigations, deleted laps).
    """
    clock = _clock(None)
    sid = _sid(clock["race_id"], clock["session_kind"])
    cutoff, as_of = _rc_cutoff(clock, sid)
    if cutoff is None:
        return _refuse("No timing for this point of the session yet, so no race control messages.")
    rows = _q("""SELECT r.*, d.code FROM race_control r LEFT JOIN drivers d USING (driver_id)
                 WHERE r.session_id = ? AND r.time_utc <= ? ORDER BY r.seq""", (sid, cutoff))
    quali = clock["session_kind"] == "qualifying"
    start = _ts(clock.get("session_start_utc")) if quali else None

    def show(m: sqlite3.Row) -> dict:
        codes = sorted(set(RC_CODES.findall(m["message"])) | ({m["code"]} if m["code"] else set()))
        out = {"message": m["message"], "category": m["category"], "flag": m["flag"], "cars": codes}
        if quali and start:
            out["at"] = _mmss(max(0, (_ts(m["time_utc"]) - start).total_seconds()))
        else:
            out["lap"] = m["lap"]
        return out

    shown = [m for m in rows if not RC_NOISE.search(m["message"].upper())]
    if quali:
        now = _ts(cutoff)
        recent = [m for m in shown if (now - _ts(m["time_utc"])).total_seconds() <= last_minutes * 60]
    else:
        lap = clock["current_lap"]
        recent = [m for m in shown if lap == 0 or (m["lap"] or 0) > lap - max(1, last_laps)]
    me = _q("SELECT code FROM drivers WHERE driver_id = ?", (clock["driver_id"],))
    my_code = me[0]["code"] if me else None
    about = lambda code: [show(m) for m in shown if code and f"({code})" in m["message"]][-3:]  # noqa: E731
    texts = " | ".join(m["message"].upper() for m in rows)
    out = {"as_of": as_of, "track_status": _track_status(rows),
           "recent": [show(m) for m in recent][-15:],
           "my_driver_messages": about(my_code),
           # whether each has happened at all this session (for "after the safety car" etc.)
           "so_far": {"safety_car": bool(re.search(r"(?<!VIRTUAL )SAFETY CAR DEPLOYED", texts)),
                      "virtual_safety_car": "VIRTUAL SAFETY CAR DEPLOYED" in texts,
                      "red_flag": any(m["flag"] == "RED" for m in rows),
                      "yellow_flag": any(m["flag"] in ("YELLOW", "DOUBLE YELLOW") for m in rows),
                      "penalty": bool(re.search(r"\bPENALTY\b", texts)),
                      "investigation": "UNDER INVESTIGATION" in texts or "WILL BE INVESTIGATED" in texts,
                      # which cars, so "HAM has a penalty" is checked against HAM
                      "penalised": sorted({c for m in rows if re.search(r"\bPENALTY\b", m["message"].upper())
                                           and "NO FURTHER" not in m["message"].upper()
                                           for c in RC_CODES.findall(m["message"])}),
                      "noted_or_investigated": sorted({c for m in rows if re.search(
                          r"\bNOTED\b|INVESTIGAT", m["message"].upper()) for c in RC_CODES.findall(m["message"])})},
           "messages_so_far": len(shown)}
    if driver:
        out["driver_messages"] = {"driver": driver.strip().upper(), "messages": about(driver.strip().upper())}
    return out


KNOWLEDGE_FILE = DATA_DIR / "knowledge" / "f1_rules.md"
# Words too common in questions to say what a question is about.
_FILLER = {"what", "whats", "who", "how", "does", "the", "this", "that", "race", "races", "f1", "formula",
           "are", "and", "for", "can", "with", "why", "when", "wins", "win", "will", "mean", "means", "rule",
           "rules", "about", "tell", "explain", "work", "works"}


@mcp.tool
@_guarded
def get_lap_records() -> dict:
    """Records in this race so far: the fastest lap (from laps already run)
    and the fastest pit stops (from stops already completed; a stop on the
    current lap isn't timed yet)."""
    clock = _clock()
    lap, sid = clock["current_lap"], _sid(clock["race_id"], clock["session_kind"])
    fastest = _q("""SELECT d.code, l.lap, l.lap_time_ms FROM laps l JOIN drivers d USING (driver_id)
                    WHERE l.session_id = ? AND l.lap <= ? AND l.lap_time_ms IS NOT NULL
                    ORDER BY l.lap_time_ms LIMIT 1""", (sid, lap))
    def stops(order: str) -> list[dict]:
        return [{"code": r["code"], "lap": r["lap"], "stationary_s": _ms_to_s(r["stationary_ms"]),
                 "pit_lane_s": _ms_to_s(r["pit_lane_ms"])} for r in _q(f"""
            SELECT d.code, p.lap, p.stationary_ms, p.pit_lane_ms FROM pitstops p JOIN drivers d USING (driver_id)
            WHERE p.session_id = ? AND p.lap < ? AND p.pit_lane_pass = 0 AND p.{order} IS NOT NULL
            ORDER BY p.{order} LIMIT 3""", (sid, lap))]
    return {
        "current_lap": lap,
        "fastest_lap": ({"code": fastest[0]["code"], "lap": fastest[0]["lap"],
                         "lap_time_s": _ms_to_s(fastest[0]["lap_time_ms"])} if fastest else None),
        "fastest_stops_stationary": stops("stationary_ms"),
        "fastest_stops_pit_lane": stops("pit_lane_ms"),
    }


def _season_point(clock: dict) -> tuple[int, int]:
    race = _q("SELECT season, round FROM races WHERE race_id = ?", (clock["race_id"],))[0]
    return race["season"], race["round"]


@mcp.tool
@_guarded
def get_championship(kind: str = "drivers") -> dict:
    """Championship standings going into the current race (after the
    previous round). Nothing from this race or later.

    Args:
        kind: "drivers" or "constructors".
    """
    clock = _clock(None)
    season, rnd = _season_point(clock)
    if kind.lower().startswith("construct") or kind.lower().startswith("team"):
        rows = _q("""SELECT s.position, t.team_id, t.name AS team, s.points, s.wins
                     FROM constructor_standings_before s JOIN teams t USING (team_id)
                     WHERE s.race_id = ? ORDER BY s.position IS NULL, s.position""", (clock["race_id"],))
        kind = "constructors"
    else:
        rows = _q("""SELECT s.position, d.code, d.first_name || ' ' || d.last_name AS name, t.name AS team,
                            s.points, s.wins
                     FROM driver_standings_before s JOIN drivers d USING (driver_id)
                     LEFT JOIN teams t USING (team_id)
                     WHERE s.race_id = ? ORDER BY s.position IS NULL, s.position""", (clock["race_id"],))
        kind = "drivers"
    return {"season": season, "kind": kind, "after_round": rnd - 1,
            "note": "standings going into this race" if rnd > 1 else "round 1: no standings yet",
            "standings": [dict(r) for r in rows]}


def _resolve_team(name: str) -> sqlite3.Row | None:
    key = name.strip().lower()
    for row in _q("SELECT team_id, name FROM teams"):
        if key in (row["team_id"], row["name"].lower()) or key in row["name"].lower():
            return row
    return None


@mcp.tool
@_guarded
def get_season_summary(driver: str | None = None, team: str | None = None) -> dict:
    """How a driver or team's season has gone in the rounds before this one:
    championship position, points and wins going into this race, plus
    Grand Prix podiums, best finish and retirements from earlier rounds.

    Args:
        driver: Three-letter code, driver id or surname.
        team: Team name or id, e.g. "McLaren".
    """
    clock = _clock(None)
    season, rnd = _season_point(clock)
    if bool(driver) == bool(team):
        return _refuse("Give exactly one of driver or team.")
    if driver:
        d = _resolve_driver(clock["race_id"], driver, clock["session_kind"])
        if d is None:
            return _refuse(f"No driver '{driver}' in this race.")
        who, col, val = d["code"], "r.driver_id", d["driver_id"]
        st = _q("""SELECT position, points, wins FROM driver_standings_before WHERE race_id = ? AND driver_id = ?""",
                (clock["race_id"], d["driver_id"]))
    else:
        t = _resolve_team(team)
        if t is None:
            return _refuse(f"No team '{team}'.")
        who, col, val = t["name"], "r.team_id", t["team_id"]
        st = _q("""SELECT position, points, wins FROM constructor_standings_before WHERE race_id = ? AND team_id = ?""",
                (clock["race_id"], t["team_id"]))
    rows = _q(f"""
        SELECT ra.round, ra.name, r.position_text, r.finish_position, r.status, r.points, d.code
        FROM results r JOIN sessions se ON se.session_id = r.session_id AND se.kind = 'race'
        JOIN races ra ON ra.race_id = se.race_id JOIN drivers d ON d.driver_id = r.driver_id
        WHERE ra.season = ? AND ra.round < ? AND {col} = ? ORDER BY ra.round""", (season, rnd, val))
    finishes = [r["finish_position"] for r in rows if r["finish_position"]]
    return {
        "season": season, "who": who, "rounds_before_this_race": rnd - 1,
        "championship_going_in": dict(st[0]) if st else None,
        "grand_prix_podiums": sum(1 for f in finishes if f <= 3),
        "grand_prix_wins": sum(1 for f in finishes if f == 1),
        "best_grand_prix_finish": min(finishes) if finishes else None,
        "grand_prix_retirements": sum(1 for r in rows if r["position_text"] in ("R", "D", "W", "N", "F")),
        "results": [{"round": r["round"], "race": r["name"], "car": r["code"], "pos": r["position_text"],
                     "points": r["points"]} for r in rows],
    }


def _rules() -> list[dict]:
    entries, cur = [], None
    for line in KNOWLEDGE_FILE.read_text().splitlines():
        if line.startswith("## "):
            rid, _, title = line[3:].partition(":")
            cur = {"id": rid.strip(), "title": title.strip(), "keywords": [], "text": ""}
            entries.append(cur)
        elif cur and line.startswith("keywords:"):
            cur["keywords"] = [k.strip().lower() for k in line[9:].split(",") if k.strip()]
        elif cur and line.strip():
            cur["text"] = (cur["text"] + " " + line.strip()).strip()
    return entries


@mcp.tool
def list_rules() -> list[dict]:
    """Every entry in the curated F1 rules and history file: id and title."""
    return [{"id": e["id"], "title": e["title"]} for e in _rules()]


@mcp.tool
def get_rule(rule_id: str) -> dict:
    """One entry from the curated rules file, by id (from list_rules or lookup_rules)."""
    e = next((e for e in _rules() if e["id"] == rule_id.strip()), None)
    return {"id": e["id"], "title": e["title"], "text": e["text"]} if e else _refuse(f"No rules entry '{rule_id}'.")


@mcp.tool
def lookup_rules(query: str) -> dict:
    """Search the curated F1 rules and history file. Returns the best-matching
    entries with their full text. Only answer rules questions from these.

    Args:
        query: A few words, e.g. "DRS", "undercut", "points for a win".
    """
    q = query.lower()
    words = {w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 2 and w not in _FILLER}
    scored = []
    for e in _rules():
        hay = set(re.findall(r"[a-z0-9]+", " ".join(e["keywords"]) + " " + e["title"].lower() + " " + e["id"]))
        score = sum(3 for k in e["keywords"] if re.search(rf"\b{re.escape(k)}\b", q)) + len(words & hay)
        if score >= 2:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return {"query": query, "entries": [{"id": e["id"], "title": e["title"], "text": e["text"]}
                                        for _, e in scored[:3]],
            "note": "nothing in the rules file matches" if not scored else None}


if __name__ == "__main__":
    mcp.run(show_banner=False)
