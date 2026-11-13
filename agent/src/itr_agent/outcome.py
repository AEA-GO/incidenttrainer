"""How a game is resolved, watched, reopened and ended.

A mixin for Agent (state.py): the methods use the agent's lock, clock, events,
feed and runbook. Rules (docs/contracts/game-states.md and events.md):

- Resolve works only while investigating (after Acknowledge, not during a watch).
  Failed: the failing checks are named in plain words; -2 points until -6.
  Passed: the watch starts.
- The watch re-runs the checks every interval. Any failure reopens the incident
  at SEV1. If they keep passing for the whole watch, the game ends `resolved`.
- Other endings: give up, the time limit (40 min), abandoned (from the gateway).
- At the end: damage checks run once; each failure is a `damage_found` event.

Checks can take seconds, so they never run while holding the lock.
"""

import logging

from itr_agent.checks import CheckResult
from itr_agent.clocks import clock_text
from itr_agent.errors import AgentError

PENALTY = 2
MAX_PENALTY_TOTAL = 6
RESOLVE_MIN_GAP_S = 5
END_NOTICES = {
    "resolved": "Resolved. The fix held for the whole watch.",
    "unresolved": "Time is up. The incident wasn't resolved.",
    "gave_up": "You ended the game.",
    "abandoned": "The game ended: no activity for 5 minutes.",
}
log = logging.getLogger(__name__)


