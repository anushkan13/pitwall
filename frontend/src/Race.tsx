import type { ReactNode } from "react";
import { api, type Call, type Decision, type Entry, type Rate, type RaceState, type Scorecard, type SimState,
         type Tire } from "./api";
import { teamColor } from "./Favorites";
import { costCell, TokenMeter } from "./TokenMeter";
import { Icon, More, Seg, TireDot, WeatherBadge } from "./ui";

export const callLabel = (c?: string | null, tire?: string | null) =>
  !c ? "–" : c === "BOX" ? (tire ? `BOX → ${tire}` : "BOX") : c === "STAY_OUT" ? "STAY OUT" : c.replace("_", " ");
const SPRINT_TIRES: Tire[] = ["INTERMEDIATE", "SOFT", "MEDIUM", "HARD"];
const fmtGap = (s?: number | null) => (s == null ? "" : `+${s.toFixed(3)}`);

export const realLabel = (d: Decision, sprint: boolean) => {
  if (!sprint) return d.actual_pitted == null ? "…" : d.actual_pitted ? "BOX" : "STAY OUT";
  const a = d.actual;
  if (!a?.action) return d.actual_pitted === 1 ? "BOX" : "…";
  if (a.action === "BOX") return callLabel("BOX", a.tire);
  if (a.action === "STAY_OUT") return `STAY OUT · ${a.mode} (inferred)`;
  return a.action;
};

/* ---------- control bar ---------- */

export function RaceControl({ sim, code, run, sprint }: {
  sim: SimState; code: string; run: (p: Promise<unknown>) => void; sprint: boolean;
}) {
  const c = sim.clock, race = sim.race, pending = sim.pending, over = !!sim.over;
  const w = race?.weather;
  const pct = c.total_laps ? (100 * c.current_lap) / c.total_laps : 0;
  return (
    <section className="card">
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div className="lapcount">
          <span className="lbl">{sprint ? "SPRINT" : "RACE"} · LAP</span>
          {c.current_lap}<span className="of">/{c.total_laps}</span>
        </div>
        <div style={{ textAlign: "right" }} className="stack">
          <span className="chip team" style={{ ["--chip" as string]: "var(--accent)", justifySelf: "end" }}>
            <span className="dot" />Driving {code}
          </span>
          {w && <WeatherBadge trackC={w.track_temp_c} raining={w.raining} recent={w.rain_in_last_5_min} />}
        </div>
      </div>
      <div className="progress" style={{ margin: "0.8rem 0" }}><i style={{ width: `${pct}%` }} /></div>
      {c.current_lap === 0 && !c.running && !pending && (
        <p className="muted small" style={{ margin: "0 0 0.7rem" }}>
          On the grid. Start the race and the clock steps lap by lap; it stops at each decision point for your call.
        </p>
      )}
      <div className="row" style={{ gap: "0.5rem" }}>
        {c.running ? (
          <button className="btn" onClick={() => run(api.pause())}>{Icon.pause(14)} Pause</button>
        ) : (
          <button className="btn" disabled={!!pending || over} onClick={() => run(api.start())}>
            {Icon.play(14)} {c.current_lap === 0 ? (sprint ? "Start sprint" : "Start race") : "Resume"}
          </button>
        )}
        <Seg value={c.seconds_per_lap} onChange={(v) => run(api.speed(v))}
             options={[0.5, 1, 2, 4, 8].map((s) => ({ value: s, label: <span className="mono">{s}s</span> }))} />
        <button className="btn ghost" disabled={!!pending || c.current_lap < 1 || over} onClick={() => run(api.ask())}>
          {Icon.spark(15)} Ask strategy
        </button>
        {over && <span className="pill accent">{Icon.flag(12)} Chequered flag</span>}
      </div>
      <div className="dim tiny" style={{ marginTop: "0.5rem" }}>Speed: seconds per lap</div>
    </section>
  );
}

/* ---------- timing tower ---------- */

