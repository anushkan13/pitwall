"""Grounding: agents cite facts, code supplies the numbers and the labels.

The no-future rule (in the MCP tools) controls WHICH data an agent can see.
This module controls whether the agent DESCRIBES that data correctly.

1. Structured citations. An agent never types a number. It lists the facts
   it wants to cite as {key, kind, driver} and writes its sentence with
   {key} placeholders. Code looks each fact up in the race data (through the
   same no-future MCP tools) and renders it as a phrase that carries its own
   label: a gap always reads as a gap to a named car, a pit time as a stop
   on a named lap. So a number cannot appear under the wrong meaning.
   Digits typed outside a placeholder are rejected.

2. Claim checks. The words around the facts are checked too:
     - pace claims ("losing pace", "improving") against the driver's recent
       lap-time trend,
     - gap claims ("closing", "pulling away") against the gap trend,
     - "X pitted / boxed" against the pit log,
     - a label written next to a placeholder ("pit time {x}") must match the
       kind of fact x is.
   Anything that fails comes back to the agent as a retry with the reason.

3. Cleanups, in code, never a retry: sentences over their word limit are
   cut back to whole clauses; when the words right before a fact already
   state its label ("PIA is {gap}", "behind {rejoin}", "costs {loss}") the
   fact's short form is used ("PIA is 3.7s ahead of us"); and an adjacent
   repeated word ("track track dry") is collapsed. All are recorded with the
   answer. Label checks still run on the agent's own wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

FactKind = Literal[
    "position", "gap_ahead", "gap_behind", "gap_to", "gap_trend", "tire", "pace_trend",
    "last_stop", "pit_time", "laps_remaining", "pit_loss", "rejoin_behind", "weather", "boxed_this_lap",
    # race records so far
    "fastest_lap", "fastest_stop", "fastest_pit_lane",
    # the season up to this race
    "drivers_leader", "constructors_leader", "champ_position", "champ_gap", "season_record",
    # qualifying (grounding_quali.py)
    "q_session", "q_time_left", "q_position", "q_best", "q_status", "q_gap_to", "q_cut", "q_evolution",
    "q_recent_best", "q_on_track", "rain_trend", "track_temp_trend",
]
QUALI_ONLY_KINDS = {"q_session", "q_time_left", "q_position", "q_best", "q_status", "q_gap_to", "q_cut",
                    "q_evolution", "q_recent_best", "q_on_track", "rain_trend", "track_temp_trend"}
NO_CAR_KINDS = {"laps_remaining", "pit_loss", "rejoin_behind", "weather", "boxed_this_lap", "fastest_lap",
                "fastest_stop", "fastest_pit_lane", "drivers_leader", "constructors_leader"}
TEAM_KINDS = {"champ_position", "champ_gap", "season_record"}  # driver OR team

FACT_HELP = """\
Fact kinds (driver = three-letter code; omit it to mean our driver):
- position(driver): where the car is running, e.g. "NOR in P3"
- gap_ahead(driver): interval to the car in front, e.g. "NOR 1.366s behind VER"
- gap_behind(driver): interval to the car behind, e.g. "NOR 8.511s ahead of HAM"
- gap_to(driver): a rival's gap to OUR car, e.g. "VER 1.366s ahead of us"
- gap_trend(driver): whether a rival's gap to us is shrinking or growing, e.g. "gap to VER shrinking 0.757s/lap"
- tire(driver): compound and age, e.g. "NOR on 10-lap-old INTERMEDIATE tires"
- pace_trend(driver): recent lap-time direction, e.g. "NOR's lap times improving by 0.951s"
- last_stop(driver): lap of the last stop, e.g. "HAM last stopped on lap 11"
- pit_time(driver): pit-lane time of the last completed stop, e.g. "HAM's lap-11 stop took 28.917s"
- laps_remaining: e.g. "42 laps to go"
- pit_loss: what a stop costs here, e.g. "a stop costs about 29.125s"
- rejoin_behind: who we would drop behind if we boxed now
- weather: current conditions on track
- boxed_this_lap: which cars pitted at the end of the current lap
Race records so far:
- fastest_lap: fastest lap of the race so far, e.g. "PIA's lap 41, 99.273s, the fastest lap so far"
- fastest_stop: shortest stationary time so far, e.g. "TSU's lap-11 stop, 2.1s stationary"
- fastest_pit_lane: shortest pit-lane time so far
Season up to this race (set team instead of driver for a team):
- drivers_leader / constructors_leader: who leads going into this race, with points
- champ_position(driver or team): championship place and points going into this race
- champ_gap(driver or team): points behind the leader going into this race
- season_record(driver or team): Grand Prix wins and podiums in the rounds before this one"""

# Words that, written right before a placeholder, claim a meaning for it.
LABEL_WORDS: dict[str, set[str]] = {
    r"pit[- ]?(?:lane )?time|stop (?:took|time|length)|stationary|in the pits for": {"pit_time", "pit_loss"},
    r"\bgap\b|\binterval\b": {"gap_ahead", "gap_behind", "gap_to", "gap_trend", "pit_loss"},
    r"tire age|tyre age|laps? old": {"tire"},
    r"\bposition\b": {"position", "q_position", "champ_position"},
    r"lap ?time|best time|best lap|fastest lap|quickest lap": {"fastest_lap", "q_best", "q_position", "q_recent_best",
                                                                "q_gap_to"},
    r"\bmargin\b|\bcut\b": {"q_cut", "q_gap_to"},
}

PACE_DOWN = r"losing pace|lose pace|degrad|drop(?:ping)?[- ]off|falling off|fall(?:ing)? away in pace|getting slower|slowing|fading|tires? (?:are |is )?(?:going|gone|dying|worn)|pace (?:is )?(?:dropping|falling|fading)"
PACE_UP = r"improving|getting faster|gaining pace|coming (?:in|good)|picking up pace|quicker"
GAP_CLOSE = r"closing|catching|reeling in|gaining on|gap (?:is )?(?:shrinking|coming down)"
GAP_AWAY = r"pulling away|dropping back|losing touch|edging away|gap (?:is )?(?:growing|opening)|opening (?:up )?(?:a|the) gap"
PITTED = r"\b(?:pitted|boxed|stopped|has pitted|just pitted|just boxed)\b"
LEADS_CHAMP = r"\bleads? the (?:drivers'? )?championship|\bchampionship leader\b"
# Predictions about the live race. The data stops at the current lap, so these are never grounded.
FUTURE = (r"\b(?:will|is going to|are going to|is set to|should|looks set to|is likely to|is expected to)\s+"
          r"(?:win|finish|take|claim|score|pit|box|stop|lead|beat|end|retire|podium)\b"
          r"|\bgoing to win\b|\bwill be (?:the )?(?:winner|champion)\b")


# "can't say who will win", "no way to know whether he will pit": talking about
# a prediction is fine; making one is not.
NOT_A_PREDICTION = re.compile(r"\bnot\b|n't|\bcannot\b|\bno way\b|\bunable\b|\bimpossible\b|\bunknown\b|"
                              r"\bwho\b|\bwhether\b|\bif\b|\bwhich\b")


def check_future(text: str, label: str) -> list[str]:
    """A prediction about how the live race goes is never grounded."""
    low = text.lower()
    for m in re.finditer(FUTURE, low):
        clause = re.split(r"[.;,:!?]|\bbut\b|\bthough\b", low[:m.start()])[-1]  # same clause only
        if NOT_A_PREDICTION.search(clause):
            continue
        return [f'{label} predicts the race ("{m.group(0)}"); the data only goes up to the current lap, '
                "so say you don't know instead"]
    return []

# Gap trends smaller than this (s/lap) count as steady.
GAP_STEADY_S = 0.1


class FactRef(BaseModel):
    key: str = Field(description="Placeholder name used in your sentences, e.g. 'ver_gap'.")
    kind: FactKind
    driver: str | None = Field(None, description="Three-letter code; omit for our driver.")
    team: str | None = Field(None, description="Team name, for season kinds about a team, e.g. 'McLaren'.")


class Fact(BaseModel):
    key: str
    kind: str
    driver: str | None
    text: str           # the rendered, self-labelled phrase
    value: Any = None   # the raw number(s), for display
    # (lead-in pattern, short form): when the words right before the fact
    # already state its label ("PIA is {gap}"), the short form avoids saying
    # it twice ("PIA is 3.7s ahead of us"). A short form that drops the
    # driver's code only applies when the lead-in names that same driver.
    shorts: list[tuple[str, str]] = Field(default_factory=list, exclude=True)


@dataclass
class RaceView:
    """The data facts are resolved against, read through the no-future MCP tools."""
    call: Callable[[str, dict], Awaitable[dict]]
    state: dict = field(default_factory=dict)
    rivals: dict = field(default_factory=dict)
    pit_log: dict = field(default_factory=dict)
    tires: dict[str, dict] = field(default_factory=dict)

    @classmethod
    async def load(cls, call: Callable[[str, dict], Awaitable[dict]]) -> "RaceView":
        v = cls(call)
        v.state = await call("get_race_state", {})
        v.rivals = await call("get_rivals", {})
        v.pit_log = await call("get_pit_log", {})
        return v

    @property
    def cars(self) -> list[dict]:
        return self.state.get("cars", [])

    @property
    def me(self) -> dict | None:
        return next((c for c in self.cars if c.get("is_my_driver")), None)

    def car(self, code: str | None) -> dict | None:
        if not code:
            return self.me
        code = code.strip().upper()
        return next((c for c in self.cars if c["code"] == code or c["driver_id"].upper() == code), None)

    def ahead_of(self, car: dict) -> dict | None:
        return next((c for c in self.cars if c["position"] == car["position"] - 1), None)

    def behind(self, car: dict) -> dict | None:
        return next((c for c in self.cars if c["position"] == car["position"] + 1), None)

    def rival(self, code: str) -> dict | None:
        return next((r for r in self.rivals.get("rivals", []) if r["code"] == code), None)

    def stops(self, code: str) -> list[dict]:
        return [s for s in self.pit_log.get("stops", []) if s["code"] == code and s["kind"] == "stop"]

    records: dict = field(default_factory=dict)
    champs: dict[str, dict] = field(default_factory=dict)
    seasons: dict[str, dict] = field(default_factory=dict)

    async def get_records(self) -> dict:
        if not self.records:
            self.records = await self.call("get_lap_records", {})
        return self.records

    async def champ(self, kind: str) -> dict:
        if kind not in self.champs:
            self.champs[kind] = await self.call("get_championship", {"kind": kind})
        return self.champs[kind]

    async def season(self, driver: str | None, team: str | None) -> dict:
        key = f"d:{driver}" if driver else f"t:{team}"
        if key not in self.seasons:
            self.seasons[key] = await self.call("get_season_summary", {"driver": driver} if driver else {"team": team})
        return self.seasons[key]

    async def tire(self, code: str) -> dict:
        if code not in self.tires:
            self.tires[code] = await self.call("get_tire_info", {"driver": code})
        return self.tires[code]

    # -- the interface ground() uses; QualiView implements the same ----------

    async def resolve(self, ref: "FactRef") -> "Fact":
        return await resolve(ref, self)

    async def prepare(self, templates: dict[str, str], my_code: str | None) -> None:
        """Pace checks need tire data for every driver a sentence talks about."""
        for code in {c["code"] for c in self.cars}:
            if any(re.search(rf"\b{code}\b", t) for t in templates.values()) or code == my_code:
                await self.tire(code)

    def check(self, template: str, facts: dict, label: str, my_code: str | None) -> list[str]:
        return check_claims(template, facts, self, label, my_code)

    def gap_to_me(self, car: dict) -> float | None:
        me = self.me
        if not me or car.get("gap_leader_s") is None or me.get("gap_leader_s") is None:
            return None
        return round(car["gap_leader_s"] - me["gap_leader_s"], 3)


class Unresolvable(Exception):
    pass


async def resolve(ref: FactRef, v: RaceView, _no_car: bool = False) -> Fact:
    """Look one fact up in the data and render it with its label."""
    k = ref.kind
    if k in QUALI_ONLY_KINDS:
        raise Unresolvable(f"{ref.key}: {k} is a qualifying fact; this is the race")
    if (k in TEAM_KINDS or k in NO_CAR_KINDS) and not _no_car:
        return await resolve_season_or_record(ref, v)
    car = None if _no_car else v.car(ref.driver)
    if car is None and not _no_car:
        raise Unresolvable(f"{ref.key}: no running car '{ref.driver}' on lap {v.state.get('current_lap')}")
    code = car["code"] if car else None

    def fact(text: str, value: Any = None, shorts: list[tuple[str, str]] | None = None) -> Fact:
        return Fact(key=ref.key, kind=k, driver=code, text=text, value=value, shorts=shorts or [])

    c = re.escape(code.lower()) if code else "^$"  # lead-in patterns that name this car

    if k == "position":
        return fact(f"{code} in P{car['position']}", car["position"],
                    [(rf"\b{c}(?:\s+(?:is|running|runs|sits))?(?:\s+in)?", f"P{car['position']}")])
    if k == "gap_ahead":
        ahead = v.ahead_of(car)
        if ahead is None or car.get("gap_ahead_s") is None:
            return fact(f"{code} leading the race")
        return fact(f"{code} {car['gap_ahead_s']}s behind {ahead['code']}", car["gap_ahead_s"],
                    [(rf"\b{c}(?:\s+is)?", f"{car['gap_ahead_s']}s behind {ahead['code']}")])
    if k == "gap_behind":
        behind = v.behind(car)
        if behind is None or car.get("gap_behind_s") is None:
            return fact(f"{code} with no car behind")
        return fact(f"{code} {car['gap_behind_s']}s ahead of {behind['code']}", car["gap_behind_s"],
                    [(rf"\b{c}(?:\s+is)?", f"{car['gap_behind_s']}s ahead of {behind['code']}")])
    if k == "gap_to":
        if car.get("is_my_driver"):
            raise Unresolvable(f"{ref.key}: gap_to needs a rival's code, not our own car")
        gap = v.gap_to_me(car)
        if gap is None:
            raise Unresolvable(f"{ref.key}: no gap between {code} and us on this lap")
        side = "behind" if gap > 0 else "ahead of"
        return fact(f"{code} {abs(gap)}s {side} us", abs(gap),
                    [(rf"\b{c}(?:\s+(?:is|sits|now))?(?:\s+at)?", f"{abs(gap)}s {side} us")])
    if k == "gap_trend":
        r = v.rival(code)
        if r is None or r.get("gap_change_per_lap_s") is None:
            raise Unresolvable(f"{ref.key}: no gap trend for {code} (only cars in get_rivals have one)")
        ch = r["gap_change_per_lap_s"]
        word = "steady" if abs(ch) < GAP_STEADY_S else ("shrinking" if ch < 0 else "growing")
        tail = "" if word == "steady" else f" {abs(ch)}s/lap"
        return fact(f"gap to {code} {word}{tail} over the last 3 laps", ch,
                    [(r"\bgap(?:\s+is)?", f"to {code} {word}{tail} over the last 3 laps")])
    if k == "tire":
        return fact(f"{code} on {car['tire_age']}-lap-old {car['compound']} tires", car["tire_age"],
                    [(rf"\b{c}(?:\s+is)?\s+on", f"{car['tire_age']}-lap-old {car['compound']} tires")])
    if k == "pace_trend":
        t = (await v.tire(code)).get("current_set") or {}
        p = t.get("recent_pace") or {}
        if p.get("direction") in (None, "unknown"):
            return fact(f"{code}'s recent pace not yet measurable on this set")
        if p["direction"] == "steady":
            return fact(f"{code}'s lap times steady (within {abs(p['change_s'])}s over the last 3 laps)",
                        p["change_s"])
        return fact(f"{code}'s lap times {p['direction']} by {abs(p['change_s'])}s "
                    f"(last 3 laps vs the 3 before)", p["change_s"],
                    [(rf"\b{c}(?:'s|’s)?\s+lap times(?:\s+are)?", f"{p['direction']} by {abs(p['change_s'])}s "
                                                  f"(last 3 laps vs the 3 before)")])
    if k == "last_stop":
        stops = v.stops(code)
        if not stops:
            return fact(f"{code} yet to stop")
        return fact(f"{code} last stopped on lap {stops[-1]['lap']}", stops[-1]["lap"],
                    [(rf"\b{c}\s+(?:last\s+)?(?:stopped|pitted|boxed)", f"on lap {stops[-1]['lap']}")])
    if k == "pit_time":
        done = [s for s in v.stops(code) if s.get("pit_lane_s") is not None]
        if not done:
            raise Unresolvable(f"{ref.key}: {code} has no completed stop with a time yet")
        s = done[-1]
        return fact(f"{code}'s lap-{s['lap']} stop took {s['pit_lane_s']}s in the pit lane", s["pit_lane_s"])
    if k == "laps_remaining":
        n = v.state.get("laps_remaining")
        return fact(f"{n} laps to go", n)
    if k == "pit_loss":
        loss = v.rivals.get("pit_loss_estimate_s")
        if loss is None:
            raise Unresolvable(f"{ref.key}: no completed stops yet to estimate pit loss from")
        return fact(f"a stop costs about {loss}s here", loss,
                    [(r"\b(?:costs?|los(?:e|es|ing)|pay(?:ing)?)", f"about {loss}s")])
    if k == "rejoin_behind":
        cars = [c["code"] for c in v.rivals.get("if_we_box_now_we_rejoin_behind", [])]
        if v.rivals.get("pit_loss_estimate_s") is None:
            raise Unresolvable(f"{ref.key}: no completed stops yet to estimate where we'd rejoin")
        names = ", ".join(cars) if cars else "nobody"
        return fact("boxing now would drop us behind " + names if cars
                    else "boxing now would not drop us behind anyone", cars,
                    [(r"\bbehind", names), (r"\brejoin(?:s|ing)?", f"behind {names}")])
    if k == "weather":
        w = v.state.get("weather") or {}
        if not w:
            raise Unresolvable(f"{ref.key}: no weather reading for this lap")
        cond = "raining" if w["raining"] else ("dry, rain in the last 5 minutes" if w["rain_in_last_5_min"] else "dry")
        return fact(f"track {cond}, {w['track_temp_c']}°C", w,
                    [(r"\btrack(?:\s+is)?", f"{cond}, {w['track_temp_c']}°C")])
    if k == "boxed_this_lap":
        boxed = [c["code"] for c in v.cars if c.get("pitted_end_of_lap") and not c.get("is_my_driver")]
        n = len(boxed)
        return fact(f"{n} {'car' if n == 1 else 'cars'} boxed this lap ({', '.join(boxed)})" if boxed
                    else "nobody boxed this lap", boxed)
    raise Unresolvable(f"{ref.key}: unknown fact kind {k}")


def _pts(x: float) -> str:
    return f"{x:g} point{'s' if x != 1 else ''}"


async def resolve_season_or_record(ref: FactRef, v: RaceView) -> Fact:
    """Facts that aren't about one running car: race records and the season so far."""
    k = ref.kind

    def fact(text: str, value: Any = None, driver: str | None = None, shorts: list | None = None) -> Fact:
        return Fact(key=ref.key, kind=k, driver=driver, text=text, value=value, shorts=shorts or [])

    if k in ("laps_remaining", "pit_loss", "rejoin_behind", "weather", "boxed_this_lap"):
        return await resolve(FactRef(key=ref.key, kind=k), v, _no_car=True)
    if k == "fastest_lap":
        f = (await v.get_records()).get("fastest_lap")
        if not f:
            raise Unresolvable(f"{ref.key}: no timed laps yet")
        return fact(f"{f['code']}'s lap {f['lap']}, {f['lap_time_s']}s, the fastest lap so far", f["lap_time_s"], f["code"])
    if k in ("fastest_stop", "fastest_pit_lane"):
        r = await v.get_records()
        stops = r.get("fastest_stops_stationary" if k == "fastest_stop" else "fastest_stops_pit_lane") or []
        if not stops:
            raise Unresolvable(f"{ref.key}: no completed, timed stops yet")
        t = stops[0]
        if k == "fastest_stop":
            return fact(f"{t['code']}'s lap-{t['lap']} stop, {t['stationary_s']}s stationary "
                        f"({t['pit_lane_s']}s in the pit lane), the fastest stop so far", t["stationary_s"], t["code"])
        return fact(f"{t['code']}'s lap-{t['lap']} stop, {t['pit_lane_s']}s in the pit lane, "
                    f"the quickest pit-lane time so far", t["pit_lane_s"], t["code"])

    if k in ("drivers_leader", "constructors_leader"):
        kind = "drivers" if k == "drivers_leader" else "constructors"
        c = await v.champ(kind)
        top = (c.get("standings") or [None])[0]
        if not top or top.get("position") is None:
            raise Unresolvable(f"{ref.key}: no {kind}' standings before this race")
        who = top.get("code") or top.get("team")
        verb = "leads" if kind == "drivers" else "lead"
        return fact(f"{who} {verb} the {kind[:-1]}s' championship on {_pts(top['points'])} going into this race "
                    f"(after round {c['after_round']})", top["points"], top.get("code"))

    # driver-or-team kinds
    if bool(ref.driver) == bool(ref.team):
        raise Unresolvable(f"{ref.key}: {k} needs exactly one of driver or team")
    kind = "drivers" if ref.driver else "constructors"
    c = await v.champ(kind)
    rows = c.get("standings") or []
    if ref.driver:
        code = ref.driver.strip().upper()
        row = next((r for r in rows if r.get("code") == code), None)
        who = code
    else:
        key = ref.team.strip().lower()
        row = next((r for r in rows if key in (r["team_id"], r["team"].lower()) or key in r["team"].lower()), None)
        who = row["team"] if row else ref.team
    label = f"the {kind[:-1]}s' championship"
    w = re.escape(who.lower())  # lead-ins that already name this driver or team

    if k in ("champ_position", "champ_gap"):
        if row is None or row.get("position") is None:
            raise Unresolvable(f"{ref.key}: {who} has no championship position going into this race")
        if k == "champ_position":
            rest = (f"P{row['position']} in {label} on {_pts(row['points'])} "
                    f"({row['wins']} win{'s' if row['wins'] != 1 else ''}) going into this race")
            return fact(f"{who} {rest}", row["points"], row.get("code"),
                        [(rf"\b{w}(?:\s+(?:are|is|sit|sits|were|was))?(?:\s+(?:in|at))?", rest)])
        leader = rows[0]
        lname = leader.get("code") or leader.get("team")
        if row is leader:
            second = rows[1] if len(rows) > 1 else None
            if not second:
                return fact(f"{who} leads {label} going into this race", 0, row.get("code"))
            return fact(f"{who} leads {label} by {_pts(row['points'] - second['points'])} over "
                        f"{second.get('code') or second.get('team')} going into this race",
                        row["points"] - second["points"], row.get("code"))
        rest = f"{_pts(leader['points'] - row['points'])} behind {lname} in {label} going into this race"
        return fact(f"{who} {rest}", leader["points"] - row["points"], row.get("code"),
                    [(rf"\b{w}(?:\s+(?:are|is|sit|sits|was|were))?", rest)])
    if k == "season_record":
        sr = await v.season(ref.driver, ref.team)
        if sr.get("refused"):
            raise Unresolvable(f"{ref.key}: {sr['reason']}")
        n = sr["rounds_before_this_race"]
        wins, p = sr["grand_prix_wins"], sr["grand_prix_podiums"]
        rest = (f"{wins} Grand Prix win{'s' if wins != 1 else ''} and {p} podium{'s' if p != 1 else ''} "
                f"in the {n} round{'s' if n != 1 else ''} before this race")
        return fact(f"{sr['who']}: {rest}", {"wins": wins, "podiums": p},
                    ref.driver.upper() if ref.driver else None,
                    [(rf"\b{re.escape(sr['who'].lower())}(?:\s+(?:have|has))?(?:\s+(?:had|scored|taken|got|racked up))?",
                      rest)])
    raise Unresolvable(f"{ref.key}: unknown fact kind {k}")


PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Words that may repeat between a sentence and a fact phrase without reading twice.
STOP_WORDS = {"the", "a", "an", "to", "of", "is", "are", "and", "on", "in", "at", "by", "with", "for",
              "us", "we", "our", "its", "it", "this", "that", "s"}


def reads_twice(template: str, facts: dict[str, Fact], label: str, skip: set[str] = frozenset()) -> list[str]:
    """Notes (not retries) on wording that repeats what a fact phrase already
    says a few words away, e.g. "costs {loss}" -> "costs a stop costs about".
    Adjacent repeats are fixed by collapse_repeats; these are only recorded."""
    notes = []
    for m in PLACEHOLDER.finditer(template):
        f = facts.get(m.group(1))
        if f is None or m.group(1) in skip:
            continue
        before = PLACEHOLDER.sub(" ", template[:m.start()])
        if re.search(r"[:;,(—]\s*$", before):
            continue  # punctuation right before the fact: it starts a fresh clause
        phrase = re.findall(r"[a-z]+", f.text.lower())
        words = {w for w in phrase[:6] if w not in STOP_WORDS}
        prev = re.findall(r"[a-z]+", before.lower())
        if prev and phrase and prev[-1] == phrase[0]:
            continue  # adjacent repeat: collapse_repeats already fixes it
        lead = [w for w in prev[-2:] if w not in STOP_WORDS]
        dup = [w for w in lead if w in words]
        if dup:
            notes.append(f'{label}: "{before.strip()[-25:]} {f.text[:35]}" repeats "{dup[0]}" (left as written)')
    return notes


