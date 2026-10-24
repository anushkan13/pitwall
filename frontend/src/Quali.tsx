import { Fragment } from "react";
import { api, type Decision, type Entry, type Rate, type Scorecard, type SimState, type Tire } from "./api";
import { teamColor } from "./Favorites";
import { BigCall, DecisionDetail, DecisionLog, DecisionShell, Tiles } from "./Race";
import { TokenMeter } from "./TokenMeter";
import { Icon, More, Seg, TireDot, WeatherBadge } from "./ui";

const TIRES: Tire[] = ["WET", "INTERMEDIATE", "SOFT"];

export const callText = (c?: string | null, tire?: string | null) => {
  if (!c) return "–";
  const base = ({ GO_OUT: "GO OUT", WAIT: "WAIT", CONTINUE: "CONTINUE", ABORT: "ABORT" } as Record<string, string>)[c] ?? c;
  return c === "GO_OUT" && tire ? `${base} · ${tire}` : base;
};

type QualiEvidence = NonNullable<Decision["evidence"]> & {
  segment?: string; time_left?: string; status?: string; best?: string | null; cut_margin_s?: number | null; when?: string;
};

/* ---------- control bar ---------- */

export function QualiControl({ sim, code, run }: { sim: SimState; code: string; run: (p: Promise<unknown>) => void }) {
  const c = sim.clock, q = sim.quali, pending = sim.pending, over = !!sim.over;
  const started = c.session_time_utc !== c.session_start_utc;
  const w = q?.weather;
  const evo = q?.track_evolution;
  return (
    <section className="card">
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div className="lapcount">
          <span className="lbl">QUALIFYING · {q?.segment ?? "PRE-SESSION"}</span>
          {q?.segment && q.running ? <>{q.scheduled_time_left}<span className="of"> left</span></> : <span style={{ fontSize: "1.3rem" }}>{q?.segment ? "Break" : "Not started"}</span>}
        </div>
        <div style={{ textAlign: "right" }} className="stack">
          <span className="chip team" style={{ ["--chip" as string]: "var(--accent)", justifySelf: "end" }}><span className="dot" />Driving {code}</span>
          {w && <WeatherBadge trackC={w.track_temp_c} raining={w.raining}
                              recent={!w.raining && w.minutes_since_rain != null && w.minutes_since_rain < 10}
                              note={!w.raining && w.minutes_since_rain != null ? `${w.minutes_since_rain}m since rain` : undefined} />}
        </div>
      </div>
      <div className="row small muted" style={{ margin: "0.7rem 0" }}>
        <span className="mono">Session {q?.session_elapsed ?? "0:00"}</span>
        {q?.segment && q.running && <span className="mono">· {q.elapsed} into {q.scheduled_length} (scheduled)</span>}
        {q && !q.running && q.segment && <span>· {q.phase}</span>}
        {evo && evo.direction !== "unknown" && (
          <span className={`pill ${evo.direction === "improving" ? "ok" : evo.direction === "worsening" ? "bad" : "neutral"}`}>
            Track {evo.direction} {evo.change_s! > 0 ? "+" : ""}{evo.change_s}s
          </span>
        )}
      </div>
      <div className="row" style={{ gap: "0.5rem" }}>
        {c.running ? (
          <button className="btn" onClick={() => run(api.pause())}>{Icon.pause(14)} Pause</button>
        ) : (
          <button className="btn" disabled={!!pending || over} onClick={() => run(api.start())}>
            {Icon.play(14)} {started ? "Resume" : "Start qualifying"}
          </button>
        )}
        <Seg value={c.quali_speed ?? 40} onChange={(v) => run(api.qualiSpeed(v))}
             options={[20, 40, 80, 160].map((s) => ({ value: s, label: <span className="mono">×{s}</span> }))} />
        <button className="btn ghost" disabled={!!pending || over} onClick={() => run(api.ask())}>{Icon.spark(15)} Ask strategy</button>
        {over && <span className="pill accent">{Icon.flag(12)} Chequered flag</span>}
      </div>
    </section>
  );
}

/* ---------- timing tower ---------- */

