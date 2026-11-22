import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Button, Eyebrow, Notice, Page } from "../components/Layout";
import { LevelTag } from "../components/LevelTag";
import { ApiError, type LabCard, api } from "../lib/api";
import { savedCode, savedNickname, saveNickname } from "../lib/invite";

const NICKNAME = /^[\p{L}\p{N} _.-]{2,24}$/u;

export function Lab() {
  const { labId = "" } = useParams();
  const navigate = useNavigate();
  const [lab, setLab] = useState<LabCard | null>(null);
  const [nickname, setNickname] = useState(savedNickname());
  const [problem, setProblem] = useState("");
  const [starting, setStarting] = useState(false);
  const code = savedCode();

  useEffect(() => {
    api
      .lab(labId)
      .then(setLab)
      .catch((error: unknown) =>
        setProblem(error instanceof ApiError ? error.message : "Something went wrong."),
      );
  }, [labId]);

  async function start(event: React.FormEvent) {
    event.preventDefault();
    const name = nickname.trim().replace(/\s+/g, " ");
    if (!NICKNAME.test(name)) {
      setProblem("Nickname: 2–24 letters, digits, spaces, _ . or -.");
      return;
    }
    setStarting(true);
    setProblem("");
    try {
      saveNickname(name);
      const game = await api.start(labId, name);
      navigate(`/game/${game.game_id}`, { replace: true });
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "Something went wrong.");
      setStarting(false);
    }
  }

  return (
    <Page>
      <div className="max-w-3xl py-16">
        <Link to="/labs" className="text-sm text-muted underline">← All labs</Link>
        {lab && (
          <>
            <Eyebrow>Lab {String(lab.order).padStart(2, "0")}</Eyebrow>
            <h1 className="mt-2 font-display text-4xl font-black">{lab.title}</h1>
            <p className="mt-3 text-lg text-muted">{lab.hook}</p>
            <div className="mt-3"><LevelTag level={lab.level} minutes={lab.minutes} /></div>
            {lab.skills.length > 0 && (
              <ul className="mt-6 flex flex-wrap gap-2">
                {lab.skills.map((skill) => (
                  <li key={skill} className="rounded border border-line px-2 py-1 font-mono text-xs text-muted">{skill}</li>
                ))}
              </ul>
            )}
            {lab.done && (
              <p className="mt-6 font-mono text-sm text-muted">
                Your best: {lab.done.score} · {lab.done.grade}{" "}
                <Link to={`/results/${lab.done.game_id}`} className="text-muted underline">results</Link>
              </p>
            )}
            {code ? (
              <form onSubmit={start} className="mt-10 max-w-md">
                <label htmlFor="nickname" className="font-display text-sm font-bold">Your nickname</label>
                <div className="mt-2 flex gap-2">
                  <input
                    id="nickname"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    maxLength={24}
                    placeholder="nightowl"
                    className="flex-1 rounded border border-line bg-panel px-3 py-2.5 text-sm placeholder:text-faint"
                  />
                  <Button type="submit" disabled={starting}>{starting ? "Starting…" : "Start shift"}</Button>
                </div>
                <p className="mt-2 text-xs text-faint">A real server starts for you. It takes about two minutes.</p>
              </form>
            ) : (
              <div className="mt-10"><Notice>You need an invite code to play. <Link to="/" className="underline">Enter it here</Link>.</Notice></div>
            )}
          </>
        )}
        {problem && <div className="mt-4"><Notice tone="bad">{problem}</Notice></div>}
      </div>
    </Page>
  );
}