def tidy(text: str) -> str:
    """Capitalise each sentence and drop doubled spaces."""
    text = re.sub(r"\s{2,}", " ", text).strip()
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)


REPEATED_WORD = re.compile(r"\b(\w+)(?:\s+\1\b)+", re.IGNORECASE)
# a phrase of two to six words said twice in a row
REPEATED_PHRASE = re.compile(r"\b((?:[\w'’]+\s+){1,5}[\w'’]+)\s+\1\b", re.IGNORECASE)


def collapse_repeats(text: str, label: str) -> tuple[str, list[str]]:
    """Code cleanup: collapse an adjacent repeated word or phrase ("track
    track dry" -> "track dry", "going into this race going into this race"
    -> "going into this race"). Never a retry."""
    notes = [f'{label}: collapsed repeated "{m.group(1)}"' for m in REPEATED_PHRASE.finditer(text)]
    text = REPEATED_PHRASE.sub(lambda m: m.group(1), text)
    notes += [f'{label}: collapsed repeated "{m.group(1)}"' for m in REPEATED_WORD.finditer(text)
              if not m.group(1).isdigit()]
    text = REPEATED_WORD.sub(lambda m: m.group(0) if m.group(1).isdigit() else m.group(1), text)
    return text, notes


CLAUSE_SPLIT = re.compile(r"(\s*[;.]\s+|\s+—\s+)")


