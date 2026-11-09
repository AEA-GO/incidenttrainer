# Data model (DynamoDB)

Version: **v0.1 (draft)**. Changes freely until v1; after v1, only through an ADR.

One table, `itr-main`: string keys `pk` and `sk`, on-demand, TTL attribute `expires_at` (Unix seconds), point-in-time recovery, deletion protection.
Used by the API, the gateway, the cleanup Lambda and our scripts. Event and state formats are in [events.md](events.md) and [game-states.md](game-states.md).

## Items

| What | pk | sk | Main attributes | TTL |
| --- | --- | --- | --- | --- |
| Invite code | `CODE#<code>` | `META` | `active` (bool), `games_total` (6), `games_used`, `note` (who got it; ours only), `created_at` | none (kept until we switch it off) |
| Games played on a day | `CODE#<code>` | `DAY#<YYYY-MM-DD>` | `count` | 2 days |
| A game on a code | `CODE#<code>` | `GAME#<started_at>#<game_id>` | `lab_id`, `state`, `end_reason`, `score`, `grade` | 30 days |
| Game | `GAME#<game_id>` | `META` | `code`, `lab_id`, `lab_version`, `level`, `nickname`, `state`, `end_reason`, `instance_id`, `private_ip`, `created_at`, `ready_at`, `ended_at`, `last_seen_at`, `reopened` | 30 days |
| Event | `GAME#<game_id>` | `EV#<by>#<seq, 6 digits>` | the event (`at`, `t`, `type`, `data`) | 30 days |
| Result | `GAME#<game_id>` | `RESULT` | the output of `score()` | 30 days |
| Feedback | `GAME#<game_id>` | `FEEDBACK` | `replay` (1-5), `stuck`, `fake`, `at` | 30 days |
| Active game | `ACTIVE` | `GAME#<game_id>` | `instance_id`, `private_ip`, `state` | 1 day (safety) |
| Running servers | `COUNTER` | `RUNNING` | `count` | none |
| Games this month | `COUNTER` | `MONTH#<YYYY-MM>` | `games` | none |
| Limits | `CONFIG` | `LIMITS` | `max_running` (5), `games_per_code` (6), `games_per_day` (3), `max_games_month` (e.g. 400 for $15), `boot_timeout_s` (180), `idle_timeout_s` (300) | none |
| Waitlist | `WAITLIST` | `EMAIL#<lower-case email>` | `consent` (true), `consent_text_version`, `at` | none (deleted after the launch emails) |

Notes:
- `game_id`: 22 random URL-safe characters (128 bits), so ids can't be guessed.
- `code`: 8 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (no 0/O, 1/I/L), shown as `K7QM-4XPA`; stored without the dash.
- Dates for the per-day limit use **Europe/Kyiv**.
- Event keys include the writer and its counter, so the gateway can copy the same event twice without creating a duplicate.
- Limits live in the table, so we can change them (e.g. raise the monthly cap) without a deploy.

## Access patterns

| Who | Question | How |
| --- | --- | --- |
| API | Is this code valid, how many games left? | GetItem `CODE#c / META` + GetItem `CODE#c / DAY#today` |
| API | Which labs did this code finish? | Query `pk = CODE#c, sk begins_with GAME#` |
| API | Start a game | transaction (below) |
| API, gateway | Everything about game g | Query `pk = GAME#g` |
| API | Status of game g | GetItem `GAME#g / META` |
| Gateway | Which server is game g on? | GetItem `GAME#g / META` (cached ~60 s) |
| Gateway | Which games should I watch? | Query `pk = ACTIVE` (at most 5 items) |
| Gateway | Save the agent's events | BatchWriteItem `GAME#g / EV#...` (same key = overwrite, no duplicates) |
| Cleanup | Which game owns this server? | EC2 tag `session` → GetItem `GAME#g / META` |
| Cleanup | Fix the counter | Update `COUNTER / RUNNING` to the real number of tagged servers |
| Scripts | Beta numbers | Query each game, or a scan (small table, fine for scripts only) |

## Start shift: one transaction

`TransactWriteItems`, all or nothing. If any condition fails, nothing is written and the API maps the failed item to an error:

| # | Write | Condition | Error if it fails |
| --- | --- | --- | --- |
| 1 | `CODE#c / META`: `games_used + 1` | `active = true AND games_used < games_total` | `code_disabled` or `out_of_games` |
| 2 | `CODE#c / DAY#today`: `count + 1` | `attribute_not_exists(count) OR count < games_per_day` | `daily_limit` |
| 3 | `COUNTER / RUNNING`: `count + 1` | `count < max_running` | `all_servers_busy` |
| 4 | `COUNTER / MONTH#m`: `games + 1` | `games < max_games_month` | `budget_exhausted` |
| 5 | Put `GAME#g / META` (state `booting`) | `attribute_not_exists(pk)` | (never: new random id) |
| 6 | Put `ACTIVE / GAME#g` and `CODE#c / GAME#...` | none | |

Then the API starts the server. If the start fails, it **gives everything back** in one more transaction (`games_used − 1`, day `count − 1`, running `count − 1`, month `games − 1`) and ends the game as `failed`.

## Ending a game: one condition

Every part that ends a game writes `state = ended` with the condition `state <> :ended`, so a game ends exactly once. On ending:
- write `ended` (and `end_reason`) on `GAME#g / META` and on `CODE#c / GAME#...`
- delete `ACTIVE / GAME#g`
- `COUNTER / RUNNING`: `count − 1` (only by the part that won the condition)
- a `failed` game also gives back the code's game (`games_used − 1`, day `count − 1`)

The cleanup Lambda sets `COUNTER / RUNNING` to the real number of servers every 5 minutes, so a missed decrement fixes itself.

## Privacy

Stored about a player: invite code, nickname, game events, feedback text. Waitlist: email and consent. No IP addresses, no browser data. Game data is deleted by TTL after 30 days.

## Open

- Whether the waitlist belongs in this table or a separate one (it has no TTL and different rules).
- A GSI is not needed for the MVP; add one only if a new question can't be answered with the keys above.
