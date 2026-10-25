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
LEADS = r"\bleads? the (?:drivers'? )?(?:championship|standings)\b|\bchampionship leader\b|\btitle leader\b"
WON = r"\b(?:won|wins?|winning|victory|victories)\b"
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
        for m in list(re.finditer(ORDINAL, low)) + list(re.finditer(COUNTED, low)):
            if re.search(r"\b(?:last|past|previous|recent)\s+$", low[:m.start()]):
                continue  # "his last three races": the form window, not a claim
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
            out = f", out in {q['out_in']}" if q.get("out_in") else ""
            return fact(f"{code} qualified P{q['position']} with a {q['best']} in {q['best_in']}{out}", q["position"],
                        [(rf"\b{c}\s+qualified", f"P{q['position']} with a {q['best']} in {q['best_in']}{out}")])
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
