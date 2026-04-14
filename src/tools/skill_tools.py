"""Skill loading tool: lets agents load procedural guides on demand.

Skills live as markdown files under `skills/` (sibling of `constitutions/`).
An agent calls `load_skill(skill_name="deep-dive")` and gets the file content
back as a tool result. The skill text then lives in that agent's context for
the rest of its task.

This is the "on-demand procedure" half of the alignment layer:
constitutions describe identity (always loaded); skills describe procedures
(loaded only when needed).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.tools import AgentRole, ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger("kaggle-company.tools.skills")


def make_skill_tools(settings: Settings) -> list[ToolDefinition]:
    """Create the skill-loading tool with the skill directory bound."""

    skill_dir = Path(settings.skill_dir)

    async def load_skill(params: dict[str, Any]) -> str:
        """Load a skill file from the skills/ directory and return its content."""
        skill_name = params.get("skill_name", "").strip()
        if not skill_name:
            return "Error: skill_name is required"

        # Disallow path traversal — skill_name must be a bare identifier.
        if "/" in skill_name or "\\" in skill_name or skill_name.startswith("."):
            return f"Error: invalid skill_name '{skill_name}'"

        skill_path = skill_dir / f"{skill_name}.md"
        if not skill_path.exists():
            available = sorted(p.stem for p in skill_dir.glob("*.md")) if skill_dir.exists() else []
            return (
                f"Error: skill '{skill_name}' not found at {skill_path}. "
                f"Available skills: {available}"
            )

        try:
            content = skill_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Failed to read skill %s: %s", skill_path, e)
            return f"Error: failed to read skill '{skill_name}': {e}"

        logger.info("Loaded skill '%s' (%d chars)", skill_name, len(content))
        return content

    async def list_skills(params: dict[str, Any]) -> str:
        """List all available skill files with a short description."""
        if not skill_dir.exists():
            return json.dumps({"skills": [], "note": "No skills directory found."})

        skills = []
        for path in sorted(skill_dir.glob("*.md")):
            # Extract the first non-empty line as description
            first_line = ""
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip().lstrip("# ").strip()
                    if stripped:
                        first_line = stripped
                        break
            except Exception:
                first_line = "(unreadable)"
            skills.append({
                "name": path.stem,
                "description": first_line,
                "size_chars": path.stat().st_size,
            })

        return json.dumps({"skills": skills}, indent=2)

    return [
        ToolDefinition(
            name="list_skills",
            description=(
                "List all available procedural skill guides. Returns skill names "
                "and short descriptions. Use this to discover what skills exist "
                "before deciding which to load. Skills are detailed how-to "
                "documents for multi-step procedures."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=list_skills,
            allowed_roles={
                AgentRole.VP,
                AgentRole.WORKER,
                AgentRole.SUBAGENT,
                AgentRole.CONSOLIDATION,
            },
        ),
        ToolDefinition(
            name="load_skill",
            description=(
                "Load a procedural skill guide from the skills/ directory. "
                "Skills are detailed how-to documents for multi-step procedures "
                "(e.g., 'deep-dive' for conducting a competition deep dive). "
                "Use list_skills first to see what's available. "
                "The full skill content is returned."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill to load (without .md extension), e.g. 'deep-dive'",
                    },
                },
                "required": ["skill_name"],
                "additionalProperties": False,
            },
            handler=load_skill,
            allowed_roles={
                AgentRole.VP,
                AgentRole.WORKER,
                AgentRole.SUBAGENT,
                AgentRole.CONSOLIDATION,
            },
            input_examples=[{"skill_name": "deep-dive"}],
        ),
    ]


def register_skill_tools(registry: ToolRegistry, settings: Settings) -> None:
    """Register all skill-loading tools."""
    for tool in make_skill_tools(settings):
        registry.register(tool)
