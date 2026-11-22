// The session API (docs/contracts/session-api.yaml). Every call sends the invite code in
// X-Invite-Code when we have one; errors come back in one shape.

import { savedCode } from "./invite";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfterS?: number,
  ) {
    super(message);
  }
}

export type LabCard = {
  id: string;
  order: number;
  title: string;
  hook: string;
  level: "intern" | "junior";
  minutes: number;
  skills: string[];
  tools: string[];
  terminal: boolean;
  handover_note?: string;
  done?: { game_id: string; score: number; grade: string } | null;
};

export type Invite = { games_total: number; games_left: number; games_left_today: number };

export type Course = {
  id: string;
  title: string;
  summary: string;
  invite?: Invite;
  labs: LabCard[];
};

export type GameState = "booting" | "ready" | "paged" | "investigating" | "watching" | "ended";

export type GameStatus = {
  game_id: string;
  lab_id: string;
  state: GameState;
  end_reason?: string;
  boot: { server_started: boolean; agent_ready: boolean; seconds_since_start?: number };
  scored?: boolean;
};

export type Started = { game_id: string; state: "booting"; game_path: string; expires_at: string };

export type LostLine = { rule: string; points: number; reason: string };

export type ScoreResult = {
  version: string;
  scored: boolean;
  why?: string;
  score?: number;
  grade?: string;
  grade_caps?: string[];
  categories?: Record<string, { max: number; got: number }>;
  penalties?: { hints: number; wrong_resolves: number };
  lost?: LostLine[];
};

export type TimelineEvent = { t: number; type: string; data: Record<string, unknown> };

export type Debrief = {
  summary?: string;
  what_happened?: string;
  root_cause?: string;
  expert?: {
    time_s?: number;
    timeline?: { t: number; text: string }[];
    commands?: { command: string; why: string }[];
  };
  in_real_life?: { text?: string; links?: { title: string; url: string }[] };
  lessons?: string[];
};

export type Results = {
  game_id: string;
  lab: LabCard;
  nickname: string;
  end_reason: string;
  score: ScoreResult;
  events: TimelineEvent[];
  debrief: Debrief;
  next_lab_id: string | null;
  feedback_given: boolean;
};

export type Feedback = { replay: number; stuck?: string; fake?: string };

async function call<T>(method: string, path: string, body?: unknown, code?: string | null): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const invite = code === undefined ? savedCode() : code;
  if (invite) headers["X-Invite-Code"] = invite;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let reply: Response;
  try {
    reply = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network", "Can't reach IncidentTrainer. Check your connection and try again.");
  }
  if (reply.status === 204) return undefined as T;
  const data = await reply.json().catch(() => null);
  if (!reply.ok) {
    const error = data?.error ?? {};
    throw new ApiError(
      reply.status,
      error.code ?? "unknown",
      error.message ?? "Something went wrong on our side.",
      error.retry_after_s,
    );
  }
  return data as T;
}

export const api = {
  invite: (code: string) => call<Invite>("GET", "/invite", undefined, code),
  course: () => call<Course>("GET", "/course"),
  lab: (labId: string) => call<LabCard>("GET", `/labs/${encodeURIComponent(labId)}`),
  start: (labId: string, nickname: string) =>
    call<Started>("POST", "/sessions", { lab_id: labId, nickname }),
  status: (gameId: string) => call<GameStatus>("GET", `/sessions/${encodeURIComponent(gameId)}`),
  finish: (gameId: string) =>
    call<ScoreResult>("POST", `/sessions/${encodeURIComponent(gameId)}/finish`),
  results: (gameId: string) => call<Results>("GET", `/results/${encodeURIComponent(gameId)}`),
  feedback: (gameId: string, answers: Feedback) =>
    call<void>("PUT", `/sessions/${encodeURIComponent(gameId)}/feedback`, answers),
};

export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
