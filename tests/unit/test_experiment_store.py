"""
DAG 1 — Unit tests: experiments/experiment_store.py

Dekt:
  - save/load roundtrip (alle velden intact)
  - experiment_id propagatie naar varianten bij save()
  - index consistentie (_index.json)
  - list_by_app() correct en gesorteerd
  - get_concluded_dimensions() telt correct
  - get_winning_values() retourneert juiste waarden
  - get_pending_experiments() filtert correct
  - get_measuring_experiments() filtert correct
  - get_by_campaign() vindt op campaign_id
  - load() geeft None bij niet-bestaand ID
  - load() geeft None bij corrupt JSON
  - list_by_app() voor onbekende app_id geeft lege lijst
  - list_all() over meerdere bestanden

Alle tests gebruiken een tijdelijke directory — echte projectdata wordt nooit aangeraakt.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from experiments.models import (
    Experiment,
    ExperimentDimension,
    ExperimentStatus,
    Hypothesis,
    Variant,
    VariantSpec,
)
from experiments.experiment_store import ExperimentStore
import experiments.experiment_store as store_module


# ── Fixture: tijdelijke store ─────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """
    Patch ROOT zodat ExperimentStore() nieuwe instanties ook
    in de tijdelijke directory werken.
    """
    monkeypatch.setattr(store_module, "ROOT", tmp_path)
    store_dir = tmp_path / "data" / "experiments"
    store_dir.mkdir(parents=True)
    index_path = store_dir / "_index.json"

    monkeypatch.setattr(store_module, "STORE_DIR", store_dir)
    monkeypatch.setattr(store_module, "INDEX_PATH", index_path)
    return store_dir


# ── Helpers ───────────────────────────────────────────────────────────

def make_hypothesis(
    dimension: ExperimentDimension = ExperimentDimension.HOOK_TYPE,
    control: str = "curiosity_gap",
    challenger: str = "bold_claim",
) -> Hypothesis:
    return Hypothesis(
        dimension=dimension,
        control_value=control,
        challenger_value=challenger,
        rationale="Test",
    )


def make_variant(
    variant_id: str,
    experiment_id: str,
    label: str = "control",
    dimension_value: str = "curiosity_gap",
) -> Variant:
    return Variant(
        variant_id=variant_id,
        experiment_id=experiment_id,
        label=label,
        spec=VariantSpec(
            label=label,
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value=dimension_value,
        ),
        idea={"title": "Test"},
        script={"scenes": []},
        caption={"caption": "test"},
    )


def make_experiment(
    experiment_id: str = "exp_test0001",
    app_id: str = "app_test",
    campaign_id: str = "camp_001",
    status: ExperimentStatus = ExperimentStatus.PENDING,
    with_variants: bool = True,
) -> Experiment:
    variants = []
    if with_variants:
        variants = [
            make_variant("var_ctrl_001", experiment_id, "control", "curiosity_gap"),
            make_variant("var_chal_001", experiment_id, "challenger_A", "bold_claim"),
        ]
    return Experiment(
        experiment_id=experiment_id,
        campaign_id=campaign_id,
        app_id=app_id,
        hypothesis=make_hypothesis(),
        variants=variants,
        status=status,
    )


# ── Save / Load roundtrip ─────────────────────────────────────────────

class TestSaveLoad:
    def test_save_creates_file(self, isolated_store):
        exp = make_experiment()
        store = ExperimentStore()
        path = store.save(exp)
        assert path.exists()
        assert path.name == f"{exp.experiment_id}.json"

    def test_load_returns_none_for_missing_id(self):
        store = ExperimentStore()
        result = store.load("exp_does_not_exist")
        assert result is None

    def test_roundtrip_experiment_id(self):
        exp = make_experiment(experiment_id="exp_roundtrip1")
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load("exp_roundtrip1")
        assert loaded is not None
        assert loaded.experiment_id == "exp_roundtrip1"

    def test_roundtrip_app_id(self):
        exp = make_experiment(app_id="my_app")
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        assert loaded.app_id == "my_app"

    def test_roundtrip_campaign_id(self):
        exp = make_experiment(campaign_id="camp_abc123")
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        assert loaded.campaign_id == "camp_abc123"

    def test_roundtrip_status(self):
        exp = make_experiment(status=ExperimentStatus.MEASURING)
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.MEASURING

    def test_roundtrip_hypothesis(self):
        exp = make_experiment()
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        assert loaded.hypothesis.dimension == ExperimentDimension.HOOK_TYPE
        assert loaded.hypothesis.control_value == "curiosity_gap"
        assert loaded.hypothesis.challenger_value == "bold_claim"

    def test_roundtrip_variants_count(self):
        exp = make_experiment(with_variants=True)
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        assert len(loaded.variants) == 2

    def test_roundtrip_variant_labels(self):
        exp = make_experiment(with_variants=True)
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        labels = {v.label for v in loaded.variants}
        assert "control" in labels
        assert "challenger_A" in labels

    def test_roundtrip_no_variants(self):
        exp = make_experiment(with_variants=False)
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        assert loaded.variants == []

    def test_save_overwrites_existing(self):
        exp = make_experiment()
        store = ExperimentStore()
        store.save(exp)
        exp.status = ExperimentStatus.SELECTED
        store.save(exp)
        loaded = store.load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.SELECTED

    def test_load_corrupt_json_returns_none(self, isolated_store):
        corrupt_path = isolated_store / "exp_corrupt.json"
        corrupt_path.write_text("{ this is not valid json }", encoding="utf-8")
        store = ExperimentStore()
        result = store.load("exp_corrupt")
        assert result is None

    def test_load_empty_file_returns_none(self, isolated_store):
        empty_path = isolated_store / "exp_empty.json"
        empty_path.write_text("", encoding="utf-8")
        store = ExperimentStore()
        result = store.load("exp_empty")
        assert result is None


# ── experiment_id propagatie naar varianten ───────────────────────────

class TestVariantExperimentIdPropagation:
    def test_save_sets_experiment_id_on_variants_without_id(self):
        """Als variant.experiment_id leeg is, vult save() het in."""
        exp = make_experiment(experiment_id="exp_propagate1")
        for v in exp.variants:
            v.experiment_id = ""  # simuleer lege variant_id
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load("exp_propagate1")
        for v in loaded.variants:
            assert v.experiment_id == "exp_propagate1"

    def test_save_does_not_overwrite_existing_experiment_id(self):
        """Als variant.experiment_id al gezet is, laat save() het staan."""
        exp = make_experiment(experiment_id="exp_propagate2")
        store = ExperimentStore()
        store.save(exp)
        loaded = store.load("exp_propagate2")
        for v in loaded.variants:
            assert v.experiment_id == "exp_propagate2"


# ── Index consistentie ────────────────────────────────────────────────

class TestIndexConsistency:
    def test_save_creates_index(self, isolated_store):
        exp = make_experiment(app_id="app_idx")
        ExperimentStore().save(exp)
        index_path = isolated_store / "_index.json"
        assert index_path.exists()

    def test_index_contains_experiment_id(self, isolated_store):
        exp = make_experiment(app_id="app_idx", experiment_id="exp_idx001")
        ExperimentStore().save(exp)
        index = json.loads((isolated_store / "_index.json").read_text())
        assert "app_idx" in index
        assert "exp_idx001" in index["app_idx"]

    def test_second_save_same_app_extends_index(self, isolated_store):
        store = ExperimentStore()
        exp1 = make_experiment(app_id="app_multi", experiment_id="exp_m001")
        exp2 = make_experiment(app_id="app_multi", experiment_id="exp_m002")
        store.save(exp1)
        store.save(exp2)
        index = json.loads((isolated_store / "_index.json").read_text())
        assert "exp_m001" in index["app_multi"]
        assert "exp_m002" in index["app_multi"]

    def test_save_same_experiment_twice_no_duplicate_in_index(self, isolated_store):
        store = ExperimentStore()
        exp = make_experiment(app_id="app_dup", experiment_id="exp_dup001")
        store.save(exp)
        store.save(exp)
        index = json.loads((isolated_store / "_index.json").read_text())
        assert index["app_dup"].count("exp_dup001") == 1

    def test_different_apps_separate_index_entries(self, isolated_store):
        store = ExperimentStore()
        exp1 = make_experiment(app_id="app_a", experiment_id="exp_a001")
        exp2 = make_experiment(app_id="app_b", experiment_id="exp_b001")
        store.save(exp1)
        store.save(exp2)
        index = json.loads((isolated_store / "_index.json").read_text())
        assert "app_a" in index
        assert "app_b" in index
        assert "exp_b001" not in index.get("app_a", [])


# ── list_by_app ───────────────────────────────────────────────────────

class TestListByApp:
    def test_empty_for_unknown_app(self):
        result = ExperimentStore().list_by_app("app_unknown")
        assert result == []

    def test_returns_saved_experiments(self):
        store = ExperimentStore()
        exp = make_experiment(app_id="app_list")
        store.save(exp)
        result = store.list_by_app("app_list")
        assert len(result) == 1
        assert result[0].experiment_id == exp.experiment_id

    def test_returns_only_own_app_experiments(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_own", experiment_id="exp_own1"))
        store.save(make_experiment(app_id="app_other", experiment_id="exp_other1"))
        result = store.list_by_app("app_own")
        assert len(result) == 1
        assert result[0].app_id == "app_own"

    def test_sorted_newest_first(self):
        """Experimenten gesorteerd op created_at, nieuwste eerst."""
        store = ExperimentStore()
        exp1 = make_experiment(app_id="app_sort", experiment_id="exp_sort1")
        exp2 = make_experiment(app_id="app_sort", experiment_id="exp_sort2")
        # exp2 is ouder
        exp1.created_at = datetime(2026, 3, 10, 12, 0)
        exp2.created_at = datetime(2026, 3, 9, 12, 0)
        store.save(exp1)
        store.save(exp2)
        result = store.list_by_app("app_sort")
        assert result[0].experiment_id == "exp_sort1"
        assert result[1].experiment_id == "exp_sort2"

    def test_multiple_experiments_all_returned(self):
        store = ExperimentStore()
        for i in range(5):
            store.save(make_experiment(app_id="app_many", experiment_id=f"exp_many{i:03d}"))
        result = store.list_by_app("app_many")
        assert len(result) == 5


# ── get_concluded_dimensions ──────────────────────────────────────────

class TestGetConcludedDimensions:
    def test_empty_for_app_without_experiments(self):
        result = ExperimentStore().get_concluded_dimensions("app_no_exp")
        assert result == {}

    def test_empty_for_app_with_only_pending(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_pend", status=ExperimentStatus.PENDING))
        result = store.get_concluded_dimensions("app_pend")
        assert result == {}

    def test_counts_concluded_by_dimension(self):
        store = ExperimentStore()
        # 2 HOOK_TYPE concluded
        for i in range(2):
            exp = make_experiment(
                app_id="app_conc",
                experiment_id=f"exp_hook{i}",
                status=ExperimentStatus.CONCLUDED,
            )
            store.save(exp)
        result = store.get_concluded_dimensions("app_conc")
        assert result.get("hook_type", 0) == 2

    def test_does_not_count_measuring(self):
        store = ExperimentStore()
        store.save(make_experiment(
            app_id="app_meas", experiment_id="exp_meas1",
            status=ExperimentStatus.MEASURING,
        ))
        result = store.get_concluded_dimensions("app_meas")
        assert result == {}

    def test_multiple_dimensions(self):
        store = ExperimentStore()

        # 1 hook_type concluded
        exp_hook = make_experiment(
            app_id="app_multi_dim",
            experiment_id="exp_hook_c1",
            status=ExperimentStatus.CONCLUDED,
        )
        exp_hook.hypothesis = Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="a", challenger_value="b", rationale="x",
        )
        store.save(exp_hook)

        # 2 cta_type concluded
        for i in range(2):
            exp_cta = make_experiment(
                app_id="app_multi_dim",
                experiment_id=f"exp_cta_c{i}",
                status=ExperimentStatus.CONCLUDED,
            )
            exp_cta.hypothesis = Hypothesis(
                dimension=ExperimentDimension.CTA_TYPE,
                control_value="soft", challenger_value="hard", rationale="x",
            )
            store.save(exp_cta)

        result = store.get_concluded_dimensions("app_multi_dim")
        assert result.get("hook_type") == 1
        assert result.get("cta_type") == 2

    def test_isolates_by_app_id(self):
        store = ExperimentStore()
        store.save(make_experiment(
            app_id="app_iso_a", experiment_id="exp_iso_a",
            status=ExperimentStatus.CONCLUDED,
        ))
        result = store.get_concluded_dimensions("app_iso_b")
        assert result == {}


# ── get_winning_values ────────────────────────────────────────────────

class TestGetWinningValues:
    def test_empty_for_no_concluded(self):
        store = ExperimentStore()
        result = store.get_winning_values("app_w", ExperimentDimension.HOOK_TYPE)
        assert result == []

    def test_returns_winner_dimension_value(self):
        store = ExperimentStore()
        exp = make_experiment(
            app_id="app_win",
            experiment_id="exp_win1",
            status=ExperimentStatus.CONCLUDED,
        )
        exp.winning_variant_id = "var_ctrl_001"
        store.save(exp)
        result = store.get_winning_values("app_win", ExperimentDimension.HOOK_TYPE)
        assert "curiosity_gap" in result

    def test_ignores_wrong_dimension(self):
        store = ExperimentStore()
        exp = make_experiment(
            app_id="app_wdim",
            experiment_id="exp_wdim1",
            status=ExperimentStatus.CONCLUDED,
        )
        exp.winning_variant_id = "var_ctrl_001"
        store.save(exp)
        result = store.get_winning_values("app_wdim", ExperimentDimension.CTA_TYPE)
        assert result == []


# ── get_pending_experiments ───────────────────────────────────────────

class TestGetPendingExperiments:
    def test_returns_pending_status(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_p", experiment_id="exp_p1",
                                   status=ExperimentStatus.PENDING))
        result = store.get_pending_experiments("app_p")
        assert len(result) == 1

    def test_returns_quality_fail_status(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_p", experiment_id="exp_p2",
                                   status=ExperimentStatus.QUALITY_FAIL))
        result = store.get_pending_experiments("app_p")
        assert len(result) == 1

    def test_excludes_measuring(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_p", experiment_id="exp_p3",
                                   status=ExperimentStatus.MEASURING))
        result = store.get_pending_experiments("app_p")
        assert len(result) == 0

    def test_excludes_concluded(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_p", experiment_id="exp_p4",
                                   status=ExperimentStatus.CONCLUDED))
        result = store.get_pending_experiments("app_p")
        assert len(result) == 0


# ── get_measuring_experiments ─────────────────────────────────────────

class TestGetMeasuringExperiments:
    def test_returns_measuring_status(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_m", experiment_id="exp_m1",
                                   status=ExperimentStatus.MEASURING))
        result = store.get_measuring_experiments()
        ids = [e.experiment_id for e in result]
        assert "exp_m1" in ids

    def test_returns_published_status(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_m", experiment_id="exp_m2",
                                   status=ExperimentStatus.PUBLISHED))
        result = store.get_measuring_experiments()
        ids = [e.experiment_id for e in result]
        assert "exp_m2" in ids

    def test_excludes_pending(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_m", experiment_id="exp_m3",
                                   status=ExperimentStatus.PENDING))
        result = store.get_measuring_experiments()
        ids = [e.experiment_id for e in result]
        assert "exp_m3" not in ids


# ── get_by_campaign ───────────────────────────────────────────────────

class TestGetByCampaign:
    def test_finds_by_campaign_id(self):
        store = ExperimentStore()
        store.save(make_experiment(campaign_id="camp_find_me", experiment_id="exp_camp1"))
        result = store.get_by_campaign("camp_find_me")
        assert result is not None
        assert result.campaign_id == "camp_find_me"

    def test_returns_none_for_unknown_campaign(self):
        store = ExperimentStore()
        result = store.get_by_campaign("camp_does_not_exist")
        assert result is None

    def test_does_not_return_wrong_campaign(self):
        store = ExperimentStore()
        store.save(make_experiment(campaign_id="camp_correct", experiment_id="exp_bc1"))
        result = store.get_by_campaign("camp_wrong")
        assert result is None


# ── list_all ──────────────────────────────────────────────────────────

class TestListAll:
    def test_empty_store_returns_empty_list(self):
        assert ExperimentStore().list_all() == []

    def test_returns_all_saved_experiments(self):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_a", experiment_id="exp_la1"))
        store.save(make_experiment(app_id="app_b", experiment_id="exp_la2"))
        result = store.list_all()
        ids = {e.experiment_id for e in result}
        assert "exp_la1" in ids
        assert "exp_la2" in ids

    def test_skips_corrupt_files(self, isolated_store):
        store = ExperimentStore()
        store.save(make_experiment(app_id="app_x", experiment_id="exp_good"))
        corrupt = isolated_store / "exp_corrupt.json"
        corrupt.write_text("not json", encoding="utf-8")
        result = store.list_all()
        ids = [e.experiment_id for e in result]
        assert "exp_good" in ids
        # corrupt wordt overgeslagen, geen exception
