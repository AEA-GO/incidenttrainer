# Events (the game diary)

Version: **v0.1 (draft)**. Changes freely until v1; after v1, only through an ADR.

Every game has one diary: a list of events, each written once and never changed.
Everything after the game is computed from it: the score (`score(runbook, events)`), the player's timeline on the results page, and the beta numbers.
Game states are described in [game-states.md](game-states.md).

## The envelope (every event)

| Field | Type | Meaning |
| --- | --- | --- |
| `seq` | integer >= 1 | Counter per writer per game (1, 2, 3...). Breaks ties when two events share `at`. |
| `at` | string, RFC 3339 UTC with ms | Wall-clock time, e.g. `2026-12-08T18:03:12.345Z`. |
| `t` | integer or `null` | Game time in seconds since the page (the page is `t = 0`). Only the agent knows game time, so `t` is `null` for events written by others and for agent events before the page. |
| `by` | `agent` \| `gateway` \| `api` \| `cleanup` | Who wrote it. |
| `type` | string | One of the types below. |
| `data` | object | Fields for that type (may be `{}`). |

Order: sort by `at`, then `by`, then `seq`.

## Rules

1. **Append only.** Events are never edited or deleted (until the 30-day TTL removes the whole game).
2. **Only the agent knows game time.** It starts the clock at the page.
3. **No answers in events.** No fault id, no fix, no runbook text. Only what the player saw anyway (hint text, check messages) plus the hidden fields listed in rule 4.
4. **Hidden until the game ends.** The browser must not receive these before `ended`: `chat_reply.data.tone`, `action.data.risky`, `status_update.data.checks_passing`, and the events `fault_started`, `server_started`, `agent_ready`, `server_terminated`.
5. **Saved as the game goes.** The agent keeps events in memory; the gateway copies new events into DynamoDB about every 10 s, so a game can still be scored if the server dies.
6. **Size limits.** Free text fields max 500 characters; `action.data.output` max 4,000 characters (cut, with `"truncated": true`).
7. **Forward compatible.** Readers ignore unknown event types and unknown fields. New optional fields are a minor version (v1.1); renaming or removing anything is a major version (v2).

## Event types

### Lifecycle

| Type | By | When | `data` |
| --- | --- | --- | --- |
| `game_created` | api | Start shift accepted | `lab_id`, `lab_version`, `level` (`intern` \| `junior`) |
| `server_started` | api | EC2 RunInstances returned | `instance_type` |
| `agent_ready` | gateway | agent answered ready and received the runbook | `runbook_version` |
| `fault_started` | agent | the fault begins (no details, see rule 3) | `{}` |
| `paged` | agent | the page fires; this event defines `t = 0` | `source` (`alert` \| `fallback`), `title`, `severity` (`SEV2`) |
| `acknowledged` | agent | player clicked Acknowledge | `{}` |

### Communication

| Type | By | When | `data` |
| --- | --- | --- | --- |
| `chat_message` | agent | Dana or Sam writes | `message_id`, `from` (`dana` \| `sam`), `text`, `kind` (`scripted` \| `chase` \| `reaction`) |
| `chat_reply` | agent | player picks a reply | `message_id`, `option_id`, `text`, `tone` (`honest` \| `overconfident` \| `deflecting`) |
| `status_update` | agent | player posts a status update | `affected` (text), `doing` (text), `next_update_t` (integer game seconds or `null`), `page_state` (`investigating` \| `identified` \| `monitoring` \| `resolved`), `fields_filled` (0-4), `checks_passing` (boolean: were the success checks passing at that moment) |
| `update_overdue` | agent | a promised update time passed without an update | `due_t` |

### Investigation and actions

| Type | By | When | `data` |
| --- | --- | --- | --- |
| `view` | agent (reported by the website) | player opens a panel; at most once per panel per minute. Optional in the MVP. | `panel` (`dashboards` \| `logs` \| `terminal` \| `changes` \| `map`) |
| `action` | agent | player presses an action button | `action_id`, `verb`, `target`, `params` (object), `command`, `exit_code`, `output`, `truncated`, `duration_ms`, `risky` (boolean) |
| `hint_used` | agent | player opens a hint | `n` (1-3), `hint_id`, `text` |

Terminal commands are **not** events: the agent judges the system, never the player's commands.

### Resolve and watch

| Type | By | When | `data` |
| --- | --- | --- | --- |
| `resolve_failed` | agent | Resolve pressed, a success check fails | `attempt`, `failing` (list of `{check_id, message}`), `penalty` (2, or 0 once -6 is reached) |
| `resolve_passed` | agent | Resolve pressed, all checks pass; the watch starts | `attempt`, `watch_seconds` |
| `reopened` | agent | a check failed during the watch | `severity` (`SEV1`), `failing` (list of `{check_id, message}`) |
| `watch_passed` | agent | the watch finished with no failure | `{}` |

### Health

| Type | By | When | `data` |
| --- | --- | --- | --- |
| `health` | agent | every 30 s from the page to the end | `value` (0-100), `failed_checkouts` (total so far) |

### End

