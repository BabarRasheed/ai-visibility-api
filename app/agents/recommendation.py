"""
app/agents/recommendation.py — Agent 3: ContentRecommendationAgent.

Responsibility:
    Given the top-scoring queries where the target domain is NOT visible,
    generate 3–5 specific, actionable content recommendations that would
    help the domain appear in AI answers for those queries.

Design notes:
    - Input is intentionally filtered to NOT_VISIBLE queries only.
      Recommending content for queries where you're already visible
      would waste the user's effort.
    - Temperature=0.5 balances creativity (unique titles) with
      consistency (reliable JSON structure).
    - We pass the top N queries (by opportunity score) to keep
      the prompt size manageable and focus recommendations on
      high-value targets.
    - Each recommendation includes a priority field derived from
      the opportunity score range:
        high:   score >= 0.7
        medium: score >= 0.4
        low:    score < 0.4
"""

import logging
from typing import Any

from .base import BaseAgent, AgentError

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# System prompt — defines strict JSON output contract
# ------------------------------------------------------------------ #

SYSTEM_PROMPT = """You are a senior content strategist specialising in AI visibility
and search intent optimisation. You help businesses appear in AI-generated answers
by recommending specific, high-quality content to create.

Your task is to generate actionable content recommendations for a business that is
NOT currently appearing in AI answers for high-value queries.

## Output Requirements

Return ONLY a valid JSON object with this exact schema — no extra text, no markdown:

{
  "recommendations": [
    {
      "query_text": "the query this recommendation addresses",
      "content_type": "blog_post | landing_page | faq | comparison | case_study | guide",
      "title": "the specific, compelling title for the content piece",
      "rationale": "2-3 sentences explaining exactly why this content will improve AI visibility",
      "target_keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
      "priority": "high | medium | low"
    }
  ]
}

## Rules
- Generate 3 to 5 recommendations total.
- Each recommendation must address a DIFFERENT query from the input list.
- Prioritise high opportunity_score queries first.
- For 'comparison' queries: recommend a comparison article as content_type.
- For 'best of' queries: recommend a comprehensive guide or landing page.
- For 'how to' queries: recommend an FAQ or tutorial-style blog post.
- Titles must be specific and compelling — not generic like "SEO Content Guide".
- target_keywords should include the core query terms plus 4-6 semantic variations.
- rationale must explain how this content will help the business appear in AI answers.
- priority must match the query's opportunity score:
    high = score >= 0.7, medium = score 0.4–0.69, low = score < 0.4

Return valid JSON only. No explanation outside the JSON.
"""


