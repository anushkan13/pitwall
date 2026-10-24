# Pitwall

Working name. Skeleton only — no race logic yet.

Same architecture as hw5 (Campus Customs):

| Layer | Folder | What it is |
|---|---|---|
| MCP server | `mcp_server/` | FastMCP server over stdio. Race tools that only answer for laps already run. |
| Agent team | `backend/team.py` | PydanticAI Race Engineer, Strategy, Rival Watch, Grader and Historian on `gpt-6-luna` through Portkey. |
| Backend | `backend/` | FastAPI on port 8000: sim clock, decision points, API. Reads races only through MCP. |
| Frontend | `frontend/` | React + Vite + TypeScript on port 5173: race strategy mode. |
| Data | `data/` | `races.db`, the read-only race database, built by `scripts/bake_race.py`; `knowledge/f1_rules.md`, the curated rules and history file. |
| Scripts | `scripts/` | `bake_race.py`: bakes a season or one round into `data/races.db`. |
| User data | `data/pitwall_user.db` | Sim clock, decisions, scorecards. Created by the backend; gitignored. |
| Output | `output/` | Run artifacts go here later. |

## Setup (once)

From this folder (`Pitwall/`):

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### API key

The backend looks for `.env` here first, then one level up (the course folder's
shared `.env`). To use a project-local one:

```bash
cp .env.example .env
```

and fill in `PORTKEY_API_KEY`. `.env` is gitignored.

## Race data

Bake a whole season, or one round, into `data/races.db`:

```bash
.venv/bin/python scripts/bake_race.py 2025
```

```bash
.venv/bin/python scripts/bake_race.py 2025 12
```

Each round gets its race, qualifying, and (on sprint weekends) sprint, plus
OpenF1's weather feed for each session. Lap-level data only, no telemetry.

The script fetches everything first, then writes all rounds in one
transaction, so a failed run never leaves a partial season. Every request is
cached in `.cache/`; on a rate limit it pauses, backs off and retries, and if
it still can't finish it stops without writing. Re-running resumes from the
cache. Afterwards it self-checks every session and prints one line per round,
then lists any flagged sessions:

```
R02 Chinese GP | Race 56L 20d 25ps tires 100% wx ✓ | Quali 20d 314 laps tires 100% wx ✓ | Sprint 19L 20d 1ps tires 100% wx ✓
```

`56L 20d 25ps` = laps, drivers, pit stops (pit-lane passes counted apart);
`wx ✓ WET` = weather present and rain fell while cars were on track,
`rain-before-start` = rain only beforehand; `⚠` = a check failed (details
listed below the table).

**Known gaps in the 2025 data** (both flagged on every run):

- **Azerbaijan qualifying (R17) has no lap rows.** OpenF1 has nothing for it:
  re-querying the right session (`session_key` 9900) returns no laps, though
  that weekend's practice and race laps, and this session's stints, pit and
  position feeds, are all there. Results (Q1–Q3 times), tire stints and
  weather are baked; only the per-lap rows are missing.
  The bake has a FastF1 fallback for exactly this case: if `F1_USERNAME` and
  `F1_PASSWORD` are set in `.env` it signs in, pulls that session's laps, and
  stores them in the same shape (`sessions.lap_source = 'fastf1'`). FastF1
  3.9 requires the account to have an **active F1TV subscription**; a free
  account is refused. No login is configured, so the gap stands. The login
  is optional, bake-time only, gitignored with `.env`, and never read by the
  app; FastF1 keeps its sign-in token outside the repo
  (`~/Library/Application Support/fastf1/`), and pulled laps are cached in
  `.cache/fallback/` so later bakes don't need the login again.
- **São Paulo qualifying (R21)**: Jolpica's results leave out Bortoleto.

