# itr-agent

The referee on every game server: starts the fault, runs the buttons, checks whether the shop is really fixed, and writes the game diary.
Specification: [contract 2, agent API](../docs/contracts/agent-api.yaml). Lab and world formats: [contract 1](../docs/contracts/lab-format.md). Diary: [events](../docs/contracts/events.md).

Two web servers in one process, sharing one in-memory `Agent`:
- **8080, player API** (`/v1/*`): reached only through the gateway.
- **8081, control API** (`/control/v1/*`): only gateway-auth calls it; protected by the token set in `claim`.

## Run the tests

```bash
cd agent
uv sync
uv run pytest
```
Tests never run Docker: a fake command runner records what would run, and verbs edit a temporary copy of the config.

## Play the start of a game by hand (laptop)

Terminal 1, the stand-in shop (see `dev/README.md`):
```bash
cd agent/dev && docker compose up -d --build
```
Terminal 2, the agent pointed at that shop:
```bash
cd agent
ITR_WORLD_MANIFEST=dev/world.yaml ITR_COMPOSE_DIR=dev uv run itr-agent
```
Terminal 3, act as the gateway:
```bash
cd agent
T=ttttttttttttttttttttttttttttttttttttttttttt
curl -s localhost:8081/control/v1/ready; echo
curl -s -X POST localhost:8081/control/v1/claim -H 'Content-Type: application/json' \
  -d "{\"game_id\":\"q3V9xYk2Lm8Ta1BcD4eF0g\",\"token\":\"$T\",\"public_base_url\":\"http://localhost:8000\"}" -w '%{http_code}\n'
curl -s -X PUT localhost:8081/control/v1/runbook -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $T" -d @dev/labs/dev-lab.push.json; echo
curl -s localhost:8081/control/v1/report -H "Authorization: Bearer $T"; echo
```
The agent turns the catalog flag on (watch the Prometheus alert fire at http://localhost:9090/alerts) and the report shows `fault_started`.
Reset afterwards: `sed -i 's/^SEARCH_V2=.*/SEARCH_V2=false/' dev/config/catalog.env && docker compose -f dev/compose.yaml up -d catalog`. Restart the agent to play again (a server is claimed once).

## Layout

| File | What |
| --- | --- |
| `main.py` | starts both servers; reads the world manifest; JSON logs with `game_id` on every line |
| `state.py` | the `Agent`: everything it remembers, in memory only (the runbook too) |
| `control.py` | port 8081: ready, claim, runbook, report |
| `player.py` | port 8080 |
| `world.py` | reads the world manifest |
| `runbook.py` | checks a runbook fits this world before accepting it |
| `verbs.py` | real commands (no shell, checked inputs); shows the player a readable command |
| `events.py` | the game diary |
| `errors.py` | one error shape for everything |

`dev/labs/dev-lab.yaml` is a fictional lab for the stand-in shop; `dev-lab.push.json` is what the gateway would send for it (generated from the YAML; `itr-lab split` will generate it once the engine has it).

## Status

Steps 1–3 of 7: ports, claim with token, runbook in memory, the fault really runs (`set_config`, `set_flag`), diary + report.
Next: game clock and page, buttons, checks and Resolve, end of game.