export function RaceTower({ race }: { race: RaceState }) {
  return (
    <section className="card flush tower">
      <div className="spread" style={{ padding: "0.85rem 0.85rem 0.6rem" }}>
        <h2 style={{ margin: 0, fontSize: "0.95rem" }}>{race.phase === "grid" ? "Starting grid" : "Running order"}</h2>
        <span className="eyebrow">{race.phase === "grid" ? "Lap 0" : `After lap ${race.lap}`}</span>
      </div>
      <div className="tower-head race">
        <span style={{ textAlign: "right" }}>P</span><span /><span>Driver</span><span className="hide-sm">Team</span>
        <span style={{ textAlign: "right" }}>Int</span><span className="hide-sm" style={{ textAlign: "right" }}>Gap</span>
        <span>Tire</span><span className="hide-sm" style={{ textAlign: "right" }}>Stp</span><span />
      </div>
      {race.cars.map((c) => (
        <div key={c.driver_id} className={`trow race ${c.is_my_driver ? "mine" : ""}`}>
          <span className="pos">{c.position}</span>
          <span className="bar" style={{ ["--tc" as string]: teamColor(c.team) }} />
          <span className="code">{c.code}</span>
          <span className="team hide-sm">{c.team}</span>
          <span className="t">{c.position === 1 && race.phase !== "grid" ? <span className="dim">Leader</span> : fmtGap(c.gap_ahead_s)}</span>
          <span className="t gap hide-sm">{fmtGap(c.gap_leader_s)}</span>
          <span><TireDot compound={c.compound} age={c.tire_age} /></span>
          <span className="t hide-sm">{c.stops ?? ""}</span>
          <span className="flag">{c.pitted_end_of_lap ? <span className="pill in">IN</span> : c.out_lap ? <span className="pill out">OUT</span> : null}</span>
        </div>
      ))}
      {race.retired?.map((r) => (
        <div key={r.driver_id} className="trow race out">
          <span className="pos">–</span><span className="bar" /><span className="code">{r.code}</span>
          <span className="small" style={{ gridColumn: "4 / -1" }}>Out after lap {r.last_lap}</span>
        </div>
      ))}
    </section>
  );
}

/* ---------- decision card ---------- */

const callClass = (c?: string | null) => ({
  BOX: "box", STAY_OUT: "stay", GO_OUT: "go", WAIT: "wait", CONTINUE: "continue", ABORT: "abort",
  DEFEND: "defend", PUSH: "push", MANAGE: "manage",
} as Record<string, string>)[c ?? ""] ?? "";

export function BigCall({ call, tire }: { call?: string | null; tire?: string | null }) {
  return (
    <div className="row" style={{ alignItems: "flex-end", gap: "0.8rem" }}>
      <div className={`bigcall ${callClass(call)}`}>{callLabel(call).replace(/ → .*/, "")}</div>
      {tire && <span style={{ marginBottom: "0.35rem" }}><TireDot compound={tire} title={tire} /> <span className="small muted">{tire}</span></span>}
    </div>
  );
}

