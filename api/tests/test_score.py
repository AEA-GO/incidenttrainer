"""Contract 6 (docs/contracts/score.md): the worked examples and the required edge cases.

The two example diaries are read straight from docs/contracts/events.md, so the contract
and the code can't drift apart silently.
"""

import copy
import json
import random
import re
from pathlib import Path

import pytest
from itr_api.score import score

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
SCORING = {"level": "intern", "target_time_s": 300}


def diaries() -> list[list[dict]]:
    text = (CONTRACTS / "events.md").read_text()
    blocks = re.findall(r"```jsonl\n(.*?)```", text, re.DOTALL)
    return [
        [json.loads(line) for line in block.splitlines() if line.strip()]
        for block in blocks
    ]


GOOD, STRUGGLING = diaries()


def ev(seq, t, type_, data=None, by="agent", at=None):
    return {
        "seq": seq,
        "at": at or f"2026-12-08T18:{seq // 60:02d}:{seq % 60:02d}.000Z",
        "t": t,
        "by": by,
        "type": type_,
        "data": data or {},
    }


def base(*middle, end=None):
    """A minimal game: page at t=0, ack at 30, then `middle`, then `ended`."""
    events = [ev(1, 0, "paged", {"source": "alert"}), ev(2, 30, "acknowledged")]
    events += [
        dict(e, seq=3 + i, at=f"2026-12-08T18:10:{i:02d}.000Z")
        for i, e in enumerate(middle)
    ]
    events.append(
        ev(
            99,
            end.get("t", 600) if end else 600,
            "ended",
            {
                k: v
                for k, v in (end or {"reason": "resolved", "final_health": 90}).items()
                if k != "t"
            },
            at="2026-12-08T18:59:00.000Z",
        )
    )
    return events


def test_example_1_good_player_98_s():
    result = score(SCORING, GOOD)
    assert result["scored"] and result["score"] == 98 and result["grade"] == "S"
    assert {k: v["got"] for k, v in result["categories"].items()} == {
        "speed": 28.0,
        "correctness": 40.0,
        "damage": 20.0,
        "communication": 10.0,
    }
    assert result["lost"] == [
        {
            "rule": "speed",
            "points": 2.0,
            "reason": "Resolved at 6:40. Full points by 5:00, none after 30:00.",
        }
    ]
    assert result["grade_caps"] == []
    assert result["facts"] == {
        "end_reason": "resolved",
        "resolve_t": 400,
        "final_health": 81,
        "reopened": False,
        "hints_used": 0,
        "wrong_resolves": 0,
    }


def test_example_2_struggling_player_2_d():
    result = score(SCORING, STRUGGLING)
    assert result["score"] == 2 and result["grade"] == "D"
    got = {k: v["got"] for k, v in result["categories"].items()}
    assert got == {
        "speed": 0.0,
        "correctness": 0.0,
        "damage": 7.1,
        "communication": 0.0,
    }
    assert result["penalties"] == {"hints": 3, "wrong_resolves": 2}
    rules = {line["rule"] for line in result["lost"]}
    assert {
        "speed",
        "correctness",
        "damage.health",
        "damage.risky",
        "comm.ack",
        "comm.honest",
    } <= rules
    assert {"penalty.hints", "penalty.wrong_resolve"} <= rules
    assert result["grade_caps"] == []  # the reopen cap to B doesn't matter at D


def test_every_lost_point_has_a_reason():
    for diary in (GOOD, STRUGGLING):
        result = score(SCORING, diary)
        assert all(line["reason"] and line["points"] > 0 for line in result["lost"])
        earned = sum(c["got"] for c in result["categories"].values())
        maximum = sum(c["max"] for c in result["categories"].values())
        category_lost = sum(
            line["points"]
            for line in result["lost"]
            if not line["rule"].startswith("penalty.")
        )
        assert abs(maximum - earned - category_lost) < 0.15


def test_same_input_same_output_and_input_untouched():
    diary = copy.deepcopy(STRUGGLING)
    assert score(SCORING, diary) == score(SCORING, diary)
    assert diary == STRUGGLING


def test_unknown_events_and_order_change_nothing():
    noisy = copy.deepcopy(GOOD) + [
        ev(50, 100, "something_new", {"x": 1}),
        ev(51, None, "view", {"panel": "logs"}),
    ]
    random.Random(7).shuffle(noisy)
    assert score(SCORING, noisy) == score(SCORING, GOOD)


def test_not_scored_cases():
    assert score(SCORING, [])["why"] == "not_ended"
    failed = [ev(1, None, "ended", {"reason": "failed"}, by="api")]
    assert score(SCORING, failed) == {
        "version": "score-v0.2",
        "scored": False,
        "why": "failed",
    }
    early = [
        ev(1, None, "fault_started"),
        ev(2, None, "ended", {"reason": "abandoned"}, by="gateway"),
    ]
    assert score(SCORING, early)["why"] == "ended_before_page"


def test_two_damage_found_and_damage_not_below_zero():
    result = score(
        SCORING,
        base(
            ev(
                0,
                100,
                "damage_found",
                {"check_id": "a", "message": "Orders were lost."},
            ),
            ev(
                0,
                101,
                "damage_found",
                {"check_id": "b", "message": "Payments were lost."},
            ),
            end={"reason": "resolved", "final_health": 75},
        ),
    )
    assert result["categories"]["damage"]["got"] == 14.0
    low = score(
        SCORING,
        base(
            *[
                ev(
                    0,
                    100 + i,
                    "action",
                    {"verb": "restart", "target": "orders", "risky": True},
                )
                for i in range(5)
            ],
            ev(
                0,
                120,
                "damage_found",
                {"check_id": "a", "message": "Orders were lost."},
            ),
            end={"reason": "resolved", "final_health": 30},
        ),
    )
    assert low["categories"]["damage"]["got"] == 0.0


