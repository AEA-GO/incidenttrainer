"""Status updates and hints (agent step 9; contract 2, /v1/status-updates and /v1/hints).

A mixin for Agent (state.py), like outcome.py.

- A status update is allowed after Acknowledge (investigating, watching), at most one
  per 10 s. `next_update_at` is an in-world time (HH:MM, the scene's clock); it must be
  later than now and at most 60 minutes ahead, and becomes `next_update_t` (game time).
  The event also records, hidden until the end, whether the success checks were passing:
  scoring uses it to see if the status page claimed recovery too early.
- Hints open in order after Acknowledge; a hint with `after_s` opens only that many
  seconds after the page. Each costs 3 points at scoring (penalty.hints).
"""

from itr_agent.errors import AgentError

STATUS_MIN_GAP_S = 10
PROMISE_MAX_S = 3600
PAGE_STATES = ("investigating", "identified", "monitoring", "resolved")
DAY_S = 24 * 3600


def promise_t(starts_at: str, t: int, hhmm: str) -> int:
    """Game time of an in-world HH:MM promise, the next time that clock shows it.

    Raises if it isn't later than now or is more than 60 minutes ahead."""
    start_h, start_m = (int(x) for x in starts_at.split(":"))
    want_h, want_m = (int(x) for x in hhmm.split(":"))
    now_s = (start_h * 3600 + start_m * 60 + t) % DAY_S
    ahead = (want_h * 3600 + want_m * 60 - now_s) % DAY_S
    if ahead == 0 or ahead > PROMISE_MAX_S:
        raise AgentError(
            422,
            "bad_input",
            "The next update time must be later than now and within 60 minutes.",
        )
    return t + ahead


class CommsMixin:
    # ----- player: status update ---------------------------------------------------
    def post_status(
        self, affected: str, doing: str, next_update_at: str | None, page_state: str
    ) -> dict:
        affected, doing = affected.strip(), doing.strip()
        with self._lock:
            if self.state == "ended" or self._ending:
                raise AgentError(409, "game_over", "The game is over.")
            if self.state not in ("investigating", "watching"):
                raise AgentError(409, "wrong_state", "Acknowledge the page first.")
            if page_state not in PAGE_STATES:
                raise AgentError(422, "bad_input", "Pick a status page state.")
            now = self.clock()
            if (
                self._last_status_at is not None
                and now - self._last_status_at < STATUS_MIN_GAP_S
            ):
                raise AgentError(
                    429, "too_fast", "One status update per 10 seconds.", 10
                )
            t = self.game_time() or 0
            starts_at = self.runbook["scene"]["starts_at"]
            next_t = promise_t(starts_at, t, next_update_at) if next_update_at else None
            self._last_status_at = now
            checks = self.runbook["checks"]["success"]
        passing = all(
            r.passed for r in self.checker.run_all(checks)
        )  # outside the lock

        with self._lock:
            if self.state == "ended" or self._ending:
                raise AgentError(409, "game_over", "The game ended meanwhile.")
            filled = sum(1 for x in (affected, doing, next_update_at) if x) + 1
            self.events.add(
                "status_update",
                {
                    "affected": affected,
                    "doing": doing,
                    "next_update_t": next_t,
                    "page_state": page_state,
                    "fields_filled": filled,
                    "checks_passing": passing,  # hidden until the end (events.md)
                },
                t=t,
            )
            self.feed.add(
                "status_update",
                t,
                status_update={
                    "affected": affected,
                    "doing": doing,
                    "next_update_at": next_update_at,
                    "page_state": page_state,
                },
            )
            self.page_state = page_state
            self.last_update_t = t
            self.next_update_at = next_update_at
            self.chat.status_posted(t, next_t)
            return {"t": t, "fields_filled": filled, "next_update_t": next_t}

    # ----- player: hints ---------------------------------------------------------------
    def hints_total(self) -> int:
        return len(self.runbook.get("hints", []))

    def next_hint_in(self) -> int | None:
        """Seconds until the next hint can be opened (0 = now); None if none are left."""
        hints = self.runbook.get("hints", [])
        if self.hints_used >= len(hints):
            return None
        t = self.game_time() or 0
        return max(0, int(hints[self.hints_used].get("after_s", 0)) - t)

    def open_hint(self) -> dict:
        with self._lock:
            if self.state == "ended" or self._ending:
                raise AgentError(409, "game_over", "The game is over.")
            if self.state not in ("investigating", "watching"):
                raise AgentError(409, "wrong_state", "Acknowledge the page first.")
            wait = self.next_hint_in()
            if wait is None:
                raise AgentError(409, "no_hints_left", "No hints left.")
            if wait > 0:
                raise AgentError(
                    409, "hint_not_yet", f"The next hint opens in {wait} s.", wait
                )
            hint = self.runbook["hints"][self.hints_used]
            self.hints_used += 1
            n, t = self.hints_used, self.game_time()
            self.events.add(
                "hint_used",
                {"n": n, "hint_id": hint.get("id", f"h{n}"), "text": hint["text"]},
                t=t,
            )
            self.feed.add("hint", t, n=n, text=hint["text"])
            self.chat.trigger("hint_used", t)
            return {
                "n": n,
                "text": hint["text"],
                "hints_left": self.hints_total() - n,
            }
