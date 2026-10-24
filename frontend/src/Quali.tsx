import type { ReactNode } from "react";
import { api, type Decision, type Rate, type Scorecard, type SimState, type Tire } from "./api";
import { costCell, TokenMeter } from "./TokenMeter";

const TIRES: Tire[] = ["WET", "INTERMEDIATE", "SOFT"];

const callText = (c?: string | null, tire?: string | null) => {
  if (!c) return "–";
  const base = { GO_OUT: "GO OUT", WAIT: "WAIT", CONTINUE: "CONTINUE", ABORT: "ABORT" }[c] ?? c;
  return c === "GO_OUT" && tire ? `${base} on ${tire}` : base;
};

type QualiRow = { n: number; when: string; trigger: string; strategy: string; you: string; real: string;
                  real_detail: string; strategy_ok: boolean; you_ok: boolean };

const pct = (r?: Rate) => (!r || r.pct == null ? "–" : `${r.matched}/${r.of} (${r.pct}%)`);

export function QualiView({ sim, code, run, askPanel }: {
  sim: SimState;
  code: string;
  run: (p: Promise<unknown>) => void;
  askPanel: ReactNode;
}) {
  const clock = sim.clock;
  const q = sim.quali;
  const pending = sim.pending;
  const over = !!sim.over;
  const started = clock.session_time_utc !== clock.session_start_utc;
  const w = q?.weather;

  return (
    <>
      <section className="panel">
        <strong>
          {q?.segment ? (q.running ? `${q.segment} · ${q.elapsed} in · ~${q.scheduled_time_left} of ${q.scheduled_length} left (scheduled)` : q.phase) : "Before qualifying"}
        </strong>{" "}
        · session {q?.session_elapsed ?? "0:00"} · driving for <strong>{code}</strong>
        {w && (
          <span>
            {" "}· track {w.track_temp_c}°C,{" "}
            {w.raining ? "RAINING" : w.minutes_since_rain == null ? "dry" : `dry, no rain for ${w.minutes_since_rain} min`}
          </span>
        )}
        {q?.track_evolution && q.track_evolution.direction !== "unknown" && (
          <span> · track {q.track_evolution.direction} ({q.track_evolution.change_s! > 0 ? "+" : ""}{q.track_evolution.change_s}s)</span>
        )}
        <div className="controls">
          {clock.running ? (
            <button onClick={() => run(api.pause())}>Pause</button>
          ) : (
            <button disabled={!!pending || over} onClick={() => run(api.start())}>
              {started ? "Resume" : "Start qualifying"}
            </button>
          )}{" "}
          <label>
            Speed{" "}
            <select value={clock.quali_speed} onChange={(e) => run(api.qualiSpeed(Number(e.target.value)))}>
              {[20, 40, 80, 160].map((s) => <option key={s} value={s}>×{s}</option>)}
            </select>
          </label>{" "}
          <button disabled={!!pending || over} onClick={() => run(api.ask())}>Ask strategy now</button>
          {over && <strong> · Chequered flag</strong>}
        </div>
      </section>

      {sim.scorecard && <QualiScorecard sc={sim.scorecard} code={code} onRegrade={() => run(api.grade())} />}
      {pending && <QualiDecisionPanel d={pending} run={run} />}

      {q && !q.refused && (
        <div className="columns">
          <section className="panel">
            <h2>{q.segment ? `${q.segment} order (best lap this segment)` : "Entry list"}</h2>
            <table>
              <thead>
                <tr><th>P</th><th>Driver</th><th>Best</th><th>On</th><th>Set at</th><th>Status</th><th>Tire now</th><th>Age</th><th>Laps</th></tr>
              </thead>
              <tbody>
                {q.order.map((c) => (
                  <tr key={c.code} className={`${c.is_my_driver ? "mine" : ""} ${q.cars_through && c.position > q.cars_through ? "dropzone" : ""}`}>
                    <td>{c.position}</td><td>{c.code}</td><td>{c.best ?? ""}</td><td>{c.best_compound ?? ""}</td>
                    <td>{c.best_set_at ?? ""}</td><td>{c.status === "garage" ? "" : c.status.toUpperCase()}</td>
                    <td>{c.compound ?? ""}</td><td>{c.tire_age ?? ""}</td><td>{c.laps_this_segment}</td>
                  </tr>
                ))}
                {q.eliminated.map((e) => (
                  <tr key={e.code} className="retired"><td>–</td><td>{e.code}</td><td colSpan={7}>out in {e.out_in}{e.best ? ` (${e.best})` : ""}</td></tr>
                ))}
              </tbody>
            </table>
            {q.cars_through && <p className="evidence">Shaded rows: outside the top {q.cars_through} (the drop zone), on lap times so far.</p>}
          </section>
          <div>
            <QualiDecisionLog decisions={sim.decisions} code={code} />
            {askPanel}
          </div>
        </div>
      )}
    </>
  );
}

