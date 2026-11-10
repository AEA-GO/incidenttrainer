# Contract 6: score

Version: **v0.2 (draft)**. Changes freely until v1; after v1, only through an ADR.

```
score(scoring, events) -> result
```

A **pure function**: no network, no database, no clock, no randomness. The same input always gives the same output, so old games can be rescored after a rule change.
It reads the game diary defined in [events.md](events.md) and the scoring part of the lab's runbook.

## Input

**`scoring`**: the lab card's `level` plus its `scoring` block (contract 1). The API copies both onto the game when it starts, so a game is always scored with the lab version it was played on.

| Field | Type | Meaning |
| --- | --- | --- |
| `level` | `intern` \| `junior` | picks the weights below |
| `target_time_s` | integer | resolving by this game time gives full speed points (the expert's time plus a margin) |
| `speed_zero_s` | integer, optional | default 1800 (30 min): no speed points from here on |
| `health_full` | integer, optional | default 75: keeping this % or more of the health bar gives full damage points |

**`events`**: the full diary of one game, in order (see events.md). Unknown event types are ignored.

Below, `1800` and `75` stand for `speed_zero_s` and `health_full`.

## Output

```json
{
  "version": "score-v0.2",
  "scored": true,
  "score": 98,
  "grade": "S",
  "grade_caps": [],
  "categories": {
    "speed":         {"max": 30, "got": 28.0},
    "correctness":   {"max": 40, "got": 40.0},
    "damage":        {"max": 20, "got": 20.0},
    "communication": {"max": 10, "got": 10.0}
  },
  "penalties": {"hints": 0, "wrong_resolves": 0},
  "lost": [
    {"rule": "speed", "points": 2.0, "reason": "Resolved at 6:40. Full points by 5:00, none after 30:00."}
  ],
  "facts": {"end_reason": "resolved", "resolve_t": 400, "final_health": 81, "reopened": false, "hints_used": 0, "wrong_resolves": 0}
}
```

- `got` values keep 1 decimal. `score` is the total rounded half up to a whole number, clamped to 0-100.
- `lost` has **one line for every rule that lost points**, with a plain-words reason. This is what the results page shows as "a reason for every lost point".
- `grade_caps` lists why a grade was limited (`reopened`, `hints_used`, `not_resolved`).
- Games that can't be scored return `{"version": "score-v0.2", "scored": false, "why": "..."}` (see Not scored).

## Weights

| Category | Intern | Junior |
| --- | --- | --- |
| Speed | 30 | 25 |
| Correctness | 40 | 35 |
| Damage | 20 | 20 |
| Communication | 10 | 20 |

Total 100. Then penalties are subtracted.

## Facts the rules use

- `page_t`: always 0 (the `paged` event).
- `end_t`: `t` of `ended`; if that is `null` (ended by gateway or cleanup), the largest `t` in the diary.
- `resolve_t`: `t` of the last `resolve_passed` that is **not** followed by `reopened`. `null` if none.
- `reopened`: any `reopened` event.
- `fix_held`: `end_reason = resolved`, or `end_reason = unresolved` with `time_limit.data.watch_cut_short = true` and no `reopened` after the last `resolve_passed`.
- `updates`: all `status_update` events, in order.

## Rules

### Speed (`speed`)
- If `fix_held` is false: 0.
- Else: `weight × clamp((1800 − resolve_t) / (1800 − target_time_s), 0, 1)`.

Reason: "Resolved at M:SS. Full points by M:SS, none after 30:00." / "Not resolved, so no speed points."

### Correctness (`correctness`)
| Situation | Points |
| --- | --- |
| `end_reason = resolved`, never reopened | full |
| `end_reason = resolved`, reopened at least once | half |
| `end_reason = unresolved`, watch cut short by the time limit, fix holding | half |
| anything else (unresolved, gave up, abandoned) | 0 |

Reasons: "The first fix didn't hold: the incident reopened." / "Time ran out during the watch." / "The incident wasn't resolved."

### Damage
- **`damage.health`:** `weight × clamp(final_health / 75, 0, 1)`, where `final_health` is from `ended`.
  Reason: "N% of the health bar left. Full points at 75% or more."
- **`damage.risky`:** −3 for each `action` with `risky: true`.
  Reason: "N risky action(s): <verb target>, ..."
- **`damage.checks`:** −3 for each `damage_found` event (a damage check that failed at the end, e.g. data was lost).
  Reason: the check's `message`, e.g. "Orders placed before the incident were lost."
- The damage category can't go below 0.

### Communication

Each rule is a share of the communication weight (Intern 10, Junior 20). If the player posted **no status update at all**, rules 2-6 give 0.

| Rule | Share | Full points when | Partial |
| --- | --- | --- | --- |
| `comm.ack` | 15% | `acknowledged.t <= 120` | none |
| `comm.first_update` | 20% | first update `t − acknowledged.t <= 300` | none |
| `comm.cadence` | 15% | every gap between updates, and from the last update to `end_t`, is <= 600 s | share of gaps that are <= 600 s |
| `comm.fields` | 10% | every update has `fields_filled = 4` | share of updates with all 4 |
| `comm.promises` | 15% | every promise was kept | share kept |
| `comm.truthful` | 10% | no update claims `monitoring` or `resolved` while `checks_passing = false` | share of truthful updates |
| `comm.honest` | 15% | every chat reply has `tone = honest` | share of honest replies; no replies at all = 0 |

Details:
- **Promise:** an update with `next_update_t` that is earlier than `end_t`. It is kept if another update comes at `t <= next_update_t + 60` (one minute of grace). If no promise fell due before the end, `comm.promises` is full.
- **Truthful:** saying `investigating` or `identified` while things already work is not a lie; only claiming recovery too early is.

Reason examples: "Acknowledged at 3:05. Target: within 2:00." / "First status update came 7:10 after acknowledging. Target: within 5:00." / "Promised an update by 13:10, posted it at 15:40." / "Status page said Monitoring while checkout was still failing." / "1 of 2 replies to colleagues was overconfident."

### Penalties
- **`penalty.hints`:** −3 per `hint_used`.
- **`penalty.wrong_resolve`:** −2 per `resolve_failed`, at most −6. (Counted here; the `penalty` field in the event is only for display.)

## Grade

| Grade | Score | Also needs |
| --- | --- | --- |
| S | 90+ | resolved, no hints, never reopened |
| A | 80+ | |
| B | 65+ | |
| C | 50+ | |
| D | below 50 | |

Caps: reopened → at most **B**. A score of 90+ with hints or without `resolved` → **A**.

## Not scored

| Case | Output |
| --- | --- |
| `end_reason = failed` (server didn't start) | `scored: false, why: "failed"` |
| The game ended before `paged` | `scored: false, why: "ended_before_page"` |
| No `ended` event | `scored: false, why: "not_ended"` |

## Worked examples

Both use the two example diaries in events.md, with `level = intern` and `target_time_s = 300`.

**Example 1 (good player): 98, grade S**

| Rule | Got | Lost | Why |
| --- | --- | --- | --- |
| speed | 28.0 / 30 | 2.0 | resolved at 6:40; (1800 − 400) / 1500 = 0.933 |
| correctness | 40 / 40 | 0 | resolved, never reopened |
| damage | 20 / 20 | 0 | 81% health left, no risky actions |
| communication | 10 / 10 | 0 | ack 0:41, first update 2:29 after ack, gaps 3:58 and 3:32, all fields, no promise due, truthful, honest reply |
| penalties | 0 | 0 | |

Total 98.0 → **98, S**.

**Example 2 (struggling player): 2, grade D**

| Rule | Got | Lost | Why |
| --- | --- | --- | --- |
| speed | 0 / 30 | 30 | gave up, fix never held |
| correctness | 0 / 40 | 40 | gave up |
| damage | 7.1 / 20 | 12.9 | 38% health → 20 × 38/75 = 10.1, then −3 for one risky restart |
| communication | 0 / 10 | 10 | ack at 3:05 (late), no status updates at all (rules 2-6 = 0), the only reply was overconfident |
| hints | −3 | 3 | one hint |
| wrong Resolves | −2 | 2 | one wrong Resolve |

Total 2.1 → **2, D** (the reopen cap to B doesn't matter here).

## Tests the implementation must have

- Both worked examples above give exactly these numbers.
- Same input twice → identical output.
- Shuffling unknown event types into the diary changes nothing.
- Edge cases: two `damage_found` events (−6, category not below 0); resolved after a reopen (half correctness, grade max B); time limit during the watch; abandoned with `ended.t = null`; no chat replies; a promise falling due after the end; 4 wrong Resolves (still −6); 90+ with a hint (grade A).

## Open

- Whether ack and first-update rules should give partial points (linear) instead of all-or-nothing.
- Watch length (4 min vs ~90 s) does not change this contract.
