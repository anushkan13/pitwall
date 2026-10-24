import { useEffect, useState } from "react";
import { api, type AskAnswer, type Call, type Decision, type EntryList, type Rate, type Scorecard, type Session,
         type SimState, type Tire } from "./api";
import { QualiView } from "./Quali";
import { costCell, TokenMeter } from "./TokenMeter";
import { applyAccent, Favorites, type Fav } from "./Favorites";

const fmtGap = (s?: number | null) => (s == null ? "" : `+${s.toFixed(3)}`);
const callLabel = (c?: string | null, tire?: string | null) =>
  !c ? "–" : c === "BOX" ? (tire ? `BOX → ${tire}` : "BOX") : c === "STAY_OUT" ? "STAY OUT" : c;
const SPRINT_TIRES: Tire[] = ["INTERMEDIATE", "SOFT", "MEDIUM", "HARD"];
const realLabel = (d: Decision, sprint: boolean) => {
  if (!sprint) return d.actual_pitted == null ? "…" : d.actual_pitted ? "BOX" : "STAY OUT";
  const a = d.actual;
  if (!a?.action) return d.actual_pitted === 1 ? "BOX" : "…";
  if (a.action === "BOX") return callLabel("BOX", a.tire);
  if (a.action === "STAY_OUT") return `STAY OUT (${a.mode}, inferred)`;
  return a.action;
};

