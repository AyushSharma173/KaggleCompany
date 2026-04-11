"""Atomic JSON state persistence with namespace/key organization.

State files are stored as: {state_dir}/{namespace}/{key}.json
Writes use temp-file-then-rename for atomicity.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable


class StateStore:
    """Thread-safe, atomic JSON state persistence."""

    def __init__(self, state_dir: str) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def clear_all(self) -> None:
        """Delete all state files for a clean restart."""
        if self._dir.exists():
            for child in self._dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                elif child.is_file():
                    child.unlink()

    def _path(self, namespace: str, key: str) -> Path:
        ns_dir = self._dir / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir / f"{key}.json"

    def read(self, namespace: str, key: str, default: Any = None) -> Any:
        """Read a value. Returns default if not found."""
        path = self._path(namespace, key)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    def write(self, namespace: str, key: str, value: Any) -> None:
        """Atomic write: write to temp file, fsync, then rename."""
        path = self._path(namespace, key)
        data = json.dumps(value, indent=2, default=str)

        # Write to temp file in same directory (so rename is atomic on same filesystem)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp_path, path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def delete(self, namespace: str, key: str) -> bool:
        """Delete a key. Returns True if it existed."""
        path = self._path(namespace, key)
        if path.exists():
            path.unlink()
            return True
        return False

    def update(self, namespace: str, key: str, updater: Callable[[Any], Any], default: Any = None) -> Any:
        """Atomic read-modify-write with file locking."""
        path = self._path(namespace, key)

        # Ensure file exists for locking
        if not path.exists():
            self.write(namespace, key, default)

        with open(path, "r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                content = f.read()
                current = json.loads(content) if content.strip() else default
                updated = updater(current)
                self.write(namespace, key, updated)
                return updated
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def list_keys(self, namespace: str) -> list[str]:
        """List all keys in a namespace."""
        ns_dir = self._dir / namespace
        if not ns_dir.exists():
            return []
        return [p.stem for p in ns_dir.glob("*.json")]

    def read_all(self, namespace: str) -> dict[str, Any]:
        """Read entire namespace as {key: value} dict."""
        result = {}
        for key in self.list_keys(namespace):
            result[key] = self.read(namespace, key)
        return result

    def exists(self, namespace: str, key: str) -> bool:
        """Check if a key exists."""
        return self._path(namespace, key).exists()
