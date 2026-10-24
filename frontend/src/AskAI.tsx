import { useEffect, useRef, useState } from "react";
import { api, type AskAnswer } from "./api";
import { TokenMeter } from "./TokenMeter";
import { Icon, More, Sheet } from "./ui";

export type AskEntry = AskAnswer | { question: string; error: string };

const KIND_LABEL: Record<AskAnswer["kind"], string> = {
  live_race: "This session, so far",
  season: "Season, up to this weekend",
  rules: "Rules file",
  unknown: "No grounding",
};
const SUGGESTIONS = ["What tires is Verstappen on?", "Who's leading the championship?", "What's an undercut?"];

/** The floating Ask button, bottom-right, from anywhere in the app. */
export function AskFab({ onClick }: { onClick: () => void }) {
  return (
    <button className="fab" onClick={onClick} aria-label="Ask AI">
      <span className="lbl">Ask AI</span>{Icon.chat(26)}
    </button>
  );
}

export function AskSheet({ loaded, answers, setAnswers, onClose }: {
  loaded: boolean; answers: AskEntry[]; setAnswers: (f: (a: AskEntry[]) => AskEntry[]) => void; onClose: () => void;
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

  return (
    <Sheet title={<span className="row">{Icon.chat(18)} Ask AI</span>} onClose={onClose}
      footer={
        <>
          <input className="input" value={input} disabled={!loaded || busy}
                 placeholder={loaded ? "Ask about this session, the season, or the rules…" : "Load a session first"}
                 onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <button className="btn" style={{ minWidth: 44, padding: 0, width: 44 }} disabled={!loaded || busy || !input.trim()}
                  onClick={() => send()} aria-label="Send">{Icon.send(18)}</button>
        </>
      }>
      {answers.length === 0 && (
        <div className="stack">
          <p className="muted small" style={{ margin: 0 }}>
            The Historian answers from the live session (only what's happened so far), the season up to this weekend,
            and a curated rules file. If it can't ground an answer, it says so.
          </p>
          <div className="suggest">
            {SUGGESTIONS.map((s) => <button key={s} className="chip" disabled={!loaded || busy} onClick={() => send(s)}>{s}</button>)}
          </div>
        </div>
      )}
      {answers.map((a, i) => <AskItem key={i} a={a} />)}
      {busy && <div className="msg-a"><div className="thinking" style={{ padding: 0 }}><span className="pulse" /> Checking the data…</div></div>}
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
