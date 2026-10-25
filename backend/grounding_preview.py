"""Grounding for the AI's pre-session read.

The read is the one place a prediction is allowed ("NOR could take the lead
at the start"), and it's labelled as the AI's prediction in the UI. Its
numbers still never come from the model: it cites grid slots, qualifying
results, recent form and the championship as facts, and code fills them in
from get_session_preview (pre-session data only) and the season tools. A
fact about this session itself (positions, tires, gaps, race control) is
refused here, because before the start it doesn't exist.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from grounding import (
    PLACEHOLDER, TEAM_KINDS, Fact, FactRef, RaceView, Unresolvable, check_labels, clause_mode,
    resolve_season_or_record,
)

ORDINAL = (r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|"
           r"fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|last)\b"
           r"(?=\s+(?:in the (?:drivers'? |constructors'? )?(?:championship|standings)|on the grid|place|position))")
NUMBER_WORDS = r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)"
# "top six", "two wins", "three podiums": counts written as words
COUNTED = (rf"\btop {NUMBER_WORDS}\b|\b{NUMBER_WORDS}\s+(?:straight\s+|consecutive\s+)?"
           r"(?:points?|places?|positions?|wins?|victories|podiums?|poles?|races?|grands prix|retirements?)\b")
# a claim that a driver leads, not a reference to "the championship leader" (whoever that is)
LEADS = (r"\bleads? the (?:drivers'? )?(?:championship|standings)\b"
         r"|\b(?:is|as|remains|stays|arrives as)\s+(?:the\s+)?(?:championship|title|points)\s+leader\b")
ORDINAL_N = {w: i + 1 for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
                                             "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
                                             "nineteenth twentieth".split())}
COUNT_N = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
           "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20}
# a claim that someone HAS won ("won last time", "his recent win"); "turn pole into a win" is a forecast
WON = (r"\bwon\b|\b(?:recent|last|latest|previous|first|maiden)\s+(?:grand prix\s+)?(?:win|victory)\b"
       r"|\b(?:a|his|her|their)\s+(?:win|victory)\s+(?:in|at|last|this season)\b|\bwinning (?:run|streak|form)\b")
RETIRED = r"\b(?:retired|retirement|dnf|did not finish|crashed out)\b"

PREVIEW_KINDS = {"grid_slot", "quali_result", "recent_form"}
SEASON_KINDS = TEAM_KINDS | {"drivers_leader", "constructors_leader"}

PREVIEW_FACT_HELP = """\
Pre-session fact kinds (driver = three-letter code):
- grid_slot(driver): where they start, e.g. "VER starts from P1" (race and sprint only)
- quali_result(driver): how they qualified, e.g. "VER qualified P1 with a 1:24.892 in Q3" (Grand Prix only)
- recent_form(driver): their last three Grands Prix, e.g. "PIA's last three Grands Prix: P2, P4, P1"
- drivers_leader, constructors_leader, champ_position(driver or team), champ_gap(driver or team), season_record(driver or team): the championship going into this weekend
Nothing from the session itself exists yet: no positions, tires, gaps or race control."""


class PreviewView(RaceView):
    """Facts for the pre-session read, from get_session_preview and the season tools."""

    preview: dict

    @classmethod
    async def load(cls, call: Callable[[str, dict], Awaitable[dict]]) -> "PreviewView":
        v = cls(call)
        v.preview = await call("get_session_preview", {})
        v.state = {"cars": [], "current_lap": 0}
        await v.load_names()
        return v

    def entrant(self, code: str | None) -> dict | None:
        code = (code or "").strip().upper()
        return next((d for d in self.preview.get("drivers", []) if d["code"] == code), None)

    async def prepare(self, templates: dict[str, str], my_code: str | None) -> None:
        return None

    def check(self, template: str, facts: dict, label: str, my_code: str | None) -> list[str]:
        return check_labels(template, facts, label) + self._claims(template, facts, label, my_code)

    def _claims(self, template: str, facts: dict, label: str, my_code: str | None) -> list[str]:
        """Standings and results written as words are checked against the pre-session data."""
        problems: list[str] = []
        plain = PLACEHOLDER.sub(lambda m: f" {facts[m.group(1)].driver or ''} " if m.group(1) in facts else " ", template)
        low = plain.lower()
        # the cited facts' own text, sentence by sentence: a place or count written as words is fine
        # when a fact cited in that same sentence contains it ("{ver_form} shows ... a second place")
        sentences = [(mm.start(), mm.end()) for mm in re.finditer(r"[^.;!?]+[.;!?]?", template)]

        def backed(at: int, word: str) -> bool:
            for a, b in sentences:
                seg = template[a:b]
                seg_plain = PLACEHOLDER.sub(lambda m: f" {facts[m.group(1)].driver or ''} " if m.group(1) in facts else " ", seg)
                if word in seg_plain.lower():
                    texts = " ".join(facts[k].text for k in PLACEHOLDER.findall(seg) if k in facts)
                    n = ORDINAL_N.get(word.split()[-1]) or next((v for k, v in COUNT_N.items() if re.search(rf"\b{k}\b", word)), None)
                    if n is None:
                        return False
                    if word.split()[-1] in ORDINAL_N:
                        return bool(re.search(rf"\bP{n}\b", texts))
                    if re.search(r"\bwins?\b|victor", word):
                        return texts.count("P1") >= n or bool(re.search(rf"\({n} wins?\)", texts))
                    if re.search(r"podium", word):
                        return len(re.findall(r"\bP[123]\b", texts)) >= n
                    return bool(re.search(rf"\b{n}\b", texts))
            return False

        for m in list(re.finditer(ORDINAL, low)) + list(re.finditer(COUNTED, low)):
            if re.search(r"\b(?:last|past|previous|recent)\s+$", low[:m.start()]):
                continue  # "his last three races": the form window, not a claim
            if backed(m.start(), m.group(0)):
                continue
            problems.append(f'{label} types a standing or a count as words ("{m.group(0)}"); cite it with a fact '
                            "placeholder (champ_position, grid_slot, quali_result or recent_form)")
        mentions = sorted([(m.start(), c) for c in entrant_codes(self) for m in re.finditer(rf"\b{c}\b", plain)]
                          + [(m.start(), c) for n, c in getattr(self, "names", {}).items()
                             for m in re.finditer(rf"\b{re.escape(n)}\b", low)])
        default = re.match(r"note for ([A-Z]{3})", label)

        def who(at: int) -> str | None:
            before = [c for i, c in mentions if i <= at]
            after = [c for i, c in mentions if i > at]
            return before[-1] if before else (default.group(1) if default else after[0] if after else None)

        drivers = {d["code"]: d for d in self.preview.get("drivers", [])}
        leader = next((c for c, d in drivers.items() if (d.get("championship") or {}).get("position") == 1), None)
        for pat, what in ((LEADS, "leads"), (WON, "won"), (RETIRED, "retired")):
            for m in re.finditer(pat, low):
                if clause_mode(low, m.start()):
                    continue  # hypothetical ("could win") or negated: a forecast, not a claim about the past
                code = who(m.start())
                d = drivers.get(code or "")
                if not d:
                    continue
                if what == "leads" and leader and code != leader:
                    problems.append(f"{label} says {code} leads the championship, but {leader} leads going in")
                if what == "won" and re.search(r"\b(?:won|victory|victories)\b|\bwins?\b(?! the)", m.group(0)) \
                        and not re.search(r"\b(?:could|might|may|can|would|will|to|chance|bid|hunt|first)\s+(?:\w+\s+)?$", low[:m.start()]) \
                        and not any(r["finish"] == "P1" for r in d.get("last_races", [])) \
                        and not (d.get("championship") or {}).get("wins"):
                    problems.append(f'{label} says {code} has won ("{m.group(0)}"), but {code} has no wins this season')
                if what == "retired" and not any(r["finish"] in ("DNF", "DNS", "DSQ") for r in d.get("last_races", [])):
                    problems.append(f'{label} says {code} retired ("{m.group(0)}"), but {code} has no retirement in '
                                    "their last three Grands Prix")
        return problems

    async def resolve(self, ref: FactRef) -> Fact:
        k = ref.kind
        if k in SEASON_KINDS:
            return await resolve_season_or_record(ref, self)
        if k not in PREVIEW_KINDS:
            raise Unresolvable(f"{ref.key}: {k} is about the session itself, which hasn't started; use "
                               f"{', '.join(sorted(PREVIEW_KINDS | SEASON_KINDS))}")
        d = self.entrant(ref.driver)
        if d is None:
            raise Unresolvable(f"{ref.key}: '{ref.driver}' isn't entered in this session")
        code = d["code"]
        c = self.lead_in(code)

        def fact(text: str, value: Any = None, shorts: list | None = None) -> Fact:
            return Fact(key=ref.key, kind=k, driver=code, text=text, value=value, shorts=shorts or [])

        session = self.preview.get("session")
        if k == "grid_slot":
            if session == "qualifying":
                raise Unresolvable(f"{ref.key}: qualifying has no grid; it decides the grid")
            if d.get("pit_lane_start"):
                return fact(f"{code} starts from the pit lane", 0)
            return fact(f"{code} starts from P{d['grid']}", d["grid"],
                        [(rf"\b{c}\s+(?:starts?|lines? up)(?:\s+(?:from|in|on))?", f"P{d['grid']}")])
        if k == "quali_result":
            q = d.get("qualifying")
            if not q:
                raise Unresolvable(f"{ref.key}: no Grand Prix qualifying result before this session"
                                   + (" (at a sprint weekend it comes after the sprint)" if session == "sprint" else ""))
            out = f", out in {q['out_in']}" if q.get("out_in") and q.get("best") else ""
            rest = f"P{q['position']} with a {q['best']} in {q['best_in']}{out}" if q.get("best") else f"P{q['position']}"
            return fact(f"{code} qualified {rest}", q["position"], [(rf"\b{c}\s+qualified", rest)])
        if k == "recent_form":
            races = d.get("last_races") or []
            if not races:
                return fact(f"{code} has no Grand Prix results yet this season")
            finishes = ", ".join(r["finish"] for r in races)
            return fact(f"{code}'s last {'three' if len(races) == 3 else len(races)} Grands Prix: {finishes} "
                        f"(most recent first)", [r["finish"] for r in races])
        raise Unresolvable(f"{ref.key}: unknown kind {k}")


def entrant_codes(v: PreviewView) -> set[str]:
    return {d["code"] for d in v.preview.get("drivers", [])}


def mentions_unknown(text: str, v: PreviewView) -> list[str]:
    """Three-letter codes in the agent's own words must be entrants."""
    codes = entrant_codes(v)
    return sorted({m for m in re.findall(r"\b[A-Z]{3}\b", text) if m not in codes and m not in {"DRS", "FIA", "DNF"}})


