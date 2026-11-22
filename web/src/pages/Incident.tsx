import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Eyebrow } from "../components/Layout";
import { ApiError, clock } from "../lib/api";
import {
  type ActionButton,
  type ActionResult,
  type AgentState,
  type FeedItem,
  type PageState,
  type Scene,
  type Service,
  game,
} from "../lib/game";
import { useTicking } from "../lib/useTicking";

const POLL_MS = 2000;
const NAMES: Record<string, string> = { dana: "Dana", sam: "Sam", you: "You" };
const ROLES: Record<string, string> = { dana: "Product", sam: "Support", you: "on call" };
const PAGE_STATES: PageState[] = ["investigating", "identified", "monitoring", "resolved"];

function message(error: unknown): string {
  return error instanceof ApiError ? error.message : "That didn't work.";
}

// The incident screen, kept deliberately light (the lead found the full mockup too heavy):
// a top bar, a narrow left column (impact, services, changes), dashboards and the buttons
// in the middle, and one right column for the chat and the status update.
export function Incident({ gameId, onEnded }: { gameId: string; onEnded: () => void }) {
  const [s, setS] = useState<AgentState | null>(null);
  const [scene, setScene] = useState<Scene | null>(null);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [answered, setAnswered] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const cursor = useRef(0);
  const ended = useRef(false);

  useEffect(() => {
    game.scene(gameId).then(setScene).catch(() => undefined);
  }, [gameId]);

  // Polling the state every 2 s is also the heartbeat (5 minutes without it = abandoned).
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const next = await game.state(gameId, cursor.current);
        if (!live) return;
        setS(next);
        if (next.feed.items.length) {
          cursor.current = next.feed.cursor;
          setFeed((old) => [...old, ...next.feed.items]);
        }
        if (next.state === "ended" && !ended.current) {
          ended.current = true;
          onEnded();
          return;
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 410) {
          onEnded();
          return;
        }
      }
      if (live) timer = window.setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [gameId, onEnded]);

  const act = useCallback(async (what: () => Promise<unknown>, done?: (reply: unknown) => string) => {
    setBusy(true);
    setNote("");
    try {
      const reply = await what();
      if (done) setNote(done(reply));
    } catch (error) {
      setNote(message(error));
    } finally {
      setBusy(false);
    }
  }, []);

  const running = s !== null && s.state !== "ended";
  const t = useTicking(s?.clock.t ?? null, 1, running);
  const left = useTicking(s?.clock.time_left_s ?? null, -1, running);
  const hintWait = useTicking(s?.hints.next_available_in_s ?? null, -1, running);

  if (!s) return <p className="p-10 font-mono text-sm text-muted">Connecting to your server…</p>;
  const paged = s.page.fired;
  const acked = s.page.acknowledged;
  const hintsLeft = s.hints.total - s.hints.used;

  // translate="no": a browser translating this screen rewrites text React updates 4 times a
  // second, which can blank the page. The rest of the site can still be translated.
  return (
    <div className="flex min-h-screen flex-col" translate="no">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-5 py-3">
        <span className="font-display text-sm font-black">IncidentTrainer</span>
        {paged ? (
          <>
            <span className={`rounded border px-2 py-0.5 font-mono text-xs ${s.severity === "SEV1" ? "border-bad text-bad" : "border-warn text-warn"}`}>
              {s.severity}
            </span>
            <span className="font-display font-black">{s.page.title}</span>
            {acked ? (
              <span className="font-mono text-xs text-good">
                ● Acknowledged{typeof s.page.acknowledged_t === "number" ? ` at ${clock(s.page.acknowledged_t)}` : ""}
              </span>
            ) : (
              <Button disabled={busy} onClick={() => act(() => game.ack(gameId))}>Acknowledge</Button>
            )}
          </>
        ) : (
          <span className="font-mono text-sm text-muted">Quiet so far… the page can come any second.</span>
        )}
        <span className="ml-auto flex items-baseline gap-3">
          <span className="font-mono text-2xl tabular-nums">{t === null ? "--:--" : clock(t)}</span>
          <span className="font-mono text-xs text-faint tabular-nums">
            {s.clock.scene_time ? `${s.clock.scene_time} in-world · ` : ""}
            {left !== null ? `${clock(left)} left` : ""}
          </span>
        </span>
        {s.hints.total > 0 && (
          <Button kind="plain" disabled={busy || !acked || hintsLeft === 0 || (hintWait ?? 0) > 0}
            onClick={() => act(() => game.hint(gameId))}>
            {hintsLeft === 0 ? "No hints left" : (hintWait ?? 0) > 0 ? `Hint in ${clock(hintWait ?? 0)}` : `Hint (−3) · ${hintsLeft} left`}
          </Button>
        )}
        <Button kind="plain" disabled={busy}
          onClick={() => {
            if (window.confirm("Give up this incident? The game ends and gets scored.")) act(() => game.giveUp(gameId));
          }}>
          Give up
        </Button>
        <Button disabled={busy || !acked || s.state === "watching"}
          onClick={() =>
            act(() => game.resolve(gameId), (r) => {
              const reply = r as { result: string; failing?: { message: string }[]; watch_seconds?: number };
              return reply.result === "passed"
                ? `Resolve passed. Watching for ${reply.watch_seconds ?? 0} s: keep it stable.`
                : `Not resolved yet: ${(reply.failing ?? []).map((f) => f.message).join(" ")}`;
            })
          }>
          {s.state === "watching" ? `Watching · ${clock(s.resolve.watch_ends_in_s ?? 0)}` : "Resolve"}
        </Button>
      </header>
      {note && <div className="border-b border-line bg-panel px-5 py-2 text-sm">{note}</div>}

      <main className="grid flex-1 gap-5 p-5 lg:grid-cols-[15rem_1fr_22rem]">
        <Situation s={s} />
        <section className="flex min-w-0 flex-col gap-5">
          <Dashboards gameId={gameId} path={scene?.dashboards[0]?.path ?? ""} />
          <Act gameId={gameId} buttons={scene?.actions ?? []} disabled={!paged} />
        </section>
        <section className="flex min-h-0 flex-col gap-5">
          <Chat
            items={feed}
            answered={answered}
            onReply={(messageId, optionId) =>
              act(async () => {
                await game.reply(gameId, messageId, optionId);
                setAnswered((old) => new Set(old).add(messageId));
              })
            }
          />
          <StatusForm gameId={gameId} s={s} disabled={!acked} />
        </section>
      </main>
    </div>
  );
}

