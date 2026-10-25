import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Car, RaceState } from "./api";
import { teamColor } from "./Favorites";

/* A stylized circuit, not the real one: what matters is where the cars are relative to each other.
   The field runs along the path in its drawn direction; the start/finish line sits on the bottom straight. */
const TRACK = "M 60 196 L 300 196 C 352 196 372 160 348 134 L 300 98 C 280 82 286 54 316 46 C 350 37 362 18 330 14 " +
              "L 128 14 C 70 14 44 46 72 78 L 112 114 C 128 130 116 152 90 152 C 48 152 26 172 38 186 C 44 193 52 196 60 196 Z";
const VIEW = { w: 400, h: 214 };
const CENTER = { x: 200, y: 108 };
const SF_FROM_START = 150;      // the start/finish line: this far along the path from its first point
const BATTLE_S = 1.0;           // within a second of the car ahead: a battle
const GRID_STEP = 0.011;        // on the grid, cars sit this fraction of a lap apart

/** The label's real width in SVG units (its font size is in SVG units too), from the actual font. */
let measureCtx: CanvasRenderingContext2D | null = null;
function textWidth(text: string, size: number): number {
  measureCtx = measureCtx ?? document.createElement("canvas").getContext("2d");
  if (!measureCtx) return text.length * size * 0.72;
  measureCtx.font = `800 ${size}px "Inter Variable", Inter, system-ui, sans-serif`;
  return measureCtx.measureText(text).width + text.length * 0.02 * size;  // + the label's letter-spacing
}

type Placed = { car: Car; len: number; x: number; y: number; nx: number; ny: number; lapped: boolean };

/** Where each car is on the loop: the leader on the line, everyone else back around it by their
 *  real gap to the leader as a fraction of a lap (the field's typical lap time this lap). */
function place(race: RaceState, path: SVGPathElement): { placed: Placed[]; lapTime: number | null } {
  const L = path.getTotalLength();
  const cars = race.cars;
  const racing = cars.filter((c) => c.last_lap_s && !c.pitted_end_of_lap && !c.out_lap).map((c) => c.last_lap_s!).sort((a, b) => a - b);
  const lapTime = racing.length ? racing[Math.floor(racing.length / 2)] : null;
  const at = (len: number) => {
    const l = ((len % L) + L) % L;
    const p = path.getPointAtLength(l), q = path.getPointAtLength((l + 1) % L);
    let nx = -(q.y - p.y), ny = q.x - p.x;  // a normal, then pointed away from the middle (the outside)
    const n = Math.hypot(nx, ny) || 1; nx /= n; ny /= n;
    if ((p.x - CENTER.x) * nx + (p.y - CENTER.y) * ny < 0) { nx = -nx; ny = -ny; }
    return { len: l, x: p.x, y: p.y, nx, ny };
  };
  const placed = cars.map((car) => {
    let frac: number;
    let lapped = false;
    if (race.phase === "grid" || !lapTime) frac = (car.position - 1) * GRID_STEP;
    else {
      const g = car.position === 1 ? 0 : car.gap_leader_s ?? 0;
      lapped = g >= lapTime;
      frac = (g / lapTime) % 1;
    }
    return { car, lapped, ...at(SF_FROM_START - frac * L) };
  });
  return { placed, lapTime };
}

/** The stretch of track between two cars, for drawing a battle. */
function stretch(path: SVGPathElement, from: number, to: number): string {
  const L = path.getTotalLength();
  let span = ((to - from) % L + L) % L;
  if (span > L / 2) span = 0;
  const pts = [];
  for (let i = 0; i <= 12; i++) {
    const p = path.getPointAtLength((from + (span * i) / 12) % L);
    pts.push(`${p.x.toFixed(1)},${p.y.toFixed(1)}`);
  }
  return `M ${pts.join(" L ")}`;
}

