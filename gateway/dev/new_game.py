"""A fresh laptop game for gateway-auth: writes dev/.games.json (state booting).

    python3 dev/new_game.py                      # lab 1 (checkout-down-3am)
    python3 dev/new_game.py --lab dev-lab --runbooks ../agent/dev/labs-out

The broker in gateway-auth then boots it like a real game: it claims the agent on this
laptop, pushes the lab's runbook (from <runbooks>/<lab>/push.json, made by the labs repo's
scripts/make_push.py) and copies the game's events to dev/.games.json.events/.
Run it before every game (and restart the agent: it accepts one claim per start).
"""

import argparse
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

GAME_ID = "q3V9xYk2Lm8Ta1BcD4eF0g"  # play.py's fixed dev id
HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lab", default="checkout-down-3am")
    parser.add_argument(
        "--runbooks",
        type=Path,
        default=Path.home() / "IncTrainer/incidenttrainer-labs/out",
    )
    args = parser.parse_args()

    push = json.loads((args.runbooks / args.lab / "push.json").read_text())
    now = time.time()
    game = {
        "state": "booting",
        "private_ip": "127.0.0.1",
        "instance_id": "laptop",
        "lab_id": push["lab_id"],
        "lab_version": push["lab_version"],
        "terminal": push["runbook"].get("level") == "junior",
        "created_at": datetime.fromtimestamp(now, UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        ),
        "expires_at_server": int(now) + 2700,
    }
    (HERE / ".games.json").write_text(
        json.dumps({"games": {GAME_ID: game}}, indent=2) + "\n"
    )
    shutil.rmtree(HERE / ".games.json.events", ignore_errors=True)
    print(f"New game {GAME_ID}: {push['lab_id']} v{push['lab_version']}, booting.")
    print(
        "Restart the agent if it ran a game before; the gateway boots this one by itself."
    )


if __name__ == "__main__":
    main()
