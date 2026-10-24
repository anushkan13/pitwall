import { useEffect, useState } from "react";
import { accounts, type Account } from "./account";
import { api, type EntryList, type Session, type SimState } from "./api";
import { AskFab, AskSheet, type AskEntry } from "./AskAI";
import { applyAccent, FavoriteChips, FavoritesSheet, teamColor, type Fav } from "./Favorites";
import { AccountSheet, Landing } from "./Landing";
import { QualiControl, QualiDecision, QualiLog, QualiScorecard, QualiTower } from "./Quali";
import { callLabel, DecisionLog, RaceControl, RaceDecision, RaceScorecard, RaceTower, realLabel } from "./Race";
import { Icon, Seg, Select, Sheet, Switch } from "./ui";

const SESSION_LABEL: Record<Session, string> = { race: "Race", qualifying: "Qualifying", sprint: "Sprint" };
const NO_FAV: Fav = { team: "", driver: "" };

export default function App() {
  // who's here: null until they pick on the landing screen
  const [mode, setMode] = useState<"landing" | "app">("landing");
  const [account, setAccount] = useState<Account | null>(null);
  const [fav, setFavState] = useState<Fav>(NO_FAV);

  const [races, setRaces] = useState<{ race_id: number; name: string; season: number; round: number }[]>([]);
  const [raceId, setRaceId] = useState<number | null>(null);
  const [session, setSession] = useState<Session>("race");
  const [defaults, setDefaults] = useState<Record<Session, number> | null>(null);
  const [entries, setEntries] = useState<EntryList | null>(null);
  const [driverId, setDriverId] = useState("");
  const [sim, setSim] = useState<SimState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<AskEntry[]>([]);
  const [showTokens, setShowTokens] = useState(false);
  const [sheet, setSheet] = useState<null | "ask" | "favorites" | "menu" | "signin" | "create">(null);
  const [editSetup, setEditSetup] = useState(false);

  const setFav = (f: Fav) => {
    setFavState(f);
    if (account) accounts.saveFavorites(account.name, f);
  };
  const enter = (a: Account | null) => {
    setAccount(a);
    setFavState(a?.favorites ?? NO_FAV);
    setMode("app");
    setSheet(null);
  };

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
        setError(`Waking the server… (${e.message}, retrying)`);
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

  if (mode === "landing") {
    const current = accounts.current();
    return (
      <>
        <Landing onGuest={() => enter(null)} onAccount={(a) => enter(a)} />
        {current && (
          <div style={{ position: "fixed", left: 0, right: 0, bottom: "calc(1rem + var(--safe-bottom))", textAlign: "center" }}>
            <button className="chip" onClick={() => enter(current)}>{Icon.user(13)} Continue as {current.name}</button>
          </div>
        )}
      </>
    );
  }

  const clock = sim?.clock;
  const loaded = clock?.race_id != null;
  const kind = sim?.mode ?? "race";
  const quali = kind === "qualifying";
  const sprint = kind === "sprint";
  const pending = sim?.pending;
  const myCode = entries?.entries.find((e) => e.driver_id === clock?.driver_id)?.code ?? clock?.driver_id ?? "";
  const loadedRace = races.find((r) => r.race_id === clock?.race_id);
  const showSetup = !loaded || editSetup;

  const setup = (
    <section className="card">
      <div className="card-head">
        <h2>{loaded ? "Change session" : "Set up a session"}</h2>
        {loaded && <button className="icon-btn" onClick={() => setEditSetup(false)} aria-label="Close">{Icon.close(16)}</button>}
      </div>
      <div className="stack">
        <Seg block value={session} onChange={(s) => { setSession(s); if (defaults) setRaceId(defaults[s]); }}
             options={(["race", "qualifying", "sprint"] as Session[]).map((s) => ({ value: s, label: SESSION_LABEL[s] }))} />
        <Select label="Weekend" value={raceId} onChange={setRaceId}
                options={races.map((r) => ({ value: r.race_id, label: <>{r.name}</>, sub: <span className="mono">{r.season} R{r.round}</span> }))} />
        <Select label="My driver" value={driverId} onChange={setDriverId}
                options={(entries?.entries ?? []).map((e) => ({
                  value: e.driver_id,
                  label: <><span style={{ width: 4, height: 16, borderRadius: 2, background: teamColor(e.team), display: "inline-block" }} />
                    <b className="mono">{e.code}</b> {e.name}</>,
                  sub: <span className="mono">{e.grid === 0 ? "PL" : `P${e.grid}`}</span>,
                }))} />
        <div className="spread">
          {entries && <FavoriteChips fav={fav} entries={entries.entries} onOpen={() => setSheet("favorites")} />}
          <button className="btn lg" disabled={!raceId || !driverId}
                  onClick={() => run(api.setup(raceId!, driverId, session)).then(() => setEditSetup(false))}>
            Load {SESSION_LABEL[session].toLowerCase()}
          </button>
        </div>
      </div>
    </section>
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="wordmark"><span className="slash" />PIT WALL</div>
        {loaded && <span className="pill accent">{SESSION_LABEL[kind]}</span>}
        <div className="grow" />
        <div className="row" style={{ gap: "0.4rem" }}>
          <span className="desktop-only"><Switch on={showTokens} onChange={setShowTokens} label="Tokens" /></span>
          <button className="chip" onClick={() => setSheet("menu")}>{Icon.user(13)} {account ? account.name : "Guest"}</button>
        </div>
      </header>

      <main className="main">
        {error && <div className="toast">{error}</div>}

        {loaded && !showSetup && (
          <section className="card" style={{ padding: "0.75rem 1rem" }}>
            <div className="spread">
              <div style={{ minWidth: 0 }}>
                <div className="eyebrow">{loadedRace ? `${loadedRace.season} · Round ${loadedRace.round}` : ""} · {SESSION_LABEL[kind]}</div>
                <div style={{ fontWeight: 750, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{loadedRace?.name ?? "Session"}</div>
              </div>
              <button className="btn sm ghost" onClick={() => setEditSetup(true)}>Change</button>
            </div>
            {entries && <div style={{ marginTop: "0.55rem" }}><FavoriteChips fav={fav} entries={entries.entries} onOpen={() => setSheet("favorites")} /></div>}
          </section>
        )}

        {showSetup && setup}

        {loaded && sim && (quali ? <QualiControl sim={sim} code={myCode} run={run} /> : <RaceControl sim={sim} code={myCode} run={run} sprint={sprint} />)}

        {loaded && sim?.scorecard && (quali
          ? <QualiScorecard sc={sim.scorecard} code={myCode} onRegrade={() => run(api.grade())} />
          : <RaceScorecard sc={sim.scorecard} code={myCode} sprint={sprint} onRegrade={() => run(api.grade())} />)}

        {loaded && pending && (quali
          ? <QualiDecision d={pending} run={run} />
          : <RaceDecision d={pending} lap={clock!.current_lap} sprint={sprint} onCall={(c, t) => run(api.call(c, t))} />)}

        {loaded && sim && (
          <div className="layout">
            <div>{quali ? <QualiTower sim={sim} entries={entries?.entries ?? []} /> : sim.race && <RaceTower race={sim.race} />}</div>
            <div className="side">
              {quali ? <QualiLog decisions={sim.decisions} code={myCode} /> : (
                <DecisionLog decisions={sim.decisions} code={myCode} rows={(d) => ({
                  when: `Lap ${d.lap + 1}`,
                  strategy: callLabel(d.strategy_call, d.evidence?.tire_to_fit),
                  you: callLabel(d.user_call, d.user_tire),
                  real: realLabel(d, sprint),
                  realTitle: d.actual?.detail,
                })} />
              )}
            </div>
          </div>
        )}
      </main>

      <AskFab onClick={() => setSheet("ask")} />
      {sheet === "ask" && <AskSheet loaded={loaded} answers={answers} setAnswers={setAnswers} onClose={() => setSheet(null)} />}
      {sheet === "favorites" && entries && (
        <FavoritesSheet entries={entries.entries} fav={fav} onChange={setFav} onClose={() => setSheet(null)} saved={!!account} />
      )}
      {sheet === "menu" && (
        <Sheet title={account ? account.name : "Guest"} onClose={() => setSheet(null)} center>
          <p className="muted small" style={{ margin: 0 }}>
            {account ? "Local account: your favorites are saved on this device." : "Demo mode: nothing is saved."}
          </p>
          <button className="btn ghost block" onClick={() => setSheet("favorites")}>{Icon.star(14)} Favorite team and driver</button>
          <div className="subcard spread">
            <span className="small">Token meter (developer view)</span>
            <Switch on={showTokens} onChange={setShowTokens} label="" />
          </div>
          <hr className="sep" />
          {account ? (
            <button className="btn outline block" onClick={() => { accounts.signOut(); setAccount(null); setFavState(NO_FAV); setSheet(null); setMode("landing"); }}>Sign out</button>
          ) : (
            <div className="row" style={{ gap: "0.5rem" }}>
              <button className="btn ghost" style={{ flex: 1 }} onClick={() => setSheet("signin")}>Sign in</button>
              <button className="btn outline" style={{ flex: 1 }} onClick={() => setSheet("create")}>Create account</button>
            </div>
          )}
          <button className="btn sm outline" style={{ justifySelf: "start" }} onClick={() => { setSheet(null); setMode("landing"); }}>Back to start</button>
        </Sheet>
      )}
      {(sheet === "signin" || sheet === "create") && (
        <AccountSheet mode={sheet} onMode={setSheet} onClose={() => setSheet(null)}
                      onDone={(a) => { if (!a.favorites.team && fav.team) accounts.saveFavorites(a.name, fav); enter({ ...a, favorites: a.favorites.team ? a.favorites : fav }); }} />
      )}
    </div>
  );
}