| Type | By | When | `data` |
| --- | --- | --- | --- |
| `gave_up` | agent | player clicked Give up | `{}` |
| `time_limit` | agent | `t` reached 2400 (40 min) | `watch_cut_short` (boolean) |
| `heartbeat_lost` | gateway | no request from the browser for 300 s | `last_seen_at` |
| `boot_failed` | api or cleanup | agent not ready 180 s after `server_started`, or launch error | `reason` (`timeout` \| `launch_error`) |
| `ended` | agent, gateway, api or cleanup | exactly once per game; the last play event | `reason` (`resolved` \| `unresolved` \| `gave_up` \| `abandoned` \| `failed`), `final_health`, `severity`, `reopened` (boolean) |
| `server_terminated` | api or cleanup | the server is deleted | `reason` (`finished` \| `expired` \| `orphan` \| `boot_failed`), `lifetime_s` |

## Coverage: everything we compute has its events

| Needed for | Computed from |
| --- | --- |
| Speed | `t` of the last `resolve_passed` that was not followed by `reopened` |
| Correctness | `ended.reason`, `reopened` |
| Damage | `ended.final_health`, `action.risky` |
| Wrong Resolves (-2 each, max -6) | `resolve_failed.penalty` |
| Hints (-3 each, S grade needs none) | `hint_used` |
| Acknowledge within 2 min | `acknowledged.t` |
| First update within 5 min of ack | first `status_update.t` minus `acknowledged.t` |
| Updates at most 10 min apart | gaps between `status_update.t` |
| All fields filled | `status_update.fields_filled` |
| Promises kept | each `status_update.next_update_t` vs the next `status_update.t` (or the end) |
| Status page truthful | `status_update.page_state` vs `status_update.checks_passing` |
| Share of honest replies | `chat_reply.tone` |
| Results timeline | all events with `t` |
| Beta numbers (started, paged, acknowledged, first action, finished, abandoned) | `game_created`, `paged`, `acknowledged`, first `action`, `ended.reason` |
| Boot time, page delay | `server_started`, `agent_ready`, `fault_started`, `paged` (`at`) |
| Servers gone within 2 min | `ended.at` vs `server_terminated.at` |

## Example 1: a good player (fictional lab, not a real one)

```jsonl
{"seq": 1, "at": "2026-12-08T18:00:00.000Z", "t": null, "by": "api", "type": "game_created", "data": {"lab_id": "example-lab", "lab_version": "v1", "level": "intern"}}
{"seq": 2, "at": "2026-12-08T18:00:01.200Z", "t": null, "by": "api", "type": "server_started", "data": {"instance_type": "t4g.medium"}}
{"seq": 1, "at": "2026-12-08T18:01:10.000Z", "t": null, "by": "gateway", "type": "agent_ready", "data": {"runbook_version": "v1"}}
{"seq": 1, "at": "2026-12-08T18:01:10.500Z", "t": null, "by": "agent", "type": "fault_started", "data": {}}
{"seq": 2, "at": "2026-12-08T18:01:32.000Z", "t": 0, "by": "agent", "type": "paged", "data": {"source": "alert", "title": "Checkout error rate 34%", "severity": "SEV2"}}
{"seq": 3, "at": "2026-12-08T18:02:13.000Z", "t": 41, "by": "agent", "type": "acknowledged", "data": {}}
{"seq": 4, "at": "2026-12-08T18:02:50.000Z", "t": 78, "by": "agent", "type": "chat_message", "data": {"message_id": "sam-1", "from": "sam", "text": "Getting tickets: 'payment failed' at checkout. How bad is it?", "kind": "scripted"}}
{"seq": 5, "at": "2026-12-08T18:03:07.000Z", "t": 95, "by": "agent", "type": "chat_reply", "data": {"message_id": "sam-1", "option_id": "a", "text": "Investigating, about a third of checkouts fail. Next update by 18:15.", "tone": "honest"}}
{"seq": 6, "at": "2026-12-08T18:04:42.000Z", "t": 190, "by": "agent", "type": "status_update", "data": {"affected": "About a third of checkouts fail", "doing": "Investigating the checkout path", "next_update_t": 790, "page_state": "investigating", "fields_filled": 4, "checks_passing": false}}
{"seq": 7, "at": "2026-12-08T18:06:34.000Z", "t": 302, "by": "agent", "type": "action", "data": {"action_id": "act-1", "verb": "set_flag", "target": "catalog", "params": {"flag": "SEARCH_V2", "value": false}, "command": "sed -i 's/^SEARCH_V2=.*/SEARCH_V2=false/' catalog.env && docker compose up -d catalog", "exit_code": 0, "output": "Container catalog Recreated\nContainer catalog Started", "truncated": false, "duration_ms": 2100, "risky": false}}
{"seq": 8, "at": "2026-12-08T18:08:12.000Z", "t": 400, "by": "agent", "type": "resolve_passed", "data": {"attempt": 1, "watch_seconds": 240}}
{"seq": 9, "at": "2026-12-08T18:08:40.000Z", "t": 428, "by": "agent", "type": "status_update", "data": {"affected": "Checkout is working again", "doing": "Monitoring after a fix", "next_update_t": 790, "page_state": "monitoring", "fields_filled": 4, "checks_passing": true}}
{"seq": 10, "at": "2026-12-08T18:12:12.000Z", "t": 640, "by": "agent", "type": "watch_passed", "data": {}}
{"seq": 11, "at": "2026-12-08T18:12:12.100Z", "t": 640, "by": "agent", "type": "ended", "data": {"reason": "resolved", "final_health": 81, "severity": "SEV2", "reopened": false}}
{"seq": 3, "at": "2026-12-08T18:12:30.000Z", "t": null, "by": "api", "type": "server_terminated", "data": {"reason": "finished", "lifetime_s": 749}}
```

