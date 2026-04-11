"""Tests for the memory system: state_store, strategy, transcripts."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.memory.state_store import StateStore
from src.memory.strategy import StrategyLibrary
from src.memory.transcripts import TranscriptLogger


# --- StateStore tests ---


class TestStateStore:
    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        store.write("agents", "vp-001", {"status": "active", "role": "vp"})
        result = store.read("agents", "vp-001")
        assert result == {"status": "active", "role": "vp"}

    def test_read_missing_returns_default(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        assert store.read("agents", "nonexistent") is None
        assert store.read("agents", "nonexistent", {"default": True}) == {"default": True}

    def test_list_keys(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        store.write("agents", "a1", {"id": "a1"})
        store.write("agents", "a2", {"id": "a2"})
        keys = store.list_keys("agents")
        assert sorted(keys) == ["a1", "a2"]

    def test_list_keys_empty_namespace(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        assert store.list_keys("nonexistent") == []

    def test_read_all(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        store.write("budget", "daily", {"spent": 10.0})
        store.write("budget", "cumulative", {"total": 100.0})
        all_data = store.read_all("budget")
        assert all_data == {
            "daily": {"spent": 10.0},
            "cumulative": {"total": 100.0},
        }

    def test_update(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        store.write("budget", "daily", {"spent": 10.0})
        result = store.update(
            "budget", "daily",
            lambda v: {**v, "spent": v["spent"] + 5.0},
        )
        assert result == {"spent": 15.0}
        assert store.read("budget", "daily") == {"spent": 15.0}

    def test_update_with_default(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        result = store.update(
            "budget", "new_key",
            lambda v: {**v, "count": v.get("count", 0) + 1},
            default={"count": 0},
        )
        assert result == {"count": 1}

    def test_delete(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        store.write("agents", "a1", {"id": "a1"})
        assert store.delete("agents", "a1") is True
        assert store.read("agents", "a1") is None
        assert store.delete("agents", "a1") is False

    def test_exists(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        assert store.exists("agents", "a1") is False
        store.write("agents", "a1", {"id": "a1"})
        assert store.exists("agents", "a1") is True

    def test_atomic_write_creates_valid_json(self, tmp_path: Path) -> None:
        store = StateStore(str(tmp_path / "state"))
        data = {"nested": {"list": [1, 2, 3], "string": "hello"}}
        store.write("test", "complex", data)
        result = store.read("test", "complex")
        assert result == data


# --- StrategyLibrary tests ---


class TestStrategyLibrary:
    def test_load_and_get(self, tmp_path: Path) -> None:
        strategy_dir = tmp_path / "strategies"
        strategy_dir.mkdir()
        (strategy_dir / "tabular-methods.md").write_text("# Tabular\nUse LightGBM.")
        lib = StrategyLibrary(str(strategy_dir))
        lib.load_all()
        content = lib.get("tabular-methods")
        assert content is not None
        assert "LightGBM" in content

    def test_get_missing(self, tmp_path: Path) -> None:
        lib = StrategyLibrary(str(tmp_path / "strategies"))
        lib.load_all()
        assert lib.get("nonexistent") is None

    def test_list_available(self, tmp_path: Path) -> None:
        strategy_dir = tmp_path / "strategies"
        strategy_dir.mkdir()
        (strategy_dir / "a.md").write_text("a")
        (strategy_dir / "b.md").write_text("b")
        lib = StrategyLibrary(str(strategy_dir))
        lib.load_all()
        assert sorted(lib.list_available()) == ["a", "b"]

    def test_search(self, tmp_path: Path) -> None:
        strategy_dir = tmp_path / "strategies"
        strategy_dir.mkdir()
        (strategy_dir / "tabular.md").write_text("Use LightGBM for tabular data")
        (strategy_dir / "nlp.md").write_text("Use transformers for NLP tasks")
        (strategy_dir / "vision.md").write_text("Use CNNs for image classification")
        lib = StrategyLibrary(str(strategy_dir))
        lib.load_all()
        results = lib.search(["tabular", "LightGBM"])
        assert len(results) > 0
        assert results[0][0] == "tabular"

    def test_write(self, tmp_path: Path) -> None:
        lib = StrategyLibrary(str(tmp_path / "strategies"))
        lib.write("new-strategy", "# New\nContent here.")
        assert lib.get("new-strategy") == "# New\nContent here."
        # Also persisted to disk
        path = tmp_path / "strategies" / "new-strategy.md"
        assert path.exists()

    def test_append(self, tmp_path: Path) -> None:
        lib = StrategyLibrary(str(tmp_path / "strategies"))
        lib.write("evolving", "# Strategy\nOriginal content.")
        lib.append("evolving", "## Update\nNew learning.")
        content = lib.get("evolving")
        assert "Original content" in content
        assert "New learning" in content


# --- TranscriptLogger tests ---


class TestTranscriptLogger:
    def test_log_and_read_recent(self, tmp_path: Path) -> None:
        logger = TranscriptLogger(str(tmp_path / "transcripts"))
        logger.log("vp-001", {"role": "assistant", "content": "Hello"})
        logger.log("vp-001", {"role": "user", "content": "World"})
        entries = logger.read_recent("vp-001", n=10)
        assert len(entries) == 2
        assert entries[0]["content"] == "Hello"
        assert entries[1]["content"] == "World"
        # Timestamps added automatically
        assert "timestamp" in entries[0]

    def test_read_recent_limit(self, tmp_path: Path) -> None:
        logger = TranscriptLogger(str(tmp_path / "transcripts"))
        for i in range(10):
            logger.log("agent-1", {"index": i})
        entries = logger.read_recent("agent-1", n=3)
        assert len(entries) == 3
        # Should be the last 3
        assert entries[0]["index"] == 7
        assert entries[2]["index"] == 9

    def test_read_empty(self, tmp_path: Path) -> None:
        logger = TranscriptLogger(str(tmp_path / "transcripts"))
        entries = logger.read_recent("nonexistent", n=10)
        assert entries == []

    def test_summarize_day(self, tmp_path: Path) -> None:
        logger = TranscriptLogger(str(tmp_path / "transcripts"))
        logger.log("agent-1", {"role": "assistant", "content": "a"})
        logger.log("agent-1", {"role": "user", "content": "b"})
        logger.log("agent-1", {"role": "assistant", "content": "c"})
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = logger.summarize_day("agent-1", today)
        assert summary["total_entries"] == 3
        assert summary["type_counts"]["assistant"] == 2
        assert summary["type_counts"]["user"] == 1

    def test_search(self, tmp_path: Path) -> None:
        logger = TranscriptLogger(str(tmp_path / "transcripts"))
        logger.log("agent-1", {"content": "Training LightGBM model"})
        logger.log("agent-1", {"content": "Submitting predictions"})
        logger.log("agent-1", {"content": "LightGBM achieved 0.85 accuracy"})
        results = logger.search("agent-1", "LightGBM")
        assert len(results) == 2
