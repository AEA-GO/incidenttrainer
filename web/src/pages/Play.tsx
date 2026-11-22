import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Notice, Page } from "../components/Layout";
import { ApiError, api } from "../lib/api";
import { normalise, saveCode } from "../lib/invite";

// The invite link (/play?code=XXXX): check the code, keep it for this tab, then drop it
// from the address bar (the code is never in a URL after this page).
export function Play() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [problem, setProblem] = useState("");

  useEffect(() => {
    const code = normalise(params.get("code"));
    if (!code) {
      setProblem("This link has no valid invite code.");
      return;
    }
    let live = true;
    api
      .invite(code)
      .then(() => {
        saveCode(code);
        if (live) navigate("/labs", { replace: true });
      })
      .catch((error: unknown) => {
        if (live) setProblem(error instanceof ApiError ? error.message : "Something went wrong.");
      });
    return () => {
      live = false;
    };
  }, [params, navigate]);

  return (
    <Page>
      <div className="mx-auto max-w-md py-24">
        {problem ? (
          <>
            <Notice tone="bad">{problem}</Notice>
            <Link to="/" className="mt-4 inline-block text-sm text-muted underline">
              Back to the start page
            </Link>
          </>
        ) : (
          <p className="font-mono text-sm text-muted">Checking your invite code…</p>
        )}
      </div>
    </Page>
  );
}