class OutcomeMixin:
    # ----- player: Resolve -------------------------------------------------------

    def resolve(self) -> dict:
        with self._lock:
            if self.state != "investigating" or self._ending:
                raise AgentError(409, "wrong_state", self._why_no_resolve())
            now = self.clock()
            if (
                self._last_resolve_at is not None
                and now - self._last_resolve_at < RESOLVE_MIN_GAP_S
            ):
                raise AgentError(
                    429, "too_fast", "Wait a few seconds before resolving again.", 5
                )
            if not self._resolve_lock.acquire(blocking=False):
                raise AgentError(429, "busy", "The checks are already running.", 5)
            self._last_resolve_at = now
            checks = self.runbook["checks"]["success"]
        try:
            results = self.checker.run_all(checks)  # outside the lock
        finally:
            self._resolve_lock.release()

        with self._lock:
            if self.state != "investigating" or self._ending:
                raise AgentError(409, "game_over", "The game changed while checking.")
            t = self.game_time()
            self.resolve_attempts += 1
            failing = [r for r in results if not r.passed]
            if failing:
                return self._resolve_failed(failing, t)
            return self._start_watch(t)

    def _why_no_resolve(self) -> str:
        return {
            "ready": "There is no incident yet.",
            "paged": "Acknowledge the page first.",
            "watching": "Already resolved: the watch is running.",
            "ended": "The game is over.",
        }.get(self.state, "You can't resolve right now.")

    def _resolve_failed(self, failing: list[CheckResult], t: int | None) -> dict:
        penalty = PENALTY if self.wrong_resolves * PENALTY < MAX_PENALTY_TOTAL else 0
        self.wrong_resolves += 1
        self.events.add(
            "resolve_failed",
            {
                "attempt": self.resolve_attempts,
                "failing": [
                    {"check_id": r.check_id, "message": r.message} for r in failing
                ],
                "penalty": penalty,
            },
            t=t,
        )
        messages = [r.message for r in failing]
        self.feed.add(
            "notice", t, level="error", text="Not resolved yet: " + " ".join(messages)
        )
        self.chat.trigger("resolve_failed", t)
        return {
            "result": "failed",
            "attempt": self.resolve_attempts,
            "failing": [{"message": m} for m in messages],  # never the check ids
            "penalty": penalty,
        }

    def _start_watch(self, t: int | None) -> dict:
        watch = self.runbook["checks"]["watch"]
        seconds = watch["seconds"]
        self.state = "watching"
        self._watch_started_at = self.clock()
        self._next_watch_at = self._watch_started_at + watch.get("interval_s", 10)
        self.events.add(
            "resolve_passed",
            {"attempt": self.resolve_attempts, "watch_seconds": seconds},
            t=t,
        )
        self.feed.add(
            "notice",
            t,
            level="info",
            text=f"Checks pass. Watching for {clock_text(seconds)} to make sure the fix holds.",
        )
        self.chat.trigger("resolve_passed", t)
        log.info("resolve passed; watching for %s s", seconds)
        return {
            "result": "passed",
            "attempt": self.resolve_attempts,
            "watch_seconds": seconds,
        }

    def watch_ends_in(self) -> int | None:
        if self.state != "watching" or self._watch_started_at is None:
            return None
        seconds = self.runbook["checks"]["watch"]["seconds"]
        return max(0, int(self._watch_started_at + seconds - self.clock()))

    # ----- background: the watch ---------------------------------------------------

    def watch_step(self) -> None:
        """Called by tick() when a watch check is due."""
        watch = self.runbook["checks"]["watch"]
        success = self.runbook["checks"]["success"]
        ids = watch.get("checks") or [c["id"] for c in success]
        results = self.checker.run_all([c for c in success if c["id"] in ids])
        finished = False
        with self._lock:
            if self.state != "watching" or self._ending:
                return  # the player gave up meanwhile
            failing = [r for r in results if not r.passed]
            if failing:
                self._reopen(failing)
                return
            if self.clock() - self._watch_started_at >= watch["seconds"]:
                finished = True
            else:
                self._next_watch_at = self.clock() + watch.get("interval_s", 10)
        if finished:
            self.finish("resolved", [("watch_passed", {})])

    def _reopen(self, failing: list[CheckResult]) -> None:
        t = self.game_time()
        self.state = "investigating"
        self.severity = "SEV1"
        self.reopened = True
        self._watch_started_at = None
        self.events.add(
            "reopened",
            {
                "severity": "SEV1",
                "failing": [
                    {"check_id": r.check_id, "message": r.message} for r in failing
                ],
            },
            t=t,
        )
        text = "The fix didn't hold. Incident reopened at SEV1: "
        self.feed.add(
            "notice", t, level="error", text=text + " ".join(r.message for r in failing)
        )
        self.chat.trigger("reopened", t)
        log.info("reopened at SEV1")

    # ----- endings -------------------------------------------------------------

    def give_up(self) -> dict:
        with self._lock:
            if (
                self.state not in ("ready", "paged", "investigating", "watching")
                or self._ending
            ):
                raise AgentError(409, "game_over", "The game is already over.")
        self.finish("gave_up", [("gave_up", {})])
        return {"state": "ended", "end_reason": "gave_up"}

    def abandon(self) -> dict:
        """From the gateway (control API): the player has been idle for 5 minutes."""
        with self._lock:
            if self.state == "ended" or self._ending:
                raise AgentError(
                    409, "already_ended", f"Already ended: {self.end_reason}."
                )
        self.finish("abandoned")
        return {"state": "ended", "end_reason": "abandoned"}

    def finish(self, reason: str, before: list[tuple[str, dict]] | None = None) -> None:
        """End the game exactly once: events before, damage checks, then `ended`."""
        with self._lock:
            if self.state == "ended" or self._ending:
                return
            self._ending = True
            self._ended_at = self.clock()  # the game clock stops here
            t = self.game_time()
            for type_, data in before or []:
                self.events.add(type_, data, t=t)
            damage_checks = (
                self.runbook["checks"].get("damage", []) if self.runbook_loaded else []
            )
        damage = self.checker.run_all(damage_checks)  # outside the lock
        with self._lock:
            for result in damage:
                if not result.passed:
                    self.events.add(
                        "damage_found",
                        {"check_id": result.check_id, "message": result.message},
                        t=t,
                    )
            self.events.add(
                "ended",
                {
                    "reason": reason,
                    "final_health": self.health,
                    "severity": self.severity,
                    "reopened": self.reopened,
                },
                t=t,
            )
            self.state = "ended"
            self.end_reason = reason
            self.feed.add("notice", t, level="info", text=END_NOTICES[reason])
            log.info("game ended: %s", reason)
