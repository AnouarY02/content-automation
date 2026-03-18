"""
Gedeelde fixtures voor alle DAG-tests.
"""
import pytest
from datetime import datetime

from experiments.models import (
    Experiment, ExperimentDimension, ExperimentStatus,
    Hypothesis, Variant, VariantSpec,
)
from quality.models import AssetQualityScore, BenchmarkResult, DimensionScore


# ── Helpers ───────────────────────────────────────────────────────────

def make_dimension_score(score: float = 75.0) -> DimensionScore:
    return DimensionScore(score=score, rationale="Test rationale")


def make_asset_quality_score(
    variant_id: str = "var_test",
    hook: float = 75.0,
    clarity: float = 75.0,
    brand: float = 75.0,
    retention: float = 75.0,
    passed: bool = True,
) -> AssetQualityScore:
    composite = hook * 0.35 + clarity * 0.25 + brand * 0.20 + retention * 0.20
    return AssetQualityScore(
        variant_id=variant_id,
        hook_strength=make_dimension_score(hook),
        clarity=make_dimension_score(clarity),
        brand_fit=make_dimension_score(brand),
        retention_potential=make_dimension_score(retention),
        composite_score=composite,
        passed=passed,
    )


def make_hypothesis(
    dimension: ExperimentDimension = ExperimentDimension.HOOK_TYPE,
    control_value: str = "curiosity_gap",
    challenger_value: str = "bold_claim",
) -> Hypothesis:
    return Hypothesis(
        dimension=dimension,
        control_value=control_value,
        challenger_value=challenger_value,
        rationale="Test hypothesis rationale",
    )


def make_variant_spec(
    label: str = "control",
    dimension: ExperimentDimension = ExperimentDimension.HOOK_TYPE,
    dimension_value: str = "curiosity_gap",
) -> VariantSpec:
    return VariantSpec(
        label=label,
        dimension=dimension,
        dimension_value=dimension_value,
    )


def make_variant(
    variant_id: str = "var_ctrl_001",
    experiment_id: str = "exp_test0001",
    label: str = "control",
    dimension_value: str = "curiosity_gap",
) -> Variant:
    return Variant(
        variant_id=variant_id,
        experiment_id=experiment_id,
        label=label,
        spec=make_variant_spec(label=label, dimension_value=dimension_value),
        idea={"title": "Test idee", "hook_type": dimension_value},
        script={"scenes": [], "total_duration_sec": 45},
        caption={"caption": "Test caption", "hashtags": ["#test"]},
    )


def make_experiment(
    experiment_id: str = "exp_test0001",
    app_id: str = "app_test",
    campaign_id: str = "camp_test001",
    status: ExperimentStatus = ExperimentStatus.PENDING,
) -> Experiment:
    return Experiment(
        experiment_id=experiment_id,
        campaign_id=campaign_id,
        app_id=app_id,
        hypothesis=make_hypothesis(),
        variants=[
            make_variant(variant_id="var_ctrl_001", experiment_id=experiment_id, label="control"),
            make_variant(variant_id="var_chal_001", experiment_id=experiment_id,
                         label="challenger_A", dimension_value="bold_claim"),
        ],
        status=status,
    )
