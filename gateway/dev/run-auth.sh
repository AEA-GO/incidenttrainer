#!/usr/bin/env bash
# gateway-auth on a laptop: games from dev/games.json, the contract's test key (bytes
# 0..31, public, laptops only), the agent and Grafana on this machine (127.0.0.1),
# insecure cookie, /dev-login.
set -euo pipefail
cd "$(dirname "$0")/.."
export ITR_DEV_GAMES=dev/games.json
ITR_COOKIE_KEYS=$(python3 -c 'import base64, json; s = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode(); print(json.dumps({"current": "k1", "keys": {"k1": {"secret": s}}}))')
export ITR_COOKIE_KEYS
export ITR_SANDBOX_CIDRS=127.0.0.0/8
export ITR_COOKIE_INSECURE=1
export ITR_DEV_LOGIN=1
export ITR_PUBLIC_ORIGIN=http://localhost:8088
exec uv run itr-gateway-auth
