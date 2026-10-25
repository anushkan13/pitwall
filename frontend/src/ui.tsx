import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/* ---------- icons (inline, stroke = currentColor) ---------- */

const svg = (path: ReactNode, size = 18) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>
);
export const Icon = {
  chev: (s?: number) => svg(<path d="m6 9 6 6 6-6" />, s),
  close: (s?: number) => svg(<path d="M18 6 6 18M6 6l12 12" />, s),
  book: (s?: number) => svg(<><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 21V5" /><path d="M9 7h6" /></>, s),
  trophy: (s?: number) => svg(<><path d="M8 4h8v5a4 4 0 0 1-8 0z" /><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4" /><path d="M12 13v4M8 20h8M9 17h6" /></>, s),
  play: (s?: number) => svg(<path d="M7 4v16l13-8z" fill="currentColor" />, s),
  pause: (s?: number) => svg(<><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" /><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" /></>, s),
  send: (s?: number) => svg(<path d="M5 12h14M13 6l6 6-6 6" />, s),
  spark: (s?: number) => svg(<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />, s),
  chat: (s?: number) => svg(<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" />, s),
  sun: (s?: number) => svg(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>, s),
  rain: (s?: number) => svg(<><path d="M17.5 16a4.5 4.5 0 0 0-.9-8.9A6 6 0 0 0 5 9a4 4 0 0 0 .5 7.9" /><path d="M8 19l-1 2M12 18l-1 3M16 19l-1 2" /></>, s),
  cloud: (s?: number) => svg(<path d="M17.5 19a4.5 4.5 0 0 0-.9-8.9A6 6 0 0 0 5 12a4 4 0 0 0 .5 7Z" />, s),
  thermo: (s?: number) => svg(<path d="M14 14.8V4a2 2 0 1 0-4 0v10.8a4 4 0 1 0 4 0Z" />, s),
  flag: (s?: number) => svg(<><path d="M4 21V4" /><path d="M4 4h12l-2 4 2 4H4" /></>, s),
  star: (s?: number) => svg(<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9Z" fill="currentColor" stroke="none" />, s),
  user: (s?: number) => svg(<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>, s),
  bolt: (s?: number) => svg(<path d="M13 2 4 14h7l-1 8 9-12h-7Z" fill="currentColor" stroke="none" />, s),
};

/* ---------- custom select (no native <select>) ---------- */

export type Option<T extends string | number> = { value: T; label: ReactNode; text?: string; sub?: ReactNode };

export function Select<T extends string | number>({ value, options, onChange, label, placeholder, disabled }: {
  value: T | null | undefined; options: Option<T>[]; onChange: (v: T) => void;
  label?: string; placeholder?: string; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const listId = useId();
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (open) {
      const i = Math.max(0, options.findIndex((o) => o.value === value));
      setActive(i);
      requestAnimationFrame(() => ref.current?.querySelector(`[data-i="${i}"]`)?.scrollIntoView({ block: "nearest" }));
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (i: number) => { const o = options[i]; if (o) onChange(o.value); setOpen(false); };
  const onKey = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); setOpen(true); return; }
    if (!open) return;
    if (e.key === "Escape") { setOpen(false); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(options.length - 1, a + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(active); }
  };
  useEffect(() => {
    if (open) ref.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const btn = (
    <div className={`select ${open ? "open" : ""}`} ref={ref}>
      <button type="button" className="select-btn" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
              aria-controls={listId} onClick={() => setOpen((o) => !o)} onKeyDown={onKey}>
        <span className="value">{current ? current.label : <span className="dim">{placeholder ?? "Choose…"}</span>}</span>
        <span className="chev">{Icon.chev(16)}</span>
      </button>
      {open && (
        <div className="select-menu" role="listbox" id={listId}>
          {options.map((o, i) => (
            <div key={String(o.value)} data-i={i} role="option" aria-selected={o.value === value}
                 className={`select-opt ${i === active ? "active" : ""} ${o.value === value ? "selected" : ""}`}
                 onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(i); }}>
              {o.label}{o.sub != null && <span className="sub">{o.sub}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
  return label ? <label className="field"><span className="label">{label}</span>{btn}</label> : btn;
}

/* ---------- segmented control ---------- */

export function Seg<T extends string | number>({ value, options, onChange, block }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; block?: boolean;
}) {
  return (
    <div className={`seg ${block ? "block" : ""}`} role="radiogroup">
      {options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value}
                className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

/* ---------- toggle switch (no native checkbox) ---------- */

export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <button type="button" role="switch" aria-checked={on} className="switch" onClick={() => onChange(!on)}>
      <span className="track"><span className="knob" /></span>{label}
    </button>
  );
}

/* ---------- tires ---------- */

export const TIRE_COLOR: Record<string, string> = {
  SOFT: "#ff3b3b", MEDIUM: "#ffd12e", HARD: "#f2f2ee", INTERMEDIATE: "#3ccb4a", WET: "#2f8cff",
};
const TIRE_LETTER: Record<string, string> = { SOFT: "S", MEDIUM: "M", HARD: "H", INTERMEDIATE: "I", WET: "W" };

export function TireDot({ compound, age, title }: { compound?: string | null; age?: number | null; title?: string }) {
  if (!compound) return <span className="tire dim">–</span>;
  const c = TIRE_COLOR[compound] ?? "#888";
  return (
    <span className="tire" title={title ?? `${compound}${age != null ? `, ${age} laps old` : ""}`}
          style={{ ["--tc" as string]: c }}>
      <span className="ring">{TIRE_LETTER[compound] ?? "?"}</span>{age != null && <span>{age}</span>}
    </span>
  );
}

/* ---------- weather ---------- */

export function WeatherBadge({ trackC, raining, recent, note }: {
  trackC?: number | null; raining?: boolean; recent?: boolean; note?: string;
}) {
  if (trackC == null) return null;
  const wet = raining || recent;
  return (
    <span className={`wx ${wet ? "wet" : ""}`} title={note}>
      {raining ? Icon.rain(16) : recent ? Icon.cloud(16) : Icon.sun(16)}
      <span>{raining ? "Rain" : recent ? "Damp" : "Dry"}</span>
      <span className="dim">·</span>{Icon.thermo(14)}<span className="num">{trackC}°</span>
      {note && <span className="muted small">{note}</span>}
    </span>
  );
}

/* ---------- sheet (bottom on phones, side/centre on laptops) ---------- */

export function Sheet({ title, onClose, children, footer, center }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; center?: boolean;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className={`sheet ${center ? "center" : ""}`} role="dialog" aria-modal="true">
        <div className="grab" />
        <div className="sheet-head"><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label="Close">{Icon.close(18)}</button></div>
        <div className="sheet-body">{children}</div>
        {footer && <div className="sheet-foot">{footer}</div>}
      </div>
    </>
  );
}

/* ---------- collapsible extra detail ---------- */

export function More({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return <details className="more"><summary>{summary}</summary>{children}</details>;
}