class ContentRecommendationAgent(BaseAgent):
    """
    Agent 3 — Generate 3–5 content recommendations for visibility gaps.

    Input:  list of scored query dicts where domain_visible=False
    Output: list of recommendation dicts
    """

    agent_name = "ContentRecommendationAgent"

    def run(self, queries: list[dict], profile: dict) -> list[dict]:
        """
        Generate content recommendations for the given visibility gaps.

        Args:
            queries: List of scored query dicts (domain_visible=False).
                     Each dict has: query_text, opportunity_score,
                     estimated_search_volume, competitive_difficulty.
            profile: Business profile dict (name, domain, industry, etc.)

        Returns:
            List of recommendation dicts:
            [
                {
                    "query_text": str,
                    "content_type": str,
                    "title": str,
                    "rationale": str,
                    "target_keywords": list[str],
                    "priority": str,
                }
            ]

        Raises:
            AgentError: If the LLM fails completely.
        """
        if not queries:
            logger.info("[ContentRecommendationAgent] No invisible queries provided — skipping")
            return []

        # Sort by opportunity score descending, take top 10 to keep prompt manageable
        sorted_queries = sorted(
            queries, key=lambda q: q.get("opportunity_score", 0), reverse=True
        )[:10]

        logger.info(
            "[ContentRecommendationAgent] Generating recommendations for %d queries (domain=%s)",
            len(sorted_queries),
            profile.get("domain"),
        )

        user_prompt = self._build_user_prompt(sorted_queries, profile)

        raw = self._call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.5,
            max_tokens=2048,
        )

        parsed = self._parse_json_response(
            raw,
            retry_system=SYSTEM_PROMPT,
            retry_user=user_prompt,
        )

        recommendations = self._extract_recommendations(parsed, sorted_queries)

        logger.info(
            "[ContentRecommendationAgent] Generated %d recommendations for %s",
            len(recommendations),
            profile.get("domain"),
        )
        return recommendations

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_user_prompt(queries: list[dict], profile: dict) -> str:
        """Build the user prompt from the profile and invisible queries."""
        competitors_str = ", ".join(profile.get("competitors", [])) or "none listed"

        # Format queries as a numbered list with their scores
        query_lines = []
        for i, q in enumerate(queries, 1):
            score = q.get("opportunity_score", 0)
            volume = q.get("estimated_search_volume", 0)
            diff = q.get("competitive_difficulty", 50)
            query_lines.append(
                f"  {i}. \"{q['query_text']}\" "
                f"(opportunity_score={score:.2f}, volume≈{volume}, difficulty={diff})"
            )

        queries_block = "\n".join(query_lines)

        return f"""Generate content recommendations for this business:

Business Name: {profile.get("name", "Unknown")}
Domain: {profile.get("domain", "unknown.com")}
Industry: {profile.get("industry", "Unknown")}
Description: {profile.get("description") or "Not provided."}
Competitors: {competitors_str}

The following queries have been identified as HIGH-OPPORTUNITY gaps where
{profile.get("domain")} does NOT currently appear in AI answers:

{queries_block}

For each recommendation:
1. Pick a different query from the list above (prioritise the highest scores).
2. Recommend a specific piece of content that, if published on {profile.get("domain")},
   would help it appear in AI answers for that query.
3. Be very specific with the title — it should be publishable as-is.
4. Explain exactly why this content type and approach will improve AI visibility.

Return 3 to 5 recommendations as JSON now."""

    @staticmethod
    def _extract_recommendations(parsed: Any, source_queries: list[dict]) -> list[dict]:
        """
        Validate and normalise recommendations from the parsed LLM response.

        Falls back gracefully if individual items are malformed.
        """
        if isinstance(parsed, dict):
            recs = parsed.get("recommendations", [])
        elif isinstance(parsed, list):
            recs = parsed
        else:
            raise AgentError(
                f"Unexpected JSON structure from ContentRecommendationAgent: {type(parsed)}"
            )

        if not isinstance(recs, list):
            raise AgentError("'recommendations' field is not a list")

        # Build a lookup for known query texts (for validation)
        known_queries = {q["query_text"].lower() for q in source_queries}

        validated = []
        for item in recs:
            if not isinstance(item, dict):
                logger.warning("[ContentRecommendationAgent] Skipping non-dict item")
                continue

            title = str(item.get("title", "")).strip()
            content_type = str(item.get("content_type", "blog_post")).strip()
            rationale = str(item.get("rationale", "")).strip()
            keywords = item.get("target_keywords", [])
            priority = str(item.get("priority", "medium")).strip().lower()
            query_text = str(item.get("query_text", "")).strip()

            if not title or not rationale:
                logger.warning("[ContentRecommendationAgent] Skipping incomplete recommendation")
                continue

            # Normalise content_type
            allowed_types = {"blog_post", "landing_page", "faq", "comparison", "case_study", "guide"}
            if content_type not in allowed_types:
                content_type = "blog_post"

            # Normalise priority
            if priority not in {"high", "medium", "low"}:
                priority = "medium"

            # Ensure keywords is a list of strings
            if not isinstance(keywords, list):
                keywords = []
            keywords = [str(k) for k in keywords if k][:10]

            validated.append(
                {
                    "query_text": query_text,
                    "content_type": content_type,
                    "title": title,
                    "rationale": rationale,
                    "target_keywords": keywords,
                    "priority": priority,
                }
            )

        return validated[:5]  # Hard cap at 5 recommendations
