"""Chat with Dana (product) and Sam (support), scripted by the lab (contract 1, `chat`).

- Scripted messages arrive at a game time (`when: {at: 60}`) or after something
  happened (`when: {trigger: acknowledged, delay_s: 20}`); the `action` trigger can
  be narrowed with `match: {verb, target}`. Each message is sent at most once.
- A message may offer 2-3 replies. The player picks one, once. Its `tone`
  (honest / overconfident / deflecting) goes into the diary and stays hidden
  until the end. The same character answers right away with the `reaction`.
- Chasing: a minute after a promised update time passes without an update, Dana
  asks for news (`update_overdue` + a chase message). When nobody has posted a
  status update for `silence.after_s`, Sam asks again, and again.

Status updates (step 9) report themselves with `status_posted`.
Every method is called with the agent's lock held.
"""

import logging

from itr_agent.clocks import clock_text, scene_clock
from itr_agent.errors import AgentError
from itr_agent.events import EventLog
from itr_agent.feed import Feed
from itr_agent.world import World

CHARACTERS = ("dana", "sam")
TONES = ("honest", "overconfident", "deflecting")
TRIGGERS = (
    "acknowledged",
    "first_status_update",
    "status_update",
    "action",
    "hint_used",
    "resolve_failed",
    "resolve_passed",
    "reopened",
)
OVERDUE_GRACE_S = 60  # the same minute of grace scoring gives (score.md, promises)
log = logging.getLogger(__name__)


class Chat:
    def __init__(self, spec: dict, events: EventLog, feed: Feed, starts_at: str):
        self.events = events
        self.feed = feed
        self.starts_at = starts_at
        self.chase = spec["chase"]
        self._messages = {m["id"]: m for m in spec["messages"]}
        self._order = {m["id"]: n for n, m in enumerate(spec["messages"])}
        # Message id -> game time it is due. Timed messages are due from the start;
        # triggered ones are added when their trigger happens.
        self._due = {
            m["id"]: m["when"]["at"] for m in spec["messages"] if "at" in m["when"]
        }
        self._sent: set[str] = set()
        self._answered: set[str] = set()
        self._feed_ids: dict[str, int] = {}
        self._chases = 0
        self._last_chase_t = 0
        self._overdue_chased: int | None = None
        # What the status updates said (step 9)
        self.last_update_t: int | None = None
        self.promised_t: int | None = None

    # ----- things that happened in the game ----------------------------------------

    def trigger(
        self,
        name: str,
        t: int | None,
        verb: str | None = None,
        target: str | None = None,
    ) -> None:
        """Something happened: schedule the messages waiting for it."""
        for message_id, message in self._messages.items():
            when = message["when"]
            if when.get("trigger") != name or message_id in self._due:
                continue
            match = when.get("match", {})
            if match.get("verb", verb) != verb or match.get("target", target) != target:
                continue
            # Sent by the loop (every ~2 s), so a delay of 0 means "in a moment".
            self._due[message_id] = (t or 0) + when.get("delay_s", 0)

    def status_posted(self, t: int, next_update_t: int | None) -> None:
        """A status update was posted (step 9): resets silence, replaces the promise."""
        first = self.last_update_t is None
        self.last_update_t = t
        self.promised_t = next_update_t
        if first:
            self.trigger("first_status_update", t)
        self.trigger("status_update", t)

    # ----- the background loop ---------------------------------------------------

    def deliver(self, t: int | None) -> None:
        """Send everything that is due by game time t, then chase if needed."""
        if t is None:
            return  # nobody writes before the page
        due = sorted(
            (at, self._order[message_id], message_id)
            for message_id, at in self._due.items()
            if at <= t and message_id not in self._sent
        )
        for _, _, message_id in due:
            message = self._messages[message_id]
            self._sent.add(message_id)
            self._send(
                message_id,
                message["from"],
                message["text"],
                "scripted",
                t,
                replies=message.get("replies"),
            )
        self._maybe_chase(t)

    def _maybe_chase(self, t: int) -> None:
        promised = self.promised_t
        if (
            promised is not None
            and promised != self._overdue_chased
            and t >= promised + OVERDUE_GRACE_S
        ):
            self._overdue_chased = promised
            self.events.add("update_overdue", {"due_t": promised}, t=t)
            self._send_chase(self.chase["overdue"], t, promised)
        silence = self.chase["silence"]
        quiet_since = max(self.last_update_t or 0, self._last_chase_t)
        if t - quiet_since >= silence["after_s"]:
            self._send_chase(silence, t)

    def _send_chase(self, spec: dict, t: int, promised: int | None = None) -> None:
        self._chases += 1
        self._last_chase_t = t  # any chase restarts the silence timer
        self._send(
            f"chase-{self._chases}", spec["from"], spec["text"], "chase", t, promised
        )

    # ----- the player answers ---------------------------------------------------

    def reply(self, message_id: str, option_id: str, t: int | None) -> None:
        """The player picks one of a message's replies (once)."""
        message = self._messages.get(message_id)
        if (
            message is None
            or message_id not in self._sent
            or not message.get("replies")
        ):
            raise AgentError(
                404, "unknown_message", "There is no such message to answer."
            )
        if message_id in self._answered:
            raise AgentError(
                409, "already_answered", "You already answered this message."
            )
        option = next((r for r in message["replies"] if r["id"] == option_id), None)
        if option is None:
            raise AgentError(404, "unknown_message", "That isn't one of the replies.")

        self._answered.add(message_id)
        self.events.add(
            "chat_reply",
            {
                "message_id": message_id,
                "option_id": option_id,
                "text": option["text"],
                "tone": option["tone"],  # hidden until the end
            },
            t=t,
        )
        self.feed.remove_field(self._feed_ids[message_id], "replies")
        self.feed.add(
            "chat_reply",
            t,
            message_id=message_id,
            option_id=option_id,
            text=option["text"],
            **{"from": "you"},
        )
        if option.get("reaction"):
            self._send(
                f"{message_id}-r", message["from"], option["reaction"], "reaction", t
            )

    # ----- writing a message -------------------------------------------------------

    def _send(
        self,
        message_id: str,
        sender: str,
        text: str,
        kind: str,
        t: int | None,
        promised: int | None = None,
        replies: list[dict] | None = None,
    ) -> None:
        text = self._fill(text, t or 0, promised)
        self.events.add(
            "chat_message",
            {"message_id": message_id, "from": sender, "text": text, "kind": kind},
            t=t,
        )
        shown: dict = {"message_id": message_id, "from": sender, "text": text}
        if replies:
            # Only id and text: the tone stays hidden.
            shown["replies"] = [{"id": r["id"], "text": r["text"]} for r in replies]
        item = self.feed.add("chat_message", t, **shown)
        self._feed_ids[message_id] = item["id"]
        log.info("chat %s from %s (%s)", message_id, sender, kind)

    def _fill(self, text: str, t: int, promised: int | None) -> str:
        """The placeholders contract 1 allows in chat: {t} and {promised}."""
        text = text.replace("{t}", clock_text(t))
        if promised is not None:
            text = text.replace("{promised}", scene_clock(self.starts_at, promised))
        return text


