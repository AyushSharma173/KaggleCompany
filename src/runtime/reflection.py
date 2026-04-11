"""Post-action reflection step.

After an agent completes a tool-use sequence, a brief reflection
helps maintain self-awareness across turns.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger("kaggle-company.reflection")

_REFLECTION_PROMPT = "Briefly reflect: what did you accomplish, what's next, any concerns? (2-3 sentences)"


class Reflector:
    """Generates brief post-session reflections."""

    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model
        self._prompt = _REFLECTION_PROMPT

    async def reflect(
        self,
        messages: list[dict[str, Any]],
        last_response_text: str,
    ) -> str:
        """Generate a brief reflection on the recent work."""
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=300,
                messages=[
                    {"role": "user", "content": f"Here's what you just did:\n{last_response_text}\n\n{self._prompt}"},
                ],
            )
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
            return text
        except Exception as e:
            logger.warning("Reflection failed: %s", e)
            return f"[Reflection skipped due to error: {e}]"
