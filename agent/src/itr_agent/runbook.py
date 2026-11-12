"""Checks on a runbook before the agent accepts it (contracts 1 and 2).

Labs CI already validated the full lab file with the engine. Here the agent checks
only what it needs to run safely on *this* world: right format, right world, every
service and alert exists, every verb is one it can run.
"""

from itr_agent.actions import problems_with_actions
from itr_agent.checks import CHECK_VERBS
from itr_agent.verbs import SUPPORTED_FAULT_VERBS
from itr_agent.world import World

REQUIRED = (
    "format",
    "id",
    "version",
    "world",
    "level",
    "health",
    "scene",
    "fault",
    "page",
    "chat",
    "actions",
    "checks",
)


def validate_runbook(runbook: dict, world: World) -> list[str]:
    problems: list[str] = []
    missing = [key for key in REQUIRED if key not in runbook]
    if missing:
        return [f"missing: {', '.join(missing)}"]
    if runbook["format"] != "itr-lab/v1":
        problems.append(f"format {runbook['format']!r} is not itr-lab/v1")
    if runbook["world"] != world.name:
        problems.append(
            f"lab is for world {runbook['world']!r}, this server runs {world.name!r}"
        )
    if runbook["page"].get("alert") not in world.alerts:
        problems.append(
            f"page alert {runbook['page'].get('alert')!r} doesn't exist in this world"
        )

    steps = runbook.get("setup", []) + runbook["fault"] + runbook.get("timeline", [])
    for step in steps:
        if step.get("target") not in world.services:
            problems.append(
                f"fault step targets unknown service {step.get('target')!r}"
            )
        if step.get("verb") not in SUPPORTED_FAULT_VERBS:
            problems.append(
                f"fault verb {step.get('verb')!r} isn't supported by this agent yet"
            )
    problems += problems_with_actions(runbook["actions"], world)
    problems += problems_with_checks(runbook["checks"], world)
    return problems


def problems_with_checks(checks: dict, world: World) -> list[str]:
    problems = []
    success = checks.get("success", [])
    if not success:
        problems.append("there are no success checks")
    for check in success + checks.get("damage", []):
        name, verb, params = check.get("id"), check.get("verb"), check.get("params", {})
        if verb not in CHECK_VERBS:
            problems.append(f"check {name!r}: verb {verb!r} isn't supported")
            continue
        service = params.get("service")
        if service is not None and service not in world.services:
            problems.append(f"check {name!r} uses unknown service {service!r}")
        elif verb == "http_ok" and not world.services[service].url:
            problems.append(f"check {name!r}: {service} has no URL to call")
    watch = checks.get("watch", {})
    if "seconds" not in watch:
        problems.append("the watch has no length")
    unknown = set(watch.get("checks", [])) - {c.get("id") for c in success}
    if unknown:
        problems.append(f"watch uses unknown checks: {', '.join(sorted(unknown))}")
    return problems
