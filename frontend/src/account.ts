import type { Fav } from "./Favorites";

// A light, local account: a display name and favorites, stored in this
// browser's localStorage. No password and nothing sent to the server; it only
// exists so favorites survive a reload. Guests use the app without one.

export type Account = { name: string; favorites: Fav; created: string };

const ACCOUNTS = "pitwall.accounts";
const CURRENT = "pitwall.currentAccount";

function read(): Record<string, Account> {
  try { return JSON.parse(localStorage.getItem(ACCOUNTS) || "{}"); } catch { return {}; }
}
function write(all: Record<string, Account>) {
  localStorage.setItem(ACCOUNTS, JSON.stringify(all));
}

export const accounts = {
  list: (): Account[] => Object.values(read()).sort((a, b) => a.name.localeCompare(b.name)),
  current: (): Account | null => {
    const name = localStorage.getItem(CURRENT);
    return name ? read()[name.toLowerCase()] ?? null : null;
  },
  create(name: string): Account {
    const key = name.trim().toLowerCase();
    const all = read();
    if (!key) throw new Error("Pick a name.");
    if (all[key]) throw new Error("That name is already taken on this device. Sign in instead.");
    all[key] = { name: name.trim(), favorites: { team: "", driver: "" }, created: new Date().toISOString() };
    write(all);
    localStorage.setItem(CURRENT, key);
    return all[key];
  },
  signIn(name: string): Account {
    const key = name.trim().toLowerCase();
    const a = read()[key];
    if (!a) throw new Error("No account with that name on this device.");
    localStorage.setItem(CURRENT, key);
    return a;
  },
  signOut() { localStorage.removeItem(CURRENT); },
  saveFavorites(name: string, favorites: Fav) {
    const all = read();
    const key = name.toLowerCase();
    if (all[key]) { all[key].favorites = favorites; write(all); }
  },
};
