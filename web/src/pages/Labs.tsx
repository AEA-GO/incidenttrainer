import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Eyebrow, Notice, Page } from "../components/Layout";
import { LevelTag } from "../components/LevelTag";
import { ApiError, type Course, api } from "../lib/api";
import { savedCode, shown } from "../lib/invite";

export function Labs() {
  const [course, setCourse] = useState<Course | null>(null);
  const [problem, setProblem] = useState("");
  const code = savedCode();

  useEffect(() => {
    api
      .course()
      .then(setCourse)
      .catch((error: unknown) =>
        setProblem(error instanceof ApiError ? error.message : "Something went wrong."),
      );
  }, []);

  return (
    <Page>
      <div className="py-16">
        <Eyebrow>Course</Eyebrow>
        {problem && <div className="mt-4"><Notice tone="bad">{problem}</Notice></div>}
        {course && (
          <>
            <h1 className="mt-2 font-display text-4xl font-black">{course.title}</h1>
            {course.summary && <p className="mt-3 max-w-2xl text-muted">{course.summary}</p>}
            {code && course.invite ? (
              <p className="mt-4 font-mono text-xs text-faint">
                Code {shown(code)} · {course.invite.games_left} of {course.invite.games_total} games left ·{" "}
                {course.invite.games_left_today} today
              </p>
            ) : (
              <div className="mt-6">
                <Notice>
                  You need an invite code to play. <Link to="/" className="underline">Enter it here</Link>.
                </Notice>
              </div>
            )}
            <ol className="mt-10 divide-y divide-line border-y border-line">
              {course.labs.map((lab) => (
                <li key={lab.id}>
                  <Link to={`/labs/${lab.id}`} className="grid gap-2 rounded px-4 py-6 hover:bg-panel md:grid-cols-[4rem_1fr_auto] md:items-center">
                    <span className="font-mono text-2xl text-faint">{String(lab.order).padStart(2, "0")}</span>
                    <span>
                      <span className="block font-display text-xl font-black">{lab.title}</span>
                      <span className="mt-1 block text-sm text-muted">{lab.hook}</span>
                      <LevelTag level={lab.level} minutes={lab.minutes} />
                    </span>
                    <span className="font-mono text-sm">
                      {lab.done ? (
                        <span className="text-muted">Best · {lab.done.score} · {lab.done.grade}</span>
                      ) : (
                        <span className="text-faint">Not played</span>
                      )}
                    </span>
                  </Link>
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </Page>
  );
}