function QualiDecisionPanel({ d, run }: { d: Decision; run: (p: Promise<unknown>) => void }) {
  const ev = d.evidence as (Decision["evidence"] & {
    segment?: string; time_left?: string; status?: string; best?: string | null; cut_margin_s?: number | null; when?: string;
  }) | null;
  const status = d.actual?.status_at_call ?? ev?.status;
  const inGarage = status === "garage";
  return (
    <section className="panel decision">
      <h2>Decision point · {d.actual?.when ?? ev?.when ?? "qualifying"} · our car {status ?? "…"}</h2>
      <p><strong>Why now:</strong> {d.trigger}</p>
      {d.status === "thinking" && <p><em>Strategy is reading the session…</em></p>}
      {d.status === "error" && <p className="error">The team could not make a call: {d.error}</p>}
      {d.status === "ready" && (
        <>
          <p className="call">Strategy: <strong>{callText(d.strategy_call, ev?.tire_to_fit)}</strong></p>
          <p>{d.strategy_reason}</p>
          {ev?.rival_factor && <p><strong>Rivals:</strong> {ev.rival_factor}</p>}
          {ev?.rival_watch && (
            <div className="rivals">
              <strong>Rival Watch:</strong> {ev.rival_watch.summary}
              <ul>{ev.rival_watch.rivals.map((r) => <li key={r.code}>{r.code}: {r.note}</li>)}</ul>
            </div>
          )}
          {ev && (
            <p className="evidence">
              From the data: {ev.segment ?? "–"}{ev.time_left ? `, ~${ev.time_left} left` : ""} · P{ev.position ?? "–"} ·{" "}
              {ev.status} on {ev.compound ?? "?"}{ev.tire_age != null ? ` (age ${ev.tire_age})` : ""} · best {ev.best ?? "none yet"}
              {ev.cut_margin_s != null ? ` · ${Math.abs(ev.cut_margin_s)}s ${ev.cut_margin_s >= 0 ? "inside" : "outside"} the cut` : ""}
            </p>
          )}
          {ev?.cited && ev.cited.length > 0 && (
            <details className="evidence">
              <summary>Cited facts ({ev.cited.length}), each filled in from the data</summary>
              <ul>{ev.cited.map((f) => <li key={f.key}><code>{f.kind}</code> {f.text}</li>)}</ul>
            </details>
          )}
          {ev?.grounding_rejections && ev.grounding_rejections.length > 0 && (
            <details className="evidence">
              <summary>Grounding check sent back {ev.grounding_rejections.length} attempt{ev.grounding_rejections.length > 1 ? "s" : ""}</summary>
              <ul>{ev.grounding_rejections.flatMap((r, i) => r.problems.map((p, j) => <li key={`${i}-${j}`}><strong>{r.agent}:</strong> {p}</li>))}</ul>
            </details>
          )}
          <p><strong>Engineer:</strong> {d.engineer_message}</p>
          <TokenMeter usage={d.evidence?.usage} />
          {inGarage ? (
            <>
              {TIRES.map((t) => (
                <button key={t} className="big" onClick={() => run(api.call("GO_OUT", t))}>Go out on {t}</button>
              ))}{" "}
              <button className="big" onClick={() => run(api.call("WAIT"))}>Wait</button>
            </>
          ) : (
            <>
              <button className="big" onClick={() => run(api.call("CONTINUE"))}>Continue the lap</button>{" "}
              <button className="big" onClick={() => run(api.call("ABORT"))}>Abort the lap</button>
            </>
          )}
        </>
      )}
    </section>
  );
}

