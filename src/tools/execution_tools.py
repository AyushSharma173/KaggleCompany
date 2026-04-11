"""Execution tools: run Python code, read/write files within workspace."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.tools import AgentRole, ToolDefinition, ToolRegistry

logger = logging.getLogger("kaggle-company.tools.exec")

# Max output size from subprocess
MAX_OUTPUT_CHARS = 10_000


def _resolve_workspace_path(workspace: str, relative_path: str) -> Path | None:
    """Resolve a path within workspace, blocking path traversal."""
    workspace_path = Path(workspace).resolve()
    target = (workspace_path / relative_path).resolve()
    try:
        target.relative_to(workspace_path)
        return target
    except ValueError:
        return None


def make_execution_tools(workspace_base: str) -> list[ToolDefinition]:
    """Create execution tools bound to a workspace base directory."""

    async def run_python(params: dict[str, Any]) -> str:
        """Execute Python code in a subprocess."""
        code = params.get("code", "")
        timeout_s = min(params.get("timeout_s", 120), 300)  # Max 5 minutes
        agent_workspace = params.get("_workspace", workspace_base)

        if not code.strip():
            return "Error: no code provided"

        workspace = Path(agent_workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        # Write code to temp file in workspace
        script_path = workspace / "_run_script.py"
        script_path.write_text(code, encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
                env={**os.environ, "PYTHONPATH": str(workspace)},
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
            stdout_str = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
            stderr_str = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

            result = f"Exit code: {proc.returncode}\n"
            if stdout_str:
                result += f"STDOUT:\n{stdout_str}\n"
            if stderr_str:
                result += f"STDERR:\n{stderr_str}\n"
            return result.strip()
        except asyncio.TimeoutError:
            return f"Error: Script timed out after {timeout_s} seconds"
        except Exception as e:
            return f"Error running Python: {e}"
        finally:
            if script_path.exists():
                script_path.unlink()

    async def run_shell(params: dict[str, Any]) -> str:
        """Execute a shell command in the workspace."""
        command = params.get("command", "")
        timeout_s = min(params.get("timeout_s", 60), 300)
        agent_workspace = params.get("_workspace", workspace_base)

        if not command.strip():
            return "Error: no command provided"

        # Block dangerous commands
        dangerous = ["rm -rf /", "dd if=", "mkfs", "> /dev/", ":(){ :|:& };:"]
        for d in dangerous:
            if d in command:
                return f"Error: blocked dangerous command pattern: {d}"

        workspace = Path(agent_workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
            stdout_str = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
            stderr_str = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

            result = f"Exit code: {proc.returncode}\n"
            if stdout_str:
                result += f"STDOUT:\n{stdout_str}\n"
            if stderr_str:
                result += f"STDERR:\n{stderr_str}\n"
            return result.strip()
        except asyncio.TimeoutError:
            return f"Error: Command timed out after {timeout_s} seconds"
        except Exception as e:
            return f"Error: {e}"

    async def read_file(params: dict[str, Any]) -> str:
        """Read a file from workspace."""
        rel_path = params.get("path", "")
        max_lines = params.get("max_lines", 500)
        agent_workspace = params.get("_workspace", workspace_base)

        if not rel_path:
            return "Error: path is required"

        target = _resolve_workspace_path(agent_workspace, rel_path)
        if target is None:
            return "Error: path traversal blocked"
        if not target.exists():
            return f"Error: file not found: {rel_path}"
        if target.is_dir():
            return f"Error: {rel_path} is a directory, use list_files"

        try:
            lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
            if len(lines) > max_lines:
                content = "\n".join(lines[:max_lines])
                return f"{content}\n... (truncated, {len(lines)} total lines)"
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading file: {e}"

    async def write_file(params: dict[str, Any]) -> str:
        """Write content to a file in workspace."""
        rel_path = params.get("path", "")
        content = params.get("content", "")
        agent_workspace = params.get("_workspace", workspace_base)

        if not rel_path:
            return "Error: path is required"

        target = _resolve_workspace_path(agent_workspace, rel_path)
        if target is None:
            return "Error: path traversal blocked"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Written {len(content)} bytes to {rel_path}"
        except Exception as e:
            return f"Error writing file: {e}"

    async def edit_file(params: dict[str, Any]) -> str:
        """Edit a file by replacing text."""
        rel_path = params.get("path", "")
        old_text = params.get("old_text", "")
        new_text = params.get("new_text", "")
        agent_workspace = params.get("_workspace", workspace_base)

        if not rel_path or not old_text:
            return "Error: path and old_text are required"

        target = _resolve_workspace_path(agent_workspace, rel_path)
        if target is None:
            return "Error: path traversal blocked"
        if not target.exists():
            return f"Error: file not found: {rel_path}"

        try:
            content = target.read_text(encoding="utf-8")
            if old_text not in content:
                return "Error: old_text not found in file"
            count = content.count(old_text)
            new_content = content.replace(old_text, new_text)
            target.write_text(new_content, encoding="utf-8")
            return f"Replaced {count} occurrence(s) in {rel_path}"
        except Exception as e:
            return f"Error editing file: {e}"

    async def list_files(params: dict[str, Any]) -> str:
        """List files in workspace directory."""
        rel_path = params.get("path", ".")
        agent_workspace = params.get("_workspace", workspace_base)

        target = _resolve_workspace_path(agent_workspace, rel_path)
        if target is None:
            return "Error: path traversal blocked"
        if not target.exists():
            return f"Error: directory not found: {rel_path}"
        if not target.is_dir():
            return f"Error: {rel_path} is not a directory"

        try:
            entries = []
            for item in sorted(target.iterdir()):
                if item.name.startswith("."):
                    continue
                prefix = "[DIR]" if item.is_dir() else f"[{item.stat().st_size:,}B]"
                entries.append(f"  {prefix} {item.name}")
            if not entries:
                return f"{rel_path}/ (empty)"
            return f"{rel_path}/\n" + "\n".join(entries)
        except Exception as e:
            return f"Error listing files: {e}"

    return [
        ToolDefinition(
            name="run_python",
            description="Execute Python code in a subprocess within the agent's workspace. Returns stdout+stderr.",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "timeout_s": {"type": "integer", "description": "Timeout in seconds (max 300)", "default": 120},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            handler=run_python,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="run_shell",
            description="Execute a shell command in the workspace. Use for pip install, data processing, etc.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout_s": {"type": "integer", "description": "Timeout in seconds (max 300)", "default": 60},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=run_shell,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="read_file",
            description="Read a file from the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within workspace"},
                    "max_lines": {"type": "integer", "description": "Max lines to read", "default": 500},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_file,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="write_file",
            description="Write content to a file in the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within workspace"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=write_file,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="edit_file",
            description="Edit a file by replacing old_text with new_text.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within workspace"},
                    "old_text": {"type": "string", "description": "Text to find"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            handler=edit_file,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="list_files",
            description="List files and directories in the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path", "default": "."},
                },
                "additionalProperties": False,
            },
            handler=list_files,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
    ]


def register_execution_tools(registry: ToolRegistry, workspace_base: str) -> None:
    """Register all execution tools."""
    for tool in make_execution_tools(workspace_base):
        registry.register(tool)
