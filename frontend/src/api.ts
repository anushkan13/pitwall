// In development the backend is on its own port; in a production build the
// backend serves this app, so the API is on the same origin.
export const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? "" : "http://localhost:8000");

export type Entry = {
  driver_id: string;
  code: string;
  name: string;
  team: string;
  car_number: number;
  grid: number;
};

export type EntryList = {
  race_id: number;
  name: string;
  date: string;
  circuit_name: string;
  scheduled_laps: number;
  entries: Entry[];
};

export type Car = {
  position: number;
  driver_id: string;
  code: string;
  team: string;
  gap_ahead_s?: number | null;
  gap_leader_s?: number | null;
  gap_behind_s?: number | null;
  last_lap_s?: number | null;
  compound?: string | null;
  tire_age?: number | null;
  stops?: number;
  pitted_end_of_lap?: boolean;
  out_lap?: boolean;
  is_my_driver: boolean;
};

export type RaceState = {
  lap: number;
  current_lap: number;
  total_laps: number;
  phase: "grid" | "racing";
  cars: Car[];
  retired?: { driver_id: string; code: string; last_lap: number }[];
  weather?: {
    air_temp_c: number;
    track_temp_c: number;
    raining: boolean;
    rain_in_last_5_min: boolean;
  } | null;
};

export type UsageRow = { agent: string; requests: number; input_tokens: number; cached_input_tokens: number;
                         output_tokens: number; cost_usd: number | null; completed?: boolean };
export type Usage = {
  model: string;
  runs: UsageRow[];
  total: { requests: number; input_tokens: number; cached_input_tokens: number; output_tokens: number;
           cost_usd: number | null };
  prices_per_mtok: { input: number; cached_input: number; output: number } | null;
  price_source: string;
};

export type Fact = { key: string; kind: string; driver: string | null; text: string };

export type AskAnswer = {
  question: string;
  lap: number;
  kind: "live_race" | "season" | "rules" | "unknown";
  answer: string;
  cited: Fact[];
  rules: { id: string; title: string }[];
  tool_calls: { agent: string; tool: string; refused?: string }[];
  rejections: { agent: string; problems: string[] }[];
  cleanups: { agent: string; note: string }[];
  usage?: Usage | null;
};

export type Decision = {
  id: number;
  lap: number;
  trigger: string;
  status: "thinking" | "ready" | "decided" | "error";
  strategy_call: Call | null;
  strategy_reason: string | null;
  engineer_message: string | null;
  evidence: {
    tire_to_fit?: string | null;
    rival_factor?: string;
    rival_watch?: {
      summary: string;
      rivals: { code: string; relation: "ahead" | "behind"; gap_s: number | null; note: string }[];
      undercut_threat_from: string | null;
      undercut_chance_on: string | null;
      rejoin_behind_if_box: string[];
    } | null;
    position: number;
    compound: string;
    tire_age: number;
    gap_ahead_s: number | null;
    gap_behind_s: number | null;
    tool_calls: { agent: string; tool: string; args: object; refused?: string }[];
    cited?: Fact[];
    grounding_rejections?: { agent: string; problems: string[] }[];
    grounding_cleanups?: { agent: string; note: string }[];
    jargon?: Jargon[];
    usage?: Usage | null;
  } | null;
  error: string | null;
  user_call: Call | null;
  user_tire?: string | null;
  actual_pitted: number | null;
  session_kind?: Session;
  actual?: {
    pending?: boolean;
    status_at_call?: string;
    when?: string;
    action?: Call | "OUT";
    tire?: string | null;
    detail?: string;
    mode?: string;
    inferred?: boolean;
  } | null;
};

export type Jargon = { id: string; title: string; text: string };

export type RCMessage = { message: string; category: string | null; flag: string | null; cars: string[];
                          lap?: number | null; at?: string };
export type RaceControl = {
  as_of: string;
  track_status: { summary: string; red_flag: boolean; safety_car: string | null; drs: string | null;
                  yellow_sectors: { sector: number; flag: string }[] };
  recent: RCMessage[];
  my_driver_messages: RCMessage[];
};

export type RecapMoment = { lap: number; title: string; what_happened: string; why: string };

