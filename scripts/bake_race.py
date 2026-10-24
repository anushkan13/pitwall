"""Bake Grand Prix weekends into the read-only race database (data/races.db).

    .venv/bin/python scripts/bake_race.py 2025 12     # one round (British GP)
    .venv/bin/python scripts/bake_race.py 2025        # the whole season

For each round it bakes the race, the qualifying session, and the sprint when
that weekend had one, plus the weather feed for each. Lap-level data only, no
car telemetry. Where each piece comes from:

  * FastF1     event schedule: name, country, date, and whether it is a
               sprint weekend.
  * Jolpica    race / sprint / qualifying results, starting grid, race laps
               (position + lap time), race pit stops, and the driver and
               constructor standings going into the weekend.
  * OpenF1     sprint and qualifying laps (Jolpica has none), lap start
               timestamps for every session, tire compound and age from
               stints, sprint pit stops, stationary pit time, and weather.

FastF1's own lap timing comes from F1's live-timing archive, which answers 401
without an F1 account login, and FastF1's public mirror has no 2025 data.

Gap to the car ahead is derived, not fetched: the time this car crossed the
line to complete lap N minus the time the car one position ahead completed the
same lap N. For the race that comes from Jolpica's cumulative lap times; for
the sprint, from OpenF1 lap timestamps. Qualifying has no positions or gaps.

Two phases, so a run never leaves a half-written season:

  1. Fetch. Every request goes through an on-disk cache (.cache/http/). On a
     rate limit (HTTP 429) or a server hiccup the run pauses, backs off, and
     retries; anything already fetched is never fetched again. If it still
     cannot finish, it stops before touching the database, and re-running
     picks up from the cache.
  2. Write. All requested rounds go into data/races.db in one transaction,
     replacing those rounds and leaving other rounds alone. The file is
     chmod'ed read-only between bakes. Anything users create later (accounts,
     scorecards) belongs in a separate database, never here.

Then every session is read back and self-checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import stat
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fastf1
import requests
from dotenv import dotenv_values

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "races.db"
CACHE_DIR = PROJECT_DIR / ".cache"
HTTP_CACHE = CACHE_DIR / "http"

JOLPICA = "https://api.jolpi.ca/ergast/f1"
OPENF1 = "https://api.openf1.org/v1"

# Seconds between uncached requests, per host. Jolpica allows 4 req/s burst and
# 500 req/hour; OpenF1's free tier is a few requests per second.
MIN_INTERVAL = {JOLPICA: 0.6, OPENF1: 0.4}
# How long one run is willing to sit paused on rate limits before giving up.
MAX_TOTAL_WAIT_S = 90 * 60

# session_id = race_id * 10 + this
SESSION_CODE = {"race": 1, "qualifying": 2, "sprint": 3}
OPENF1_NAME = {"race": "Race", "qualifying": "Qualifying", "sprint": "Sprint"}

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id      TEXT PRIMARY KEY,         -- Jolpica constructorId, e.g. 'mclaren'
    name         TEXT NOT NULL,
    nationality  TEXT
);

CREATE TABLE IF NOT EXISTS drivers (
    driver_id    TEXT PRIMARY KEY,         -- Jolpica driverId, e.g. 'norris'
    code         TEXT,                     -- three-letter code, e.g. 'NOR'
    first_name   TEXT NOT NULL,
    last_name    TEXT NOT NULL,
    nationality  TEXT,
    date_of_birth TEXT
);

-- One row per Grand Prix weekend.
CREATE TABLE IF NOT EXISTS races (
    race_id      INTEGER PRIMARY KEY,      -- season * 100 + round, e.g. 202512
    season       INTEGER NOT NULL,
    round        INTEGER NOT NULL,
    name         TEXT NOT NULL,
    circuit_id   TEXT,
    circuit_name TEXT,
    locality     TEXT,
    country      TEXT,
    date         TEXT NOT NULL,            -- race day, YYYY-MM-DD
    has_sprint   INTEGER NOT NULL,
    baked_at     TEXT NOT NULL,
    UNIQUE (season, round)
);

-- One row per baked session: race, qualifying, sprint.
CREATE TABLE IF NOT EXISTS sessions (
    session_id   INTEGER PRIMARY KEY,      -- race_id * 10 + (1 race, 2 qualifying, 3 sprint)
    race_id      INTEGER NOT NULL REFERENCES races(race_id),
    kind         TEXT NOT NULL CHECK (kind IN ('race', 'qualifying', 'sprint')),
    start_utc    TEXT,                     -- scheduled start, ISO 8601 UTC
    total_laps   INTEGER,                  -- laps completed by the winner; NULL for qualifying
    openf1_session_key INTEGER,
    lap_source   TEXT                      -- 'jolpica' (race), 'openf1', 'fastf1' (fallback), NULL if none
);

-- Race and sprint classification: grid, finish, status, points.
CREATE TABLE IF NOT EXISTS results (
    session_id   INTEGER NOT NULL REFERENCES sessions(session_id),
    driver_id    TEXT NOT NULL REFERENCES drivers(driver_id),
    team_id      TEXT NOT NULL REFERENCES teams(team_id),
    car_number   INTEGER,                  -- the number raced that weekend
    grid         INTEGER,                  -- 0 = pit lane start
    position_order INTEGER NOT NULL,       -- 1..N, every entrant, classified or not
    finish_position INTEGER,               -- NULL when not classified
    position_text TEXT,                    -- '1'.. or 'R' retired, 'W' withdrawn, 'D' disqualified
    status       TEXT,                     -- 'Finished', 'Lapped', 'Retired', ...
    laps_completed INTEGER,
    race_time_ms INTEGER,
    points       REAL NOT NULL DEFAULT 0,
    fastest_lap_rank INTEGER,
    PRIMARY KEY (session_id, driver_id)
);

CREATE TABLE IF NOT EXISTS qualifying_results (
    session_id   INTEGER NOT NULL REFERENCES sessions(session_id),
    driver_id    TEXT NOT NULL REFERENCES drivers(driver_id),
    team_id      TEXT NOT NULL REFERENCES teams(team_id),
    car_number   INTEGER,
    position     INTEGER NOT NULL,
    q1_ms        INTEGER,
    q2_ms        INTEGER,
    q3_ms        INTEGER,
    PRIMARY KEY (session_id, driver_id)
);

CREATE TABLE IF NOT EXISTS laps (
    session_id   INTEGER NOT NULL REFERENCES sessions(session_id),
    driver_id    TEXT NOT NULL REFERENCES drivers(driver_id),
    lap          INTEGER NOT NULL,
    lap_start_utc TEXT,                    -- ISO 8601 UTC; join to weather on this
    position     INTEGER,                  -- track position at the end of the lap; NULL in qualifying
    lap_time_ms  INTEGER,
    race_time_ms INTEGER,                  -- cumulative, end of this lap; NULL in qualifying
    gap_ahead_ms INTEGER,                  -- to the car one position ahead, same lap; NULL for P1
    gap_leader_ms INTEGER,                 -- to P1, same lap
    compound     TEXT,                     -- SOFT / MEDIUM / HARD / INTERMEDIATE / WET, NULL if unknown
    tire_age     INTEGER,                  -- laps on this set at the end of this lap
    stint        INTEGER,
    pit_in       INTEGER NOT NULL DEFAULT 0,  -- 1 if the car entered the pit lane at the end of this lap
    pit_out      INTEGER NOT NULL DEFAULT 0,  -- 1 if this lap started in the pit lane
    PRIMARY KEY (session_id, driver_id, lap)
);

CREATE TABLE IF NOT EXISTS pitstops (
    session_id   INTEGER NOT NULL REFERENCES sessions(session_id),
    driver_id    TEXT NOT NULL REFERENCES drivers(driver_id),
    stop         INTEGER NOT NULL,
    lap          INTEGER NOT NULL,
    time_utc     TEXT,                     -- ISO 8601 UTC, NULL if OpenF1 has no match
    pit_lane_ms  INTEGER,                  -- pit entry to pit exit
    stationary_ms INTEGER,                 -- car stopped in the box, NULL if unknown
    pit_lane_pass INTEGER NOT NULL DEFAULT 0,  -- 1 if the car drove through without a tire change
                                           -- (e.g. the field led through the pit lane under a safety car)
    PRIMARY KEY (session_id, driver_id, stop)
);

-- OpenF1 weather feed, roughly one reading a minute through each session.
CREATE TABLE IF NOT EXISTS weather (
    session_id   INTEGER NOT NULL REFERENCES sessions(session_id),
    time_utc     TEXT NOT NULL,            -- ISO 8601 UTC
    air_temp_c   REAL,
    track_temp_c REAL,
    rainfall     INTEGER,                  -- 1 while rain is detected, else 0
    humidity_pct REAL,
    pressure_mbar REAL,
    wind_speed_ms REAL,
    wind_direction_deg INTEGER,
    PRIMARY KEY (session_id, time_utc)
);

-- Championship standings going INTO this weekend (after the previous round,
-- so before this weekend's sprint). Empty for round 1.
CREATE TABLE IF NOT EXISTS driver_standings_before (
    race_id      INTEGER NOT NULL REFERENCES races(race_id),
    driver_id    TEXT NOT NULL REFERENCES drivers(driver_id),
    team_id      TEXT REFERENCES teams(team_id),
    position     INTEGER,
    points       REAL NOT NULL,
    wins         INTEGER NOT NULL,
    PRIMARY KEY (race_id, driver_id)
);

CREATE TABLE IF NOT EXISTS constructor_standings_before (
    race_id      INTEGER NOT NULL REFERENCES races(race_id),
    team_id      TEXT NOT NULL REFERENCES teams(team_id),
    position     INTEGER,
    points       REAL NOT NULL,
    wins         INTEGER NOT NULL,
    PRIMARY KEY (race_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_race ON sessions (race_id);
CREATE INDEX IF NOT EXISTS idx_laps_session_lap ON laps (session_id, lap, position);
CREATE INDEX IF NOT EXISTS idx_pitstops_session_lap ON pitstops (session_id, lap);

-- Each lap with the latest weather reading taken at or before its start.
-- This is how a scenario tells a wet lap from a drying one.
CREATE VIEW IF NOT EXISTS lap_weather AS
SELECT l.*, w.time_utc AS weather_utc, w.air_temp_c, w.track_temp_c, w.rainfall, w.humidity_pct
FROM laps l
LEFT JOIN weather w
  ON w.session_id = l.session_id
 AND w.time_utc = (SELECT MAX(time_utc) FROM weather
                   WHERE session_id = l.session_id AND time_utc <= l.lap_start_utc);
"""