export function TrackMap({ race, spotlight, onPick }: {
  race: RaceState; spotlight?: string | null; onPick: (code: string) => void;
}) {
  const ref = useRef<SVGPathElement>(null);
  const [ready, setReady] = useState(false);
  useLayoutEffect(() => { if (ref.current) setReady(true); }, []);

  const layout = useMemo(() => {
    if (!ready || !ref.current) return null;
    const path = ref.current;
    const { placed, lapTime } = place(race, path);
    const byPos = [...placed].sort((a, b) => a.car.position - b.car.position);
    // close battles: a car within a second of the one ahead (not while either is in the pits)
    const battles = race.phase === "racing" ? byPos.slice(1).flatMap((p, i) => {
      const ahead = byPos[i];
      const g = p.car.gap_ahead_s;
      if (g == null || g > BATTLE_S || p.car.pitted_end_of_lap || ahead.car.pitted_end_of_lap || p.car.out_lap) return [];
      return [{ ahead, behind: p, gap: g, d: stretch(path, p.len, ahead.len) }];
    }) : [];
    // labels: the followed car first, then the order; each takes the first spot (outside, then inside, stepping
    // further out) that overlaps no label, no dot and not the start/finish text
    const sfp = path.getPointAtLength(SF_FROM_START);
    type Box = { x0: number; y0: number; x1: number; y1: number };
    const taken: Box[] = [{ x0: sfp.x - 34, y0: sfp.y - 23, x1: sfp.x + 34, y1: sfp.y - 12 }];
    const dots: Box[] = placed.map((p) => ({ x0: p.x - 7, y0: p.y - 7, x1: p.x + 7, y1: p.y + 7 }));
    const hits = (b: Box, list: Box[]) => list.some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0);
    const labels = new Map<string, { x: number; y: number; anchor: "start" | "middle" | "end"; lead: boolean; dx: number; dy: number }>();
    const order = [...placed].sort((a, b) => Number(b.car.is_my_driver) - Number(a.car.is_my_driver) || a.car.position - b.car.position);
    for (const p of order) {
      const big = p.car.is_my_driver;
      const pitTag = p.car.pitted_end_of_lap ? " IN" : p.car.out_lap ? " OUT" : "";
      const w = textWidth(p.car.code, big ? 13.5 : 11.5) + (pitTag ? textWidth(pitTag, 9) : 0), h = big ? 14 : 12;
      type Cand = { x: number; y: number; anchor: "start" | "middle" | "end"; lead: boolean; box: Box; cost: number };
      const overlap = (b: Box, list: Box[]) => list.reduce((sum, o) =>
        sum + Math.max(0, Math.min(b.x1, o.x1) - Math.max(b.x0, o.x0)) * Math.max(0, Math.min(b.y1, o.y1) - Math.max(b.y0, o.y0)), 0);
      let best: Cand | null = null, fallback: Cand | null = null;
      // outward first, then inward, then sliding along the track, stepping further out
      search:
      for (const dist of [14, 26, 38, 50, 62]) {
        for (const slide of [0, 14, -14, 28, -28]) {
          for (const side of [1, -1]) {
            const nx = p.nx * side, ny = p.ny * side;
            const cx = p.x + nx * dist - p.ny * slide, cy = p.y + ny * dist + p.nx * slide;
            const anchor: "start" | "middle" | "end" = Math.abs(nx) < 0.35 ? "middle" : nx > 0 ? "start" : "end";
            const x0 = anchor === "start" ? cx : anchor === "end" ? cx - w : cx - w / 2;
            const box = { x0: x0 - 4, y0: cy - h / 2 - 1.5, x1: x0 + w + 4, y1: cy + h / 2 + 1.5 };  // with breathing room
            if (!(box.x0 > 0 && box.x1 < VIEW.w && box.y0 > 0 && box.y1 < VIEW.h)) continue;
            const cand = { x: cx, y: cy + h / 2 - 2, anchor, lead: dist > 14 || slide !== 0, box,
                           cost: overlap(box, taken) + overlap(box, dots) + dist + Math.abs(slide) };
            if (!hits(box, taken) && !hits(box, dots)) { best = cand; break search; }
            if (!fallback || cand.cost < fallback.cost) fallback = cand;
          }
        }
      }
      best = best ?? fallback ?? { x: p.x, y: p.y - 12, anchor: "middle", lead: false,
                                   box: { x0: p.x - w / 2, y0: p.y - 18, x1: p.x + w / 2, y1: p.y - 6 }, cost: 0 };
      taken.push(best.box);
      labels.set(p.car.code, { x: best.x, y: best.y, anchor: best.anchor, lead: best.lead,
                               dx: (best.box.x0 + best.box.x1) / 2, dy: (best.box.y0 + best.box.y1) / 2 });
    }
    return { placed, battles, labels, lapTime };
  }, [ready, race]);

  const sf = useMemo(() => {
    if (!ready || !ref.current) return null;
    const p = ref.current.getPointAtLength(SF_FROM_START);
    return p;
  }, [ready]);

  // a tap picks the nearest dot, so a car in a tight pack can still be picked
  const svgRef = useRef<SVGSVGElement>(null);
  const tapNearest = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current, ctm = svg?.getScreenCTM();
    if (!svg || !ctm || !layout) return;
    const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
    let best: Placed | null = null, bestD = 22;
    for (const p of layout.placed) {
      const d = Math.hypot(p.x - pt.x, p.y - pt.y);
      if (d < bestD) { best = p; bestD = d; }
    }
    if (best) onPick(best.car.code);
  };
  const me = race.cars.find((c) => c.is_my_driver);
  const battlesText = layout?.battles ?? [];
  return (
    <section className="card flush trackmap">
      <div className="spread" style={{ padding: "0.85rem 0.85rem 0.2rem" }}>
        <h2 style={{ margin: 0, fontSize: "0.95rem" }}>On track</h2>
        <span className="eyebrow">{race.phase === "grid" ? "On the grid" : `After lap ${race.lap}`}</span>
      </div>
      <svg viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} className="tm-svg" role="img" ref={svgRef} onClick={tapNearest}
           aria-label={`Track map after lap ${race.lap}: ${race.cars.map((c) => `P${c.position} ${c.code}`).join(", ")}`}>
        <path d={TRACK} className="tm-kerb" />
        <path d={TRACK} className="tm-road" ref={ref} />
        {sf && (
          <g transform={`translate(${sf.x} ${sf.y})`} className="tm-sf">
            <rect x={-3} y={-11} width={6} height={22} />
            <text x={0} y={-15} textAnchor="middle">START / FINISH</text>
          </g>
        )}
        {layout?.battles.map((b, i) => <path key={i} d={b.d} className="tm-battle" />)}
        {layout && [...layout.placed].sort((a, b) => Number(a.car.is_my_driver || a.car.code === spotlight) - Number(b.car.is_my_driver || b.car.code === spotlight))
          .map((p) => {
            const c = p.car;
            const mine = c.is_my_driver, spot = c.code === spotlight && !mine;
            const pit = c.pitted_end_of_lap || c.out_lap;
            const l = layout.labels.get(c.code)!;
            const color = teamColor(c.team);
            return (
              <g key={c.code} className={`tm-car ${mine ? "mine" : ""} ${spot ? "spot" : ""} ${pit ? "pit" : ""}`}
                 role="button" tabIndex={0}
                 onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPick(c.code)}
                 aria-label={`P${c.position} ${c.code}${pit ? ", in the pits" : ""}`}>
                {l.lead && <line x1={p.x} y1={p.y} x2={l.dx} y2={l.dy} className="tm-lead" />}
                <circle cx={p.x} cy={p.y} r={16} className="tm-hit" />
                {mine && <circle cx={p.x} cy={p.y} r={11.5} className="tm-ring" />}
                {spot && <circle cx={p.x} cy={p.y} r={10.5} className="tm-spot" style={{ stroke: color }} />}
                <circle cx={p.x} cy={p.y} r={mine ? 8 : 6} fill={pit ? "#0b0d10" : color} stroke={color} strokeWidth={pit ? 2.5 : 1.2}
                        className="tm-dot" />
                <text x={l.x} y={l.y} textAnchor={l.anchor} className="tm-label"
                      onClick={(e) => { e.stopPropagation(); onPick(c.code); }}>
                  {c.code}{pit && <tspan className="tm-pit"> {c.pitted_end_of_lap ? "IN" : "OUT"}</tspan>}
                </text>
              </g>
            );
          })}
      </svg>
      <div className="tm-foot">
        {battlesText.length > 0 && (
          <div className="row" style={{ gap: "0.35rem" }}>
            <span className="eyebrow">Close battles</span>
            {battlesText.map((b, i) => {
              const involved = b.ahead.car.is_my_driver || b.behind.car.is_my_driver;
              return (
                <button key={i} className={`chip ${involved ? "sel" : ""}`} onClick={() => onPick(b.behind.car.code)}>
                  {b.ahead.car.code}–{b.behind.car.code} <span className="mono dim">{b.gap.toFixed(1)}s</span>
                </button>
              );
            })}
          </div>
        )}
        <p className="tiny dim" style={{ margin: "0.4rem 0 0" }}>
          {race.phase === "grid" ? "Cars in grid order behind the start line." :
            <>Leader on the line; everyone else is placed back around the lap by their real gap to the leader
              {layout?.lapTime ? ` (a lap is about ${layout.lapTime.toFixed(1)}s this lap)` : ""}. A ring marks {me?.code ?? "your driver"}; tap a dot to spotlight a car.
              {layout?.placed.some((p) => p.lapped) ? " Lapped cars are a lap or more behind." : ""}</>}
        </p>
      </div>
    </section>
  );
}
