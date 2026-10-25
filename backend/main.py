"""FastAPI backend for Pitwall.

Run from the project root (no --reload, so one edit never spawns a second
sim loop):

    .venv/bin/python -m uvicorn main:app --app-dir backend --port 8000

All race data is read through the MCP server, which only answers for laps
that have already happened. User-generated state (the sim clock, decisions)
lives in data/pitwall_user.db; data/races.db is never written.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import llm  # noqa: E402
import focus
import mcp_client  # noqa: E402
import sim  # noqa: E402
import sim_quali  # noqa: E402
import user_db  # noqa: E402
from team import PitwallTeam  # noqa: E402

DEFAULT_RACE_ID = 202512  # 2025 British GP


@asynccontextmanager
async def lifespan(_: FastAPI):
    user_db.connect().close()
    user_db.set_clock(running=0)  # never resume a race on its own after a restart
    await mcp_client.shared.open()
    sim.start_loop()
    yield
    await sim.stop_loop()
    await mcp_client.shared.close()


app = FastAPI(title="Pitwall API", version="0.2.0", lifespan=lifespan)

# The Vite dev server runs on another origin in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "pitwall-api",
        "model": llm.MODEL_NAME,
        "portkey_key_found": llm.has_key(),
    }


@app.get("/api/mcp/ping")
async def mcp_ping() -> dict:
    try:
        return await mcp_client.call("ping")
    except Exception as exc:  # surface MCP startup failures as a clean 502
        raise HTTPException(status_code=502, detail=f"MCP server unreachable: {exc}")


# --------------------------------------------------------------------------
# Races
# --------------------------------------------------------------------------


@app.get("/api/races")
async def races() -> dict:
    return {"default_race_id": DEFAULT_RACE_ID, "default_quali_race_id": DEFAULT_QUALI_RACE_ID,
            "default_sprint_race_id": DEFAULT_SPRINT_RACE_ID,
            "races": await mcp_client.call("list_races")}


@app.get("/api/races/{race_id}/entries")
async def entries(race_id: int, session: str = "race") -> dict:
    data = await mcp_client.call("get_entry_list", {"race_id": race_id,
                                                    "session": "sprint" if session == "sprint" else "race"})
    if data.get("refused"):
        raise HTTPException(404, data["reason"])
    return data


# --------------------------------------------------------------------------
# Sim
# --------------------------------------------------------------------------


DEFAULT_QUALI_RACE_ID = 202522   # 2025 Las Vegas GP: a wet qualifying
DEFAULT_SPRINT_RACE_ID = 202506  # 2025 Miami GP: a wet-to-dry sprint


class SetupBody(BaseModel):
    race_id: int | None = None
    driver_id: str
    session: Literal["race", "qualifying", "sprint"] = "race"


class SpeedBody(BaseModel):
    seconds_per_lap: float | None = Field(None, ge=0.2, le=30)   # race
    quali_speed: float | None = Field(None, ge=5, le=300)        # qualifying: session seconds per real second


class CallBody(BaseModel):
    call: Literal["BOX", "STAY_OUT", "GO_OUT", "WAIT", "CONTINUE", "ABORT", "DEFEND", "PUSH", "MANAGE"]
    tire: Literal["WET", "INTERMEDIATE", "SOFT", "MEDIUM", "HARD"] | None = None


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=500)


@app.post("/api/sim/setup")
async def setup(body: SetupBody) -> dict:
    """Load a race on the grid (lap 0), or a qualifying session at its start, for a driver, paused."""
    race_id = body.race_id or {"qualifying": DEFAULT_QUALI_RACE_ID, "sprint": DEFAULT_SPRINT_RACE_ID}.get(
        body.session, DEFAULT_RACE_ID)
    info = await mcp_client.call("get_entry_list", {"race_id": race_id,
                                                    "session": "sprint" if body.session == "sprint" else "race"})
    if info.get("refused"):
        raise HTTPException(404, info["reason"])
    if body.driver_id not in {e["driver_id"] for e in info["entries"]}:
        raise HTTPException(400, f"{body.driver_id} is not entered in this race.")
    if body.session == "qualifying":
        try:
            clock = await sim_quali.setup(race_id, body.driver_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        focus.ensure_preview(clock)
        return await state()
    user_db.clear_decisions(race_id, body.driver_id, body.session)
    clock = user_db.set_clock(race_id=race_id, driver_id=body.driver_id, current_lap=0, session_kind=body.session,
                              total_laps=info["scheduled_laps"], running=0)
    focus.ensure_preview(clock)
    return await state()


class FollowBody(BaseModel):
    driver_id: str = Field(min_length=1, max_length=64)


@app.post("/api/sim/follow")
async def follow(body: FollowBody) -> dict:
    """Follow a different driver from right now, without restarting the session."""
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        raise HTTPException(400, "Load a session first.")
    kind = clock["session_kind"]
    info = await mcp_client.call("get_entry_list", {"race_id": clock["race_id"],
                                                    "session": "sprint" if kind == "sprint" else "race"})
    if body.driver_id not in {e["driver_id"] for e in info.get("entries", [])}:
        raise HTTPException(400, f"{body.driver_id} is not entered in this session.")
    if body.driver_id == clock["driver_id"]:
        return await state()
    # the old driver's open moment is closed, so it never pops back up
    pending = sim.pending_decision(clock)
    if pending:
        user_db.update_decision(pending["id"], user_call="WATCHED", status="decided" if pending["status"] != "thinking"
                                else "thinking")
    user_db.clear_moments_from(clock["race_id"], body.driver_id, kind, clock["current_lap"], clock.get("session_time_utc"))
    clock = user_db.set_clock(driver_id=body.driver_id)
    started = (clock["current_lap"] > 0 if kind != "qualifying"
               else clock.get("session_time_utc") != clock.get("session_start_utc"))
    if not started:
        focus.ensure_preview(clock)
    return await state()


@app.get("/api/sim/driver")
async def driver(code: str | None = None) -> dict:
    """The driver-focus panel: the followed driver by default, or any car by code. Current lap only."""
    return await focus.driver_focus(code.strip().upper() if code else None)


@app.get("/api/sim/preview")
async def preview() -> dict:
    """Before the start: the grid, qualifying, form and championship, plus the AI's pre-session read."""
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        raise HTTPException(400, "Load a session first.")
    return await focus.preview(clock)


