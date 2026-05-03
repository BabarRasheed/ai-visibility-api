"""
app/models/profile.py — BusinessProfile model.

Represents a registered business that will be processed through
the AI visibility pipeline.
"""

import uuid
from datetime import datetime, timezone

from app.extensions import db


class BusinessProfile(db.Model):
    __tablename__ = "business_profiles"

    # Primary key — UUID stored as string for SQLite compatibility
    uuid = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Core business fields
    name = db.Column(db.String(255), nullable=False)
    domain = db.Column(db.String(255), nullable=False, unique=True)
    industry = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Competitors stored as JSON array of domain strings
    competitors = db.Column(db.JSON, nullable=False, default=list)

    # Lifecycle status: created | running | completed | failed
    status = db.Column(db.String(50), nullable=False, default="created")

    # Timestamps (UTC)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    pipeline_runs = db.relationship(
        "PipelineRun", backref="profile", lazy="dynamic", cascade="all, delete-orphan"
    )
    queries = db.relationship(
        "DiscoveredQuery", backref="profile", lazy="dynamic", cascade="all, delete-orphan"
    )
    recommendations = db.relationship(
        "ContentRecommendation", backref="profile", lazy="dynamic", cascade="all, delete-orphan"
    )

    def to_dict(self, include_stats: bool = False) -> dict:
        """Serialise to a JSON-safe dictionary."""
        data = {
            "profile_uuid": self.uuid,
            "name": self.name,
            "domain": self.domain,
            "industry": self.industry,
            "description": self.description,
            "competitors": self.competitors or [],
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_stats:
            # Compute summary stats for the GET /profiles/{id} response
            queries_q = self.queries
            total_queries = queries_q.count()
            scored = queries_q.filter_by(opportunity_score=None).count()  # not null
            avg_score = db.session.query(
                db.func.avg(db.literal_column("opportunity_score"))
            ).select_from(
                db.text("discovered_queries")
            ).filter(
                db.text(f"profile_uuid = '{self.uuid}'")
            ).scalar()

            data["stats"] = {
                "total_queries_discovered": total_queries,
                "avg_opportunity_score": round(float(avg_score), 4) if avg_score else None,
            }

        return data

    def __repr__(self) -> str:
        return f"<BusinessProfile {self.domain}>"