function QualiDecisionLog({ decisions, code }: { decisions: Decision[]; code: string }) {
  return (
    <section className="panel">
      <h2>Decisions</h2>
      {decisions.length === 0 && <p>None yet.</p>}
      <table>
        <thead><tr><th>When</th><th>Why</th><th>Strategy</th><th>You</th><th>Real {code}</th><th className="token-cell">Cost</th></tr></thead>
        <tbody>
          {decisions.map((d) => (
            <tr key={d.id}>
              <td>{d.actual?.when ?? ""}</td>
              <td>{d.trigger}</td>
              <td>{d.status === "error" ? "error" : callText(d.strategy_call, (d.evidence as { tire_to_fit?: string } | null)?.tire_to_fit)}</td>
              <td>{callText(d.user_call, d.user_tire)}</td>
              <td title={d.actual?.detail}>{d.actual?.action ? callText(d.actual.action, d.actual.tire) : "…"}</td>
              <td className="token-cell">{costCell(d.evidence?.usage)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function QualiScorecard({ sc, code, onRegrade }: { sc: Scorecard; code: string; onRegrade: () => void }) {
  const st = sc.stats as (Scorecard["stats"] & {
    your_tire_vs_real?: Rate; strategy_tire_vs_real?: Rate;
    real_best?: { time: string; compound: string; segment: string; set_at_in_segment: string } | null;
  }) | null;
  const rows = (st?.rows ?? []) as unknown as QualiRow[];
  const v = sc.verdict;
  const fin = st?.real_finish as unknown as { pos: string; q1: string | null; q2: string | null; q3: string | null } | undefined;
  return (
    <section className="panel decision">
      <h2>Chequered flag · Qualifying scorecard</h2>
      {sc.status === "grading" && <p><em>The Grader is reading the final classification…</em></p>}
      {sc.status === "error" && <p className="error">Grading failed: {sc.error} <button onClick={onRegrade}>Grade again</button></p>}
      {fin && (
        <p>
          Real {code}: P{fin.pos} ({["q1", "q2", "q3"].map((k) => (fin as Record<string, string | null>)[k] ? `${k.toUpperCase()} ${(fin as Record<string, string | null>)[k]}` : null).filter(Boolean).join(" · ")})
          {st?.real_best && <> · best lap {st.real_best.time} on {st.real_best.compound}, set {st.real_best.set_at_in_segment} into {st.real_best.segment}</>}
        </p>
      )}
      {v?.headline && <p className="call">{v.headline}</p>}
      {st && st.decision_points > 0 && (
        <table className="score">
          <tbody>
            <tr><td>You matched the real team's call</td><td><strong>{pct(st.you_vs_real)}</strong></td></tr>
            <tr><td>Strategy agent matched the real team's call</td><td><strong>{pct(st.strategy_vs_real)}</strong></td></tr>
            <tr><td>Your tire matched, when you both went out</td><td>{pct(st.your_tire_vs_real)}</td></tr>
            <tr><td>Strategy's tire matched, when both went out</td><td>{pct(st.strategy_tire_vs_real)}</td></tr>
            <tr><td>You agreed with the Strategy agent</td><td>{pct(st.you_vs_strategy)}</td></tr>
          </tbody>
        </table>
      )}
      {v?.biggest_call && <p><strong>Biggest call (#{v.biggest_call_lap}):</strong> {v.biggest_call}</p>}
      {v?.your_calls && <p><strong>Your calls:</strong> {v.your_calls}</p>}
      {v?.strategy_calls && <p><strong>Strategy agent:</strong> {v.strategy_calls}</p>}
      <TokenMeter usage={v?.usage} />
      {rows.length > 0 && (
        <table>
          <thead><tr><th>#</th><th>When</th><th>Why</th><th>Strategy</th><th>You</th><th>Real</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.n}>
                <td>{r.n}</td><td>{r.when}</td><td>{r.trigger}</td>
                <td className={r.strategy_ok ? "hit" : "miss"}>{r.strategy}</td>
                <td className={r.you_ok ? "hit" : "miss"}>{r.you}</td>
                <td title={r.real_detail}>{r.real}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
