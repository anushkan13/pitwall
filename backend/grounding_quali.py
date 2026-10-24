"""Grounding for qualifying: the same rules as the race, on qualifying data.

QualiView plugs into grounding.ground() exactly like RaceView: facts are
cited by kind and filled in by code with their labels (so a lap time can only
read as a lap time, a margin as a margin to the cut), digits typed outside a
placeholder are rejected, and the words around the facts are checked:

  - track claims ("track is drying", "getting faster", "times dropping")
    against the field's lap-time evolution and the rain readings,
  - rain claims ("still raining", "rain has stopped") against the weather feed,
  - status claims ("NOR is on track", "in the garage") against each car's status,
  - cut claims ("in the drop zone", "safe", "inside the cut") against the order.

Everything is read through the qualifying MCP tools, so it only ever sees
the session up to the current session time.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from grounding import (
    NO_CAR_KINDS, PLACEHOLDER, TEAM_KINDS, Fact, FactRef, RaceView, Unresolvable, check_labels,
    clause_mode, default_subject, resolve_season_or_record,
)

QUALI_FACT_HELP = """\
Qualifying fact kinds (driver = three-letter code; omit it to mean our driver):
- q_session: which segment and how far into it, e.g. "Q2, 3:57 into a scheduled 15:00"
- q_time_left: scheduled time left, e.g. "about 11:03 of the scheduled 15:00 left in Q2"
- q_position(driver): place and best time this segment, e.g. "NOR P3 in Q2 on 1:51.379 (WET)"
- q_best(driver): best lap this segment and when it was set
- q_status(driver): garage / out lap / on a lap, and on what tires
- q_gap_to(driver): a rival's best this segment against ours, e.g. "VER 0.214s quicker than us in Q2"
- q_cut: the elimination line and our margin to it, e.g. "the Q2 cut is P10 (GAS, 1:51.760); NOR is 0.381s inside it"
- q_evolution: the field's best lap in the last 5 minutes against the 5 before
- q_recent_best: the fastest lap in the last 5 minutes, who set it and on what tire
- q_on_track: how many cars are out and on what tires, e.g. "15 cars on track: 14 on WET, 1 on INTERMEDIATE"
- weather: rain now, minutes since rain, track temperature
- rain_trend: how many of the last 10 weather readings had rain
- track_temp_trend: track temperature change over the last 10 minutes
Season kinds (drivers_leader, champ_position, ...) work here too."""

QUALI_KINDS = {"q_session", "q_time_left", "q_position", "q_best", "q_status", "q_gap_to", "q_cut",
               "q_evolution", "q_recent_best", "q_on_track", "weather", "rain_trend", "track_temp_trend"}
QUALI_NO_CAR = {"q_session", "q_time_left", "q_cut", "q_evolution", "q_recent_best", "q_on_track", "weather",
                "rain_trend", "track_temp_trend"}

STATUS_WORDS = {"garage": "in the garage", "out lap": "on an out lap", "on a lap": "on a timed lap"}

# claims checked against the data
DRYING = r"\bdrying\b|\bdries\b|dry(?:ing)? line|drier"
TRACK_FASTER = (r"getting faster|getting quicker|improving|coming in|ramping up|evolving|"
                r"times (?:are |keep )?(?:tumbling|dropping|falling|coming down)|more grip")
TRACK_SLOWER = r"getting slower|worsening|deteriorat|times (?:are )?(?:rising|going up)|losing grip"
TRACK_CONTEXT = r"\btrack\b|\bfield\b|\bconditions\b|\bgrip\b|lap times|\bsurface\b"
# saying the trend isn't there
STILL_WET = r"\bstill wet\b|\btrack (?:is |remains |stays )?(?:still )?wet\b"
STATIC = (r"conditions (?:are |remain |stay )?(?:unchanged|the same|stable)|nothing has changed|"
          r"no change in (?:the )?(?:conditions|track)|track (?:is |remains |stays )?(?:unchanged|the same)")
RAINING = r"\b(?:it is|it's|still|currently|now) raining\b|\brain is (?:falling|coming down)\b|\bin the rain\b"
RAIN_STOPPED = r"rain (?:has )?stopped|stopped raining|\bno rain\b|\bno longer raining\b"
ON_TRACK = r"\bon track\b|\bout on (?:track|a lap)\b|\bon (?:a|an out|a timed|a flying|a push) lap\b|\bis out\b"
IN_GARAGE = r"\bin the garage\b|\bin the pits\b|\bin the box\b"
DROP_ZONE = r"drop zone|knock-?out zone|below the cut|outside the cut|outside the top|would be knocked out"
SAFE = r"inside the cut|above the cut|\bsafe\b|inside the top"


def _view_status(car: dict | None) -> str | None:
    return car.get("status") if car else None


class QualiView(RaceView):
    """The data qualifying facts are resolved against."""

    quali: dict
    qrivals: dict
    drivers: dict[str, dict]

    @classmethod
    async def load(cls, call: Callable[[str, dict], Awaitable[dict]]) -> "QualiView":
        v = cls(call)
        v.quali = await call("get_quali_state", {})
        v.qrivals = await call("get_quali_rivals", {})
        v.drivers = {}
        # RaceView fields some shared code reads
        v.state = {"cars": [], "current_lap": None}
        v.rivals = {"rivals": v.qrivals.get("rivals", [])}
        return v

    # -- shape ----------------------------------------------------------------

    @property
    def cars(self) -> list[dict]:
        return self.quali.get("order", [])

    @property
    def me(self) -> dict | None:
        return next((c for c in self.cars if c.get("is_my_driver")), None)

    def car(self, code: str | None) -> dict | None:
        if not code:
            return self.me
        code = code.strip().upper()
        return next((c for c in self.cars if c["code"] == code), None)

    @property
    def segment(self) -> str | None:
        return self.quali.get("segment")

    # -- the interface ground() uses -----------------------------------------

    async def prepare(self, templates: dict[str, str], my_code: str | None) -> None:
        return None

    def check(self, template: str, facts: dict, label: str, my_code: str | None) -> list[str]:
        return check_labels(template, facts, label) + self._claims(template, facts, label, my_code)

    async def resolve(self, ref: FactRef) -> Fact:
        k = ref.kind
        if k in TEAM_KINDS or k in {"drivers_leader", "constructors_leader"}:
            return await resolve_season_or_record(ref, self)
        if k not in QUALI_KINDS:
            raise Unresolvable(f"{ref.key}: {k} is a race fact; this is qualifying, use the q_ kinds")
        q, seg = self.quali, self.segment
        if not seg and k not in ("weather", "rain_trend", "track_temp_trend"):
            raise Unresolvable(f"{ref.key}: qualifying hasn't started yet")
        car = None if k in QUALI_NO_CAR else self.car(ref.driver)
        if k not in QUALI_NO_CAR and car is None:
            raise Unresolvable(f"{ref.key}: no car '{ref.driver}' still in {seg or 'the session'}")
        code = car["code"] if car else None
        c = re.escape(code.lower()) if code else "^$"

        def fact(text: str, value: Any = None, shorts: list | None = None, driver: str | None = code) -> Fact:
            return Fact(key=ref.key, kind=k, driver=driver, text=text, value=value, shorts=shorts or [])

        if k == "q_session":
            if not q.get("running"):
                return fact(f"no running since {seg} (a break or a stoppage)", None)
            return fact(f"{seg}, {q['elapsed']} into a scheduled {q['scheduled_length']}", q["elapsed"])
        if k == "q_time_left":
            if not q.get("running"):
                raise Unresolvable(f"{ref.key}: no cars are running, so there's no time left to count "
                                   f"({seg} is over or stopped)")
            return fact(f"about {q['scheduled_time_left']} of the scheduled {q['scheduled_length']} left in {seg}",
                        q["scheduled_time_left_s"])
        if k == "q_position":
            rest = (f"P{car['position']} in {seg} on {car['best']} ({car['best_compound']})" if car["best"]
                    else f"yet to set a time in {seg}")
            return fact(f"{code} {rest}", car["position"], [(rf"\b{c}(?:\s+(?:is|sits|runs))?", rest)])
        if k == "q_best":
            if not car["best"]:
                return fact(f"{code} yet to set a time in {seg}")
            rest = f"best in {seg} is {car['best']} on {car['best_compound']}, set {car['best_set_at']} in"
            return fact(f"{code}'s {rest}", car["best"], [(rf"\b{c}(?:'s|’s)", rest)])
        if k == "q_status":
            where = STATUS_WORDS.get(car["status"], car["status"])
            tires = (f"{car['tire_age']}-lap-old {car['compound']} tires" if car["status"] == "garage" and car["compound"]
                     else f"{car['compound']} tires" if car["compound"] else "tires unknown")
            rest = f"{where} on {tires}" if car["compound"] else where
            return fact(f"{code} {rest}", car["status"], [(rf"\b{c}(?:\s+is)?", rest)])
        if k == "q_gap_to":
            r = next((r for r in self.qrivals.get("rivals", []) if r["code"] == code), None)
            if car.get("is_my_driver"):
                raise Unresolvable(f"{ref.key}: q_gap_to needs a rival's code, not our own car")
            if r is None or r.get("gap_s") is None:
                raise Unresolvable(f"{ref.key}: no time gap between {code} and us in {seg} yet "
                                   "(only cars in get_quali_rivals with times have one)")
            side = "quicker than" if r["relation"] == "ahead" else "slower than"
            rest = f"{r['gap_s']}s {side} us in {seg}"
            return fact(f"{code} {rest}", r["gap_s"], [(rf"\b{c}(?:\s+is)?", rest)])
        if k == "q_cut":
            cut = self.qrivals.get("cut")
            if not cut:
                raise Unresolvable(f"{ref.key}: there's no elimination line in {seg}")
            line = f"the {seg} cut is P{cut['cars_through']}"
            if cut["car_on_the_line"] and cut["line_time"]:
                line += f" ({cut['car_on_the_line']}, {cut['line_time']})"
            me = self.me
            if cut["my_margin_s"] is None:
                tail = f"{me['code']} has no time yet" if me and not me["best"] else "our margin can't be measured yet"
            elif cut["my_margin_s"] >= 0:
                tail = f"{me['code']} is {cut['my_margin_s']}s inside it"
            else:
                tail = f"{me['code']} is {abs(cut['my_margin_s'])}s outside it"
            return fact(f"{line}; {tail}", cut["my_margin_s"], driver=me["code"] if me else None)
        if k == "q_evolution":
            e = q.get("track_evolution") or {}
            if e.get("direction") in (None, "unknown"):
                return fact(f"the field's lap-time trend in {seg} not measurable yet", None)
            if e["direction"] == "steady":
                return fact(f"the field's best lap about the same in the last {e['window_minutes']} minutes "
                            f"as in the {e['window_minutes']} before ({e['change_s']:+}s)", e["change_s"])
            word = "faster" if e["change_s"] < 0 else "slower"
            return fact(f"the field's best lap {abs(e['change_s'])}s {word} in the last {e['window_minutes']} "
                        f"minutes than in the {e['window_minutes']} before", e["change_s"])
        if k == "q_recent_best":
            rb = (q.get("track_evolution") or {}).get("recent_best")
            if not rb:
                raise Unresolvable(f"{ref.key}: no completed laps in the last few minutes of {seg}")
            return fact(f"the fastest lap in the last 5 minutes: {rb['code']} {rb['time']} on {rb['compound']}",
                        rb["time"], driver=rb["code"])
        if k == "q_on_track":
            ot = q.get("on_track") or {}
            n = ot.get("count", 0)
            if not n:
                return fact("no cars on track", 0)
            mix = ", ".join(f"{v} on {kk}" for kk, v in ot["by_compound"].items())
            return fact(f"{n} car{'s' if n != 1 else ''} on track ({mix})", n)
        w = q.get("weather")
        if not w:
            raise Unresolvable(f"{ref.key}: no weather reading yet")
        if k == "weather":
            if w["raining"]:
                cond = "raining"
            elif w["minutes_since_rain"] is None:
                cond = "dry, no rain this session"
            else:
                cond = f"dry, no rain for {w['minutes_since_rain']} minutes"
            return fact(f"track {cond}, {w['track_temp_c']}°C", w, [(r"\btrack(?:\s+is)?", f"{cond}, {w['track_temp_c']}°C")],
                        driver=None)
        if k == "rain_trend":
            return fact(f"rain on {w['rain_readings_last_10']} of the last 10 weather readings",
                        w["rain_readings_last_10"], driver=None)
        if k == "track_temp_trend":
            ch = w.get("track_temp_change_10_min")
            if ch is None:
                raise Unresolvable(f"{ref.key}: not enough weather readings yet")
            word = "steady" if abs(ch) < 0.2 else ("up" if ch > 0 else "down")
            return fact(f"track temperature {word}" + ("" if word == "steady" else f" {abs(ch)}°C") +
                        " over 10 minutes", ch, driver=None)
        raise Unresolvable(f"{ref.key}: unknown qualifying fact kind {k}")

    # -- claim checks -----------------------------------------------------------

    def _claims(self, template: str, facts: dict[str, Fact], label: str, my_code: str | None) -> list[str]:
        problems: list[str] = []
        codes = {c["code"] for c in self.cars}
        q = self.quali
        evo = (q.get("track_evolution") or {}).get("direction")
        w = q.get("weather") or {}
        cut = (self.qrivals.get("cut") or {}).get("cars_through")

        carry = default_subject(label, my_code)
        for sentence in re.split(r"(?<=[.;!?])\s+|\s+—\s+", template):
            plain = PLACEHOLDER.sub(lambda m: f" {facts[m.group(1)].driver or ''} " if m.group(1) in facts else " ",
                                    sentence)
            low = plain.lower()
            mentions = sorted([(m.start(), cc) for cc in codes for m in re.finditer(rf"\b{cc}\b", plain)]
                              + ([(m.start(), my_code) for m in re.finditer(r"\b(?:our|we|us|my)\b", low)]
                                 if my_code else []))
            fallback = carry

            def subject(at: int) -> str | None:
                before = [cc for i, cc in mentions if i <= at]
                return before[-1] if before else fallback

            def clause_negated(at: int) -> bool:
                clause = re.split(r"[.;,:!?]|\bbut\b|\bthough\b", low[:at])[-1]
                return bool(re.search(r"\bnot\b|n't|\bno longer\b|\bnever\b|\bwithout\b|\bif\b|\bwhether\b|\bunless\b",
                                      clause))

            # drying is a present trend once rain earlier in the session has stopped for a few
            # minutes (the same test that allows a "drying" claim), so denying it is checked too
            drying_now = (not w.get("raining") and w.get("minutes_since_rain") is not None
                          and w["minutes_since_rain"] >= 3)
            dry_for = w.get("minutes_since_rain")
            for m in re.finditer(STILL_WET, low):
                if clause_mode(low, m.start()) is None and drying_now:
                    problems.append(f'{label} says "{m.group(0)}", but it hasn\'t rained for {dry_for} minutes '
                                    "and the track is drying")
            for m in re.finditer(STATIC, low):
                if clause_mode(low, m.start()) == "hypothetical":
                    continue
                if drying_now or evo in ("improving", "worsening"):
                    why = (f"it hasn't rained for {dry_for} minutes and the track is drying" if drying_now else
                           f"the field's best lap is {evo}")
                    problems.append(f'{label} says "{m.group(0)}", but {why}')

            if re.search(TRACK_CONTEXT, low) or re.search(DRYING, low):
                for m in re.finditer(DRYING, low):
                    mode = clause_mode(low, m.start())
                    if mode == "hypothetical":
                        continue
                    if mode == "negated":
                        if drying_now:
                            problems.append(f'{label} denies the track is "{m.group(0)}", but it hasn\'t rained for '
                                            f"{dry_for} minutes")
                        continue
                    if w.get("raining"):
                        problems.append(f'{label} says the track is "{m.group(0)}", but it is raining right now')
                    elif w.get("minutes_since_rain") is not None and w["minutes_since_rain"] < 3:
                        problems.append(f'{label} says the track is "{m.group(0)}", but it rained '
                                        f"{w['minutes_since_rain']} minutes ago")
                if re.search(TRACK_CONTEXT, low):
                    for pat, need in ((TRACK_FASTER, "improving"), (TRACK_SLOWER, "worsening")):
                        for m in re.finditer(pat, low):
                            mode = clause_mode(low, m.start())
                            if mode == "hypothetical":
                                continue
                            if mode == "negated":
                                if evo == need:
                                    e = q.get("track_evolution") or {}
                                    problems.append(f'{label} denies the track is "{m.group(0)}", but the field\'s '
                                                    f"best lap is {evo} ({e.get('change_s', 0):+}s, last 5 min vs the 5 before)")
                                continue
                            if evo != need:
                                e = q.get("track_evolution") or {}
                                problems.append(f'{label} says the track is "{m.group(0)}", but the field\'s best lap '
                                                f"is {evo or 'unknown'}"
                                                + (f" ({e['change_s']:+}s, last 5 min vs the 5 before)"
                                                   if e.get("change_s") is not None else " (not measurable yet)"))

            for m in re.finditer(RAINING, low):
                if not clause_negated(m.start()) and not w.get("raining"):
                    problems.append(f'{label} says "{m.group(0)}", but the latest weather reading has no rain')
            for m in re.finditer(RAIN_STOPPED, low):
                if w.get("raining"):
                    problems.append(f'{label} says "{m.group(0)}", but it is raining in the latest reading')

            for pat, want in ((ON_TRACK, {"out lap", "on a lap"}), (IN_GARAGE, {"garage"})):
                for m in re.finditer(pat, low):
                    if clause_negated(m.start()):
                        continue
                    code = subject(m.start())
                    st = _view_status(self.car(code))
                    if code and st and st not in want:
                        problems.append(f'{label} says {code} is "{m.group(0)}", but {code} is '
                                        f"{STATUS_WORDS.get(st, st)}")

            if cut:
                for pat, outside in ((DROP_ZONE, True), (SAFE, False)):
                    for m in re.finditer(pat, low):
                        if clause_negated(m.start()):
                            continue
                        code = subject(m.start())
                        car = self.car(code)
                        if not car:
                            continue
                        is_out = car["position"] > cut or not car["best"]
                        if outside != is_out:
                            problems.append(f'{label} says {code} is "{m.group(0)}", but {code} is P{car["position"]}'
                                            f" with the cut at P{cut}" + ("" if car["best"] else " (no time yet)"))
            if mentions:
                carry = mentions[-1][1]
        return problems


async def load_view(call: Callable[[str, dict], Awaitable[dict]], session_kind: str) -> RaceView:
    """The right data view for the session that's loaded."""
    return await (QualiView.load(call) if session_kind == "qualifying" else RaceView.load(call))
