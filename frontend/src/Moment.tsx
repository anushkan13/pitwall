import { useState, type ReactNode } from "react";
import type { Decision, Jargon, RaceControl, Scorecard } from "./api";
import { costCell, TokenMeter } from "./TokenMeter";
import { Icon, More, TireDot } from "./ui";

/** How each session's moments are labelled: shared by race, sprint and qualifying. */
export type MomentText = {
  /** "end of lap 12" / "Q1, 6:20 in" */
  when: (d: Decision) => string;
  /** the option the data leaned towards, in plain words: "Stay out", "Box for mediums" */
  lean: (d: Decision) => string;
  /** what the real team did, once it has happened; null while it hasn't yet */
  did: (d: Decision) => string | null;
  /** when it will be known: "once lap 13 has been played" */
  revealAt: (d: Decision) => string;
  /** an Ask AI question about what the team did */
  askWhy: (d: Decision) => string;
};

const TIRE_WORD: Record<string, string> = { SOFT: "softs", MEDIUM: "mediums", HARD: "hards", INTERMEDIATE: "intermediates", WET: "full wets" };
export const tireWord = (t?: string | null) => (t ? TIRE_WORD[t] ?? t.toLowerCase() : "");

/* ---------- race control: why the cars are slowing down ---------- */

const RC_PLAIN: [RegExp, string, string][] = [
  [/red flag/, "bad", "Red flag: the session is stopped and the cars head back to the pit lane."],
  [/^safety car coming in/, "warn", "The safety car comes in this lap: racing restarts soon."],
  [/^safety car/, "warn", "Safety car: the field follows it at reduced speed and nobody can overtake."],
  [/virtual safety car ending/, "warn", "The virtual safety car is ending: racing resumes shortly."],
  [/virtual safety car/, "warn", "Virtual safety car: every driver must slow to a set pace, and nobody can overtake."],
  [/yellow/, "warn", "Yellow flags: there's danger in part of the track, so drivers slow down there and can't overtake."],
];

export function RaceControlStrip({ rc, onAsk }: { rc?: RaceControl | null; onAsk: (q: string) => void }) {
  if (!rc) return null;
  const st = rc.track_status;
  const hit = RC_PLAIN.find(([re]) => re.test(st.summary));
  const tone = hit?.[1] ?? "ok";
  const msgs = [...rc.recent].reverse().slice(0, 6);
  return (
    <div className={`rc-strip ${tone}`}>
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">Race control</div>
          <div className="rc-now">{hit ? hit[2] : `Green: no flags out${st.drs ? `, DRS ${st.drs}` : ""}.`}</div>
        </div>
        {hit && <button className="btn sm ghost" onClick={() => onAsk("Why did the cars slow down?")}>{Icon.chat(13)} Why?</button>}
      </div>
      {msgs.length > 0 && (
        <More summary={<>Latest messages ({msgs.length})</>}>
          <ul className="rc-list">
            {msgs.map((m, i) => (
              <li key={i}><span className="mono dim">{m.lap != null ? `L${m.lap}` : m.at ?? ""}</span> {m.message}</li>
            ))}
          </ul>
        </More>
      )}
    </div>
  );
}

/* ---------- jargon, explained ---------- */

