"""
scorer.py — HOT / WARM / COLD classification logic.

Classification rules:
  HOT  — strong fit on 3-4 platforms (score >= 7) AND senior contact
          AND no clearly negative news signals
  WARM — fit on 1-2 platforms (score >= 5) OR junior/mid contact
          OR mixed signals
  COLD — no clear fit (all platform scores < 5)
"""
from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

# Score thresholds
STRONG_FIT_THRESHOLD = 7   # per-platform score >= this = "strong fit"
MODERATE_FIT_THRESHOLD = 5  # per-platform score >= this = "moderate fit"

Classification = Literal["HOT", "WARM", "COLD"]


def extract_score(score_str: str) -> int:
    """
    Parse the numeric score from strings like "8 — Sehr guter Fit" or plain "8".

    Returns 0 if parsing fails.
    """
    if not score_str:
        return 0
    # Take the first token; handle "8/10", "8 — ...", "8: ..." etc.
    token = score_str.strip().split()[0]
    token = token.split("/")[0].split("—")[0].split(":")[0].strip()
    try:
        return max(0, min(10, int(token)))
    except ValueError:
        return 0


def classify(
    shoop_score: int,
    igraal_score: int,
    mydealz_score: int,
    gutscheine_score: int,
    contact_seniority: str = "mid",
) -> tuple[Classification, int]:
    """
    Classify a lead and return (classification, score_total).

    score_total is the raw sum of the four platform scores (0-40).
    """
    scores = [shoop_score, igraal_score, mydealz_score, gutscheine_score]
    score_total = sum(scores)
    strong_fits = sum(1 for s in scores if s >= STRONG_FIT_THRESHOLD)
    moderate_fits = sum(1 for s in scores if s >= MODERATE_FIT_THRESHOLD)

    is_senior = contact_seniority == "senior"
    is_junior = contact_seniority == "junior"

    if strong_fits >= 3 and is_senior:
        classification: Classification = "HOT"
    elif strong_fits >= 3 and not is_junior:
        # Strong fit on 3+ platforms but not explicitly senior → still HOT
        classification = "HOT"
    elif moderate_fits >= 2 or strong_fits >= 1:
        classification = "WARM"
    else:
        classification = "COLD"

    logger.info(
        "Klassifikation: %s | Scores: Shoop=%d iGraal=%d mydealz=%d Gutscheine=%d | Total=%d | Seniority=%s",
        classification, shoop_score, igraal_score, mydealz_score, gutscheine_score,
        score_total, contact_seniority,
    )
    return classification, score_total
