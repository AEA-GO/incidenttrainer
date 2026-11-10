# itr-agent

The referee on every game server: starts the fault, runs the buttons, checks whether the shop is really fixed, and writes the game diary.
Specification: [contract 2, agent API](../docs/contracts/agent-api.yaml). Lab and world formats: [contract 1](../docs/contracts/lab-format.md).

Two web servers in one process, sharing one in-memory `Agent`:
- **8080, player API** (`/v1/*`): reached only through the gateway.
- **8081, control API** (`/control/v1/*`): only gateway-auth calls it; protected by the token set in `claim`.

## Run and test

```bash
cd agent
uv sync
uv run pytest
uv run itr-agent          # starts both ports
```

Try it in a second terminal:
```bash
curl -s localhost:8081/control/v1/ready
curl -s -X POST localhost:8081/control/v1/claim -H 'Content-Type: application/json' \
  -d '{"game_id":"q3V9xYk2Lm8Ta1BcD4eF0g","token":"ttttttttttttttttttttttttttttttttttttttttttt","public_base_url":"http://localhost:8000"}' -w '%{http_code}\n'
curl -s localhost:8081/control/v1/report -H 'Authorization: Bearer ttttttttttttttttttttttttttttttttttttttttttt'
```

## Layout

| File | What |
| --- | --- |
| `main.py` | starts both servers, JSON logs with `game_id` on every line |
| `state.py` | the `Agent`: everything it remembers, in memory only |
| `control.py` | port 8081 |
| `player.py` | port 8080 |
| `errors.py` | one error shape for everything |

## Status

Step 1 of 7: ports, `ready`, `claim`, token check, `report` (empty). Next: stand-in shop, runbook + event log, clock + page, one button, one check, end of game.
