import { useEffect, useState, type ReactNode } from "react";
import { api, type DriverFocus, type Preview, type SimState } from "./api";
import { teamColor } from "./Favorites";
import { tireWord } from "./Moment";
import { Icon, More, TireDot } from "./ui";

const fmtLap = (s?: number | null) => {
  if (s == null) return "–";
  const m = Math.floor(s / 60);
  return m ? `${m}:${(s - m * 60).toFixed(3).padStart(6, "0")}` : s.toFixed(3);
};

/** The pace in a few words, for the simple view (the exact numbers are under Show more). */
function simplePace(p: NonNullable<DriverFocus["pace"]>): string {
  if (p.direction === "improving") return "Getting faster, lap after lap.";
  if (p.direction === "worsening") return "Getting slower: the tires are wearing.";
  if (p.direction === "steady") return "Steady: lapping at a consistent pace.";
  if (/safety car|VSC|pit stop|neutralised/i.test(p.text)) return "Hard to read right now: a safety car or a pit stop is in the recent laps.";
  return "Too early to tell on these tires.";
}

/* ---------- driver focus: their race as it unfolds ---------- */

export function DriverPanel({ sim, code, followed, team, onFocus, onAsk }: {
  sim: SimState; code: string; followed: string; team?: string;
  onFocus: (code: string | null) => void; onAsk: (q: string) => void;
}) {
  const [f, setF] = useState<DriverFocus | null>(null);
  // refresh whenever the replay moves (a lap, or session time in qualifying)
  const tick = `${sim.clock.current_lap}|${sim.clock.session_time_utc ?? ""}|${sim.clock.driver_id}|${code}`;
  useEffect(() => {
    let live = true;
    api.driver(code).then((d) => live && setF(d)).catch(() => {});
    return () => { live = false; };
  }, [tick]);  // eslint-disable-line react-hooks/exhaustive-deps

  const color = teamColor(f?.team ?? team);
  // newcomers see the simple version first; the dense detail is one tap away (remembered on this device)
  const [more, setMore] = useState(() => localStorage.getItem("pitwall.panelMore") === "1");
  const toggleMore = () => setMore((m) => { localStorage.setItem("pitwall.panelMore", m ? "0" : "1"); return !m; });
  const isFollowed = code === followed;
  return (
    <section className="card driver-panel" style={{ ["--tc" as string]: color }}>
      <div className="spread">
        <div className="row" style={{ gap: "0.6rem" }}>
          <span className="dp-code">{code}</span>
          <div style={{ minWidth: 0 }}>
            <div className="eyebrow">{isFollowed ? "Following" : "Spotlight"} · {f?.team ?? team ?? ""}</div>
            <div className="small muted">{f?.phase === "qualifying" ? "Their qualifying so far" : "Their race so far"}</div>
          </div>
        </div>
        {!isFollowed && <button className="btn sm ghost" onClick={() => onFocus(null)}>Back to {followed}</button>}
      </div>
      {!f && <div className="thinking"><span className="pulse" /> Reading the timing…</div>}
      {f?.refused && <p className="small muted">{f.reason}</p>}
      {f && !f.refused && (
        <>
          <p className="dp-text">{f.text}</p>
          {f.phase === "racing" && (
            <div className="dp-stats">
              <Stat label="Position" value={`P${f.position}`} />
              <Stat label="Tires" value={<><TireDot compound={f.compound ?? null} /> {tireWord(f.compound)}</>}
                    sub={f.tire_age != null ? `${f.tire_age} laps old` : undefined} />
              <Stat label={f.ahead ? `Behind ${f.ahead.code}` : "Out front"} value={f.ahead ? `${f.ahead.gap_s?.toFixed(1) ?? "–"}s` : "Leading"}
                    sub={f.ahead ? (f.ahead.gap_s != null && f.ahead.gap_s < 1 ? "close enough to attack" : "gap to the car ahead") : undefined} />
              {more && <>
                <Stat label="Lap" value={<>{f.lap}<span className="dim">/{f.total_laps}</span></>} />
                <Stat label="Stops" value={f.stops ?? 0} />
                <Stat label="To the car behind" value={f.behind ? `${f.behind.gap_s?.toFixed(3) ?? "–"}s` : "–"} sub={f.behind?.code} />
              </>}
            </div>
          )}
          {f.phase === "qualifying" && (
            <div className="dp-stats">
              <Stat label={f.segment ?? "Session"} value={f.best ? `P${f.position}` : "No time"} />
              <Stat label="Best lap" value={f.best ?? "–"} sub={tireWord(f.best_compound)} />
              <Stat label="Now" value={f.status === "garage" ? "Garage" : f.status === "out lap" ? "Out lap" : "On a lap"} />
              {more && <>
                <Stat label="Tires" value={<><TireDot compound={f.compound ?? null} /> {f.tire_age ?? ""}</>} sub={tireWord(f.compound)} />
                {f.cut && <Stat label={`Cut: top ${f.cut.cars_through}`} value={f.cut.inside ? "Safe for now" : "In the drop zone"}
                                sub={f.cut.line_code ? `${f.cut.line_code} ${f.cut.line_time} on the line` : undefined} />}
                <Stat label="Laps this segment" value={f.laps_this_segment ?? 0} />
              </>}
            </div>
          )}
          {f.pace && (
            <div className={`dp-pace ${f.pace.direction ?? ""}`}>
              <div className="eyebrow">Pace</div>
              <div className="small">{more ? f.pace.text : simplePace(f.pace)}</div>
            </div>
          )}
          {more && f.recent_laps && f.recent_laps.length > 0 && (
            <div className="dp-laps">
              {f.recent_laps.map((l, i) => (
                <div key={i} className={`dp-lap ${l.neutralised ? "neutral" : ""} ${l.pit ? "pit" : ""}`}
                     title={l.neutralised ? "Safety car / VSC lap" : l.pit ? "Pit lap" : undefined}>
                  <span className="dim tiny">{f.phase === "qualifying" ? (l.segment ?? "") : `L${l.lap}`}</span>
                  <span className="mono">{f.phase === "qualifying" ? (l.time ?? "–") : fmtLap(l.time_s)}</span>
                  {l.neutralised && <span className="tiny tag">SC</span>}
                  {l.pit && <span className="tiny tag">PIT</span>}
                </div>
              ))}
            </div>
          )}
          {(f.notes?.length ?? 0) > 0 && (
            <div className="dp-notes">
              <div className="eyebrow">Race control, about {f.code}</div>
              <ul>{f.notes!.map((n, i) => <li key={i} className="small"><span className="mono dim">{n.when}</span> {n.plain}
                {more && <div className="tiny dim">{n.message}</div>}</li>)}</ul>
            </div>
          )}
          {more && f.flags?.note && <p className="tiny muted" style={{ margin: "0.5rem 0 0" }}>{f.flags.note}</p>}
          <button className="link small dp-more" onClick={toggleMore} aria-expanded={more}>
            {more ? "Show less" : f.phase === "qualifying" ? "Show more: tires, the cut line, recent laps" : "Show more: lap times, stops, the car behind"}
          </button>
          <div className="row" style={{ marginTop: "0.7rem", gap: "0.4rem" }}>
            <button className="chip" onClick={() => onAsk(`How is ${f.code}'s race going?`)}>{Icon.chat(12)} How's it going for {f.code}?</button>
            {f.phase === "racing" && <button className="chip" onClick={() => onAsk(`Why isn't ${f.code} pitting?`)}>{Icon.chat(12)} Why isn't {f.code} pitting?</button>}
          </div>
          {more && <p className="tiny dim" style={{ margin: "0.5rem 0 0" }}>Straight from the timing data up to now. Live speed isn't in the data, so pace comes from lap times. Tap any car in the order to spotlight it.</p>}
        </>
      )}
    </section>
  );
}

function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="dp-stat">
      <div className="lbl">{label}</div>
      <div className="val">{value}</div>
      {sub && <div className="tiny dim">{sub}</div>}
    </div>
  );
}

