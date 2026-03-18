"""
Unit tests: SQLite repository implementaties.

Dekt:
  - SqliteCampaignRepository: save, get, list, list_pending, delete
  - SqliteExperimentRepository: save, get, list_by_app, list_measuring,
                                 get_by_campaign, get_concluded_dimensions
  - SqliteMaturityRepository: save_scorecard, get_latest, get_history
  - Protocol conformance: isinstance checks
  - Tenant isolatie via gescheiden databases
"""

import pytest

import backend.repository.sqlite_db as db_module
from backend.repository.sqlite_campaigns import SqliteCampaignRepository
from backend.repository.sqlite_experiments import SqliteExperimentRepository
from backend.repository.sqlite_maturity import SqliteMaturityRepository
from backend.repository.base import (
    ICampaignRepository,
    IExperimentRepository,
    IMaturityRepository,
)
from backend.models.campaign import CampaignBundle, CampaignStatus
from experiments.models import (
    Experiment,
    ExperimentDimension,
    ExperimentStatus,
    Hypothesis,
    Variant,
    VariantSpec,
)
from maturity.models import MaturityScorecard, MaturityStatus


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Isoleer SQLite databases naar tmp_path."""
    monkeypatch.setattr(db_module, "DB_DIR", tmp_path)
    return tmp_path


# ── Helpers ──────────────────────────────────────────────────────────

def _make_bundle(app_id: str = "app_test", status=CampaignStatus.PENDING_APPROVAL):
    return CampaignBundle(app_id=app_id, platform="tiktok", status=status)


def _make_experiment(
    exp_id: str = "exp_001",
    app_id: str = "app_test",
    campaign_id: str = "camp_001",
    status: ExperimentStatus = ExperimentStatus.PENDING,
):
    return Experiment(
        experiment_id=exp_id,
        campaign_id=campaign_id,
        app_id=app_id,
        hypothesis=Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Test",
        ),
        variants=[],
        status=status,
    )


def _make_scorecard(app_id: str = "app_test"):
    return MaturityScorecard(
        app_id=app_id,
        maturity_score=67.4,
        status=MaturityStatus.VALIDATED,
        replication_score=60.0,
        prediction_accuracy=72.5,
        learning_delta=58.0,
        operator_adoption=75.0,
        stability_index=95.0,
    )


# ── Campaign Repository ─────────────────────────────────────────────

class TestSqliteCampaignRepository:
    def test_protocol_conformance(self):
        repo = SqliteCampaignRepository()
        assert isinstance(repo, ICampaignRepository)

    def test_save_en_get(self):
        repo = SqliteCampaignRepository()
        bundle = _make_bundle()
        repo.save(bundle)
        loaded = repo.get(bundle.id)
        assert loaded is not None
        assert loaded.id == bundle.id

    def test_get_niet_gevonden(self):
        repo = SqliteCampaignRepository()
        assert repo.get("niet_bestaand") is None

    def test_upsert(self):
        repo = SqliteCampaignRepository()
        bundle = _make_bundle()
        repo.save(bundle)
        updated = bundle.model_copy(update={"platform": "instagram"})
        repo.save(updated)
        loaded = repo.get(bundle.id)
        assert loaded.platform == "instagram"

    def test_list_filter_app_id(self):
        repo = SqliteCampaignRepository()
        repo.save(_make_bundle(app_id="app_a"))
        repo.save(_make_bundle(app_id="app_b"))
        result = repo.list(app_id="app_a")
        assert len(result) == 1

    def test_list_filter_status(self):
        repo = SqliteCampaignRepository()
        repo.save(_make_bundle(status=CampaignStatus.PENDING_APPROVAL))
        repo.save(_make_bundle(status=CampaignStatus.APPROVED))
        result = repo.list(status=CampaignStatus.PENDING_APPROVAL)
        assert len(result) == 1

    def test_list_pending(self):
        repo = SqliteCampaignRepository()
        repo.save(_make_bundle(status=CampaignStatus.PENDING_APPROVAL))
        repo.save(_make_bundle(status=CampaignStatus.FAILED))
        pending = repo.list_pending()
        assert len(pending) == 1

    def test_delete(self):
        repo = SqliteCampaignRepository()
        bundle = _make_bundle()
        repo.save(bundle)
        assert repo.delete(bundle.id) is True
        assert repo.get(bundle.id) is None

    def test_delete_niet_gevonden(self):
        repo = SqliteCampaignRepository()
        assert repo.delete("onbekend") is False


# ── Experiment Repository ────────────────────────────────────────────

class TestSqliteExperimentRepository:
    def test_protocol_conformance(self):
        repo = SqliteExperimentRepository()
        assert isinstance(repo, IExperimentRepository)

    def test_save_en_get(self):
        repo = SqliteExperimentRepository()
        exp = _make_experiment()
        repo.save(exp)
        loaded = repo.get("exp_001")
        assert loaded is not None
        assert loaded.experiment_id == "exp_001"

    def test_get_niet_gevonden(self):
        repo = SqliteExperimentRepository()
        assert repo.get("onbekend") is None

    def test_list_by_app(self):
        repo = SqliteExperimentRepository()
        repo.save(_make_experiment(exp_id="exp_a1", app_id="app_a"))
        repo.save(_make_experiment(exp_id="exp_b1", app_id="app_b"))
        result = repo.list_by_app("app_a")
        assert len(result) == 1

    def test_list_measuring(self):
        repo = SqliteExperimentRepository()
        repo.save(_make_experiment(exp_id="exp_m1", status=ExperimentStatus.MEASURING))
        repo.save(_make_experiment(exp_id="exp_p1", status=ExperimentStatus.PENDING))
        result = repo.list_measuring()
        assert len(result) == 1
        assert result[0].status == ExperimentStatus.MEASURING

    def test_get_by_campaign(self):
        repo = SqliteExperimentRepository()
        repo.save(_make_experiment(exp_id="exp_c1", campaign_id="camp_x"))
        loaded = repo.get_by_campaign("camp_x")
        assert loaded is not None
        assert loaded.experiment_id == "exp_c1"

    def test_get_by_campaign_niet_gevonden(self):
        repo = SqliteExperimentRepository()
        assert repo.get_by_campaign("onbekend") is None

    def test_get_concluded_dimensions(self):
        repo = SqliteExperimentRepository()
        repo.save(_make_experiment(exp_id="e1", status=ExperimentStatus.CONCLUDED))
        repo.save(_make_experiment(exp_id="e2", status=ExperimentStatus.CONCLUDED))
        repo.save(_make_experiment(exp_id="e3", status=ExperimentStatus.PENDING))
        dims = repo.get_concluded_dimensions("app_test")
        assert dims.get("hook_type", 0) == 2


# ── Maturity Repository ─────────────────────────────────────────────

class TestSqliteMaturityRepository:
    def test_protocol_conformance(self):
        repo = SqliteMaturityRepository()
        assert isinstance(repo, IMaturityRepository)

    def test_save_en_get_latest(self):
        repo = SqliteMaturityRepository()
        sc = _make_scorecard()
        repo.save_scorecard(sc)
        loaded = repo.get_latest("app_test")
        assert loaded is not None
        assert loaded.maturity_score == pytest.approx(67.4)

    def test_get_latest_niet_gevonden(self):
        repo = SqliteMaturityRepository()
        assert repo.get_latest("onbekend") is None

    def test_save_overschrijft_latest(self):
        repo = SqliteMaturityRepository()
        sc1 = _make_scorecard()
        repo.save_scorecard(sc1)
        sc2 = MaturityScorecard(
            app_id="app_test", maturity_score=80.0, status=MaturityStatus.INTERN_VOLWASSEN,
            replication_score=75.0, prediction_accuracy=80.0, learning_delta=70.0,
            operator_adoption=85.0, stability_index=96.0,
        )
        repo.save_scorecard(sc2)
        loaded = repo.get_latest("app_test")
        assert loaded.maturity_score == pytest.approx(80.0)

    def test_get_history(self):
        repo = SqliteMaturityRepository()
        repo.save_scorecard(_make_scorecard())
        repo.save_scorecard(MaturityScorecard(
            app_id="app_test", maturity_score=72.0, status=MaturityStatus.VALIDATED,
            replication_score=65.0, prediction_accuracy=75.0, learning_delta=60.0,
            operator_adoption=78.0, stability_index=94.0,
        ))
        history = repo.get_history("app_test")
        assert len(history) == 2

    def test_get_history_limit(self):
        repo = SqliteMaturityRepository()
        for i in range(5):
            repo.save_scorecard(MaturityScorecard(
                app_id="app_test", maturity_score=50.0 + i, status=MaturityStatus.EARLY,
                replication_score=30.0, prediction_accuracy=40.0, learning_delta=35.0,
                operator_adoption=45.0, stability_index=80.0,
            ))
        history = repo.get_history("app_test", limit=3)
        assert len(history) == 3

    def test_get_history_leeg(self):
        repo = SqliteMaturityRepository()
        assert repo.get_history("onbekend") == []


# ── Tenant isolatie ─────────────────────────────────────────────────

class TestTenantIsolatie:
    def test_campagnes_gescheiden(self):
        repo_a = SqliteCampaignRepository(tenant_id="t_a")
        repo_b = SqliteCampaignRepository(tenant_id="t_b")
        repo_a.save(_make_bundle())
        assert len(repo_a.list(tenant_id="t_a")) == 1
        assert len(repo_b.list(tenant_id="t_b")) == 0

    def test_experimenten_gescheiden(self):
        repo_a = SqliteExperimentRepository(tenant_id="t_a")
        repo_b = SqliteExperimentRepository(tenant_id="t_b")
        repo_a.save(_make_experiment())
        assert repo_a.get("exp_001", tenant_id="t_a") is not None
        assert repo_b.get("exp_001", tenant_id="t_b") is None
