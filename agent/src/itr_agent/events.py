"""The game diary (docs/contracts/events.md). Append only, in memory.

The gateway copies new events into DynamoDB every ~10 s through /control/v1/report.
Hidden fields (reply tones, risky flags...) are stored here like any other field;
the player API is responsible for never sending them before the end.
"""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Event:
    seq: int
    at: str
    t: int | None
    type: str
    data: dict

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "at": self.at,
            "t": self.t,
            "by": "agent",
            "type": self.type,
            "data": self.data,
        }


def now_utc() -> str:
    """RFC 3339 UTC with milliseconds, e.g. 2026-12-08T18:03:12.345Z."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventLog:
    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = threading.Lock()

    def add(self, type_: str, data: dict | None = None, t: int | None = None) -> Event:
        with self._lock:
            event = Event(len(self._events) + 1, now_utc(), t, type_, data or {})
            self._events.append(event)
            return event

    def since(self, after_seq: int = 0, limit: int = 500) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.seq > after_seq][:limit]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return len(self._events)