@app.post("/api/sim/preview/retry")
async def preview_retry() -> dict:
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        raise HTTPException(400, "Load a session first.")
    focus.ensure_preview(clock, force=True)
    return await focus.preview(clock)


@app.post("/api/sim/start")
async def start() -> dict:
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        raise HTTPException(400, "Pick a race and driver first.")
    if sim.pending_decision(clock):
        raise HTTPException(409, "Make the pending call first.")
    if _over(clock):
        raise HTTPException(409, "The session is over.")
    user_db.set_clock(running=1)
    return await state()


@app.post("/api/sim/pause")
async def pause() -> dict:
    user_db.set_clock(running=0)
    return await state()


@app.post("/api/sim/speed")
async def speed(body: SpeedBody) -> dict:
    if body.seconds_per_lap is not None:
        user_db.set_clock(seconds_per_lap=body.seconds_per_lap)
    if body.quali_speed is not None:
        user_db.set_clock(quali_speed=body.quali_speed)
    return await state()


def _over(clock: dict) -> bool:
    if clock["session_kind"] == "qualifying":
        return sim_quali.over(clock)
    return clock["current_lap"] >= clock["total_laps"]


@app.post("/api/sim/ask")
async def ask_now() -> dict:
    """Ask for a strategy call right now, outside the automatic triggers."""
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        raise HTTPException(400, "Load a session first.")
    if _over(clock):
        raise HTTPException(409, "The session is over.")
    if sim.pending_decision(clock):
        raise HTTPException(409, "A decision is already waiting.")
    if clock["session_kind"] == "qualifying":
        sim_quali.ask(clock, "You asked what's going on")
        return await state()
    if clock["current_lap"] < 1:
        raise HTTPException(400, "The race has to be under way.")
    sim.ask(clock, "You asked what's going on")
    return await state()


