import type { Entry } from "./api";

// One accent color per team, from its livery. Guest mode: nothing is saved.
export const TEAM_ACCENT: Record<string, { color: string; text: string }> = {
  "McLaren": { color: "#FF8000", text: "#000" },          // papaya
  "Ferrari": { color: "#DC0000", text: "#fff" },          // Ferrari red
  "Mercedes": { color: "#00A19C", text: "#fff" },         // teal
  "Red Bull": { color: "#1E2B5C", text: "#fff" },         // navy
  "RB F1 Team": { color: "#4F6FDF", text: "#fff" },       // Racing Bulls blue
  "Williams": { color: "#005AFF", text: "#fff" },         // Williams blue
  "Aston Martin": { color: "#006F62", text: "#fff" },     // racing green
  "Alpine F1 Team": { color: "#0090FF", text: "#fff" },   // Alpine blue
  "Haas F1 Team": { color: "#5A5D60", text: "#fff" },     // Haas grey
  "Sauber": { color: "#00C853", text: "#000" },           // Kick Sauber green
};
export const DEFAULT_ACCENT = { color: "#333333", text: "#fff" };

export type Fav = { team: string; driver: string };

export function applyAccent(team: string) {
  const a = TEAM_ACCENT[team] ?? DEFAULT_ACCENT;
  document.documentElement.style.setProperty("--accent", a.color);
  document.documentElement.style.setProperty("--accent-text", a.text);
}

export function Favorites({ entries, fav, onChange }: {
  entries: Entry[]; fav: Fav; onChange: (f: Fav) => void;
}) {
  const teams = [...new Set(entries.map((e) => e.team))].sort();
  const drivers = entries.filter((e) => e.team === fav.team);
  return (
    <section className="panel favorites">
      <strong>Favorites</strong> <span className="evidence">(guest mode, not saved)</span>{" "}
      <label>
        Team{" "}
        <select value={fav.team} onChange={(e) => {
          const team = e.target.value;
          onChange({ team, driver: entries.find((x) => x.team === team)?.driver_id ?? "" });
        }}>
          <option value="">none</option>
          {teams.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </label>{" "}
      <label>
        Driver{" "}
        <select value={fav.driver} disabled={!fav.team} onChange={(e) => onChange({ ...fav, driver: e.target.value })}>
          {drivers.map((d) => <option key={d.driver_id} value={d.driver_id}>{d.code} · {d.name}</option>)}
        </select>
      </label>
      {fav.team && <span className="swatch" title={fav.team} />}
    </section>
  );
}
