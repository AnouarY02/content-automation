"""
Evaluator — berekent de gewogen maturity_score en bepaalt de MaturityStatus.

Gewichten (som = 1.0):
  replication_score   → 0.25  (sterkste signaal: produceert het systeem kennis?)
  prediction_accuracy → 0.20  (kloppen de voorspellingen?)
  learning_delta      → 0.20  (verbetering aantoonbaar over tijd?)
  operator_adoption   → 0.20  (gebruiken operators het experiment-flow?)
  stability_index     → 0.15  (technische betrouwbaarheid)

Status drempelwaarden:
  INTERN_VOLWASSEN  composite ≥ 75  AND  replication ≥ 70  AND  stability ≥ 90  AND  adoption ≥ 70
  VALIDATED         composite ≥ 50  AND  replication ≥ 40
  EARLY             alles hieronder
"""

from __future__ import annotations

from maturity.models import (
    DimensionMaturity,
    MaturityMetric,
    MaturityScorecard,
    MaturityStatus,
    MaturityThresholds,
)

TH = MaturityThresholds()


class Evaluator:
    """
    Stateless evaluator: neemt ruwe scores, bouwt MaturityScorecard.

    Gebruik:
        scorecard = Evaluator.build(
            app_id="app_demo",
            replication=(60.0, dims, 9),
            prediction=(72.5, 8, "8/8 correct"),
            delta=(58.0, 12, "slope +0.8"),
            adoption=(75.0, 20, "15/20 via exp"),
            stability=(96.0, 210, "failure 4%"),
        )
    """

    @staticmethod
    def build(
        app_id: str,
        replication:  tuple[float, list[DimensionMaturity], int],
        prediction:   tuple[float, int, str],
        delta:        tuple[float, int, str],
        adoption:     tuple[float, int, str],
        stability:    tuple[float, int, str],
    ) -> MaturityScorecard:
        rep_score, dim_details, rep_evidence     = replication
        pred_score, pred_evidence, pred_notes     = prediction
        delta_score, delta_evidence, delta_notes  = delta
        adop_score, adop_evidence, adop_notes     = adoption
        stab_score, stab_evidence, stab_notes     = stability

        weights = TH.WEIGHTS
        targets = TH.METRIC_TARGETS

        metrics = [
            MaturityMetric(
                name="replication_score",
                raw_value=round(rep_score / 100, 3),
                score=rep_score,
                weight=weights["replication_score"],
                target=targets["replication_score"],
                evidence_count=rep_evidence,
                notes=_replication_note(dim_details),
            ),
            MaturityMetric(
                name="prediction_accuracy",
                raw_value=round(pred_score / 100, 3),
                score=pred_score,
                weight=weights["prediction_accuracy"],
                target=targets["prediction_accuracy"],
                evidence_count=pred_evidence,
                notes=pred_notes,
            ),
            MaturityMetric(
                name="learning_delta",
                raw_value=round(delta_score / 100, 3),
                score=delta_score,
                weight=weights["learning_delta"],
                target=targets["learning_delta"],
                evidence_count=delta_evidence,
                notes=delta_notes,
            ),
            MaturityMetric(
                name="operator_adoption",
                raw_value=round(adop_score / 100, 3),
                score=adop_score,
                weight=weights["operator_adoption"],
                target=targets["operator_adoption"],
                evidence_count=adop_evidence,
                notes=adop_notes,
            ),
            MaturityMetric(
                name="stability_index",
                raw_value=round(stab_score / 100, 3),
                score=stab_score,
                weight=weights["stability_index"],
                target=targets["stability_index"],
                evidence_count=stab_evidence,
                notes=stab_notes,
            ),
        ]

        composite = _weighted_composite(metrics)
        status    = _classify(composite, rep_score, stab_score, adop_score)

        return MaturityScorecard(
            app_id=app_id,
            maturity_score=round(composite, 1),
            status=status,
            metrics=metrics,
            dimension_details=dim_details,
            replication_score=rep_score,
            prediction_accuracy=pred_score,
            learning_delta=delta_score,
            operator_adoption=adop_score,
            stability_index=stab_score,
            experiments_analyzed=rep_evidence,
            posts_analyzed=delta_evidence,
            audit_entries_analyzed=stab_evidence,
        )


# ── Interne hulpfuncties ──────────────────────────────────────────────

def _weighted_composite(metrics: list[MaturityMetric]) -> float:
    """Gewogen gemiddelde van alle metric scores."""
    return sum(m.score * m.weight for m in metrics)


def _classify(
    composite:    float,
    replication:  float,
    stability:    float,
    adoption:     float,
) -> MaturityStatus:
    """
    Bepaal MaturityStatus op basis van drempelwaarden.

    INTERN_VOLWASSEN vereist dat ALLE vier condities voldaan zijn:
      composite     ≥ 75
      replication   ≥ 70
      stability     ≥ 90
      adoption      ≥ 70

    VALIDATED:
      composite     ≥ 50
      replication   ≥ 40

    Anders: EARLY.
    """
    if (
        composite   >= TH.INTERN_VOLWASSEN_COMPOSITE   and
        replication >= TH.INTERN_VOLWASSEN_REPLICATION and
        stability   >= TH.INTERN_VOLWASSEN_STABILITY   and
        adoption    >= TH.INTERN_VOLWASSEN_ADOPTION
    ):
        return MaturityStatus.INTERN_VOLWASSEN

    if (
        composite   >= TH.VALIDATED_COMPOSITE and
        replication >= TH.VALIDATED_REPLICATION
    ):
        return MaturityStatus.VALIDATED

    return MaturityStatus.EARLY


def _replication_note(dims: list[DimensionMaturity]) -> str:
    qualifying = sum(1 for d in dims if d.contributes_to_replication)
    eligible   = sum(1 for d in dims if d.experiment_count >= 3)
    total      = len(dims)
    return (
        f"{qualifying}/{eligible} dimensies kwalificeren "
        f"(≥3 exp, consistent winner, conf≥0.7) — "
        f"{total} dimensies totaal"
    )
