import { useEffect, useRef, useState } from "react";
import type { Session } from "./api";
import { api, type AskAnswer } from "./api";
import { TokenMeter } from "./TokenMeter";
import { Icon, More, Sheet } from "./ui";

export type AskEntry = AskAnswer | { question: string; error: string };

const KIND_LABEL: Record<AskAnswer["kind"], string> = {
  live_race: "From this session so far",
  season: "From the season so far",
  rules: "From the rules file",
  unknown: "Can't answer that from the data",
};

/** Newcomer questions first: the "why" behind what's on screen. */
const suggestions = (name: string, kind: Session) => kind === "qualifying" ? [
  `Why is ${name} waiting in the garage?`, "How does qualifying work?", "Why are they on different tires?",
  "Why did the car slow down?", "What's the pit wall thinking right now?",
] : [
  `Why isn't ${name} pitting?`, "What's an undercut?", "Why are they on different tires?",
  "Why did the car slow down?", "What's the pit wall thinking right now?",
];

/** The floating Ask button, bottom-right, from anywhere in the app. */
export function AskFab({ onClick }: { onClick: () => void }) {
  return (
    <button className="fab" onClick={onClick} aria-label="Ask AI">
      {Icon.chat(22)}<span className="lbl">Ask AI</span>
    </button>
  );
}

export function AskSheet({ loaded, answers, setAnswers, onClose, name, kind, seed, onSeedUsed }: {
  loaded: boolean; answers: AskEntry[]; setAnswers: (f: (a: AskEntry[]) => AskEntry[]) => void; onClose: () => void;
  name: string; kind: Session; seed?: string | null; onSeedUsed?: () => void;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [answers.length, busy]);

  const send = async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    try {
      const r = await api.askAI(question);
      setAnswers((a) => [...a, r]);
    } catch (e) {
      setAnswers((a) => [...a, { question, error: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  };

  // opened from an "Ask why" button: ask it straight away
  const seeded = useRef(false);
  useEffect(() => {
    if (seed && loaded && !seeded.current) { seeded.current = true; send(seed); onSeedUsed?.(); }
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Sheet title={<span className="row">{Icon.chat(18)} Ask AI</span>} onClose={onClose}
      footer={
        <>
          <input className="input" value={input} disabled={!loaded || busy}
                 placeholder={loaded ? "Ask anything: why, what's that, who's…" : "Pick a session to watch first"}
                 onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <button className="btn" style={{ minWidth: 44, padding: 0, width: 44 }} disabled={!loaded || busy || !input.trim()}
                  onClick={() => send()} aria-label="Send">{Icon.send(18)}</button>
        </>
      }>
      {answers.length === 0 && (
        <div className="stack">
          <p style={{ margin: 0 }}>
            New to F1, or just curious? Ask about anything on screen: why a driver is or isn't pitting, what a word means, why the cars slowed down.
          </p>
          <p className="muted small" style={{ margin: 0 }}>
            Answers come from this session so far (no spoilers), the season up to this weekend, race control and a rules
            file. If the data can't back an answer up, it says so instead of guessing.
          </p>
          <div className="suggest">
            {suggestions(name, kind).map((s) => <button key={s} className="chip" disabled={!loaded || busy} onClick={() => send(s)}>{s}</button>)}
          </div>
        </div>
      )}
      {answers.map((a, i) => <AskItem key={i} a={a} />)}
      {answers.length > 0 && !busy && (
        <div className="suggest">
          {suggestions(name, kind).filter((s) => !answers.some((a) => a.question === s)).slice(0, 3).map((s) =>
            <button key={s} className="chip" disabled={!loaded} onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      {busy && <div className="msg-a"><div className="thinking" style={{ padding: 0 }}><span className="pulse" /> Looking into it…</div></div>}
      <div ref={endRef} />
    </Sheet>
  );
}

function AskItem({ a }: { a: AskEntry }) {
  if ("error" in a) {
    return (<><div className="msg-q">{a.question}</div><div className="toast">{a.error}</div></>);
  }
  return (
    <>
      <div className="msg-q">{a.question}</div>
      <div className={`msg-a ${a.kind === "unknown" ? "unknown" : ""}`}>
        <div className="row"><span className={`pill ${a.kind === "unknown" ? "neutral" : "accent"}`}>{KIND_LABEL[a.kind]}</span>
          <span className="tiny dim mono">after lap {a.lap}</span></div>
        <div className="text">{a.answer}</div>
        <TokenMeter usage={a.usage} />
        {(a.cited.length > 0 || a.rules.length > 0 || a.rejections.length > 0) && (
          <More summary={<>Sources{a.rejections.length > 0 ? ` · grounding check sent back ${a.rejections.length}` : ""}</>}>
            <ul>
              {a.cited.map((f) => <li key={f.key}><code className="k">{f.kind}</code> {f.text}</li>)}
              {a.rules.map((r) => <li key={r.id}><code className="k">rules</code> {r.title}</li>)}
              {a.rejections.flatMap((r, i) => r.problems.map((p, j) => <li key={`r${i}-${j}`}>sent back: {p}</li>))}
            </ul>
          </More>
        )}
      </div>
    </>
  );
}
