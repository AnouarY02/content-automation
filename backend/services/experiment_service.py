"""
Experiment Service — orchestreert de volledige experiment lifecycle.

Wordt aangeroepen door:
  - campaign_pipeline.py (score_experiment na variant generatie)
  - backend/api/experiments.py (select_variant, mark_published)
  - workflows/feedback_loop.py (mark_published na TikTok publish)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from experiments.experiment_store import ExperimentStore
from experiments.models import Experiment, ExperimentStatus

ROOT = Path(__file__).parent.parent.parent


class ExperimentService:
    """
    Centrale service voor experiment lifecycle beheer.

    Scheiding van verantwoordelijkheden:
      - VariantGenerator    → aanmaken van varianten
      - ExperimentService   → scoren, selecteren, publiceren, concluderen
      - VariantComparator   → post-publish vergelijking (apart bestand)
    """

    def __init__(self, tenant_id: str = "default"):
        self._tenant_id = tenant_id
        self._store = ExperimentStore(tenant_id=tenant_id)

    # ── Scoring ───────────────────────────────────────────────────────

    def score_experiment(self, experiment_id: str, app_id: str) -> Experiment:
        """
        Scoort alle varianten in een experiment via de quality scorer en benchmarker.
        Werkt de experiment status bij naar PENDING of QUALITY_FAIL.

        Args:
            experiment_id: ID van het te scoren experiment
            app_id:        App ID voor brand memory en top-performers

        Returns:
            Bijgewerkt Experiment object
        """
        experiment = self._store.load(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} niet gevonden")

        logger.info(f"[ExperimentService] Scoren: {experiment_id} ({len(experiment.variants)} varianten)")

        brand_memory  = self._load_brand_memory(app_id)
        top_performers = self._load_top_performers(app_id)

        from quality.scorer import AssetQualityScorer
        from quality.benchmarker import Benchmarker

        scorer     = AssetQualityScorer()
        benchmarker = Benchmarker(app_id)

        all_passed = True
        for variant in experiment.variants:
            # Quality score
            score = scorer.score_variant(variant, brand_memory, top_performers)
            variant.quality_score = score.model_dump(mode="json")

            # Benchmark (pre-publish indicatie)
            benchmark = benchmarker.benchmark_variant(
                variant.variant_id,
                variant.script,
                variant.caption if isinstance(variant.caption, dict) else {},
            )

            # Voeg benchmark toe aan performance veld (pre-publish markering)
            variant.performance = {
                "benchmark_similarity":    benchmark.similarity_to_top_performers,
                "predicted_band":          benchmark.predicted_performance_band,
                "benchmark_confidence":    benchmark.prediction_confidence,
                "benchmark_patterns":      benchmark.matching_patterns,
                "pre_publish":             True,
            }

            if not score.passed:
                all_passed = False
                logger.warning(
                    f"[ExperimentService] Variant {variant.variant_id} GEBLOKKEERD: "
                    f"{score.blockers}"
                )

        experiment.status = (
            ExperimentStatus.PENDING if all_passed else ExperimentStatus.QUALITY_FAIL
        )
        self._store.save(experiment)
        logger.success(
            f"[ExperimentService] Scoren klaar: {experiment_id} → status={experiment.status.value}"
        )
        return experiment

    # ── Operator beslissing ───────────────────────────────────────────

    def select_variant(
        self,
        experiment_id: str,
        variant_id: str,
        approved_by: str,
    ) -> dict:
        """
        Registreert de operator-keuze voor een variant.
        Wordt aangeroepen vanuit de approval view (desktop) of de API.

        Returns:
            Dict met geselecteerde variant metadata
        """
        experiment = self._store.load(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} niet gevonden")

        variant = experiment.get_variant(variant_id)
        if not variant:
            raise ValueError(f"Variant {variant_id} niet gevonden in experiment {experiment_id}")

        if not variant.passed_quality:
            raise ValueError(
                f"Variant {variant_id} is geblokkeerd door quality scorer — selectie niet toegestaan"
            )

        experiment.selected_variant_id = variant_id
        experiment.selected_by         = approved_by
        experiment.selected_at         = datetime.utcnow()
        experiment.status              = ExperimentStatus.SELECTED
        self._store.save(experiment)

        logger.success(
            f"[ExperimentService] Variant geselecteerd: {variant_id} "
            f"({variant.label}, {variant.spec.dimension_value}) door {approved_by}"
        )

        return {
            "experiment_id":     experiment_id,
            "selected_variant_id": variant_id,
            "variant_label":     variant.label,
            "dimension":         experiment.hypothesis.dimension.value,
            "dimension_value":   variant.spec.dimension_value,
            "changes_from_control": variant.spec.changes_from_control,
        }

    # ── Publicatie lifecycle ──────────────────────────────────────────

    def mark_published(
        self,
        experiment_id: str,
        variant_id: str,
        post_id: str,
    ) -> None:
        """
        Markeert een variant als gepubliceerd op TikTok.
        Zet experiment status op MEASURING.
        Wordt aangeroepen door approval_service.py na succesvolle publish.
        """
        experiment = self._store.load(experiment_id)
        if not experiment:
            logger.warning(f"[ExperimentService] Experiment {experiment_id} niet gevonden voor mark_published")
            return

        variant = experiment.get_variant(variant_id)
        if variant:
            variant.tiktok_post_id = post_id
            # Verwijder pre-publish benchmark markering
            if variant.performance and variant.performance.get("pre_publish"):
                variant.performance["pre_publish"] = False

        experiment.status = ExperimentStatus.MEASURING
        self._store.save(experiment)
        logger.info(f"[ExperimentService] Experiment {experiment_id} → MEASURING (post_id={post_id})")

    # ── Approval flow helpers ─────────────────────────────────────────

    def get_pending_for_approval(self, app_id: str) -> list[dict]:
        """
        Geeft experimenten terug die wachten op operator goedkeuring.
        Inclusief kwaliteitsscores per variant voor de approval view.
        """
        experiments = self._store.get_pending_experiments(app_id)
        return [e.model_dump(mode="json") for e in experiments]

    def get_experiment_for_campaign(self, campaign_id: str) -> Optional[dict]:
        """Zoek het experiment bij een campagne (voor approval view koppeling)."""
        exp = self._store.get_by_campaign(campaign_id)
        return exp.model_dump(mode="json") if exp else None

    # ── Context loaders ───────────────────────────────────────────────

    @staticmethod
    def _load_brand_memory(app_id: str) -> dict:
        try:
            from agents import brand_memory as bm
            return bm.load(app_id)
        except Exception:
            return {}

    def _load_top_performers(self, app_id: str) -> list[dict]:
        if self._tenant_id == "default":
            posts_path = ROOT / "data" / "analytics" / app_id / "posts.json"
        else:
            posts_path = ROOT / "data" / "tenants" / self._tenant_id / "analytics" / app_id / "posts.json"
        if not posts_path.exists():
            return []
        try:
            posts = json.loads(posts_path.read_text(encoding="utf-8"))
            if not isinstance(posts, list):
                return []
            return sorted(
                posts,
                key=lambda p: p.get("score", p.get("composite_score", 0)),
                reverse=True,
            )[:10]
        except Exception:
            return []