def trim_to_limit(template: str, limit: int, label: str) -> tuple[str, list[str]]:
    """Code cleanup: keep whole clauses, in order, until the word limit (a
    placeholder counts as one word). A clause is never cut in half, so a
    number is never separated from its label. Never a retry."""
    count = lambda t: len(re.findall(r"[\w'’-]+", PLACEHOLDER.sub(" X ", t)))  # noqa: E731
    total = count(template)
    if total <= limit:
        return template, []
    parts = CLAUSE_SPLIT.split(template)  # clause, separator, clause, ...
    kept, used = [parts[0]], count(parts[0])
    for i in range(1, len(parts) - 1, 2):
        sep, clause = parts[i], parts[i + 1]
        if used + count(clause) > limit:
            break
        kept += [sep, clause]
        used += count(clause)
    out = "".join(kept).rstrip(" ;,—")
    if not out.endswith((".", "!", "?")):
        out += "."
    return out, [f"{label}: trimmed from {total} to {count(out)} words (limit {limit})"]


def render(template: str, facts: dict[str, Fact], label: str,
           allowed_numbers: set[str] = frozenset()) -> tuple[str, list[str], set[str]]:
    """Fill a sentence from resolved facts. Returns (text, problems, keys shown in short form).

    allowed_numbers: numbers that may be typed because they come from a cited
    rules entry (e.g. "25" points for a win). Anything else typed is rejected."""
    problems = []
    # session names (Q1, Q2, Q3) aren't numbers being cited
    typed = re.findall(r"\d+(?:\.\d+)?", re.sub(r"\bQ[123]\b", " ", PLACEHOLDER.sub("", template)))
    stray = [n for n in typed if n not in allowed_numbers]
    if stray:
        problems.append(f"{label} contains a typed number ({', '.join(stray)}); race and season numbers must come "
                        "through a {fact} placeholder, and rules numbers must appear in a cited rules entry")
    for key in PLACEHOLDER.findall(template):
        if key not in facts:
            problems.append(f"{label} uses {{{key}}}, which is not one of your facts")
    shortened: set[str] = set()

    def fill(m: re.Match) -> str:
        f = facts.get(m.group(1))
        if f is None:
            return m.group(0)
        lead = PLACEHOLDER.sub(" ", template[:m.start()]).rstrip().lower()
        for pattern, short in f.shorts:
            if re.search(pattern + r"$", lead):
                shortened.add(m.group(1))
                return short
        return f.text

    text = PLACEHOLDER.sub(fill, template)
    return tidy(text), problems, shortened