export function JargonChips({ terms }: { terms?: Jargon[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!terms?.length) return null;
  const cur = terms.find((t) => t.id === open);
  return (
    <div className="jargon">
      <div className="row" style={{ gap: "0.35rem" }}>
        <span className="eyebrow" style={{ marginRight: "0.2rem" }}>New words</span>
        {terms.map((t) => (
          <button key={t.id} className={`chip ${open === t.id ? "sel" : ""}`} onClick={() => setOpen(open === t.id ? null : t.id)}>
            {t.title}
          </button>
        ))}
      </div>
      {cur && <p className="small jargon-def"><b>{cur.title}.</b> {cur.text}</p>}
    </div>
  );
}

/* ---------- the explanation card ---------- */

/** "How we know": every fact the explanation cites, filled in from the data, and the grounding trail. */
export function HowWeKnow({ d, extra }: { d: Decision; extra?: ReactNode }) {
  const ev = d.evidence;
  return (
    <More summary={<>How we know</>}>
      <div className="stack" style={{ gap: "0.5rem", marginTop: "0.5rem" }}>
        {extra && <p className="small muted" style={{ margin: 0 }}>{extra}</p>}
        {ev?.rival_watch && (
          <div>
            <div className="eyebrow">The cars around</div>
            <p className="small" style={{ margin: "0.25rem 0" }}>{ev.rival_watch.summary}</p>
            <ul className="small">
              {ev.rival_watch.rivals.map((r) => <li key={r.code}><b>{r.code}</b> ({r.relation}): {r.note}</li>)}
            </ul>
          </div>
        )}
        {ev?.cited && ev.cited.length > 0 && (
          <div>
            <div className="eyebrow">Facts cited, each filled in from the real data</div>
            <ul className="small">{ev.cited.map((f) => <li key={f.key}><code className="k">{f.kind}</code> {f.text}</li>)}</ul>
          </div>
        )}
        {ev?.grounding_rejections && ev.grounding_rejections.length > 0 && (
          <div>
            <div className="eyebrow">Drafts the fact-checker sent back ({ev.grounding_rejections.length})</div>
            <ul className="small">{ev.grounding_rejections.flatMap((r, i) => r.problems.map((p, j) => <li key={`${i}-${j}`}>{p}</li>))}</ul>
          </div>
        )}
        {ev?.tool_calls && <p className="tiny dim" style={{ margin: 0 }}>Data read: {ev.tool_calls.map((t) => t.tool).join(", ")}</p>}
      </div>
    </More>
  );
}

export function ExplanationCard({ d, text, code, onContinue, onAsk, extra }: {
  d: Decision; text: MomentText; code: string; onContinue: () => void; onAsk: (q: string) => void; extra?: ReactNode;
}) {
  const ev = d.evidence;
  return (
    <section className="card accent moment">
      <div className="eyebrow" style={{ color: "var(--accent)" }}>What's happening · {text.when(d)}</div>
      <div className="trigger">{d.trigger}</div>
      {d.status === "thinking" && <div className="thinking"><span className="pulse" /> Working out what this means for {code}…</div>}
      {d.status === "error" && <div className="toast">Couldn't explain this moment: {d.error}</div>}
      {d.status === "ready" && (
        <div className="stack" style={{ gap: "0.9rem" }}>
          {d.engineer_message && (
            <div>
              <div className="eyebrow">What just happened</div>
              <p className="explain">{d.engineer_message}</p>
            </div>
          )}
          <div className="weigh">
            <div className="spread">
              <div className="eyebrow">What the pit wall is weighing</div>
              <span className="pill accent">Data leans: {text.lean(d)}</span>
            </div>
            <p className="small" style={{ margin: "0.4rem 0 0" }}>{d.strategy_reason}</p>
            {ev?.rival_factor && <p className="small muted" style={{ margin: "0.4rem 0 0" }}>{ev.rival_factor}</p>}
          </div>
          <JargonChips terms={ev?.jargon} />
          <div className="next">
            <div className="eyebrow">What {code} does next</div>
            <p className="small muted" style={{ margin: "0.25rem 0 0" }}>
              The real team has already made this call. You'll see what they did, and why, {text.revealAt(d)}.
            </p>
          </div>
          <HowWeKnow d={d} extra={extra} />
          <TokenMeter usage={ev?.usage} />
        </div>
      )}
      {d.status !== "thinking" && (
        <div className="row" style={{ marginTop: "1rem", gap: "0.5rem" }}>
          <button className="btn lg" style={{ flex: 1 }} onClick={onContinue}>{Icon.play(15)} Continue watching</button>
          {d.status === "ready" && (
            <button className="btn lg ghost" onClick={() => onAsk("What's the pit wall thinking right now?")}>{Icon.chat(16)} Ask</button>
          )}
        </div>
      )}
    </section>
  );
}

/* ---------- the moments so far ---------- */

export function MomentsLog({ decisions, code, text, onAsk }: {
  decisions: Decision[]; code: string; text: MomentText; onAsk: (q: string) => void;
}) {
  const past = decisions.filter((d) => d.status !== "thinking" && d.user_call != null);
  return (
    <section className="card flush">
      <div className="spread" style={{ padding: "0.85rem 0.85rem 0.6rem" }}>
        <h2 style={{ margin: 0, fontSize: "0.95rem" }}>Key moments</h2>
        <span className="eyebrow">{past.length} so far</span>
      </div>
      {past.length === 0 && <div className="empty">Key moments show up here as the session unfolds: a rival pits, tires get old, the weather turns.</div>}
      <div className="log">
        {[...past].reverse().map((d) => {
          const did = text.did(d);
          const same = did != null && d.strategy_call != null && sameCall(d);
          return (
            <div key={d.id} className="log-item">
              <div className="spread"><span className="when">{text.when(d)}</span><span className="token-cell">{costCell(d.evidence?.usage)}</span></div>
              <div className="small" style={{ fontWeight: 650 }}>{d.trigger}</div>
              <div className="calls two">
                <div className="callbox"><div className="who">Data leaned</div><div className="what">{d.status === "error" ? "–" : text.lean(d)}</div></div>
                <div className={`callbox ${did ? "real" : ""}`}><div className="who">What {code} did</div><div className="what">{did ?? "…"}</div></div>
              </div>
              {did ? (
                <p className="small muted" style={{ margin: 0 }}>
                  {same ? <>That's the way the data leaned: {lowerFirst(d.strategy_reason)}</>
                        : <>The team went the other way from the data's lean. There's usually something the timing screens don't show: </>}
                  {" "}<button className="link" onClick={() => onAsk(text.askWhy(d))}>Ask why</button>
                </p>
              ) : (
                <p className="tiny dim" style={{ margin: 0 }}>You'll see what {code} did {text.revealAt(d)}.</p>
              )}
              {d.engineer_message && (
                <More summary={<>What we explained</>}>
                  <p className="small" style={{ margin: "0.4rem 0 0" }}>{d.engineer_message}</p>
                  <JargonChips terms={d.evidence?.jargon} />
                </More>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

const lowerFirst = (s?: string | null) => (!s ? "" : s.charAt(0).toLowerCase() + s.slice(1));

/** The real team's choice matched the option the data leaned towards (box/stay, go/wait, continue/abort). */
function sameCall(d: Decision): boolean {
  const a = d.actual?.action ?? (d.actual_pitted == null ? null : d.actual_pitted ? "BOX" : "STAY_OUT");
  const lean = d.strategy_call;
  if (!a || !lean) return false;
  if (a === "STAY_OUT") return lean !== "BOX";  // sprint: defend / push / manage are all stay-outs
  return a === lean;
}

/* ---------- the end-of-session recap ---------- */

export function Recap({ sc, title, momentLabel, finish, onRetry }: {
  sc: Scorecard; title: string; momentLabel: (n: number) => string; finish?: ReactNode; onRetry: () => void;
}) {
  const v = sc.verdict;
  return (
    <section className="card accent recap">
      <div className="eyebrow" style={{ color: "var(--accent)" }}>{Icon.flag(12)} {title}</div>
      {sc.status === "grading" && <div className="thinking"><span className="pulse" /> Putting the story of the session together…</div>}
      {sc.status === "error" && <div className="toast">The recap failed: {sc.error} <button className="btn sm" onClick={onRetry}>Try again</button></div>}
      {v?.headline && <div className="headline">{v.headline}</div>}
      {finish && <p className="small muted" style={{ margin: "0 0 0.6rem" }}>{finish}</p>}
      {v?.moments && v.moments.length > 0 && (
        <ol className="recap-moments">
          {v.moments.map((m, i) => (
            <li key={i}>
              <div className="eyebrow">{momentLabel(m.lap)}</div>
              <div className="recap-title">{m.title}</div>
              <p className="small" style={{ margin: "0.25rem 0" }}><b>What happened.</b> {m.what_happened}</p>
              <p className="small muted" style={{ margin: 0 }}><b>Why.</b> {m.why}</p>
            </li>
          ))}
        </ol>
      )}
      {v?.takeaway && (
        <div className="biggest" style={{ marginTop: "0.8rem" }}>
          <div className="eyebrow">Look for this next time</div>
          <div style={{ marginTop: "0.3rem" }}>{v.takeaway}</div>
        </div>
      )}
      <TokenMeter usage={v?.usage} />
    </section>
  );
}

export { TireDot };