ALL_TABLES = (
    "lap_weather", "weather", "laps", "pitstops", "results", "qualifying_results",
    "driver_standings_before", "constructor_standings_before", "sessions", "races",
    "drivers", "teams",
)


# --------------------------------------------------------------------------
# HTTP: cached, throttled, and patient with rate limits
# --------------------------------------------------------------------------


class Fetcher:
    def __init__(self) -> None:
        self.last_call: dict[str, float] = {}
        self.waited = 0.0
        self.network_calls = 0
        self.cache_hits = 0

    def get(self, url: str, params: dict | None = None):
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        path = HTTP_CACHE / (hashlib.sha1(key.encode()).hexdigest() + ".json")
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text())

        host = JOLPICA if url.startswith(JOLPICA) else OPENF1
        attempt = 0
        while True:
            gap = time.monotonic() - self.last_call.get(host, 0)
            if gap < MIN_INTERVAL[host]:
                time.sleep(MIN_INTERVAL[host] - gap)
            self.last_call[host] = time.monotonic()

            try:
                resp = requests.get(url, params=params, timeout=30)
                status = resp.status_code
            except requests.RequestException as exc:
                resp, status = None, f"network error ({exc.__class__.__name__})"

            if resp is not None and status == 200:
                data = resp.json()
                break
            if resp is not None and status == 404 and host == OPENF1:
                data = []  # OpenF1 answers 404 for "no rows"
                break
            if resp is not None and status not in (429, 500, 502, 503, 504):
                resp.raise_for_status()

            attempt += 1
            retry_after = resp.headers.get("Retry-After") if resp is not None else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else min(5 * 2 ** (attempt - 1), 300)
            if self.waited + wait > MAX_TOTAL_WAIT_S:
                raise RuntimeError(
                    f"Still blocked after pausing {self.waited / 60:.0f} min ({status} from {url}). "
                    "Nothing was written. Re-run later; everything fetched so far is cached."
                )
            print(f"    paused {wait:.0f}s: {status} from {host.split('//')[1].split('/')[0]} "
                  f"(attempt {attempt}, cache keeps progress)", flush=True)
            time.sleep(wait)
            self.waited += wait

        self.network_calls += 1
        HTTP_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def jolpica(self, path: str, table: str = "RaceTable") -> list:
        """Every page of a Jolpica endpoint: the list of Races / StandingsLists."""
        out: list = []
        offset, limit = 0, 100
        while True:
            mr = self.get(f"{JOLPICA}/{path}.json", {"limit": limit, "offset": offset})["MRData"]
            container = mr[table]
            out += container.get("Races") or container.get("StandingsLists") or []
            offset += limit
            if offset >= int(mr["total"]):
                return out


