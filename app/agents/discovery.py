"""
app/agents/discovery.py — Agent 1: QueryDiscoveryAgent.

Responsibility:
    Given a business profile (name, domain, industry, description,
    competitors), generate 10–20 realistic natural-language questions
    that users ask AI assistants (ChatGPT, Perplexity, Claude) when
    searching for products or services in this competitive space.

Design notes:
    - Temperature is deliberately set to 0.7 to produce diverse,
      creative queries rather than the same obvious questions.
    - We ask the model to categorise each query by intent
      (commercial / informational / navigational / comparison) so
      the pipeline can prioritise high-intent queries downstream.
    - The system prompt defines the exact JSON schema and includes
      3 concrete few-shot examples to anchor the model's output style.
"""

import logging
from typing import Any

from .base import BaseAgent, AgentError

logger = logging.getLogger(__name__)


# System prompt — defines agent persona and strict output contract

SYSTEM_PROMPT = """You are a senior SEO and AI visibility strategist with 10+ years of
experience researching how businesses appear in AI-generated answers.

Your task is to generate a set of realistic search queries that potential customers
would type into AI assistants (ChatGPT, Perplexity, Claude, Gemini) when looking for
products or services in a given industry.

## Output Requirements

Return ONLY a valid JSON object matching this exact schema — no extra text, no markdown.

{
  "queries": [
    {
      "query_text": "the full natural-language question",
      "intent": "commercial | informational | comparison | navigational",
      "rationale": "one sentence explaining why this query is relevant"
    }
  ]
}

## Rules
- Generate between 10 and 20 queries — aim for 15 for good coverage.
- Queries must be natural language questions people actually ask AI assistants.
- Prioritise commercial and comparison queries — these have the highest opportunity value.
- Include competitor comparison queries (e.g. "X vs Y").
- Include "best of" queries relevant to the industry.
- Include use-case / job-to-be-done queries.
- Do NOT generate navigational queries like "how do I log into [brand]".
- Do NOT repeat queries that mean the same thing.
- Queries should be 5–15 words long.
- Use the competitor domains provided to generate comparison queries.

## Few-shot Examples (for an SEO content tool)

Query: "What is the best AI tool for writing SEO content briefs?"
Intent: commercial

Query: "Surfer SEO vs Clearscope — which is better for content teams?"
Intent: comparison

Query: "How can I use AI to speed up keyword research for my blog?"
Intent: informational
"""


class QueryDiscoveryAgent(BaseAgent):
    """
    Agent 1 — Discover 10–20 AI-assistant queries for a business profile.

    Input:  business profile dict
    Output: list of query dicts with text, intent, and rationale
    """

    agent_name = "QueryDiscoveryAgent"

    def run(self, profile: dict) -> list[dict]:
        """
        Generate queries for the given business profile.

        Args:
            profile: {
                "name": str,
                "domain": str,
                "industry": str,
                "description": str,
                "competitors": list[str]
            }

        Returns:
            List of query dicts:
            [{"query_text": str, "intent": str, "rationale": str}, ...]

        Raises:
            AgentError: If the LLM fails or returns unparseable output.
        """
        logger.info(
            "[QueryDiscoveryAgent] Generating queries for domain=%s industry=%s",
            profile.get("domain"),
            profile.get("industry"),
        )

        user_prompt = self._build_user_prompt(profile)

        raw = self._call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.7,  # Higher creativity for diverse queries
            max_tokens=2048,
        )

        parsed = self._parse_json_response(
            raw,
            retry_system=SYSTEM_PROMPT,
            retry_user=user_prompt,
        )

        queries = self._extract_queries(parsed)

        logger.info(
            "[QueryDiscoveryAgent] Discovered %d queries for %s",
            len(queries),
            profile.get("domain"),
        )
        return queries

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_user_prompt(profile: dict) -> str:
        """Build the variable-filled user prompt from the profile."""
        competitors_str = (
            ", ".join(profile.get("competitors", [])) or "none listed"
        )
        return f"""Generate search queries for the following business:

Business Name: {profile.get("name", "Unknown")}
Domain: {profile.get("domain", "unknown.com")}
Industry: {profile.get("industry", "Unknown")}
Description: {profile.get("description") or "No description provided."}
Key Competitors: {competitors_str}

Focus on queries that potential customers would use when researching this type of
product or service through AI assistants. Emphasise comparison queries that include
the competitors listed above, and commercial-intent queries showing purchase readiness.

Return the JSON object now."""

    @staticmethod
    def _extract_queries(parsed: Any) -> list[dict]:
        """
        Extract and validate the queries list from the parsed LLM response.

        Handles both:
          - {"queries": [...]}   (expected)
          - [...]                (bare list fallback)
        """
        if isinstance(parsed, dict):
            queries = parsed.get("queries", [])
        elif isinstance(parsed, list):
            queries = parsed
        else:
            raise AgentError(
                f"Unexpected JSON structure from LLM: {type(parsed).__name__}"
            )

        if not isinstance(queries, list):
            raise AgentError("'queries' field is not a list in LLM response")

        # Validate and normalise each query
        validated = []
        for item in queries:
            if not isinstance(item, dict):
                logger.warning("[QueryDiscoveryAgent] Skipping non-dict query item: %s", item)
                continue

            query_text = item.get("query_text", "").strip()
            if not query_text:
                logger.warning("[QueryDiscoveryAgent] Skipping query with empty text")
                continue

            validated.append(
                {
                    "query_text": query_text,
                    "intent": item.get("intent", "informational"),
                    "rationale": item.get("rationale", ""),
                }
            )

        if not validated:
            raise AgentError("Agent 1 returned zero valid queries")

        return validated
