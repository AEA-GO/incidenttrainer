// The game itself, through the gateway (contract 5): /s/<game_id>/api/v1/* goes to the
// agent on the game's server (contract 2). The game cookie (path /s/<game_id>/, HttpOnly)
// was set by Start shift; the browser sends it, our code never sees it. Polling state every
// 2 s is also the heartbeat: 5 minutes without it and the game ends as abandoned.

import { ApiError } from "./api";

export type Service = { name: string; status: "ok" | "degraded" | "down" | "unknown"; label: string; detail?: string };

export type PageState = "investigating" | "identified" | "monitoring" | "resolved";

export type FeedItem = {
  id: number;
  kind: "chat_message" | "chat_reply" | "action_result" | "status_update" | "hint" | "notice";
  t: number | null;
  message_id?: string;
  from?: "dana" | "sam" | "you";
  text?: string;
  replies?: { id: string; text: string }[];
  option_id?: string;
  n?: number;
  level?: "info" | "warning" | "error";
  status_update?: { affected: string; doing: string; next_update_at: string | null; page_state: PageState };
  result?: ActionResult;
};

export type ActionResult = {
  action_id: string;
  label: string;
  command: string;
  exit_code: number;
  output: string;
  truncated: boolean;
  duration_ms: number;
  t: number | null;
};

export type ActionButton = {
  id: string;
  verb: string;
  target: string;
  label: string;
  confirm?: string;
  input?: Record<string, { choices?: string[]; pattern?: string }>;
};

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
  hints: { used: number; total: number; next_available_in_s: number | null };
  status_page: { state: PageState; last_update_t: number | null; next_update_at: string | null };
  feed: { cursor: number; items: FeedItem[] };
};

// Dashboard paths are relative to the game path, e.g. "grafana/d/shop-overview?kiosk&theme=dark".
export type Scene = {
  terminal: boolean;
  dashboards: { uid: string; title: string; path: string }[];
  actions: ActionButton[];
  scene: { starts_at?: string; weekday?: string };
  characters: { id: string; name: string; role: string }[];
};

export type ResolveReply = {
  result: "passed" | "failed";
  watch_seconds?: number;
  penalty?: number;
  failing?: { message: string }[];
};

async function agent<T>(
  gameId: string,
  method: string,
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  let reply: Response;
  try {
    reply = await fetch(`/s/${encodeURIComponent(gameId)}/api/v1${path}`, {
      method,
      headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...extraHeaders },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network", "Lost the connection to your server. Retrying…");
  }
  const data = await reply.json().catch(() => null);
  if (reply.status === 204) return undefined as T;
  if (!reply.ok) {
    const error = data?.error ?? {};
    throw new ApiError(reply.status, error.code ?? "unknown", error.message ?? "The server didn't answer.", error.retry_after_s);
  }
  return data as T;
}

export const game = {
  state: (id: string, after = 0) => agent<AgentState>(id, "GET", `/state?feed_after=${after}`),
  press: (id: string, actionId: string, input: Record<string, string>) =>
    agent<ActionResult>(id, "POST", "/actions", { action_id: actionId, input }, {
      "Idempotency-Key": crypto.randomUUID().replaceAll("-", ""),
    }),
  reply: (id: string, messageId: string, optionId: string) =>
    agent<void>(id, "POST", "/chat/replies", { message_id: messageId, option_id: optionId }),
  status: (
    id: string,
    update: { affected: string; doing: string; next_update_at: string | null; page_state: PageState },
  ) => agent<{ t: number; fields_filled: number; next_update_t: number | null }>(id, "POST", "/status-updates", update),
  hint: (id: string) => agent<{ n: number; text: string; hints_left: number }>(id, "POST", "/hints"),
  ack: (id: string) => agent<{ t: number }>(id, "POST", "/ack"),
  resolve: (id: string) => agent<ResolveReply>(id, "POST", "/resolve"),
  giveUp: (id: string) => agent<unknown>(id, "POST", "/give-up", { confirm: true }), // contract 2 requires it
  scene: (id: string) => agent<Scene>(id, "GET", "/scene"),
  // Grafana's own health check, through the gateway (the game cookie covers /s/<id>/grafana/).
  grafanaReady: async (id: string): Promise<boolean> => {
    try {
      const reply = await fetch(`/s/${encodeURIComponent(id)}/grafana/api/health`, { credentials: "same-origin" });
      return reply.ok;
    } catch {
      return false;
    }
  },
};
