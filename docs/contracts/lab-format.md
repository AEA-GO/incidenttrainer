# Contract 1: lab file format and verb menu

Version: **v0.1 (draft)**. Changes freely until v1; after v1, only through an ADR.

A lab is **one YAML file** in the private labs repo: `labs/<lab_id>/lab.yaml`. Labs CI checks it against [lab-format.schema.json](lab-format.schema.json) and the extra rules below, plays it, and publishes it split into three files with different secrecy.
A lab runs on a **world** (a shop), described by a world manifest ([world.schema.json](world.schema.json)) that is baked into the server image.

Examples (fictional, safe to be public): [examples/example-lab.yaml](examples/example-lab.yaml), [examples/example-world.yaml](examples/example-world.yaml).
**Real labs never appear in this repo.**

## Principles

1. **The lab describes what happens, never how the world is built.** No AWS, no IPs, no file paths outside the world's own folders. The same lab format works for any world that runs an agent.
2. **Faults are real.** Every fault is a fixed verb that changes real config, flags, versions or resources. No hidden fault files, no fake metrics.
3. **The agent judges the system, not the player.** Success, watch and damage checks look at the running shop; terminal commands are never inspected.
4. **Secrecy by file.** What the player may see before, during and after the game is decided by which published file a field goes into (below), not by convention.
5. **Times are integers in seconds.** `t` is game time: 0 is the page. Durations end in `_s`.

## Who sees what

