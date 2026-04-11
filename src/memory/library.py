"""Generic markdown library loader.

Reusable loader for any directory of .md files: strategies, skills, tasks.
Provides get/list/search/write operations with in-memory caching.
"""

from __future__ import annotations

from pathlib import Path


class MarkdownLibrary:
    """Loads and manages a directory of markdown files."""

    def __init__(self, library_dir: str, name: str = "library") -> None:
        self._dir = Path(library_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._name = name
        self._cache: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self._name

    def load_all(self) -> None:
        """Read all .md files into cache."""
        self._cache.clear()
        for path in self._dir.glob("*.md"):
            self._cache[path.stem] = path.read_text(encoding="utf-8")

    def get(self, name: str) -> str | None:
        """Get item by name (without .md extension)."""
        if name not in self._cache:
            path = self._dir / f"{name}.md"
            if path.exists():
                self._cache[name] = path.read_text(encoding="utf-8")
        return self._cache.get(name)

    def search(self, keywords: list[str]) -> list[tuple[str, str]]:
        """Keyword search. Returns [(name, content)] sorted by relevance."""
        results: list[tuple[str, int, str]] = []
        for name, content in self._cache.items():
            content_lower = content.lower()
            name_lower = name.lower()
            score = sum(
                1 for kw in keywords
                if kw.lower() in content_lower or kw.lower() in name_lower
            )
            if score > 0:
                results.append((name, score, content))
        results.sort(key=lambda x: x[1], reverse=True)
        return [(name, content) for name, _, content in results]

    def list_available(self) -> list[str]:
        """List all item names."""
        return sorted(self._cache.keys())

    def write(self, name: str, content: str) -> None:
        """Write or update an item."""
        path = self._dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")
        self._cache[name] = content

    def exists(self, name: str) -> bool:
        """Check if an item exists."""
        return name in self._cache or (self._dir / f"{name}.md").exists()
