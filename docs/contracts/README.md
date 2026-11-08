# Contracts

Every part of IncidentTrainer talks to the others only through these contracts.
What sits behind a contract can change freely; a contract itself changes only through an ADR, with a new version number and an updated test.

| # | Contract | Between | File | Status |
| --- | --- | --- | --- | --- |
| 1 | Lab file format and verb menu | labs repo and agent | `lab-format.schema.json` | planned |
| 2 | Agent API (player 8080, control 8081) | gateway and agent | `agent-api.yaml` | planned |
| 3 | Session API `/api/*` | website and API | `session-api.yaml` | planned |
| 4 | Server launcher (start, stop, address) | API and EC2 | `launcher.md` | planned |
| 5 | Game path `/s/<id>/*` and signed cookie | browser and gateway | `game-path.md` | planned |
| 6 | `score(runbook, events) -> result` | event log and results | `score.schema.json` | planned |

Shared by several contracts: the event list and game states (`events.md`, planned).

Rules:
- Each file states its version (e.g. `v1.0`) at the top.
- Each contract has an example and a test that checks the example against it.
- No lab answers here: this repo is public. Lab content lives in the private labs repo.
