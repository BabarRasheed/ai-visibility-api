"""
app/utils/scoring.py — Opportunity score formula.

Formula Design:
    opportunity_score = w1 * norm_volume + w2 * ease + w3 * visibility_gap

Where:
    norm_volume    = min(search_volume, MAX_VOLUME) / MAX_VOLUME
                     Normalises search volume to [0, 1] with a soft cap.
                     Higher volume = higher opportunity.

    ease           = 1 - (difficulty / 100)
                     Inverts competitive difficulty so lower difficulty = higher score.
                     A difficulty of 0 (easiest) gives ease=1.0.
                     A difficulty of 100 (hardest) gives ease=0.0.

    visibility_gap = 1.0 if domain is NOT visible, 0.0 if already visible.
                     This is the most important signal: if you're already there,
                     there's no gap to close.

Weights:
    w_volume  = 0.35  — Volume matters but it's the least actionable factor.
    w_ease    = 0.30  — Difficulty determines feasibility.
    w_gap     = 0.35  — Visibility gap is the primary driver of opportunity.

Rationale for weight selection:
    - volume and gap share the top weight (0.35 each) because both indicate
      the SIZE of the opportunity — high-volume queries with no presence = goldmine.
    - ease (0.30) acts as a feasibility multiplier — a high-volume invisible query
      with difficulty=95 might still not be worth pursuing.

Score interpretation:
    >= 0.80  Excellent opportunity — prioritise immediately
    0.60–0.79  Good opportunity
    0.40–0.59  Moderate — consider if resources allow
    < 0.40   Low priority
"""

import math


# Soft cap for search volume normalisation.
# Queries above this volume are treated as equally "high volume".
MAX_VOLUME: int = 10_000

# Weights (must sum to 1.0)
W_VOLUME: float = 0.35
W_EASE: float = 0.30
W_GAP: float = 0.35

assert abs(W_VOLUME + W_EASE + W_GAP - 1.0) < 1e-9, "Weights must sum to 1.0"


def compute_opportunity_score(
    search_volume: int,
    difficulty: int,
    domain_visible: bool,
) -> float:
    """
    Compute the opportunity score for a single query.

    Args:
        search_volume: Estimated monthly searches (≥ 0).
        difficulty:    Competitive difficulty (0–100).
        domain_visible: Whether the target domain currently appears.

    Returns:
        Float in [0.0, 1.0].
    """
    # Normalise search volume with a soft cap
    norm_volume = min(max(search_volume, 0), MAX_VOLUME) / MAX_VOLUME

    # Invert difficulty (lower difficulty = higher opportunity)
    difficulty_clamped = min(100, max(0, difficulty))
    ease = 1.0 - (difficulty_clamped / 100.0)

    # Visibility gap: 1.0 if NOT visible (opportunity exists), 0.0 if already there
    visibility_gap = 0.0 if domain_visible else 1.0

    # Weighted sum
    raw_score = (W_VOLUME * norm_volume) + (W_EASE * ease) + (W_GAP * visibility_gap)

    # Clamp to [0, 1] and round to 4 decimal places
    return round(min(1.0, max(0.0, raw_score)), 4)


def priority_from_score(score: float) -> str:
    """Map an opportunity score to a priority label."""
    if score >= 0.70:
        return "high"
    if score >= 0.40:
        return "medium"
    return "low"
