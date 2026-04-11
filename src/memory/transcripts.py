"""Append-only JSONL transcript logging per agent per day."""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TranscriptLogger:
    """Append-only JSONL logs organized by agent and date."""

    def __init__(self, transcript_dir: str) -> None:
        self._dir = Path(transcript_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def clear_all(self) -> None:
        """Delete all transcript files for a clean restart. Preserves LOGGING.md."""
        if self._dir.exists():
            for child in self._dir.iterdir():
                if child.name == "LOGGING.md":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                elif child.is_file():
                    child.unlink()

    def _agent_dir(self, agent_id: str) -> Path:
        d = self._dir / agent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _today_file(self, agent_id: str) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._agent_dir(agent_id) / f"{date_str}.jsonl"

    def log(self, agent_id: str, entry: dict[str, Any]) -> None:
        """Append a timestamped entry to agent's daily log file."""
        entry_with_ts = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **entry,
        }
        path = self._today_file(agent_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry_with_ts, default=str) + "\n")

    def read_recent(self, agent_id: str, n: int = 50) -> list[dict[str, Any]]:
        """Read last N entries for agent, from most recent files."""
        agent_dir = self._agent_dir(agent_id)
        files = sorted(agent_dir.glob("*.jsonl"), reverse=True)
        entries: list[dict[str, Any]] = []
        for path in files:
            if len(entries) >= n:
                break
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            for line in reversed(lines):
                if len(entries) >= n:
                    break
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        entries.reverse()
        return entries

    def read_day(self, agent_id: str, date: str) -> list[dict[str, Any]]:
        """Read all entries for a specific date (YYYY-MM-DD format)."""
        path = self._agent_dir(agent_id) / f"{date}.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def summarize_day(self, agent_id: str, date: str) -> dict[str, Any]:
        """Return stats for a day: entry count, types, etc."""
        entries = self.read_day(agent_id, date)
        type_counts: dict[str, int] = {}
        for entry in entries:
            entry_type = entry.get("type", entry.get("role", "unknown"))
            type_counts[entry_type] = type_counts.get(entry_type, 0) + 1
        return {
            "date": date,
            "agent_id": agent_id,
            "total_entries": len(entries),
            "type_counts": type_counts,
        }

    def search(self, agent_id: str, query: str, max_results: int = 20) -> list[dict[str, Any]]:
        """Simple text search through agent's transcripts."""
        agent_dir = self._agent_dir(agent_id)
        results: list[dict[str, Any]] = []
        query_lower = query.lower()
        for path in sorted(agent_dir.glob("*.jsonl"), reverse=True):
            for line in path.read_text(encoding="utf-8").strip().split("\n"):
                if len(results) >= max_results:
                    return results
                if line.strip() and query_lower in line.lower():
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return results
