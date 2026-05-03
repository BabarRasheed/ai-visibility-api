"""
app/agents/scoring.py — Agent 2: VisibilityScoringAgent.

Responsibility:
    For each discovered query, determine:
      1. Estimated search volume  — from DataForSEO API (real data)
      2. Competitive difficulty   — from DataForSEO or LLM estimate
      3. Domain visibility        — LLM-simulated AI answer check
      4. Visibility position      — rank if visible, else null
      5. Opportunity score        — our custom formula

Design notes on model selection:
    - Visibility checking uses GPT-4o with temperature=0 because we need
      a DETERMINISTIC yes/no decision, not creative output.
    - DataForSEO provides real search volume and difficulty data.
    - If DataForSEO credentials are absent, we fall back to LLM-estimated
      values with a clear warning logged — the pipeline does not crash.

Opportunity score formula (documented in README and utils/scoring.py):
    score = 0.35 * norm_volume + 0.30 * (1 - difficulty/100) + 0.35 * visibility_gap
    where visibility_gap = 1.0 if not visible, 0.0 if visible
"""

import logging
from typing import Any

from .base import BaseAgent, AgentError
from app.utils.scoring import compute_opportunity_score
from app.utils.dataforseo import DataForSEOClient, DataForSEOError

logger = logging.getLogger(__name__)


# System prompt for visibility check

VISIBILITY_SYSTEM_PROMPT = """You are an AI search analyst who simulates how AI assistants
(ChatGPT, Perplexity, Claude) respond to search queries.

Your task is to evaluate whether a specific domain would appear prominently in an
AI-generated answer for a given search query.

## Output Requirements

Return ONLY a valid JSON object with this exact schema:

{
  "domain_visible": true or false,
  "visibility_position": null or integer (1-10, where 1 = prominently featured),
  "reasoning": "one sentence explaining the visibility assessment",
  "competitive_difficulty_estimate": integer (0-100, where 0=easy, 100=very hard)
}

## Scoring guidance
- domain_visible = true if the domain is likely to be mentioned/recommended in an AI answer
- visibility_position = 1-3 for prominently featured, 4-10 for mentioned but not primary
- competitive_difficulty = how hard it is for any new domain to appear for this query
  (consider: number of established players, query specificity, commercial intent)

Be realistic and critical — most domains are NOT visible for most queries.
Return valid JSON only, no extra text.
"""


class VisibilityScoringAgent(BaseAgent):
    """
    Agent 2 — Score each query for search volume, difficulty, and domain visibility.

    Input:  query_text (str) + domain (str) + profile context
    Output: dict with scoring data for one query
    """

    agent_name = "VisibilityScoringAgent"

    def __init__(self):
        super().__init__()
        self._dataforseo: DataForSEOClient | None = None

    @property
    def dataforseo(self) -> DataForSEOClient:
        """Lazy-initialise the DataForSEO client."""
        if self._dataforseo is None:
            from flask import current_app
            login = current_app.config.get("DATAFORSEO_LOGIN", "")
            password = current_app.config.get("DATAFORSEO_PASSWORD", "")
            self._dataforseo = DataForSEOClient(login=login, password=password)
        return self._dataforseo

    def run(self, query_text: str, domain: str, industry: str = "") -> dict:
        """
        Score a single query for the given domain.

        Args:
            query_text: The natural-language query to score.
            domain:     The target domain to check visibility for.
            industry:   Industry context for better LLM estimates.

        Returns:
            {
                "query_text": str,
                "estimated_search_volume": int,
                "competitive_difficulty": int,    # 0–100
                "domain_visible": bool,
                "visibility_position": int | None,
                "opportunity_score": float,       # 0.0–1.0
            }
        """
        logger.info(
            "[VisibilityScoringAgent] Scoring query='%s' for domain=%s",
            query_text[:60],
            domain,
        )

        # Step 1: Get real search volume + difficulty from DataForSEO
        search_volume, difficulty = self._get_search_data(query_text)

        # Step 2: Check domain visibility via LLM
        visibility_data = self._check_visibility(query_text, domain, industry)

        domain_visible = visibility_data.get("domain_visible", False)
        visibility_position = visibility_data.get("visibility_position")

        # Use DataForSEO difficulty if we got it; otherwise use LLM estimate
        if difficulty is None:
            difficulty = int(visibility_data.get("competitive_difficulty_estimate", 50))
            logger.debug(
                "[VisibilityScoringAgent] Using LLM difficulty estimate: %d", difficulty
            )

        # Step 3: Compute opportunity score
        opportunity_score = compute_opportunity_score(
            search_volume=search_volume or 0,
            difficulty=difficulty,
            domain_visible=domain_visible,
        )

        result = {
            "query_text": query_text,
            "estimated_search_volume": search_volume or 0,
            "competitive_difficulty": min(100, max(0, difficulty)),
            "domain_visible": domain_visible,
            "visibility_position": visibility_position if domain_visible else None,
            "opportunity_score": opportunity_score,
        }

        logger.info(
            "[VisibilityScoringAgent] Scored: volume=%d difficulty=%d visible=%s score=%.3f",
            result["estimated_search_volume"],
            result["competitive_difficulty"],
            result["domain_visible"],
            result["opportunity_score"],
        )

        return result

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _get_search_data(self, query_text: str) -> tuple[int | None, int | None]:
        """
        Fetch search volume and difficulty from DataForSEO.

        Returns (None, None) if DataForSEO is not configured or fails.
        The caller must handle the None case gracefully.
        """
        try:
            volume, difficulty = self.dataforseo.get_keyword_data(query_text)
            return volume, difficulty
        except DataForSEOError as exc:
            logger.warning(
                "[VisibilityScoringAgent] DataForSEO unavailable for '%s': %s — using LLM fallback",
                query_text[:60],
                exc,
            )
            return None, None

    def _check_visibility(self, query_text: str, domain: str, industry: str) -> dict:
        """
        Use GPT-4o to assess whether the domain is visible for this query.

        Returns a dict with domain_visible, visibility_position, etc.
        Falls back to safe defaults if the LLM call fails.
        """
        user_prompt = f"""Evaluate AI visibility for the following:

Query: "{query_text}"
Target Domain: {domain}
Industry: {industry or "not specified"}

Determine:
1. Would {domain} appear in an AI assistant's answer to this query?
2. If yes, at what position (1 = prominently featured, 10 = briefly mentioned)?
3. How difficult is this query for any domain to rank for?

Consider:
- Domain authority and brand recognition of {domain}
- Whether {domain} is likely to have content addressing this exact query
- The competitive landscape for this type of query

Return the JSON assessment now."""

        try:
            raw = self._call_llm(
                system_prompt=VISIBILITY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.0,  # Deterministic for consistent scoring
                max_tokens=512,
            )
            parsed = self._parse_json_response(raw)
            if isinstance(parsed, dict):
                return parsed
            logger.warning("[VisibilityScoringAgent] Unexpected LLM structure: %s", type(parsed))
        except (AgentError, Exception) as exc:
            logger.warning(
                "[VisibilityScoringAgent] Visibility LLM call failed for '%s': %s — using defaults",
                query_text[:60],
                exc,
            )

        # Safe fallback — assume not visible, moderate difficulty
        return {
            "domain_visible": False,
            "visibility_position": None,
            "competitive_difficulty_estimate": 50,
            "reasoning": "Fallback due to LLM error",
        }
