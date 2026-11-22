import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { Button, Eyebrow, Notice, Page } from "../components/Layout";
import { ApiError, type Results as ResultsData, api, clock } from "../lib/api";

const END = {
  resolved: "Resolved",
  unresolved: "Time ran out",
  gave_up: "Gave up",
  abandoned: "Left the shift",
} as Record<string, string>;

const EVENT_TEXT: Record<string, (d: Record<string, unknown>) => string> = {
  paged: (d) => `Paged: ${d.title ?? ""}`,
  acknowledged: () => "Acknowledged",
  action: (d) => `Ran: ${d.command ?? d.action_id}`,
  chat_message: (d) => `${d.from === "dana" ? "Dana" : "Sam"}: ${d.text ?? ""}`,
  chat_reply: (d) => `You: ${d.text ?? ""}`,
  status_update: (d) => `Status update (${d.page_state ?? ""})`,
  hint_used: (d) => `Hint ${d.n ?? ""}`,
  resolve_failed: () => "Resolve: not fixed yet",
  resolve_passed: () => "Resolve passed, watch started",
  reopened: () => "Reopened at SEV1",
  watch_passed: () => "Watch passed",
  gave_up: () => "Gave up",
  time_limit: () => "Time limit",
  ended: () => "Ended",
};

export function Results() {
  const { gameId = "" } = useParams();
  const [data, setData] = useState<ResultsData | null>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    api.results(gameId).then(setData).catch((error: unknown) =>
      setProblem(error instanceof ApiError ? error.message : "Something went wrong."),
    );
  }, [gameId]);

  if (problem || !data) {
    return (
      <Page>
        <div className="mx-auto max-w-md py-24">
          {problem ? <Notice tone="bad">{problem}</Notice> : <p className="font-mono text-sm text-muted">Loading your results…</p>}
          <Link to="/labs" className="mt-4 inline-block text-sm text-muted underline">Back to the labs</Link>
        </div>
      </Page>
    );
  }
  const { score, debrief } = data;
  const timeline = data.events.filter((e) => e.type in EVENT_TEXT);
  return (
    <Page>
      <div className="py-16">
        <Eyebrow>{data.lab.title} · {data.nickname} · {END[data.end_reason] ?? data.end_reason}</Eyebrow>
        <div className="mt-4 flex items-end gap-6">
          <p className="font-mono text-7xl">{score.score}</p>
          <p className="pb-2 font-display text-4xl font-black text-accent">{score.grade}</p>
        </div>

        <section className="mt-10 grid gap-10 md:grid-cols-2">
          <div>
            <h2 className="font-display text-xl font-black">Where the points went</h2>
            <ul className="mt-4 divide-y divide-line border-y border-line text-sm">
              {Object.entries(score.categories ?? {}).map(([name, c]) => (
                <li key={name} className="flex justify-between py-2">
                  <span className="capitalize">{name}</span>
                  <span className="font-mono">{c.got} / {c.max}</span>
                </li>
              ))}
            </ul>
            {(score.lost ?? []).length > 0 && (
              <ul className="mt-6 space-y-2 text-sm">
                {(score.lost ?? []).map((line) => (
                  <li key={line.rule} className="flex gap-3">
                    <span className="w-12 shrink-0 font-mono text-bad">−{line.points}</span>
                    <span className="text-muted">{line.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h2 className="font-display text-xl font-black">Your timeline</h2>
            <ol className="mt-4 space-y-1 font-mono text-xs">
              {timeline.map((e, i) => (
                <li key={i} className="flex gap-3">
                  <span className="w-12 shrink-0 text-faint">{clock(e.t)}</span>
                  <span className="text-muted">{EVENT_TEXT[e.type](e.data)}</span>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="mt-16 max-w-3xl border-t border-line pt-10">
          <h2 className="font-display text-xl font-black">What really happened</h2>
          {debrief.summary && <p className="mt-3 text-lg">{debrief.summary}</p>}
          {debrief.what_happened && <p className="mt-4 whitespace-pre-line text-muted">{debrief.what_happened}</p>}
          {debrief.root_cause && <p className="mt-4 text-sm"><span className="text-faint">Root cause: </span>{debrief.root_cause}</p>}
          {debrief.expert?.commands && debrief.expert.commands.length > 0 && (
            <>
              <h3 className="mt-8 font-display font-black">What an expert ran</h3>
              <ul className="mt-3 space-y-3">
                {debrief.expert.commands.map((c) => (
                  <li key={c.command}>
                    <code className="block rounded bg-panel px-3 py-2 font-mono text-xs">{c.command}</code>
                    <p className="mt-1 text-sm text-muted">{c.why}</p>
                  </li>
                ))}
              </ul>
            </>
          )}
          {debrief.lessons && debrief.lessons.length > 0 && (
            <>
              <h3 className="mt-8 font-display font-black">Take away</h3>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-muted">
                {debrief.lessons.map((l) => <li key={l}>{l}</li>)}
              </ul>
            </>
          )}
          {debrief.in_real_life?.text && (
            <>
              <h3 className="mt-8 font-display font-black">In real life</h3>
              <p className="mt-3 text-sm text-muted">{debrief.in_real_life.text}</p>
              <ul className="mt-2 space-y-1 text-sm">
                {(debrief.in_real_life.links ?? []).map((l) => (
                  <li key={l.url}><a href={l.url} className="underline" rel="noreferrer" target="_blank">{l.title}</a></li>
                ))}
              </ul>
            </>
          )}
        </section>

        <FeedbackForm gameId={data.game_id} given={data.feedback_given} />
        <div className="mt-10 flex gap-4">
          <Link to={data.next_lab_id ? `/labs/${data.next_lab_id}` : "/labs"}>
            <Button>{data.next_lab_id ? "Next lab" : "Back to the labs"}</Button>
          </Link>
          <Link to={`/labs/${data.lab.id}`}><Button kind="plain">Play again</Button></Link>
        </div>
      </div>
    </Page>
  );
}

function FeedbackForm({ gameId, given }: { gameId: string; given: boolean }) {
  const [replay, setReplay] = useState(0);
  const [stuck, setStuck] = useState("");
  const [fake, setFake] = useState("");
  const [state, setState] = useState<"open" | "sending" | "sent">(given ? "sent" : "open");
  const [problem, setProblem] = useState("");

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!replay) return setProblem("Pick a number from 1 to 5 first.");
    setState("sending");
    try {
      await api.feedback(gameId, { replay, stuck: stuck || undefined, fake: fake || undefined });
      setState("sent");
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "Couldn't send it. Try again.");
      setState("open");
    }
  }

  if (state === "sent") return <p className="mt-16 text-sm text-good">● Thanks for the feedback.</p>;
  return (
    <form onSubmit={send} className="mt-16 max-w-2xl rounded border border-line bg-panel p-6">
      <h2 className="font-display text-xl font-black">Three quick questions</h2>
      <fieldset className="mt-4">
        <legend className="text-sm">Would you play another lab? (1 = no, 5 = right now)</legend>
        <div className="mt-2 flex gap-2">
          {[1, 2, 3, 4, 5].map((n) => (
            <button type="button" key={n} onClick={() => setReplay(n)} aria-pressed={replay === n}
              className={`h-10 w-10 rounded border font-mono ${replay === n ? "border-accent text-accent" : "border-line text-muted"}`}>{n}</button>
          ))}
        </div>
      </fieldset>
      <label className="mt-4 block text-sm">Where did you get stuck or confused?
        <textarea value={stuck} onChange={(e) => setStuck(e.target.value)} maxLength={1000} rows={3}
          className="mt-1 block w-full rounded border border-line bg-ink px-3 py-2 text-sm" />
      </label>
      <label className="mt-4 block text-sm">Did anything feel fake?
        <textarea value={fake} onChange={(e) => setFake(e.target.value)} maxLength={1000} rows={3}
          className="mt-1 block w-full rounded border border-line bg-ink px-3 py-2 text-sm" />
      </label>
      {problem && <p className="mt-3 text-sm text-bad">{problem}</p>}
      <Button type="submit" className="mt-4" disabled={state === "sending"}>Send</Button>
    </form>
  );
}
