import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type Entry, type Session } from "./api";
import { teamColor } from "./Favorites";
import { Icon, Seg, Select, Sheet } from "./ui";

export type Tab = "races" | "learn" | "standings" | "watch";

export type Round = {
  race_id: number; round: number; name: string; circuit_name: string | null; locality: string | null;
  country: string | null; date: string; has_sprint: boolean;
  sessions: { kind: Session; start_utc: string | null; watchable: boolean }[];
};

const FLAG: Record<string, string> = {
  Australia: "🇦🇺", China: "🇨🇳", Japan: "🇯🇵", Bahrain: "🇧🇭", "Saudi Arabia": "🇸🇦", USA: "🇺🇸", "United States": "🇺🇸",
  Italy: "🇮🇹", Monaco: "🇲🇨", Spain: "🇪🇸", Canada: "🇨🇦", Austria: "🇦🇹", UK: "🇬🇧", "United Kingdom": "🇬🇧",
  Belgium: "🇧🇪", Hungary: "🇭🇺", Netherlands: "🇳🇱", Azerbaijan: "🇦🇿", Singapore: "🇸🇬", Mexico: "🇲🇽",
  Brazil: "🇧🇷", Qatar: "🇶🇦", UAE: "🇦🇪", "United Arab Emirates": "🇦🇪",
};
const SESSION_LABEL: Record<Session, string> = { race: "Race", qualifying: "Qualifying", sprint: "Sprint" };
const fmtDate = (d: string, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }) =>
  new Date(`${d}T12:00:00Z`).toLocaleDateString(undefined, opts);
const shortName = (n: string) => n.replace(" Grand Prix", " GP");

/* ---------- bottom navigation ---------- */