# --------------------------------------------------------------------------
# Small parsers
# --------------------------------------------------------------------------


def ms(text: str | None) -> int | None:
    """'1:45.820' or '28.721' -> milliseconds."""
    if not text:
        return None
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return round(seconds * 1000)


def sec_ms(seconds) -> int | None:
    return round(seconds * 1000) if seconds is not None else None


def to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_ts(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text).astimezone(timezone.utc) if text else None


def iso(dt: datetime | None) -> str | None:
    """One fixed format, so timestamps compare correctly as strings in SQL."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if dt else None


# --------------------------------------------------------------------------
# Fetch: everything for one round, into a plain dict
# --------------------------------------------------------------------------


def load_schedule(season: int):
    warnings.filterwarnings("ignore", category=FutureWarning, module="fastf1")
    fastf1.set_log_level("ERROR")
    CACHE_DIR.joinpath("fastf1").mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(CACHE_DIR / "fastf1"))
    return fastf1.get_event_schedule(season, include_testing=False)


def fetch_round(f: Fetcher, season: int, rnd: int, event, openf1_sessions: list) -> dict:
    has_sprint = "sprint" in str(event["EventFormat"])

    def first_race(path: str) -> dict | None:
        pages = f.jolpica(f"{season}/{rnd}/{path}")
        if not pages:
            return None
        merged = dict(pages[0])
        for list_key in ("Results", "SprintResults", "QualifyingResults"):
            if list_key in merged:
                merged[list_key] = [x for p in pages for x in p.get(list_key, [])]
        return merged

    race = first_race("results")
    if race is None:
        raise RuntimeError(f"No race results on Jolpica for {season} round {rnd}.")

    rd = {
        "season": season,
        "round": rnd,
        "event": {"name": str(event["EventName"]), "country": str(event["Country"])},
        "has_sprint": has_sprint,
        "race": race,
        "qualifying": first_race("qualifying"),
        "sprint": first_race("sprint") if has_sprint else None,
        "race_laps": [
            {"driver_id": t["driverId"], "lap": int(lap["number"]),
             "position": to_int(t.get("position")), "lap_time_ms": ms(t.get("time"))}
            for page in f.jolpica(f"{season}/{rnd}/laps")
            for lap in page.get("Laps", []) for t in lap["Timings"]
        ],
        "race_pitstops": [p for page in f.jolpica(f"{season}/{rnd}/pitstops")
                          for p in page.get("PitStops", [])],
        "driver_standings": [], "constructor_standings": [],
        "openf1": {},
    }
    if rnd > 1:
        rd["driver_standings"] = [s for lst in f.jolpica(f"{season}/{rnd - 1}/driverStandings", "StandingsTable")
                                  for s in lst["DriverStandings"]]
        rd["constructor_standings"] = [s for lst in f.jolpica(f"{season}/{rnd - 1}/constructorStandings", "StandingsTable")
                                       for s in lst["ConstructorStandings"]]

    # OpenF1: find this weekend's meeting through its race session date.
    race_sessions = [s for s in openf1_sessions
                     if s["session_name"] == "Race" and s["date_start"][:10] == race["date"]]
    meeting = race_sessions[0]["meeting_key"] if race_sessions else None
    for kind in ("race", "qualifying", "sprint"):
        if kind == "sprint" and not has_sprint:
            continue
        match = [s for s in openf1_sessions
                 if s["meeting_key"] == meeting and s["session_name"] == OPENF1_NAME[kind]]
        if not match:
            rd["openf1"][kind] = None
            continue
        key = match[0]["session_key"]
        rd["openf1"][kind] = {
            "session_key": key,
            "date_start": match[0]["date_start"],
            "laps": f.get(f"{OPENF1}/laps", {"session_key": key}),
            "stints": f.get(f"{OPENF1}/stints", {"session_key": key}),
            "pit": f.get(f"{OPENF1}/pit", {"session_key": key}) if kind != "qualifying" else [],
            "race_control": f.get(f"{OPENF1}/race_control", {"session_key": key}) if kind != "qualifying" else [],
            "weather": f.get(f"{OPENF1}/weather", {"session_key": key}),
            "lap_source": "openf1",
        }
        if kind == "qualifying" and not rd["openf1"][kind]["laps"]:
            laps, note = fastf1_qualifying_laps(season, rnd)
            print(f"    OpenF1 has no qualifying laps; FastF1 fallback: {note}", flush=True)
            if laps:
                rd["openf1"][kind]["laps"] = laps
                rd["openf1"][kind]["lap_source"] = "fastf1"
    return rd


# --------------------------------------------------------------------------
# FastF1 fallback for a qualifying session OpenF1 has no laps for
#
# FastF1 reads F1's live-timing archive, which needs an F1 account login
# (FastF1 3.9 requires one with an active F1TV subscription). The login is
# optional: it is only read from F1_USERNAME / F1_PASSWORD in .env (the
# project's, else the one level up) and only used here, at bake time. Nothing
# in the app reads it. Once a session has been pulled, the normalized laps are
# kept in .cache/fallback/, so later bakes don't need the login again.
# --------------------------------------------------------------------------

FALLBACK_CACHE = CACHE_DIR / "fallback"


def f1_credentials() -> tuple[str, str] | None:
    for env in (PROJECT_DIR / ".env", PROJECT_DIR.parent / ".env"):
        if env.exists():
            values = dotenv_values(env)
            if values.get("F1_USERNAME") and values.get("F1_PASSWORD"):
                return values["F1_USERNAME"], values["F1_PASSWORD"]
    return None


def normalize_fastf1_laps(laps, t0_date) -> list[dict]:
    """FastF1 Laps -> OpenF1-style /laps rows (driver_number, lap_number,
    date_start, lap_duration, is_pit_out_lap), so the rest of the bake treats
    them like any other qualifying session."""
    t0 = t0_date.tz_localize("UTC") if t0_date.tzinfo is None else t0_date
    rows = []
    for _, lap in laps.iterrows():
        if lap.isna()["LapNumber"] or lap.isna()["DriverNumber"]:
            continue
        start, dur = lap["LapStartTime"], lap["LapTime"]
        rows.append({
            "driver_number": int(lap["DriverNumber"]),
            "lap_number": int(lap["LapNumber"]),
            "date_start": (t0 + start).isoformat() if start == start else None,  # NaT != NaT
            "lap_duration": dur.total_seconds() if dur == dur else None,
            "is_pit_out_lap": bool(lap["PitOutTime"] == lap["PitOutTime"]),
        })
    return rows


def fastf1_qualifying_laps(season: int, rnd: int) -> tuple[list | None, str]:
    """Qualifying laps from FastF1, shaped like OpenF1's /laps rows."""
    cached = FALLBACK_CACHE / f"{season}_{rnd:02d}_qualifying_laps.json"
    if cached.exists():
        return json.loads(cached.read_text()), "used laps cached from an earlier FastF1 pull"

    creds = f1_credentials()
    if not creds:
        return None, "skipped, no F1_USERNAME / F1_PASSWORD in .env"

    import os
    saved = {k: os.environ.get(k) for k in ("F1_USERNAME", "F1_PASSWORD")}
    os.environ["F1_USERNAME"], os.environ["F1_PASSWORD"] = creds
    try:
        session = fastf1.get_session(season, rnd, "Q")
        session.load(laps=True, telemetry=False, weather=False, messages=False)
        laps = session.laps
    except Exception as exc:  # login refused, no data, network: the gap just stays
        return None, f"failed ({exc.__class__.__name__}: {str(exc).splitlines()[0][:120]})"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    rows = normalize_fastf1_laps(laps, session.t0_date)
    if not rows:
        return None, "FastF1 loaded the session but it has no laps either"
    FALLBACK_CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(rows))
    return rows, f"pulled {len(rows)} laps"


