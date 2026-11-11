"""Buttons (contract 2, POST /v1/actions): input checks, labels, recent-change text.

The agent (state.py) adds the rules around them: one action at a time, at most
30 a minute, and the same Idempotency-Key never runs a command twice.
"""

import re

from itr_agent.errors import AgentError
from itr_agent.verbs import BUTTON_VERBS, READ_ONLY_VERBS, VerbResult
from itr_agent.world import World

LABELS = {
    "restart": "Restart",
    "rollback": "Roll back",
    "scale": "Scale",
    "set_config": "Edit config",
    "set_flag": "Toggle flag",
    "clear_logs": "Clear logs",
    "show_config": "Show config",
    "show_status": "Show status",
    "show_versions": "Show versions",
}
# Whatever a lab's pattern says, typed input never contains spaces, quotes or
# shell characters (contract 1).
SAFE_TYPED = re.compile(r"^[A-Za-z0-9._:/-]{0,64}$")


def label(action: dict) -> str:
    return action.get("label", LABELS.get(action["verb"], action["verb"]))


def check_input(action: dict, given: dict[str, str]) -> dict[str, str]:
    """Return the checked input values, or raise 422 bad_input."""
    spec = action.get("input", {})
    unexpected = sorted(set(given) - set(spec))
    if unexpected:
        raise AgentError(
            422, "bad_input", f"Unexpected input: {', '.join(unexpected)}."
        )
    values = {}
    for name, rule in spec.items():
        if name not in given:
            raise AgentError(422, "bad_input", f"Missing input: {name}.")
        value = given[name]
        if "choices" in rule:
            if value not in rule["choices"]:
                choices = ", ".join(rule["choices"])
                raise AgentError(422, "bad_input", f"{name} must be one of: {choices}.")
        elif not SAFE_TYPED.fullmatch(value) or not re.fullmatch(
            rule["pattern"], value
        ):
            raise AgentError(422, "bad_input", f"{name} has the wrong format.")
        values[name] = value
    return values


def change_text(result: VerbResult, values: dict[str, str]) -> str | None:
    """One line for "Recent changes", or None for read-only buttons."""
    if result.verb in READ_ONLY_VERBS or not result.ok:
        return None
    if result.verb == "set_flag":
        return f"flag {values['flag']} -> {values['value']}"
    if result.verb == "set_config":
        return f"{values['key']} -> {values['value']}"
    if result.verb == "scale":
        return f"scaled to {values['replicas']}"
    if result.verb == "rollback":
        # First output line is "<service>: <current> -> <previous>".
        return "rolled back " + result.output.splitlines()[0].split(": ", 1)[-1]
    return {"restart": "restarted", "clear_logs": "logs cleared"}.get(result.verb)


def problems_with_actions(actions: list[dict], world: World) -> list[str]:
    """Checks for PUT runbook: every button can really run on this world."""
    problems = []
    for action in actions:
        verb, target, name = action.get("verb"), action.get("target"), action.get("id")
        if verb not in BUTTON_VERBS:
            problems.append(f"action {name!r}: verb {verb!r} isn't supported")
            continue
        service = world.services.get(target)
        if service is None:
            problems.append(f"action {name!r} targets unknown service")
        elif verb == "rollback" and len(service.versions) < 2:
            problems.append(f"action {name!r}: {target} has no earlier version")
        elif verb == "scale" and not service.scalable:
            problems.append(f"action {name!r}: {target} can't be scaled")
    return problems
