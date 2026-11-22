import { InviteBox } from "../components/InviteBox";
import { Eyebrow, GITHUB, Page } from "../components/Layout";

const STEPS = [
  ["01", "Get paged", "Something on the shop's server breaks. You acknowledge the page and the clock starts."],
  [
    "02",
    "Investigate and fix on a real server",
    "Dashboards, logs and, from Junior level, a terminal. Nothing is scripted underneath, so any command works.",
  ],
  [
    "03",
    "Get scored on the fix and how you communicated",
    "Time, hints, wrong calls and the updates you sent. Each lost point comes with the reason.",
  ],
];

export function Home() {
  return (
    <Page>
      <section className="grid gap-10 py-20 md:grid-cols-[1.2fr_1fr] md:items-end">
        <div>
          <Eyebrow>Free during the beta · invite only</Eyebrow>
          <h1 className="mt-4 font-display text-5xl leading-[1.05] font-black tracking-tight md:text-6xl">
            Your first on-call shift, before your first on-call shift.
          </h1>
          <p className="mt-6 max-w-xl text-lg text-muted">
            A real server running a small tea shop breaks. You get paged, read the dashboards and
            logs, fix the actual cause and keep your colleagues posted. Then you get a score, with
            a reason for every lost point.
          </p>
        </div>
        <InviteBox />
      </section>

      <section id="how" className="border-t border-line py-16">
        <h2 className="font-display text-sm font-bold text-muted">How it works</h2>
        <div className="mt-8 grid gap-10 md:grid-cols-3">
          {STEPS.map(([n, title, text]) => (
            <div key={n}>
              <p className="font-mono text-3xl text-faint">{n}</p>
              <h3 className="mt-3 font-display text-xl font-black">{title}</h3>
              <p className="mt-2 text-sm text-muted">{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="teams" className="grid gap-6 border-t border-line py-16 md:grid-cols-2">
        <h2 className="font-display text-2xl font-black">For teams and schools</h2>
        <div>
          <p className="text-lg">Run it as a game day or a course lab.</p>
          <p className="mt-2 text-sm text-muted">
            Same labs, same real servers, your people on call. We're trying this with a few teams
            and IT schools during the beta.
          </p>
          <a href={GITHUB} className="mt-3 inline-block font-display text-sm font-black text-accent underline">
            Get in touch
          </a>
        </div>
      </section>
    </Page>
  );
}