/** Everything under the call that the crew showed its work with. Shared by every mode. */
export function DecisionDetail({ d, evidenceLine }: { d: Decision; evidenceLine?: ReactNode }) {
  const ev = d.evidence;
  return (
    <>
      <p className="reason">{d.strategy_reason}</p>
      {ev?.rival_factor && <p className="small" style={{ margin: "0.3rem 0" }}><span className="eyebrow">Rivals</span><br />{ev.rival_factor}</p>}
      {d.engineer_message && <p className="quote small">{d.engineer_message} <span className="dim">— Race Engineer</span></p>}
      {ev?.rival_watch && (
        <More summary={<>Rival Watch report</>}>
          <p className="small" style={{ margin: "0.5rem 0 0" }}>{ev.rival_watch.summary}</p>
          <ul>
            {ev.rival_watch.rivals.map((r) => (
              <li key={r.code}><b>{r.code}</b> {r.relation}{r.gap_s == null ? "" : ` ${r.gap_s}s`}: {r.note}</li>
            ))}
          </ul>
          {ev.rival_watch.rejoin_behind_if_box?.length > 0 && (
            <p className="small muted" style={{ margin: "0.4rem 0 0" }}>If we box now we rejoin behind: {ev.rival_watch.rejoin_behind_if_box.join(", ")}</p>
          )}
        </More>
      )}
      {evidenceLine && (
        <More summary={<>Checked against the data</>}>
          <p className="small muted" style={{ margin: "0.5rem 0 0" }}>{evidenceLine}</p>
          {ev?.tool_calls && <p className="tiny dim" style={{ margin: "0.4rem 0 0" }}>Tools: {ev.tool_calls.map((t) => `${t.agent}:${t.tool}`).join(", ")}</p>}
        </More>
      )}
      {ev?.cited && ev.cited.length > 0 && (
        <More summary={<>Cited facts ({ev.cited.length}), each filled in from the data</>}>
          <ul>{ev.cited.map((f) => <li key={f.key}><code className="k">{f.kind}</code> {f.text}</li>)}</ul>
        </More>
      )}
      {ev?.grounding_rejections && ev.grounding_rejections.length > 0 && (
        <More summary={<>Grounding check sent back {ev.grounding_rejections.length} attempt{ev.grounding_rejections.length > 1 ? "s" : ""}</>}>
          <ul>{ev.grounding_rejections.flatMap((r, i) => r.problems.map((p, j) => <li key={`${i}-${j}`}><b>{r.agent}:</b> {p}</li>))}</ul>
        </More>
      )}
      {ev?.grounding_cleanups && ev.grounding_cleanups.length > 0 && (
        <More summary={<>Cleaned up in code ({ev.grounding_cleanups.length}), no retry</>}>
          <ul>{ev.grounding_cleanups.map((c, i) => <li key={i}><b>{c.agent}:</b> {c.note}</li>)}</ul>
        </More>
      )}
      <TokenMeter usage={ev?.usage} />
    </>
  );
}

export function DecisionShell({ title, d, children, actions }: {
  title: ReactNode; d: Decision; children?: ReactNode; actions?: ReactNode;
}) {
  return (
    <section className="card accent decision">
      <div className="eyebrow" style={{ color: "var(--accent)" }}>{title}</div>
      <div className="trigger">{d.trigger}</div>
      {d.status === "thinking" && <div className="thinking"><span className="pulse" /> The crew is reading the data…</div>}
      {d.status === "error" && <div className="toast">The team could not make a call: {d.error}</div>}
      {d.status === "ready" && <>{children}{actions && <div style={{ marginTop: "0.4rem" }}>{actions}</div>}</>}
    </section>
  );
}

export function RaceDecision({ d, lap, sprint, onCall }: {
  d: Decision; lap: number; sprint: boolean; onCall: (c: Call, tire?: Tire) => void;
}) {
  const ev = d.evidence;
  const evidence = ev && (
    <>P{ev.position}, {ev.compound} {ev.tire_age} laps, {ev.gap_ahead_s == null ? "leading" : `${ev.gap_ahead_s}s to the car ahead`},
      {" "}{ev.gap_behind_s == null ? "nobody behind" : `${ev.gap_behind_s}s to the car behind`}</>
  );
  return (
    <DecisionShell d={d} title={<>Decision · end of lap {lap} · call for lap {lap + 1}</>}
      actions={sprint ? (
        <>
          <div className="eyebrow" style={{ marginTop: "0.9rem" }}>Box at the end of lap {lap + 1} for</div>
          <div className="actions tires" style={{ marginTop: "0.5rem" }}>
            {SPRINT_TIRES.map((t) => (
              <button key={t} className="btn lg ghost" onClick={() => onCall("BOX", t)}><TireDot compound={t} /> {t === "INTERMEDIATE" ? "INTER" : t}</button>
            ))}
          </div>
          <div className="eyebrow" style={{ marginTop: "0.9rem" }}>Or stay out and</div>
          <div className="actions" style={{ gridTemplateColumns: "repeat(3, 1fr)", marginTop: "0.5rem" }}>
            <button className="btn lg" onClick={() => onCall("DEFEND")}>Defend</button>
            <button className="btn lg" onClick={() => onCall("PUSH")}>Push</button>
            <button className="btn lg" onClick={() => onCall("MANAGE")}>Manage</button>
          </div>
        </>
      ) : (
        <div className="actions two">
          <button className="btn lg stop" onClick={() => onCall("BOX")}>Box this lap</button>
          <button className="btn lg go" onClick={() => onCall("STAY_OUT")}>Stay out</button>
        </div>
      )}>
      <div className="eyebrow" style={{ marginTop: "0.2rem" }}>Strategy says</div>
      <BigCall call={d.strategy_call} tire={d.strategy_call === "BOX" ? ev?.tire_to_fit : null} />
      <DecisionDetail d={d} evidenceLine={evidence} />
    </DecisionShell>
  );
}

