"""The real nginx config + gateway-auth together, as a browser sees them.

Starts nginx (from gateway/nginx/, ports and paths rewritten for a test folder),
gateway-auth (laptop store) and fake game-server ports on 127.0.0.5. Skipped where
nginx isn't installed (GitHub's ubuntu runners have it).
"""

import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn
from conftest import KEY, KEYS
from itr_gateway.app import Settings, build_app
from itr_gateway.cookie import sign
from itr_gateway.store import FileStore

NGINX = shutil.which("nginx") or (
    "/usr/sbin/nginx" if Path("/usr/sbin/nginx").exists() else None
)
pytestmark = pytest.mark.skipif(NGINX is None, reason="nginx is not installed")

CONF = Path(__file__).resolve().parents[1] / "nginx"
SERVER_IP = "127.0.0.5"  # stands in for a game server in the sandbox subnet
ORIGIN = "https://drer7le45ultl.cloudfront.net"
PLAYING = "q3V9xYk2Lm8Ta1BcD4eF0g"
BOOTING = "Bb9xYk2Lm8Ta1BcD4eF0g_"
ENDED = "Ee9xYk2Lm8Ta1BcD4eF0g_"
SILENT = "Dd9xYk2Lm8Ta1BcD4eF0g_"  # in the sandbox range, nothing listening


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Echo(BaseHTTPRequestHandler):
    """A game-server port that tells the test what reached it."""

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.dumps(
            {
                "port": self.server.server_port,
                "method": self.command,
                "path": self.path,
                "game": self.headers.get("X-Itr-Game-Id"),
                "request_id": self.headers.get("X-Request-Id"),
                "cookie": self.headers.get("Cookie"),
                "authorization": self.headers.get("Authorization"),
                "body": self.rfile.read(length).decode() if length else "",
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *args) -> None:
        pass


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    folder = tmp_path_factory.mktemp("gateway")
    servers = []
    for port in (8080, 3000, 7681):
        server = ThreadingHTTPServer((SERVER_IP, port), Echo)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)

    games = folder / "games.json"
    far = int(time.time()) + 3600

    def meta(state: str, **extra) -> dict:
        return (
            {
                "state": state,
                "private_ip": SERVER_IP,
                "instance_id": "i-1",
                "terminal": True,
            }
            | {
                "created_at": int(time.time()) - 30,
                "expires_at_server": far,
            }
            | extra
        )

    games.write_text(
        json.dumps(
            {
                "games": {
                    PLAYING: meta("investigating"),
                    BOOTING: meta("booting", private_ip=None),
                    ENDED: meta("ended", expires_at_server=1),
                    SILENT: meta("investigating", private_ip="127.0.0.6"),
                }
            }
        )
    )
    store = FileStore(path=games, keys_json=json.dumps(KEYS))
    app = build_app(
        store, Settings(public_origin=ORIGIN, sandbox_cidrs=["127.0.0.0/8"])
    )
    auth_port = free_port()
    auth = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=auth_port, log_level="warning")
    )
    threading.Thread(target=auth.run, daemon=True).start()

    web_port = free_port()
    for name in ("itr-auth.conf", "itr-proxy-headers.conf"):
        shutil.copy(CONF / name, folder / name)
    site = (CONF / "itr-gateway.conf").read_text()
    site = site.replace("/etc/nginx/", f"{folder}/")
    site = site.replace("/var/log/nginx/", f"{folder}/")
    site = site.replace(
        "listen 80 default_server", f"listen 127.0.0.1:{web_port} default_server"
    )
    site = site.replace("server 127.0.0.1:9000", f"server 127.0.0.1:{auth_port}")
    (folder / "site.conf").write_text(site)
    (folder / "nginx.conf").write_text(
        f"worker_processes 1;\npid {folder}/nginx.pid;\nerror_log {folder}/error.log;\n"
        "events { worker_connections 64; }\n"
        f"http {{ client_body_temp_path {folder}/body; proxy_temp_path {folder}/proxy;\n"
        f"  fastcgi_temp_path {folder}/fcgi; uwsgi_temp_path {folder}/uwsgi; scgi_temp_path {folder}/scgi;\n"
        f"  include {folder}/site.conf; }}\n"
    )
    subprocess.run(
        [NGINX, "-t", "-p", str(folder), "-c", str(folder / "nginx.conf")],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [NGINX, "-p", str(folder), "-c", str(folder / "nginx.conf")], check=True
    )
    for _ in range(50):
        if auth.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{web_port}"
    subprocess.run(
        [NGINX, "-p", str(folder), "-c", str(folder / "nginx.conf"), "-s", "stop"],
        check=False,
    )
    auth.should_exit = True
    for server in servers:
        server.shutdown()


def call(
    url: str,
    game: str | None = None,
    method: str = "GET",
    data: bytes | None = None,
    **headers,
):
    if game:
        cookie = sign(KEY, game, int(time.time()) + 600, "k1")
        headers.setdefault("Cookie", f"__Secure-itr_game={cookie}")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return (
                reply.status,
                reply.headers,
                json.loads(reply.read() or b"null"),
            )
    except urllib.error.HTTPError as error:
        return error.code, error.headers, json.loads(error.read() or b"null")


