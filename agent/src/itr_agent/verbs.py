"""Verbs: real commands that change or show the world (contract 1, "Verb menu").

Rules:
- Every input is checked against a strict pattern before use.
- Commands run as argument lists (never through a shell), so no input can inject one.
- `command` in a result is a readable equivalent for the player, not what runs.

Fault verbs supported so far: set_config, set_flag.
Button verbs: restart, rollback, scale, set_config, set_flag, clear_logs,
show_config, show_status, show_versions.
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
SECRET_KEY = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL", re.IGNORECASE)
MAX_OUTPUT = 4000
COMMAND_TIMEOUT_S = 30
MAX_REPLICAS = 3

SUPPORTED_FAULT_VERBS = {"set_config", "set_flag"}
BUTTON_VERBS = {
    "restart",
    "rollback",
    "scale",
    "set_config",
    "set_flag",
    "clear_logs",
    "show_config",
    "show_status",
    "show_versions",
}
READ_ONLY_VERBS = {"show_config", "show_status", "show_versions"}


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


def read_env(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def env_value(path: Path, key: str) -> str | None:
    for line in read_env(path):
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return None


def write_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value in an env file, keeping every other line. Atomic replace."""
    lines = read_env(path)
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


def version_key(service: str) -> str:
    return f"{service.upper().replace('-', '_')}_VERSION"


class Verbs:
    def __init__(self, world: World, runner: CommandRunner) -> None:
        self.world = world
        self.runner = runner

    # ----- helpers -----------------------------------------------------------

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
        return VerbResult(
            verb,
            target,
            shown,
            done.exit_code,
            output[:MAX_OUTPUT],
            len(output) > MAX_OUTPUT,
            done.duration_ms,
        )

    def _local(self, verb: str, target: str, shown: str, output: str) -> VerbResult:
        """A verb done in Python (no command to run): reading or truncating files."""
        return self._result(verb, target, shown, CommandResult(0, output, 0))

    # ----- changing verbs ----------------------------------------------------

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

    def restart(self, service: str) -> VerbResult:
        service = self._service(service)
        shown = f"docker compose restart {service}"
        return self._result(
            "restart", service, shown, self._compose("restart", service)
        )

    def previous_version(self, service: str) -> tuple[str, str]:
        """(current, previous) image version of a service, from the versions file."""
        versions = self.world.services[service].versions
        current = env_value(self.world.versions_path, version_key(service))
        if current not in versions or versions.index(current) == 0:
            raise VerbError(f"{service} has no earlier version to roll back to")
        return current, versions[versions.index(current) - 1]

    def rollback(self, service: str) -> VerbResult:
        service = self._service(service)
        current, previous = self.previous_version(service)
        key = version_key(service)
        write_env_value(self.world.versions_path, key, previous)
        shown = (
            f"sed -i 's/^{key}=.*/{key}={previous}/' {self.world.versions_file}"
            f" && docker compose up -d {service}"
        )
        done = self._compose("up", "-d", service)
        note = f"{service}: {current} -> {previous}\n"
        return self._result(
            "rollback",
            service,
            shown,
            CommandResult(done.exit_code, note + done.output, done.duration_ms),
        )

    def scale(self, service: str, replicas: str) -> VerbResult:
        service = self._service(service)
        if not self.world.services[service].scalable:
            raise VerbError(f"{service} can't be scaled")
        if not replicas.isdigit() or not 1 <= int(replicas) <= MAX_REPLICAS:
            raise VerbError(f"replicas must be 1 to {MAX_REPLICAS}")
        scale = f"{service}={int(replicas)}"
        shown = f"docker compose up -d --scale {scale} {service}"
        return self._result(
            "scale",
            service,
            shown,
            self._compose("up", "-d", "--scale", scale, service),
        )

    def clear_logs(self, service: str) -> VerbResult:
        service = self._service(service)
        folder = self.world.logs_dir / service
        shown = f"truncate -s 0 {folder}/*.log && rm -f {folder}/*.log.*"
        if not folder.is_dir():
            return self._local("clear_logs", service, shown, "no log files found\n")
        lines = []
        for path in sorted(folder.glob("*.log*")):
            if not path.is_file():
                continue
            size_mb = path.stat().st_size / 1_000_000
            if path.suffix == ".log":
                path.write_bytes(b"")
                lines.append(f"truncated {path.name} ({size_mb:.1f} MB)")
            else:
                path.unlink()
                lines.append(f"removed {path.name} ({size_mb:.1f} MB)")
        output = "\n".join(lines) + "\n" if lines else "no log files found\n"
        return self._local("clear_logs", service, shown, output)

    # ----- read-only verbs ---------------------------------------------------

    def show_config(self, service: str) -> VerbResult:
        service = self._service(service)
        relative = f"{self.world.config_dir}/{service}.env"
        lines = []
        for line in read_env(self.world.config_file(service)):
            key, _, value = line.partition("=")
            lines.append(f"{key}=***" if value and SECRET_KEY.search(key) else line)
        return self._local(
            "show_config", service, f"cat {relative}", "\n".join(lines) + "\n"
        )

    def show_status(self, service: str) -> VerbResult:
        service = self._service(service)
        return self._result(
            "show_status", service, "docker compose ps", self._compose("ps")
        )

    def show_versions(self, service: str) -> VerbResult:
        service = self._service(service)
        lines = [
            line for line in read_env(self.world.versions_path) if "_VERSION=" in line
        ]
        output = "\n".join(lines) + "\n" if lines else "no versions file\n"
        shown = f"grep _VERSION {self.world.versions_file}"
        return self._local("show_versions", service, shown, output)

    # ----- dispatch ------------------------------------------------------------

    def run_fault_step(self, step: dict) -> VerbResult:
        verb, target, params = step["verb"], step["target"], step.get("params", {})
        if verb == "set_config":
            return self.set_config(target, params["key"], params["value"])
        if verb == "set_flag":
            return self.set_flag(target, params["flag"], params["value"])
        raise VerbError(f"fault verb not supported yet: {verb}")

    def run_button(self, verb: str, target: str, values: dict[str, str]) -> VerbResult:
        """Run a button verb with already-checked input values."""
        simple = {
            "restart": self.restart,
            "rollback": self.rollback,
            "clear_logs": self.clear_logs,
            "show_config": self.show_config,
            "show_status": self.show_status,
            "show_versions": self.show_versions,
        }
        if verb in simple:
            return simple[verb](target)
        if verb == "set_config":
            return self.set_config(target, values["key"], values["value"])
        if verb == "set_flag":
            return self.set_flag(target, values["flag"], values["value"])
        if verb == "scale":
            return self.scale(target, values["replicas"])
        raise VerbError(f"button verb not supported: {verb}")
