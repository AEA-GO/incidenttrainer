import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Button, Eyebrow, Notice, Page } from "../components/Layout";
import { ApiError, type GameStatus, type LabCard, api, clock } from "../lib/api";
import { type AgentState, type Service, game } from "../lib/game";
import { useTicking } from "../lib/useTicking";

const POLL_MS = 2000;

// One page for the whole game: boot screen → incident → (ended) finish → results.
export function Game() {
  const { gameId = "" } = useParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<GameStatus | null>(null);
  const [lab, setLab] = useState<LabCard | null>(null);
  const [problem, setProblem] = useState("");

  // Boot phase: ask the API (the gateway only routes once the game is ready).
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const s = await api.status(gameId);
        if (!live) return;
        setStatus(s);
        if (s.state !== "booting") return; // the incident view polls the game itself
      } catch (error) {
        if (live) setProblem(error instanceof ApiError ? error.message : "Something went wrong.");
      }
      if (live) timer = window.setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [gameId]);

  useEffect(() => {
    if (status?.lab_id && !lab) api.lab(status.lab_id).then(setLab).catch(() => undefined);
  }, [status?.lab_id, lab]);

  const ended = useCallback(() => navigate(`/results/${gameId}`, { replace: true }), [gameId, navigate]);
  const markEnded = useCallback(() => setStatus((s) => (s ? { ...s, state: "ended" } : s)), []);

  if (problem) {
    return (
      <Page>
        <div className="mx-auto max-w-md py-24">
          <Notice tone="bad">{problem}</Notice>
          <Link to="/labs" className="mt-4 inline-block text-sm text-muted underline">Back to the labs</Link>
        </div>
      </Page>
    );
  }
  if (!status || status.state === "booting") return <Boot status={status} lab={lab} />;
  if (status.state === "ended") return <Finishing gameId={gameId} onDone={ended} />;
  return <Incident gameId={gameId} onEnded={markEnded} />;
}

function Boot({ status, lab }: { status: GameStatus | null; lab: LabCard | null }) {
  const seconds = useTicking(status?.boot.seconds_since_start ?? null) ?? 0;
  const steps: [string, boolean][] = [
    ["Server started", Boolean(status?.boot.server_started)],
    ["Shop, monitoring and the agent starting", Boolean(status?.boot.agent_ready)],
  ];
  return (
    <Page>
      <div className="grid gap-12 py-16 md:grid-cols-[1.3fr_1fr]">
        <div>
          <Eyebrow>Handover note</Eyebrow>
          <h1 className="mt-2 font-display text-3xl font-black">{lab?.title ?? "Your shift is starting"}</h1>
          <p className="mt-6 whitespace-pre-line text-muted">
            {lab?.handover_note || "Your server is starting. Read this while it boots."}
          </p>
        </div>
        <div className="rounded border border-line bg-panel p-6">
          <Eyebrow>Starting your server</Eyebrow>
          <ul className="mt-4 space-y-3 font-mono text-sm">
            {steps.map(([label, done]) => (
              <li key={label} className={done ? "text-text" : "text-faint"}>
                {done ? <span className="text-good">● </span> : <span className="animate-pulse">○ </span>}
                {label}
              </li>
            ))}
          </ul>
          <p className="mt-6 font-mono text-3xl tabular-nums">{clock(seconds)}</p>
          <p className="mt-1 text-xs text-faint">Usually about two minutes. The page comes soon after.</p>
        </div>
      </div>
    </Page>
  );
}

function tone(service: Service): string {
  return service.status === "ok" ? "text-good" : service.status === "unknown" ? "text-faint" : service.status === "down" ? "text-bad" : "text-warn";
}

