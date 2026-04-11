"""Strategy library: loads and searches strategy markdown files."""

from __future__ import annotations

from pathlib import Path


class StrategyLibrary:
    """Loads strategy markdown files from a directory and provides retrieval."""

    def __init__(self, strategy_dir: str) -> None:
        self._dir = Path(strategy_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, str] = {}

    def load_all(self) -> None:
        """Read all .md files into cache."""
        self._cache.clear()
        for path in self._dir.glob("*.md"):
            self._cache[path.stem] = path.read_text(encoding="utf-8")

    def get(self, name: str) -> str | None:
        """Get strategy by name (without .md extension)."""
        if name not in self._cache:
            # Try loading from disk in case it was added after load_all
            path = self._dir / f"{name}.md"
            if path.exists():
                self._cache[name] = path.read_text(encoding="utf-8")
        return self._cache.get(name)

    def search(self, keywords: list[str]) -> list[tuple[str, str]]:
        """Keyword search across strategy docs. Returns [(name, content)] sorted by relevance."""
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
        """List all strategy names."""
        return list(self._cache.keys())

    def write(self, name: str, content: str) -> None:
        """Write or update a strategy file."""
        path = self._dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")
        self._cache[name] = content

    def append(self, name: str, addition: str) -> None:
        """Append content to an existing strategy file."""
        current = self.get(name) or ""
        self.write(name, current + "\n" + addition)