(`health` events every 30 s are left out of both examples to keep them short.)

## Example 2: a struggling player (same fictional lab)

```jsonl
{"seq": 1, "at": "2026-12-08T19:00:00.000Z", "t": null, "by": "api", "type": "game_created", "data": {"lab_id": "example-lab", "lab_version": "v1", "level": "intern"}}
{"seq": 2, "at": "2026-12-08T19:00:01.000Z", "t": null, "by": "api", "type": "server_started", "data": {"instance_type": "t4g.medium"}}
{"seq": 1, "at": "2026-12-08T19:01:15.000Z", "t": null, "by": "gateway", "type": "agent_ready", "data": {"runbook_version": "v1"}}
{"seq": 1, "at": "2026-12-08T19:01:15.400Z", "t": null, "by": "agent", "type": "fault_started", "data": {}}
{"seq": 2, "at": "2026-12-08T19:02:15.400Z", "t": 0, "by": "agent", "type": "paged", "data": {"source": "fallback", "title": "Checkout error rate 34%", "severity": "SEV2"}}
{"seq": 3, "at": "2026-12-08T19:05:20.400Z", "t": 185, "by": "agent", "type": "acknowledged", "data": {}}
{"seq": 4, "at": "2026-12-08T19:05:40.400Z", "t": 205, "by": "agent", "type": "chat_reply", "data": {"message_id": "dana-1", "option_id": "b", "text": "Should be fixed in 5 minutes.", "tone": "overconfident"}}
{"seq": 5, "at": "2026-12-08T19:06:40.400Z", "t": 265, "by": "agent", "type": "action", "data": {"action_id": "act-1", "verb": "restart", "target": "orders", "params": {}, "command": "docker compose restart orders", "exit_code": 0, "output": "Container orders Restarting\nContainer orders Started", "truncated": false, "duration_ms": 3400, "risky": true}}
{"seq": 6, "at": "2026-12-08T19:07:40.400Z", "t": 325, "by": "agent", "type": "resolve_failed", "data": {"attempt": 1, "failing": [{"check_id": "checkout-healthy", "message": "Checkout success is 82% (needs 99% for 2:00)"}], "penalty": 2}}
{"seq": 7, "at": "2026-12-08T19:09:40.400Z", "t": 445, "by": "agent", "type": "hint_used", "data": {"n": 1, "hint_id": "h1", "text": "Which service started failing first?"}}
{"seq": 8, "at": "2026-12-08T19:12:40.400Z", "t": 625, "by": "agent", "type": "chat_message", "data": {"message_id": "dana-chase-1", "from": "dana", "text": "You said 5 minutes. Any news?", "kind": "chase"}}
{"seq": 9, "at": "2026-12-08T19:16:40.400Z", "t": 865, "by": "agent", "type": "action", "data": {"action_id": "act-2", "verb": "set_flag", "target": "catalog", "params": {"flag": "SEARCH_V2", "value": false}, "command": "sed -i 's/^SEARCH_V2=.*/SEARCH_V2=false/' catalog.env && docker compose up -d catalog", "exit_code": 0, "output": "Container catalog Recreated\nContainer catalog Started", "truncated": false, "duration_ms": 2000, "risky": false}}
{"seq": 10, "at": "2026-12-08T19:17:40.400Z", "t": 925, "by": "agent", "type": "resolve_passed", "data": {"attempt": 2, "watch_seconds": 240}}
{"seq": 11, "at": "2026-12-08T19:19:40.400Z", "t": 1045, "by": "agent", "type": "reopened", "data": {"severity": "SEV1", "failing": [{"check_id": "checkout-healthy", "message": "Checkout success dropped to 90%"}]}}
{"seq": 12, "at": "2026-12-08T19:25:40.400Z", "t": 1405, "by": "agent", "type": "gave_up", "data": {}}
{"seq": 13, "at": "2026-12-08T19:25:40.500Z", "t": 1405, "by": "agent", "type": "ended", "data": {"reason": "gave_up", "final_health": 38, "severity": "SEV1", "reopened": true}}
{"seq": 3, "at": "2026-12-08T19:26:05.000Z", "t": null, "by": "api", "type": "server_terminated", "data": {"reason": "finished", "lifetime_s": 1564}}
```

## Next steps

- `events.schema.json` (JSON Schema) and a test that checks both examples against it, once `engine/` exists.
- Contract 6 (`score`) builds directly on the coverage table above.
