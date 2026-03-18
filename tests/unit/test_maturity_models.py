"""
Unit tests: maturity/models.py — Pydantic modellen voor maturity scorecard.

Dekt:
  - MaturityStatus enum waarden
  - MaturityMetric: aanmaken + velden
  - DimensionMaturity: contributes_to_replication logica
  - MaturityScorecard: volledige scorecard met defaults
  - MaturitySnapshot: historisch record
  - MaturityThresholds: drempelwaarden en gewichten
"""

import pytest

from maturity.models import (
    DimensionMaturity,
    MaturityMetric,
    MaturityScorecard,
    MaturitySnapshot,
    MaturityStatus,
    MaturityThresholds,
)


class TestMaturityStatus:
    def test_drie_niveaus(self):
        assert len(MaturityStatus) == 3

    def test_waarden(self):
        assert MaturityStatus.EARLY.value == "early"
        assert MaturityStatus.VALIDATED.value == "validated"
        assert MaturityStatus.INTERN_VOLWASSEN.value == "intern_volwassen"


class TestMaturityMetric:
    def test_aanmaken(self):
        m = MaturityMetric(
            name="replication_score",
            raw_value=0.6,
            score=60.0,
            weight=0.25,
            target=60.0,
            evidence_count=9,
        )
        assert m.name == "replication_score"
        assert m.score == 60.0

    def test_notes_default_leeg(self):
        m = MaturityMetric(
            name="test", raw_value=0.5, score=50.0,
            weight=0.2, target=55.0, evidence_count=3,
        )
        assert m.notes == ""


class TestDimensionMaturity:
    def test_contributes_default_false(self):
        d = DimensionMaturity(
            dimension="hook_type",
            experiment_count=5,
            has_consistent_winner=True,
        )
        assert d.contributes_to_replication is False

    def test_met_volledige_data(self):
        d = DimensionMaturity(
            dimension="hook_type",
            experiment_count=5,
            has_consistent_winner=True,
            winner_value="bold_claim",
            winner_consistency=0.8,
            causal_confidence_avg=0.75,
            contributes_to_replication=True,
        )
        assert d.contributes_to_replication is True
        assert d.winner_value == "bold_claim"


class TestMaturityScorecard:
    def test_volledige_scorecard(self):
        sc = MaturityScorecard(
            app_id="app_test",
            maturity_score=67.4,
            status=MaturityStatus.VALIDATED,
            replication_score=60.0,
            prediction_accuracy=72.5,
            learning_delta=58.0,
            operator_adoption=75.0,
            stability_index=95.0,
        )
        assert sc.app_id == "app_test"
        assert sc.scorecard_id.startswith("msc_")
        assert sc.tenant_id == "default"

    def test_default_waarden(self):
        sc = MaturityScorecard(
            app_id="app_test",
            maturity_score=50.0,
            status=MaturityStatus.EARLY,
            replication_score=30.0,
            prediction_accuracy=40.0,
            learning_delta=35.0,
            operator_adoption=45.0,
            stability_index=80.0,
        )
        assert sc.metrics == []
        assert sc.dimension_details == []
        assert sc.experiments_analyzed == 0


class TestMaturitySnapshot:
    def test_aanmaken(self):
        sc = MaturityScorecard(
            app_id="app_test",
            maturity_score=50.0,
            status=MaturityStatus.EARLY,
            replication_score=30.0,
            prediction_accuracy=40.0,
            learning_delta=35.0,
            operator_adoption=45.0,
            stability_index=80.0,
        )
        snap = MaturitySnapshot(
            app_id="app_test",
            maturity_score=50.0,
            status=MaturityStatus.EARLY,
            scorecard=sc,
        )
        assert snap.snapshot_id.startswith("snp_")
        assert snap.tenant_id == "default"


class TestMaturityThresholds:
    def test_gewichten_som_1(self):
        th = MaturityThresholds()
        assert sum(th.WEIGHTS.values()) == pytest.approx(1.0)

    def test_vijf_metrics(self):
        th = MaturityThresholds()
        assert len(th.WEIGHTS) == 5
        assert len(th.METRIC_TARGETS) == 5

    def test_intern_volwassen_drempels(self):
        th = MaturityThresholds()
        assert th.INTERN_VOLWASSEN_COMPOSITE == 75.0
        assert th.INTERN_VOLWASSEN_REPLICATION == 70.0
        assert th.INTERN_VOLWASSEN_STABILITY == 90.0
        assert th.INTERN_VOLWASSEN_ADOPTION == 70.0

    def test_validated_drempels(self):
        th = MaturityThresholds()
        assert th.VALIDATED_COMPOSITE == 50.0
        assert th.VALIDATED_REPLICATION == 40.0