# ----- checking a runbook's chat ------------------------------------------------


def problems_with_chat(chat: object, world: World) -> list[str]:
    """What would stop this chat from working. Empty list = fine."""
    if not isinstance(chat, dict) or not isinstance(chat.get("messages"), list):
        return ["chat needs a list of messages"]
    problems: list[str] = []
    seen: set[str] = set()
    for message in chat["messages"]:
        message_id = message.get("id") if isinstance(message, dict) else None
        if not isinstance(message_id, str) or not message_id:
            problems.append("a chat message has no id")
            continue
        name = f"chat message {message_id!r}"
        if message_id in seen:
            problems.append(f"{name} appears twice")
        seen.add(message_id)
        if message_id.startswith("chase-") or message_id.endswith("-r"):
            problems.append(
                f"{name}: ids starting 'chase-' or ending '-r' are reserved"
            )
        if message.get("from") not in CHARACTERS:
            problems.append(f"{name}: unknown sender {message.get('from')!r}")
        if not isinstance(message.get("text"), str):
            problems.append(f"{name} has no text")
        problems += _when_problems(name, message.get("when"), world)
        problems += _reply_problems(name, message.get("replies"))

    chase = chat.get("chase")
    for kind in ("overdue", "silence"):
        spec = chase.get(kind) if isinstance(chase, dict) else None
        if (
            not isinstance(spec, dict)
            or spec.get("from") not in CHARACTERS
            or not isinstance(spec.get("text"), str)
        ):
            problems.append(f"chat chase {kind!r} needs a sender and a text")
        elif kind == "silence" and not (
            isinstance(spec.get("after_s"), int) and spec["after_s"] >= 60
        ):
            problems.append("chat chase 'silence' needs after_s of at least 60")
    return problems


def _when_problems(name: str, when: object, world: World) -> list[str]:
    if not isinstance(when, dict):
        return [f"{name} has no 'when'"]
    if "at" in when:
        at = when["at"]
        return [] if isinstance(at, int) and at >= 0 else [f"{name}: bad 'at'"]
    trigger = when.get("trigger")
    if trigger not in TRIGGERS:
        return [f"{name}: unknown trigger {trigger!r}"]
    problems = []
    delay = when.get("delay_s", 0)
    if not isinstance(delay, int) or delay < 0:
        problems.append(f"{name}: bad delay_s")
    match = when.get("match", {})
    if match and trigger != "action":
        problems.append(f"{name}: 'match' only works with the action trigger")
    target = match.get("target") if isinstance(match, dict) else None
    if target is not None and target not in world.services:
        problems.append(f"{name}: match uses unknown service {target!r}")
    return problems


def _reply_problems(name: str, replies: object) -> list[str]:
    if replies is None:
        return []
    if not isinstance(replies, list) or not 2 <= len(replies) <= 3:
        return [f"{name} needs 2-3 replies"]
    if not all(isinstance(r, dict) for r in replies):
        return [f"{name}: every reply needs an id, a tone and a text"]
    problems = []
    ids = [r.get("id") for r in replies]
    if len(set(ids)) != len(ids):
        problems.append(f"{name}: reply ids must be different")
    for reply in replies:
        if reply.get("tone") not in TONES:
            problems.append(f"{name}: unknown tone {reply.get('tone')!r}")
        if not isinstance(reply.get("text"), str):
            problems.append(f"{name}: a reply has no text")
    if not any(r.get("tone") == "honest" for r in replies):
        problems.append(f"{name}: at least one reply must be honest")
    return problems