# --------------------------------------------------------------------------
# Transform
# --------------------------------------------------------------------------


def tire_lookup(stints: list) -> dict[tuple[int, int], tuple]:
    """(car number, lap) -> (compound, tire age at end of lap, stint number)."""
    tire = {}
    for s in stints:
        start, end = s.get("lap_start"), s.get("lap_end")
        if start is None or end is None:
            continue
        age0 = s.get("tyre_age_at_start") or 0
        compound = s.get("compound") if s.get("compound") not in (None, "UNKNOWN") else None
        for lap in range(start, end + 1):
            tire[(s["driver_number"], lap)] = (compound, age0 + (lap - start) + 1, s.get("stint_number"))
    return tire


# When stops and tire changes don't pair up one-to-one, a stop is matched
# to a tire change within this many laps.
STINT_MATCH_WINDOW = 6


def align_tires(stints: list, stops: list[tuple[int, int]],
                known_passes: set[tuple[int, int]] = frozenset()) -> tuple[dict, set]:
    """Tire per lap, with stint boundaries moved onto the recorded pit stops.

    OpenF1's stint boundaries sit 0 or 1 lap after the recorded stop depending
    on where the pit box is relative to the timing line, and in a few sessions
    they are many laps off. The stop laps are the reliable
    part, so each tire change is matched to the nearest recorded stop and the
    boundary moved there, using the session's usual offset.

    A recorded stop with no tire change to match is a pit-lane pass: the car
    went down the pit lane without a new set, as when the safety car leads the
    field through it. `known_passes` are stops already known to be passes (from
    race control); any same-compound stint OpenF1 starts there is folded back in.

    Returns ({(car number, lap): (compound, tire age, stint)}, {(car number, lap) passes}).
    """
    by_car = defaultdict(list)
    for st in stints:
        if st.get("lap_start") is not None and st.get("lap_end") is not None:
            by_car[st["driver_number"]].append(st)
    stops_by_car = defaultdict(list)
    for num, lap in stops:
        if (num, lap) not in known_passes:
            stops_by_car[num].append(lap)
    pass_laps = defaultdict(set)
    for num, lap in known_passes:
        pass_laps[num] |= {lap, lap + 1}

    # OpenF1 opens a new "stint" on every trip down the pit lane, even with no
    # tire change. Same compound with the tire age carrying straight on means
    # the same set, so fold it into the previous stint.
    for num, sts in by_car.items():
        sts.sort(key=lambda x: x.get("stint_number") or 0)
        merged = []
        for st in sts:
            prev = merged[-1] if merged else None
            if prev and st.get("compound") == prev.get("compound") and st.get("tyre_age_at_start") is not None:
                age_at_end = (prev.get("tyre_age_at_start") or 0) + prev["lap_end"] - prev["lap_start"] + 1
                if abs(st["tyre_age_at_start"] - age_at_end) <= 1 or st["lap_start"] in pass_laps[num]:
                    merged[-1] = {**prev, "lap_end": max(prev["lap_end"], st["lap_end"])}
                    continue
            merged.append(dict(st))
        for i, st in enumerate(merged, start=1):
            st["stint_number"] = i
        by_car[num] = merged

    # Match each tire change (stint 2+) to a recorded stop. With exactly one
    # stop per change they pair up in order, however far OpenF1's boundary is
    # off (the 2025 Miami sprint has it 12 laps early). With extra stops, each
    # change takes the nearest stop and the leftovers are pit-lane passes.
    matches: dict[int, dict[int, int]] = {}
    offsets = []
    for num, sts in by_car.items():
        free = sorted(stops_by_car.get(num, []))
        matches[num] = {}
        changes = range(1, len(sts))
        if len(free) == len(changes):
            matches[num] = dict(zip(changes, free))
        else:
            window = None if len(free) > len(changes) else STINT_MATCH_WINDOW
            for i in changes:
                start = sts[i]["lap_start"]
                near = [lap for lap in free if window is None or abs(start - lap) <= window]
                if near:
                    lap = min(near, key=lambda lap: (abs(start - lap - 1), lap))
                    free.remove(lap)
                    matches[num][i] = lap
        offsets += [sts[i]["lap_start"] - lap for i, lap in matches[num].items()]
    # where the new set shows up relative to the stop lap: 0 or 1, by circuit
    offset = 0 if offsets.count(0) > offsets.count(1) else 1

    tire, passes = {}, set(known_passes)
    for num, sts in by_car.items():
        used = set(matches[num].values())
        passes |= {(num, lap) for lap in stops_by_car.get(num, []) if lap not in used}
        starts = [sts[0]["lap_start"]] + [
            matches[num][i] + offset if i in matches[num] else sts[i]["lap_start"]
            for i in range(1, len(sts))
        ]
        for i, st in enumerate(sts):
            end = starts[i + 1] - 1 if i + 1 < len(sts) else max(st["lap_end"], starts[i])
            age0 = st.get("tyre_age_at_start") or 0
            compound = st.get("compound") if st.get("compound") not in (None, "UNKNOWN") else None
            for lap in range(starts[i], end + 1):
                tire[(num, lap)] = (compound, age0 + (lap - starts[i]) + 1, st.get("stint_number"))
    # stops for cars with no stint data stay as stops
    return tire, passes


