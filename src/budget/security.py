"""Security controls: workspace isolation, rate limiting, deletion protection."""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("kaggle-company.security")

# Files/directories that agents cannot modify
PROTECTED_PATHS = {
    "src/",
    "constitutions/",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    ".env",
}

# Files agents cannot delete (append-only protection)
APPEND_ONLY_DIRS = {
    "transcripts/",
    "state/",
}


class SecurityController:
    """Deterministic security enforcement for agent file access."""

    def __init__(self, workspace_base: str) -> None:
        self._workspace_base = Path(workspace_base).resolve()

    def validate_file_access(self, agent_id: str, file_path: str, write: bool = False) -> bool:
        """Check if an agent can access a file path.

        Agents can only write within their workspace directory.
        Read access is less restricted but still logged.
        """
        resolved = Path(file_path).resolve()

        if write:
            # Must be within the agent's workspace
            agent_workspace = self._workspace_base / agent_id
            try:
                resolved.relative_to(agent_workspace)
                return True
            except ValueError:
                logger.warning(
                    "SECURITY: Agent %s attempted write outside workspace: %s",
                    agent_id, file_path,
                )
                return False

        # Read access: block protected paths
        for protected in PROTECTED_PATHS:
            try:
                resolved.relative_to(Path(protected).resolve())
                logger.warning(
                    "SECURITY: Agent %s attempted read of protected path: %s",
                    agent_id, file_path,
                )
                return False
            except ValueError:
                continue

        return True

    def validate_deletion(self, file_path: str) -> bool:
        """Check if a file can be deleted. Blocks deletion in append-only dirs."""
        resolved = Path(file_path).resolve()
        for protected_dir in APPEND_ONLY_DIRS:
            try:
                resolved.relative_to(Path(protected_dir).resolve())
                logger.warning("SECURITY: Blocked deletion in append-only dir: %s", file_path)
                return False
            except ValueError:
                continue
        return True

    def create_workspace(self, agent_id: str) -> Path:
        """Create an isolated workspace for an agent."""
        workspace = self._workspace_base / agent_id
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace


class RateLimiter:
    """Sliding window rate limiter."""

    def __init__(self, max_calls: int, window_seconds: float = 60.0) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: list[float] = []

    def check(self) -> bool:
        """Return True if within rate limit."""
        now = time.time()
        # Remove old entries outside the window
        self._calls = [t for t in self._calls if now - t < self._window]
        return len(self._calls) < self._max_calls

    def record(self) -> None:
        """Record a call."""
        self._calls.append(time.time())

    def try_acquire(self) -> bool:
        """Check and record in one step. Returns True if allowed."""
        if self.check():
            self.record()
            return True
        return False
