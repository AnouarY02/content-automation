"""
DAG 3 — Integration tests: backend/api/experiments.py

Gebruikt FastAPI TestClient met geïsoleerde ExperimentStore (tmp_path).
Geen echte LLM-aanroepen.

Dekt:
  GET  /api/experiments/?app_id=x     — lege lijst, lijst met experimenten
  GET  /api/experiments/pending?app_id=x — alleen PENDING/QUALITY_FAIL
  GET  /api/experiments/{id}           — 200 met data, 404 bij onbekend ID
  POST /api/experiments/{id}/select-variant
       — 200 bij geldige keuze, 400 bij geblokkeerde/onbekende variant
  GET  /api/experiments/{id}/comparison
       — 200 bij CONCLUDED, 409 bij MEASURING, 404 bij onbekend ID

Hardening (400/404/409) volledig gedekt.
"""

import unittest.mock as mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import experiments.experiment_store as store_module
from experiments.experiment_store import ExperimentStore
from experiments.models import (
    Experiment, ExperimentDimension, ExperimentStatus,
    Hypothesis, Variant, VariantSpec,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Isoleer de ExperimentStore naar tmp_path voor elke test."""
    monkeypatch.setattr(store_module, "ROOT", tmp_path)
    store_dir = tmp_path / "data" / "experiments"
    store_dir.mkdir(parents=True)
    monkeypatch.setattr(store_module, "STORE_DIR", store_dir)
    monkeypatch.setattr(store_module, "INDEX_PATH", store_dir / "_index.json")
    return store_dir


@pytest.fixture
def client():
    """Minimale TestClient met alleen de experiments router."""
    from backend.api.experiments import router
    app = FastAPI()
    app.include_router(router, prefix="/api/experiments")
    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────

def _make_hypothesis(
    dimension: ExperimentDimension = ExperimentDimension.HOOK_TYPE,
) -> Hypothesis:
    return Hypothesis(
        dimension=dimension,
        control_value="curiosity_gap",
        challenger_value="bold_claim",
        rationale="Test",
    )


def _make_variant(
    variant_id: str,
    exp_id: str,
    label: str = "control",
    quality_score: dict | None = None,
) -> Variant:
    return Variant(
        variant_id=variant_id,
        experiment_id=exp_id,
        label=label,
        spec=VariantSpec(
            label=label,
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value="curiosity_gap" if label == "control" else "bold_claim",
        ),
        idea={}, script={}, caption={},
        quality_score=quality_score,
    )


def _make_experiment(
    exp_id: str = "exp_api001",
    app_id: str = "app_test",
    campaign_id: str = "camp_001",
    status: ExperimentStatus = ExperimentStatus.PENDING,
    with_variants: bool = True,
    winning_variant_id: str | None = None,
) -> Experiment:
    variants = []
    if with_variants:
        variants = [
            _make_variant("var_ctrl_001", exp_id, "control",
                          quality_score={"passed": True, "composite_score": 75.0}),
            _make_variant("var_chal_001", exp_id, "challenger_A",
                          quality_score={"passed": True, "composite_score": 72.0}),
        ]
    exp = Experiment(
        experiment_id=exp_id,
        campaign_id=campaign_id,
        app_id=app_id,
        hypothesis=_make_hypothesis(),
        variants=variants,
        status=status,
        winning_variant_id=winning_variant_id,
    )
    if status == ExperimentStatus.CONCLUDED:
        exp.causal_confidence = 0.72
        exp.conclusion = "bold_claim presteert beter"
    ExperimentStore().save(exp)
    return exp


# ── GET / (lijst) ─────────────────────────────────────────────────────

class TestListExperiments:
    def test_lege_lijst_bij_onbekende_app(self, client):
        r = client.get("/api/experiments/", params={"app_id": "app_unknown"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["experiments"] == []

    def test_retourneert_experiment_voor_app(self, client):
        _make_experiment(app_id="app_list")
        r = client.get("/api/experiments/", params={"app_id": "app_list"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["experiments"][0]["experiment_id"] == "exp_api001"

    def test_retourneert_alleen_eigen_app(self, client):
        _make_experiment(exp_id="exp_a", app_id="app_a")
        _make_experiment(exp_id="exp_b", app_id="app_b")
        r = client.get("/api/experiments/", params={"app_id": "app_a"})
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_app_id_verplicht(self, client):
        """Zonder app_id → 422 Unprocessable Entity."""
        r = client.get("/api/experiments/")
        assert r.status_code == 422

    def test_app_id_in_response(self, client):
        _make_experiment(app_id="app_check")
        r = client.get("/api/experiments/", params={"app_id": "app_check"})
        assert r.json()["app_id"] == "app_check"

    def test_meerdere_experimenten(self, client):
        _make_experiment(exp_id="exp_m1", app_id="app_multi")
        _make_experiment(exp_id="exp_m2", app_id="app_multi")
        r = client.get("/api/experiments/", params={"app_id": "app_multi"})
        assert r.json()["total"] == 2


# ── GET /pending ──────────────────────────────────────────────────────

class TestListPending:
    def test_retourneert_pending_experimenten(self, client):
        _make_experiment(exp_id="exp_p1", app_id="app_pend", status=ExperimentStatus.PENDING)
        r = client.get("/api/experiments/pending", params={"app_id": "app_pend"})
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_retourneert_quality_fail(self, client):
        _make_experiment(exp_id="exp_qf", app_id="app_qf", status=ExperimentStatus.QUALITY_FAIL)
        r = client.get("/api/experiments/pending", params={"app_id": "app_qf"})
        assert r.json()["total"] == 1

    def test_sluit_measuring_uit(self, client):
        _make_experiment(exp_id="exp_meas", app_id="app_meas2", status=ExperimentStatus.MEASURING)
        r = client.get("/api/experiments/pending", params={"app_id": "app_meas2"})
        assert r.json()["total"] == 0

    def test_sluit_concluded_uit(self, client):
        _make_experiment(exp_id="exp_conc", app_id="app_conc2", status=ExperimentStatus.CONCLUDED)
        r = client.get("/api/experiments/pending", params={"app_id": "app_conc2"})
        assert r.json()["total"] == 0

    def test_lege_lijst_bij_geen_pending(self, client):
        r = client.get("/api/experiments/pending", params={"app_id": "app_no_pending"})
        assert r.status_code == 200
        assert r.json()["experiments"] == []


# ── GET /{experiment_id} ──────────────────────────────────────────────

class TestGetExperiment:
    def test_200_met_experiment_data(self, client):
        _make_experiment(exp_id="exp_get001")
        r = client.get("/api/experiments/exp_get001")
        assert r.status_code == 200
        data = r.json()
        assert data["experiment_id"] == "exp_get001"

    def test_response_bevat_varianten(self, client):
        _make_experiment(exp_id="exp_vars001")
        r = client.get("/api/experiments/exp_vars001")
        data = r.json()
        assert "variants" in data
        assert len(data["variants"]) == 2

    def test_response_bevat_hypothesis(self, client):
        _make_experiment(exp_id="exp_hyp001")
        r = client.get("/api/experiments/exp_hyp001")
        data = r.json()
        assert "hypothesis" in data
        assert data["hypothesis"]["dimension"] == "hook_type"

    def test_404_bij_onbekend_id(self, client):
        r = client.get("/api/experiments/exp_does_not_exist")
        assert r.status_code == 404

    def test_404_detail_bevat_experiment_id(self, client):
        r = client.get("/api/experiments/exp_missing_xyz")
        assert "exp_missing_xyz" in r.json()["detail"]

    def test_response_bevat_status(self, client):
        _make_experiment(exp_id="exp_status001", status=ExperimentStatus.MEASURING)
        r = client.get("/api/experiments/exp_status001")
        assert r.json()["status"] == "measuring"


# ── POST /{experiment_id}/select-variant ─────────────────────────────

class TestSelectVariant:
    def test_200_bij_geldige_selectie(self, client):
        _make_experiment(exp_id="exp_sel001")
        r = client.post(
            "/api/experiments/exp_sel001/select-variant",
            json={"variant_id": "var_ctrl_001", "approved_by": "operator"},
        )
        assert r.status_code == 200

    def test_response_bevat_status_selected(self, client):
        _make_experiment(exp_id="exp_sel002")
        r = client.post(
            "/api/experiments/exp_sel002/select-variant",
            json={"variant_id": "var_ctrl_001", "approved_by": "operator"},
        )
        assert r.json()["status"] == "selected"

    def test_response_bevat_selected_variant_id(self, client):
        _make_experiment(exp_id="exp_sel003")
        r = client.post(
            "/api/experiments/exp_sel003/select-variant",
            json={"variant_id": "var_chal_001", "approved_by": "operator"},
        )
        assert r.json()["selected_variant_id"] == "var_chal_001"

    def test_experiment_status_wordt_selected_na_call(self, client):
        _make_experiment(exp_id="exp_sel004")
        client.post(
            "/api/experiments/exp_sel004/select-variant",
            json={"variant_id": "var_ctrl_001", "approved_by": "operator"},
        )
        loaded = ExperimentStore().load("exp_sel004")
        assert loaded.status == ExperimentStatus.SELECTED

    def test_400_bij_geblokkeerde_variant(self, client):
        """Variant met passed_quality=False → HTTP 400."""
        exp = Experiment(
            experiment_id="exp_blocked001",
            campaign_id="c001",
            app_id="app_test",
            hypothesis=_make_hypothesis(),
            variants=[
                _make_variant("var_blocked", "exp_blocked001", "control",
                              quality_score={"passed": False, "composite_score": 30}),
            ],
            status=ExperimentStatus.PENDING,
        )
        ExperimentStore().save(exp)
        r = client.post(
            "/api/experiments/exp_blocked001/select-variant",
            json={"variant_id": "var_blocked", "approved_by": "operator"},
        )
        assert r.status_code == 400

    def test_400_detail_bij_geblokkeerde_variant(self, client):
        exp = Experiment(
            experiment_id="exp_blocked002",
            campaign_id="c002",
            app_id="app_test",
            hypothesis=_make_hypothesis(),
            variants=[
                _make_variant("var_blocked2", "exp_blocked002", "control",
                              quality_score={"passed": False, "composite_score": 30}),
            ],
            status=ExperimentStatus.PENDING,
        )
        ExperimentStore().save(exp)
        r = client.post(
            "/api/experiments/exp_blocked002/select-variant",
            json={"variant_id": "var_blocked2", "approved_by": "operator"},
        )
        assert r.status_code == 400
        assert len(r.json()["detail"]) > 0

    def test_400_bij_onbekend_variant_id(self, client):
        _make_experiment(exp_id="exp_novar001")
        r = client.post(
            "/api/experiments/exp_novar001/select-variant",
            json={"variant_id": "var_does_not_exist", "approved_by": "operator"},
        )
        assert r.status_code == 400

    def test_400_bij_onbekend_experiment_id(self, client):
        r = client.post(
            "/api/experiments/exp_unknown/select-variant",
            json={"variant_id": "var_ctrl_001", "approved_by": "operator"},
        )
        assert r.status_code == 400

    def test_approved_by_default_is_operator(self, client):
        """approved_by heeft default waarde 'operator'."""
        _make_experiment(exp_id="exp_def001")
        r = client.post(
            "/api/experiments/exp_def001/select-variant",
            json={"variant_id": "var_ctrl_001"},
        )
        assert r.status_code == 200
        loaded = ExperimentStore().load("exp_def001")
        assert loaded.selected_by == "operator"

    def test_422_bij_ontbrekend_request_body(self, client):
        _make_experiment(exp_id="exp_nobody")
        r = client.post("/api/experiments/exp_nobody/select-variant")
        assert r.status_code == 422


# ── GET /{experiment_id}/comparison ──────────────────────────────────

class TestGetComparison:
    def test_200_bij_concluded_status(self, client):
        _make_experiment(
            exp_id="exp_conc001",
            status=ExperimentStatus.CONCLUDED,
            winning_variant_id="var_ctrl_001",
        )
        r = client.get("/api/experiments/exp_conc001/comparison")
        assert r.status_code == 200

    def test_200_bij_inconclusive_status(self, client):
        _make_experiment(exp_id="exp_inconcl001", status=ExperimentStatus.INCONCLUSIVE)
        r = client.get("/api/experiments/exp_inconcl001/comparison")
        assert r.status_code == 200

    def test_response_bevat_dimension(self, client):
        _make_experiment(exp_id="exp_comp_dim", status=ExperimentStatus.CONCLUDED)
        r = client.get("/api/experiments/exp_comp_dim/comparison")
        assert r.json()["dimension"] == "hook_type"

    def test_response_bevat_varianten(self, client):
        _make_experiment(exp_id="exp_comp_vars", status=ExperimentStatus.CONCLUDED)
        r = client.get("/api/experiments/exp_comp_vars/comparison")
        data = r.json()
        assert "variants" in data
        assert len(data["variants"]) == 2

    def test_response_variant_bevat_label(self, client):
        _make_experiment(exp_id="exp_comp_lbl", status=ExperimentStatus.CONCLUDED)
        r = client.get("/api/experiments/exp_comp_lbl/comparison")
        labels = {v["label"] for v in r.json()["variants"]}
        assert "control" in labels
        assert "challenger_A" in labels

    def test_409_bij_pending_status(self, client):
        _make_experiment(exp_id="exp_409_pend", status=ExperimentStatus.PENDING)
        r = client.get("/api/experiments/exp_409_pend/comparison")
        assert r.status_code == 409

    def test_409_bij_measuring_status(self, client):
        _make_experiment(exp_id="exp_409_meas", status=ExperimentStatus.MEASURING)
        r = client.get("/api/experiments/exp_409_meas/comparison")
        assert r.status_code == 409

    def test_409_bij_selected_status(self, client):
        _make_experiment(exp_id="exp_409_sel", status=ExperimentStatus.SELECTED)
        r = client.get("/api/experiments/exp_409_sel/comparison")
        assert r.status_code == 409

    def test_409_detail_bevat_status_naam(self, client):
        _make_experiment(exp_id="exp_409_det", status=ExperimentStatus.MEASURING)
        r = client.get("/api/experiments/exp_409_det/comparison")
        assert "measuring" in r.json()["detail"]

    def test_404_bij_onbekend_id(self, client):
        r = client.get("/api/experiments/exp_never_existed/comparison")
        assert r.status_code == 404

    def test_response_bevat_causal_confidence(self, client):
        _make_experiment(exp_id="exp_conf001", status=ExperimentStatus.CONCLUDED)
        r = client.get("/api/experiments/exp_conf001/comparison")
        data = r.json()
        assert "causal_confidence" in data

    def test_response_bevat_status(self, client):
        _make_experiment(exp_id="exp_status_check", status=ExperimentStatus.CONCLUDED)
        r = client.get("/api/experiments/exp_status_check/comparison")
        assert r.json()["status"] == "concluded"