def field_through_pit_lane(race_control: list) -> set[int]:
    """Laps on which race control sent the whole field down the pit lane.

    From the lap before the 'THROUGH THE PIT LANE' message (the call can come
    after the first cars are already in) until the pit straight is back in use.
    """
    laps: set[int] = set()
    msgs = sorted((m for m in race_control if m.get("lap_number") is not None),
                  key=lambda m: m.get("date") or "")
    for i, m in enumerate(msgs):
        text = (m.get("message") or "").upper()
        if "THROUGH THE PIT LANE" in text and "PENALTY" not in text:
            start = m["lap_number"]
            end = next((n["lap_number"] for n in msgs[i + 1:]
                        if any(k in (n.get("message") or "").upper()
                               for k in ("START/FINISH STRAIGHT", "PIT LANE ENTRY OPEN", "SAFETY CAR IN THIS LAP"))),
                       start + 4)
            laps |= set(range(start - 1, end))
    return laps


def openf1_lap_index(of1_laps: list) -> dict[tuple[int, int], dict]:
    return {(x["driver_number"], x["lap_number"]): x for x in of1_laps}


def race_lap_rows(rd: dict, number_of: dict, of1: dict | None, tire: dict) -> list[tuple]:
    """Race laps from Jolpica, with OpenF1 timestamps and tires attached."""
    lap_rows = rd["race_laps"]
    pit_laps = {(p["driverId"], int(p["lap"])) for p in rd["race_pitstops"]}
    of1_laps = openf1_lap_index(of1["laps"]) if of1 else {}

    cum: dict[tuple[str, int], int | None] = {}
    for r in sorted(lap_rows, key=lambda r: (r["driver_id"], r["lap"])):
        prev = cum.get((r["driver_id"], r["lap"] - 1), 0) if r["lap"] > 1 else 0
        cum[(r["driver_id"], r["lap"])] = (
            prev + r["lap_time_ms"] if prev is not None and r["lap_time_ms"] is not None else None
        )
    by_lap_pos = {(r["lap"], r["position"]): r["driver_id"] for r in lap_rows}

    out = []
    for r in lap_rows:
        d, lap, pos = r["driver_id"], r["lap"], r["position"]
        num = number_of.get(d)
        t = cum[(d, lap)]

        def gap_to(other):
            o = cum.get((other, lap)) if other else None
            return t - o if t is not None and o is not None else None

        ahead = by_lap_pos.get((lap, pos - 1)) if pos and pos > 1 else None
        compound, age, stint = tire.get((num, lap), (None, None, None))
        start = of1_laps.get((num, lap), {}).get("date_start")
        out.append((
            d, lap, iso(parse_ts(start)), pos, r["lap_time_ms"], t,
            gap_to(ahead), gap_to(by_lap_pos.get((lap, 1))),
            compound, age, stint, int((d, lap) in pit_laps), int((d, lap - 1) in pit_laps),
        ))
    return out


def sprint_lap_rows(results: list, number_of: dict, of1: dict, tire: dict) -> list[tuple]:
    """Sprint laps from OpenF1. Positions and gaps from line-crossing times."""
    id_of = {n: d for d, n in number_of.items()}
    laps_done = {r["Driver"]["driverId"]: to_int(r["laps"]) or 0 for r in results}
    pit_laps = {(id_of.get(p["driver_number"]), p["lap_number"]) for p in of1["pit"]}

    by_driver = defaultdict(list)
    for x in of1["laps"]:
        d = id_of.get(x["driver_number"])
        if d and x["lap_number"] <= laps_done.get(d, 0):  # drop a retiree's unfinished lap
            by_driver[d].append(x)

    rows = {}
    for d, laps in by_driver.items():
        laps.sort(key=lambda x: x["lap_number"])
        cum = 0
        for i, x in enumerate(laps):
            start = parse_ts(x["date_start"])
            dur = x.get("lap_duration")
            if start and dur is not None:
                end = start + timedelta(seconds=dur)
            elif i + 1 < len(laps) and laps[i + 1]["date_start"]:
                end = parse_ts(laps[i + 1]["date_start"])
            else:
                end = None
            cum = cum + sec_ms(dur) if cum is not None and dur is not None else None
            rows[(d, x["lap_number"])] = {"start": start, "end": end, "lap_time_ms": sec_ms(dur), "cum": cum}

    # rank each lap by when the cars crossed the line to complete it
    by_lap = defaultdict(list)
    for (d, lap), v in rows.items():
        if v["end"]:
            by_lap[lap].append((v["end"], d))
    pos_gap = {}
    for lap, crossings in by_lap.items():
        crossings.sort()
        leader_t = crossings[0][0]
        for i, (t, d) in enumerate(crossings):
            ahead = None if i == 0 else round((t - crossings[i - 1][0]).total_seconds() * 1000)
            pos_gap[(d, lap)] = (i + 1, ahead, round((t - leader_t).total_seconds() * 1000))

    out = []
    for (d, lap), v in sorted(rows.items()):
        pos, ahead, leader = pos_gap.get((d, lap), (None, None, None))
        compound, age, stint = tire.get((number_of.get(d), lap), (None, None, None))
        out.append((
            d, lap, iso(v["start"]), pos, v["lap_time_ms"], v["cum"], ahead, leader,
            compound, age, stint, int((d, lap) in pit_laps), int((d, lap - 1) in pit_laps),
        ))
    return out