function tone(service: Service): string {
  return service.status === "ok" ? "text-good" : service.status === "unknown" ? "text-faint" : service.status === "down" ? "text-bad" : "text-warn";
}

function Situation({ s }: { s: AgentState }) {
  return (
    <aside className="space-y-6">
      <section>
        <Eyebrow>Customer impact</Eyebrow>
        <p className="mt-1 font-mono text-3xl">{s.health.value}%</p>
        <div className="mt-2 h-1.5 rounded bg-raised">
          <div className={`h-1.5 rounded ${s.health.value < 50 ? "bg-bad" : "bg-accent"}`} style={{ width: `${s.health.value}%` }} />
        </div>
        <p className="mt-1 font-mono text-xs text-faint">{s.health.failed_checkouts ?? 0} failed checkouts</p>
      </section>
      <section>
        <Eyebrow>Services</Eyebrow>
        <ul className="mt-2 space-y-1 font-mono text-xs">
          {s.services.map((service) => (
            <li key={service.name} className="flex justify-between gap-2">
              <span className="text-text">{service.name}</span>
              <span className={tone(service)}>● {service.label}</span>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <Eyebrow>Recent changes</Eyebrow>
        <ul className="mt-2 space-y-2 text-xs">
          {s.changes.slice(0, 6).map((c, i) => (
            <li key={i}>
              <span className="font-mono text-faint">
                {c.t !== null && c.t !== undefined ? clock(c.t) : c.ago_s ? `${Math.round(c.ago_s / 60)} min ago` : ""}
              </span>{" "}
              <span className="font-mono">{c.service}</span> <span className="text-muted">{c.text}</span>
              <span className="block text-faint">by {c.by}</span>
            </li>
          ))}
        </ul>
      </section>
    </aside>
  );
}

function Dashboards({ gameId, path }: { gameId: string; path: string }) {
  const [up, setUp] = useState(false);
  useEffect(() => {
    if (up) return;
    let live = true;
    let timer: number | undefined;
    const check = async () => {
      if (await game.grafanaReady(gameId)) {
        if (live) setUp(true);
        return;
      }
      if (live) timer = window.setTimeout(check, 3000);
    };
    check();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [gameId, up]);
  return (
    <div className="h-[52vh] overflow-hidden rounded border border-line">
      {path && up ? (
        <iframe title="Dashboards" src={`/s/${gameId}/${path}`} className="h-full w-full" />
      ) : (
        <p className="p-6 font-mono text-sm text-faint">Dashboards are starting…</p>
      )}
    </div>
  );
}

function Act({ gameId, buttons, disabled }: { gameId: string; buttons: ActionButton[]; disabled: boolean }) {
  const [open, setOpen] = useState<ActionButton | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ActionResult | null>(null);
  const [running, setRunning] = useState("");
  const [problem, setProblem] = useState("");

  async function run(button: ActionButton, input: Record<string, string>) {
    if (button.confirm && !window.confirm(button.confirm)) return;
    setRunning(button.id);
    setProblem("");
    try {
      setResult(await game.press(gameId, button.id, input));
      setOpen(null);
    } catch (error) {
      setProblem(message(error));
    } finally {
      setRunning("");
    }
  }

  function press(button: ActionButton) {
    const inputs = Object.entries(button.input ?? {});
    if (inputs.length === 0) return run(button, {});
    setOpen(button);
    setValues(Object.fromEntries(inputs.map(([name, spec]) => [name, spec.choices?.length === 1 ? spec.choices[0] : ""])));
  }

  return (
    <section>
      <div className="flex items-baseline justify-between">
        <Eyebrow>Act · each button runs a real command on your server</Eyebrow>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {buttons.map((button) => (
          <button key={button.id} disabled={disabled || Boolean(running)} onClick={() => press(button)}
            className={`rounded border px-3 py-1.5 text-sm disabled:opacity-40 ${open?.id === button.id ? "border-accent text-accent" : "border-line hover:border-muted"}`}>
            {running === button.id ? "Running…" : button.label}
          </button>
        ))}
      </div>
      {open && (
        <form className="mt-3 flex flex-wrap items-end gap-2 rounded border border-line bg-panel p-3"
          onSubmit={(e) => {
            e.preventDefault();
            run(open, values);
          }}>
          {Object.entries(open.input ?? {}).map(([name, spec]) => (
            <label key={name} className="text-xs text-muted">
              {name}
              {spec.choices ? (
                <select value={values[name] ?? ""} onChange={(e) => setValues({ ...values, [name]: e.target.value })}
                  className="mt-1 block rounded border border-line bg-ink px-2 py-1.5 font-mono text-sm text-text">
                  <option value="" disabled>choose…</option>
                  {spec.choices.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <input value={values[name] ?? ""} pattern={spec.pattern} maxLength={64}
                  onChange={(e) => setValues({ ...values, [name]: e.target.value })}
                  className="mt-1 block rounded border border-line bg-ink px-2 py-1.5 font-mono text-sm text-text" />
              )}
            </label>
          ))}
          <Button type="submit" disabled={Object.values(values).some((v) => !v) || Boolean(running)}>Run</Button>
          <Button type="button" kind="plain" onClick={() => setOpen(null)}>Cancel</Button>
        </form>
      )}
      {problem && <p className="mt-2 text-sm text-bad">{problem}</p>}
      {result && (
        <pre className="mt-3 max-h-56 overflow-auto rounded border border-line bg-panel p-3 font-mono text-xs leading-relaxed">
          <span className="text-faint">{result.t !== null ? clock(result.t) : ""} $ </span>
          <span className="text-text">{result.command}</span>
          {"\n"}
          <span className="text-muted">{result.output}</span>
          {result.truncated ? "\n…" : ""}
          {"\n"}
          <span className={result.exit_code === 0 ? "text-good" : "text-bad"}>exit {result.exit_code}</span>
          <span className="text-faint"> · {(result.duration_ms / 1000).toFixed(1)} s</span>
        </pre>
      )}
    </section>
  );
}

function Chat({ items, answered, onReply }: {
  items: FeedItem[];
  answered: Set<string>;
  onReply: (messageId: string, optionId: string) => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const shown = items.filter((i) => i.kind !== "action_result");
  // A block body on purpose: Chrome and Edge 150+ return a Promise from scrollIntoView(),
  // and an effect that returns anything is called later as its cleanup: that crashed the
  // screen on the next chat message ("l is not a function", 22 Nov).
  useEffect(() => {
    void end.current?.scrollIntoView({ block: "nearest" });
  }, [shown.length]);
  return (
    <section className="flex min-h-0 flex-1 flex-col rounded border border-line">
      <div className="border-b border-line px-3 py-2"><Eyebrow>#inc-checkout</Eyebrow></div>
      <div className="max-h-[45vh] min-h-40 flex-1 space-y-3 overflow-y-auto p-3 text-sm">
        {shown.length === 0 && <p className="text-xs text-faint">Nothing here yet.</p>}
        {shown.map((item) => (
          <FeedLine key={item.id} item={item} answered={answered} onReply={onReply} />
        ))}
        <div ref={end} />
      </div>
    </section>
  );
}

function FeedLine({ item, answered, onReply }: {
  item: FeedItem;
  answered: Set<string>;
  onReply: (messageId: string, optionId: string) => void;
}) {
  const at = item.t !== null ? clock(item.t) : "";
  if (item.kind === "chat_message" || item.kind === "chat_reply") {
    const who = item.from ?? "you";
    const open = item.kind === "chat_message" && item.replies?.length && item.message_id && !answered.has(item.message_id);
    return (
      <div>
        <p className="text-xs">
          <span className="font-display font-black">{NAMES[who]}</span>{" "}
          <span className="text-faint">{ROLES[who]} · {at}</span>
        </p>
        <p className={who === "you" ? "text-muted" : ""}>{item.text}</p>
        {open && (
          <div className="mt-2 space-y-1">
            {item.replies?.map((r) => (
              <button key={r.id} onClick={() => onReply(item.message_id ?? "", r.id)}
                className="block w-full rounded border border-line px-2 py-1.5 text-left text-xs hover:border-accent">
                {r.text}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (item.kind === "status_update" && item.status_update) {
    const u = item.status_update;
    return (
      <div className="rounded border border-line bg-panel p-2 text-xs">
        <p className="text-faint">Status update · {at} · page: {u.page_state}</p>
        <p>{u.affected}</p>
        <p className="text-muted">{u.doing}</p>
        {u.next_update_at && <p className="text-faint">Next update by {u.next_update_at}</p>}
      </div>
    );
  }
  if (item.kind === "hint") {
    return <p className="rounded border border-warn/40 p-2 text-xs"><span className="text-warn">Hint {item.n} · </span>{item.text}</p>;
  }
  return <p className={`text-xs ${item.level === "error" ? "text-bad" : item.level === "warning" ? "text-warn" : "text-faint"}`}>{at} {item.text}</p>;
}

function StatusForm({ gameId, s, disabled }: { gameId: string; s: AgentState; disabled: boolean }) {
  const [affected, setAffected] = useState("");
  const [doing, setDoing] = useState("");
  const [next, setNext] = useState("");
  const [pageState, setPageState] = useState<PageState>(s.status_page.state);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  async function post(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem("");
    try {
      await game.status(gameId, { affected, doing, next_update_at: next || null, page_state: pageState });
      setAffected("");
      setDoing("");
      setNext("");
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={post} className="space-y-2 rounded border border-line p-3 text-xs">
      <div className="flex justify-between">
        <Eyebrow>Status update</Eyebrow>
        <span className="font-mono text-faint">
          {s.status_page.last_update_t !== null ? `last ${clock(s.status_page.last_update_t)}` : "none yet"}
        </span>
      </div>
      <input placeholder="Who is affected" value={affected} onChange={(e) => setAffected(e.target.value)} maxLength={500}
        className="block w-full rounded border border-line bg-panel px-2 py-1.5 text-sm placeholder:text-faint" />
      <input placeholder="What we're doing" value={doing} onChange={(e) => setDoing(e.target.value)} maxLength={500}
        className="block w-full rounded border border-line bg-panel px-2 py-1.5 text-sm placeholder:text-faint" />
      <label className="flex items-center justify-between gap-2 text-muted">
        Next update at (in-world)
        <input type="time" value={next} onChange={(e) => setNext(e.target.value)}
          className="rounded border border-line bg-panel px-2 py-1 font-mono text-sm text-text" />
      </label>
      <div className="grid grid-cols-4 gap-1">
        {PAGE_STATES.map((p) => (
          <button type="button" key={p} onClick={() => setPageState(p)} aria-pressed={pageState === p}
            className={`rounded border px-1 py-1 capitalize ${pageState === p ? "border-accent text-accent" : "border-line text-muted"}`}>
            {p}
          </button>
        ))}
      </div>
      <p className="text-faint">Public page now: <span className="capitalize text-text">{s.status_page.state}</span></p>
      {problem && <p className="text-bad">{problem}</p>}
      <Button type="submit" className="w-full" disabled={disabled || busy}>Post</Button>
    </form>
  );
}
