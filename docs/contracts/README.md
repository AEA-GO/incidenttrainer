# Contracts

Every part of IncidentTrainer talks to the others only through these contracts.
What sits behind a contract can change freely; a contract itself changes only through an ADR, with a new version number and an updated test.

| # | Contract | Between | File | Status |
| --- | --- | --- | --- | --- |
| 1 | Lab file format, world manifest and verb menu | labs repo and agent | [lab-format.md](lab-format.md), [lab-format.schema.json](lab-format.schema.json), [world.schema.json](world.schema.json) | draft v0.1 |
| 2 | Agent API (player 8080, control 8081) | gateway and agent | [agent-api.yaml](agent-api.yaml) | draft v0.1 |
| 3 | Session API `/api/*` | website and API | [session-api.yaml](session-api.yaml) | draft v0.2 |
| 4 | Server launcher (start, stop, address, list) | API/cleanup and EC2 | [launcher.md](launcher.md) | draft v0.1 |
| 5 | Game path `/s/<id>/*` and signed cookie | browser and gateway | [game-path.md](game-path.md) | draft v0.1 |
| 6 | `score(scoring, events) -> result` | event log and results | [score.md](score.md) | draft v0.2 |

Shared by several contracts:

| Document | What | Status |
| --- | --- | --- |
| [events.md](events.md) | the game diary: every event type and its fields | draft v0.2 |
| [game-states.md](game-states.md) | the stages of a game and who may change them | draft v0.2 |
| [data-model.md](data-model.md) | DynamoDB items, keys and the start/end transactions | draft v0.2 |

Examples (fictional, safe to be public): [examples/example-lab.yaml](examples/example-lab.yaml) and [examples/example-world.yaml](examples/example-world.yaml). The event diaries in events.md and the worked scores in score.md are games of that example lab.

## How a game flows through the contracts

```
website ──3──► API ──4──► EC2 server boots
   │            │
   │            └── writes the game (data-model) and sets the cookie (5)
   │
   └──5──► gateway ──2 (control)──► agent: claim, runbook (1), report → events, states
              │
              └──2 (player)──► agent: state, actions, chat, resolve …
API finish: events ──6──► score → results page
```

Rules:
- Each file states its version at the top.
- Each contract has an example and a test that checks the example against it (the tests arrive with `engine/`, the API and the agent).
- No lab answers here: this repo is public. Lab content lives in the private labs repo.
