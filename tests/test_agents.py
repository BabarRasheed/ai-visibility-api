"""
tests/test_agents.py — Unit tests for all three AI agents.

All LLM calls are mocked using unittest.mock so tests:
  1. Run without a real OpenAI API key
  2. Are fast (no network I/O)
  3. Test the agent's JSON parsing, validation, and error handling
     independently of the LLM

DataForSEO is also mocked so scoring tests are fully offline.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ================================================================== #
# Agent 1 — QueryDiscoveryAgent
# ================================================================== #

class TestQueryDiscoveryAgent:
    """Unit tests for Agent 1."""

    @pytest.fixture
    def agent(self, app):
        """Create agent instance within app context."""
        with app.app_context():
            from app.agents.discovery import QueryDiscoveryAgent
            return QueryDiscoveryAgent()

    @pytest.fixture
    def profile(self):
        return {
            "name": "Frase",
            "domain": "frase.io",
            "industry": "SEO Content Tools",
            "description": "AI content brief tool",
            "competitors": ["surferseo.com", "marketmuse.com"],
        }

    def test_run_returns_queries_list(self, agent, profile, app):
        """Agent should return a list of query dicts when LLM returns valid JSON."""
        mock_response = {
            "queries": [
                {
                    "query_text": "What is the best AI content brief tool?",
                    "intent": "commercial",
                    "rationale": "High commercial intent query",
                },
                {
                    "query_text": "Frase vs Surfer SEO comparison",
                    "intent": "comparison",
                    "rationale": "Competitor comparison",
                },
            ]
        }

        with app.app_context():
            with patch.object(agent, "_call_llm", return_value=json.dumps(mock_response)):
                result = agent.run(profile=profile)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["query_text"] == "What is the best AI content brief tool?"
        assert result[0]["intent"] == "commercial"

    def test_run_handles_malformed_json_with_retry(self, agent, profile, app):
        """Agent should retry once on malformed JSON and succeed on second try."""
        valid_response = json.dumps(
            {"queries": [{"query_text": "Best SEO tool for content?", "intent": "commercial", "rationale": "test"}]}
        )

        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "This is not JSON at all!!!"
            return valid_response

        with app.app_context():
            with patch.object(agent, "_call_llm", side_effect=side_effect):
                result = agent.run(profile=profile)

        assert len(result) == 1
        assert call_count["n"] == 2  # Called twice (original + retry)

    def test_run_skips_items_missing_query_text(self, agent, profile, app):
        """Items without query_text should be silently skipped."""
        mock_response = {
            "queries": [
                {"query_text": "Valid query", "intent": "commercial", "rationale": "ok"},
                {"intent": "commercial", "rationale": "missing text"},  # No query_text
                {"query_text": "", "intent": "commercial", "rationale": "empty text"},
            ]
        }

        with app.app_context():
            with patch.object(agent, "_call_llm", return_value=json.dumps(mock_response)):
                result = agent.run(profile=profile)

        assert len(result) == 1
        assert result[0]["query_text"] == "Valid query"

    def test_run_handles_bare_list_response(self, agent, profile, app):
        """Agent should handle LLM returning a bare list instead of {queries: [...]}."""
        mock_response = [
            {"query_text": "Best content tool", "intent": "commercial", "rationale": "test"}
        ]

        with app.app_context():
            with patch.object(agent, "_call_llm", return_value=json.dumps(mock_response)):
                result = agent.run(profile=profile)

        assert len(result) == 1

    def test_build_user_prompt_includes_domain_and_competitors(self, agent):
        """User prompt should contain domain and competitor info."""
        profile = {
            "name": "Frase",
            "domain": "frase.io",
            "industry": "SEO Tools",
            "description": "Content tool",
            "competitors": ["surferseo.com", "marketmuse.com"],
        }
        prompt = agent._build_user_prompt(profile)
        assert "frase.io" in prompt
        assert "surferseo.com" in prompt
        assert "marketmuse.com" in prompt


# ================================================================== #
# Agent 2 — VisibilityScoringAgent
# ================================================================== #

class TestVisibilityScoringAgent:
    """Unit tests for Agent 2."""

    @pytest.fixture
    def agent(self, app):
        with app.app_context():
            from app.agents.scoring import VisibilityScoringAgent
            return VisibilityScoringAgent()

    def test_run_returns_correct_structure(self, agent, app):
        """Agent should return a dict with all required scoring fields."""
        visibility_response = {
            "domain_visible": False,
            "visibility_position": None,
            "reasoning": "Domain not known for this topic",
            "competitive_difficulty_estimate": 65,
        }

        with app.app_context():
            # Mock both DataForSEO and LLM calls
            with patch.object(
                agent, "_get_search_data", return_value=(1200, 62)
            ):
                with patch.object(
                    agent, "_call_llm", return_value=json.dumps(visibility_response)
                ):
                    result = agent.run(
                        query_text="Best AI content brief tool",
                        domain="frase.io",
                        industry="SEO Content Tools",
                    )

        assert "estimated_search_volume" in result
        assert "competitive_difficulty" in result
        assert "opportunity_score" in result
        assert "domain_visible" in result
        assert "visibility_position" in result
        assert 0.0 <= result["opportunity_score"] <= 1.0

    def test_run_uses_dataforseo_difficulty_when_available(self, agent, app):
        """When DataForSEO returns difficulty, it should be used over LLM estimate."""
        visibility_response = {
            "domain_visible": False,
            "visibility_position": None,
            "competitive_difficulty_estimate": 80,  # Should be ignored
        }

        with app.app_context():
            with patch.object(agent, "_get_search_data", return_value=(500, 45)):  # 45 from DataForSEO
                with patch.object(agent, "_call_llm", return_value=json.dumps(visibility_response)):
                    result = agent.run("test query", "frase.io", "SEO")

        # DataForSEO difficulty (45) should be used, not LLM estimate (80)
        assert result["competitive_difficulty"] == 45

    def test_run_falls_back_gracefully_when_dataforseo_fails(self, agent, app):
        """Pipeline should not crash if DataForSEO is unavailable."""
        visibility_response = {
            "domain_visible": True,
            "visibility_position": 2,
            "competitive_difficulty_estimate": 55,
        }

        with app.app_context():
            with patch.object(agent, "_get_search_data", return_value=(None, None)):
                with patch.object(agent, "_call_llm", return_value=json.dumps(visibility_response)):
                    result = agent.run("test query", "frase.io", "SEO")

        assert result is not None
        assert result["competitive_difficulty"] == 55  # From LLM estimate

    def test_opportunity_score_higher_when_not_visible(self, agent, app):
        """Invisible domain should score higher than visible domain (all else equal)."""
        with app.app_context():
            with patch.object(agent, "_get_search_data", return_value=(1000, 50)):
                with patch.object(agent, "_call_llm", return_value=json.dumps({
                    "domain_visible": False,
                    "visibility_position": None,
                    "competitive_difficulty_estimate": 50,
                })):
                    invisible_result = agent.run("test query", "frase.io", "SEO")

                with patch.object(agent, "_call_llm", return_value=json.dumps({
                    "domain_visible": True,
                    "visibility_position": 1,
                    "competitive_difficulty_estimate": 50,
                })):
                    visible_result = agent.run("test query", "frase.io", "SEO")

        assert invisible_result["opportunity_score"] > visible_result["opportunity_score"]


# ================================================================== #
# Agent 3 — ContentRecommendationAgent
# ================================================================== #

class TestContentRecommendationAgent:
    """Unit tests for Agent 3."""

    @pytest.fixture
    def agent(self, app):
        with app.app_context():
            from app.agents.recommendation import ContentRecommendationAgent
            return ContentRecommendationAgent()

    @pytest.fixture
    def sample_queries(self):
        return [
            {
                "query_text": "Best AI content brief tool",
                "opportunity_score": 0.85,
                "estimated_search_volume": 1200,
                "competitive_difficulty": 55,
                "domain_visible": False,
            },
            {
                "query_text": "Frase vs Surfer SEO comparison",
                "opportunity_score": 0.72,
                "estimated_search_volume": 800,
                "competitive_difficulty": 40,
                "domain_visible": False,
            },
        ]

    @pytest.fixture
    def profile(self):
        return {
            "name": "Frase",
            "domain": "frase.io",
            "industry": "SEO Content Tools",
            "description": "Content brief tool",
            "competitors": ["surferseo.com"],
        }

    def test_run_returns_recommendations_list(self, agent, sample_queries, profile, app):
        """Agent should return a list of recommendation dicts."""
        mock_response = {
            "recommendations": [
                {
                    "query_text": "Best AI content brief tool",
                    "content_type": "blog_post",
                    "title": "The 5 Best AI Content Brief Tools in 2025 (Compared)",
                    "rationale": "Comprehensive comparison guide targeting high-intent searchers",
                    "target_keywords": ["ai content brief tool", "content brief generator"],
                    "priority": "high",
                }
            ]
        }

        with app.app_context():
            with patch.object(agent, "_call_llm", return_value=json.dumps(mock_response)):
                result = agent.run(queries=sample_queries, profile=profile)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["content_type"] == "blog_post"
        assert result[0]["priority"] == "high"
        assert isinstance(result[0]["target_keywords"], list)

    def test_run_returns_empty_list_for_empty_queries(self, agent, profile, app):
        """Should return empty list when no queries are provided."""
        with app.app_context():
            result = agent.run(queries=[], profile=profile)
        assert result == []

    def test_run_normalises_invalid_content_type(self, agent, sample_queries, profile, app):
        """Invalid content_type should default to 'blog_post'."""
        mock_response = {
            "recommendations": [
                {
                    "query_text": "test query",
                    "content_type": "invalid_type_xyz",  # Not in allowed set
                    "title": "Test Title",
                    "rationale": "Test rationale for this content piece",
                    "target_keywords": ["keyword"],
                    "priority": "high",
                }
            ]
        }

        with app.app_context():
            with patch.object(agent, "_call_llm", return_value=json.dumps(mock_response)):
                result = agent.run(queries=sample_queries, profile=profile)

        assert result[0]["content_type"] == "blog_post"

    def test_run_normalises_invalid_priority(self, agent, sample_queries, profile, app):
        """Invalid priority should default to 'medium'."""
        mock_response = {
            "recommendations": [
                {
                    "query_text": "test query",
                    "content_type": "blog_post",
                    "title": "Test Title",
                    "rationale": "Good rationale here",
                    "target_keywords": ["kw"],
                    "priority": "URGENT",  # Not in allowed set
                }
            ]
        }

        with app.app_context():
            with patch.object(agent, "_call_llm", return_value=json.dumps(mock_response)):
                result = agent.run(queries=sample_queries, profile=profile)

        assert result[0]["priority"] == "medium"

    def test_run_caps_at_five_recommendations(self, agent, sample_queries, profile, app):
        """Output should be capped at 5 recommendations."""
        many_recs = [
            {
                "query_text": f"query {i}",
                "content_type": "blog_post",
                "title": f"Title {i}",
                "rationale": f"Rationale for item {i} with detailed explanation",
                "target_keywords": ["kw"],
                "priority": "medium",
            }
            for i in range(10)  # Return 10
        ]
        mock_response = {"recommendations": many_recs}

        with app.app_context():
            with patch.object(agent, "_call_llm", return_value=json.dumps(mock_response)):
                result = agent.run(queries=sample_queries, profile=profile)

        assert len(result) <= 5


# ================================================================== #
# Opportunity Score Formula
# ================================================================== #

class TestOpportunityScore:
    """Unit tests for the scoring formula."""

    def test_not_visible_high_volume_low_difficulty_is_near_one(self):
        from app.utils.scoring import compute_opportunity_score
        score = compute_opportunity_score(
            search_volume=10000, difficulty=0, domain_visible=False
        )
        # Should be very high — all factors maximally favourable
        assert score >= 0.9

    def test_visible_low_volume_high_difficulty_is_near_zero(self):
        from app.utils.scoring import compute_opportunity_score
        score = compute_opportunity_score(
            search_volume=0, difficulty=100, domain_visible=True
        )
        # Should be very low — already visible, hard to improve, no volume
        assert score <= 0.1

    def test_score_is_in_zero_to_one_range(self):
        from app.utils.scoring import compute_opportunity_score
        for volume in [0, 500, 5000, 50000]:
            for diff in [0, 25, 50, 75, 100]:
                for visible in [True, False]:
                    score = compute_opportunity_score(volume, diff, visible)
                    assert 0.0 <= score <= 1.0, (
                        f"Score out of range: volume={volume} diff={diff} visible={visible} → {score}"
                    )

    def test_invisible_always_scores_higher_than_visible(self):
        from app.utils.scoring import compute_opportunity_score
        for volume in [0, 1000, 5000]:
            for diff in [20, 50, 80]:
                invisible = compute_opportunity_score(volume, diff, domain_visible=False)
                visible = compute_opportunity_score(volume, diff, domain_visible=True)
                assert invisible > visible, (
                    f"Invisible should score higher: volume={volume} diff={diff}"
                )

    def test_higher_volume_scores_higher(self):
        from app.utils.scoring import compute_opportunity_score
        low = compute_opportunity_score(100, 50, False)
        high = compute_opportunity_score(5000, 50, False)
        assert high > low

    def test_lower_difficulty_scores_higher(self):
        from app.utils.scoring import compute_opportunity_score
        hard = compute_opportunity_score(1000, 90, False)
        easy = compute_opportunity_score(1000, 10, False)
        assert easy > hard
