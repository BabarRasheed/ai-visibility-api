"""
app/agents/base.py — BaseAgent.

Provides shared infrastructure for all AI agents:
  - OpenAI client initialisation
  - Structured JSON extraction with regex fallback
  - Retry logic on malformed LLM responses
  - Token tracking
  - Structured logging

Design decision: GPT-4o is used across all three agents because:
  1. Its JSON mode and function-calling capabilities ensure very reliable
     structured output, which is the #1 requirement here.
  2. It handles long system prompts with complex schemas better than
     smaller models.
  3. Temperature=0 gives deterministic, parseable outputs — critical for
     a pipeline that must NOT crash on malformed JSON.
"""

import json
import logging
import re
from typing import Any

from flask import current_app
from openai import OpenAI

logger = logging.getLogger(__name__)


class AgentError(Exception):
    """Raised when an agent cannot recover from a failure."""
    pass


class BaseAgent:
    """
    Shared base for all AI agents.

    Subclasses must implement:
        - system_prompt (property or class attribute)
        - run(**kwargs) → dict
    """

    #: Override in subclasses with the agent's role description
    agent_name: str = "BaseAgent"

    def __init__(self):
        self._client: OpenAI | None = None
        self.total_tokens_used: int = 0

    # ------------------------------------------------------------------ #
    # OpenAI client (lazy-initialised so tests can avoid real API calls)
    # ------------------------------------------------------------------ #

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            api_key = current_app.config.get("OPENAI_API_KEY", "")
            if not api_key:
                raise AgentError(
                    "OPENAI_API_KEY is not configured. Set it in your .env file."
                )
            self._client = OpenAI(api_key=api_key)
        return self._client

    @property
    def model(self) -> str:
        return current_app.config.get("OPENAI_MODEL", "gpt-4o")

    # ------------------------------------------------------------------ #
    # Core LLM call
    # ------------------------------------------------------------------ #

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """
        Make a single chat completion call and return the response text.

        Args:
            system_prompt: Role and output format instructions.
            user_prompt:   Variable-filled task description.
            temperature:   Sampling temperature (low = more deterministic).
            max_tokens:    Response length limit.

        Returns:
            Raw string content from the LLM.

        Raises:
            AgentError: On OpenAI API errors.
        """
        logger.debug(
            "[%s] Calling LLM model=%s temperature=%.1f",
            self.agent_name,
            self.model,
            temperature,
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},  # Force JSON mode
            )
        except Exception as exc:
            raise AgentError(f"OpenAI API call failed: {exc}") from exc

        # Track token usage across the pipeline run
        if response.usage:
            self.total_tokens_used += response.usage.total_tokens
            logger.debug(
                "[%s] Tokens used this call: %d (total: %d)",
                self.agent_name,
                response.usage.total_tokens,
                self.total_tokens_used,
            )

        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------ #
    # JSON extraction helpers
    # ------------------------------------------------------------------ #

    def _parse_json_response(
        self, raw: str, retry_system: str | None = None, retry_user: str | None = None
    ) -> dict | list:
        """
        Safely parse LLM output as JSON.

        Strategy:
          1. Direct json.loads on the raw string.
          2. Regex extraction of the first JSON object/array in the text.
          3. Optional one-shot retry with the original prompts.

        Args:
            raw:          Raw LLM response string.
            retry_system: System prompt to use for retry (optional).
            retry_user:   User prompt to use for retry (optional).

        Returns:
            Parsed Python dict or list.

        Raises:
            AgentError: If all parsing attempts fail.
        """
        # Attempt 1: direct parse
        parsed = self._try_parse(raw)
        if parsed is not None:
            return parsed

        logger.warning("[%s] Direct JSON parse failed, trying regex extraction", self.agent_name)

        # Attempt 2: extract JSON block from text
        extracted = self._extract_json_block(raw)
        if extracted is not None:
            return extracted

        # Attempt 3: retry LLM call once
        if retry_system and retry_user:
            logger.warning("[%s] Retrying LLM call after malformed JSON", self.agent_name)
            raw2 = self._call_llm(retry_system, retry_user)
            parsed2 = self._try_parse(raw2)
            if parsed2 is not None:
                return parsed2
            extracted2 = self._extract_json_block(raw2)
            if extracted2 is not None:
                return extracted2

        raise AgentError(
            f"[{self.agent_name}] Could not parse valid JSON from LLM response. "
            f"Raw snippet: {raw[:300]}"
        )

    @staticmethod
    def _try_parse(text: str) -> Any | None:
        """Attempt json.loads; return None on failure."""
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _extract_json_block(text: str) -> Any | None:
        """Extract the first JSON object or array from a text blob."""
        # Match ```json ... ``` code fences first
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

        # Then try the first { ... } block
        obj_match = re.search(r"(\{.*\})", text, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group(1))
            except json.JSONDecodeError:
                pass

        # Then try the first [ ... ] block
        arr_match = re.search(r"(\[.*\])", text, re.DOTALL)
        if arr_match:
            try:
                return json.loads(arr_match.group(1))
            except json.JSONDecodeError:
                pass

        return None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"
