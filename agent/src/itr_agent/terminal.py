"""The player's terminal in Junior labs (contract 2, runbook; contract 5, paths).

ttyd serves a shell over a WebSocket on port 7681. The gateway forwards
/s/<game_id>/term/ws to it only for labs with a terminal, and only from our own site.
The agent starts ttyd when a Junior runbook arrives (after setup, before the fault) and
stops it when the game ends. Stopping it closes every open terminal: ttyd hangs up its
shells, and on a game server anything the player left running is killed too.

Every WebSocket gets its own new shell (ttyd starts one per connection); at most 2 at a
time, so a page reload can reconnect while the old connection is still closing.

Settings come from environment variables (systemd sets them on the game server):
  ITR_TTYD            the ttyd binary, default "ttyd"
  ITR_TERMINAL_PORT   default 7681
  ITR_TERMINAL_USER   run ttyd and the shells as this user (game servers: oncall);
                      unset = the agent's own user (laptops)
  ITR_TERMINAL_BIND   "lo" (default; only this machine, safe on a laptop) or "all"
                      (game servers: the security group lets only the gateway in)
  ITR_TERMINAL_CWD    the shell's starting folder; default: the world's compose folder
"""

import logging
import os
import pwd
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)
MAX_CLIENTS = 2
PING_S = 30
STOP_WAIT_S = 5
READY_POLLS = 20  # x 0.5 s
MAX_RESTARTS = 3
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class TerminalSettings:
    ttyd: str = "ttyd"
    port: int = 7681
    user: str | None = None
    bind: str = "lo"
    cwd: Path = Path(".")

    @classmethod
    def from_env(cls, env: Mapping[str, str], default_cwd: Path) -> "TerminalSettings":
        bind = env.get("ITR_TERMINAL_BIND", "lo")
        if bind not in ("lo", "all"):
            raise ValueError(f"ITR_TERMINAL_BIND must be lo or all, not {bind!r}")
        return cls(
            ttyd=env.get("ITR_TTYD", "ttyd"),
            port=int(env.get("ITR_TERMINAL_PORT", "7681")),
            user=env.get("ITR_TERMINAL_USER") or None,
            bind=bind,
            cwd=Path(env.get("ITR_TERMINAL_CWD") or default_cwd),
        )


def ttyd_argv(s: TerminalSettings) -> list[str]:
    argv = [
        s.ttyd,
        "--port",
        str(s.port),
        "--writable",  # ttyd 1.7.4+ is read-only without it
        "--max-clients",
        str(MAX_CLIENTS),
        "--ping-interval",
        str(PING_S),
        "--cwd",
        str(s.cwd),
    ]
    if s.bind == "lo":
        argv += ["--interface", "lo"]
    return [*argv, "bash", "--login"]


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def spawn(argv: list[str], s: TerminalSettings) -> Process:
    """Start ttyd as the terminal user, with that user's groups and a clean environment."""
    if s.user is None:
        return subprocess.Popen(argv, cwd=s.cwd, stdin=subprocess.DEVNULL)
    who = pwd.getpwnam(s.user)
    env = {
        "HOME": who.pw_dir,
        "USER": who.pw_name,
        "LOGNAME": who.pw_name,
        "SHELL": who.pw_shell or "/bin/bash",
        "PATH": SAFE_PATH,
        "LANG": "C.UTF-8",
    }
    return subprocess.Popen(
        argv,
        cwd=s.cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        user=who.pw_uid,
        group=who.pw_gid,
        extra_groups=os.getgrouplist(who.pw_name, who.pw_gid),
    )


def kill_user_processes(user: str) -> None:
    """Whatever the player left running (`nohup`, background jobs) ends with the game."""
    subprocess.run(["pkill", "-KILL", "-u", user], check=False, timeout=10)


class Terminal:
    """Starts and stops ttyd. Reports `starting`, `ready`, `failed` or `stopped`."""

    def __init__(
        self,
        settings: TerminalSettings,
        http: Callable[..., int | None],
        sleep: Callable[[float], None],
        report: Callable[[str], None] = lambda _status: None,
        start_process: Callable[[list[str], TerminalSettings], Process] = spawn,
        kill_user: Callable[[str], None] = kill_user_processes,
    ) -> None:
        self.settings, self.http, self.sleep, self.report = (
            settings,
            http,
            sleep,
            report,
        )
        self.start_process, self.kill_user = start_process, kill_user
        self.process: Process | None = None
        self.restarts = 0
        self.stopped = False
        self._lock = threading.RLock()  # the game loop and the end of the game race

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.settings.port}/"

    def start(self) -> bool:
        """Start ttyd and wait until it answers (a few hundred ms)."""
        with self._lock:
            return self._start()

    def _start(self) -> bool:
        if self.stopped:
            return False
        self.report("starting")
        try:
            self.process = self.start_process(ttyd_argv(self.settings), self.settings)
        except (OSError, KeyError, ValueError) as error:
            log.error("terminal: can't start ttyd: %s", error)
            self.report("failed")
            return False
        for _ in range(READY_POLLS):
            if self.process.poll() is not None:
                break
            if self.http(self.url) == 200:
                log.info("terminal: ttyd ready on port %s", self.settings.port)
                self.report("ready")
                return True
            self.sleep(0.5)
        log.error("terminal: ttyd didn't answer on %s", self.url)
        self.report("failed")
        return False

    def keep_alive(self) -> None:
        """Called by the game loop: start ttyd again if it died (e.g. the player killed it)."""
        with self._lock:
            self._keep_alive()

    def _keep_alive(self) -> None:
        if self.stopped or self.process is None or self.process.poll() is None:
            return
        if self.restarts >= MAX_RESTARTS:
            return
        self.restarts += 1
        log.warning(
            "terminal: ttyd exited (%s); restart %d of %d",
            self.process.poll(),
            self.restarts,
            MAX_RESTARTS,
        )
        self._start()

    @staticmethod
    def _other_user(user: str) -> bool:
        """Never kill the agent's own processes (a laptop with ITR_TERMINAL_USER = you)."""
        try:
            return pwd.getpwnam(user).pw_uid != os.getuid()
        except KeyError:
            return False

    def stop(self) -> None:
        """Close every terminal. Safe to call more than once."""
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=STOP_WAIT_S)
        if self.settings.user is not None and self._other_user(self.settings.user):
            try:
                self.kill_user(self.settings.user)
            except (OSError, subprocess.SubprocessError) as error:
                log.warning("terminal: couldn't clean up after the player: %s", error)
        self.report("stopped")
        log.info("terminal: stopped")
