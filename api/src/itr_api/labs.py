"""Lab cards from the private lab bucket (contract 1): labs/<lab>/current + labs/<lab>/v<n>/card.json.

Only card.json is read here: public card + scoring. The runbook is the gateway's; the
debrief is read only after a game has ended (step 4).
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

LAB_ID = re.compile(r"^[a-z0-9-]{1,40}$")
CACHE_S = 60


@dataclass(frozen=True)
class LabCard:
    id: str
    version: int
    world: str
    card: dict  # title, hook, level, minutes, skills, tools, handover_note
    scoring: dict  # target_time_s, optional speed_zero_s, health_full
    course: dict | None = None

    @property
    def level(self) -> str:
        return str(self.card.get("level", "intern"))

    @property
    def terminal(self) -> bool:
        return bool(self.card.get("terminal", self.level == "junior"))  # contract 1


def card_from_json(data: dict) -> LabCard:
    for field in ("id", "version", "world", "card", "scoring"):
        if field not in data:
            raise ValueError(f"card.json has no {field}")
    return LabCard(
        id=str(data["id"]),
        version=int(data["version"]),
        world=str(data["world"]),
        card=dict(data["card"]),
        scoring=dict(data["scoring"]),
        course=data.get("course"),
    )


def parse_current(text: str) -> int:
    """`labs/<lab>/current` holds `v<version>` (contract 1); a bare number is accepted too."""
    return int(text.strip().removeprefix("v"))


def course_labs(data) -> list[str]:
    """`course/<id>.json` lists lab ids in order: a list, or {"labs": [...]}."""
    labs = data.get("labs", []) if isinstance(data, dict) else data
    return [str(lab) for lab in labs]


def course_doc(course_id: str, data) -> dict:
    """The course file as {id, title, summary, labs} (a bare list gets the id as title)."""
    info = data if isinstance(data, dict) else {}
    return {
        "id": course_id,
        "title": str(info.get("title", course_id)),
        "summary": str(info.get("summary", "")),
        "labs": course_labs(data),
    }


def lab_view(card: LabCard) -> dict:
    """The public LabCard of contract 3 (never anything from the runbook or debrief)."""
    c = card.card
    return {
        "id": card.id,
        "order": int((card.course or {}).get("order", 0)),
        "title": c.get("title", ""),
        "hook": c.get("hook", ""),
        "level": card.level,
        "minutes": int(c.get("minutes", 0)),
        "skills": list(c.get("skills", [])),
        "tools": list(c.get("tools", [])),
        "terminal": card.terminal,
        "handover_note": c.get("handover_note", ""),
    }


class Labs(Protocol):
    def card(self, lab_id: str) -> LabCard | None: ...
    def card_at(self, lab_id: str, version: int) -> LabCard | None: ...
    def debrief(self, lab_id: str, version: int) -> dict | None: ...
    def course(self, course_id: str) -> list[str] | None: ...
    def course_info(self, course_id: str) -> dict | None: ...


class S3Labs:
    """Reads are cached per Lambda container: `current` and courses for a minute, versioned
    files longer (a published version never changes, contract 1)."""

    VERSIONED_CACHE_S = 600

    def __init__(
        self, bucket: str, s3=None, clock: Callable[[], float] = time.monotonic
    ):
        if s3 is None:
            import boto3

            s3 = boto3.client("s3")
        self.bucket, self.s3, self.clock = bucket, s3, clock
        self._cache: dict[str, tuple[float, object]] = {}

    def _read(self, key: str) -> bytes | None:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
            if code in (
                "NoSuchKey",
                "404",
                "AccessDenied",
            ):  # no ListBucket: missing = denied
                return None
            raise

    def _cached(self, key: str, ttl: float, parse: Callable[[bytes], object]) -> object:
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < ttl:
            return hit[1]
        body = self._read(key)
        value = parse(body) if body is not None else None
        self._cache[key] = (self.clock(), value)
        return value

    def card(self, lab_id: str) -> LabCard | None:
        if not LAB_ID.match(lab_id):
            return None
        version = self._cached(
            f"labs/{lab_id}/current", CACHE_S, lambda b: parse_current(b.decode())
        )
        return None if version is None else self.card_at(lab_id, int(version))

    def card_at(self, lab_id: str, version: int) -> LabCard | None:
        if not LAB_ID.match(lab_id):
            return None
        key = f"labs/{lab_id}/v{int(version)}/card.json"
        return self._cached(
            key, self.VERSIONED_CACHE_S, lambda b: card_from_json(json.loads(b))
        )

    def debrief(self, lab_id: str, version: int) -> dict | None:
        """Only for games that have ended (the caller checks)."""
        if not LAB_ID.match(lab_id):
            return None
        key = f"labs/{lab_id}/v{int(version)}/debrief.json"
        return self._cached(
            key,
            self.VERSIONED_CACHE_S,
            lambda b: dict(json.loads(b).get("debrief", {})),
        )

    def course(self, course_id: str) -> list[str] | None:
        info = self.course_info(course_id)
        return info["labs"] if info else None

    def course_info(self, course_id: str) -> dict | None:
        if not LAB_ID.match(course_id):
            return None
        return self._cached(
            f"course/{course_id}.json",
            CACHE_S,
            lambda b: course_doc(course_id, json.loads(b)),
        )


class DictLabs:
    """Labs in memory (tests, laptops)."""

    def __init__(
        self,
        cards: list[LabCard],
        debriefs: dict | None = None,
        courses: dict | None = None,
    ):
        self.cards = {card.id: card for card in cards}
        self.debriefs = debriefs or {}  # (lab_id, version) -> debrief
        self.courses = courses or {}  # course_id -> [lab ids] or {title, summary, labs}

    def card(self, lab_id: str) -> LabCard | None:
        return self.cards.get(lab_id)

    def card_at(self, lab_id: str, version: int) -> LabCard | None:
        card = self.cards.get(lab_id)
        return card if card and card.version == version else None

    def debrief(self, lab_id: str, version: int) -> dict | None:
        return self.debriefs.get((lab_id, version))

    def course(self, course_id: str) -> list[str] | None:
        info = self.course_info(course_id)
        return info["labs"] if info else None

    def course_info(self, course_id: str) -> dict | None:
        data = self.courses.get(course_id)
        return None if data is None else course_doc(course_id, data)