export default function App() {
  const [races, setRaces] = useState<{ race_id: number; name: string; season: number; round: number }[]>([]);
  const [raceId, setRaceId] = useState<number | null>(null);
  const [session, setSession] = useState<Session>("race");
  const [defaults, setDefaults] = useState<Record<Session, number> | null>(null);
  const [entries, setEntries] = useState<EntryList | null>(null);
  const [driverId, setDriverId] = useState("");
  const [sim, setSim] = useState<SimState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [askInput, setAskInput] = useState("");
  const [answers, setAnswers] = useState<(AskAnswer | { question: string; error: string })[]>([]);
  const [askBusy, setAskBusy] = useState(false);
  const [showTokens, setShowTokens] = useState(false);
  const [fav, setFav] = useState<Fav>({ team: "", driver: "" });
  useEffect(() => { applyAccent(fav.team); }, [fav.team]);
  // the favorite driver becomes "my driver" whenever they're in the chosen session
  useEffect(() => {
    if (fav.driver && entries?.entries.some((e) => e.driver_id === fav.driver)) setDriverId(fav.driver);
  }, [fav.driver, entries]);
  useEffect(() => { document.body.classList.toggle("show-tokens", showTokens); }, [showTokens]);

  const run = (p: Promise<SimState | unknown>) =>
    p.then((s) => {
      setError(null);
      if (s && typeof s === "object" && "clock" in s) setSim(s as SimState);
    }).catch((e: Error) => setError(e.message));

  useEffect(() => {
    // retry until the backend is up (it can start after the page loads)
    let timer: ReturnType<typeof setTimeout>;
    const load = () =>
      api.races().then((r) => {
        setError(null);
        setRaces(r.races);
        setRaceId(r.default_race_id);
        setDefaults({ race: r.default_race_id, qualifying: r.default_quali_race_id, sprint: r.default_sprint_race_id });
      }).catch((e: Error) => {
        setError(`Backend unreachable: ${e.message} (retrying)`);
        timer = setTimeout(load, 2000);
      });
    load();
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (raceId == null) return;
    api.entries(raceId, session === "sprint" ? "sprint" : "race").then((e) => {
      setEntries(e);
      setDriverId((d) => (e.entries.some((x) => x.driver_id === d) ? d : e.entries[0]?.driver_id ?? ""));
    }).catch((e: Error) => setError(e.message));
  }, [raceId, session]);

  useEffect(() => {
    const tick = () => api.state().then(setSim).catch(() => {});
    tick();
    const id = setInterval(tick, 700);
    return () => clearInterval(id);
  }, []);

  const clock = sim?.clock;
  const loaded = clock?.race_id != null;
  const race = sim?.race;
  const pending = sim?.pending;
  const myCode = entries?.entries.find((e) => e.driver_id === clock?.driver_id)?.code ?? clock?.driver_id;
  const over = loaded && !!sim?.over;
  const quali = sim?.mode === "qualifying";
  const sprint = sim?.mode === "sprint";

  const sendAsk = async () => {
    const question = askInput.trim();
    if (!question) return;
    setAskInput("");
    setAskBusy(true);
    try {
      const r = await api.askAI(question);
      setAnswers((a) => [r, ...a]);
    } catch (e) {
      setAnswers((a) => [{ question, error: (e as Error).message }, ...a]);
    } finally {
      setAskBusy(false);
    }
  };

  const askPanel = (
    <section className="panel">
      <h2>Ask AI</h2>
      <input
        value={askInput}
        placeholder="Ask about this race, the season, or the rules…"
        onChange={(e) => setAskInput(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && sendAsk()}
        disabled={!loaded || askBusy}
      />{" "}
      <button onClick={sendAsk} disabled={!loaded || askBusy}>Ask</button>
      {askBusy && <p><em>The Historian is checking the data…</em></p>}
      <div className="chat">
        {answers.map((a, i) => <AskItem key={i} a={a} />)}
      </div>
    </section>
  );

  return (
    <main className="page">
      <h1>Pitwall — {quali ? "Qualifying" : sprint ? "Sprint" : "Race"} Strategy</h1>

      <section className="panel">
        <label>
          Session{" "}
          <select value={session} onChange={(e) => {
            const s = e.target.value as Session;
            setSession(s);
            if (defaults) setRaceId(defaults[s]);
          }}>
            <option value="race">Race</option>
            <option value="qualifying">Qualifying</option>
            <option value="sprint">Sprint</option>
          </select>
        </label>{" "}
        <label>
          Weekend{" "}
          <select value={raceId ?? ""} onChange={(e) => setRaceId(Number(e.target.value))}>
            {races.map((r) => (
              <option key={r.race_id} value={r.race_id}>
                {r.season} R{r.round} · {r.name}
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          My driver{" "}
          <select value={driverId} onChange={(e) => setDriverId(e.target.value)}>
            {entries?.entries.map((e) => (
              <option key={e.driver_id} value={e.driver_id}>
                {e.code} · {e.name} ({e.team}) · P{e.grid === 0 ? "L" : e.grid}
              </option>
            ))}
          </select>
        </label>{" "}
        <button disabled={!raceId || !driverId} onClick={() => run(api.setup(raceId!, driverId, session))}>
          Load {session}
        </button>{" "}
        <label className="dev-toggle">
          <input type="checkbox" checked={showTokens} onChange={(e) => setShowTokens(e.target.checked)} /> Token meter (dev view)
        </label>
      </section>

      {entries && <Favorites entries={entries.entries} fav={fav} onChange={setFav} />}

      {error && <p className="error">{error}</p>}

      {loaded && quali && <QualiView sim={sim!} code={myCode ?? ""} run={run} askPanel={askPanel} />}

      {loaded && !quali && (
        <section className="panel">
          <strong>
            Lap {clock!.current_lap} / {clock!.total_laps}
          </strong>{" "}
          · driving for <strong>{myCode}</strong>
          {race?.weather && (
            <span>
              {" "}· track {race.weather.track_temp_c}°C, air {race.weather.air_temp_c}°C,{" "}
              {race.weather.raining ? "RAINING" : race.weather.rain_in_last_5_min ? "recent rain" : "dry"}
            </span>
          )}
          <div className="controls">
            {clock!.running ? (
              <button onClick={() => run(api.pause())}>Pause</button>
            ) : (
              <button disabled={!!pending || over} onClick={() => run(api.start())}>
                {clock!.current_lap === 0 ? "Start race" : "Resume"}
              </button>
            )}{" "}
            <label>
              Speed{" "}
              <select value={clock!.seconds_per_lap} onChange={(e) => run(api.speed(Number(e.target.value)))}>
                {[0.5, 1, 2, 4, 8].map((s) => (
                  <option key={s} value={s}>{s} s / lap</option>
                ))}
              </select>
            </label>{" "}
            <button disabled={!!pending || clock!.current_lap < 1 || over} onClick={() => run(api.ask())}>
              Ask strategy now
            </button>
            {over && <strong> · Chequered flag</strong>}
          </div>
        </section>
      )}

      {!quali && sim?.scorecard && (
        <ScorecardPanel sc={sim.scorecard} code={myCode ?? ""} sprint={sprint} onRegrade={() => run(api.grade())} />
      )}

      {!quali && pending && (
        <DecisionPanel d={pending} lap={clock!.current_lap} sprint={sprint} onCall={(c, t) => run(api.call(c, t))} />
      )}

      {loaded && !quali && race && (
        <div className="columns">
          <section className="panel">
            <h2>{race.phase === "grid" ? "Starting grid" : `Running order after lap ${race.lap}`}</h2>
            <table>
              <thead>
                <tr>
                  <th>P</th><th>Driver</th><th>Team</th><th>Interval</th><th>Gap</th>
                  <th>Tire</th><th>Age</th><th>Stops</th><th></th>
                </tr>
              </thead>
              <tbody>
                {race.cars.map((c) => (
                  <tr key={c.driver_id} className={c.is_my_driver ? "mine" : ""}>
                    <td>{c.position}</td>
                    <td>{c.code}</td>
                    <td>{c.team}</td>
                    <td>{fmtGap(c.gap_ahead_s)}</td>
                    <td>{fmtGap(c.gap_leader_s)}</td>
                    <td>{c.compound ?? ""}</td>
                    <td>{c.tire_age ?? ""}</td>
                    <td>{c.stops ?? ""}</td>
                    <td>{c.pitted_end_of_lap ? "IN" : c.out_lap ? "OUT" : ""}</td>
                  </tr>
                ))}
                {race.retired?.map((r) => (
                  <tr key={r.driver_id} className="retired">
                    <td>–</td><td>{r.code}</td><td colSpan={7}>out after lap {r.last_lap}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <div>
            <section className="panel">
              <h2>Decisions</h2>
              {sim!.decisions.length === 0 && <p>None yet.</p>}
              <table>
                <thead>
                  <tr><th>For lap</th><th>Why</th><th>Strategy</th><th>You</th><th>Real {myCode}</th><th className="token-cell">Cost</th></tr>
                </thead>
                <tbody>
                  {sim!.decisions.map((d) => (
                    <tr key={d.id}>
                      <td>{d.lap + 1}</td>
                      <td>{d.trigger}</td>
                      <td>{d.status === "error" ? "error" : callLabel(d.strategy_call, d.evidence?.tire_to_fit)}</td>
                      <td>{callLabel(d.user_call, d.user_tire)}</td>
                      <td title={d.actual?.detail}>{realLabel(d, sprint)}</td>
                      <td className="token-cell">{costCell(d.evidence?.usage)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {askPanel}
          </div>
        </div>
      )}
    </main>
  );
}

function DecisionPanel({ d, lap, sprint, onCall }: {
  d: Decision; lap: number; sprint: boolean; onCall: (c: Call, tire?: Tire) => void;
}) {
  return (
    <section className="panel decision">
      <h2>Decision point · end of lap {lap} · call for lap {lap + 1}</h2>
      <p><strong>Why now:</strong> {d.trigger}</p>
      {d.status === "thinking" && <p><em>Strategy is reading the data…</em></p>}
      {d.status === "error" && <p className="error">The team could not make a call: {d.error}</p>}
      {d.status === "ready" && (
        <>
          <p className="call">
            Strategy: <strong>{callLabel(d.strategy_call, sprint ? d.evidence?.tire_to_fit : undefined)}</strong>
            {d.strategy_call === "BOX" && d.evidence?.tire_to_fit ? ` → ${d.evidence.tire_to_fit}` : ""}
          </p>
          <p>{d.strategy_reason}</p>
          {d.evidence?.rival_factor && <p><strong>Rivals:</strong> {d.evidence.rival_factor}</p>}
          {d.evidence?.rival_watch && (
            <div className="rivals">
              <strong>Rival Watch:</strong> {d.evidence.rival_watch.summary}
              <ul>
                {d.evidence.rival_watch.rivals.map((r) => (
                  <li key={r.code}>
                    {r.code} {r.relation} {r.gap_s == null ? "" : `${r.gap_s}s`}: {r.note}
                  </li>
                ))}
              </ul>
              {d.evidence.rival_watch.rejoin_behind_if_box.length > 0 && (
                <span>If we box now we rejoin behind: {d.evidence.rival_watch.rejoin_behind_if_box.join(", ")}</span>
              )}
            </div>
          )}
          {d.evidence && (
            <p className="evidence">
              Checked against the data: P{d.evidence.position}, {d.evidence.compound} {d.evidence.tire_age} laps,
              {" "}{d.evidence.gap_ahead_s == null ? "leading" : `${d.evidence.gap_ahead_s}s to the car ahead`},
              {" "}{d.evidence.gap_behind_s == null ? "nobody behind" : `${d.evidence.gap_behind_s}s to the car behind`}
              {" "}· tools used: {d.evidence.tool_calls.map((t) => `${t.agent}:${t.tool}`).join(", ")}
            </p>
          )}
          {d.evidence?.cited && d.evidence.cited.length > 0 && (
            <details className="evidence">
              <summary>Cited facts ({d.evidence.cited.length}), each filled in from the data</summary>
              <ul>
                {d.evidence.cited.map((f) => (
                  <li key={f.key}><code>{f.kind}</code> {f.text}</li>
                ))}
              </ul>
            </details>
          )}
          {d.evidence?.grounding_rejections && d.evidence.grounding_rejections.length > 0 && (
            <details className="evidence">
              <summary>
                Grounding check sent back {d.evidence.grounding_rejections.length} attempt
                {d.evidence.grounding_rejections.length > 1 ? "s" : ""} before accepting
              </summary>
              <ul>
                {d.evidence.grounding_rejections.flatMap((r, i) =>
                  r.problems.map((p, j) => <li key={`${i}-${j}`}><strong>{r.agent}:</strong> {p}</li>))}
              </ul>
            </details>
          )}
          {d.evidence?.grounding_cleanups && d.evidence.grounding_cleanups.length > 0 && (
            <details className="evidence">
              <summary>Cleaned up in code ({d.evidence.grounding_cleanups.length}), no retry</summary>
              <ul>
                {d.evidence.grounding_cleanups.map((c, i) => (
                  <li key={i}><strong>{c.agent}:</strong> {c.note}</li>
                ))}
              </ul>
            </details>
          )}
          <p><strong>Engineer:</strong> {d.engineer_message}</p>
          <TokenMeter usage={d.evidence?.usage} />
          {sprint ? (
            <>
              <div>
                Box at the end of lap {lap + 1} for:{" "}
                {SPRINT_TIRES.map((t) => (
                  <button key={t} className="big" onClick={() => onCall("BOX", t)}>{t}</button>
                ))}
              </div>
              <div style={{ marginTop: "0.4rem" }}>
                Or stay out and:{" "}
                <button className="big" onClick={() => onCall("DEFEND")}>Defend</button>{" "}
                <button className="big" onClick={() => onCall("PUSH")}>Push</button>{" "}
                <button className="big" onClick={() => onCall("MANAGE")}>Manage tires</button>
              </div>
            </>
          ) : (
            <>
              <button className="big" onClick={() => onCall("BOX")}>Box this lap</button>{" "}
              <button className="big" onClick={() => onCall("STAY_OUT")}>Stay out</button>
            </>
          )}
        </>
      )}
    </section>
  );
}

const pct = (r: Rate) => (r.pct == null ? "–" : `${r.matched}/${r.of} (${r.pct}%)`);

function ScorecardPanel({ sc, code, sprint, onRegrade }: {
  sc: Scorecard; code: string; sprint: boolean; onRegrade: () => void;
}) {
  const extra = sc.stats as unknown as Record<string, Rate | undefined> | null;
  type Row = { lap: number; trigger: string; strategy: string; you: string; real: string; real_detail?: string;
               strategy_ok?: boolean; you_ok?: boolean };
  const rows = (sc.stats?.rows ?? []) as Row[];
  const st = sc.stats;
  const v = sc.verdict;
  return (
    <section className="panel decision">
      <h2>Chequered flag · Scorecard</h2>
      {sc.status === "grading" && <p><em>The Grader is reading the final result…</em></p>}
      {sc.status === "error" && (
        <p className="error">
          Grading failed: {sc.error} <button onClick={onRegrade}>Grade again</button>
        </p>
      )}
      {st?.real_finish && (
        <p>
          Real {code}: started P{st.real_finish.grid}, finished {st.real_finish.pos === "R" ? "DNF" : `P${st.real_finish.pos}`}{" "}
          ({st.real_finish.status}, {st.real_finish.points} pts)
          {st.real_stops && st.real_stops.length > 0 && (
            <> · stops: {st.real_stops.filter((s) => !s.pit_lane_pass).map((s) => `lap ${s.lap} ${s.from_tire ?? "?"}→${s.to_tire ?? "?"}`).join(", ")}</>
          )}
        </p>
      )}
      {v?.headline && <p className="call">{v.headline}</p>}
      {st && st.decision_points > 0 && (
        <table className="score">
          <tbody>
            <tr><td>You matched the real team{sprint ? " (box or stay out)" : ""}</td><td><strong>{pct(st.you_vs_real)}</strong></td></tr>
            <tr><td>Strategy agent matched the real team{sprint ? " (box or stay out)" : ""}</td><td><strong>{pct(st.strategy_vs_real)}</strong></td></tr>
            {sprint && extra?.your_tire_vs_real && <tr><td>Your tire matched, when you both boxed</td><td>{pct(extra.your_tire_vs_real)}</td></tr>}
            {sprint && extra?.strategy_tire_vs_real && <tr><td>Strategy's tire matched, when both boxed</td><td>{pct(extra.strategy_tire_vs_real)}</td></tr>}
            {sprint && extra?.your_mode_vs_inferred && <tr><td>Your defend / push / manage vs what the driver did (inferred)</td><td>{pct(extra.your_mode_vs_inferred)}</td></tr>}
            {sprint && extra?.strategy_mode_vs_inferred && <tr><td>Strategy's defend / push / manage vs inferred</td><td>{pct(extra.strategy_mode_vs_inferred)}</td></tr>}
            <tr><td>You agreed with the Strategy agent</td><td>{pct(st.you_vs_strategy)}</td></tr>
          </tbody>
        </table>
      )}
      {v?.biggest_call && (
        <p><strong>Biggest call (lap {v.biggest_call_lap}):</strong> {v.biggest_call}</p>
      )}
      {v?.your_calls && <p><strong>Your calls:</strong> {v.your_calls}</p>}
      {v?.strategy_calls && <p><strong>Strategy agent:</strong> {v.strategy_calls}</p>}
      <TokenMeter usage={v?.usage} />
      {rows.length > 0 && (
        <table>
          <thead><tr><th>Lap</th><th>Why</th><th>Strategy</th><th>You</th><th>Real</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.lap}>
                <td>{r.lap}</td><td>{r.trigger}</td>
                <td className={(sprint ? r.strategy_ok : r.strategy === r.real) ? "hit" : "miss"}>{sprint ? r.strategy : callLabel(r.strategy)}</td>
                <td className={(sprint ? r.you_ok : r.you === r.real) ? "hit" : "miss"}>{sprint ? r.you : callLabel(r.you)}</td>
                <td title={r.real_detail}>{sprint ? r.real : callLabel(r.real)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

const KIND_LABEL: Record<AskAnswer["kind"], string> = {
  live_race: "this race, so far",
  season: "season, up to this race",
  rules: "rules file",
  unknown: "no grounding",
};

function AskItem({ a }: { a: AskAnswer | { question: string; error: string } }) {
  if ("error" in a) {
    return <div className="ask"><p><strong>Q:</strong> {a.question}</p><p className="error">{a.error}</p></div>;
  }
  return (
    <div className="ask">
      <p><strong>Q:</strong> {a.question} <span className="evidence">(after lap {a.lap})</span></p>
      <p className={a.kind === "unknown" ? "unknown" : ""}>
        <span className="tag">{KIND_LABEL[a.kind]}</span> {a.answer}
      </p>
      <TokenMeter usage={a.usage} />
      {(a.cited.length > 0 || a.rules.length > 0 || a.rejections.length > 0) && (
        <details className="evidence">
          <summary>
            Sources{a.rejections.length > 0 ? ` · grounding check sent back ${a.rejections.length}` : ""}
          </summary>
          <ul>
            {a.cited.map((f) => <li key={f.key}><code>{f.kind}</code> {f.text}</li>)}
            {a.rules.map((r) => <li key={r.id}><code>rules</code> {r.title}</li>)}
            {a.rejections.flatMap((r, i) => r.problems.map((p, j) => <li key={`r${i}-${j}`}>sent back: {p}</li>))}
          </ul>
        </details>
      )}
    </div>
  );
}
