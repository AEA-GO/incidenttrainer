import json

import pytest
from fastapi.testclient import TestClient
from itr_gateway.app import Settings, build_app
from itr_gateway.cookie import b64url, sign
from itr_gateway.store import FileStore

# Contract 5 test vector: key bytes 0..31, game id, kid k1
KEY = bytes(range(32))
KEY_B64 = b64url(KEY)  # as in the contract; test_cookie checks the text
GAME_ID = "q3V9xYk2Lm8Ta1BcD4eF0g"
OTHER_ID = "Zz9xYk2Lm8Ta1BcD4eF0g_"
NOW = 1796753000.0  # 2026-12-08T18:03:20Z, before the vector's exp
EXP = 1796755500
ORIGIN = "https://drer7le45ultl.cloudfront.net"
KEYS = {
    "current": "k1",
    "keys": {"k1": {"secret": KEY_B64, "created_at": "2026-11-19T00:00:00Z"}},
}


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def meta(state: str = "investigating", **extra) -> dict:
    item = {
        "state": state,
        "private_ip": "10.20.3.17",
        "instance_id": "i-0abc",
        "terminal": False,
        "created_at": "2026-12-08T18:00:00Z",
        "expires_at_server": EXP,
    }
    item.update(extra)
    return item


class World:
    """A laptop-style FileStore + the app, with helpers that act like nginx."""

    def __init__(self, tmp_path, clock: Clock) -> None:
        self.file = tmp_path / "games.json"
        self.games: dict[str, dict] = {}
        self.clock = clock
        self.save()
        self.store = FileStore(path=self.file, keys_json=json.dumps(KEYS), clock=clock)
        self.app = build_app(self.store, Settings(public_origin=ORIGIN), clock=clock)
        self.client = TestClient(self.app)

    def save(self) -> None:
        self.file.write_text(json.dumps({"games": self.games}))

    def put(self, game_id: str = GAME_ID, **item) -> None:
        self.games[game_id] = meta(**item)
        self.save()
        # a test may write twice in the same mtime tick: make the change visible
        self.store._mtime = -1.0

    def cookie(
        self, game_id: str = GAME_ID, exp: int = EXP, kid: str = "k1", key: bytes = KEY
    ) -> str:
        return f"__Secure-itr_game={sign(key, game_id, exp, kid)}"

    def auth(
        self,
        path: str,
        cookie: str | None = "default",
        method: str = "GET",
        origin: str | None = None,
    ):
        headers = {
            "X-Original-URI": path,
            "X-Original-Method": method,
            "X-Request-Id": "r1",
        }
        if cookie == "default":
            cookie = self.cookie()
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        return self.client.get("/auth", headers=headers)

    def browser(self, path: str, **kwargs):
        """What the browser gets: /auth, then /deny on a refusal (like nginx's error_page)."""
        first = self.auth(path, **kwargs)
        if first.status_code == 204:
            return first
        headers = {"X-Original-URI": path}
        for name in ("X-Itr-Status", "X-Itr-Code", "X-Itr-Retry"):
            if name in first.headers:
                headers[name] = first.headers[name]
        return self.client.get("/deny", headers=headers)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def world(tmp_path, clock) -> World:
    return World(tmp_path, clock)