export function BottomNav({ tab, onTab, watching }: { tab: Tab; onTab: (t: Tab) => void; watching: boolean }) {
  const items: { id: Tab; label: string; icon: ReactNode }[] = [
    { id: "races", label: "Races", icon: Icon.flag(20) },
    { id: "learn", label: "Learn", icon: Icon.book(20) },
    { id: "standings", label: "Standings", icon: Icon.trophy(20) },
  ];
  return (
    <nav className="bottomnav" aria-label="Main">
      {items.map((it) => {
        const on = tab === it.id || (it.id === "races" && tab === "watch");
        return (
          <button key={it.id} className={`bn-item ${on ? "on" : ""}`} onClick={() => onTab(it.id)} aria-current={on ? "page" : undefined}>
            <span className="bn-icon">{it.icon}{it.id === "races" && watching && <i className="bn-live" />}</span>
            <span className="bn-label">{it.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

/* ---------- Races: the season as cards ---------- */

export function RacesPage({ rounds, onOpen, watching, onResume }: {
  rounds: Round[] | null; onOpen: (r: Round) => void;
  watching: { name: string; label: string } | null; onResume: () => void;
}) {
  const [filter, setFilter] = useState<"all" | "sprint">("all");
  const shown = (rounds ?? []).filter((r) => filter === "all" || r.has_sprint);
  return (
    <div className="stack page">
      <div>
        <div className="eyebrow" style={{ color: "var(--accent)" }}>2025 season · {rounds?.length ?? "–"} weekends</div>
        <h1 className="page-title">Pick a race weekend</h1>
        <p className="muted" style={{ margin: 0 }}>Open a weekend, choose a session and a driver to follow, and watch it unfold with every key moment explained.</p>
      </div>
      {watching && (
        <button className="resume card" onClick={onResume}>
          <span className="pulse" />
          <span style={{ minWidth: 0, textAlign: "left" }}>
            <span className="eyebrow">Now watching</span>
            <span className="resume-name">{watching.name}</span>
            <span className="small muted">{watching.label}</span>
          </span>
          <span className="btn sm">{Icon.play(13)} Back to it</span>
        </button>
      )}
      <Seg value={filter} onChange={setFilter} options={[{ value: "all", label: "All weekends" }, { value: "sprint", label: "Sprint weekends" }]} />
      {!rounds && <div className="thinking"><span className="pulse" /> Loading the calendar…</div>}
      <div className="race-grid">
        {shown.map((r) => (
          <button key={r.race_id} className="race-card" onClick={() => onOpen(r)}>
            <div className="spread">
              <span className="rc-round">R{String(r.round).padStart(2, "0")}</span>
              <span className="rc-date mono">{fmtDate(r.date)}</span>
            </div>
            <div className="rc-flag" aria-hidden>{FLAG[r.country ?? ""] ?? "🏁"}</div>
            <div className="rc-name">{shortName(r.name)}</div>
            <div className="rc-place small muted">{r.locality ? `${r.locality}, ` : ""}{r.country}</div>
            <div className="rc-sessions">
              {r.sessions.map((s) => (
                <span key={s.kind} className={`sess ${s.kind} ${s.watchable ? "" : "off"}`} title={s.watchable ? undefined : "No timing data for this session"}>
                  {s.kind === "qualifying" ? "Quali" : SESSION_LABEL[s.kind]}
                </span>
              ))}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------- a weekend: pick a session and a driver, then watch ---------- */

export function WeekendSheet({ round, session, onSession, entries, driverId, onDriver, onWatch, onClose, sameAsLoaded,
                               onRestart, favorites }: {
  round: Round; session: Session; onSession: (s: Session) => void; entries: Entry[]; driverId: string;
  onDriver: (id: string) => void; onWatch: () => void; onClose: () => void; sameAsLoaded: boolean;
  onRestart: () => void; favorites: ReactNode;
}) {
  const sess = round.sessions.find((s) => s.kind === session);
  return (
    <Sheet title={<span className="row">{FLAG[round.country ?? ""] ?? "🏁"} {round.name}</span>} onClose={onClose}
      footer={sameAsLoaded ? (
        <div className="row" style={{ gap: "0.5rem", width: "100%" }}>
          <button className="btn lg" style={{ flex: 1 }} onClick={onClose}>{Icon.play(15)} Keep watching</button>
          <button className="btn lg ghost" onClick={onRestart}>Restart</button>
        </div>
      ) : (
        <button className="btn lg block" disabled={!driverId || !sess?.watchable} onClick={onWatch}>
          {Icon.play(15)} Watch the {SESSION_LABEL[session].toLowerCase()}
        </button>
      )}>
      <p className="muted small" style={{ margin: 0 }}>
        Round {round.round} · {round.circuit_name} · {fmtDate(round.date, { weekday: "short", day: "numeric", month: "long", year: "numeric" })}
      </p>
      <div className="field"><span className="label">Session</span>
        <Seg block value={session} onChange={onSession}
             options={round.sessions.map((s) => ({ value: s.kind, label: <>{SESSION_LABEL[s.kind]}{!s.watchable && <span className="dim"> ·no data</span>}</> }))} />
      </div>
      {sess && !sess.watchable && <div className="toast">There's no timing data for this session, so it can't be replayed. Try another session.</div>}
      <p className="small muted" style={{ margin: 0 }}>
        {session === "race" ? "The Grand Prix, lap by lap. You'll see the grid and the AI's pre-race read first."
          : session === "sprint" ? "The short Saturday race: about a third of the distance, no mandatory stop."
          : "The knockout session that sets the grid: Q1, Q2 and Q3."}
      </p>
      <Select label="Follow driver" value={driverId} onChange={onDriver}
              options={entries.map((e) => ({
                value: e.driver_id,
                label: <><span style={{ width: 4, height: 16, borderRadius: 2, background: teamColor(e.team), display: "inline-block" }} />
                  <b className="mono">{e.code}</b> {e.name}</>,
                sub: session === "qualifying" ? <span className="dim">{e.team}</span> : <span className="mono">{e.grid === 0 ? "PL" : `P${e.grid}`}</span>,
              }))} />
      {favorites}
      {sameAsLoaded && <p className="tiny dim" style={{ margin: 0 }}>Already playing: picking a driver follows them from right now.</p>}
    </Sheet>
  );
}

/* ---------- Learn: F1 explained, from the rules file ---------- */

type Rule = { id: string; title: string; text: string };
const GROUPS: { title: string; blurb: string; ids: string[] }[] = [
  { title: "Strategy", blurb: "Why teams pit when they do, and how places are won in the pit lane.",
    ids: ["box", "pit_stop", "stint", "pit_window", "undercut", "overcut", "track_position", "out_lap", "gap_interval"] },
  { title: "Tires", blurb: "The single biggest thing on the pit wall's mind.",
    ids: ["tire_compounds", "compound_rule", "tire_degradation"] },
  { title: "Flags and safety", blurb: "What race control is telling the drivers, and why the cars slow down.",
    ids: ["flags", "safety_car", "vsc", "red_flag", "blue_flags", "track_limits", "penalties"] },
  { title: "Rules and format", blurb: "How a weekend works and how the championship is decided.",
    ids: ["qualifying", "sprint", "points", "championships", "drs", "track_evolution", "parc_ferme", "classification"] },
  { title: "A bit of history", blurb: "", ids: ["history_first_race", "history_most_titles"] },
];

export function LearnPage({ onAsk }: { onAsk: (q: string) => void }) {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { api.learn().then((r) => setRules(r.entries)).catch(() => setRules([])); }, []);
  const byId = useMemo(() => new Map((rules ?? []).map((r) => [r.id, r])), [rules]);
  const match = (r: Rule) => !q.trim() || `${r.title} ${r.text}`.toLowerCase().includes(q.trim().toLowerCase());
  const grouped = GROUPS.map((g) => ({ ...g, rules: g.ids.map((id) => byId.get(id)).filter((r): r is Rule => !!r && match(r)) }));
  const listed = new Set(GROUPS.flatMap((g) => g.ids));
  const other = (rules ?? []).filter((r) => !listed.has(r.id) && match(r));
  return (
    <div className="stack page">
      <div>
        <div className="eyebrow" style={{ color: "var(--accent)" }}>F1 explained</div>
        <h1 className="page-title">New to Formula 1? Start here.</h1>
        <p className="muted" style={{ margin: 0 }}>
          The handful of ideas that make a race make sense, in plain words. Tap one to read it; ask Ask AI anything that's still fuzzy.
        </p>
      </div>
      <input className="input" placeholder="Search: undercut, safety car, points…" value={q} onChange={(e) => setQ(e.target.value)} />
      {!rules && <div className="thinking"><span className="pulse" /> Loading…</div>}
      {[...grouped, ...(other.length ? [{ title: "More", blurb: "", ids: [], rules: other }] : [])].filter((g) => g.rules.length).map((g) => (
        <section key={g.title} className="card learn-group">
          <h2 style={{ fontSize: "1.05rem" }}>{g.title}</h2>
          {g.blurb && <p className="small muted" style={{ margin: "0.2rem 0 0.6rem" }}>{g.blurb}</p>}
          <div className="learn-list">
            {g.rules.map((r) => {
              const isOpen = open === r.id || !!q.trim();
              return (
                <div key={r.id} className={`learn-item ${isOpen ? "open" : ""}`}>
                  <button className="learn-head" onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={isOpen}>
                    <span>{r.title}</span><span className="chev">{Icon.chev(16)}</span>
                  </button>
                  {isOpen && (
                    <div className="learn-body">
                      <p style={{ margin: 0 }}>{r.text}</p>
                      <button className="chip" style={{ marginTop: "0.6rem" }} onClick={() => onAsk(`Explain ${r.title.toLowerCase()} like I'm new to F1, with an example.`)}>
                        {Icon.chat(12)} Ask AI about this</button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}
      <p className="tiny dim" style={{ margin: 0 }}>From Pit Wall's rules file, written for the 2025 season. Ask AI answers rules questions from these same entries.</p>
    </div>
  );
}

/* ---------- Standings: season reference, results behind a tap ---------- */

export type Standings = {
  after_round: number; last_round: number;
  drivers: { position: number; code: string; name: string; team: string; points: number; wins: number; podiums: number }[];
  constructors: { position: number; team: string; points: number; wins: number }[];
};
export type Podium = { race?: { pos: string; code: string; name: string; team: string }[]; sprint?: { pos: string; code: string; name: string; team: string }[];
                pole?: { code: string; name: string } };

export function StandingsPage({ rounds, onOpen }: { rounds: Round[] | null; onOpen: (r: Round) => void }) {
  const [view, setView] = useState<"drivers" | "constructors" | "calendar">("drivers");
  const [after, setAfter] = useState<number | null>(null);
  const [st, setSt] = useState<Standings | null>(null);
  useEffect(() => { api.standings(after).then(setSt).catch(() => {}); }, [after]);
  const roundName = (n: number) => rounds?.find((r) => r.round === n)?.name ?? `round ${n}`;
  return (
    <div className="stack page">
      <div>
        <div className="eyebrow" style={{ color: "var(--accent)" }}>2025 season</div>
        <h1 className="page-title">Standings and calendar</h1>
        <p className="muted" style={{ margin: 0 }}>
          The championship as season reference. Watching an earlier race? Set the standings to before it so nothing gets spoiled.
        </p>
      </div>
      <Seg block value={view} onChange={setView}
           options={[{ value: "drivers", label: "Drivers" }, { value: "constructors", label: "Teams" }, { value: "calendar", label: "Calendar" }]} />
      {view !== "calendar" && st && (
        <Select label="Standings after" value={after ?? st.last_round} onChange={(v) => setAfter(v)}
                options={Array.from({ length: st.last_round }, (_, i) => st.last_round - i).map((n) => ({
                  value: n, label: <>Round {n} · {shortName(roundName(n))}</>, sub: n === st.last_round ? <span className="dim">final</span> : undefined,
                }))} />
      )}
      {view === "drivers" && st && (
        <section className="card flush">
          <div className="st-row head"><span>Pos</span><span>Driver</span><span className="hide-sm">Wins</span><span>Pts</span></div>
          {st.drivers.map((d) => (
            <div key={d.code} className="st-row" style={{ ["--tc" as string]: teamColor(d.team) }}>
              <span className="mono pos">{d.position}</span>
              <span className="who"><i className="bar" /><b>{d.code}</b> <span className="small">{d.name}</span><span className="tiny dim team">{d.team}</span></span>
              <span className="mono hide-sm">{d.wins}</span>
              <span className="mono pts">{d.points}</span>
            </div>
          ))}
        </section>
      )}
      {view === "constructors" && st && (
        <section className="card flush">
          <div className="st-row head"><span>Pos</span><span>Team</span><span className="hide-sm">Wins</span><span>Pts</span></div>
          {st.constructors.map((t) => (
            <div key={t.team} className="st-row" style={{ ["--tc" as string]: teamColor(t.team) }}>
              <span className="mono pos">{t.position}</span>
              <span className="who"><i className="bar" /><b>{t.team}</b></span>
              <span className="mono hide-sm">{t.wins}</span>
              <span className="mono pts">{t.points}</span>
            </div>
          ))}
        </section>
      )}
      {view !== "calendar" && st && <p className="tiny dim" style={{ margin: 0 }}>Points from every Grand Prix and sprint up to round {st.after_round}.</p>}
      {view === "calendar" && (
        <section className="card flush">
          {(rounds ?? []).map((r) => <CalendarRow key={r.race_id} r={r} onOpen={onOpen} />)}
        </section>
      )}
    </div>
  );
}

function CalendarRow({ r, onOpen }: { r: Round; onOpen: (r: Round) => void }) {
  const [podium, setPodium] = useState<Podium | null>(null);
  const [busy, setBusy] = useState(false);
  const reveal = () => { setBusy(true); api.podium(r.race_id).then(setPodium).finally(() => setBusy(false)); };
  return (
    <div className="cal-row">
      <div className="spread">
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">R{r.round} · {fmtDate(r.date)}{r.has_sprint ? " · sprint weekend" : ""}</div>
          <div className="cal-name">{FLAG[r.country ?? ""] ?? "🏁"} {r.name}</div>
        </div>
        <button className="btn sm ghost" onClick={() => onOpen(r)}>{Icon.play(12)} Watch</button>
      </div>
      {!podium ? (
        <button className="link small" onClick={reveal} disabled={busy}>{busy ? "Revealing…" : "Reveal result"}</button>
      ) : (
        <div className="podium">
          {podium.race && <div><span className="eyebrow">Grand Prix</span> {podium.race.map((p) => (
            <span key={p.code} className="pod" style={{ ["--tc" as string]: teamColor(p.team) }}><i className="bar" />P{p.pos} <b>{p.code}</b></span>))}</div>}
          {podium.sprint && <div><span className="eyebrow">Sprint</span> {podium.sprint.map((p) => (
            <span key={p.code} className="pod" style={{ ["--tc" as string]: teamColor(p.team) }}><i className="bar" />P{p.pos} <b>{p.code}</b></span>))}</div>}
          {podium.pole && <div className="small muted">Pole: <b>{podium.pole.code}</b> {podium.pole.name}</div>}
          <button className="link tiny" onClick={() => setPodium(null)}>Hide</button>
        </div>
      )}
    </div>
  );
}
