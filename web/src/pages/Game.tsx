import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Eyebrow, Notice, Page } from "../components/Layout";
import { Incident } from "./Incident";
import { ApiError, type GameStatus, type LabCard, api, clock } from "../lib/api";
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