def available_facts(preview: dict) -> dict[str, int]:
    """How many drivers each pre-session fact kind can be cited for."""
    ds = preview.get("drivers", [])
    return {"grid_slot": sum(1 for d in ds if d.get("grid") or d.get("pit_lane_start")),
            "quali_result": sum(1 for d in ds if d.get("qualifying")),
            "champ_position": sum(1 for d in ds if d.get("championship")),
            "recent_form": sum(1 for d in ds if d.get("last_races"))}


def quick_read(preview: dict) -> dict:
    """A short read built in code from the pre-session data, for when the AI's read can't be grounded
    (or there's nothing for it to work with, like the first qualifying of the season). Facts only, no forecast."""
    ds = preview.get("drivers", [])
    me = next((d for d in ds if d.get("is_my_driver")), None)
    session = preview.get("session")
    on_grid = sorted((d for d in ds if d.get("grid")), key=lambda d: d["grid"])
    leader = next((d for d in ds if (d.get("championship") or {}).get("position") == 1), None)
    form = lambda d: ", ".join(r["finish"] for r in d["last_races"])  # noqa: E731
    stories, watch = [], []
    if on_grid:
        front = on_grid[:2]
        stories.append({"title": "The front row", "text": " and ".join(f"{d['code']} starts from P{d['grid']}" for d in front)
                        + ". Track position is valuable, so the start and the first stops are the moments to watch."})
    if leader:
        c = leader["championship"]
        stories.append({"title": "The championship", "text": f"{leader['code']} leads the drivers' championship on "
                        f"{c['points']:g} points going into this weekend."})
    if me:
        bits = []
        if me.get("pit_lane_start"):
            bits.append(f"{me['code']} starts from the pit lane")
        elif me.get("grid"):
            bits.append(f"{me['code']} starts from P{me['grid']}")
        if me.get("qualifying"):
            q = me["qualifying"]
            bits.append(f"qualified P{q['position']}" + (f" with a {q['best']} in {q['best_in']}" if q.get("best") else ""))
        if me.get("championship") and me["championship"].get("position"):
            bits.append(f"P{me['championship']['position']} in the championship going in")
        if me.get("last_races"):
            bits.append(f"last three Grands Prix: {form(me)} (most recent first)")
        text = (", ".join(bits) + ".") if bits else (
            f"{me['code']} lines up for {'qualifying' if session == 'qualifying' else 'the start'}: "
            + ("the first session of the season, so there's no form to go on yet." if not ds or not any(d.get("last_races") for d in ds)
               else "watch how they get on."))
        stories.append({"title": f"Your driver, {me['code']}", "text": text[0].upper() + text[1:]})
        watch.append({"code": me["code"], "why": "The driver you're following."})
    for d in on_grid[:2]:
        if not me or d["code"] != me["code"]:
            watch.append({"code": d["code"], "why": f"Starts from P{d['grid']}, at the front."})
    if leader and all(w["code"] != leader["code"] for w in watch):
        watch.append({"code": leader["code"], "why": "Leads the championship going in."})
    first = not any(d.get("last_races") for d in ds) and not leader
    headline = ("The season starts here: no form or championship to go on yet, so everyone starts level." if first
                else f"{on_grid[0]['code']} starts from pole: can anyone take it from them?" if on_grid
                else f"Who sets the pace in {'qualifying' if session == 'qualifying' else 'this session'}?")
    return {"headline": headline, "storylines": stories, "watch": watch[:3], "cited": [], "quick": True}