@app.post("/api/sim/continue")
async def continue_watching() -> dict:
    """Carry on watching after a key moment's explanation."""
    try:
        sim.continue_watching()
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return await state()


@app.post("/api/sim/call")
async def call(body: CallBody) -> dict:
    try:
        sim.make_call(body.call, body.tire)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return await state()


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.post("/api/ask")
async def ask_ai(body: AskBody) -> dict:
    """Ask AI: the Historian answers live-race, season and rules questions."""
    async with PitwallTeam() as team:
        answer = await team.ask(body.question)
    return {"question": body.question, "lap": user_db.get_clock()["current_lap"], **answer.model_dump()}


@app.post("/api/engineer/chat")
async def chat(body: ChatBody) -> dict:
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        raise HTTPException(400, "Pick a race and driver first.")
    async with PitwallTeam() as team:
        result = await team.chat(f"(Race is at the end of lap {clock['current_lap']}.) {body.message}")
    return {"message": result.message,
            "strategy": result.strategy.model_dump() if result.strategy else None, "usage": result.usage}


@app.get("/api/sim/state")
async def state() -> dict:
    clock = user_db.get_clock()
    if clock["race_id"] is None:
        return {"clock": clock, "mode": clock["session_kind"], "race": None, "quali": None, "race_control": None,
                "decisions": [],
                "pending": None, "over": False, "scorecard": None}
    kind = clock["session_kind"]
    live = await mcp_client.call("get_quali_state" if kind == "qualifying" else "get_race_state", {})
    decisions = user_db.decisions_for(clock["race_id"], clock["driver_id"], kind)
    rc = await mcp_client.call("get_race_control", {"last_laps": 2, "last_minutes": 4})
    return {"clock": clock, "mode": kind, "race_control": None if rc.get("refused") else rc,
            "race": live if kind != "qualifying" else None, "quali": live if kind == "qualifying" else None,
            "decisions": decisions, "pending": sim.pending_decision(clock), "over": _over(clock),
            "scorecard": user_db.get_scorecard(clock["race_id"], clock["driver_id"], kind)}


# --------------------------------------------------------------------------
# Season reference and Learn: the Races, Standings and Learn pages
# --------------------------------------------------------------------------


@app.get("/api/season/calendar")
async def season_calendar() -> dict:
    return await mcp_client.call("get_season_calendar", {})


@app.get("/api/season/standings")
async def season_standings(after_round: int | None = None) -> dict:
    return await mcp_client.call("get_season_standings", {} if after_round is None else {"after_round": after_round})


@app.get("/api/season/podium/{race_id}")
async def season_podium(race_id: int) -> dict:
    """A weekend's podiums. The app only asks after the user taps "reveal result"."""
    return await mcp_client.call("get_race_podium", {"race_id": race_id})


@app.get("/api/learn")
async def learn() -> dict:
    """Every entry in the rules and history file, with its text (the Learn page)."""
    entries = []
    for e in await mcp_client.call("list_rules", {}):
        r = await mcp_client.call("get_rule", {"rule_id": e["id"]})
        if not r.get("refused"):
            entries.append(r)
    return {"entries": entries}


@app.post("/api/sim/grade")
async def grade() -> dict:
    """Grade the race again (e.g. after a grader error). Only after the flag."""
    clock = user_db.get_clock()
    if clock["race_id"] is None or not _over(clock):
        raise HTTPException(409, "The session has to be finished first.")
    sim.start_grading(clock)
    return await state()


# --------------------------------------------------------------------------
# The built frontend (production: one service serves the app and the API)
# --------------------------------------------------------------------------

DIST = BACKEND_DIR.parent / "frontend" / "dist"

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "No such API route.")
        file = (DIST / path).resolve()
        if path and file.is_file() and DIST in file.parents:
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
