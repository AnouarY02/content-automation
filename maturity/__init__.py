"""
Maturity — Intern Volwassen Scorecard systeem.

Meet objectief of het Content Quality & Experiment OS intern volwassen is
aan de hand van 5 meetbare dimensies:
  - replication_score    (25%) — experimenten produceren repliceerbare resultaten
  - prediction_accuracy  (20%) — quality scorer voorspelt winners correct
  - learning_delta       (20%) — aantoonbare kwaliteitsverbetering over tijd
  - operator_adoption    (20%) — operators gebruiken het experiment-flow
  - stability_index      (15%) — technische betrouwbaarheid van het systeem
"""
from maturity.models import (
    DimensionMaturity,
    MaturityMetric,
    MaturityScorecard,
    MaturitySnapshot,
    MaturityStatus,
)
from maturity.scorecard import ScorecardBuilder

__all__ = [
    "DimensionMaturity",
    "MaturityMetric",
    "MaturityScorecard",
    "MaturitySnapshot",
    "MaturityStatus",
    "ScorecardBuilder",
]
