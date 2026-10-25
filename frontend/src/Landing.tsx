import { useState } from "react";
import { accounts, type Account } from "./account";
import { Icon, Sheet } from "./ui";

export function Landing({ onGuest, onAccount }: { onGuest: () => void; onAccount: (a: Account) => void }) {
  const [sheet, setSheet] = useState<null | "signin" | "create">(null);
  return (
    <div className="landing">
      <div className="landing-inner">
        <div className="eyebrow" style={{ color: "var(--accent)" }}>Real 2025 F1 races · explained as they happen</div>
        <div className="hero-mark">PIT<br /><span>WALL</span></div>
        <div className="tagline">Make sense of every race, as it unfolds.</div>
        <div className="features">
          <div><b>Follow a driver through a real race.</b> At every key moment, a friendly explanation of what just happened and why it matters.</div>
          <div><b>See what the pit wall is weighing.</b> Box or stay out, go now or wait: the tradeoff in plain words, then what the team really did.</div>
          <div><b>Ask anything, any time.</b> "Why isn't Hamilton pitting?" "Why did the car slow down?" Answers come from the real data, with no spoilers.</div>
        </div>
        <div className="ctas">
          <button className="btn lg block" onClick={onGuest}>{Icon.play(16)} Start watching</button>
          <div className="row" style={{ gap: "0.6rem" }}>
            <button className="btn lg ghost" style={{ flex: 1 }} onClick={() => setSheet("signin")}>Sign in</button>
            <button className="btn lg outline" style={{ flex: 1 }} onClick={() => setSheet("create")}>Create account</button>
          </div>
          <div className="dim tiny" style={{ textAlign: "center" }}>
            Demo mode saves nothing. An account keeps your favorite team and driver on this device.
          </div>
        </div>
      </div>
      {sheet && <AccountSheet mode={sheet} onMode={setSheet} onClose={() => setSheet(null)} onDone={onAccount} />}
    </div>
  );
}

export function AccountSheet({ mode, onMode, onClose, onDone }: {
  mode: "signin" | "create"; onMode: (m: "signin" | "create") => void; onClose: () => void; onDone: (a: Account) => void;
}) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const existing = accounts.list();
  const submit = () => {
    try { onDone(mode === "create" ? accounts.create(name) : accounts.signIn(name)); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <Sheet title={mode === "create" ? "Create account" : "Sign in"} onClose={onClose} center
           footer={<button className="btn block lg" onClick={submit} disabled={!name.trim()}>
             {mode === "create" ? "Create account" : "Sign in"}</button>}>
      <label className="field">
        <span className="label">Display name</span>
        <input className="input" value={name} autoFocus placeholder="e.g. Anushka"
               onChange={(e) => { setName(e.target.value); setErr(null); }}
               onKeyDown={(e) => e.key === "Enter" && name.trim() && submit()} />
      </label>
      {mode === "signin" && existing.length > 0 && (
        <div className="row">
          {existing.map((a) => (
            <button key={a.name} className="chip" onClick={() => { setName(a.name); setErr(null); }}>{Icon.user(13)} {a.name}</button>
          ))}
        </div>
      )}
      {err && <div className="toast">{err}</div>}
      <p className="muted small" style={{ margin: 0 }}>
        Accounts are local to this browser: a name and your favorites, nothing else, and nothing leaves the device.
      </p>
      <button className="btn sm outline" style={{ justifySelf: "start" }}
              onClick={() => { onMode(mode === "create" ? "signin" : "create"); setErr(null); }}>
        {mode === "create" ? "I already have one: sign in" : "New here? Create an account"}
      </button>
    </Sheet>
  );
}