/* ---------- before the start: what to watch ---------- */

export function PreviewCard({ sim, onAsk }: { sim: SimState; onAsk: (q: string) => void }) {
  const [p, setP] = useState<Preview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const key = `${sim.clock.race_id}|${sim.clock.driver_id}|${sim.clock.session_kind}`;
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = () => api.preview().then((x) => {
      if (!live) return;
      setP(x); setErr(null);
      if (!x.read || x.read.status === "writing") timer = setTimeout(load, 2500);  // the AI's read is on its way
    }).catch((e: Error) => live && setErr(e.message));
    setP(null); load();
    return () => { live = false; clearTimeout(timer); };
  }, [key]);

  if (err) return <section className="card"><div className="toast">{err}</div></section>;
  if (!p) return <section className="card"><div className="thinking"><span className="pulse" /> Getting the preview…</div></section>;
  const d = p.data;
  const quali = d.session === "qualifying";
  const me = d.drivers.find((x) => x.is_my_driver);
  const shown = quali ? d.drivers.slice(0, 10) : d.drivers.slice(0, 10);
  if (me && !shown.includes(me)) shown.push(me);
  const r = p.read;
  return (
    <section className="card preview">
      <div className="eyebrow" style={{ color: "var(--accent)" }}>Before the start · {d.race}</div>
      <h2 className="preview-title">{quali ? "Who to watch in qualifying" : "The grid, and what to watch"}</h2>

      <div className="ai-read">
        <div className="spread">
          <span className="pill accent">{r?.read?.quick ? <>Quick read from the data</> : <>{Icon.spark(11)} The AI's prediction, not fact</>}</span>
          <span className="tiny dim">From {quali ? "" : "the grid, qualifying, "}form and the championship only</span>
        </div>
        {(!r || r.status === "writing") && <div className="thinking"><span className="pulse" /> Reading the grid and the form…</div>}
        {r?.status === "error" && <div className="small muted">Couldn't write the read this time. <button className="link" onClick={() => api.previewRetry().then(setP)}>Try again</button></div>}
        {r?.status === "ready" && r.read && (
          <>
            <div className="headline" style={{ marginTop: "0.5rem" }}>{r.read.headline}</div>
            <div className="stack" style={{ gap: "0.6rem" }}>
              {r.read.storylines.map((s, i) => (
                <div key={i}><div className="recap-title">{s.title}</div><p className="small" style={{ margin: "0.2rem 0 0" }}>{s.text}</p></div>
              ))}
            </div>
            <div className="eyebrow" style={{ marginTop: "0.9rem" }}>Who to watch</div>
            <ul className="watch">
              {r.read.watch.map((w) => {
                const x = d.drivers.find((y) => y.code === w.code);
                return <li key={w.code} style={{ ["--tc" as string]: teamColor(x?.team) }}><b>{w.code}</b> <span className="small">{w.why}</span></li>;
              })}
            </ul>
            <More summary={<>Facts the read cites ({r.read.cited.length}), each from the data</>}>
              <ul className="small">{r.read.cited.map((c) => <li key={c.key}><code className="k">{c.kind}</code> {c.text}</li>)}</ul>
            </More>
          </>
        )}
      </div>

      <div className="eyebrow" style={{ margin: "1rem 0 0.4rem" }}>{quali ? "Championship going in" : "Starting grid"}</div>
      <div className="pv-table">
        <div className="pv-row head">
          <span>{quali ? "Champ" : "Grid"}</span><span>Driver</span>{!quali && <span className="hide-sm">Qualifying</span>}
          {!quali && <span>Champ</span>}<span>Last 3 GPs</span>
        </div>
        {shown.map((x) => (
          <div key={x.code} className={`pv-row ${x.is_my_driver ? "mine" : ""}`} style={{ ["--tc" as string]: teamColor(x.team) }}>
            <span className="mono">{quali ? (x.championship?.position ? `P${x.championship.position}` : "–") : x.pit_lane_start ? "PL" : `P${x.grid}`}</span>
            <span><i className="bar" /> <b>{x.code}</b> <span className="dim small hide-sm">{x.name.split(" ").slice(-1)[0]}</span></span>
            {!quali && <span className="mono small hide-sm">{x.qualifying ? (x.qualifying.best ? `${x.qualifying.best} ${x.qualifying.best_in}` : `P${x.qualifying.position}`) : "–"}</span>}
            {!quali && <span className="mono small">{x.championship?.position ? `P${x.championship.position} · ${x.championship.points}` : "–"}</span>}
            <span className="form">{x.last_races.map((f, i) => <span key={i} className={`fchip ${f.finish === "P1" ? "win" : /^P[23]$/.test(f.finish) ? "pod" : /^P\d+$/.test(f.finish) ? "" : "dnf"}`} title={f.race}>{f.finish}</span>)}</span>
          </div>
        ))}
      </div>
      <p className="tiny dim" style={{ margin: "0.5rem 0 0" }}>{d.note} Champ = championship place and points going into this weekend.</p>
      <div className="row" style={{ marginTop: "0.7rem", gap: "0.4rem" }}>
        <button className="chip" onClick={() => onAsk(quali ? "How does qualifying work?" : "Why does grid position matter so much?")}>{Icon.chat(12)} {quali ? "How does qualifying work?" : "Why does grid position matter?"}</button>
        {me && <button className="chip" onClick={() => onAsk(`How has ${me.code} been doing this season?`)}>{Icon.chat(12)} How has {me.code} been doing?</button>}
      </div>
      {r?.read?.usage && <div className="token-meter">preview read · ${r.read.usage.total.cost_usd?.toFixed(5)}</div>}
    </section>
  );
}
