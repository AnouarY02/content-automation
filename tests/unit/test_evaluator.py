"""
Unit tests: maturity/evaluator.py — gewogen composite score en status classificatie.

Dekt:
  - Evaluator.build(): bouw volledige scorecard
  - _weighted_composite(): correcte gewogen berekening
  - _classify(): EARLY / VALIDATED / INTERN_VOLWASSEN drempelwaarden
  - Randgevallen: precies op drempel, nul scores
"""

import pytest

from maturity.evaluator import Evaluator, _classify, _weighted_composite
from maturity.models import (
    DimensionMaturity,
    MaturityMetric,
    MaturityScorecard,
    MaturityStatus,
    MaturityThresholds,
)

TH = MaturityThresholds()


def _dummy_dims(count: int = 2) -> list[DimensionMaturity]:
    return [
        DimensionMaturity(
            dimension=f"dim_{i}",
            experiment_count=5,
            has_consistent_winner=True,
            winner_value="bold_claim",
            winner_consistency=0.8,
            causal_confidence_avg=0.75,
            contributes_to_replication=True,
        )
        for i in range(count)
    ]


class TestEvaluatorBuild:
    def test_bouwt_volledige_scorecard(self):
        sc = Evaluator.build(
            app_id="app_test",
            replication=(60.0, _dummy_dims(), 9),
            prediction=(72.5, 8, "8/8 correct"),
            delta=(58.0, 12, "slope +0.8"),
            adoption=(75.0, 20, "15/20 via exp"),
            stability=(96.0, 210, "failure 4%"),
        )
        assert isinstance(sc, MaturityScorecard)
        assert sc.app_id == "app_test"
        assert len(sc.metrics) == 5
        assert sc.maturity_score > 0

    def test_subscores_correct_doorgestuurd(self):
        sc = Evaluator.build(
            app_id="app_test",
            replication=(60.0, _dummy_dims(), 9),
            prediction=(72.5, 8, "notes"),
            delta=(58.0, 12, "notes"),
            adoption=(75.0, 20, "notes"),
            stability=(96.0, 210, "notes"),
        )
        assert sc.replication_score == 60.0
        assert sc.prediction_accuracy == 72.5
        assert sc.learning_delta == 58.0
        assert sc.operator_adoption == 75.0
        assert sc.stability_index == 96.0

    def test_metadata_correct(self):
        sc = Evaluator.build(
            app_id="app_test",
            replication=(60.0, _dummy_dims(), 9),
            prediction=(72.5, 8, "n"),
            delta=(58.0, 12, "n"),
            adoption=(75.0, 20, "n"),
            stability=(96.0, 210, "n"),
        )
        assert sc.experiments_analyzed == 9
        assert sc.posts_analyzed == 12
        assert sc.audit_entries_analyzed == 210


class TestWeightedComposite:
    def test_gewichten_som_is_1(self):
        assert sum(TH.WEIGHTS.values()) == pytest.approx(1.0)

    def test_berekening_correct(self):
        metrics = [
            MaturityMetric(name="replication_score", raw_value=0.6, score=60.0,
                           weight=0.25, target=60.0, evidence_count=5),
            MaturityMetric(name="prediction_accuracy", raw_value=0.7, score=70.0,
                           weight=0.20, target=65.0, evidence_count=5),
            MaturityMetric(name="learning_delta", raw_value=0.5, score=50.0,
                           weight=0.20, target=55.0, evidence_count=5),
            MaturityMetric(name="operator_adoption", raw_value=0.8, score=80.0,
                           weight=0.20, target=80.0, evidence_count=5),
            MaturityMetric(name="stability_index", raw_value=0.95, score=95.0,
                           weight=0.15, target=95.0, evidence_count=5),
        ]
        expected = 60*0.25 + 70*0.20 + 50*0.20 + 80*0.20 + 95*0.15
        assert _weighted_composite(metrics) == pytest.approx(expected)

    def test_nul_scores_geeft_nul(self):
        metrics = [
            MaturityMetric(name="m", raw_value=0, score=0, weight=1.0,
                           target=50, evidence_count=0),
        ]
        assert _weighted_composite(metrics) == 0.0


class TestClassify:
    def test_early_bij_lage_scores(self):
        assert _classify(30.0, 20.0, 50.0, 30.0) == MaturityStatus.EARLY

    def test_validated_bij_voldoende_composite_en_replication(self):
        assert _classify(55.0, 45.0, 50.0, 30.0) == MaturityStatus.VALIDATED

    def test_validated_niet_bij_lage_replication(self):
        # composite >= 50 maar replication < 40
        assert _classify(55.0, 35.0, 95.0, 80.0) == MaturityStatus.EARLY

    def test_intern_volwassen_bij_alle_drempels(self):
        assert _classify(80.0, 75.0, 95.0, 75.0) == MaturityStatus.INTERN_VOLWASSEN

    def test_intern_volwassen_niet_bij_lage_stability(self):
        # composite >= 75, replication >= 70, maar stability < 90
        assert _classify(80.0, 75.0, 85.0, 75.0) == MaturityStatus.VALIDATED

    def test_intern_volwassen_niet_bij_lage_adoption(self):
        # composite >= 75, replication >= 70, stability >= 90, maar adoption < 70
        assert _classify(80.0, 75.0, 95.0, 60.0) == MaturityStatus.VALIDATED

    def test_exact_op_validated_drempel(self):
        assert _classify(50.0, 40.0, 50.0, 30.0) == MaturityStatus.VALIDATED

    def test_exact_op_intern_volwassen_drempel(self):
        assert _classify(75.0, 70.0, 90.0, 70.0) == MaturityStatus.INTERN_VOLWASSEN
