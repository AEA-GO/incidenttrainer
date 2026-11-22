"""Contract 6: score(scoring, events) -> result (docs/contracts/score.md, v0.2).

A pure function: no network, database, clock or randomness. The same game always gets the
same score, so old games can be rescored after a rule change.
"""

import itertools
from decimal import ROUND_HALF_UP, Decimal

VERSION = "score-v0.2"
WEIGHTS = {
    "intern": {"speed": 30, "correctness": 40, "damage": 20, "communication": 10},
    "junior": {"speed": 25, "correctness": 35, "damage": 20, "communication": 20},
}
COMM_SHARES = {
    "comm.ack": 0.15,
    "comm.first_update": 0.20,
    "comm.cadence": 0.15,
    "comm.fields": 0.10,
    "comm.promises": 0.15,
    "comm.truthful": 0.10,
    "comm.honest": 0.15,
}
ACK_S, FIRST_UPDATE_S, CADENCE_S, PROMISE_GRACE_S = 120, 300, 600, 60
RISKY_POINTS = DAMAGE_FOUND_POINTS = HINT_POINTS = 3
WRONG_RESOLVE_POINTS, WRONG_RESOLVE_MAX = 2, 6


def one_decimal(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def whole(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def mss(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def empty_fields_reason(updates: list[dict]) -> str:
    """Name what was missing, e.g. "Missing in status updates: next update time (3 of 3)."."""
    missing = {"who is affected": 0, "what you're doing": 0, "next update time": 0}
    for update in updates:
        data = update.get("data") or {}
        missing["who is affected"] += not str(data.get("affected") or "").strip()
        missing["what you're doing"] += not str(data.get("doing") or "").strip()
        missing["next update time"] += data.get("next_update_t") is None
    parts = [f"{name} ({n} of {len(updates)})" for name, n in missing.items() if n]
    if not parts:
        return "Some status updates left fields empty."
    return "Missing in status updates: " + ", ".join(parts) + "."


def _order(events: list[dict]) -> list[dict]:
    """Diary order (events.md): by `at`, then `by`, then `seq`."""
    return sorted(
        events,
        key=lambda e: (
            str(e.get("at", "")),
            str(e.get("by", "")),
            int(e.get("seq", 0)),
        ),
    )


def score(scoring: dict, events: list[dict]) -> dict:
    events = _order(events)

    def of(kind: str) -> list[dict]:
        return [e for e in events if e.get("type") == kind]

    ended = next(iter(of("ended")), None)  # rule 8: the first `ended` wins
    if ended is None:
        return {"version": VERSION, "scored": False, "why": "not_ended"}
    end = ended.get("data") or {}
    if end.get("reason") == "failed":
        return {"version": VERSION, "scored": False, "why": "failed"}
    paged = next(iter(of("paged")), None)
    if paged is None or str(paged.get("at", "")) > str(ended.get("at", "")):
        return {"version": VERSION, "scored": False, "why": "ended_before_page"}

    level = scoring.get("level", "intern")
    weights = WEIGHTS.get(level, WEIGHTS["intern"])
    target = float(scoring["target_time_s"])
    zero = float(scoring.get("speed_zero_s", 1800))
    health_full = float(scoring.get("health_full", 75))
    end_reason = end.get("reason")
    times = [e["t"] for e in events if e.get("t") is not None]
    end_t = (
        ended.get("t") if ended.get("t") is not None else (max(times) if times else 0)
    )

    # ----- facts ------------------------------------------------------------------------
    passes = [i for i, e in enumerate(events) if e.get("type") == "resolve_passed"]
    reopens = [i for i, e in enumerate(events) if e.get("type") == "reopened"]
    last_pass = events[passes[-1]] if passes else None
    reopened_after_last = bool(passes) and any(i > passes[-1] for i in reopens)
    resolve_t = last_pass["t"] if last_pass and not reopened_after_last else None
    reopened = bool(reopens)
    limit = next(iter(of("time_limit")), None)
    cut_short = bool(limit and (limit.get("data") or {}).get("watch_cut_short"))
    fix_held = end_reason == "resolved" or (
        end_reason == "unresolved"
        and cut_short
        and bool(last_pass)
        and not reopened_after_last
    )
    hints = len(of("hint_used"))
    wrong = len(of("resolve_failed"))
    final_health = end.get("final_health")
    if final_health is None:
        health = of("health")
        final_health = (
            (health[-1].get("data") or {}).get("value", 100) if health else 100
        )

    lost: list[dict] = []

    def lose(rule: str, points: float, reason: str) -> None:
        if points > 0:
            lost.append({"rule": rule, "points": one_decimal(points), "reason": reason})

    # ----- speed ------------------------------------------------------------------------
    w = weights["speed"]
    if fix_held and resolve_t is not None:
        speed = w * clamp((zero - resolve_t) / (zero - target))
        lose(
            "speed",
            w - speed,
            f"Resolved at {mss(resolve_t)}. Full points by {mss(target)}, none after {mss(zero)}.",
        )
    else:
        speed = 0.0
        lose("speed", w, "Not resolved, so no speed points.")

    # ----- correctness ------------------------------------------------------------------
    w = weights["correctness"]
    if end_reason == "resolved" and not reopened:
        correct = float(w)
    elif end_reason == "resolved":
        correct = w / 2
        lose("correctness", w / 2, "The first fix didn't hold: the incident reopened.")
    elif end_reason == "unresolved" and fix_held:
        correct = w / 2
        lose("correctness", w / 2, "Time ran out during the watch.")
    else:
        correct = 0.0
        lose("correctness", w, "The incident wasn't resolved.")

    # ----- damage -----------------------------------------------------------------------
    w = weights["damage"]
    damage = w * clamp(float(final_health) / health_full)
    lose(
        "damage.health",
        w - damage,
        f"{int(final_health)}% of the health bar left. Full points at {int(health_full)}% or more.",
    )
    risky = [e for e in of("action") if (e.get("data") or {}).get("risky")]
    if risky:
        take = min(RISKY_POINTS * len(risky), damage)
        names = ", ".join(
            f"{e['data'].get('verb')} {e['data'].get('target')}" for e in risky
        )
        lose("damage.risky", take, f"{len(risky)} risky action(s): {names}")
        damage -= take
    for found in of("damage_found"):
        take = min(DAMAGE_FOUND_POINTS, damage)
        lose(
            "damage.checks",
            take,
            str((found.get("data") or {}).get("message", "Damage found at the end.")),
        )
        damage -= take

    # ----- communication ----------------------------------------------------------------
    w = weights["communication"]
    got: dict[str, float] = {}
    acks = of("acknowledged")
    ack_t = acks[0]["t"] if acks else None
    updates = [e for e in of("status_update") if e.get("t") is not None]
    replies = of("chat_reply")

    got["comm.ack"] = 1.0 if ack_t is not None and ack_t <= ACK_S else 0.0
    ack_reason = (
        f"Acknowledged at {mss(ack_t)}. Target: within {mss(ACK_S)}."
        if ack_t is not None
        else "The page was never acknowledged."
    )
    reasons = {"comm.ack": ack_reason}
    if not updates:
        for rule in (
            "comm.first_update",
            "comm.cadence",
            "comm.fields",
            "comm.promises",
            "comm.truthful",
        ):
            got[rule] = 0.0
            reasons[rule] = "No status update was posted."
    else:
        first_gap = updates[0]["t"] - (ack_t if ack_t is not None else 0)
        got["comm.first_update"] = (
            1.0 if ack_t is not None and first_gap <= FIRST_UPDATE_S else 0.0
        )
        reasons["comm.first_update"] = (
            f"First status update came {mss(first_gap)} after acknowledging. Target: within {mss(FIRST_UPDATE_S)}."
        )
        stamps = [u["t"] for u in updates] + [end_t]
        gaps = [b - a for a, b in itertools.pairwise(stamps)]
        long_gaps = [g for g in gaps if g > CADENCE_S]
        got["comm.cadence"] = (len(gaps) - len(long_gaps)) / len(gaps) if gaps else 1.0
        reasons["comm.cadence"] = (
            f"{len(long_gaps)} gap(s) between updates were longer than {mss(CADENCE_S)} (longest {mss(max(gaps))})."
        )
        full = [u for u in updates if (u.get("data") or {}).get("fields_filled") == 4]
        got["comm.fields"] = len(full) / len(updates)
        reasons["comm.fields"] = empty_fields_reason(updates)
        kept, due, broken = 0, 0, ""
        for index, update in enumerate(updates):
            promised = (update.get("data") or {}).get("next_update_t")
            if promised is None or promised >= end_t:
                continue  # no promise, or it fell due after the end
            later = [u["t"] for u in updates[index + 1 :]]
            if any(t <= promised + PROMISE_GRACE_S for t in later):
                kept, due = kept + 1, due + 1
            elif (
                promised + PROMISE_GRACE_S < end_t
            ):  # its minute of grace ran out before the end
                due += 1
                after = next((t for t in later), None)
                broken = broken or (
                    f"Promised an update by {mss(promised)}, posted it at {mss(after)}."
                    if after is not None
                    else f"Promised an update by {mss(promised)}, none came."
                )
        got["comm.promises"] = kept / due if due else 1.0
        reasons["comm.promises"] = broken or "A promised update came late."
        lies = [
            u
            for u in updates
            if (u.get("data") or {}).get("page_state") in ("monitoring", "resolved")
            and (u.get("data") or {}).get("checks_passing") is False
        ]
        got["comm.truthful"] = (len(updates) - len(lies)) / len(updates)
        claimed = (lies[0]["data"]["page_state"].capitalize()) if lies else ""
        reasons["comm.truthful"] = (
            f"Status page said {claimed} while checkout was still failing."
        )
    honest = [r for r in replies if (r.get("data") or {}).get("tone") == "honest"]
    got["comm.honest"] = len(honest) / len(replies) if replies else 0.0
    reasons["comm.honest"] = (
        f"{len(replies) - len(honest)} of {len(replies)} replies to colleagues weren't honest."
        if replies
        else "No replies to colleagues."
    )
    communication = 0.0
    for rule, share in COMM_SHARES.items():
        points = w * share * got[rule]
        communication += points
        lose(rule, w * share - points, reasons[rule])

    # ----- penalties --------------------------------------------------------------------
    hint_penalty = HINT_POINTS * hints
    wrong_penalty = min(WRONG_RESOLVE_POINTS * wrong, WRONG_RESOLVE_MAX)
    lose(
        "penalty.hints",
        hint_penalty,
        f"{hints} hint(s) used, {HINT_POINTS} points each.",
    )
    lose(
        "penalty.wrong_resolve",
        wrong_penalty,
        f"{wrong} wrong Resolve(s), {WRONG_RESOLVE_POINTS} points each (at most {WRONG_RESOLVE_MAX}).",
    )

    categories = {
        "speed": {"max": weights["speed"], "got": one_decimal(speed)},
        "correctness": {"max": weights["correctness"], "got": one_decimal(correct)},
        "damage": {"max": weights["damage"], "got": one_decimal(max(damage, 0.0))},
        "communication": {
            "max": weights["communication"],
            "got": one_decimal(communication),
        },
    }
    total = sum(c["got"] for c in categories.values()) - hint_penalty - wrong_penalty
    points = max(0, min(100, whole(total)))

    grade = (
        "S"
        if points >= 90
        else "A"
        if points >= 80
        else "B"
        if points >= 65
        else "C"
        if points >= 50
        else "D"
    )
    caps: list[str] = []
    if grade == "S" and (reopened or hints or end_reason != "resolved"):
        caps = [
            c
            for c, hit in (
                ("reopened", reopened),
                ("hints_used", hints),
                ("not_resolved", end_reason != "resolved"),
            )
            if hit
        ]
        grade = "A"
    if reopened and grade in ("S", "A"):
        grade = "B"
        caps = sorted(set(caps) | {"reopened"})

    return {
        "version": VERSION,
        "scored": True,
        "score": points,
        "grade": grade,
        "grade_caps": caps,
        "categories": categories,
        "penalties": {"hints": hint_penalty, "wrong_resolves": wrong_penalty},
        "lost": lost,
        "facts": {
            "end_reason": end_reason,
            "resolve_t": resolve_t,
            "final_health": final_health,
            "reopened": reopened,
            "hints_used": hints,
            "wrong_resolves": wrong,
        },
    }
