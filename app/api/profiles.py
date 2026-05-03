"""
app/api/profiles.py — Business Profile and Pipeline endpoints.

Endpoints:
    POST   /api/v1/profiles              — Register a new business profile
    GET    /api/v1/profiles/<uuid>       — Get profile + summary stats
    POST   /api/v1/profiles/<uuid>/run   — Trigger the 3-agent pipeline
    GET    /api/v1/profiles/<uuid>/queries          — List queries with filtering/pagination
    GET    /api/v1/profiles/<uuid>/recommendations  — List content recommendations
"""

import logging
from flask import Blueprint, request, jsonify, current_app

from app.extensions import db
from app.models.profile import BusinessProfile
from app.models.query import DiscoveredQuery
from app.models.recommendation import ContentRecommendation
from app.services.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)

profiles_bp = Blueprint("profiles", __name__)


# ------------------------------------------------------------------ #
# POST /api/v1/profiles — Register a business profile
# ------------------------------------------------------------------ #

@profiles_bp.route("/profiles", methods=["POST"])
def create_profile():
    """
    Register a new business profile.

    Request body:
        {
            "name": "Surfer SEO",
            "domain": "surferseo.com",
            "industry": "SEO Software",
            "description": "AI-powered SEO content optimization tool",
            "competitors": ["clearscope.io", "marketmuse.com"]
        }

    Returns 201 on success, 400 on validation error, 409 if domain exists.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "Request body must be valid JSON"}), 400

    # Validate required fields
    required = ["name", "domain", "industry"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify(
            {"error": "Bad Request", "message": f"Missing required fields: {', '.join(missing)}"}
        ), 400

    # Check for duplicate domain
    existing = BusinessProfile.query.filter_by(domain=data["domain"].lower().strip()).first()
    if existing:
        return jsonify(
            {
                "error": "Conflict",
                "message": f"A profile for domain '{data['domain']}' already exists",
                "profile_uuid": existing.uuid,
            }
        ), 409

    # Validate competitors field
    competitors = data.get("competitors", [])
    if not isinstance(competitors, list):
        return jsonify(
            {"error": "Bad Request", "message": "'competitors' must be a list of domain strings"}
        ), 400

    profile = BusinessProfile(
        name=data["name"].strip(),
        domain=data["domain"].lower().strip(),
        industry=data["industry"].strip(),
        description=data.get("description", "").strip() or None,
        competitors=[str(c).lower().strip() for c in competitors if c],
        status="created",
    )

    db.session.add(profile)
    db.session.commit()

    logger.info("[API] Created profile uuid=%s domain=%s", profile.uuid, profile.domain)

    return jsonify(
        {
            "profile_uuid": profile.uuid,
            "name": profile.name,
            "domain": profile.domain,
            "status": profile.status,
            "created_at": profile.created_at.isoformat(),
        }
    ), 201


# ------------------------------------------------------------------ #
# GET /api/v1/profiles/<profile_uuid> — Get profile + stats
# ------------------------------------------------------------------ #

@profiles_bp.route("/profiles/<profile_uuid>", methods=["GET"])
def get_profile(profile_uuid: str):
    """
    Retrieve a profile and its summary statistics.

    Returns 200 with profile data + stats, or 404 if not found.
    """
    profile = db.session.get(BusinessProfile, profile_uuid)
    if not profile:
        return jsonify({"error": "Not Found", "message": f"Profile '{profile_uuid}' not found"}), 404

    # Compute stats from the DB
    total_queries = DiscoveredQuery.query.filter_by(profile_uuid=profile_uuid).count()

    avg_score_result = db.session.query(
        db.func.avg(DiscoveredQuery.opportunity_score)
    ).filter(
        DiscoveredQuery.profile_uuid == profile_uuid,
        DiscoveredQuery.opportunity_score.isnot(None),
    ).scalar()

    avg_score = round(float(avg_score_result), 4) if avg_score_result is not None else None

    return jsonify(
        {
            **profile.to_dict(),
            "stats": {
                "total_queries_discovered": total_queries,
                "avg_opportunity_score": avg_score,
            },
        }
    ), 200


# ------------------------------------------------------------------ #
# POST /api/v1/profiles/<profile_uuid>/run — Trigger pipeline
# ------------------------------------------------------------------ #

@profiles_bp.route("/profiles/<profile_uuid>/run", methods=["POST"])
def run_pipeline(profile_uuid: str):
    """
    Trigger the full 3-agent pipeline for a profile.

    This runs synchronously and may take 15–60 seconds depending on
    the number of queries and AI provider latency.

    Returns the full pipeline result including top opportunities
    and content recommendations.
    """
    profile = db.session.get(BusinessProfile, profile_uuid)
    if not profile:
        return jsonify({"error": "Not Found", "message": f"Profile '{profile_uuid}' not found"}), 404

    # Prevent concurrent runs
    if profile.status == "running":
        return jsonify(
            {
                "error": "Conflict",
                "message": "A pipeline run is already in progress for this profile",
            }
        ), 409

    logger.info("[API] Triggering pipeline for profile=%s", profile_uuid)

    orchestrator = PipelineOrchestrator()
    result = orchestrator.run(profile)

    status_code = 200 if result.get("status") == "completed" else 500
    return jsonify(result), status_code


# ------------------------------------------------------------------ #
# GET /api/v1/profiles/<profile_uuid>/queries — List queries
# ------------------------------------------------------------------ #

@profiles_bp.route("/profiles/<profile_uuid>/queries", methods=["GET"])
def get_queries(profile_uuid: str):
    """
    Return all discovered queries for a profile.

    Query parameters:
        min_score  (float)  — filter by minimum opportunity score
        status     (str)    — 'visible' | 'not_visible' | 'unknown'
        page       (int)    — page number (default: 1)
        per_page   (int)    — results per page (default: 20, max: 100)
    """
    profile = db.session.get(BusinessProfile, profile_uuid)
    if not profile:
        return jsonify({"error": "Not Found", "message": f"Profile '{profile_uuid}' not found"}), 404

    # Base query
    q = DiscoveredQuery.query.filter_by(profile_uuid=profile_uuid)

    # Filter by minimum opportunity score
    min_score = request.args.get("min_score", type=float)
    if min_score is not None:
        q = q.filter(DiscoveredQuery.opportunity_score >= min_score)

    # Filter by visibility status
    status_filter = request.args.get("status", "").lower()
    if status_filter == "visible":
        q = q.filter(DiscoveredQuery.domain_visible.is_(True))
    elif status_filter == "not_visible":
        q = q.filter(DiscoveredQuery.domain_visible.is_(False))
    elif status_filter == "unknown":
        q = q.filter(DiscoveredQuery.domain_visible.is_(None))

    # Sort by opportunity score descending (NULLs last)
    q = q.order_by(
        DiscoveredQuery.opportunity_score.desc().nulls_last()
    )

    # Pagination
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify(
        {
            "profile_uuid": profile_uuid,
            "queries": [query.to_dict() for query in paginated.items],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": paginated.total,
                "pages": paginated.pages,
                "has_next": paginated.has_next,
                "has_prev": paginated.has_prev,
            },
        }
    ), 200


# ------------------------------------------------------------------ #
# GET /api/v1/profiles/<profile_uuid>/recommendations
# ------------------------------------------------------------------ #

@profiles_bp.route("/profiles/<profile_uuid>/recommendations", methods=["GET"])
def get_recommendations(profile_uuid: str):
    """
    Return all content recommendations for a profile.
    """
    profile = db.session.get(BusinessProfile, profile_uuid)
    if not profile:
        return jsonify({"error": "Not Found", "message": f"Profile '{profile_uuid}' not found"}), 404

    recs = db.session.query(ContentRecommendation).filter_by(
        profile_uuid=profile_uuid
    ).order_by(
        ContentRecommendation.created_at.desc()
    ).all()

    return jsonify(
        {
            "profile_uuid": profile_uuid,
            "recommendations": [r.to_dict() for r in recs],
            "total": len(recs),
        }
    ), 200
