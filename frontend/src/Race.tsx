import type { ReactNode } from "react";
import { api, type Decision, type Entry, type RaceState, type Scorecard, type SimState } from "./api";
import { teamColor } from "./Favorites";
import { ExplanationCard, Recap, tireWord, type MomentText } from "./Moment";
import { Icon, Seg, TireDot, WeatherBadge } from "./ui";

const fmtGap = (s?: number | null) => (s == null ? "" : `+${s.toFixed(3)}`);

/* ---------- control bar ---------- */

export function RaceControl({ sim, code, run, sprint, children }: {
  sim: SimState; code: string; run: (p: Promise<unknown>) => void; sprint: boolean; children?: ReactNode;
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
            <span className="dot" />Following {code}
          </span>
          {w && <WeatherBadge trackC={w.track_temp_c} raining={w.raining} recent={w.rain_in_last_5_min} />}
        </div>
      </div>
      <div className="progress" style={{ margin: "0.8rem 0" }}><i style={{ width: `${pct}%` }} /></div>
      {c.current_lap === 0 && !c.running && !pending && (
        <p className="muted small" style={{ margin: "0 0 0.7rem" }}>
          On the grid. Start the race and it plays back lap by lap. At each key moment it pauses to explain what's going on and why.
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
          {Icon.spark(15)} Explain now
        </button>
        {over && <span className="pill accent">{Icon.flag(12)} Chequered flag</span>}
      </div>
      <div className="dim tiny" style={{ marginTop: "0.5rem" }}>Speed: seconds per lap</div>
      {children}
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

/* ---------- what's happening ---------- */

const leanWord = (call?: string | null, tire?: string | null) =>
  !call ? "–" : call === "BOX" ? (tire ? `Box for ${tireWord(tire)}` : "Box") : call === "STAY_OUT" ? "Stay out"
    : ({ DEFEND: "Stay out and defend", PUSH: "Stay out and push", MANAGE: "Stay out, look after the tires" } as Record<string, string>)[call] ?? call;

export const raceText = (sprint: boolean, code: string): MomentText => ({
  when: (d) => `end of lap ${d.lap}`,
  lean: (d) => leanWord(d.strategy_call, d.evidence?.tire_to_fit),
  did: (d) => {
    const a = d.actual;
    if (sprint) {
      if (!a?.action) return d.actual_pitted === 1 ? "Boxed" : null;
      if (a.action === "BOX") return a.tire ? `Boxed for ${tireWord(a.tire)}` : "Boxed";
      if (a.action === "STAY_OUT") return `Stayed out${a.mode ? ` (${a.mode.toLowerCase()}, from the next laps)` : ""}`;
      return a.action === "OUT" ? "Retired" : a.action;
    }
    if (d.actual_pitted == null) return null;
    if (!d.actual_pitted) return "Stayed out";
    return a?.tire ? `Boxed for ${tireWord(a.tire)}` : "Boxed";
  },
  revealAt: (d) => `once lap ${d.lap + 1} has been played`,
  askWhy: (d) => {
    const pitted = d.actual?.action ? d.actual.action === "BOX" : d.actual_pitted === 1;
    return pitted ? `Why did ${code} box at the end of lap ${d.lap + 1}?` : `Why did ${code} stay out on lap ${d.lap + 1}?`;
  },
});

export function RaceMoment({ d, code, sprint, run, onAsk }: {
  d: Decision; code: string; sprint: boolean; run: (p: Promise<unknown>) => void; onAsk: (q: string) => void;
}) {
  const ev = d.evidence;
  const where = ev && (
    <>{code} at the time: P{ev.position}, {ev.tire_age}-lap-old {ev.compound?.toLowerCase()} tires, {ev.gap_ahead_s == null ? "leading" : `${ev.gap_ahead_s}s behind the car ahead`},
      {" "}{ev.gap_behind_s == null ? "nobody behind" : `${ev.gap_behind_s}s ahead of the car behind`}.</>
  );
  return <ExplanationCard d={d} code={code} text={raceText(sprint, code)} extra={where}
                          onContinue={() => run(api.continueWatching())} onAsk={onAsk} />;
}

/* ---------- strategy recap ---------- */

export function RaceRecap({ sc, code, sprint, onRetry }: { sc: Scorecard; code: string; sprint: boolean; onRetry: () => void }) {
  const st = sc.stats;
  const fin = st?.real_finish;
  return (
    <Recap sc={sc} title={`Chequered flag · ${sprint ? "Sprint" : "Race"} recap`} onRetry={onRetry}
           momentLabel={(n) => `Lap ${n}`}
           finish={fin && <>{code} started P{fin.grid} and finished {fin.pos === "R" ? "with a DNF" : `P${fin.pos}`} ({fin.status}, {fin.points} pts)
             {st?.real_stops && st.real_stops.length > 0 && <> · stops: {st.real_stops.filter((s) => !s.pit_lane_pass).map((s) => `lap ${s.lap}, ${tireWord(s.from_tire) || "?"} → ${tireWord(s.to_tire) || "?"}`).join("; ")}</>}</>} />
  );
}

export type { Entry };
