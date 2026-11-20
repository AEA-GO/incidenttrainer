"""Who may go where (contract 5): the path, the cookie, the game's state.

`decide()` runs the steps of "Checking a cookie" and "Routing by state" in order and
returns either a Forward (nginx proxies to the game server) or a BootAnswer (gateway-auth
answers `GET .../api/v1/state` itself while the game boots). Every refusal is a
GatewayError with the contract's status and code.
"""

import ipaddress
import re
from dataclasses import dataclass

from itr_gateway import cookie
from itr_gateway.errors import (
    BAD_ORIGIN,
    BOOTING,
    ENDED,
    NO_TERMINAL,
    NOT_FOUND,
    UNREACHABLE,
    WRONG_GAME,
    gateway_error,
)
from itr_gateway.store import PLAYING, Game, Store

PATH = re.compile(r"^/s/([A-Za-z0-9_-]{22})/(api/v1/|grafana/|term/)([^?#]*)")
PORTS = {"api": 8080, "grafana": 3000, "term": 7681}


@dataclass(frozen=True)
class Target:
    game_id: str
    kind: str  # api | grafana | term
    rest: str  # the part after the kind, e.g. "state" or "ws"


@dataclass(frozen=True)
class Forward:
    game: Game
    kind: str
    upstream: str  # the game server's private IP

    @property
    def port(self) -> int:
        return PORTS[self.kind]


@dataclass(frozen=True)
class BootAnswer:
    game: Game


def parse(uri: str) -> Target:
    """Step 1: the path must be one of ours, else 404 not_found."""
    match = PATH.match(uri or "")
    if not match:
        raise gateway_error(NOT_FOUND)
    game_id, kind, rest = match.groups()
    return Target(game_id=game_id, kind=kind.split("/")[0], rest=rest)


def in_sandbox(ip: str, networks: list[ipaddress.IPv4Network]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


@dataclass
class Router:
    store: Store
    cookie_name: str
    public_origin: str | None
    sandbox: list[ipaddress.IPv4Network]

    def decide(
        self,
        uri: str,
        method: str,
        cookie_header: str | None,
        origin: str | None,
        now: float,
    ) -> Forward | BootAnswer:
        target = parse(uri)
        value = cookie.from_header(cookie_header, self.cookie_name)
        game_cookie = cookie.check(value, self.store.key, now)  # steps 2-4
        if game_cookie.game_id != target.game_id:  # step 5
            raise gateway_error(WRONG_GAME)
        game = self.store.game(target.game_id)  # step 6
        if game is None:
            raise gateway_error(NOT_FOUND)
        if (
            target.kind == "term"
            and target.rest == "ws"
            and (not self.public_origin or origin != self.public_origin)
        ):
            raise gateway_error(BAD_ORIGIN)  # against cross-site WebSocket hijacking
        return self._by_state(game, target, method, now)  # step 7

    def _by_state(
        self, game: Game, target: Target, method: str, now: float
    ) -> Forward | BootAnswer:
        if game.state == "booting":
            if target.kind == "api" and target.rest == "state" and method == "GET":
                return BootAnswer(game)
            raise gateway_error(BOOTING, retry_after_s=2)
        if game.state == "ended":
            if game.server_gone(now) or target.kind == "term":
                raise gateway_error(ENDED)
        elif (
            game.state not in PLAYING
        ):  # pragma: no cover - store rejects unknown states
            raise gateway_error(NOT_FOUND)
        if target.kind == "term" and not game.terminal:
            raise gateway_error(NO_TERMINAL)
        if not game.private_ip or not in_sandbox(game.private_ip, self.sandbox):
            raise gateway_error(UNREACHABLE)  # never forward outside the sandbox subnet
        return Forward(game=game, kind=target.kind, upstream=game.private_ip)