/* ---------- decision log ---------- */

export function DecisionLog({ decisions, code, rows }: {
  decisions: Decision[]; code: string;
  rows: (d: Decision) => { when: ReactNode; strategy: string; you: string; real: string; realTitle?: string };
}) {
  return (
    <section className="card flush">
      <div className="spread" style={{ padding: "0.85rem 0.85rem 0.6rem" }}>
        <h2 style={{ margin: 0, fontSize: "0.95rem" }}>Decisions</h2>
        <span className="eyebrow">{decisions.length} so far</span>
      </div>
      {decisions.length === 0 && <div className="empty">No decision points yet. They come up as the session unfolds.</div>}
      <div className="log">
        {[...decisions].reverse().map((d) => {
          const r = rows(d);
          return (
            <div key={d.id} className="log-item">
              <div className="spread"><span className="when">{r.when}</span><span className="token-cell">{costCell(d.evidence?.usage)}</span></div>
              <div className="small">{d.trigger}</div>
              <div className="calls">
                <div className="callbox"><div className="who">Strategy</div><div className="what">{d.status === "error" ? "error" : r.strategy}</div></div>
                <div className="callbox"><div className="who">You</div><div className="what">{r.you}</div></div>
                <div className="callbox" title={r.realTitle}><div className="who">Real {code}</div><div className="what">{r.real}</div></div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ---------- scorecard ---------- */

export const pct = (r?: Rate) => (!r || r.pct == null ? "–" : `${r.pct}%`);
export const ofs = (r?: Rate) => (!r || r.pct == null ? "" : `${r.matched}/${r.of}`);

export function Tiles({ items }: { items: { label: string; rate?: Rate; me?: boolean }[] }) {
  return (
    <div className="tiles">
      {items.map((t) => (
        <div key={t.label} className={`tile ${t.me ? "me" : ""}`}>
          <div className="big">{pct(t.rate)}</div>
          <div className="of">{ofs(t.rate)}</div>
          <div className="lbl">{t.label}</div>
        </div>
      ))}
    </div>
  );
}

export function RaceScorecard({ sc, code, sprint, onRegrade }: {
  sc: Scorecard; code: string; sprint: boolean; onRegrade: () => void;
}) {
  const extra = sc.stats as unknown as Record<string, Rate | undefined> | null;
  type Row = { lap: number; trigger: string; strategy: string; you: string; real: string; real_detail?: string;
               strategy_ok?: boolean; you_ok?: boolean };
  const rows = (sc.stats?.rows ?? []) as Row[];
  const st = sc.stats, v = sc.verdict;
  return (
    <section className="card accent">
      <div className="spread">
        <div className="eyebrow" style={{ color: "var(--accent)" }}>{Icon.flag(12)} Chequered flag · Scorecard</div>
        {st?.real_finish && <span className="pill neutral">Real {code}: {st.real_finish.pos === "R" ? "DNF" : `P${st.real_finish.pos}`}</span>}
      </div>
      {sc.status === "grading" && <div className="thinking"><span className="pulse" /> The Grader is reading the final result…</div>}
      {sc.status === "error" && <div className="toast">Grading failed: {sc.error} <button className="btn sm" onClick={onRegrade}>Grade again</button></div>}
      {v?.headline && <div className="headline">{v.headline}</div>}
      {st && st.decision_points > 0 && (
        <Tiles items={[
          { label: `You vs real team${sprint ? " (box/stay)" : ""}`, rate: st.you_vs_real, me: true },
          { label: `Strategy vs real team${sprint ? " (box/stay)" : ""}`, rate: st.strategy_vs_real },
          { label: "You vs Strategy", rate: st.you_vs_strategy },
        ]} />
      )}
      {sprint && extra && (extra.your_tire_vs_real || extra.your_mode_vs_inferred) && (
        <div className="kv" style={{ marginTop: "0.7rem" }}>
          {extra.your_tire_vs_real && <><dt>Your tire, when you both boxed</dt><dd className="num">{ofs(extra.your_tire_vs_real) || "–"}</dd></>}
          {extra.strategy_tire_vs_real && <><dt>Strategy's tire, when both boxed</dt><dd className="num">{ofs(extra.strategy_tire_vs_real) || "–"}</dd></>}
          {extra.your_mode_vs_inferred && <><dt>Your defend / push / manage vs inferred</dt><dd className="num">{ofs(extra.your_mode_vs_inferred) || "–"}</dd></>}
          {extra.strategy_mode_vs_inferred && <><dt>Strategy's vs inferred</dt><dd className="num">{ofs(extra.strategy_mode_vs_inferred) || "–"}</dd></>}
        </div>
      )}
      {v?.biggest_call && (
        <div className="biggest" style={{ marginTop: "0.8rem" }}>
          <div className="eyebrow">Biggest call · lap {v.biggest_call_lap}</div>
          <div style={{ marginTop: "0.3rem" }}>{v.biggest_call}</div>
        </div>
      )}
      {st?.real_finish && (
        <p className="small muted" style={{ margin: "0.7rem 0 0" }}>
          Real {code}: started P{st.real_finish.grid}, finished {st.real_finish.pos === "R" ? "DNF" : `P${st.real_finish.pos}`} ({st.real_finish.status}, {st.real_finish.points} pts)
          {st.real_stops && st.real_stops.length > 0 && <> · stops: {st.real_stops.filter((s) => !s.pit_lane_pass).map((s) => `lap ${s.lap} ${s.from_tire ?? "?"}→${s.to_tire ?? "?"}`).join(", ")}</>}
        </p>
      )}
      {v?.your_calls && <p className="small" style={{ margin: "0.5rem 0 0" }}><span className="eyebrow">Your calls</span><br />{v.your_calls}</p>}
      {v?.strategy_calls && <p className="small" style={{ margin: "0.5rem 0 0" }}><span className="eyebrow">Strategy agent</span><br />{v.strategy_calls}</p>}
      <TokenMeter usage={v?.usage} />
      {rows.length > 0 && (
        <More summary={<>Every decision ({rows.length})</>}>
          <div className="log" style={{ marginTop: "0.5rem" }}>
            {rows.map((r) => {
              const sOk = sprint ? r.strategy_ok : r.strategy === r.real;
              const yOk = sprint ? r.you_ok : r.you === r.real;
              return (
                <div key={r.lap} className="log-item" style={{ padding: "0.55rem 0" }}>
                  <div className="when">Lap {r.lap}</div>
                  <div className="small">{r.trigger}</div>
                  <div className="calls">
                    <div className={`callbox ${sOk ? "hit" : "miss"}`}><div className="who">Strategy</div><div className="what">{sprint ? r.strategy : callLabel(r.strategy)}</div></div>
                    <div className={`callbox ${yOk ? "hit" : "miss"}`}><div className="who">You</div><div className="what">{sprint ? r.you : callLabel(r.you)}</div></div>
                    <div className="callbox" title={r.real_detail}><div className="who">Real</div><div className="what">{sprint ? r.real : callLabel(r.real)}</div></div>
                  </div>
                </div>
              );
            })}
          </div>
        </More>
      )}
    </section>
  );
}

export type { Entry };