def check_claims(template: str, facts: dict[str, Fact], v: RaceView, label: str,
                 my_code: str | None) -> list[str]:
    """Check the words around the facts: labels, pace and gap directions, pit claims."""
    problems = check_labels(template, facts, label)
    codes = {c["code"] for c in v.cars}
    problems += _race_claims(template, facts, v, label, my_code, codes)
    return problems


HYPOTHETICAL = r"\bif\b|\bwhether\b|\bunless\b|\bcould\b|\bmight\b|\bwould\b|\bonce\b|\buntil\b"
NEGATION = (r"\bnot\b|n't|\bno\b|\bno longer\b|\bnever\b|\bwithout\b|\bnothing\b|\black of\b|"
            r"\bnone\b|\bneither\b")
# Saying a trend is absent: checked like a false claim that it's present.
PACE_STEADY = r"pace (?:is |remains |stays )?(?:steady|stable|consistent|unchanged|holding)|holding (?:its |their |our )?pace"
GAP_STEADY = r"gap(?: to [a-z]{3})? (?:is |remains |stays |has been )?(?:steady|stable|holding|unchanged|constant)"


def clause_mode(low: str, at: int) -> str | None:
    """How the clause containing position `at` uses the words found there:
    'hypothetical' ("if it dries"): not a claim, skip it;
    'negated' ("no evidence of drying", "isn't losing pace"): a denial, so
    check that the trend really is absent; None: a plain claim."""
    clause = re.split(r"[.;,:!?]|\bbut\b|\bthough\b|\bwhile\b", low[:at])[-1]
    if re.search(HYPOTHETICAL, clause):
        return "hypothetical"
    if re.search(NEGATION, clause):
        return "negated"
    return None


