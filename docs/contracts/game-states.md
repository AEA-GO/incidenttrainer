# Game states

Version: **v0.1 (draft)**. Changes freely until v1; after v1, only through an ADR.

A game is always in exactly one state. The state is stored on the game's `META` item in DynamoDB (`state`, plus `end_reason` once ended) and returned by the agent's `GET /state`.
Every state change is caused by an event from [events.md](events.md); the state is never changed without writing that event.

## Diagram

```
                     ┌──────────── boot_failed ─────────────────────────────┐
                     │                                                      ▼
 booting ──agent_ready──► ready ──paged──► paged ──acknowledged──► investigating
                                             │                    ▲     │
                                             │      reopened      │     │ resolve_passed
                                             │   ┌────────────────┘     ▼
                                             │   │                  watching ──watch_passed──► ENDED (resolved)
                                             │   └──────────────────────┘
                                             │
   from ready, paged, investigating, watching:
     gave_up ──────────► ENDED (gave_up)
     time_limit ───────► ENDED (unresolved)
     heartbeat_lost ───► ENDED (abandoned)
   from booting: boot_failed ─► ENDED (failed)
```

`investigating` covers both "before the first Resolve" and "after a reopen"; whether the game was reopened is a flag (`reopened: true` on the game), not a separate state.

## States

| State | Means | Game clock | Player can |
| --- | --- | --- | --- |
| `booting` | server is starting, agent not ready yet | not started | read the handover note |
| `ready` | agent has the runbook, the fault has started, the page hasn't fired yet | not started | look around (dashboards, logs) |
| `paged` | the page fired; waiting for Acknowledge | running from `t = 0` | Acknowledge, and everything below |
| `investigating` | acknowledged; working on it | running | act, chat, post updates, use hints, Resolve, Give up |
| `watching` | Resolve passed; the agent checks that the fix holds | running | post updates, chat, Give up (no Resolve) |
| `ended` | game over; `end_reason` says why | stopped | see results |

## End reasons

| `end_reason` | When | Scored as |
| --- | --- | --- |
| `resolved` | the watch passed | normal score |
| `unresolved` | 40 minutes reached without a passed watch | no speed or correctness points, except half when time ran out during a holding watch |
| `gave_up` | player clicked Give up | like `unresolved` |
| `abandoned` | no browser request for 5 minutes | like `unresolved`; not counted as "finished" in beta numbers |
| `failed` | server did not start or agent not ready within 3 min | not scored; the game is given back to the invite code |

(Exact scoring is in [score.md](score.md).)

## Allowed changes

| From | To | Caused by event | Written by |
| --- | --- | --- | --- |
| `booting` | `ready` | `agent_ready` (then `fault_started`) | gateway |
| `booting` | `ended` (failed) | `boot_failed` | API or cleanup |
| `ready` | `paged` | `paged` | agent |
| `paged` | `investigating` | `acknowledged` | agent |
| `investigating` | `watching` | `resolve_passed` | agent |
| `investigating` | `investigating` | `resolve_failed` (no change, penalty only) | agent |
| `watching` | `investigating` | `reopened` (severity becomes SEV1, `reopened: true`) | agent |
| `watching` | `ended` (resolved) | `watch_passed` | agent |
| `ready`, `paged`, `investigating`, `watching` | `ended` (gave_up) | `gave_up` | agent |
| `paged`, `investigating`, `watching` | `ended` (unresolved) | `time_limit` | agent |
| `ready`, `paged`, `investigating`, `watching` | `ended` (abandoned) | `heartbeat_lost` | gateway |
| any not `ended` | `ended` (abandoned or unresolved) | cleanup finds the server gone or past 45 min | cleanup |

Anything not in this table is refused (e.g. Resolve while `paged`, a second `ended`).

## Who owns the state

- **API:** creates the game in `booting`; can end it as `failed`.
- **Gateway:** moves `booting → ready`; ends it as `abandoned`; copies the agent's state into DynamoDB about every 10 s.
- **Agent:** owns every change during play (`ready` to `ended`). Its state is the truth while the server is alive.
- **Cleanup:** last resort; ends any game whose server is gone or too old.

Writes to DynamoDB use a condition (`state <> ended`), so a game can be ended only once, even if two parts try at the same moment.

## Edge cases (decided)

| Case | What happens |
| --- | --- |
| Resolve pressed before Acknowledge | refused: "Acknowledge the page first" |
| Give up during the watch | ends as `gave_up` (the fix may have worked, but the player chose to stop) |
| 40 minutes reached during the watch | ends as `unresolved`; `time_limit.data.watch_cut_short = true`; contract 6 decides partial credit |
| Reopen, then Resolve again | a new full watch starts; the grade is capped at B |
| Second reopen | allowed; severity stays SEV1 |
| Browser closed during `booting` | the boot continues; if the player never returns, the game ends `abandoned` 5 min after `ready` |
| Server dies mid-game | the gateway can't reach the agent; cleanup ends the game as `abandoned` and scores it from the events already saved |
| Player opens the game in a second tab | allowed; both tabs poll the same state |
| The page never fires (alert broken) | the fallback in the runbook fires it, so `ready` never lasts forever |

## Open

- Watch length (4 min now, ~90 s proposed): only changes `resolve_passed.data.watch_seconds`, not the states.
- Whether `ready` should be shown to the player at all, or hidden as part of the boot screen.
