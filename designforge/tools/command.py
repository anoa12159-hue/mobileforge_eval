"""Shell command execution used by Build agent.

All commands are executed with the Node-resolved environment from node_env,
ensuring the correct Node.js version is used regardless of the user's default.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from dataclasses import dataclass

from designforge.node_env import resolve_node_env

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    return_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.return_code == 0

    @property
    def output(self) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(f"[stderr] {self.stderr.strip()}")
        return "\n".join(parts) or "(no output)"


def run_command(cmd: str, cwd: str | None = None, timeout: int = 120) -> CommandResult:
    logger.info("running: %s (cwd=%s)", cmd, cwd)
    env = resolve_node_env()
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return CommandResult(-1, "", f"command timed out after {timeout}s")
        return CommandResult(proc.returncode, stdout, stderr)
    except Exception as e:
        return CommandResult(-1, "", str(e))
