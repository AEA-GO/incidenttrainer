"""The incident feed: what appears in the player's timeline, in order.

Each item has an increasing `id`; the browser passes the last id it saw as
`feed_after` to get only new items (contract 2, GET /v1/state).
Items here are safe to show: never hidden fields.
"""

from dataclasses import dataclass, field


@dataclass
class Feed:
    items: list[dict] = field(default_factory=list)

    def add(self, kind: str, t: int | None, **fields: object) -> dict:
        item = {"id": len(self.items) + 1, "kind": kind, "t": t, **fields}
        self.items.append(item)
        return item

    def remove_field(self, item_id: int, key: str) -> None:
        """E.g. a chat message's reply buttons, once the player has answered."""
        self.items[item_id - 1].pop(key, None)

    def since(self, after: int = 0, limit: int = 100) -> list[dict]:
        """Copies, so a later change never alters an answer already handed out."""
        return [dict(item) for item in self.items if item["id"] > after][:limit]

    @property
    def cursor(self) -> int:
        return len(self.items)
