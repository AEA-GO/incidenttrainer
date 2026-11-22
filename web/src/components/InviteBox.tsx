import { useState } from "react";
import { useNavigate } from "react-router";
import { normalise } from "../lib/invite";
import { Button } from "./Layout";

export function InviteBox() {
  const [text, setText] = useState("");
  const [problem, setProblem] = useState("");
  const navigate = useNavigate();

  function play(event: React.FormEvent) {
    event.preventDefault();
    const code = normalise(text);
    if (!code) {
      setProblem("Invite codes have 8 letters and digits, like K7Q4-2MXD.");
      return;
    }
    navigate(`/play?code=${code}`);
  }

  return (
    <form onSubmit={play} className="w-full max-w-md">
      <label htmlFor="invite" className="font-display text-sm font-bold">
        Have an invite code?
      </label>
      <div className="mt-2 flex gap-2">
        <input
          id="invite"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setProblem("");
          }}
          placeholder="K7Q4-2MXD"
          autoComplete="off"
          spellCheck={false}
          maxLength={12}
          className="flex-1 rounded border border-line bg-panel px-3 py-2.5 font-mono text-sm uppercase tracking-wider placeholder:text-faint"
        />
        <Button type="submit">Play</Button>
      </div>
      <p className={`mt-2 text-xs ${problem ? "text-bad" : "text-faint"}`}>
        {problem || "No account. Pick a nickname when you start."}
      </p>
    </form>
  );
}