def test_resolved_after_reopen_half_correctness_grade_at_most_b():
    events = base(
        ev(0, 60, "resolve_passed", {"attempt": 1}),
        ev(0, 100, "reopened", {"severity": "SEV1"}),
        ev(0, 150, "resolve_passed", {"attempt": 2}),
        ev(
            0,
            160,
            "status_update",
            {
                "page_state": "monitoring",
                "fields_filled": 4,
                "checks_passing": True,
                "next_update_t": None,
            },
        ),
        ev(0, 170, "chat_reply", {"tone": "honest"}),
        end={"reason": "resolved", "final_health": 100, "t": 400},
    )
    result = score(SCORING, events)
    assert result["categories"]["correctness"]["got"] == 20.0
    assert result["facts"]["resolve_t"] == 150
    assert result["grade"] in ("B", "C", "D") and result["score"] >= 65
    assert result["grade_caps"] == ["reopened"]


def test_time_limit_during_a_holding_watch():
    events = base(
        ev(0, 2300, "resolve_passed", {"attempt": 1}),
        ev(0, 2400, "time_limit", {"watch_cut_short": True}),
        end={"reason": "unresolved", "final_health": 80, "t": 2400},
    )
    result = score(SCORING, events)
    assert result["categories"]["correctness"]["got"] == 20.0
    assert result["categories"]["speed"]["got"] == 0.0  # resolved after 30:00
    assert any(
        line["reason"] == "Time ran out during the watch." for line in result["lost"]
    )


def test_abandoned_with_ended_t_null_uses_the_largest_t():
    events = base(
        ev(0, 700, "health", {"value": 60}),
        end={"reason": "abandoned", "final_health": None, "t": None},
    )
    result = score(SCORING, events)
    assert result["scored"] and result["facts"]["final_health"] == 60
    assert result["categories"]["correctness"]["got"] == 0.0


def test_no_chat_replies_is_zero_honesty():
    result = score(SCORING, base())
    assert any(
        line["rule"] == "comm.honest" and line["reason"] == "No replies to colleagues."
        for line in result["lost"]
    )


def test_promise_due_after_the_end_is_not_counted_and_a_broken_one_is():
    fine = base(
        ev(
            0,
            60,
            "status_update",
            {
                "page_state": "investigating",
                "fields_filled": 4,
                "checks_passing": False,
                "next_update_t": 900,
            },
        ),
        end={"reason": "resolved", "final_health": 90, "t": 600},
    )
    assert not any(
        line["rule"] == "comm.promises" for line in score(SCORING, fine)["lost"]
    )
    broken = base(
        ev(
            0,
            60,
            "status_update",
            {
                "page_state": "investigating",
                "fields_filled": 4,
                "checks_passing": False,
                "next_update_t": 200,
            },
        ),
        ev(
            0,
            400,
            "status_update",
            {
                "page_state": "monitoring",
                "fields_filled": 4,
                "checks_passing": True,
                "next_update_t": None,
            },
        ),
        end={"reason": "resolved", "final_health": 90, "t": 600},
    )
    lines = [
        line
        for line in score(SCORING, broken)["lost"]
        if line["rule"] == "comm.promises"
    ]
    assert (
        lines and lines[0]["reason"] == "Promised an update by 3:20, posted it at 6:40."
    )


def test_four_wrong_resolves_still_minus_six():
    events = base(
        *[ev(0, 50 + i, "resolve_failed", {"attempt": i + 1}) for i in range(4)]
    )
    assert score(SCORING, events)["penalties"]["wrong_resolves"] == 6


def test_ninety_plus_with_a_hint_is_a():
    events = base(
        ev(0, 40, "chat_reply", {"tone": "honest"}),
        ev(
            0,
            50,
            "status_update",
            {
                "page_state": "investigating",
                "fields_filled": 4,
                "checks_passing": False,
                "next_update_t": None,
            },
        ),
        ev(0, 60, "hint_used", {"n": 1}),
        ev(0, 100, "resolve_passed", {"attempt": 1}),
        end={"reason": "resolved", "final_health": 100, "t": 340},
    )
    result = score(SCORING, events)
    assert result["score"] >= 90 and result["grade"] == "A"
    assert result["grade_caps"] == ["hints_used"]


@pytest.mark.parametrize(
    ("level", "maxima"), [("intern", (30, 40, 20, 10)), ("junior", (25, 35, 20, 20))]
)
def test_weights_by_level(level, maxima):
    result = score({"level": level, "target_time_s": 300}, GOOD)
    assert tuple(c["max"] for c in result["categories"].values()) == maxima


def test_empty_fields_reason_names_the_missing_field():
    events = base(
        ev(
            0,
            60,
            "status_update",
            {
                "affected": "all",
                "doing": "x",
                "next_update_t": None,
                "page_state": "investigating",
                "fields_filled": 3,
                "checks_passing": False,
            },
        ),
        ev(
            0,
            200,
            "status_update",
            {
                "affected": "",
                "doing": "y",
                "next_update_t": None,
                "page_state": "identified",
                "fields_filled": 2,
                "checks_passing": False,
            },
        ),
    )
    line = next(x for x in score(SCORING, events)["lost"] if x["rule"] == "comm.fields")
    assert (
        line["reason"]
        == "Missing in status updates: who is affected (1 of 2), next update time (2 of 2)."
    )


def test_no_status_update_explains_each_rule_once():
    result = score(SCORING, base(ev(0, 290, "resolve_passed")))
    comm = [line for line in result["lost"] if line["rule"].startswith("comm.")]
    reasons = [line["reason"] for line in comm]
    assert len(reasons) == len(set(reasons)) >= 5
    assert any("within 5:00" in r for r in reasons)
