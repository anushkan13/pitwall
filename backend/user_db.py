"""The user database: data/pitwall_user.db.

Everything the app generates lives here, never in the read-only races.db:

    sim_clock   one row: which race, whose car, which lap. The backend is the
                only writer. The MCP server reads it on every tool call, which
                is how the no-future rule knows where "now" is.
    decisions   each strategy decision point: the Strategy agent's call, the
                user's call, and (once the lap is past) what really happened.
    scorecards  the end-of-race grade for a race + driver.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

USER_DB = Path(__file__).resolve().parent.parent / "data" / "pitwall_user.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_clock (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    race_id         INTEGER,
    driver_id       TEXT,
    current_lap     INTEGER NOT NULL DEFAULT 0,   -- laps completed; 0 = on the grid
    total_laps      INTEGER NOT NULL DEFAULT 0,
    running         INTEGER NOT NULL DEFAULT 0,
    seconds_per_lap REAL NOT NULL DEFAULT 2.0,
    updated_at      TEXT
);
INSERT OR IGNORE INTO sim_clock (id) VALUES (1);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY,
    race_id         INTEGER NOT NULL,
    driver_id       TEXT NOT NULL,
    lap             INTEGER NOT NULL,           -- laps completed when asked; the call is for lap + 1
    trigger         TEXT NOT NULL,
    status          TEXT NOT NULL,              -- thinking / ready / decided / error
    strategy_call   TEXT,                       -- BOX / STAY_OUT
    strategy_reason TEXT,
    evidence_json   TEXT,
    engineer_message TEXT,
    error           TEXT,
    user_call       TEXT,                       -- BOX / STAY_OUT
    actual_pitted   INTEGER,                    -- did the real driver box at the end of lap + 1? NULL until known
    created_at      TEXT NOT NULL,
    decided_at      TEXT
);

CREATE TABLE IF NOT EXISTS scorecards (
    race_id         INTEGER NOT NULL,
    driver_id       TEXT NOT NULL,
    status          TEXT NOT NULL,              -- grading / ready / error
    stats_json      TEXT,
    verdict_json    TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    session_kind    TEXT NOT NULL DEFAULT 'race',
    PRIMARY KEY (race_id, driver_id, session_kind)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added for qualifying mode. Added in place so existing rows read as race mode.
ADDED_COLUMNS = {
    "sim_clock": {
        "session_kind": "TEXT NOT NULL DEFAULT 'race'",   # race / qualifying
        "session_time_utc": "TEXT",                         # qualifying: "now" in session time
        "session_start_utc": "TEXT",
        "session_end_utc": "TEXT",
        "quali_speed": "REAL NOT NULL DEFAULT 40",          # qualifying: session seconds per real second
    },
    "decisions": {
        "session_kind": "TEXT NOT NULL DEFAULT 'race'",
        "at_time_utc": "TEXT",                              # qualifying: session time of the decision
        "user_tire": "TEXT",
        "actual_json": "TEXT",                              # qualifying: what the real team did
    },
    "scorecards": {"session_kind": "TEXT NOT NULL DEFAULT 'race'"},
}


def connect() -> sqlite3.Connection:
    USER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USER_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")  # the MCP server reads while we write
    # scorecards from before qualifying mode were keyed (race, driver) only; re-key them
    pk = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(scorecards)")}
    if pk and not pk.get("session_kind"):
        conn.execute("ALTER TABLE scorecards RENAME TO scorecards_old")
        conn.executescript(SCHEMA)
        conn.execute("""INSERT INTO scorecards (race_id, driver_id, status, stats_json, verdict_json, error, created_at)
                        SELECT race_id, driver_id, status, stats_json, verdict_json, error, created_at
                        FROM scorecards_old""")
        conn.execute("DROP TABLE scorecards_old")
    conn.executescript(SCHEMA)
    for table, cols in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    return conn


def get_clock() -> dict:
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM sim_clock WHERE id = 1").fetchone())


def set_clock(**fields: Any) -> dict:
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE sim_clock SET {cols} WHERE id = 1", tuple(fields.values()))
    return get_clock()


def new_decision(race_id: int, driver_id: str, lap: int, trigger: str,
                 session_kind: str = "race", at_time_utc: str | None = None) -> int:
    """lap: laps completed (race) or seconds since the session started (qualifying)."""
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO decisions (race_id, driver_id, lap, trigger, status, created_at, session_kind, at_time_utc)
               VALUES (?, ?, ?, ?, 'thinking', ?, ?, ?)""",
            (race_id, driver_id, lap, trigger, _now(), session_kind, at_time_utc))
        return cur.lastrowid


def update_decision(decision_id: int, **fields: Any) -> None:
    if "evidence" in fields:
        fields["evidence_json"] = json.dumps(fields.pop("evidence"))
    if "actual" in fields:
        fields["actual_json"] = json.dumps(fields.pop("actual"))
    if fields.get("user_call"):
        fields["decided_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE decisions SET {cols} WHERE id = ?", (*fields.values(), decision_id))


def decisions_for(race_id: int, driver_id: str, session_kind: str = "race") -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE race_id = ? AND driver_id = ? AND session_kind = ? ORDER BY id",
            (race_id, driver_id, session_kind)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["evidence"] = json.loads(d.pop("evidence_json")) if d.get("evidence_json") else None
        d["actual"] = json.loads(d.pop("actual_json")) if d.get("actual_json") else None
        out.append(d)
    return out


def clear_decisions(race_id: int, driver_id: str, session_kind: str = "race") -> None:
    with connect() as conn:
        conn.execute("DELETE FROM decisions WHERE race_id = ? AND driver_id = ? AND session_kind = ?",
                     (race_id, driver_id, session_kind))
        conn.execute("DELETE FROM scorecards WHERE race_id = ? AND driver_id = ? AND session_kind = ?",
                     (race_id, driver_id, session_kind))


def save_scorecard(race_id: int, driver_id: str, status: str, stats: dict | None = None,
                   verdict: dict | None = None, error: str | None = None, session_kind: str = "race") -> None:
    with connect() as conn:
        conn.execute("DELETE FROM scorecards WHERE race_id = ? AND driver_id = ? AND session_kind = ?",
                     (race_id, driver_id, session_kind))
        conn.execute(
            """INSERT INTO scorecards (race_id, driver_id, status, stats_json, verdict_json, error, created_at,
                                       session_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (race_id, driver_id, status, json.dumps(stats) if stats else None,
             json.dumps(verdict) if verdict else None, error, _now(), session_kind))


def get_scorecard(race_id: int, driver_id: str, session_kind: str = "race") -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM scorecards WHERE race_id = ? AND driver_id = ? AND session_kind = ?",
                           (race_id, driver_id, session_kind)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["stats"] = json.loads(d.pop("stats_json")) if d.get("stats_json") else None
    d["verdict"] = json.loads(d.pop("verdict_json")) if d.get("verdict_json") else None
    return d