export function QualiTower({ sim, entries }: { sim: SimState; entries: Entry[] }) {
  const q = sim.quali;
  if (!q || q.refused) return null;
  const team = (code: string) => entries.find((e) => e.code === code)?.team;
  const cut = q.cars_through ?? null;
  return (
    <section className="card flush tower">
      <div className="spread" style={{ padding: "0.85rem 0.85rem 0.6rem" }}>
        <h2 style={{ margin: 0, fontSize: "0.95rem" }}>{q.segment ? `${q.segment} order` : "Entry list"}</h2>
        <span className="eyebrow">Best lap this segment</span>
      </div>
      <div className="tower-head quali">
        <span style={{ textAlign: "right" }}>P</span><span /><span>Driver</span><span className="hide-sm">Status</span>
        <span style={{ textAlign: "right" }}>Best</span><span>On</span><span className="hide-sm" style={{ textAlign: "right" }}>Set</span>
        <span>Now</span><span className="hide-sm" style={{ textAlign: "right" }}>Laps</span>
      </div>
      {q.order.map((c) => (
        <Fragment key={c.code}>
          {cut != null && c.position === cut + 1 && <div className="cutline">Drop zone</div>}
          <div className={`trow quali ${c.is_my_driver ? "mine" : ""} ${cut != null && c.position > cut ? "drop" : ""}`}>
            <span className="pos">{c.position}</span>
            <span className="bar" style={{ ["--tc" as string]: teamColor(team(c.code)) }} />
            <span className="code">{c.code}</span>
            <span className="hide-sm">{c.status === "garage" ? <span className="dim small">Garage</span>
              : <span className={`pill ${c.status === "out lap" ? "out" : "ok"}`}>{c.status === "out lap" ? "Out lap" : "On lap"}</span>}</span>
            <span className="t">{c.best ?? <span className="dim">no time</span>}</span>
            <span>{c.best_compound ? <TireDot compound={c.best_compound} /> : null}</span>
            <span className="t hide-sm gap">{c.best_set_at ?? ""}</span>
            <span><TireDot compound={c.compound} age={c.tire_age} /></span>
            <span className="t hide-sm">{c.laps_this_segment}</span>
          </div>
        </Fragment>
      ))}
      {q.eliminated.map((e) => (
        <div key={e.code} className="trow quali out">
          <span className="pos">–</span><span className="bar" style={{ ["--tc" as string]: teamColor(team(e.code)) }} />
          <span className="code">{e.code}</span>
          <span className="small" style={{ gridColumn: "4 / -1" }}>Out in {e.out_in}{e.best ? ` · ${e.best}` : ""}</span>
        </div>
      ))}
      {cut != null && <div className="dim tiny" style={{ padding: "0.5rem 0.85rem" }}>Shaded: outside the top {cut}, on lap times so far.</div>}
    </section>
  );
}

/* ---------- decision card ---------- */

export function QualiDecision({ d, run }: { d: Decision; run: (p: Promise<unknown>) => void }) {
  const ev = d.evidence as QualiEvidence | null;
  const status = d.actual?.status_at_call ?? ev?.status;
  const inGarage = status === "garage";
  const evidence = ev && (
    <>{ev.segment ?? "–"}{ev.time_left ? `, ~${ev.time_left} left` : ""} · P{ev.position ?? "–"} · {ev.status} on {ev.compound ?? "?"}
      {ev.tire_age != null ? ` (age ${ev.tire_age})` : ""} · best {ev.best ?? "none yet"}
      {ev.cut_margin_s != null ? ` · ${Math.abs(ev.cut_margin_s)}s ${ev.cut_margin_s >= 0 ? "inside" : "outside"} the cut` : ""}</>
  );
  return (
    <DecisionShell d={d} title={<>Decision · {d.actual?.when ?? ev?.when ?? "qualifying"} · our car {status ?? "…"}</>}
      actions={inGarage ? (
        <>
          <div className="eyebrow" style={{ marginTop: "0.9rem" }}>Go out now on</div>
          <div className="actions tires" style={{ marginTop: "0.5rem" }}>
            {TIRES.map((t) => (
              <button key={t} className="btn lg go" onClick={() => run(api.call("GO_OUT", t))}><TireDot compound={t} /> {t === "INTERMEDIATE" ? "INTER" : t}</button>
            ))}
          </div>
          <div className="actions" style={{ marginTop: "0.6rem" }}>
            <button className="btn lg ghost" onClick={() => run(api.call("WAIT"))}>Wait in the garage</button>
          </div>
        </>
      ) : (
        <div className="actions two">
          <button className="btn lg go" onClick={() => run(api.call("CONTINUE"))}>Continue lap</button>
          <button className="btn lg stop" onClick={() => run(api.call("ABORT"))}>Abort lap</button>
        </div>
      )}>
      <div className="eyebrow" style={{ marginTop: "0.2rem" }}>Strategy says</div>
      <BigCall call={d.strategy_call} tire={d.strategy_call === "GO_OUT" ? ev?.tire_to_fit : null} />
      <DecisionDetail d={d} evidenceLine={evidence} />
    </DecisionShell>
  );
}

export function QualiLog({ decisions, code }: { decisions: Decision[]; code: string }) {
  return (
    <DecisionLog decisions={decisions} code={code} rows={(d) => ({
      when: d.actual?.when ?? "",
      strategy: callText(d.strategy_call, (d.evidence as QualiEvidence | null)?.tire_to_fit),
      you: callText(d.user_call, d.user_tire),
      real: d.actual?.action ? callText(d.actual.action, d.actual.tire) : "…",
      realTitle: d.actual?.detail,
    })} />
  );
}

