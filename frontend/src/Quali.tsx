import { Fragment, type ReactNode } from "react";
import { api, type Decision, type Entry, type Scorecard, type SimState } from "./api";
import { teamColor } from "./Favorites";
import { ExplanationCard, Recap, tireWord, type MomentText } from "./Moment";
import { Icon, Seg, TireDot, WeatherBadge } from "./ui";

type QualiEvidence = NonNullable<Decision["evidence"]> & {
  segment?: string; time_left?: string; status?: string; best?: string | null; cut_margin_s?: number | null; when?: string;
};

/* ---------- control bar ---------- */

export function QualiControl({ sim, code, run, children }: {
  sim: SimState; code: string; run: (p: Promise<unknown>) => void; children?: ReactNode;
}) {
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
          <span className="chip team" style={{ ["--chip" as string]: "var(--accent)", justifySelf: "end" }}><span className="dot" />Following {code}</span>
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
        <button className="btn ghost" disabled={!!pending || over} onClick={() => run(api.ask())}>{Icon.spark(15)} Explain now</button>
        {over && <span className="pill accent">{Icon.flag(12)} Chequered flag</span>}
      </div>
      {children}
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

/* ---------- what's happening ---------- */

const qLean = (call?: string | null, tire?: string | null) =>
  !call ? "–" : call === "GO_OUT" ? (tire ? `Go out on ${tireWord(tire)}` : "Go out") : call === "WAIT" ? "Wait in the garage"
    : call === "CONTINUE" ? "Keep going on this lap" : call === "ABORT" ? "Abort the lap" : call;

export const qualiText = (code: string): MomentText => ({
  when: (d) => d.actual?.when ?? (d.evidence as QualiEvidence | null)?.when ?? "qualifying",
  lean: (d) => qLean(d.strategy_call, (d.evidence as QualiEvidence | null)?.tire_to_fit),
  did: (d) => {
    const a = d.actual;
    if (!a?.action) return null;
    const what = qLean(a.action, a.tire).replace("Go out", "Went out").replace("Wait in", "Waited in")
      .replace("Keep going on", "Finished").replace("Abort", "Aborted");
    return a.detail ? `${what} (${a.detail})` : what;
  },
  revealAt: () => "about two and a half minutes of session time from here",
  askWhy: (d) => {
    const a = d.actual?.action;
    return a === "GO_OUT" ? `Why did ${code} go out ${d.actual?.tire ? `on ${tireWord(d.actual.tire)} ` : ""}then?`
      : a === "WAIT" ? `Why did ${code} wait in the garage?` : a === "ABORT" ? `Why did ${code} abort that lap?`
      : `Why did ${code} keep going on that lap?`;
  },
});

export function QualiMoment({ d, code, run, onAsk }: {
  d: Decision; code: string; run: (p: Promise<unknown>) => void; onAsk: (q: string) => void;
}) {
  const ev = d.evidence as QualiEvidence | null;
  const where = ev && (
    <>{code} at the time: {ev.segment ?? "before the session"}{ev.time_left ? `, about ${ev.time_left} left` : ""} · {ev.position ? `P${ev.position}` : "no time yet"} · {ev.status} on {ev.compound?.toLowerCase() ?? "?"}
      {ev.best ? ` · best ${ev.best}` : ""}{ev.cut_margin_s != null ? ` · ${Math.abs(ev.cut_margin_s)}s ${ev.cut_margin_s >= 0 ? "inside" : "outside"} the cut` : ""}</>
  );
  return <ExplanationCard d={d} code={code} text={qualiText(code)} extra={where}
                          onContinue={() => run(api.continueWatching())} onAsk={onAsk} />;
}

/* ---------- strategy recap ---------- */

export function QualiRecap({ sc, code, onRetry }: { sc: Scorecard; code: string; onRetry: () => void }) {
  const st = sc.stats as (Scorecard["stats"] & {
    real_best?: { time: string; compound: string; segment: string; set_at_in_segment: string } | null;
  }) | null;
  const fin = st?.real_finish as unknown as { pos: string; q1: string | null; q2: string | null; q3: string | null } | undefined;
  return (
    <Recap sc={sc} title="Chequered flag · Qualifying recap" onRetry={onRetry} momentLabel={(n) => `Moment ${n}`}
           finish={fin && <>{code} qualified P{fin.pos} · {(["q1", "q2", "q3"] as const).map((k) => fin[k] ? `${k.toUpperCase()} ${fin[k]}` : null).filter(Boolean).join(" · ")}
             {st?.real_best && <> · best lap {st.real_best.time} on {tireWord(st.real_best.compound)}, {st.real_best.set_at_in_segment} into {st.real_best.segment}</>}</>} />
  );
}
