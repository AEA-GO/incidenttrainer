"""What the player sees: GET /v1/scene and GET /v1/state (contract 2).

Built from the agent's memory. Never includes hidden fields (reply tones, risky
flags, checks_passing, check ids) or anything else from the runbook that the
player shouldn't see before the end.
"""

from itr_agent.actions import label
from itr_agent.state import Agent

CHARACTERS = [
    {"id": "dana", "name": "Dana", "role": "Product"},
    {"id": "sam", "name": "Sam", "role": "Support"},
]


def scene_clock(starts_at: str, t: int) -> str:
    """In-world time: the scene's start (HH:MM) plus game time."""
    hours, minutes = (int(part) for part in starts_at.split(":"))
    total = (hours * 60 + minutes + t // 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def button(action: dict) -> dict:
    shown = {
        "id": action["id"],
        "verb": action["verb"],
        "target": action["target"],
        "label": label(action),
    }
    if "confirm" in action:
        shown["confirm"] = action["confirm"]
    if "input" in action:
        shown["input"] = action["input"]
    return shown  # `risky` is deliberately left out


def scene_view(agent: Agent) -> dict:
    runbook = agent.runbook
    return {
        "game_id": agent.game_id,
        "lab": {
            "id": runbook["id"],
            "version": runbook["version"],
            "title": runbook.get("title", runbook["id"]),
            "level": runbook["level"],
        },
        "terminal": runbook["level"] == "junior",
        "time_limit_s": agent.time_limit_s,
        "scene": {
            key: runbook["scene"][key]
            for key in ("starts_at", "weekday")
            if key in runbook["scene"]
        },
        "services": [
            {"name": s.name, "kind": s.kind, "calls": list(s.calls), "map": s.map}
            for s in agent.world.services.values()
        ],
        "dashboards": [
            {
                "uid": d["uid"],
                "title": d["title"],
                "path": f"grafana/d/{d['uid']}?kiosk&theme=dark",
            }
            for d in agent.world.dashboards
        ],
        "actions": [button(a) for a in runbook["actions"]],
        "hints_total": len(runbook.get("hints", [])),
        "characters": CHARACTERS,
    }


def state_view(agent: Agent, feed_after: int = 0) -> dict:
    runbook = agent.runbook
    t = agent.game_time()
    scene_changes = [
        {
            "t": None,
            "ago_s": c["ago_s"],
            "service": c["service"],
            "text": c["text"],
            "by": c["by"],
        }
        for c in sorted(runbook["scene"]["recent_changes"], key=lambda c: c["ago_s"])
    ]
    changes = (agent.changes + scene_changes)[:30]
    return {
        "game_id": agent.game_id,
        "state": agent.state,
        "end_reason": agent.end_reason,
        "severity": agent.severity,
        "reopened": agent.reopened,
        "clock": {
            "t": t,
            "time_left_s": None if t is None else max(0, agent.time_limit_s - t),
            "scene_time": None
            if t is None
            else scene_clock(runbook["scene"]["starts_at"], t),
        },
        "page": {
            "fired": agent.page_title is not None,
            "title": agent.page_title,
            "acknowledged": agent.acknowledged_t is not None,
            "acknowledged_t": agent.acknowledged_t,
        },
        "health": {"value": agent.health, "failed_checkouts": agent.failed_checkouts},
        "services": [
            {"name": s.name, "status": s.status, "label": s.label, "detail": s.detail}
            for s in agent.services
        ],
        "alerts": [
            {"name": a.name, "state": a.state, "summary": a.summary}
            for a in agent.alerts
        ],
        "changes": changes,
        "resolve": {
            "attempts": agent.resolve_attempts,
            "wrong": agent.wrong_resolves,
            "watch_ends_in_s": agent.watch_ends_in(),
        },
        "hints": {
            "used": 0,
            "total": len(runbook.get("hints", [])),
            "next_available_in_s": None,
        },
        "status_page": {
            "state": "investigating",
            "last_update_t": None,
            "next_update_at": None,
        },
        # Button results and notices; chat messages come with the chat feature.
        "feed": {"cursor": agent.feed.cursor, "items": agent.feed.since(feed_after)},
    }