/* ---------- scorecard ---------- */

type QualiRow = { n: number; when: string; trigger: string; strategy: string; you: string; real: string;
                  real_detail: string; strategy_ok: boolean; you_ok: boolean };

export function QualiScorecard({ sc, code, onRegrade }: { sc: Scorecard; code: string; onRegrade: () => void }) {
  const st = sc.stats as (Scorecard["stats"] & {
    your_tire_vs_real?: Rate; strategy_tire_vs_real?: Rate;
    real_best?: { time: string; compound: string; segment: string; set_at_in_segment: string } | null;
  }) | null;
  const rows = (st?.rows ?? []) as unknown as QualiRow[];
  const v = sc.verdict;
  const fin = st?.real_finish as unknown as { pos: string; q1: string | null; q2: string | null; q3: string | null } | undefined;
  return (
    <section className="card accent">
      <div className="spread">
        <div className="eyebrow" style={{ color: "var(--accent)" }}>{Icon.flag(12)} Chequered flag · Qualifying scorecard</div>
        {fin && <span className="pill neutral">Real {code}: P{fin.pos}</span>}
      </div>
      {sc.status === "grading" && <div className="thinking"><span className="pulse" /> The Grader is reading the final classification…</div>}
      {sc.status === "error" && <div className="toast">Grading failed: {sc.error} <button className="btn sm" onClick={onRegrade}>Grade again</button></div>}
      {v?.headline && <div className="headline">{v.headline}</div>}
      {st && st.decision_points > 0 && (
        <Tiles items={[
          { label: "You vs real team's call", rate: st.you_vs_real, me: true },
          { label: "Strategy vs real team's call", rate: st.strategy_vs_real },
          { label: "You vs Strategy", rate: st.you_vs_strategy },
        ]} />
      )}
      {st && (st.your_tire_vs_real || st.strategy_tire_vs_real) && (
        <div className="kv" style={{ marginTop: "0.7rem" }}>
          <dt>Your tire, when you both went out</dt><dd className="num">{st.your_tire_vs_real?.of ? `${st.your_tire_vs_real.matched}/${st.your_tire_vs_real.of}` : "–"}</dd>
          <dt>Strategy's tire, when both went out</dt><dd className="num">{st.strategy_tire_vs_real?.of ? `${st.strategy_tire_vs_real.matched}/${st.strategy_tire_vs_real.of}` : "–"}</dd>
        </div>
      )}
      {v?.biggest_call && (
        <div className="biggest" style={{ marginTop: "0.8rem" }}>
          <div className="eyebrow">Biggest call · #{v.biggest_call_lap}</div>
          <div style={{ marginTop: "0.3rem" }}>{v.biggest_call}</div>
        </div>
      )}
      {fin && (
        <p className="small muted" style={{ margin: "0.7rem 0 0" }}>
          Real {code}: P{fin.pos} · {(["q1", "q2", "q3"] as const).map((k) => fin[k] ? `${k.toUpperCase()} ${fin[k]}` : null).filter(Boolean).join(" · ")}
          {st?.real_best && <> · best {st.real_best.time} on {st.real_best.compound}, {st.real_best.set_at_in_segment} into {st.real_best.segment}</>}
        </p>
      )}
      {v?.your_calls && <p className="small" style={{ margin: "0.5rem 0 0" }}><span className="eyebrow">Your calls</span><br />{v.your_calls}</p>}
      {v?.strategy_calls && <p className="small" style={{ margin: "0.5rem 0 0" }}><span className="eyebrow">Strategy agent</span><br />{v.strategy_calls}</p>}
      <TokenMeter usage={v?.usage} />
      {rows.length > 0 && (
        <More summary={<>Every decision ({rows.length})</>}>
          <div className="log" style={{ marginTop: "0.5rem" }}>
            {rows.map((r) => (
              <div key={r.n} className="log-item" style={{ padding: "0.55rem 0" }}>
                <div className="when">#{r.n} · {r.when}</div>
                <div className="small">{r.trigger}</div>
                <div className="calls">
                  <div className={`callbox ${r.strategy_ok ? "hit" : "miss"}`}><div className="who">Strategy</div><div className="what">{r.strategy}</div></div>
                  <div className={`callbox ${r.you_ok ? "hit" : "miss"}`}><div className="who">You</div><div className="what">{r.you}</div></div>
                  <div className="callbox" title={r.real_detail}><div className="who">Real</div><div className="what">{r.real}</div></div>
                </div>
              </div>
            ))}
          </div>
        </More>
      )}
    </section>
  );
}
