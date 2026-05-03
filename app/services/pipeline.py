"""
app/services/pipeline.py — PipelineOrchestrator.

Coordinates the 3-agent pipeline in sequence:
    Agent 1 (Discovery) → Agent 2 (Scoring per query) → Agent 3 (Recommendations)

Key design decisions:
    1. Partial failure isolation: If Agent 2 fails for a single query,
       we log the error and continue with the remaining queries.
       The pipeline only marks as FAILED if Agent 1 completely fails.

    2. All DB writes happen within a single Flask application context
       and are committed at the end to keep the pipeline atomic.
       If the DB write fails, the run is marked as failed.

    3. Token counting: Each agent's total_tokens_used is accumulated
       and stored on the PipelineRun record.

    4. Top 3 opportunities: Sorted by opportunity_score descending,
       returned inline in the POST /run response for immediate value.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.extensions import db
from app.models.profile import BusinessProfile
from app.models.pipeline import PipelineRun
from app.models.query import DiscoveredQuery
from app.models.recommendation import ContentRecommendation
from app.agents.discovery import QueryDiscoveryAgent
from app.agents.scoring import VisibilityScoringAgent
from app.agents.recommendation import ContentRecommendationAgent
from app.agents.base import AgentError

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Orchestrates the full 3-agent AI visibility pipeline.

    Usage:
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run(profile)
    """

    def run(self, profile: BusinessProfile) -> dict:
        """
        Execute the full pipeline for a business profile.

        Args:
            profile: BusinessProfile ORM model instance.

        Returns:
            Pipeline result dict with run_uuid, status, counts, top opportunities,
            and content recommendations.
        """
        logger.info(
            "[Pipeline] Starting run for profile=%s domain=%s",
            profile.uuid,
            profile.domain,
        )

        # Create pipeline run record
        run = PipelineRun(profile_uuid=profile.uuid, status="running")
        db.session.add(run)
        db.session.flush()  # Get run.uuid without committing

        # Update profile status
        profile.status = "running"
        db.session.flush()

        total_tokens = 0

        try:
            # ---------------------------------------------------------- #
            # Agent 1 — Query Discovery
            # ---------------------------------------------------------- #
            agent1 = QueryDiscoveryAgent()
            profile_dict = {
                "name": profile.name,
                "domain": profile.domain,
                "industry": profile.industry,
                "description": profile.description,
                "competitors": profile.competitors or [],
            }

            logger.info("[Pipeline] Running Agent 1 (QueryDiscovery)")
            discovered_queries = agent1.run(profile=profile_dict)
            total_tokens += agent1.total_tokens_used

            run.queries_discovered = len(discovered_queries)
            logger.info("[Pipeline] Agent 1 complete: %d queries discovered", len(discovered_queries))

            # ---------------------------------------------------------- #
            # Persist discovered queries (before scoring, so we have UUIDs)
            # ---------------------------------------------------------- #
            query_records: list[DiscoveredQuery] = []
            for q in discovered_queries:
                record = DiscoveredQuery(
                    profile_uuid=profile.uuid,
                    run_uuid=run.uuid,
                    query_text=q["query_text"],
                )
                db.session.add(record)
                query_records.append(record)

            db.session.flush()  # Assign UUIDs

            # ---------------------------------------------------------- #
            # Agent 2 — Visibility Scoring (per-query, failures isolated)
            # ---------------------------------------------------------- #
            agent2 = VisibilityScoringAgent()
            scored_queries: list[dict] = []
            queries_scored_count = 0

            logger.info("[Pipeline] Running Agent 2 (VisibilityScoring) for %d queries", len(query_records))

            for record in query_records:
                try:
                    score_data = agent2.run(
                        query_text=record.query_text,
                        domain=profile.domain,
                        industry=profile.industry,
                    )

                    # Update the DB record with scoring data
                    record.estimated_search_volume = score_data["estimated_search_volume"]
                    record.competitive_difficulty = score_data["competitive_difficulty"]
                    record.opportunity_score = score_data["opportunity_score"]
                    record.domain_visible = score_data["domain_visible"]
                    record.visibility_position = score_data["visibility_position"]

                    scored_queries.append(
                        {
                            "query_uuid": record.uuid,
                            "query_text": record.query_text,
                            **score_data,
                        }
                    )
                    queries_scored_count += 1

                except (AgentError, Exception) as exc:
                    # Partial failure — log and continue with next query
                    logger.warning(
                        "[Pipeline] Agent 2 failed for query='%s': %s — skipping",
                        record.query_text[:60],
                        exc,
                    )
                    # Leave this record's scoring fields as None

            total_tokens += agent2.total_tokens_used
            run.queries_scored = queries_scored_count
            logger.info("[Pipeline] Agent 2 complete: %d/%d queries scored", queries_scored_count, len(query_records))

            # ---------------------------------------------------------- #
            # Agent 3 — Content Recommendations
            # ---------------------------------------------------------- #
            # Only feed queries where domain is NOT visible
            invisible_queries = [
                q for q in scored_queries
                if q.get("domain_visible") is False
            ]

            recommendations: list[dict] = []
            rec_records: list[ContentRecommendation] = []

            if invisible_queries:
                agent3 = ContentRecommendationAgent()
                logger.info(
                    "[Pipeline] Running Agent 3 (ContentRecommendation) for %d invisible queries",
                    len(invisible_queries),
                )
                try:
                    recommendations = agent3.run(
                        queries=invisible_queries,
                        profile=profile_dict,
                    )
                    total_tokens += agent3.total_tokens_used

                    # Persist recommendations
                    for rec in recommendations:
                        # Find the matching query UUID
                        query_uuid = self._find_query_uuid(
                            rec.get("query_text", ""), query_records
                        )
                        rec_record = ContentRecommendation(
                            profile_uuid=profile.uuid,
                            query_uuid=query_uuid or query_records[0].uuid,
                            content_type=rec["content_type"],
                            title=rec["title"],
                            rationale=rec["rationale"],
                            target_keywords=rec["target_keywords"],
                            priority=rec["priority"],
                        )
                        db.session.add(rec_record)
                        rec_records.append(rec_record)

                except (AgentError, Exception) as exc:
                    logger.warning("[Pipeline] Agent 3 failed: %s — continuing without recommendations", exc)
            else:
                logger.info("[Pipeline] All queries are visible — skipping Agent 3")

            # ---------------------------------------------------------- #
            # Finalise the pipeline run
            # ---------------------------------------------------------- #
            run.status = "completed"
            run.tokens_used = total_tokens
            run.completed_at = datetime.now(timezone.utc)
            profile.status = "completed"

            db.session.commit()

            logger.info(
                "[Pipeline] Run %s completed: discovered=%d scored=%d recs=%d tokens=%d",
                run.uuid,
                run.queries_discovered,
                run.queries_scored,
                len(rec_records),
                total_tokens,
            )

            # ---------------------------------------------------------- #
            # Build response
            # ---------------------------------------------------------- #
            top_opportunities = self._get_top_opportunities(scored_queries, n=3)

            return {
                "run_uuid": run.uuid,
                "profile_uuid": profile.uuid,
                "status": "completed",
                "queries_discovered": run.queries_discovered,
                "queries_scored": run.queries_scored,
                "tokens_used": total_tokens,
                "top_opportunities": top_opportunities,
                "recommendations": [r.to_dict() for r in rec_records],
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat(),
            }

        except AgentError as exc:
            return self._fail_run(run, profile, str(exc))
        except Exception as exc:
            logger.exception("[Pipeline] Unexpected error in run %s", run.uuid)
            return self._fail_run(run, profile, f"Unexpected error: {exc}")

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _fail_run(run: PipelineRun, profile: BusinessProfile, error_msg: str) -> dict:
        """Mark the run as failed, persist, and return an error response."""
        try:
            run.status = "failed"
            run.error_message = error_msg
            run.completed_at = datetime.now(timezone.utc)
            profile.status = "failed"
            db.session.commit()
        except Exception as commit_exc:
            logger.error("[Pipeline] Failed to persist failed run state: %s", commit_exc)
            db.session.rollback()

        return {
            "run_uuid": run.uuid,
            "profile_uuid": profile.uuid,
            "status": "failed",
            "error_message": error_msg,
            "queries_discovered": run.queries_discovered,
            "queries_scored": run.queries_scored,
        }

    @staticmethod
    def _get_top_opportunities(scored_queries: list[dict], n: int = 3) -> list[dict]:
        """Return the top-N queries sorted by opportunity_score descending."""
        sorted_qs = sorted(
            scored_queries,
            key=lambda q: q.get("opportunity_score", 0),
            reverse=True,
        )
        return [
            {
                "query_uuid": q.get("query_uuid"),
                "query_text": q["query_text"],
                "opportunity_score": q.get("opportunity_score"),
                "estimated_search_volume": q.get("estimated_search_volume"),
                "competitive_difficulty": q.get("competitive_difficulty"),
                "domain_visible": q.get("domain_visible"),
            }
            for q in sorted_qs[:n]
        ]

    @staticmethod
    def _find_query_uuid(query_text: str, query_records: list[DiscoveredQuery]) -> str | None:
        """Find the UUID of a query record by matching query_text."""
        query_lower = query_text.lower().strip()
        for record in query_records:
            if record.query_text.lower().strip() == query_lower:
                return record.uuid
        # Fuzzy match: check if the query text is a substring
        for record in query_records:
            if query_lower in record.query_text.lower() or record.query_text.lower() in query_lower:
                return record.uuid
        return None
