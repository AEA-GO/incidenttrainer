// The game itself, through the gateway (contract 5): /s/<game_id>/api/v1/* goes to the
// agent on the game's server (contract 2). The game cookie (path /s/<game_id>/, HttpOnly)
// was set by Start shift; the browser sends it, our code never sees it. Polling state every
// 2 s is also the heartbeat: 5 minutes without it and the game ends as abandoned.

import { ApiError } from "./api";

export type Service = { name: string; status: "ok" | "degraded" | "down" | "unknown"; label: string; detail?: string };

export type AgentState = {
  game_id: string;
  state: string;
  end_reason?: string;
  severity: "SEV2" | "SEV1";
  reopened: boolean;
  clock: { t: number | null; time_left_s: number | null; scene_time: string | null };
  page: { fired: boolean; title?: string; acknowledged: boolean; acknowledged_t?: number | null };
  health: { value: number; failed_checkouts?: number };
  services: Service[];
  alerts: { name: string; state: "pending" | "firing"; summary?: string }[];
  changes: { t?: number | null; ago_s?: number; service?: string; text: string; by: string }[];
  resolve: { attempts: number; wrong: number; watch_ends_in_s?: number | null };
};

// Dashboard paths are relative to the game path, e.g. "grafana/d/shop-overview?kiosk&theme=dark".
export type Scene = { dashboards: { uid: string; title: string; path: string }[] };

export type ResolveReply = {
  result: "passed" | "failed";
  watch_seconds?: number;
  penalty?: number;
  failing?: { message: string }[];
};

async function agent<T>(gameId: string, method: string, path: string, body?: unknown): Promise<T> {
  let reply: Response;
  try {
    reply = await fetch(`/s/${encodeURIComponent(gameId)}/api/v1${path}`, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network", "Lost the connection to your server. Retrying…");
  }
  const data = await reply.json().catch(() => null);
  if (!reply.ok) {
    const error = data?.error ?? {};
    throw new ApiError(reply.status, error.code ?? "unknown", error.message ?? "The server didn't answer.", error.retry_after_s);
  }
  return data as T;
}

export const game = {
  state: (id: string) => agent<AgentState>(id, "GET", "/state"),
  ack: (id: string) => agent<{ t: number }>(id, "POST", "/ack"),
  resolve: (id: string) => agent<ResolveReply>(id, "POST", "/resolve"),
  giveUp: (id: string) => agent<unknown>(id, "POST", "/give-up"),
  scene: (id: string) => agent<Scene>(id, "GET", "/scene"),
};
