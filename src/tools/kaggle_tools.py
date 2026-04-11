"""Kaggle CLI tools: competition listing, data download, submission management."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from src.tools import AgentRole, ToolDefinition, ToolRegistry

logger = logging.getLogger("kaggle-company.tools.kaggle")


def _run_kaggle_cli(args: list[str]) -> str:
    """Run a kaggle CLI command and return output."""
    try:
        result = subprocess.run(
            ["kaggle"] + args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return f"Kaggle CLI error: {result.stderr.strip()}"
        return result.stdout.strip()
    except FileNotFoundError:
        return "Error: kaggle CLI not found. Install with: pip install kaggle"
    except subprocess.TimeoutExpired:
        return "Error: kaggle CLI timed out after 60 seconds"
    except Exception as e:
        return f"Error running kaggle CLI: {e}"


async def download_data(params: dict[str, Any]) -> str:
    """Download competition data to workspace."""
    slug = params.get("slug", "")
    file_name = params.get("file", "")
    path = params.get("path", ".")

    if not slug:
        return "Error: competition slug is required"

    args = ["competitions", "download", slug, "-p", path]
    if file_name:
        args.extend(["-f", file_name])

    output = _run_kaggle_cli(args)
    return output or f"Downloaded data for {slug} to {path}"


async def submit_prediction(params: dict[str, Any]) -> str:
    """Submit a prediction file to a competition."""
    slug = params.get("slug", "")
    file_path = params.get("file_path", "")
    message = params.get("message", "Automated submission")

    if not slug or not file_path:
        return "Error: slug and file_path are required"

    output = _run_kaggle_cli([
        "competitions", "submit", slug,
        "-f", file_path,
        "-m", message,
    ])
    return output or f"Submitted {file_path} to {slug}"


async def list_submissions(params: dict[str, Any]) -> str:
    """List our submissions for a competition."""
    slug = params.get("slug", "")
    if not slug:
        return "Error: competition slug is required"

    output = _run_kaggle_cli(["competitions", "submissions", slug, "--csv"])
    return output or f"No submissions found for {slug}"


def register_kaggle_tools(registry: ToolRegistry) -> None:
    """Register Kaggle CLI tools (auth-required operations only)."""
    registry.register(ToolDefinition(
        name="download_data",
        description="Download competition data files to the workspace. Requires Kaggle authentication.",
        input_schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Competition slug (from the competition URL)"},
                "file": {"type": "string", "description": "Specific file to download (optional, downloads all if omitted)"},
                "path": {"type": "string", "description": "Download path", "default": "."},
            },
            "required": ["slug"],
            "additionalProperties": False,
        },
        handler=download_data,
        allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
    ))

    registry.register(ToolDefinition(
        name="submit_prediction",
        description="Submit a prediction CSV file to a Kaggle competition. Requires Kaggle authentication.",
        input_schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Competition slug"},
                "file_path": {"type": "string", "description": "Path to prediction file"},
                "message": {"type": "string", "description": "Submission message", "default": "Automated submission"},
            },
            "required": ["slug", "file_path"],
            "additionalProperties": False,
        },
        handler=submit_prediction,
        allowed_roles={AgentRole.WORKER},
    ))

    registry.register(ToolDefinition(
        name="list_submissions",
        description="List our past submissions for a competition with scores. Requires Kaggle authentication.",
        input_schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Competition slug"},
            },
            "required": ["slug"],
            "additionalProperties": False,
        },
        handler=list_submissions,
        allowed_roles={AgentRole.VP, AgentRole.WORKER},
    ))
