import type { Entry } from "./api";
import { Icon, Sheet } from "./ui";

// One accent color per team, from its livery. Default: a bright F1 red.
export const TEAM_ACCENT: Record<string, { color: string; text: string }> = {
  "McLaren": { color: "#FF8000", text: "#000" },          // papaya
  "Ferrari": { color: "#E8002D", text: "#fff" },          // Ferrari red
  "Mercedes": { color: "#00D2BE", text: "#00201d" },      // teal
  "Red Bull": { color: "#3B5BDB", text: "#fff" },         // navy, lifted to read on black
  "RB F1 Team": { color: "#6C98FF", text: "#001033" },    // Racing Bulls blue
  "Williams": { color: "#1E7BFF", text: "#fff" },         // Williams blue
  "Aston Martin": { color: "#229971", text: "#fff" },     // racing green
  "Alpine F1 Team": { color: "#FF5FAE", text: "#2a0016" },// Alpine pink
  "Haas F1 Team": { color: "#B6BABD", text: "#111" },     // Haas grey
  "Sauber": { color: "#52E252", text: "#032103" },        // Kick Sauber green
};
export const DEFAULT_ACCENT = { color: "#E10600", text: "#fff" };
export const teamColor = (team?: string | null) => (team && TEAM_ACCENT[team]?.color) || "#5f6570";

export type Fav = { team: string; driver: string };

/** a + (b - a) * t, on hex colors: the near-black team shade for the browser's own chrome */
function mix(a: string, b: string, t: number): string {
  const n = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [n(a), n(b)];
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

/** The whole app takes on the team: the accent, and --team, which tints every surface (index.css).
 *  No team: F1 red accent on a neutral dark base. */
export function applyAccent(team: string) {
  const a = TEAM_ACCENT[team];
  const root = document.documentElement.style;
  root.setProperty("--accent", (a ?? DEFAULT_ACCENT).color);
  root.setProperty("--accent-text", (a ?? DEFAULT_ACCENT).text);
  if (a) root.setProperty("--team", a.color); else root.removeProperty("--team");
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", a ? mix("#050506", a.color, 0.55) : "#07080a");
}

/** Favorite team and driver as chips in the team color. */
export function FavoriteChips({ fav, entries, onOpen }: { fav: Fav; entries: Entry[]; onOpen: () => void }) {
  const d = entries.find((e) => e.driver_id === fav.driver);
  if (!fav.team) {
    return <button className="chip" onClick={onOpen}>{Icon.star(13)} Pick a favorite team</button>;
  }
  return (
    <div className="row">
      <button className="chip team" style={{ ["--chip" as string]: teamColor(fav.team) }} onClick={onOpen}>
        <span className="dot" />{fav.team}
      </button>
      {d && (
        <button className="chip team" style={{ ["--chip" as string]: teamColor(fav.team) }} onClick={onOpen}>
          {Icon.star(12)}{d.code}
        </button>
      )}
    </div>
  );
}

export function FavoritesSheet({ entries, fav, onChange, onClose, saved }: {
  entries: Entry[]; fav: Fav; onChange: (f: Fav) => void; onClose: () => void; saved: boolean;
}) {
  const teams = [...new Set(entries.map((e) => e.team))].sort();
  const drivers = entries.filter((e) => e.team === fav.team);
  return (
    <Sheet title="Favorites" onClose={onClose} center
           footer={<button className="btn block" onClick={onClose}>Done</button>}>
      <p className="muted small" style={{ margin: 0 }}>
        The app takes its accent color from your team, and your favorite driver becomes your driver whenever
        they're in the session. {saved ? "Saved to your account on this device." : "Guest mode: not saved."}
      </p>
      <div className="eyebrow">Team</div>
      <div className="teamgrid">
        <button className={`chip ${!fav.team ? "sel" : ""}`} onClick={() => onChange({ team: "", driver: "" })}>None</button>
        {teams.map((t) => (
          <button key={t} className={`chip team ${fav.team === t ? "sel" : ""}`} style={{ ["--chip" as string]: teamColor(t) }}
                  onClick={() => onChange({ team: t, driver: entries.find((x) => x.team === t)?.driver_id ?? "" })}>
            <span className="dot" />{t}
          </button>
        ))}
      </div>
      {fav.team && (
        <>
          <div className="eyebrow">Driver</div>
          <div className="row">
            {drivers.map((d) => (
              <button key={d.driver_id} className={`chip team ${fav.driver === d.driver_id ? "sel" : ""}`}
                      style={{ ["--chip" as string]: teamColor(fav.team) }}
                      onClick={() => onChange({ ...fav, driver: d.driver_id })}>
                <b>{d.code}</b> <span className="muted">{d.name}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </Sheet>
  );
}