def qualifying_lap_rows(number_of: dict, of1: dict) -> list[tuple]:
    id_of = {n: d for d, n in number_of.items()}
    tire = tire_lookup(of1["stints"])
    out = []
    for x in of1["laps"]:
        d = id_of.get(x["driver_number"])
        if not d:
            continue
        lap = x["lap_number"]
        compound, age, stint = tire.get((x["driver_number"], lap), (None, None, None))
        out.append((
            d, lap, iso(parse_ts(x["date_start"])), None, sec_ms(x.get("lap_duration")), None, None, None,
            compound, age, stint, 0, int(bool(x.get("is_pit_out_lap"))),
        ))
    return out


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------


def make_writable(path: Path) -> None:
    if path.exists():
        path.chmod(path.stat().st_mode | stat.S_IWUSR)


def make_read_only(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def open_for_write(rebuild_ok: bool) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    make_writable(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    has_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
    if not has_tables:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        if not rebuild_ok:
            conn.close()
            make_read_only(DB_PATH)
            raise SystemExit(f"{DB_PATH.name} uses an older schema. Re-bake the whole season "
                             "(omit the round number) to rebuild it.")
        # Older layout (race-only). It is derived data, so rebuild it.
        for name in ALL_TABLES:
            kind = "VIEW" if name == "lap_weather" else "TABLE"
            conn.execute(f"DROP {kind} IF EXISTS {name}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def write_round(conn: sqlite3.Connection, rd: dict) -> None:
    season, rnd = rd["season"], rd["round"]
    race_id = season * 100 + rnd
    sids = [race_id * 10 + c for c in SESSION_CODE.values()]
    marks = ",".join("?" * len(sids))
    for table in ("weather", "laps", "pitstops", "results", "qualifying_results"):
        conn.execute(f"DELETE FROM {table} WHERE session_id IN ({marks})", sids)
    for table in ("sessions", "driver_standings_before", "constructor_standings_before", "races"):
        conn.execute(f"DELETE FROM {table} WHERE race_id = ?", (race_id,))

    race, quali, sprint = rd["race"], rd["qualifying"], rd["sprint"]
    entries = race["Results"] + (sprint or {}).get("SprintResults", []) + (quali or {}).get("QualifyingResults", [])

    for p in [e["Driver"] for e in entries] + [s["Driver"] for s in rd["driver_standings"]]:
        conn.execute("INSERT OR REPLACE INTO drivers VALUES (?,?,?,?,?,?)",
                     (p["driverId"], p.get("code"), p["givenName"], p["familyName"],
                      p.get("nationality"), p.get("dateOfBirth")))
    teams = [e["Constructor"] for e in entries] + [s["Constructor"] for s in rd["constructor_standings"]]
    teams += [s["Constructors"][-1] for s in rd["driver_standings"] if s.get("Constructors")]
    for t in teams:
        conn.execute("INSERT OR REPLACE INTO teams VALUES (?,?,?)",
                     (t["constructorId"], t["name"], t.get("nationality")))

    c = race["Circuit"]
    conn.execute("INSERT INTO races VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (race_id, season, rnd, rd["event"]["name"], c["circuitId"], c["circuitName"],
                  c["Location"]["locality"], rd["event"]["country"], race["date"], int(rd["has_sprint"]),
                  datetime.now(timezone.utc).isoformat(timespec="seconds")))

    for kind, jol in (("race", race), ("qualifying", quali), ("sprint", sprint)):
        if jol is None:
            continue
        sid = race_id * 10 + SESSION_CODE[kind]
        of1 = rd["openf1"].get(kind)
        rows = jol.get({"race": "Results", "sprint": "SprintResults", "qualifying": "QualifyingResults"}[kind], [])
        number_of = {r["Driver"]["driverId"]: to_int(r["number"]) for r in rows}
        winner_laps = next((to_int(r.get("laps")) for r in rows if r.get("position") == "1"), None)
        # Jolpica's qualifying/sprint payloads carry the race's date, so prefer OpenF1's
        start = iso(parse_ts(of1["date_start"])) if of1 else (
            iso(parse_ts(f"{jol['date']}T{jol['time'].replace('Z', '+00:00')}"))
            if kind == "race" and jol.get("time") else None)
        if kind == "race":
            lap_source = "jolpica" if rd["race_laps"] else None
        else:
            lap_source = of1["lap_source"] if of1 and of1["laps"] else None
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                     (sid, race_id, kind, start,
                      None if kind == "qualifying" else winner_laps, (of1 or {}).get("session_key"),
                      lap_source))

        if kind == "qualifying":
            for r in rows:
                conn.execute("INSERT INTO qualifying_results VALUES (?,?,?,?,?,?,?,?)",
                             (sid, r["Driver"]["driverId"], r["Constructor"]["constructorId"],
                              to_int(r["number"]), int(r["position"]),
                              ms(r.get("Q1")), ms(r.get("Q2")), ms(r.get("Q3"))))
        else:
            for r in rows:
                conn.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (sid, r["Driver"]["driverId"], r["Constructor"]["constructorId"],
                              to_int(r["number"]), to_int(r["grid"]), int(r["position"]),
                              to_int(r["positionText"]), r["positionText"], r["status"], to_int(r["laps"]),
                              to_int((r.get("Time") or {}).get("millis")), float(r["points"]),
                              to_int((r.get("FastestLap") or {}).get("rank"))))

        # recorded stops, as (car number, lap), to line the tire stints up against
        if kind == "race":
            stops = [(number_of.get(p["driverId"]), int(p["lap"])) for p in rd["race_pitstops"]]
        else:
            stops = [(p["driver_number"], p["lap_number"]) for p in (of1 or {}).get("pit", [])]
        if of1 and kind != "qualifying":
            stationary = {(p["driver_number"], p["lap_number"]): p.get("stop_duration") for p in of1["pit"]}
            known = {(num, lap) for num, lap in stops
                     if lap in field_through_pit_lane(of1.get("race_control", [])) and not stationary.get((num, lap))}
            tire, passes = align_tires(of1["stints"], stops, known)
        else:
            tire, passes = {}, set()

        if kind == "race":
            lap_rows = race_lap_rows(rd, number_of, of1, tire)
        elif of1 is None:
            lap_rows = []
        elif kind == "sprint":
            lap_rows = sprint_lap_rows(rows, number_of, of1, tire)
        else:
            lap_rows = qualifying_lap_rows(number_of, of1)
        conn.executemany("INSERT INTO laps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         [(sid, *row) for row in lap_rows])

        # pit stops: Jolpica for the race (OpenF1 adds time and stationary), OpenF1 for the sprint
        of1_pit = {(p["driver_number"], p["lap_number"]): p for p in (of1 or {}).get("pit", [])}

        def lane_pass(num, lap) -> int:
            return int((num, lap) in passes)

        if kind == "race":
            for p in rd["race_pitstops"]:
                d, lap = p["driverId"], int(p["lap"])
                m = of1_pit.get((number_of.get(d), lap), {})
                conn.execute("INSERT INTO pitstops VALUES (?,?,?,?,?,?,?,?)",
                             (sid, d, int(p["stop"]), lap, iso(parse_ts(m.get("date"))),
                              ms(p.get("duration")), sec_ms(m.get("stop_duration")),
                              lane_pass(number_of.get(d), lap)))
        elif kind == "sprint" and of1:
            id_of = {n: d for d, n in number_of.items()}
            count = defaultdict(int)
            for p in sorted(of1["pit"], key=lambda p: p["date"]):
                d = id_of.get(p["driver_number"])
                if not d:
                    continue
                count[d] += 1
                conn.execute("INSERT INTO pitstops VALUES (?,?,?,?,?,?,?,?)",
                             (sid, d, count[d], p["lap_number"], iso(parse_ts(p["date"])),
                              sec_ms(p.get("lane_duration") or p.get("pit_duration")),
                              sec_ms(p.get("stop_duration")), lane_pass(p["driver_number"], p["lap_number"])))

        if of1:
            conn.executemany(
                "INSERT OR IGNORE INTO weather VALUES (?,?,?,?,?,?,?,?,?)",
                [(sid, iso(parse_ts(w["date"])), w.get("air_temperature"), w.get("track_temperature"),
                  to_int(w.get("rainfall")), w.get("humidity"), w.get("pressure"),
                  w.get("wind_speed"), to_int(w.get("wind_direction")))
                 for w in of1["weather"] if w.get("date")])

    for s in rd["driver_standings"]:
        team = s["Constructors"][-1]["constructorId"] if s.get("Constructors") else None
        conn.execute("INSERT INTO driver_standings_before VALUES (?,?,?,?,?,?)",
                     (race_id, s["Driver"]["driverId"], team, to_int(s.get("position")),
                      float(s["points"]), int(s["wins"])))
    for s in rd["constructor_standings"]:
        conn.execute("INSERT INTO constructor_standings_before VALUES (?,?,?,?,?)",
                     (race_id, s["Constructor"]["constructorId"], to_int(s.get("position")),
                      float(s["points"]), int(s["wins"])))


# --------------------------------------------------------------------------
# Self-checks, read back from the database (mode=ro)
# --------------------------------------------------------------------------

MIN_TIRE_COVERAGE = 0.90
SHORT = {"race": "Race", "qualifying": "Quali", "sprint": "Sprint"}


def check_session(q, sid: int, kind: str) -> tuple[str, list[str]]:
    """One short status string for the session, plus a list of problems."""
    problems = []
    n_laps, tire_cov = q("SELECT COUNT(*), AVG(compound IS NOT NULL) FROM laps WHERE session_id=?", sid)[0]
    # The feed starts about an hour before the session and runs past the end, so
    # 'WET' means rain while cars were on track; rain only beforehand is shown apart.
    first, last = q("SELECT MIN(lap_start_utc), MAX(lap_start_utc) FROM laps WHERE session_id=?", sid)[0]
    n_wx, wet, pre = q("""SELECT COUNT(*),
            COALESCE(MAX(rainfall * (time_utc BETWEEN ? AND strftime('%Y-%m-%dT%H:%M:%fZ', ?, '+3 minutes'))), 0),
            COALESCE(MAX(rainfall * (time_utc < ?)), 0)
        FROM weather WHERE session_id=?""", first, last, first, sid)[0]
    tire_cov = tire_cov or 0.0

    if kind == "qualifying":
        n_drivers = q("SELECT COUNT(*) FROM qualifying_results WHERE session_id=?", sid)[0][0]
        no_laps = q("""SELECT r.driver_id FROM qualifying_results r WHERE r.session_id=? AND r.q1_ms IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM laps l WHERE l.session_id=r.session_id
                                       AND l.driver_id=r.driver_id)""", sid)
        if no_laps:
            problems.append(f"no laps for {len(no_laps)} of {n_drivers} drivers"
                            if len(no_laps) > 5 else f"no laps for {', '.join(d for d, in no_laps)}")
        missing = q("""SELECT r.driver_id FROM results r WHERE r.session_id = ? - 1
                        AND r.driver_id NOT IN (SELECT driver_id FROM qualifying_results WHERE session_id=?)""",
                    sid, sid)
        if missing:
            problems.append(f"race entrant missing from qualifying results: {', '.join(d for d, in missing)}")
        source, = q("SELECT lap_source FROM sessions WHERE session_id=?", sid)[0]
        head = f"{n_drivers}d {n_laps} laps" + (" (via FastF1)" if source == "fastf1" else "")
    else:
        total, = q("SELECT total_laps FROM sessions WHERE session_id=?", sid)[0]
        n_drivers = q("SELECT COUNT(*) FROM results WHERE session_id=?", sid)[0][0]
        n_stops, n_pass = q("SELECT COUNT(*) - SUM(pit_lane_pass), SUM(pit_lane_pass) FROM pitstops WHERE session_id=?",
                            sid)[0]
        n_stops, n_pass = n_stops or 0, n_pass or 0
        # Jolpica records 0 laps for a disqualified car, so DSQs are left out of this check
        mismatch = q("""SELECT r.driver_id, r.laps_completed, COUNT(l.lap) FROM results r
                        LEFT JOIN laps l ON l.session_id=r.session_id AND l.driver_id=r.driver_id
                        WHERE r.session_id=? AND r.position_text != 'D' GROUP BY r.driver_id
                        HAVING r.laps_completed != COUNT(l.lap)""", sid)
        if mismatch:
            problems.append("lap count ≠ result for " + ", ".join(f"{d} ({b}/{a})" for d, a, b in mismatch))
        dupes = q("""SELECT COUNT(*) FROM (SELECT 1 FROM laps WHERE session_id=? AND position IS NOT NULL
                     GROUP BY lap, position HAVING COUNT(*) > 1)""", sid)[0][0]
        if dupes:
            problems.append(f"{dupes} laps with two cars in one position")
        neg = q("SELECT COUNT(*) FROM laps WHERE session_id=? AND gap_ahead_ms < 0", sid)[0][0]
        if neg:
            problems.append(f"{neg} negative gaps")
        no_pos = q("SELECT COUNT(*) FROM laps WHERE session_id=? AND position IS NULL", sid)[0][0]
        if no_pos:
            problems.append(f"{no_pos} laps without a position")
        head = f"{total}L {n_drivers}d {n_stops}ps" + (f" (+{n_pass} pit-lane passes)" if n_pass else "")

    if n_drivers == 0:
        problems.append("no results")
    if n_laps == 0:
        # one clear line instead of every downstream check failing too
        problems = [p for p in problems if not p.startswith("no laps for")]
        problems.append("no lap data: OpenF1 has none and the FastF1 fallback supplied none (see bake output)")
    elif tire_cov < MIN_TIRE_COVERAGE:
        problems.append(f"tire data on only {tire_cov:.0%} of laps")
    if n_wx == 0:
        problems.append("no weather")

    wx = ("wx ✓" + (" WET" if wet else " rain-before-start" if pre else "")) if n_wx else "wx ✗"
    return f"{head} tires {tire_cov:.0%} {wx}", problems


def report(race_ids: list[int], detail: bool) -> int:
    """Print one line per round and return how many sessions were flagged."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
    flagged = []

    print()
    for race_id in race_ids:
        (rnd, name), = q("SELECT round, name FROM races WHERE race_id=?", race_id)
        parts = []
        for sid, kind in q("SELECT session_id, kind FROM sessions WHERE race_id=? ORDER BY session_id", race_id):
            line, problems = check_session(q, sid, kind)
            mark = " ⚠" if problems else ""
            parts.append(f"{SHORT[kind]} {line}{mark}")
            flagged += [f"R{rnd:02d} {SHORT[kind]}: {p}" for p in problems]
        print(f"R{rnd:02d} {name.replace(' Grand Prix', ' GP'):<20} | " + " | ".join(parts))

    if detail and len(race_ids) == 1:
        print_classification(q, race_ids[0])

    print()
    if flagged:
        print(f"Flagged ({len(flagged)}):")
        for f in flagged:
            print(f"  - {f}")
    else:
        print("All sessions passed: lap counts match results, one car per position per lap, "
              "no negative gaps, tire and weather data present.")
    conn.close()
    return len(flagged)


def print_classification(q, race_id: int) -> None:
    bold, dim, reset = "\033[1m", "\033[2m", "\033[0m"
    medal = {1: "\033[33m", 2: "\033[37m", 3: "\033[38;5;173m"}
    print()
    print(f"  {'Pos':>3}  {'Driver':<27}{'Team':<22}{'Grid':>4}  {'Stops':>5}  {'Status':<14}{'Pts':>4}")
    for order, text, code, full, team, grid, status, pts, stops in q("""
            SELECT r.position_order, r.position_text, d.code, d.first_name || ' ' || d.last_name,
                   t.name, r.grid, r.status, r.points,
                   (SELECT COUNT(*) FROM pitstops p WHERE p.session_id=r.session_id AND p.driver_id=r.driver_id)
            FROM results r JOIN drivers d USING (driver_id) JOIN teams t USING (team_id)
            WHERE r.session_id=? ORDER BY r.position_order""", race_id * 10 + 1):
        line = (f"  {text:>3}  {code} {full:<23}{team:<22}{'PL' if grid == 0 else grid:>4}  {stops:>5}  "
                f"{status:<14}{pts:>4g}")
        print(f"{medal[order]}{bold}{line}{reset}" if order <= 3 else line)
    stand = q("""SELECT s.position, d.code, s.points FROM driver_standings_before s
                 JOIN drivers d USING (driver_id) WHERE s.race_id=?
                 ORDER BY s.position IS NULL, s.position LIMIT 5""", race_id)
    if stand:
        print(f"\n  {dim}Championship going in:{reset} " + "  ".join(f"P{p} {c} {pts:g}" for p, c, pts in stand))


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("season", type=int)
    parser.add_argument("round", type=int, nargs="?", help="omit to bake the whole season")
    args = parser.parse_args()

    schedule = load_schedule(args.season)
    rounds = [args.round] if args.round else [int(r) for r in schedule["RoundNumber"]]
    today = datetime.now(timezone.utc).date()

    f = Fetcher()
    openf1_sessions = f.get(f"{OPENF1}/sessions", {"year": args.season})
    fetched = []
    print(f"Fetching {args.season}: {len(rounds)} round(s)")
    for rnd in rounds:
        event = schedule[schedule["RoundNumber"] == rnd].iloc[0]
        if event["EventDate"].date() >= today:
            print(f"  R{rnd:02d} {event['EventName']}: not raced yet, skipped")
            continue
        print(f"  R{rnd:02d} {event['EventName']}", flush=True)
        fetched.append(fetch_round(f, args.season, rnd, event, openf1_sessions))
    print(f"Fetched: {f.network_calls} new requests, {f.cache_hits} from cache"
          + (f", paused {f.waited / 60:.1f} min on rate limits" if f.waited else ""))

    conn = open_for_write(rebuild_ok=not args.round)
    try:
        with conn:  # one transaction for every round in this run
            for rd in fetched:
                write_round(conn, rd)
    finally:
        conn.close()
        make_read_only(DB_PATH)
    race_ids = [rd["season"] * 100 + rd["round"] for rd in fetched]
    print(f"Wrote {len(race_ids)} round(s) to {DB_PATH.relative_to(PROJECT_DIR)} (read-only)")

    report(race_ids, detail=bool(args.round))
    return 0


if __name__ == "__main__":
    sys.exit(main())
