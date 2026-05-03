"""
app/models/query.py — DiscoveredQuery model.

Represents a single search query discovered by Agent 1 and scored by Agent 2.
Each query belongs to a profile and a specific pipeline run.
"""

import uuid
from datetime import datetime, timezone

from app.extensions import db


class DiscoveredQuery(db.Model):
    __tablename__ = "discovered_queries"

    uuid = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign keys
    profile_uuid = db.Column(
        db.String(36), db.ForeignKey("business_profiles.uuid"), nullable=False, index=True
    )
    run_uuid = db.Column(
        db.String(36), db.ForeignKey("pipeline_runs.uuid"), nullable=False, index=True
    )

    # The discovered query text
    query_text = db.Column(db.Text, nullable=False)

    # Scoring data populated by Agent 2
    estimated_search_volume = db.Column(db.Integer, nullable=True)
    competitive_difficulty = db.Column(db.Integer, nullable=True)  # 0–100
    opportunity_score = db.Column(db.Float, nullable=True)          # 0.0–1.0

    # Visibility data
    domain_visible = db.Column(db.Boolean, nullable=True)
    visibility_position = db.Column(db.Integer, nullable=True)  # rank if visible, else null

    # When this query was first discovered
    discovered_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    recommendations = db.relationship(
        "ContentRecommendation",
        backref="source_query",  # Avoid shadowing SQLAlchemy's .query accessor
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        return {
            "query_uuid": self.uuid,
            "profile_uuid": self.profile_uuid,
            "run_uuid": self.run_uuid,
            "query_text": self.query_text,
            "estimated_search_volume": self.estimated_search_volume,
            "competitive_difficulty": self.competitive_difficulty,
            "opportunity_score": round(self.opportunity_score, 4) if self.opportunity_score is not None else None,
            "domain_visible": self.domain_visible,
            "visibility_position": self.visibility_position,
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
        }

    def __repr__(self) -> str:
        return f"<DiscoveredQuery '{self.query_text[:40]}...'>"