def default_subject(label: str, my_code: str | None) -> str | None:
    """Who a claim is about when its sentence names nobody: in a note about a
    rival, that rival; otherwise our driver."""
    m = re.match(r"note for ([A-Z]{3})\b", label)
    return m.group(1) if m else my_code


def check_labels(template: str, facts: dict[str, Fact], label: str) -> list[str]:
    """A label written just before a placeholder must fit the fact's kind."""
    problems: list[str] = []
    for m in PLACEHOLDER.finditer(template):
        key = m.group(1)
        if key not in facts:
            continue
        before = template[max(0, m.start() - 40):m.start()].lower()
        before = PLACEHOLDER.sub(" ", before)  # don't read an earlier fact's name as a label
        for words, kinds in LABEL_WORDS.items():
            if re.search(rf"(?:{words})\W*(?:of|at|is|was|:)?\W*$", before) and facts[key].kind not in kinds:
                problems.append(f'{label} labels {{{key}}} as "{re.search(words, before).group(0)}", '
                                f"but it is a {facts[key].kind} fact ({facts[key].text})")
    return problems


def _race_claims(template: str, facts: dict[str, Fact], v: "RaceView", label: str, my_code: str | None,
                 codes: set[str]) -> list[str]:
    problems: list[str] = []

    # 2. claims checked sentence by sentence; each claim is about the driver
    #    named nearest before it ("our"/"we"/"us" = our driver)
    carry = default_subject(label, my_code)
    for sentence in re.split(r"(?<=[.;!?])\s+|\s+—\s+", template):
        plain = PLACEHOLDER.sub(lambda m: f" {facts[m.group(1)].driver or ''} " if m.group(1) in facts else " ",
                                sentence)
        low = plain.lower()
        mentions = sorted(
            [(m.start(), c) for c in codes for m in re.finditer(rf"\b{c}\b", plain)]
            + ([(m.start(), my_code) for m in re.finditer(r"\b(?:our|we|us|my)\b", low)] if my_code else []))
        fallback = carry

        def subject(at: int, rivals_only: bool = False) -> str | None:
            pool = [(i, c) for i, c in mentions if not (rivals_only and c == my_code)]
            before = [c for i, c in pool if i <= at]
            if before:
                return before[-1]
            after = [c for i, c in pool if i > at]
            if after:
                return after[0]
            return None if rivals_only and fallback == my_code else fallback

        for m in re.finditer(f"(?P<down>{PACE_DOWN})|(?P<up>{PACE_UP})|(?P<steady>{PACE_STEADY})", low):
            mode = clause_mode(low, m.start())
            if mode == "hypothetical":
                continue
            code = subject(m.start())
            p = ((v.tires.get(code) or {}).get("current_set") or {}).get("recent_pace") or {}
            d = p.get("direction")
            if d in (None, "unknown"):
                continue
            said = "worsening" if m.group("down") else "improving" if m.group("up") else "steady"
            # a denial ("isn't losing pace") or a steady claim is wrong when that trend is happening
            wrong = (d == said) if mode == "negated" else (d != said)
            if said == "steady" and mode != "negated":
                wrong = d != "steady"
            if wrong:
                problems.append(f'{label} {"denies" if mode == "negated" else "says"} "{m.group(0)}" for {code}, '
                                f"but {code}'s lap times are {d} ({p['change_s']:+}s, last 3 laps vs the 3 before)")

        for m in re.finditer(f"(?P<close>{GAP_CLOSE})|(?P<away>{GAP_AWAY})|(?P<steady>{GAP_STEADY})", low):
            mode = clause_mode(low, m.start())
            if mode == "hypothetical":
                continue
            code = subject(m.start(), rivals_only=True)
            r = v.rival(code) if code else None
            if not r or r.get("gap_change_per_lap_s") is None:
                continue
            ch = r["gap_change_per_lap_s"]
            actual = "steady" if abs(ch) < GAP_STEADY_S else ("shrinking" if ch < 0 else "growing")
            said = "shrinking" if m.group("close") else "growing" if m.group("away") else "steady"
            wrong = (actual == said) if mode == "negated" else (actual != said)
            if wrong:
                problems.append(f'{label} {"denies" if mode == "negated" else "says"} "{m.group(0)}" about {code}, '
                                f"but the gap to {code} is {actual} ({ch:+}s/lap)")

        for m in re.finditer(LEADS_CHAMP, low):
            code = subject(m.start(), rivals_only=False)
            leader = ((v.champs.get("drivers") or {}).get("standings") or [{}])[0].get("code")
            if code and leader and code != leader and not re.search(r"\bconstructor", low[m.start():m.end() + 20]):
                problems.append(f'{label} says {code} leads the championship, but {leader} leads going into this race')

        for m in re.finditer(PITTED, low):
            code = subject(m.start(), rivals_only=True)
            if code and not v.stops(code):
                problems.append(f"{label} says {code} pitted, but {code} has not stopped so far")
        if mentions:
            carry = mentions[-1][1]
    return problems


