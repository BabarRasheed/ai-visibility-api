"""
tests/test_api.py — Integration tests for API endpoints.

Tests hit the Flask test client with a real in-memory SQLite database.
AI agent calls are mocked to keep tests fast and offline.
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ================================================================== #
# POST /api/v1/profiles
# ================================================================== #

class TestCreateProfile:

    def test_create_profile_returns_201(self, client, sample_profile_data, db):
        response = client.post(
            "/api/v1/profiles",
            json=sample_profile_data,
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.get_json()
        assert "profile_uuid" in data
        assert data["domain"] == "frase.io"
        assert data["status"] == "created"

    def test_create_profile_missing_required_fields(self, client, db):
        response = client.post(
            "/api/v1/profiles",
            json={"name": "Frase"},  # Missing domain and industry
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_create_profile_duplicate_domain_returns_409(self, client, sample_profile_data, db):
        client.post("/api/v1/profiles", json=sample_profile_data)
        response = client.post("/api/v1/profiles", json=sample_profile_data)
        assert response.status_code == 409

    def test_create_profile_no_json_body_returns_400(self, client, db):
        response = client.post("/api/v1/profiles", data="not json", content_type="text/plain")
        assert response.status_code == 400

    def test_create_profile_invalid_competitors_type(self, client, db):
        payload = {
            "name": "Test",
            "domain": "test.com",
            "industry": "Tech",
            "competitors": "not-a-list",
        }
        response = client.post("/api/v1/profiles", json=payload)
        assert response.status_code == 400


# ================================================================== #
# GET /api/v1/profiles/<uuid>
# ================================================================== #

class TestGetProfile:

    def test_get_existing_profile(self, client, sample_profile_data, db):
        create_resp = client.post("/api/v1/profiles", json=sample_profile_data)
        profile_uuid = create_resp.get_json()["profile_uuid"]

        response = client.get(f"/api/v1/profiles/{profile_uuid}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["profile_uuid"] == profile_uuid
        assert data["domain"] == "frase.io"
        assert "stats" in data

    def test_get_nonexistent_profile_returns_404(self, client, db):
        response = client.get("/api/v1/profiles/nonexistent-uuid-12345")
        assert response.status_code == 404


# ================================================================== #
# GET /api/v1/profiles/<uuid>/queries — filtering and pagination
# ================================================================== #

class TestGetQueries:

    def test_get_queries_empty_for_new_profile(self, client, sample_profile_data, db):
        create_resp = client.post("/api/v1/profiles", json=sample_profile_data)
        profile_uuid = create_resp.get_json()["profile_uuid"]

        response = client.get(f"/api/v1/profiles/{profile_uuid}/queries")
        assert response.status_code == 200
        data = response.get_json()
        assert data["queries"] == []
        assert data["pagination"]["total"] == 0

    def test_get_queries_nonexistent_profile_returns_404(self, client, db):
        response = client.get("/api/v1/profiles/no-such-uuid/queries")
        assert response.status_code == 404

    def test_get_queries_pagination_params(self, client, sample_profile_data, db):
        create_resp = client.post("/api/v1/profiles", json=sample_profile_data)
        profile_uuid = create_resp.get_json()["profile_uuid"]

        response = client.get(
            f"/api/v1/profiles/{profile_uuid}/queries?page=1&per_page=5"
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["pagination"]["per_page"] == 5
        assert data["pagination"]["page"] == 1


# ================================================================== #
# GET /api/v1/profiles/<uuid>/recommendations
# ================================================================== #

class TestGetRecommendations:

    def test_get_recommendations_empty_for_new_profile(self, client, sample_profile_data, db):
        create_resp = client.post("/api/v1/profiles", json=sample_profile_data)
        profile_uuid = create_resp.get_json()["profile_uuid"]

        response = client.get(f"/api/v1/profiles/{profile_uuid}/recommendations")
        assert response.status_code == 200
        data = response.get_json()
        assert data["recommendations"] == []

    def test_get_recommendations_nonexistent_profile_returns_404(self, client, db):
        response = client.get("/api/v1/profiles/no-such-uuid/recommendations")
        assert response.status_code == 404


# ================================================================== #
# POST /api/v1/queries/<uuid>/recheck
# ================================================================== #

class TestRecheckQuery:

    def test_recheck_nonexistent_query_returns_404(self, client, db):
        response = client.post("/api/v1/queries/nonexistent-uuid/recheck")
        assert response.status_code == 404


# ================================================================== #
# POST /api/v1/profiles/<uuid>/run — mocked pipeline
# ================================================================== #

class TestRunPipeline:

    def test_run_pipeline_nonexistent_profile_returns_404(self, client, db):
        response = client.post("/api/v1/profiles/no-such-uuid/run")
        assert response.status_code == 404

    def test_run_pipeline_with_mocked_agents(self, client, sample_profile_data, db, app):
        """
        Full pipeline test with all 3 agents mocked.
        Validates the response structure without real LLM calls.
        """
        create_resp = client.post("/api/v1/profiles", json=sample_profile_data)
        profile_uuid = create_resp.get_json()["profile_uuid"]

        # Mock the entire PipelineOrchestrator.run to return a canned result
        mock_result = {
            "run_uuid": "test-run-uuid-123",
            "profile_uuid": profile_uuid,
            "status": "completed",
            "queries_discovered": 3,
            "queries_scored": 3,
            "tokens_used": 1500,
            "top_opportunities": [
                {
                    "query_text": "Best SEO tool",
                    "opportunity_score": 0.85,
                    "estimated_search_volume": 1200,
                    "competitive_difficulty": 55,
                    "domain_visible": False,
                }
            ],
            "recommendations": [],
            "started_at": "2025-01-15T10:00:00+00:00",
            "completed_at": "2025-01-15T10:00:30+00:00",
        }

        with patch(
            "app.api.profiles.PipelineOrchestrator.run",
            return_value=mock_result,
        ):
            response = client.post(f"/api/v1/profiles/{profile_uuid}/run")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "completed"
        assert "run_uuid" in data
        assert "top_opportunities" in data
        assert "recommendations" in data
