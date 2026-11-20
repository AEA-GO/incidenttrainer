#!/usr/bin/env bash
# gateway-auth on a laptop, with the broker: games from dev/.games.json (dev/new_game.py),
# runbooks from the labs repo's out/ folder, the contract's test key (bytes 0..31, public,
# laptops only), the agent and Grafana on this machine (127.0.0.1), insecure cookie,
# /dev-login.
set -euo pipefail
cd "$(dirname "$0")/.."
export ITR_DEV_GAMES=dev/.games.json
export ITR_DEV_RUNBOOKS="${ITR_DEV_RUNBOOKS:-$HOME/IncTrainer/incidenttrainer-labs/out}"
[ -f "$ITR_DEV_GAMES" ] || { echo "No game yet: run python3 dev/new_game.py first" >&2; exit 1; }
ITR_COOKIE_KEYS=$(python3 -c 'import base64, json; s = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode(); print(json.dumps({"current": "k1", "keys": {"k1": {"secret": s}}}))')
export ITR_COOKIE_KEYS
export ITR_SANDBOX_CIDRS=127.0.0.0/8
export ITR_COOKIE_INSECURE=1
export ITR_DEV_LOGIN=1
export ITR_PUBLIC_ORIGIN=http://localhost:8088
exec uv run itr-gateway-auth
