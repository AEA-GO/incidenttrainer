"""Verbs: real commands that change the world (contract 1, "Verb menu").

Rules:
- Every input is checked against a strict pattern before use.
- Commands run as argument lists (never through a shell), so no input can inject one.
- The `command` returned is a readable equivalent for the player, not what runs.

Step 3 supports the fault verbs set_config and set_flag. The others come later.
"""

import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from itr_agent.world import World

KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
VALUE = re.compile(r"^[A-Za-z0-9._:/-]{0,64}$")
MAX_OUTPUT = 4000
COMMAND_TIMEOUT_S = 30


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str
    duration_ms: int


class CommandRunner(Protocol):
    def run(self, argv: list[str], cwd: Path, timeout_s: int) -> CommandResult: ...


class SubprocessRunner:
    """Runs real commands on the server."""

    def run(self, argv: list[str], cwd: Path, timeout_s: int) -> CommandResult:
        start = time.perf_counter()
        try:
            done = subprocess.run(
                argv,
                check=False,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            code, output = done.returncode, done.stdout + done.stderr
        except subprocess.TimeoutExpired:
            code, output = 124, f"timed out after {timeout_s} s"
        except FileNotFoundError:
            code, output = 127, f"{argv[0]}: command not found"
        return CommandResult(code, output, int((time.perf_counter() - start) * 1000))


@dataclass(frozen=True)
class VerbResult:
    verb: str
    target: str
    command: str
    exit_code: int
    output: str
    truncated: bool
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class VerbError(ValueError):
    """The verb or its input isn't allowed. Nothing was changed."""


def write_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value in an env file, keeping every other line. Atomic replace."""
    lines = path.read_text().splitlines() if path.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


class Verbs:
    def __init__(self, world: World, runner: CommandRunner) -> None:
        self.world = world
        self.runner = runner

    def _service(self, name: str) -> str:
        if name not in self.world.services:
            raise VerbError(f"unknown service: {name}")
        return name

    def _compose(self, *args: str) -> CommandResult:
        argv = ["docker", "compose", "-p", self.world.project, *args]
        return self.runner.run(argv, self.world.compose_dir, COMMAND_TIMEOUT_S)

    def _result(
        self, verb: str, target: str, shown: str, done: CommandResult
    ) -> VerbResult:
        output = done.output
        truncated = len(output) > MAX_OUTPUT
        return VerbResult(
            verb,
            target,
            shown,
            done.exit_code,
            output[:MAX_OUTPUT],
            truncated,
            done.duration_ms,
        )

    def set_config(
        self, service: str, key: str, value: str, verb: str = "set_config"
    ) -> VerbResult:
        service = self._service(service)
        if not KEY.match(key):
            raise VerbError(f"bad key: {key!r}")
        if not VALUE.match(value):
            raise VerbError(f"bad value for {key}")
        env_file = self.world.config_file(service)
        if not env_file.exists():
            raise VerbError(f"{service} has no config file")
        write_env_value(env_file, key, value)
        relative = f"{self.world.config_dir}/{service}.env"
        shown = (
            f"sed -i 's/^{key}=.*/{key}={value}/' {relative}"
            f" && docker compose up -d {service}"
        )
        return self._result(verb, service, shown, self._compose("up", "-d", service))

    def set_flag(self, service: str, flag: str, value: str) -> VerbResult:
        if value not in ("true", "false"):
            raise VerbError("a flag is true or false")
        return self.set_config(service, flag, value, verb="set_flag")

    def run_fault_step(self, step: dict) -> VerbResult:
        verb, target, params = step["verb"], step["target"], step.get("params", {})
        if verb == "set_config":
            return self.set_config(target, params["key"], params["value"])
        if verb == "set_flag":
            return self.set_flag(target, params["flag"], params["value"])
        raise VerbError(f"fault verb not supported yet: {verb}")


SUPPORTED_FAULT_VERBS = {"set_config", "set_flag"}