**Sources.** FastF1 for the event schedule and sprint-weekend flag;
[Jolpica](https://api.jolpi.ca) for race/sprint/qualifying results, grid, race
laps (position and lap time), race pit stops, and standings;
[OpenF1](https://openf1.org) for sprint and qualifying laps, lap timestamps,
tire compound and age, sprint pit stops, stationary times, race control
messages, and weather.

**Tire stints** come from OpenF1, which places stint boundaries 0–1 laps
from the recorded stop depending on the circuit, sometimes many laps off, and
opens a new "stint" on every trip down the pit lane. The script lines each
tire change up with a recorded stop and folds same-set "stints" back together.
A recorded stop with no tire change is stored as a pit-lane pass
(`pitstops.pit_lane_pass = 1`): the safety car leading the field through the
pit lane (Australia and Canada 2025, taken from race control), or a
drive-through penalty.
FastF1's own lap timing needs an F1 account login (F1's live-timing archive
returns 401 without one), so it isn't used for laps.

**Schema** (`data/races.db`; `race_id = season * 100 + round`,
`session_id = race_id * 10 + 1 race / 2 qualifying / 3 sprint`):

| Table | One row per |
|---|---|
| `races` | weekend: name, circuit, date, sprint weekend or not |
| `sessions` | race / qualifying / sprint: start time, total laps, where the laps came from |
| `drivers`, `teams` | driver / constructor |
| `results` | race or sprint entrant: team, car number, grid, finish, status, points |
| `qualifying_results` | qualifier: position, Q1/Q2/Q3 times |
| `laps` | driver-lap: start time, position, lap time, cumulative time, gap to car ahead and to leader, compound, tire age, stint, pit-in / pit-out |
| `pitstops` | stop: lap, time, pit-lane time, stationary time, pit-lane pass flag |
| `weather` | reading (about one a minute): air and track temp, rainfall, humidity, wind |
| `driver_standings_before`, `constructor_standings_before` | standings after the previous round |

The `lap_weather` view joins each lap to the latest weather reading at its
start, so a scenario can tell when a session went wet or started drying.

The file is read-only (chmod 444, and readers should open it with
`?mode=ro`); only the bake script writes it. User data such as accounts and
scorecards goes in a separate database, never this one.

## Race strategy mode

Pick a driver, start the race, and the clock steps through it lap by lap.
At a decision point the clock pauses, the Strategy agent recommends **box
at the end of the next lap** or **stay out** with a one-line reason, and you
make the call. Once that lap has run, the log shows what the real driver did.
At the chequered flag the Grader scores the race.

Default race: 2025 British GP (round 12), wet to dry.

**Decision points** (`backend/sim.py`) fire when the car directly ahead or
behind boxes, when three or more cars box on one lap, when rain starts or
stops, or when your tires reach a compound-specific age (soft 14, medium 20,
hard 28, inter 18, wet 15 laps, then every 6). They are at least 3 laps apart
(weather excepted) and never in the last 3 laps. "Ask strategy now" forces one.

**The no-future rule** is enforced by the MCP tools, not the prompts. The
backend is the only writer of the sim clock (`data/pitwall_user.db`); the
MCP server reads it on every call. No tool takes a race id or a way to move
the clock. Asking for a lap after the current one is refused; ranges are
clipped. A stop on the current lap shows the car went in, but its durations
and new tires stay hidden until the next lap completes, because the call
being made is for that next lap. The race tools never read the final
classification; only `get_final_result` does, and it refuses until the clock
reaches the last lap.

| MCP tool | Returns |
|---|---|
| `get_race_state(lap?)` | Running order at the end of a lap: gaps ahead/behind/to leader, compound, tire age, stops, pit in/out, weather. Lap 0 = grid. |
| `get_driver_laps(driver, from_lap?, to_lap?)` | One driver's laps so far. |
| `get_tire_info(driver)` | Stints so far with pace drop-off; current set; compounds used. |
| `get_pit_log()` | Every stop so far with tires before/after and times. |
| `get_rivals(window?)` | Cars within 2 places or 3 s of ours: gap now vs 3 laps ago, tires vs ours, last stop, undercut range; pit-loss estimate and who we'd rejoin behind if we boxed now. |
| `get_final_result()` | Real classification, our driver's stops and position by lap. **Refused until the race is over.** |
| `get_lap_records()` | Fastest lap so far; fastest stops (stationary and pit lane) among stops already completed. |
| `get_championship(kind)` | Drivers' or constructors' standings going into this race. |
| `get_season_summary(driver? \| team?)` | Standing going into this race, plus Grand Prix wins, podiums, best finish and retirements in earlier rounds. |
| `lookup_rules(query)`, `get_rule(id)`, `list_rules()` | Entries from `data/knowledge/f1_rules.md`. |
| `list_races()`, `get_entry_list(race_id)` | Pre-race info only, for picking a race and driver. |

**Agents** (`backend/team.py`, same pattern as hw5): reasoning effort `none`,
usage limits per agent, a `delegate_to` tool (Engineer → Strategy → Rival
Watch) with a depth cap of 2 and four delegations per run. Each agent only
gets the tools its job needs:

| Agent | Tools | Sees |
|---|---|---|
| Race Engineer | none (delegates) | what Strategy tells it |
| Strategy | race state, driver laps, tire info, pit log; asks Rival Watch | current lap and earlier |
| Rival Watch | the same + `get_rivals` | current lap and earlier |
| Grader | `get_final_result` + race tools | the full race, after the flag only |
| Historian | race tools, `get_rivals`, lap records, championship, season summary, rules file | current lap and earlier; season before this race; the rules file |


- **Race Engineer** talks to you and coordinates. It has no data tools; it
  delegates strategy questions and briefs you on the radio.
- **Strategy** reads our car through the race tools, must consult Rival
  Watch, and returns BOX / STAY_OUT, a one-line reason, a one-line rival
  factor and the tire to fit. The UI shows Strategy's own grounded output,
  not the Engineer's paraphrase.
- **Rival Watch** reports who just pitted near us, who is in undercut range
  ahead or behind, who is closing, and who we'd rejoin behind. It may only
  name cars in `get_rivals`, and an undercut threat or chance must really be
  behind or ahead of us and in range.
- **Grader** runs once the race ends. The backend computes the match rates
  (you vs the real team, Strategy vs the real team, you vs Strategy) over
  every decision point; the Grader reads the real result and writes the
  headline, the biggest call of the race (which must be one of the decision
  laps), and a line each on your calls and Strategy's. If it fails, **Grade
  again** reruns it.

## Qualifying mode

Pick **Qualifying** in the session menu (default: 2025 Las Vegas GP, round
22, a wet qualifying), choose a driver, **Load qualifying**, **Start
qualifying**. Same clock, crew, tools and grounding as the race; what
changes is the clock and the decision.

**Clock.** Session time instead of laps (`sim_clock.session_time_utc`),
stepped at ×20 to ×160. Q1 / Q2 / Q3 are found from the running so far (a
5-minute lull in lap starts ends a segment); time left is against the
scheduled 18 / 15 / 12 minutes, since a future red flag can't be known.
Eliminations are worked out from lap times at the moment the next segment
starts. For Las Vegas they match the official result exactly. Deleted lap
times aren't in the data, so in general this is the lap-time order.

**Decisions.** With our car in the garage: **go out now** (on WET,
INTERMEDIATE or a slick) or **wait**. On track: **continue** the lap or
**abort** it. Decision points fire when a segment goes green, when rain
starts or stops, when the first car of a segment goes out on a different
kind of tire (wets → inters), when scheduled time left passes 6:00 and 3:00
with us in the garage, and when the field's best lap is clearly coming down.
That last one is the drying-track tradeoff: waiting buys a faster track,
but rain can come back and time runs out.

**What the real team did** is read back 2:30 of session time later: did
the driver go out, and on what tire; or did the lap in progress produce a
representative time (within 5% of their best so far) or was it abandoned.

**The crew, reused:**

| Agent | In qualifying |
|---|---|
| Strategy | `get_quali_state`, `get_quali_driver`; must consult Rival Watch; returns GO_OUT (+ tire) / WAIT / CONTINUE / ABORT. A call that doesn't fit where our car is (e.g. GO_OUT while on track) is sent back. |
| Rival Watch | `get_quali_rivals`: who's around us in the order, the cut line and our margin, who's on track on what tires, who improved in the last 3 minutes. |
| Historian | the qualifying tools during qualifying (the race tools refuse), plus season and rules as before. |
| Grader | `get_quali_final`, refused until the session is over: the official classification and our driver's best lap (time, tire, when). Scores your calls and Strategy's against the real team's, on the call and on the tire. |

**Qualifying MCP tools** (all clamped to session time; the race tools refuse
during qualifying so Saturday can't read Sunday): `get_quali_state`,
`get_quali_driver`, `get_quali_rivals`, `get_quali_final` (after the flag).
The backend also uses `get_session_info` for the session's start and end; no
agent is given it.

**Grounding, same rules.** Qualifying facts (`grounding_quali.py`) are
code-filled with their labels:

| kind | renders as |
|---|---|
| `q_session`, `q_time_left` | Q2, 12:57 into a scheduled 15:00 / about 2:03 of the scheduled 15:00 left in Q2 |
| `q_position`, `q_best` | NOR P6 in Q2 on 1:52.137 (WET) |
| `q_cut` | the Q2 cut is P10 (ALO, 1:52.871); NOR is 0.85s inside it |
| `q_status` | NOR on a timed lap on WET tires |
| `q_gap_to` | RUS 0.383s quicker than us in Q2 |
| `q_evolution` | the field's best lap 1.159s faster in the last 5 minutes than in the 5 before |
| `q_recent_best`, `q_on_track` | 15 cars on track (1 on INTERMEDIATE, 14 on WET) |
| `weather`, `rain_trend`, `track_temp_trend` | track dry, no rain for 25.5 minutes, 12.5°C |

Claim checks: "drying" (not raining, no rain for 3+ minutes), "track getting
faster / improving / times coming down" (the field's best lap really got
faster), "still raining" / "rain has stopped", "on track" / "in the garage",
and "in the drop zone" / "safe" are all checked against the data. Track
evolution only counts representative laps from the current segment: no out
laps, nothing slower than 107% of the segment's best. During a break the
facts say there's no running rather than counting down.

## Sprint mode

Pick **Sprint** in the session menu (default: 2025 Miami GP, round 6, a
sprint that started wet on intermediates and dried, with the switch to slicks
between laps 10 and 14), choose a driver, **Load sprint**, **Start sprint**.

A sprint is a short race, so it **is** race mode: the same lap clock and loop,
the same MCP race tools (they read the sprint session when a sprint is
loaded, with the same no-future limits), the same crew and the same
grounding. What changes:

- **The call**: **box** at the end of the next lap and for which tire
  (intermediate, soft, medium, hard), or stay out and **defend**, **push**,
  or **manage** the tires.
- **Decision points** come sooner and closer together (tire checks from 8-14
  laps of age, at least 2 laps apart, none in the last 2 laps), and a car
  within a second ahead or behind is a decision point of its own.
- **What the real driver did**: whether they boxed at the end of the next
  lap, and for what tire, is read from the data. Defend / push / manage isn't
  in any timing feed, so it's **inferred** from the next three laps and
  labeled that way everywhere: DEFEND if a car was within 1.0s behind and they
  kept the place, PUSH if they gained a place or took 0.5s+ out of the car
  ahead, otherwise MANAGE.
- **Scorecard**: as in the race, your calls vs Strategy's vs the real driver's
  on box-or-stay-out, plus tire matches when you both boxed, and defend /
  push / manage against the inferred mode (shown separately, since it's
  softer evidence). The Grader is told the same.

## Ask AI (the Historian)

The Ask AI box sends free-form questions to the Historian, which answers
three kinds, each from its own source:

| Kind | Example | Source |
|---|---|---|
| Live race | "Who has the fastest pit stop so far?" | race tools and `get_lap_records`, current lap and earlier |
| Season | "How has McLaren done this year?" | `get_championship`, `get_season_summary`: rounds before this race only |
| Rules and history | "What's DRS?" | `data/knowledge/f1_rules.md` via `lookup_rules` |

Answers go through the same grounding as Strategy (below). Race and season
numbers are cited as facts and filled in by code. Rules numbers may be typed
only if they appear in a cited rules entry. Claims that someone leads the
championship are checked against the standings.

When it can't ground an answer, it says **"I don't know."** and why:

- **Predictions** about the live race ("who wins this race?", "will Norris
  pit again?") are never grounded, since the data stops at the current lap. A
  prediction anywhere in an answer ("X will win", "is likely to finish") is
  rejected; talking about one ("can't say who will win") is fine.
- **Topics the rules file doesn't cover** ("tell me about Ayrton Senna") are
  answered with "I don't know" rather than from general knowledge.
- **Out of retries**: if it still can't produce a grounded answer after 4
  tries, the box shows "I don't know" instead of anything unchecked.

The rules file is short on purpose: about 20 entries (DRS, undercut and
overcut, Safety Car and VSC, red flags, tire compounds, the two-compound
rule, points, sprints, qualifying, penalties, track limits, blue flags, parc
fermé, classification, gap and interval, the championships, and two history
entries). Add an entry under `## id: Title` with a `keywords:` line to extend
it.

## Favorites and theming

The **Favorites** bar (guest mode: nothing is saved) picks a favorite team and
driver. The app takes one accent color from the team's livery (Ferrari red,
McLaren papaya, Mercedes teal, Red Bull navy, Aston Martin green, Williams
blue, Alpine blue, Racing Bulls blue, Haas grey, Sauber green) for the title
bar, buttons, the decision panel and your row in the timing table, and the
favorite driver becomes "my driver" whenever they're in the chosen session.
Colors live in `frontend/src/Favorites.tsx`.

## Token meter

Tick **Token meter (dev view)** to show, under every AI answer (Strategy
decisions, Ask AI answers and the Grader's scorecard), the tokens it really
used and what it cost; each decision's cost also shows in the decision log.

- **Real numbers.** Every agent run (`PitwallTeam._run`) gets its own
  PydanticAI usage counter, filled from the provider's `usage` block on each
  response. An answer's total is the sum over every run behind it:
  delegated Rival Watch and Strategy runs, grounding retries, and runs that
  failed partway (those cost tokens too). Breakdown per run is in the meter.
- **Input / cached input / output.** Cached input is the part of the input
  the provider served from its prompt cache (our system prompts are long, so
  this is often half or more). It's billed as a slice of input.
- **Cost** (`backend/pricing.py`): gpt-6-luna at $0.10 input, $0.01 cached
  input, $0.50 output per 1M tokens, standard tier, from the course's
  Lecture 11 `PRICING.md` (retrieved 2026-10-06). A request over 272K input
  tokens would be repriced at the long-context rate; Pitwall's are a few
  thousand.
- Portkey's response cache isn't in play (`x-portkey-cache-status: MISS`),
  so every answer is a fresh, billed call.

Typical costs seen: a race decision (Engineer + Strategy + Rival Watch,
about 9 requests) ≈ $0.0017; an Ask AI answer ≈ $0.0004; a Grader verdict
≈ $0.0006.

## Grounding: describing the data correctly

The no-future rule decides what an agent can see. A second guard
(`backend/grounding.py`) decides whether it describes what it saw correctly.

**Numbers are cited, never typed.** Strategy and Rival Watch list the facts
they want to cite as `{key, kind, driver}` and write their sentences with
`{key}` placeholders. Code looks each fact up through the same no-future MCP
tools and writes it as a phrase that carries its own label:

| kind | renders as |
|---|---|
| `gap_ahead` / `gap_behind` | NOR 1.366s behind VER / NOR 8.511s ahead of HAM |
| `gap_to` | VER 1.366s ahead of us |
| `gap_trend` | gap to VER shrinking 0.757s/lap over the last 3 laps |
| `tire` | NOR on 10-lap-old INTERMEDIATE tires |
| `pace_trend` | NOR's lap times improving by 17.629s (last 3 laps vs the 3 before) |
| `last_stop` / `pit_time` | HAM last stopped on lap 11 / HAM's lap-11 stop took 28.917s in the pit lane |
| `position`, `laps_remaining`, `pit_loss`, `rejoin_behind`, `weather`, `boxed_this_lap` | NOR in P3, 42 laps to go, … |
| `fastest_lap`, `fastest_stop`, `fastest_pit_lane` | TSU's lap-11 stop, 2.1s stationary (28.544s in the pit lane), the fastest stop so far |
| `drivers_leader`, `constructors_leader`, `champ_position`, `champ_gap`, `season_record` (driver or team) | PIA leads the drivers' championship on 216 points going into this race (after round 11) |

A gap can only ever read as a gap to a named car, and a pit time as a stop on
a named lap. Any digit typed outside a placeholder is rejected, so is a
fact that can't be resolved yet (e.g. a pit-time before any stop has
finished), and so is a placeholder that doesn't match a cited fact. Our own
car's position, compound, tire age and gaps are filled from
`get_race_state` by code, not by the agent.

**The words around the numbers are checked too:**

- **Pace claims** ("losing pace", "degrading", "dropping off" / "improving")
  must match the driver's recent pace from `get_tire_info`: the median of
  the last 3 clean laps against the 3 before, steady within 0.3s.
- **Gap claims** ("closing", "catching" / "pulling away", "dropping back")
  must match the gap trend from `get_rivals`, steady within 0.1 s/lap.
- **"X pitted / boxed"** requires X to have stopped.
- **Labels next to a placeholder** must fit it: "pit time {x}" is rejected
  when x is a gap.
- **Denials are checked too.** Saying a trend isn't there is held to the
  same data as saying it is: "isn't losing pace" and "pace is steady" when
  lap times are worsening, "not closing" and "the gap is steady" when it's
  shrinking, and in qualifying "no evidence of drying", "conditions
  unchanged" and "track still wet" once rain earlier in the session has
  stopped (the same test that allows a "drying" claim), or "the track isn't
  getting faster" when the field's best lap is coming down. Hypotheticals
  ("if it keeps drying") aren't claims and aren't checked.
- Each claim is checked against the driver named nearest before it ("our",
  "we", "us" = our driver); with no name in the sentence it carries over from
  the one before, and in a note about a rival it defaults to that rival.

Those failures, and only those, go back to the agent as a retry with the
exact problem (up to 4 times): a fact that can't be resolved yet, a typed
number, a mislabel, or a wrong pace, gap or pit claim. Every rejected attempt
is kept with the decision and shown under **Grounding check sent back N
attempts**; the accepted answer's facts are under **Cited facts**.

**Style is fixed in code, never retried** (shown under **Cleaned up in
code**):

- **Word limits** (reason and rival factor 35, Rival Watch summary 50, notes
  22; a fact counts as one word): the sentence is cut back to whole clauses,
  in order, so a number is never separated from its label. Agents are told to
  put the most important clause first.
- **Short forms after a lead-in**: each fact phrase labels itself, so when
  the words right before it already say the label, code uses the fact's
  short form instead: "PIA is {gap}" → "PIA is 3.7s ahead of us", "behind
  {rejoin}" → "behind STR, HUL", "costs {loss}" → "costs about 29.1s", "the
  track is {weather}" → "the track is dry, 22.8°C". A short form that drops
  a driver's code (a last stop, a pace trend) is only used when the lead-in
  names that same driver, so a number can't move to the wrong car. The label
  check still runs on the agent's own wording.
- **Repeated words**: an adjacent repeated word or phrase is collapsed
  ("track track dry" → "track dry", "going into this race going into this
  race" → once). Any other repeat a word or two apart is left as written and
  only noted. The Engineer may only repeat numbers that are in the
Strategy call (plus the lap numbers), and its trend words get the same
checks.

## Run

Each in its own terminal, from `Pitwall/`.

**Backend** (port 8000):

```bash
.venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

- `GET /api/health`: API is up, model name, whether a Portkey key was found
- `GET /api/sim/state`: clock, running order, pending decision, decision log
- `POST /api/sim/setup` `{race_id, driver_id}`, `/start`, `/pause`,
  `/speed` `{seconds_per_lap}`, `/ask`, `/call` `{call: BOX | STAY_OUT}`
- `POST /api/engineer/chat` `{message}`: ask the Race Engineer anything

No `--reload`: the sim clock runs inside the server, and a reload would start
a second one.

**Frontend** (port 5173):

```bash
cd frontend && npm run dev
```

Open http://localhost:5173, pick a driver (British GP is preselected), click
**Load race**, then **Start race**. It reads `VITE_API_BASE` (default
`http://localhost:8000`).

**MCP server**: you don't need to start it yourself. The backend keeps one
connection open for its own reads, and each agent run spawns its own over
stdio, both from `mcp.json`. `.mcp.json` lets Claude Code connect to it too. To run it on its own:

```bash
.venv/bin/python mcp_server/server.py
```

It then waits for an MCP client on stdin/stdout (Ctrl+C to stop).

## Stopping

Ctrl+C in each terminal. If something is left behind, kill the whole process,
not just whatever holds the port:

```bash
pkill -f "uvicorn main:app --app-dir backend"
```