// A deliberately small first incident view (the lead found the full mockup too heavy):
// page + Acknowledge, clock, health, services, Grafana, Resolve, Give up. Buttons, chat and
// the status form come next.
function Incident({ gameId, onEnded }: { gameId: string; onEnded: () => void }) {
  const [s, setS] = useState<AgentState | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [dashboard, setDashboard] = useState("");
  const ended = useRef(false);

  useEffect(() => {
    game.scene(gameId).then((scene) => setDashboard(scene.dashboards[0]?.path ?? "")).catch(() => undefined);
  }, [gameId]);

  // Grafana starts a little after the agent: show the dashboards only once it answers.
  const [grafanaUp, setGrafanaUp] = useState(false);
  useEffect(() => {
    if (grafanaUp) return;
    let live = true;
    let timer: number | undefined;
    const check = async () => {
      if (await game.grafanaReady(gameId)) {
        if (live) setGrafanaUp(true);
        return;
      }
      if (live) timer = window.setTimeout(check, 3000);
    };
    check();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [gameId, grafanaUp]);

  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const next = await game.state(gameId);
        if (!live) return;
        setS(next);
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

  async function act(what: () => Promise<unknown>, done?: (reply: unknown) => string) {
    setBusy(true);
    setNote("");
    try {
      const reply = await what();
      if (done) setNote(done(reply));
    } catch (error) {
      setNote(error instanceof ApiError ? error.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  }

  const clockRuns = s !== null && s.state !== "ended";
  const t = useTicking(s?.clock.t ?? null, 1, clockRuns);
  const left = useTicking(s?.clock.time_left_s ?? null, -1, clockRuns);

  if (!s) return <Page><p className="py-24 font-mono text-sm text-muted">Connecting to your server…</p></Page>;
  const paged = s.page.fired;
  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center gap-4 border-b border-line px-6 py-3">
        <span className="font-display text-sm font-black">IncidentTrainer</span>
        {paged ? (
          <>
            <span className={`rounded border px-2 py-0.5 font-mono text-xs ${s.severity === "SEV1" ? "border-bad text-bad" : "border-warn text-warn"}`}>{s.severity}</span>
            <span className="font-display font-black">{s.page.title}</span>
          </>
        ) : (
          <span className="font-mono text-sm text-muted">Quiet so far… the page can come any second.</span>
        )}
        <span className="ml-auto font-mono text-2xl tabular-nums">{t === null ? "--:--" : clock(t)}</span>
        {left !== null && <span className="font-mono text-xs text-faint tabular-nums">{clock(left)} left</span>}
      </header>
      <main className="grid gap-6 p-6 lg:grid-cols-[18rem_1fr]">
        <aside className="space-y-6">
          {paged && !s.page.acknowledged && (
            <Button className="w-full" disabled={busy} onClick={() => act(() => game.ack(gameId))}>Acknowledge</Button>
          )}
          {paged && s.page.acknowledged && (
            <p className="font-mono text-xs text-good">
              ● Acknowledged{typeof s.page.acknowledged_t === "number" ? ` at ${clock(s.page.acknowledged_t)}` : ""}
            </p>
          )}
          <section>
            <Eyebrow>Customer impact</Eyebrow>
            <p className="mt-1 font-mono text-3xl">{s.health.value}%</p>
            <div className="mt-2 h-1.5 rounded bg-raised"><div className="h-1.5 rounded bg-accent" style={{ width: `${s.health.value}%` }} /></div>
            <p className="mt-1 font-mono text-xs text-faint">{s.health.failed_checkouts ?? 0} failed checkouts</p>
          </section>
          <section>
            <Eyebrow>Services</Eyebrow>
            <ul className="mt-2 space-y-1 font-mono text-sm">
              {s.services.map((service) => (
                <li key={service.name} className="flex justify-between gap-2">
                  <span>{service.name}</span>
                  <span className={tone(service)}>● {service.label} {service.detail ?? ""}</span>
                </li>
              ))}
            </ul>
          </section>
          <section className="space-y-2">
            <Button className="w-full" disabled={busy || !paged} onClick={() =>
              act(() => game.resolve(gameId), (r) => {
                const reply = r as { result: string; failing?: { message: string }[]; watch_seconds?: number };
                return reply.result === "passed"
                  ? `Resolve passed. Watching for ${reply.watch_seconds ?? 0} s.`
                  : `Not resolved yet: ${(reply.failing ?? []).map((f) => f.message).join(" ")}`;
              })}>Resolve</Button>
            <Button kind="plain" className="w-full" disabled={busy} onClick={() => {
              if (window.confirm("Give up this incident? The game ends and gets scored.")) act(() => game.giveUp(gameId));
            }}>Give up</Button>
            {note && <Notice>{note}</Notice>}
          </section>
        </aside>
        <section className="min-h-[70vh] overflow-hidden rounded border border-line">
          {dashboard && grafanaUp ? (
            <iframe title="Dashboards" src={`/s/${gameId}/${dashboard}`} className="h-full min-h-[70vh] w-full" />
          ) : (
            <p className="p-6 font-mono text-sm text-faint">Dashboards are starting…</p>
          )}
        </section>
      </main>
    </div>
  );
}

function Finishing({ gameId, onDone }: { gameId: string; onDone: () => void }) {
  const [problem, setProblem] = useState("");
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const finish = async () => {
      try {
        const result = await api.finish(gameId);
        if (!live) return;
        if (result.scored) onDone();
        else setProblem(result.why === "failed" ? "Your server didn't start. The game wasn't counted: try again." : "This game ended before the page, so there's no score.");
      } catch (error) {
        if (live && error instanceof ApiError && error.code === "not_ended") {
          timer = window.setTimeout(finish, (error.retryAfterS ?? 3) * 1000);
        } else if (live) setProblem(error instanceof ApiError ? error.message : "Something went wrong.");
      }
    };
    finish();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [gameId, onDone]);
  return (
    <Page>
      <div className="mx-auto max-w-md py-24">
        {problem ? (
          <>
            <Notice>{problem}</Notice>
            <Link to="/labs" className="mt-4 inline-block text-sm text-muted underline">Back to the labs</Link>
          </>
        ) : (
          <p className="font-mono text-sm text-muted">The incident is over. Scoring your shift…</p>
        )}
      </div>
    </Page>
  );
}
