"""Play a game against a local agent, from the terminal. Python standard library only.

Plays both roles the browser and the gateway will have later:

    python3 dev/play.py start              # gateway: claim the agent, load the dev lab
    python3 dev/play.py state              # what the incident screen would show
    python3 dev/play.py ack
    python3 dev/play.py press show-catalog-config
    python3 dev/play.py press catalog-flags flag=SEARCH_V2 value=false
    python3 dev/play.py resolve
    python3 dev/play.py give-up
    python3 dev/play.py feed               # the timeline: button results, notices
    python3 dev/play.py diary              # the game diary (what scoring will read)
    python3 dev/play.py auto               # play a whole game like an expert

`start --watch 30` and `auto --watch 30` shorten the watch (normally 4:00) for demos.
The agent forgets everything when restarted: restart it before each new game.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HOST = os.environ.get("ITR_AGENT_HOST", "127.0.0.1")
PLAYER = f"http://{HOST}:{os.environ.get('ITR_PLAYER_PORT', '8080')}"
CONTROL = f"http://{HOST}:{os.environ.get('ITR_CONTROL_PORT', '8081')}"
GAME_ID = (
    "q3V9xYk2Lm8Ta1BcD4eF0g"  # dev values only; the real gateway makes random ones
)
TOKEN = "t" * 43
PUSH = Path(__file__).resolve().parent / "labs" / "dev-lab.push.json"
STARTED = time.monotonic()
TIMESTAMPS = False  # only `auto` prints elapsed time


# ----- HTTP ---------------------------------------------------------------------


def call(method: str, url: str, body: dict | None = None, headers: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else {}
    except urllib.error.URLError as error:
        sys.exit(f"Can't reach the agent at {url} ({error.reason}). Is it running?")


def player(method: str, path: str, body: dict | None = None, **headers: str):
    return call(method, PLAYER + path, body, {"X-Itr-Game-Id": GAME_ID, **headers})


def control(method: str, path: str, body: dict | None = None):
    return call(method, CONTROL + path, body, {"Authorization": f"Bearer {TOKEN}"})


def error_text(body: dict) -> str:
    error = body.get("error", {})
    return f"{error.get('code')}: {error.get('message')}"


def say(text: str) -> None:
    if not TIMESTAMPS:
        print(text)
        return
    elapsed = int(time.monotonic() - STARTED)
    print(f"[{elapsed // 60:02d}:{elapsed % 60:02d}] {text}")


# ----- commands ------------------------------------------------------------------


def start(watch: int | None = None) -> None:
    _, ready = call("GET", CONTROL + "/control/v1/ready")
    if ready.get("claimed"):
        sys.exit(
            "This agent is already claimed. Restart it (Ctrl+C, then start it again)."
        )
    status, body = call(
        "POST",
        CONTROL + "/control/v1/claim",
        {
            "game_id": GAME_ID,
            "token": TOKEN,
            "public_base_url": "http://localhost:8000",
        },
    )
    if status != 204:
        sys.exit(f"claim failed: {error_text(body)}")
    push = json.loads(PUSH.read_text())
    if watch:
        push["runbook"]["checks"]["watch"]["seconds"] = watch
    status, body = control("PUT", "/control/v1/runbook", push)
    if status != 202:
        sys.exit(f"runbook refused: {error_text(body)}")
    say("Game started. The agent has broken the shop; wait for the page.")


def bar(value: int) -> str:
    filled = round(value / 10)
    return "#" * filled + "-" * (10 - filled)


def show_state() -> dict:
    status, s = player("GET", "/v1/state")
    if status != 200:
        print(error_text(s))
        return s
    clock = s["clock"]
    head = f"{s['state'].upper()}  {s['severity']}"
    if clock["t"] is not None:
        left = clock["time_left_s"]
        head += f"  t={clock['t']}s  in-world {clock['scene_time']}  left {left // 60}:{left % 60:02d}"
    if s.get("end_reason"):
        head += f"  ({s['end_reason']})"
    print(head)
    health = s["health"]
    print(
        f"  health   [{bar(health['value'])}] {health['value']}%  ({health['failed_checkouts']} failed checkouts)"
    )
    print(
        f"  page     {s['page']['title'] or '-'}{'  (acknowledged)' if s['page']['acknowledged'] else ''}"
    )
    for service in s["services"]:
        print(f"  {service['name']:<11}{service['label']:<9}{service['detail']}")
    for alert in s["alerts"]:
        print(f"  ALERT    {alert['name']} ({alert['state']})")
    for change in s["changes"][:3]:
        when = (
            f"t={change['t']}s"
            if change["t"] is not None
            else f"{change['ago_s'] // 60} min ago"
        )
        print(
            f"  change   {change['service']}: {change['text']} (by {change['by']}, {when})"
        )
    resolve = s["resolve"]
    if resolve["attempts"]:
        print(f"  resolve  {resolve['attempts']} tries, {resolve['wrong']} wrong")
    if resolve["watch_ends_in_s"] is not None:
        print(f"  watch    ends in {resolve['watch_ends_in_s']} s")
    return s


def ack() -> None:
    status, body = player("POST", "/v1/ack")
    say(f"Acknowledged at t={body['t']}s." if status == 200 else error_text(body))


def press(action_id: str, pairs: list[str]) -> dict:
    given = dict(pair.split("=", 1) for pair in pairs)
    key = uuid.uuid4().hex
    status, body = player(
        "POST",
        "/v1/actions",
        {"action_id": action_id, "input": given},
        **{"Idempotency-Key": key},
    )
    if status != 200:
        say(error_text(body))
        return body
    say(f"{body['label']} -> exit {body['exit_code']} in {body['duration_ms']} ms")
    print(f"  $ {body['command']}")
    for line in body["output"].rstrip().splitlines():
        print(f"    {line}")
    return body


def resolve() -> dict:
    status, body = player("POST", "/v1/resolve")
    if status != 200:
        say(error_text(body))
        return body
    if body["result"] == "passed":
        say(f"Resolve passed. Watching for {body['watch_seconds']} s.")
    else:
        say(f"Not resolved (-{body['penalty']} points):")
        for failing in body["failing"]:
            print(f"  - {failing['message']}")
    return body


def give_up() -> None:
    status, body = player("POST", "/v1/give-up", {"confirm": True})
    say("You ended the game." if status == 200 else error_text(body))


def feed() -> None:
    _, s = player("GET", "/v1/state")
    for item in s.get("feed", {}).get("items", []):
        t = f"t={item['t']}s" if item["t"] is not None else "before the page"
        if item["kind"] == "action_result":
            print(f"{t:>16}  {item['result']['label']}: $ {item['result']['command']}")
        else:
            print(f"{t:>16}  {item.get('level', '')}: {item.get('text', '')}")


def diary() -> list[dict]:
    status, body = control("GET", "/control/v1/report")
    if status != 200:
        sys.exit(error_text(body))
    for event in body["events"]:
        t = "-" if event["t"] is None else f"{event['t']}s"
        data = {
            k: v for k, v in event["data"].items() if k not in ("output", "command")
        }
        print(
            f"{event['seq']:>3}  t={t:<6} {event['type']:<16} {json.dumps(data) if data else ''}"
        )
    return body["events"]


# ----- auto: an expert's game ---------------------------------------------------------


def wait_for(what: str, condition, timeout_s: int, every_s: int = 3) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _, s = player("GET", "/v1/state")
        if condition(s):
            return s
        time.sleep(every_s)
    sys.exit(f"Gave up waiting for {what}.")


def service_ok(s: dict, name: str) -> bool:
    return any(x["name"] == name and x["status"] == "ok" for x in s.get("services", []))


def auto(watch: int | None) -> None:
    global TIMESTAMPS
    TIMESTAMPS = True
    start(watch)
    s = wait_for("the page", lambda s: s.get("state") == "paged", 180)
    say(f"PAGED: {s['page']['title']}")
    time.sleep(5)
    ack()
    say("Investigating: what does catalog's config say?")
    press("show-catalog-config", [])
    say("A flag that was turned on 25 minutes ago. Turning it off.")
    press("catalog-flags", ["flag=SEARCH_V2", "value=false"])
    wait_for("storefront to recover", lambda s: service_ok(s, "storefront"), 180)
    say("Storefront looks OK. Waiting for a clean minute before resolving.")
    time.sleep(45)
    for _ in range(4):
        if resolve().get("result") == "passed":
            break
        time.sleep(15)
    seconds = (
        watch or json.loads(PUSH.read_text())["runbook"]["checks"]["watch"]["seconds"]
    )
    s = wait_for(
        "the end of the watch", lambda s: s.get("state") == "ended", seconds + 120, 5
    )
    say(f"GAME OVER: {s['end_reason']}")
    print()
    show_state()
    print("\nTimeline:")
    feed()
    print("\nDiary (what scoring reads):")
    diary()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "auto"):
        sub = commands.add_parser(name)
        sub.add_argument("--watch", type=int, help="watch length in seconds (demo)")
    for name in ("state", "ack", "resolve", "give-up", "feed", "diary"):
        commands.add_parser(name)
    press_parser = commands.add_parser("press")
    press_parser.add_argument("action")
    press_parser.add_argument("input", nargs="*", help="key=value")
    args = parser.parse_args()

    if args.command == "start":
        start(args.watch)
    elif args.command == "auto":
        auto(args.watch)
    elif args.command == "press":
        press(args.action, args.input)
    else:
        {
            "state": show_state,
            "ack": ack,
            "resolve": resolve,
            "give-up": give_up,
            "feed": feed,
            "diary": diary,
        }[args.command]()


if __name__ == "__main__":
    main()
