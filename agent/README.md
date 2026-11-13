# itr-agent

The referee on every game server: starts the fault, pages, runs the buttons, plays Dana and Sam in the chat, checks whether the shop is really fixed, watches that the fix holds, ends the game, and writes the game diary.
Specification: [contract 2, agent API](../docs/contracts/agent-api.yaml). Lab and world formats: [contract 1](../docs/contracts/lab-format.md). Diary: [events](../docs/contracts/events.md). States: [game states](../docs/contracts/game-states.md).

Two web servers in one process, sharing one in-memory `Agent`, plus a background loop:
- **8080, player API** (`/v1/*`): reached only through the gateway.
- **8081, control API** (`/control/v1/*`): only gateway-auth calls it; protected by the token set in `claim`.
- **Game loop** (every `ITR_TICK_S`, default 2 s): reads Prometheus, pages, keeps the health bar, sends chat messages that are due, runs the watch, stops the game at the time limit.

## Run the tests

```bash
cd agent
uv sync
uv run pytest
```
Tests never run Docker or need Prometheus: fakes stand in for commands, Prometheus, HTTP and the clock.

## Play a game on your laptop

Terminal 1, the stand-in shop (see `dev/README.md`):
```bash
cd agent/dev
sed -i 's/^SEARCH_V2=.*/SEARCH_V2=false/' config/catalog.env    # start healthy
docker compose up -d
```
Terminal 2, the agent pointed at that shop (restart it before every game: it keeps everything in memory, by design):
```bash
cd agent
ITR_WORLD_MANIFEST=dev/world.yaml ITR_COMPOSE_DIR=dev uv run itr-agent
```
Terminal 3, you. `dev/play.py` plays both the gateway and the browser (any folder works):
```bash
cd agent
python3 dev/play.py start           # claim + load the dev lab; the agent breaks the shop
python3 dev/play.py state           # repeat until PAGED (~30 s)
python3 dev/play.py ack
python3 dev/play.py press show-catalog-config
python3 dev/play.py press catalog-flags flag=SEARCH_V2 value=false
python3 dev/play.py chat            # Dana and Sam write; each message with replies shows its letters
python3 dev/play.py reply sam-1 a   # answer (once per message); the character reacts
python3 dev/play.py state           # wait until storefront is OK, then about a minute more
python3 dev/play.py resolve         # failed: what's still wrong. Passed: the watch starts (4:00)
python3 dev/play.py state           # watch the countdown; the game ends "resolved"
python3 dev/play.py feed            # your timeline
python3 dev/play.py diary           # what scoring will read
```
Other moves: `press restart-catalog` (it won't help: the flag stays on, and Sam notices), `give-up`. `state` says when someone is waiting for a reply.
Watch it play itself: `python3 dev/play.py auto --watch 30` (a whole expert game in about 2.5 minutes, answering the chat honestly; `--watch` shortens the watch for demos).

## Layout

| File | What |
| --- | --- |
| `main.py` | starts both servers and the game loop; reads the world manifest; JSON logs with `game_id` on every line |
| `state.py` | the `Agent`: everything it remembers, in memory only (the runbook too); claim, runbook, ack, buttons, the loop |
| `outcome.py` | Resolve, the watch, reopen at SEV1, every way a game ends, damage checks |
| `chat.py` | Dana and Sam: scripted messages (at a time or after a trigger), replies with a hidden tone, reactions, chasing missed promises and silence |
| `checks.py` | the 8 check verbs; "no data" is never healthy |
| `control.py` | port 8081: ready, claim, runbook, report, end |
| `player.py` | port 8080: scene, state, ack, actions, resolve, give-up |
| `views.py` | what the player sees (never hidden fields) |
| `actions.py` | buttons: input checks, labels, recent-change text, runbook checks |
| `verbs.py` | real commands (no shell, checked inputs); shows the player a readable command |
| `observe.py` | one look at the shop: alerts, failed checkouts, service colours |
| `prometheus.py` | Prometheus API client (no data rather than errors) |
| `world.py` | reads the world manifest |
| `runbook.py` | checks a runbook fits this world before accepting it |
| `events.py` | the game diary |
| `feed.py` | the player's timeline (cursor-based) |
| `clocks.py` | game time (M:SS) and in-world time (HH:MM) |
| `errors.py` | one error shape for everything |

`dev/labs/dev-lab.yaml` is a fictional lab for the stand-in shop; `dev-lab.push.json` is what the gateway would send for it (generated from the YAML; `itr-lab split` will generate it once the engine has it).

## Status

Done: claim, runbook, fault, page, clock, Acknowledge, health bar, service map, buttons, Resolve, watch, reopen, endings, damage checks, diary (steps 1-7); chat with Dana and Sam (step 8).

Still to add before the beta: status updates and the status page (they feed the chat's chasing through `Chat.status_posted`), hints, starting Grafana with the game's sub-path at claim, real boot checks for `ready`, the terminal (ttyd) for Junior labs, `view` events.
