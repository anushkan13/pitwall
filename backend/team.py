"""The Pitwall agent team.

Same pattern as hw5: PydanticAI agents on gpt-6-luna through Portkey with
reasoning effort "none", one MCP connection per team run, a delegate_to tool
with a depth cap and a per-run delegation budget, and usage limits per agent.

    Race Engineer  talks to the user and coordinates. No data tools; it
                   delegates strategy questions to Strategy.
    Strategy       reads our car through the MCP race tools, always consults
                   Rival Watch, and returns BOX / STAY_OUT with a one-line
                   reason.
    Rival Watch    a specialist on the cars fighting ours: who just pitted,
                   who is in undercut range, who is closing, where we would
                   rejoin.

Strategy and Rival Watch cite numbers only as structured facts that code
fills in and labels (grounding.py), and their wording is checked against
the data's trends. The Engineer may only repeat numbers Strategy gave it.
    Grader         after the flag only: reads the real final result and
                   writes the scorecard's verdict.

Who may see what is enforced by the MCP tools (no future laps for anyone;
get_final_result refuses until the race is over) and, on top of that, by
which tools each agent is given.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset, StdioTransport
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RunUsage, UsageLimits

import re

import mcp_client
import user_db
from grounding import FACT_HELP, Fact, FactRef, RaceView, check_future, ground
from grounding_quali import QualiView, load_view
from llm import MODEL_NAME, MODEL_SETTINGS, build_model
from pricing import LONG_CONTEXT_THRESHOLD, summarize

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

AgentName = Literal["engineer", "strategy", "rival_watch", "grader", "historian"]

# Tools per agent. The race tools refuse future laps on their own; this keeps
# each agent to the reads its job needs.
RACE_TOOLS = {"get_race_state", "get_driver_laps", "get_tire_info", "get_pit_log"}
QUALI_TOOLS = {"get_quali_state", "get_quali_driver", "get_quali_rivals"}
# Qualifying mode swaps these in; the agents and the checks are the same ones.
QUALI_MODE_TOOLS: dict[str, set[str]] = {
    "strategy": {"get_quali_state", "get_quali_driver"},
    "rival_watch": QUALI_TOOLS,
    "grader": {"get_quali_final", "get_quali_state", "get_quali_driver"},
}
QUALI_CALLS = {"garage": {"GO_OUT", "WAIT"}, "out lap": {"CONTINUE", "ABORT"}, "on a lap": {"CONTINUE", "ABORT"}}
QUALI_TIRES = {"WET", "INTERMEDIATE", "SOFT", "MEDIUM", "HARD"}
TOOLS: dict[str, set[str]] = {
    "strategy": RACE_TOOLS,
    "rival_watch": RACE_TOOLS | {"get_rivals"},
    "grader": {"get_final_result", "get_race_state", "get_driver_laps", "get_pit_log"},
    # Ask AI: the same clamped race tools, the season up to this race, and the rules file.
    # Never get_final_result.
    "historian": RACE_TOOLS | QUALI_TOOLS | {"get_rivals", "get_lap_records", "get_championship", "get_season_summary",
                               "lookup_rules", "list_rules", "get_rule"},
}
# Who may delegate to whom.
CAN_ASK: dict[str, set[str]] = {"engineer": {"strategy"}, "strategy": {"rival_watch"}}

LIMITS = {
    # requests include grounding retries (up to GROUNDING_RETRIES), so leave room for them
    "engineer": UsageLimits(request_limit=8, tool_calls_limit=4),
    "strategy": UsageLimits(request_limit=16, tool_calls_limit=10),
    "rival_watch": UsageLimits(request_limit=14, tool_calls_limit=10),
    "grader": UsageLimits(request_limit=10, tool_calls_limit=10),
    "historian": UsageLimits(request_limit=8, tool_calls_limit=8),
}

# Engineer -> Strategy -> Rival Watch is depth 2; nothing goes deeper.
MAX_DELEGATION_DEPTH = 2
MAX_DELEGATIONS = 4

# Grounding failures send the answer back; give the agents room to fix them.
GROUNDING_RETRIES = 4

NUMBER = re.compile(r"\d+(?:\.\d+)?")


# --------------------------------------------------------------------------
# Outputs
#
# Strategy and Rival Watch never type numbers. They list the facts they want
# to cite (grounding.FactRef) and write sentences with {key} placeholders;
# grounding.py fills in the numbers and labels from the data and checks the
# wording. What they write is a draft; the team assembles the final, grounded
# answer from the draft plus the resolved facts.
# --------------------------------------------------------------------------

SENTENCE_HELP = ("One line. No digits: every number must come from a {key} placeholder that names one "
                 "of your facts.")


class RivalNoteDraft(BaseModel):
    code: str = Field(description="Three-letter code of a car in get_rivals.")
    note_template: str = Field(description="A few words on this rival, with {key} placeholders. " + SENTENCE_HELP)


class RivalDraft(BaseModel):
    facts: list[FactRef] = Field(description="Every fact you cite. " + FACT_HELP)
    summary_template: str = Field(description="What matters most about rivals for a box/stay-out call now. "
                                              + SENTENCE_HELP)
    rivals: list[RivalNoteDraft] = Field(description="The rivals that matter, nearest first.")
    undercut_threat_from: str | None = Field(None, description="Code of a car BEHIND, in undercut range.")
    undercut_chance_on: str | None = Field(None, description="Code of a car AHEAD, in undercut range.")


class StrategyDraft(BaseModel):
    call: Literal["BOX", "STAY_OUT"] = Field(description="Box at the end of the next lap, or stay out.")
    facts: list[FactRef] = Field(description="Every fact you cite. " + FACT_HELP)
    reason_template: str = Field(description="Why. At most about 25 words. " + SENTENCE_HELP)
    rival_template: str = Field(description="The rival situation (from Rival Watch) that shaped the call. "
                                            + SENTENCE_HELP)
    tire_to_fit: str | None = Field(None, description="Compound to fit if boxing, e.g. MEDIUM.")


class RivalNote(BaseModel):
    code: str
    relation: Literal["ahead", "behind"]
    gap_s: float | None
    note: str


class RivalReport(BaseModel):
    summary: str
    rivals: list[RivalNote]
    undercut_threat_from: str | None
    undercut_chance_on: str | None
    rejoin_behind_if_box: list[str]
    cited: list[Fact]


class SprintStrategyDraft(StrategyDraft):
    call: Literal["BOX", "DEFEND", "PUSH", "MANAGE"] = Field(
        description="BOX at the end of the next lap (with tire_to_fit), or stay out and DEFEND, PUSH or MANAGE.")


class StrategyCall(BaseModel):
    call: Literal["BOX", "STAY_OUT", "DEFEND", "PUSH", "MANAGE"]
    reason: str
    rival_factor: str
    tire_to_fit: str | None
    cited: list[Fact]
    # our car, straight from get_race_state
    lap: int
    position: int | None
    compound: str | None
    tire_age: int | None
    gap_ahead_s: float | None
    gap_behind_s: float | None


class HistorianDraft(BaseModel):
    kind: Literal["live_race", "season", "rules", "unknown"] = Field(
        description="Which kind of question this is; 'unknown' when it can't be grounded.")
    facts: list[FactRef] = Field(default_factory=list, description="Race/season facts you cite. " + FACT_HELP)
    answer_template: str = Field("", description="The answer, 1-3 sentences, with {key} placeholders for "
                                                 "race/season numbers. Empty when kind is 'unknown'.")
    rule_ids: list[str] = Field(default_factory=list, description="Ids of the rules entries you used.")
    unknown_reason: str | None = Field(None, description="When kind is 'unknown': why it can't be answered "
                                                         "from the data or the rules file. No numbers.")


class HistorianAnswer(BaseModel):
    kind: str
    answer: str
    cited: list[Fact]
    rules: list[dict]          # [{id, title}]
    tool_calls: list[dict] = []
    rejections: list[dict] = []
    cleanups: list[dict] = []
    usage: dict | None = None


class QualiStrategyDraft(BaseModel):
    call: Literal["GO_OUT", "WAIT", "CONTINUE", "ABORT"] = Field(
        description="In the garage: GO_OUT or WAIT. On track: CONTINUE the lap or ABORT it.")
    tire_to_fit: str | None = Field(None, description="With GO_OUT: WET, INTERMEDIATE, SOFT, MEDIUM or HARD.")
    facts: list[FactRef] = Field(description="Every fact you cite. Use the qualifying (q_) kinds.")
    reason_template: str = Field(description="Why. At most about 25 words. " + SENTENCE_HELP)
    rival_template: str = Field(description="The rival situation (from Rival Watch) that shaped the call. "
                                            + SENTENCE_HELP)


class QualiCall(BaseModel):
    call: Literal["GO_OUT", "WAIT", "CONTINUE", "ABORT"]
    tire_to_fit: str | None
    reason: str
    rival_factor: str
    cited: list[Fact]
    # our car and the session, straight from get_quali_state / get_quali_rivals
    segment: str | None
    time_left: str | None
    position: int | None
    status: str | None
    best: str | None
    compound: str | None
    tire_age: int | None
    cut_margin_s: float | None


class EngineerReply(BaseModel):
    message: str = Field(description="What you say to the user: 1-3 short sentences.")


class GraderVerdict(BaseModel):
    headline: str = Field(description="One line on how the user's race went against the real team.")
    biggest_call_lap: int = Field(description="The lap the biggest call was for (one of the decision laps).")
    biggest_call: str = Field(description="One line on the biggest call of the race, with real numbers.")
    your_calls: str = Field(description="One line on the user's calls overall.")
    strategy_calls: str = Field(description="One line on the Strategy agent's calls overall.")


@dataclass
class TeamDeps:
    agent: AgentName = "engineer"
    depth: int = 0
    chain: tuple[str, ...] = ("engineer",)
    delegations_used: list[int] = field(default_factory=lambda: [0])
    strategy_calls: list[Any] = field(default_factory=list)  # StrategyCall (race) or QualiCall
    rival_reports: list[RivalReport] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)
    rejections: list[dict] = field(default_factory=list)  # grounding-check failures, kept for display
    cleanups: list[dict] = field(default_factory=list)    # fixed in code (trims, repeated words)
    usage_log: list[dict] = field(default_factory=list)   # real token usage of every agent run in this answer
    decision_laps: set[int] = field(default_factory=set)  # grader only
    answers: list[HistorianAnswer] = field(default_factory=list)  # historian only

    def child(self, callee: AgentName) -> "TeamDeps":
        return TeamDeps(agent=callee, depth=self.depth + 1, chain=self.chain + (callee,),
                        delegations_used=self.delegations_used, strategy_calls=self.strategy_calls,
                        rival_reports=self.rival_reports, tool_log=self.tool_log,
                        rejections=self.rejections, cleanups=self.cleanups, usage_log=self.usage_log,
                        decision_laps=self.decision_laps)


@dataclass
class TeamResult:
    message: str
    strategy: StrategyCall | None
    rivals: RivalReport | None
    tool_log: list[dict]
    rejections: list[dict]
    cleanups: list[dict]
    usage: dict | None = None


class PitwallTeam:
    """Use as an async context manager so the MCP server starts once per run:

        async with PitwallTeam() as team:
            result = await team.decide("Car ahead just boxed", lap=10)
    """

    def __init__(self, mode: str = "race") -> None:
        self.mode = mode
        if mcp_client.TRANSPORT == "inprocess":
            client = mcp_client.inprocess_server()
        else:
            command, args = mcp_client.server_command()
            client = StdioTransport(command, args, cwd=str(mcp_client.PROJECT_DIR))
        self.toolset = MCPToolset(client, process_tool_call=self._log_tool_call)
        model = build_model()

        def make(name: AgentName, output_type: type, tools: bool = True, retries: int = 2) -> Agent:
            allowed = (QUALI_MODE_TOOLS.get(name) if mode == "qualifying" else None) or TOOLS.get(name, set())
            suffix = {"qualifying": "_quali", "sprint": "_sprint"}.get(mode, "")
            prompt = PROMPT_DIR / f"{name}{suffix}.md"
            if not suffix or not prompt.exists():
                prompt = PROMPT_DIR / f"{name}.md"
            return Agent(
                model,
                deps_type=TeamDeps,
                system_prompt=prompt.read_text(),
                toolsets=[self.toolset.filtered(lambda ctx, tool, allowed=allowed: tool.name in allowed)]
                if tools and allowed else [],
                output_type=output_type,
                model_settings=MODEL_SETTINGS,
                retries=retries,
                name=name,
            )

        self.agents: dict[str, Agent] = {
            "engineer": make("engineer", EngineerReply, tools=False),
            "strategy": make("strategy", {"qualifying": QualiStrategyDraft, "sprint": SprintStrategyDraft}.get(
                mode, StrategyDraft), retries=GROUNDING_RETRIES),
            "rival_watch": make("rival_watch", RivalDraft, retries=GROUNDING_RETRIES),
            "grader": make("grader", GraderVerdict),
            "historian": make("historian", HistorianDraft, retries=GROUNDING_RETRIES),
        }
        self.agents["historian"].output_validator(self._ground_historian)
        self.agents["strategy"].output_validator(
            self._ground_strategy_quali if mode == "qualifying" else self._ground_strategy)
        self.agents["rival_watch"].output_validator(self._ground_rivals)
        self.agents["engineer"].output_validator(self._check_engineer)
        self.agents["grader"].output_validator(self._check_verdict)

        for caller, callees in CAN_ASK.items():
            self._add_delegate_tool(caller, callees)

    def _add_delegate_tool(self, caller: str, callees: set[str]) -> None:
        names = " or ".join(f'"{c}"' for c in sorted(callees))

        async def delegate_to(ctx: RunContext[TeamDeps], agent_name: str, task: str) -> dict[str, Any]:
            return await self._delegate(ctx, agent_name, task, callees)

        delegate_to.__doc__ = f"""Hand a question to another agent and get their answer back.

            Args:
                agent_name: The agent to ask: {names}.
                task: The specific question you want answered.
            """
        self.agents[caller].tool(delegate_to)

    # -- tool-call log (shown with each decision as the grounding trail) ---

    async def _log_tool_call(self, ctx: RunContext[TeamDeps], call_tool, name: str, args: dict) -> Any:
        result = await call_tool(name, args)
        refused = isinstance(result, dict) and result.get("refused")
        ctx.deps.tool_log.append({"agent": ctx.deps.agent, "tool": name, "args": args,
                                  **({"refused": result.get("reason")} if refused else {})})
        return result

    async def _view(self, mode: str | None = None) -> RaceView:
        """The data view for this team's session (race and sprint share the race view)."""
        return await load_view(self._direct, mode or self.mode)

    async def _direct(self, tool: str, args: dict | None = None) -> dict:
        data = await self.toolset.direct_call_tool(tool, args or {})
        return json.loads(data) if isinstance(data, str) else data

    # -- grounding checks ---------------------------------------------------

    def _reject(self, ctx: RunContext[TeamDeps], draft: dict, problems: list[str]) -> None:
        ctx.deps.rejections.append({"agent": ctx.deps.agent, "problems": problems, "draft": draft})
        raise ModelRetry("Not accepted, fix and resend: " + " | ".join(problems))

    async def _ground_rivals(self, ctx: RunContext[TeamDeps], out: RivalDraft) -> RivalDraft:
        v = await self._view()
        my_code = (v.me or {}).get("code")
        known = {r["code"]: r for r in v.rivals.get("rivals", [])}
        templates = {"summary": out.summary_template}
        problems = []
        for i, r in enumerate(out.rivals):
            if r.code.upper() not in known:
                tool = "get_quali_rivals" if self.mode == "qualifying" else "get_rivals"
                problems.append(f"{r.code} is not in {tool}' list ({', '.join(known) or 'none'})")
            templates[f"note for {r.code}"] = r.note_template
        undercut = () if self.mode == "qualifying" else (
            (out.undercut_threat_from, "behind"), (out.undercut_chance_on, "ahead"))
        for code, need in undercut:
            if not code:
                continue
            real = known.get(code.upper())
            if real is None or real["relation"] != need or not real["in_undercut_range"]:
                problems.append(f"{code} is not a car {need} of us in undercut range "
                                f"({real['relation'] if real else 'not a rival'}, "
                                f"in range: {real['in_undercut_range'] if real else '-'})")
        rendered, facts, errs, cleanups = await ground(out.facts, templates, v, my_code,
                                                       limits={"summary": 50, "*": 22})
        problems += errs
        if problems:
            self._reject(ctx, out.model_dump(), problems)
        report = RivalReport(
            summary=rendered["summary"],
            rivals=[RivalNote(code=r.code.upper(), relation=known[r.code.upper()]["relation"],
                              gap_s=known[r.code.upper()]["gap_s"], note=rendered[f"note for {r.code}"])
                    for r in out.rivals],
            undercut_threat_from=out.undercut_threat_from if self.mode != "qualifying" else None,
            undercut_chance_on=out.undercut_chance_on if self.mode != "qualifying" else None,
            rejoin_behind_if_box=[c["code"] for c in v.rivals.get("if_we_box_now_we_rejoin_behind", [])],
            cited=facts,
        )
        ctx.deps.cleanups += [{"agent": "rival_watch", "note": n} for n in cleanups]
        ctx.deps.rival_reports.append(report)
        return out

    async def _ground_strategy(self, ctx: RunContext[TeamDeps], out: StrategyDraft) -> StrategyDraft:
        v = await RaceView.load(self._direct)
        me = v.me
        problems = []
        if not ctx.deps.rival_reports:
            problems.append('you have not consulted Rival Watch; call delegate_to("rival_watch", ...) first')
        rendered, facts, errs, cleanups = await ground(
            out.facts, {"reason": out.reason_template, "rival_factor": out.rival_template}, v,
            (me or {}).get("code"), limits={"reason": 35, "rival_factor": 35})
        problems += errs
        if out.call == "BOX" and not out.tire_to_fit:
            problems.append("say which compound to fit in tire_to_fit")
        if problems:
            self._reject(ctx, out.model_dump(), problems)
        ctx.deps.cleanups += [{"agent": "strategy", "note": n} for n in cleanups]
        ctx.deps.strategy_calls.append(StrategyCall(
            call=out.call, reason=rendered["reason"], rival_factor=rendered["rival_factor"],
            tire_to_fit=out.tire_to_fit, cited=facts, lap=v.state["current_lap"],
            position=(me or {}).get("position"), compound=(me or {}).get("compound"),
            tire_age=(me or {}).get("tire_age"), gap_ahead_s=(me or {}).get("gap_ahead_s"),
            gap_behind_s=(me or {}).get("gap_behind_s")))
        return out

    async def _ground_strategy_quali(self, ctx: RunContext[TeamDeps], out: QualiStrategyDraft) -> QualiStrategyDraft:
        v = await self._view("qualifying")
        me = v.me
        problems = []
        if not ctx.deps.rival_reports:
            problems.append('you have not consulted Rival Watch; call delegate_to("rival_watch", ...) first')
        status = (me or {}).get("status")
        allowed = QUALI_CALLS.get(status or "", set())
        if me and out.call not in allowed:
            problems.append(f"{me['code']} is {status}; the choices are {' or '.join(sorted(allowed))}, not {out.call}")
        if out.call == "GO_OUT" and (out.tire_to_fit or "").upper() not in QUALI_TIRES:
            problems.append(f"with GO_OUT set tire_to_fit to one of {', '.join(sorted(QUALI_TIRES))}")
        rendered, facts, errs, cleanups = await ground(
            out.facts, {"reason": out.reason_template, "rival_factor": out.rival_template}, v,
            (me or {}).get("code"), limits={"reason": 35, "rival_factor": 35})
        problems += errs
        if problems:
            self._reject(ctx, out.model_dump(), problems)
        ctx.deps.cleanups += [{"agent": "strategy", "note": n} for n in cleanups]
        cut = v.qrivals.get("cut") or {}
        ctx.deps.strategy_calls.append(QualiCall(
            call=out.call, tire_to_fit=(out.tire_to_fit or "").upper() or None, reason=rendered["reason"],
            rival_factor=rendered["rival_factor"], cited=facts, segment=v.quali.get("segment"),
            time_left=v.quali.get("scheduled_time_left"), position=(me or {}).get("position"), status=status,
            best=(me or {}).get("best"), compound=(me or {}).get("compound"), tire_age=(me or {}).get("tire_age"),
            cut_margin_s=cut.get("my_margin_s")))
        return out

    async def _ground_historian(self, ctx: RunContext[TeamDeps], out: HistorianDraft) -> HistorianDraft:
        problems: list[str] = []
        if out.kind == "unknown":
            reason = (out.unknown_reason or "").strip() or "I can't ground an answer to that in the data I have."
            if re.search(r"\d", reason):
                problems.append("unknown_reason contains a number; keep it to words")
            problems += check_future(reason, "unknown_reason")
            if problems:
                self._reject(ctx, out.model_dump(), problems)
            ctx.deps.answers.append(HistorianAnswer(kind="unknown", answer=f"I don't know. {reason}", cited=[], rules=[]))
            return out

        # rules entries: must exist, and must have been read this run
        rules, allowed = [], set()
        for rid in out.rule_ids:
            r = await self._direct("get_rule", {"rule_id": rid})
            if r.get("refused"):
                problems.append(f"rule_ids has '{rid}', which is not in the rules file")
            else:
                rules.append({"id": r["id"], "title": r["title"]})
                allowed |= set(NUMBER.findall(r["text"]))
        if out.rule_ids and not any(t["tool"] in ("lookup_rules", "get_rule", "list_rules") for t in ctx.deps.tool_log):
            problems.append("you cited rules entries without reading them; call lookup_rules first")
        if not out.facts and not out.rule_ids:
            problems.append("an answer needs at least one cited fact or rules entry; if you can't ground it, "
                            "use kind 'unknown'")
        if not out.answer_template.strip():
            problems.append("answer_template is empty")

        v = await self._view(user_db.get_clock().get("session_kind") or "race")
        data = v.quali if isinstance(v, QualiView) else v.state
        if data.get("refused"):
            problems.append(f"no session data: {data['reason']} Use kind 'unknown'.")
            self._reject(ctx, out.model_dump(), problems)
        for name in ("drivers",):  # lets the championship-lead claim check run
            await v.champ(name)
        rendered, facts, errs, cleanups = await ground(
            out.facts, {"answer": out.answer_template}, v, (v.me or {}).get("code"),
            limits={"answer": 60}, allowed_numbers=allowed, check_predictions=True)
        problems += errs
        if problems:
            self._reject(ctx, out.model_dump(), problems)
        ctx.deps.cleanups += [{"agent": "historian", "note": n} for n in cleanups]
        ctx.deps.answers.append(HistorianAnswer(kind=out.kind, answer=rendered["answer"], cited=facts, rules=rules))
        return out

    async def _check_engineer(self, ctx: RunContext[TeamDeps], out: EngineerReply) -> EngineerReply:
        """The Engineer may only repeat numbers Strategy gave it, and its trend words are checked too."""
        if not ctx.deps.strategy_calls:
            return out
        s = ctx.deps.strategy_calls[-1]
        allowed = set(NUMBER.findall(s.reason + " " + s.rival_factor))
        allowed |= ({str(s.lap), str(s.lap + 1)} if isinstance(s, StrategyCall) else {"1", "2", "3"})  # laps / Q1-Q3
        problems = [f'the number {n} is not in the strategy call; quote the strategy reason instead'
                    for n in NUMBER.findall(out.message) if n not in allowed]
        v = await self._view()
        my_code = (v.me or {}).get("code")
        await v.prepare({"message": out.message}, my_code)
        problems += v.check(out.message, {}, "message", my_code)
        if problems:
            self._reject(ctx, out.model_dump(), problems)
        return out

    async def _check_verdict(self, ctx: RunContext[TeamDeps], out: GraderVerdict) -> GraderVerdict:
        if ctx.deps.decision_laps and out.biggest_call_lap not in ctx.deps.decision_laps:
            raise ModelRetry(f"biggest_call_lap must be one of the decision laps: "
                             f"{sorted(ctx.deps.decision_laps)}.")
        return out

    # -- every agent run goes through here, so its real token usage is recorded --

    async def _run(self, name: str, prompt: str, deps: TeamDeps):
        """Run one agent and record the tokens it really used, as reported by the
        provider, even if the run fails partway (retries and failed runs cost too)."""
        used = RunUsage()
        result = None
        try:
            result = await self.agents[name].run(prompt, deps=deps, usage_limits=LIMITS[name], usage=used)
            return result
        finally:
            per_request = []
            if result is not None:
                per_request = [m.usage.input_tokens for m in result.all_messages()
                               if isinstance(m, ModelResponse) and m.usage]
            deps.usage_log.append({
                "agent": name, "requests": used.requests, "input_tokens": used.input_tokens,
                "cached_input_tokens": used.cache_read_tokens, "output_tokens": used.output_tokens,
                "long_context": any(t > LONG_CONTEXT_THRESHOLD for t in per_request),
                "completed": result is not None,
            })

    def _usage(self, deps: TeamDeps) -> dict:
        return summarize(MODEL_NAME, deps.usage_log)

    # -- delegation -----------------------------------------------------------

    async def _delegate(self, ctx: RunContext[TeamDeps], agent_name: str, task: str,
                        allowed: set[str]) -> dict[str, Any]:
        deps = ctx.deps
        callee = agent_name.strip().lower().replace(" ", "_").replace("-", "_")
        if callee not in allowed:
            return {"refused": True, "reason": f"'{agent_name}' is not an agent you can ask. "
                                               f"Use: {', '.join(sorted(allowed))}."}
        if deps.depth + 1 > MAX_DELEGATION_DEPTH:
            return {"refused": True, "reason": f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) reached "
                                               f"via {' -> '.join(deps.chain)}. Answer with what you have."}
        if deps.delegations_used[0] >= MAX_DELEGATIONS:
            return {"refused": True, "reason": f"This run has used its {MAX_DELEGATIONS} delegations. "
                                               "Answer with what you already have."}
        deps.delegations_used[0] += 1
        try:
            result = await self._run(callee, task, deps.child(callee))
        except Exception as exc:
            return {"refused": True, "reason": f"{callee} could not answer: {exc}"}
        # hand back the grounded answer the validator assembled, not the draft
        if callee == "strategy" and deps.strategy_calls:
            return deps.strategy_calls[-1].model_dump(include={"call", "reason", "rival_factor", "tire_to_fit"})
        if callee == "rival_watch" and deps.rival_reports:
            return deps.rival_reports[-1].model_dump(exclude={"cited"})
        return result.output.model_dump()

    # -- entry points ---------------------------------------------------------

    async def decide(self, trigger: str, lap: int, when: str | None = None) -> TeamResult:
        """A decision point: the Engineer gets the Strategy call and briefs the user.

        lap: laps completed (race). when: a session-time label (qualifying)."""
        deps = TeamDeps()
        if self.mode == "qualifying":
            prompt = (f"Qualifying decision point ({when}): {trigger}. Get the strategy call for our driver: go out "
                      "now or wait, and on which tire; or, if we're on track, continue or abort the lap. Then brief me.")
            if trigger.endswith("is about to go green"):
                prompt += (f" {trigger.split()[0]} starts in a second, so the tools still show the break before it; "
                           "every car is in the garage.")
            fallback = f"Qualifying, {when}: {trigger}. What's the call for our driver?"
        elif self.mode == "sprint":
            prompt = (f"Sprint decision point at the end of lap {lap}: {trigger}. Get the strategy call for our "
                      "driver for the next lap: box (and for which tire), or stay out and defend, push or manage "
                      "the tires. Then brief me.")
            fallback = f"Sprint, end of lap {lap}: {trigger}. Box (which tire), defend, push or manage?"
        else:
            prompt = (f"Decision point at the end of lap {lap}: {trigger}. "
                      "Get the strategy call for our driver for the next lap, then brief me.")
            fallback = f"Box or stay out at the end of lap {lap + 1}? Trigger: {trigger}."
        reply = await self._run("engineer", prompt, deps)
        if not deps.strategy_calls:
            # The Engineer is told to delegate; if it didn't, ask Strategy directly
            # so a decision point never ends without a grounded call.
            await self._run("strategy", fallback, deps.child("strategy"))
        return TeamResult(reply.output.message, deps.strategy_calls[-1],
                          deps.rival_reports[-1] if deps.rival_reports else None, deps.tool_log,
                          deps.rejections, deps.cleanups, self._usage(deps))

    async def chat(self, message: str) -> TeamResult:
        deps = TeamDeps()
        reply = await self._run("engineer", message, deps)
        return TeamResult(reply.output.message, deps.strategy_calls[-1] if deps.strategy_calls else None,
                          deps.rival_reports[-1] if deps.rival_reports else None, deps.tool_log,
                          deps.rejections, deps.cleanups, self._usage(deps))

    async def ask(self, question: str) -> HistorianAnswer:
        """Ask AI: one free-form question to the Historian."""
        deps = TeamDeps(agent="historian", chain=("historian",))
        try:
            await self._run("historian", question, deps)
            answer = deps.answers[-1]
        except Exception as exc:
            # Out of retries or limits: never fall back to an ungrounded answer.
            answer = HistorianAnswer(kind="unknown", cited=[], rules=[],
                                     answer=f"I don't know. I couldn't ground an answer in the data "
                                            f"({exc.__class__.__name__}).")
        return answer.model_copy(update={"tool_calls": deps.tool_log, "rejections": deps.rejections,
                                         "usage": self._usage(deps),
                                         "cleanups": deps.cleanups})

    async def grade(self, decisions_table: str, stats: str, decision_laps: set[int]) -> tuple[GraderVerdict, list]:
        deps = TeamDeps(agent="grader", chain=("grader",), decision_laps=decision_laps)
        if self.mode == "qualifying":
            prompt = ("Qualifying is over. Here are the decision points (# = decision number; use it as "
                      f"biggest_call_lap):\n\n{decisions_table}\n\nMatch rates (already computed):\n{stats}\n\n"
                      "Grade the session.")
        elif self.mode == "sprint":
            prompt = ("The sprint is over. Here are the decision points (lap = the lap the call was for). Whether "
                      "the real driver boxed, and on what tire, is from the data; DEFEND / PUSH / MANAGE for the real "
                      "driver is inferred from the following laps, so treat it as softer evidence.\n\n"
                      f"{decisions_table}\n\nMatch rates (already computed):\n{stats}\n\nGrade the sprint.")
        else:
            prompt = ("The race is over. Here are the decision points (lap = the lap the call was for):\n\n"
                      f"{decisions_table}\n\nMatch rates (already computed):\n{stats}\n\n"
                      "Grade the race.")
        result = await self._run("grader", prompt, deps)
        return result.output, deps.tool_log, self._usage(deps)

    async def __aenter__(self) -> "PitwallTeam":
        await self.toolset.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.toolset.__aexit__(*exc)
