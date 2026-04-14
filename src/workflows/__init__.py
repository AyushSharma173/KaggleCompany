"""Workflow layer — handlers that react to events emitted by tools and the orchestrator.

See `alignment.md` Part 8 for the design rationale. The workflow layer never
tells an agent what to think — it only controls what fires the agent and
what catches the agent's outputs.
"""