def test_agent_paths_are_rewritten_and_scrubbed(gateway) -> None:
    status, headers, body = call(
        f"{gateway}/s/{PLAYING}/api/v1/feed?after=3",
        PLAYING,
        Authorization="Bearer x",
        **{"X-Itr-Game-Id": "spoofed", "X-Itr-Upstream": "6.6.6.6"},
    )
    assert status == 200
    assert body["port"] == 8080
    assert body["path"] == "/v1/feed?after=3"
    assert body["game"] == PLAYING
    assert body["request_id"]
    assert body["cookie"] is None and body["authorization"] is None
    assert headers["Cache-Control"] == "no-store"


def test_post_bodies_reach_the_agent(gateway) -> None:
    status, _, body = call(
        f"{gateway}/s/{PLAYING}/api/v1/actions",
        PLAYING,
        method="POST",
        data=b'{"verb":"restart"}',
        **{"Content-Type": "application/json"},
    )
    assert status == 200
    assert (body["method"], body["body"]) == ("POST", '{"verb":"restart"}')


def test_grafana_keeps_its_path_and_the_terminal_is_stripped(gateway) -> None:
    _, _, grafana = call(
        f"{gateway}/s/{PLAYING}/grafana/d/shop-overview?kiosk", PLAYING
    )
    assert (grafana["port"], grafana["path"]) == (
        3000,
        f"/s/{PLAYING}/grafana/d/shop-overview?kiosk",
    )
    _, _, term = call(f"{gateway}/s/{PLAYING}/term/token", PLAYING)
    assert (term["port"], term["path"]) == (7681, "/token")
    status, _, _ = call(f"{gateway}/s/{PLAYING}/term/ws", PLAYING, Origin=ORIGIN)
    assert status == 200
    status, _, body = call(
        f"{gateway}/s/{PLAYING}/term/ws", PLAYING, Origin="https://evil.example"
    )
    assert (status, body["error"]["code"]) == (403, "bad_origin")


@pytest.mark.parametrize(
    ("path", "game", "status", "code"),
    [
        (f"/s/{PLAYING}/api/v1/state", None, 401, "bad_cookie"),
        (f"/s/{PLAYING}/api/v1/state", BOOTING, 403, "wrong_game"),
        (f"/s/{PLAYING}/control/v1/report", PLAYING, 404, "not_found"),
        ("/", None, 404, "not_found"),
        (f"/s/{ENDED}/api/v1/state", ENDED, 410, "ended"),
        (f"/s/{SILENT}/api/v1/state", SILENT, 502, "server_unreachable"),
        (f"/s/{BOOTING}/grafana/", BOOTING, 503, "booting"),
    ],
)
def test_refusals_are_json(gateway, path, game, status, code) -> None:
    got_status, _, body = call(f"{gateway}{path}", game)
    assert (got_status, body["error"]["code"]) == (status, code)


def test_the_boot_screen_is_answered_while_booting(gateway) -> None:
    status, headers, body = call(f"{gateway}/s/{BOOTING}/api/v1/state", BOOTING)
    assert status == 200
    assert body["state"] == "booting"
    assert body["boot"]["server_started"] is True
    assert headers["Cache-Control"] == "no-store"
    status, headers, body = call(
        f"{gateway}/s/{BOOTING}/api/v1/ack", BOOTING, method="POST", data=b"{}"
    )
    assert (status, body["error"]["code"], headers["Retry-After"]) == (
        503,
        "booting",
        "2",
    )


def test_dot_dot_cannot_jump_to_another_part(gateway) -> None:
    # nginx normalises the path before gateway-auth checks it, so the check and the
    # forwarding always agree (here: an ended game's terminal stays closed).
    for trick in (
        f"/s/{ENDED}/api/v1/../../term/token",
        f"/s/{PLAYING}/api/v1/../../control/v1/report",
    ):
        sock = socket.create_connection(
            ("127.0.0.1", int(gateway.rsplit(":", 1)[1])), timeout=5
        )
        cookie = sign(KEY, trick.split("/")[2], int(time.time()) + 600, "k1")
        sock.sendall(
            f"GET {trick} HTTP/1.1\r\nHost: x\r\nCookie: __Secure-itr_game={cookie}\r\n\r\n".encode()
        )
        first_line = sock.recv(200).split(b"\r\n", 1)[0]
        sock.close()
        assert first_line.split()[1] in (b"404", b"410"), (trick, first_line)


def test_limits(gateway) -> None:
    status, _, body = call(
        f"{gateway}/s/{PLAYING}/api/v1/actions",
        PLAYING,
        method="POST",
        data=b"a" * 70_000,
    )
    assert (status, body["error"]["code"]) == (413, "too_big")
    codes = [call(f"{gateway}/s/{PLAYING}/api/v1/state", PLAYING)[0] for _ in range(40)]
    assert codes.count(429) >= 15  # 10/s, burst 20
    status, headers, body = call(f"{gateway}/s/{PLAYING}/api/v1/state", PLAYING)
    if status == 429:
        assert (body["error"]["code"], headers["Retry-After"]) == ("too_fast", "1")