export type Rate = { matched: number; of: number; pct: number | null };

export type Scorecard = {
  status: "grading" | "ready" | "error";
  error: string | null;
  stats: {
    decision_points: number;
    you_vs_real: Rate;
    strategy_vs_real: Rate;
    you_vs_strategy: Rate;
    rows: { lap: number; trigger: string; strategy: string; you: string; real: string }[];
    real_finish?: { pos: string; grid: number; status: string; points: number };
    real_stops?: { stop: number; lap: number; from_tire: string | null; to_tire: string | null; pit_lane_pass: number }[];
  } | null;
  verdict: {
    headline: string;
    moments?: RecapMoment[];
    takeaway?: string;
    biggest_call_lap?: number;
    biggest_call?: string;
    your_calls?: string;
    strategy_calls?: string;
    usage?: Usage | null;
  } | null;
};

export type Call = "BOX" | "STAY_OUT" | "GO_OUT" | "WAIT" | "CONTINUE" | "ABORT" | "DEFEND" | "PUSH" | "MANAGE";
export type Session = "race" | "qualifying" | "sprint";
export type Tire = "WET" | "INTERMEDIATE" | "SOFT" | "MEDIUM" | "HARD";

export type QualiCar = {
  position: number;
  code: string;
  best: string | null;
  best_compound: string | null;
  best_set_at: string | null;
  status: "garage" | "out lap" | "on a lap";
  compound: string | null;
  tire_age: number | null;
  laps_this_segment: number;
  is_my_driver: boolean;
};

export type QualiState = {
  refused?: boolean;
  reason?: string;
  session_elapsed: string;
  segment: string | null;
  phase: string;
  running: boolean;
  elapsed?: string;
  scheduled_length?: string;
  scheduled_time_left?: string | null;
  cars_through?: number | null;
  order: QualiCar[];
  on_track: { count: number; cars: string[]; by_compound: Record<string, number> };
  eliminated: { code: string; out_in: string; best: string | null }[];
  track_evolution: { direction: string; change_s?: number; window_minutes: number;
                     recent_best?: { code: string; time: string; compound: string } | null };
  weather: { raining: boolean; track_temp_c: number; air_temp_c: number; minutes_since_rain: number | null;
             rain_readings_last_10: number } | null;
  my_status: string;
};

export type SimState = {
  clock: {
    race_id: number | null;
    driver_id: string | null;
    current_lap: number;
    total_laps: number;
    running: number;
    seconds_per_lap: number;
    session_kind?: Session;
    session_time_utc?: string | null;
    session_start_utc?: string | null;
    quali_speed?: number;
  };
  race: RaceState | null;
  quali?: QualiState | null;
  mode?: Session;
  over?: boolean;
  decisions: Decision[];
  pending: Decision | null;
  scorecard: Scorecard | null;
  race_control?: RaceControl | null;
};

async function req<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  races: () => req<{ default_race_id: number; default_quali_race_id: number; default_sprint_race_id: number;
                      races: { race_id: number; name: string; season: number; round: number }[] }>("/api/races"),
  entries: (raceId: number, session = "race") => req<EntryList>(`/api/races/${raceId}/entries?session=${session}`),
  state: () => req<SimState>("/api/sim/state"),
  setup: (race_id: number, driver_id: string, session: Session = "race") =>
    req<SimState>("/api/sim/setup", { race_id, driver_id, session }),
  start: () => req<SimState>("/api/sim/start", {}),
  pause: () => req<SimState>("/api/sim/pause", {}),
  speed: (seconds_per_lap: number) => req<SimState>("/api/sim/speed", { seconds_per_lap }),
  qualiSpeed: (quali_speed: number) => req<SimState>("/api/sim/speed", { quali_speed }),
  ask: () => req<SimState>("/api/sim/ask", {}),
  call: (call: Call, tire?: Tire) => req<SimState>("/api/sim/call", { call, tire: tire ?? null }),
  continueWatching: () => req<SimState>("/api/sim/continue", {}),
  grade: () => req<SimState>("/api/sim/grade", {}),
  askAI: (question: string) => req<AskAnswer>("/api/ask", { question }),
  chat: (message: string) => req<{ message: string }>("/api/engineer/chat", { message }),
};
