"""
app/api/queries.py — Query-level endpoints.

Endpoints:
    POST /api/v1/queries/<query_uuid>/recheck — Re-run Agent 2 on a single query
"""

import logging
from flask import Blueprint, jsonify

from app.extensions import db
from app.models.query import DiscoveredQuery
from app.models.profile import BusinessProfile
from app.agents.scoring import VisibilityScoringAgent
from app.agents.base import AgentError

logger = logging.getLogger(__name__)

queries_bp = Blueprint("queries", __name__)


# ------------------------------------------------------------------ #
# POST /api/v1/queries/<query_uuid>/recheck
# ------------------------------------------------------------------ #

@queries_bp.route("/queries/<query_uuid>/recheck", methods=["POST"])
def recheck_query(query_uuid: str):
    """
    Re-run Agent 2 (visibility scoring) on a single query.

    Useful for re-checking visibility after content has been published.
    Updates the query record with fresh scoring data.

    Returns 200 with updated query data, or 404 if not found.
    """
    query = db.session.get(DiscoveredQuery, query_uuid)
    if not query:
        return jsonify(
            {"error": "Not Found", "message": f"Query '{query_uuid}' not found"}
        ), 404

    # Fetch the profile for domain + industry context
    profile = db.session.get(BusinessProfile, query.profile_uuid)
    if not profile:
        return jsonify(
            {"error": "Not Found", "message": "Associated profile not found"}
        ), 404

    logger.info(
        "[API] Rechecking query uuid=%s text='%s'",
        query_uuid,
        query.query_text[:60],
    )

    try:
        agent = VisibilityScoringAgent()
        score_data = agent.run(
            query_text=query.query_text,
            domain=profile.domain,
            industry=profile.industry,
        )

        # Update the query record with fresh data
        query.estimated_search_volume = score_data["estimated_search_volume"]
        query.competitive_difficulty = score_data["competitive_difficulty"]
        query.opportunity_score = score_data["opportunity_score"]
        query.domain_visible = score_data["domain_visible"]
        query.visibility_position = score_data["visibility_position"]

        db.session.commit()

        logger.info(
            "[API] Recheck complete: query=%s new_score=%.3f visible=%s",
            query_uuid,
            score_data["opportunity_score"],
            score_data["domain_visible"],
        )

        return jsonify(
            {
                "message": "Query re-scored successfully",
                "query": query.to_dict(),
                "tokens_used": agent.total_tokens_used,
            }
        ), 200

    except AgentError as exc:
        logger.error("[API] Agent error during recheck of query=%s: %s", query_uuid, exc)
        return jsonify(
            {
                "error": "Agent Error",
                "message": str(exc),
                "query_uuid": query_uuid,
            }
        ), 500

    except Exception as exc:
        logger.exception("[API] Unexpected error during recheck of query=%s", query_uuid)
        db.session.rollback()
        return jsonify(
            {
                "error": "Internal Server Error",
                "message": "An unexpected error occurred during re-scoring",
            }
        ), 500
