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


class Labs(Protocol):
    def card(self, lab_id: str) -> LabCard | None: ...


class S3Labs:
    def __init__(
        self, bucket: str, s3=None, clock: Callable[[], float] = time.monotonic
    ):
        if s3 is None:
            import boto3

            s3 = boto3.client("s3")
        self.bucket, self.s3, self.clock = bucket, s3, clock
        self._cache: dict[str, tuple[float, LabCard | None]] = {}

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

    def card(self, lab_id: str) -> LabCard | None:
        if not LAB_ID.match(lab_id):
            return None
        cached = self._cache.get(lab_id)
        if cached and self.clock() - cached[0] < CACHE_S:
            return cached[1]
        current = self._read(f"labs/{lab_id}/current")
        found = None
        if current is not None:
            version = parse_current(current.decode())
            body = self._read(f"labs/{lab_id}/v{version}/card.json")
            if body is not None:
                found = card_from_json(json.loads(body))
        self._cache[lab_id] = (self.clock(), found)
        return found


class DictLabs:
    """Cards in memory (tests, laptops)."""

    def __init__(self, cards: list[LabCard]):
        self.cards = {card.id: card for card in cards}

    def card(self, lab_id: str) -> LabCard | None:
        return self.cards.get(lab_id)