async def ground(refs: list[FactRef], templates: dict[str, str], v: "RaceView",
                 my_code: str | None, limits: dict[str, int] | None = None,
                 allowed_numbers: set[str] = frozenset(), check_predictions: bool = False
                 ) -> tuple[dict[str, str], list[Fact], list[str], list[str]]:
    """Resolve facts, tidy each sentence in code, then run the grounding checks.

    Returns (rendered texts by label, facts used, problems, cleanups).
    Problems are grounding failures worth a retry: an unresolvable fact, a typed
    number, a mislabel, a wrong trend or pit claim. Cleanups are fixed in code
    and only recorded: trimming to the word limit and collapsing repeated words.
    """
    problems: list[str] = []
    cleanups: list[str] = []
    facts: dict[str, Fact] = {}
    for ref in refs:
        if ref.key in facts:
            problems.append(f"fact key {ref.key} is used twice")
            continue
        try:
            facts[ref.key] = await v.resolve(ref)
        except Unresolvable as exc:
            problems.append(str(exc))
    await v.prepare(templates, my_code)
    rendered, used = {}, set()
    for label, template in templates.items():
        limit = (limits or {}).get(label) or (limits or {}).get("*")
        if limit:
            template, notes = trim_to_limit(template, limit, label)
            cleanups += notes
        used |= set(PLACEHOLDER.findall(template))
        text, errs, shortened = render(template, facts, label, allowed_numbers)
        problems += errs
        problems += v.check(template, facts, label, my_code)
        if check_predictions:
            problems += check_future(PLACEHOLDER.sub(" ", template), label)
        cleanups += [f"{label}: short form for {{{k}}} after its lead-in ({facts[k].kind})" for k in sorted(shortened)]
        text, notes = collapse_repeats(text, label)
        cleanups += notes + reads_twice(template, facts, label, skip=shortened)
        rendered[label] = text
    return rendered, [f for k, f in facts.items() if k in used], problems, cleanups
