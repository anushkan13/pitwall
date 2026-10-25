import { useEffect, useState } from "react";
import { accounts, type Account } from "./account";
import { api, type EntryList, type Session, type SimState } from "./api";
import { AskFab, AskSheet, type AskEntry } from "./AskAI";
import { applyAccent, FavoriteChips, FavoritesSheet, type Fav } from "./Favorites";
import { AccountSheet, Landing } from "./Landing";
import { DriverPanel, PreviewCard } from "./Focus";
import { BottomNav, LearnPage, RacesPage, StandingsPage, WeekendSheet, type Round, type Tab } from "./Pages";
import { MomentsLog, RaceControlStrip } from "./Moment";
import { QualiControl, QualiMoment, QualiRecap, QualiTower, qualiText } from "./Quali";
import { RaceControl, RaceMoment, RaceRecap, RaceTower, raceText } from "./Race";
import { TrackMap } from "./TrackMap";
import { Icon, Sheet, Switch } from "./ui";

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
  const [entries, setEntries] = useState<EntryList | null>(null);
  const [driverId, setDriverId] = useState("");
  const [sim, setSim] = useState<SimState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<AskEntry[]>([]);
  const [showTokens, setShowTokens] = useState(false);
  const [sheet, setSheet] = useState<null | "ask" | "favorites" | "menu" | "signin" | "create" | "weekend">(null);
  const [tab, setTab] = useState<Tab>("races");
  const [rounds, setRounds] = useState<Round[] | null>(null);
  const [weekend, setWeekend] = useState<Round | null>(null);  // the weekend sheet's round
  const [askSeed, setAskSeed] = useState<string | null>(null);
  const [spotlight, setSpotlight] = useState<string | null>(null);  // a car tapped in the order; null = the followed driver
  const askAbout = (q: string | null) => { setAskSeed(q); setSheet("ask"); };

  const setFav = (f: Fav) => {
    setFavState(f);
    if (account) accounts.saveFavorites(account.name, f);
  };
  const enter = (a: Account | null) => {
    setAccount(a);
    setFavState(a?.favorites ?? NO_FAV);
    setMode("app");
    setSheet(null);
    setTab("races");
  };

  // the theme is the followed driver's team while a session is loaded, else the favorite team
  const followedTeam = entries?.entries.find((e) => e.driver_id === sim?.clock.driver_id)?.team;
  const themeTeam = (sim?.clock.race_id != null && followedTeam) || fav.team;
  useEffect(() => { applyAccent(themeTeam); }, [themeTeam]);
  // the picker always shows who's really being followed (e.g. after a switch)
  useEffect(() => {
    if (sim?.clock.driver_id) setDriverId(sim.clock.driver_id);
    setSpotlight(null);
  }, [sim?.clock.driver_id]);
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
        setRaceId((x) => x ?? r.default_race_id);
        api.calendar().then((c) => setRounds(c.rounds)).catch(() => {});
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
  const myEntry = entries?.entries.find((e) => e.driver_id === clock?.driver_id);
  const myCode = myEntry?.code ?? clock?.driver_id ?? "";
  const myName = myEntry?.name.split(" ").slice(-1)[0] ?? myCode;
  const loadedRace = races.find((r) => r.race_id === clock?.race_id);
  const notStarted = loaded && !clock!.running && (quali ? clock!.session_time_utc === clock!.session_start_utc
                                                         : clock!.current_lap === 0);
  const loadedRound = rounds?.find((r) => r.race_id === clock?.race_id) ?? null;

  /** Open a weekend: pick a session (the one playing, if it's this weekend) and a driver. */
  const openWeekend = (r: Round) => {
    const playing = loaded && r.race_id === clock?.race_id;
    const pick = playing ? kind : (r.sessions.find((x) => x.kind === "race" && x.watchable)
                                    ?? r.sessions.find((x) => x.watchable) ?? r.sessions[0])?.kind ?? "race";
    setRaceId(r.race_id);
    setSession(pick as Session);
    setWeekend(r);
    setSheet("weekend");
  };
  const sameAsLoaded = loaded && weekend?.race_id === clock?.race_id && session === kind;
  const watch = () => run(api.setup(weekend!.race_id, driverId, session)).then(() => { setSheet(null); setTab("watch"); window.scrollTo(0, 0); });

  return (
    <div className="app">
      <header className={`topbar ${themeTeam ? "team" : ""}`}>
        <div className="wordmark"><span className="slash" />PIT WALL</div>
        {loaded && <span className="pill accent">{SESSION_LABEL[kind]}</span>}
        {loaded && myCode && <button className="chip team follow-chip" onClick={() => { if (loadedRound) openWeekend(loadedRound); }} title="Switch driver">
          <span className="dot" />Following <b>{myCode}</b>{followedTeam && <span className="hb-team"> · {followedTeam}</span>}</button>}
        <div className="grow" />
        <div className="row" style={{ gap: "0.4rem" }}>
          <span className="desktop-only"><Switch on={showTokens} onChange={setShowTokens} label="Tokens" /></span>
          <button className="chip" onClick={() => setSheet("menu")}>{Icon.user(13)} {account ? account.name : "Guest"}</button>
        </div>
      </header>

      <main className="main">
        {error && <div className="toast">{error}</div>}

        {tab === "races" && (
          <RacesPage rounds={rounds} onOpen={openWeekend} onResume={() => setTab("watch")}
                     watching={loaded && loadedRound ? { name: loadedRound.name,
                       label: `${SESSION_LABEL[kind]} · following ${myCode} · ${quali ? (sim?.quali?.segment ?? "before the start") : `lap ${clock!.current_lap}/${clock!.total_laps}`}` } : null} />
        )}
        {tab === "learn" && <LearnPage onAsk={askAbout} />}
        {tab === "standings" && <StandingsPage rounds={rounds} onOpen={openWeekend} />}

        {tab === "watch" && !loaded && (
          <section className="card"><p style={{ margin: 0 }}>Nothing playing yet.</p>
            <button className="btn" style={{ marginTop: "0.7rem" }} onClick={() => setTab("races")}>Pick a race weekend</button></section>
        )}

        {tab === "watch" && loaded && (
          <section className="card" style={{ padding: "0.75rem 1rem" }}>
            <div className="spread">
              <button className="icon-btn" onClick={() => setTab("races")} aria-label="Back to races" style={{ transform: "rotate(90deg)" }}>{Icon.chev(18)}</button>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="eyebrow">{loadedRace ? `${loadedRace.season} · Round ${loadedRace.round}` : ""} · {SESSION_LABEL[kind]}</div>
                <div style={{ fontWeight: 750, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{loadedRace?.name ?? "Session"}</div>
              </div>
              <button className="btn sm ghost" onClick={() => { if (loadedRound) openWeekend(loadedRound); }}>Change</button>
            </div>
            {entries && <div style={{ marginTop: "0.55rem" }}><FavoriteChips fav={fav} entries={entries.entries} onOpen={() => setSheet("favorites")} /></div>}
          </section>
        )}

        {tab === "watch" && loaded && sim && (quali
          ? <QualiControl sim={sim} code={myCode} run={run}><RaceControlStrip rc={sim.race_control} onAsk={askAbout} /></QualiControl>
          : <RaceControl sim={sim} code={myCode} run={run} sprint={sprint}><RaceControlStrip rc={sim.race_control} onAsk={askAbout} /></RaceControl>)}

        {tab === "watch" && loaded && sim?.scorecard && (quali
          ? <QualiRecap sc={sim.scorecard} code={myCode} onRetry={() => run(api.grade())} />
          : <RaceRecap sc={sim.scorecard} code={myCode} sprint={sprint} onRetry={() => run(api.grade())} />)}

        {tab === "watch" && loaded && pending && (quali
          ? <QualiMoment d={pending} code={myCode} run={run} onAsk={askAbout} />
          : <RaceMoment d={pending} code={myCode} sprint={sprint} run={run} onAsk={askAbout} />)}

        {tab === "watch" && loaded && sim && notStarted && !pending && <PreviewCard sim={sim} onAsk={askAbout} />}

        {tab === "watch" && loaded && sim && (
          <div className="layout">
            {!quali && sim.race && <div className="lay-map"><TrackMap race={sim.race} spotlight={spotlight} onPick={setSpotlight} /></div>}
            <div className="lay-tower">
              {quali ? <QualiTower sim={sim} entries={entries?.entries ?? []} onPick={setSpotlight} />
                     : sim.race && <RaceTower race={sim.race} onPick={setSpotlight} spotlight={spotlight} />}
            </div>
            <div className="side">
              <DriverPanel sim={sim} code={spotlight ?? myCode} followed={myCode} team={followedTeam}
                           onFocus={setSpotlight} onAsk={askAbout} />
              <MomentsLog decisions={sim.decisions} code={myCode} onAsk={askAbout}
                          text={quali ? qualiText(myCode) : raceText(sprint, myCode)} />
            </div>
          </div>
        )}
      </main>

      <BottomNav tab={tab} onTab={(t) => { setTab(t); window.scrollTo(0, 0); }} watching={loaded} />
      <AskFab onClick={() => askAbout(null)} />
      {sheet === "weekend" && weekend && (
        <WeekendSheet round={weekend} session={session} onSession={setSession} entries={entries?.entries ?? []}
                      driverId={driverId} sameAsLoaded={!!sameAsLoaded} onClose={() => { setSheet(null); if (sameAsLoaded) setTab("watch"); }}
                      onDriver={(id) => { setDriverId(id); if (sameAsLoaded && id !== clock?.driver_id) run(api.follow(id)); }}
                      onWatch={watch}
                      onRestart={() => { if (confirm("Start this session again from the beginning?")) watch(); }}
                      favorites={entries && <FavoriteChips fav={fav} entries={entries.entries} onOpen={() => setSheet("favorites")} />} />
      )}
      {sheet === "ask" && <AskSheet loaded={loaded} answers={answers} setAnswers={setAnswers} name={myName || "this driver"} kind={kind}
                                    seed={askSeed} onSeedUsed={() => setAskSeed(null)} onClose={() => setSheet(null)} />}
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
