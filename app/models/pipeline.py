"""
app/models/pipeline.py — PipelineRun model.

Tracks a single execution of the 3-agent pipeline for a profile.
One profile can have many pipeline runs (e.g. re-runs over time).
"""

import uuid
from datetime import datetime, timezone

from app.extensions import db


class PipelineRun(db.Model):
    __tablename__ = "pipeline_runs"

    uuid = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to the owning profile
    profile_uuid = db.Column(
        db.String(36), db.ForeignKey("business_profiles.uuid"), nullable=False, index=True
    )

    # Execution status: running | completed | failed
    status = db.Column(db.String(50), nullable=False, default="running")

    # Counters populated after each agent completes
    queries_discovered = db.Column(db.Integer, nullable=True)
    queries_scored = db.Column(db.Integer, nullable=True)

    # Token usage from the AI provider (if available)
    tokens_used = db.Column(db.Integer, nullable=True)

    # Error details for failed runs
    error_message = db.Column(db.Text, nullable=True)

    # Timing
    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships
    queries = db.relationship(
        "DiscoveredQuery", backref="pipeline_run", lazy="dynamic"
    )

    def to_dict(self) -> dict:
        return {
            "run_uuid": self.uuid,
            "profile_uuid": self.profile_uuid,
            "status": self.status,
            "queries_discovered": self.queries_discovered,
            "queries_scored": self.queries_scored,
            "tokens_used": self.tokens_used,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    def __repr__(self) -> str:
        return f"<PipelineRun {self.uuid} status={self.status}>"