| Published file | Contains | Read by | When the player can see it |
| --- | --- | --- | --- |
| `card.json` | `id`, `version`, `world`, `course`, `card`, `scoring` | API | any time (labs page, lab page, boot screen) |
| `runbook.json` | `id`, `version`, `world`, `level`, `limits`, `health`, `scene`, `setup`, `fault`, `page`, `timeline`, `chat`, `actions`, `checks`, `hints` | gateway → agent memory | never as a file; pieces appear in the game when they happen (a chat message, a hint, a failed check's message) |
| `debrief.json` | `id`, `version`, `debrief` | API | only after the game has ended |
| (not published) | `secrets` | labs CI and platform CI | never |

Bucket layout: `labs/<lab_id>/v<version>/{card,runbook,debrief}.json` and `labs/<lab_id>/current` (text: `v<version>`); `course/<course_id>.json` lists lab ids in order. A game records the `lab_version` it started with.

## Top-level fields

| Field | Required | Meaning |
| --- | --- | --- |
| `format` | yes | `itr-lab/v1` |
| `id` | yes | slug `^[a-z0-9-]{2,40}$`, never reused |
| `version` | yes | integer ≥ 1; bump on every published change (CI refuses a changed lab with an already published version) |
| `world` | yes | world name, e.g. `teapot-basic` |
| `course` | yes | `{id, order}` |
| `card` | yes | public description (below) |
| `scoring` | yes | `target_time_s` (resolving by then gives full speed points); optional `speed_zero_s` (default 1800), `health_full` (default 75) |
| `limits` | no | `time_limit_s` (default 2400) |
| `health` | yes | `budget_failed_checkouts`: failed checkouts (counted from the page) that empty the health bar; tune so the expert keeps ~75% |
| `scene` | yes | the story's setting (below) |
| `setup` | no | fault-verb steps run when the runbook arrives, before the fault, to put the world in the lab's starting state |
| `fault` | yes | fault-verb steps run right after `setup`; the incident begins |
| `page` | yes | which alert pages the player (below) |
| `timeline` | no | fault-verb steps at a game time (`at`), e.g. a second problem or a noisy alert |
| `chat` | yes | Dana and Sam (below) |
| `actions` | yes | the buttons the player gets (below) |
| `checks` | yes | `success`, `watch`, `damage` (below) |
| `hints` | no | up to 3, in order (below) |
| `debrief` | yes | what really happened (below) |
| `secrets` | yes | give-away words and phrases (below) |

### `card` (public)

| Field | Meaning |
| --- | --- |
| `title` | ≤ 60 chars |
| `hook` | one line, ≤ 140 chars |
| `level` | `intern` (buttons) or `junior` (buttons + terminal) |
| `minutes` | typical play time |
| `skills` | 1–5 short phrases |
| `tools` | from `grafana`, `logs`, `buttons`, `terminal` |
| `handover_note` | shown on the boot screen; ≤ 800 chars; must not hint at the cause |

Derived when publishing: `terminal = (level == junior)`.

### `scene`

| Field | Meaning |
| --- | --- |
| `starts_at` | in-world clock at the page, `HH:MM` (lab 1 starts at night, lab 2 on Friday evening). The incident screen shows `starts_at + t`; the player types promise times in this clock |
| `weekday` | optional, e.g. `Friday` |
| `recent_changes` | changes that happened before the shift, shown in "Recent changes": `{ago_s, service, text, by}`. May include unrelated changes (noise) |

### `page`

| Field | Meaning |
| --- | --- |
| `alert` | name of an alert rule in the world (`world.alerts`) |
| `fallback_after_s` | seconds after the fault starts; if the alert hasn't fired by then, the agent pages anyway (≤ 120) |
| `title` | page title; may use `{value}` (the alert's value, rounded) |
| `severity` | `SEV2` (a reopen raises it to `SEV1`) |

### `chat`

```yaml
chat:
  messages:
    - id: sam-1                 # unique
      from: sam                 # dana | sam
      when: {at: 60}            # game seconds, or {trigger: <name>, delay_s: N, match: {...}}
      text: "..."
      replies:                  # optional, 2–3 options
        - {id: a, tone: honest, text: "...", reaction: "..."}
  chase:
    overdue: {from: dana, text: "You said {promised}. Any news?"}      # a promised update time passed (+60 s)
    silence: {from: sam, after_s: 600, text: "..."}                     # no status update for this long
```
- `when.trigger` values (not `on`: YAML reads a bare `on` key as `true`): `acknowledged`, `first_status_update`, `status_update`, `action`, `hint_used`, `resolve_failed`, `resolve_passed`, `reopened`. `match` narrows the `action` trigger by `verb` and/or `target`. Each message is sent at most once.
- A reply's `reaction` is sent by the same character right after the player picks it. `tone` is hidden from the player until the game ends.
- Placeholders: `{promised}` (in-world `HH:MM`), `{t}` (game `M:SS`), `{value}` (where a value exists). Nothing else.

### `actions` (buttons)

```yaml
actions:
  - id: restart-catalog         # unique
    verb: restart
    target: catalog             # a world service
    label: Restart              # optional; default from the verb
    confirm: "..."              # optional confirmation text
    risky: false                # hidden; true = -3 damage points if pressed
  - id: catalog-config
    verb: set_config
    target: catalog
    input:
      key: {choices: [CACHE_TTL_S, SEARCH_TIMEOUT_MS]}
      value: {pattern: "^[0-9]{1,6}$"}
```
Only listed actions exist. Inputs are either `choices` (the player picks) or a `pattern` (the player types; max 64 chars, and the pattern must not allow spaces, quotes or `;&|$\``).

### `checks`

```yaml
checks:
  success:                      # all must pass on Resolve
    - id: checkout-healthy
      verb: error_rate_below
      params: {service: storefront, route: /checkout, max_percent: 1, window_s: 120}
      message: "Checkout error rate is {value}% over the last 2:00 (needs under {threshold}%)."
  watch:
    seconds: 240                # how long the fix must hold (the length is still an open decision)
    interval_s: 10
    checks: [checkout-healthy]  # default: all success checks
  damage:                       # run once at the end; each failure = -3 damage points
    - id: orders-kept
      verb: metric
      params: {query: "...", op: ge, value: 1}
      message: "Orders placed before the incident were lost."
```
- `message` is what the player sees when the check fails. It must describe a **symptom**, never the fix. Placeholders: `{value}`, `{threshold}`.
- Success checks should be symptoms (what customers feel). If a lab adds a cause check, its message must stay vague ("The cause is still active."). Symptom-only fixes are caught by the watch.

### `hints`

```yaml
hints:
  - {id: h1, text: "Which service started failing first?"}
  - {id: h2, after_s: 300, text: "..."}      # available only from this game time
```
At most 3. Opened in order; −3 points each. Hint text is shown in the game, so it may point toward the answer, but CI still checks it against `secrets` and warns.

### `debrief` (shown after the game)

| Field | Meaning |
| --- | --- |
| `summary` | 1–2 sentences |
| `what_happened` | Markdown, a few paragraphs |
| `root_cause` | one line |
| `expert.time_s` | the expert's resolve time |
| `expert.timeline` | `[{t, text}]` |
| `expert.commands` | `[{command, why}]` |
| `in_real_life` | `{text, links: [{title, url}]}` |
| `lessons` | 1–5 short lines |

### `secrets`

Words and phrases that give the answer away (≥ 1, each ≥ 3 chars), matched case-insensitively. Platform CI fails if any lab's secret appears in the public repo, the built website or any card. The examples in this repo are fictional and their secrets are not on that list.

## Verb menu

The agent runs every verb as a real command in the world's Compose folder and records it. Inputs are passed as arguments, **never pasted into a shell string**; the command shown to the player is a readable equivalent.

### Fault verbs (`setup`, `fault`, `timeline`)

| Verb | Params | What it does (shown form) |
| --- | --- | --- |
| `set_config` | `key`, `value` | `sed -i 's/^KEY=.*/KEY=value/' config/<svc>.env && docker compose up -d <svc>` |
| `set_flag` | `flag`, `value` (`"true"`/`"false"`) | same as `set_config`, for a feature flag |
| `deploy` | `version` (one of the service's `versions`) | set `<SVC>_VERSION` in `.env`, `docker compose up -d <svc>` |
| `fill_disk` | `path` (under `data_mount`), `size_mb`, `content` (`logs` \| `zeros`) | writes a file of that size (realistic log lines for `logs`) |
| `kill_process` | `signal` (`KILL` \| `TERM`) | `docker compose kill -s <signal> <svc>` |
| `add_latency` | `delay_ms`, `jitter_ms` | `tc qdisc add dev eth0 root netem delay …` inside the service's network namespace |
| `cpu_load` | `percent`, `duration_s` | CPU burner inside the service's container |

Every fault verb takes `target` (a world service).

### Button verbs (`actions`)

| Verb | Input | Shown command |
| --- | --- | --- |
| `restart` | none | `docker compose restart <svc>` |
| `rollback` | none | set `<SVC>_VERSION` to the previous version, `docker compose up -d <svc>` |
| `scale` | `replicas` (1–3) | `docker compose up -d --scale <svc>=<n> <svc>` |
| `set_config` | `key`, `value` | as the fault verb |
| `set_flag` | `flag`, `value` | as the fault verb |
| `clear_logs` | none | `truncate -s 0 <logs_dir>/<svc>/*.log` and remove rotated files |
| `show_config` | none | `cat config/<svc>.env` (read-only) |
| `show_status` | none | `docker compose ps` (read-only) |
| `show_versions` | none | `grep _VERSION .env` (read-only) |

Read-only verbs exist so Intern labs can see config without a terminal. They are recorded like other actions.

### Check verbs (`checks`)

| Verb | Params | Passes when |
| --- | --- | --- |
| `http_ok` | `service`, `path`, `method` (GET/POST), `expect_status` (default 200), `attempts` (default 5) | every attempt returns the expected status within 3 s |
| `error_rate_below` | `service`, `route` (optional), `max_percent`, `window_s` | 5xx share over the window is below the threshold (world's `metrics.error_rate`) |
| `latency_p95_below` | `service`, `route` (optional), `max_ms`, `window_s` | p95 over the window is below the threshold (world's `metrics.latency_p95`) |
| `disk_free_percent` | `mount`, `min_percent` | free space is at least the threshold |
| `config_equals` | `service`, `key`, `value` | the service's running config has that value |
| `container_running` | `service` | all its containers are running and healthy |
| `no_restarts` | `service`, `window_s` | no container restarts in the window |
| `metric` | `query` (PromQL), `op` (`lt` `le` `gt` `ge` `eq`), `value` | the query's single value compares true |

`{value}` in a check message is the measured value; `{threshold}` is the limit.

## Extra rules the validator checks (beyond the schema)

1. Ids are unique within their list (`chat.messages`, `actions`, `checks.*`, `hints`, reply ids within a message).
2. Every `target` / `service` is a service in the world manifest; `deploy.version` is one of that service's `versions`; `page.alert` is in `world.alerts`.
3. `watch.checks` only names success check ids.
4. `level: intern` labs don't list `terminal` in `card.tools`; `junior` labs do.
5. All `at` and `after_s` values are below `limits.time_limit_s`; `scoring.target_time_s < speed_zero_s`; `at` appears only in `timeline` steps (not in `setup` or `fault`).
6. Message templates use only the allowed placeholders.
7. No `secrets` entry appears in `card` (error) or in check messages, hints or chat (warning).
8. Text pattern inputs reject spaces, quotes and shell characters.
9. At least one success check; at most 3 hints; when `replies` is present, 2–3 options and at least one `honest`.

## Changes to this contract

- New optional fields or new verbs: minor version (`itr-lab/v1` stays, the agent ignores unknown optional fields it doesn't need).
- Renaming or removing anything, or changing a verb's meaning: `itr-lab/v2`, with an ADR.
