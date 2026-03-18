"""
Variant Generator — genereert doelbewuste content-varianten voor A/B experimenten.

PRINCIPE:
  1. Bepaal welke dimensie getest wordt (dimensie met minste geconcludeerde experimenten)
  2. Bouw een data-gedreven hypothese
  3. Maak CONTROL variant (bestaande bundle, ongewijzigd)
  4. Maak CHALLENGER variant (agent opnieuw aanroepen met dimensie-instructie)
  5. Label elke variant met exacte spec
  6. Retourneer Experiment object (scores worden apart toegevoegd door ExperimentService)

VARIATIE-BEPERKING:
  MVP: max 1 challenger (control + 1 challenger = 2 varianten totaal)
  Slechts één dimensie per experiment (voor causale attribuutie)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from experiments.experiment_store import ExperimentStore
from experiments.models import (
    CaptionStyle,
    CtaType,
    Experiment,
    ExperimentDimension,
    ExperimentStatus,
    HookType,
    Hypothesis,
    Variant,
    VariantSpec,
)

ROOT = Path(__file__).parent.parent

# Prioriteit: dimensie met minste geconcludeerde experimenten wordt gekozen
DIMENSION_PRIORITY = [
    ExperimentDimension.HOOK_TYPE,
    ExperimentDimension.CTA_TYPE,
    ExperimentDimension.CAPTION_STYLE,
]

# Aantal geconcludeerde experimenten voor een dimensie als "voldoende getest"
CONCLUDED_THRESHOLD = 2


class VariantGenerator:
    """
    Genereert een experiment met 2 varianten (control + challenger)
    op basis van een bestaande campaign bundle.

    Gebruik:
        generator = VariantGenerator(tenant_id="acme")
        experiment = generator.generate(bundle_dict, app_id="myapp_001")
    """

    def __init__(self, tenant_id: str = "default"):
        self._tenant_id = tenant_id
        self._store = ExperimentStore(tenant_id=tenant_id)

    def generate(
        self,
        campaign_bundle: dict,
        app_id: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> Experiment:
        """
        Hoofdmethode. Neemt een bestaande campaign bundle en genereert een experiment.

        Args:
            campaign_bundle: Dict van een CampaignBundle (model_dump output)
            app_id:          App ID voor brand memory en experiment store
            on_progress:     Optionele callback voor voortgangsmeldingen

        Returns:
            Experiment object met control + challenger variant (status=GENERATING)
        """
        def _log(msg: str):
            logger.info(msg)
            if on_progress:
                on_progress(msg)

        campaign_id = campaign_bundle.get("id", "")
        _log(f"[VariantGenerator] Start voor campagne {campaign_id[:16]}")

        # Laad context
        app        = self._load_app(app_id)
        memory     = self._load_memory(app_id)
        learnings  = self._load_learnings(app_id)

        # Bepaal dimensie + hypothese
        _log("Dimensie selecteren...")
        dimension, hypothesis = self._select_dimension_and_hypothesis(
            app_id, memory, learnings, campaign_bundle
        )
        _log(f"Experiment dimensie: {dimension.value} | {hypothesis.control_value} vs {hypothesis.challenger_value}")

        # Genereer varianten
        _log("Control variant aanmaken...")
        control = self._make_control_variant(campaign_bundle, dimension, hypothesis)

        _log(f"Challenger variant genereren ({hypothesis.challenger_value})...")
        challenger = self._make_challenger_variant(
            campaign_bundle, dimension, hypothesis, app, memory
        )

        # Koppel experiment_id aan varianten
        experiment = Experiment(
            campaign_id=campaign_id,
            app_id=app_id,
            hypothesis=hypothesis,
            variants=[],
            status=ExperimentStatus.GENERATING,
        )
        control.experiment_id   = experiment.experiment_id
        challenger.experiment_id = experiment.experiment_id
        experiment.variants = [control, challenger]

        self._store.save(experiment)
        _log(f"Experiment opgeslagen: {experiment.experiment_id}")
        return experiment

    # ── Dimensie selectie ─────────────────────────────────────────────

    def _select_dimension_and_hypothesis(
        self,
        app_id: str,
        memory: dict,
        learnings: list[dict],
        bundle: dict,
    ) -> tuple[ExperimentDimension, Hypothesis]:
        concluded = self._store.get_concluded_dimensions(app_id)

        # Kies dimensie met minste geconcludeerde experimenten
        chosen_dim = DIMENSION_PRIORITY[0]
        for dim in DIMENSION_PRIORITY:
            if concluded.get(dim.value, 0) < CONCLUDED_THRESHOLD:
                chosen_dim = dim
                break

        hypothesis = self._build_hypothesis(chosen_dim, memory, learnings, bundle, app_id)
        return chosen_dim, hypothesis

    def _build_hypothesis(
        self,
        dimension: ExperimentDimension,
        memory: dict,
        learnings: list[dict],
        bundle: dict,
        app_id: str,
    ) -> Hypothesis:
        if dimension == ExperimentDimension.HOOK_TYPE:
            return self._hook_hypothesis(memory, learnings, bundle, app_id)
        elif dimension == ExperimentDimension.CTA_TYPE:
            return self._cta_hypothesis(memory, learnings)
        else:
            return self._caption_hypothesis()

    def _hook_hypothesis(
        self, memory: dict, learnings: list[dict], bundle: dict, app_id: str
    ) -> Hypothesis:
        # Control: huidige hook type van het script
        current_hook = bundle.get("script", {}).get("experiment_hook_type", HookType.CURIOSITY_GAP.value)

        # Challenger: winnende hook types uit eerdere experimenten
        past_winners = self._store.get_winning_values(app_id, ExperimentDimension.HOOK_TYPE)
        challenger_hook = HookType.BOLD_CLAIM.value  # default

        if past_winners:
            # Gebruik de meest recente winnaar als challenger (als die verschilt van control)
            for winner in past_winners:
                if winner != current_hook:
                    challenger_hook = winner
                    break
        else:
            # Kies een alternatief op basis van positieve learnings
            positive_hooks = [
                l for l in learnings
                if l.get("type") == "positive" and l.get("category") == "hook"
            ]
            for learning in positive_hooks:
                for hook_type in HookType:
                    if hook_type.value in learning.get("finding", "").lower():
                        if hook_type.value != current_hook:
                            challenger_hook = hook_type.value
                            break

        # Zorg dat control en challenger altijd verschillen
        if challenger_hook == current_hook:
            alternatives = [h.value for h in HookType if h.value != current_hook]
            challenger_hook = alternatives[0] if alternatives else HookType.BOLD_CLAIM.value

        # Bewijsmateriaal uit learnings
        evidence = [
            f"{l.get('finding', '')[:80]}" for l in learnings[:3]
            if l.get("category") == "hook" and l.get("type") == "positive"
        ]

        return Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value=current_hook,
            challenger_value=challenger_hook,
            rationale=(
                f"We testen of '{challenger_hook}' hooks beter presteren dan "
                f"'{current_hook}' hooks voor {app_id}. "
                f"Doel: causale attributie van hook type op performance score."
            ),
            supporting_evidence=evidence,
        )

    def _cta_hypothesis(self, memory: dict, learnings: list[dict]) -> Hypothesis:
        current_cta = memory.get("best_cta_type", CtaType.SOFT.value)
        challenger  = CtaType.RETENTION.value if current_cta != CtaType.RETENTION.value else CtaType.SOCIAL.value
        return Hypothesis(
            dimension=ExperimentDimension.CTA_TYPE,
            control_value=current_cta,
            challenger_value=challenger,
            rationale=(
                f"'{challenger}' CTAs correleren met hogere completion rates in de industrie. "
                f"We testen of dit ook geldt voor dit merk."
            ),
        )

    @staticmethod
    def _caption_hypothesis() -> Hypothesis:
        return Hypothesis(
            dimension=ExperimentDimension.CAPTION_STYLE,
            control_value=CaptionStyle.MINIMAL.value,
            challenger_value=CaptionStyle.QUESTION.value,
            rationale=(
                "Een caption met een afsluitende vraag stimuleert comment-engagement. "
                "We testen of dit de comment rate verhoogt t.o.v. een minimale caption."
            ),
        )

    # ── Variant aanmaken ──────────────────────────────────────────────

    def _make_control_variant(
        self,
        bundle: dict,
        dimension: ExperimentDimension,
        hypothesis: Hypothesis,
    ) -> Variant:
        """Control = bestaande bundle ongewijzigd."""
        spec = VariantSpec(
            label="control",
            dimension=dimension,
            dimension_value=hypothesis.control_value,
            changes_from_control=[],
            generation_instruction=f"Originele bundle — geen wijzigingen ({hypothesis.control_value})",
        )
        return Variant(
            variant_id=spec.variant_id,
            experiment_id="",  # Wordt ingevuld door generate()
            label="control",
            spec=spec,
            idea=bundle.get("idea", {}),
            script=bundle.get("script", {}),
            caption=bundle.get("caption", {}),
            video_path=bundle.get("video_path"),
        )

    def _make_challenger_variant(
        self,
        bundle: dict,
        dimension: ExperimentDimension,
        hypothesis: Hypothesis,
        app: dict,
        memory: dict,
    ) -> Variant:
        """Challenger = agent opnieuw aanroepen met dimensie-specifieke instructie."""
        if dimension == ExperimentDimension.HOOK_TYPE:
            return self._generate_hook_variant(bundle, hypothesis, app, memory)
        elif dimension == ExperimentDimension.CTA_TYPE:
            return self._generate_cta_variant(bundle, hypothesis, app, memory)
        else:
            return self._generate_caption_variant(bundle, hypothesis, app, memory)

    def _generate_hook_variant(
        self, bundle: dict, hypothesis: Hypothesis, app: dict, memory: dict
    ) -> Variant:
        from agents.script_writer import ScriptWriterAgent

        agent = ScriptWriterAgent()
        new_script = agent.generate_with_hook_override(
            idea=bundle.get("idea", {}),
            app=app,
            memory=memory,
            hook_type_override=hypothesis.challenger_value,
            extra_instruction=hypothesis.as_prompt_context(),
        )

        spec = VariantSpec(
            label="challenger_A",
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value=hypothesis.challenger_value,
            changes_from_control=[
                f"Hook type: {hypothesis.control_value} → {hypothesis.challenger_value}",
                "Openingsscène (eerste 3-5 seconden) volledig herschreven",
                f"Hook stijl aangepast naar '{hypothesis.challenger_value}' patroon",
            ],
            generation_instruction=hypothesis.as_prompt_context(),
        )
        return Variant(
            variant_id=spec.variant_id,
            experiment_id="",
            label="challenger_A",
            spec=spec,
            idea=bundle.get("idea", {}),
            script=new_script,
            caption=bundle.get("caption", {}),
            video_path=None,  # Moet opnieuw gerenderd worden met nieuwe hook
        )

    def _generate_cta_variant(
        self, bundle: dict, hypothesis: Hypothesis, app: dict, memory: dict
    ) -> Variant:
        from agents.caption_writer import CaptionWriterAgent

        agent = CaptionWriterAgent()
        new_caption = agent.generate_with_cta_override(
            script=bundle.get("script", {}),
            app=app,
            memory=memory,
            cta_type_override=hypothesis.challenger_value,
        )

        spec = VariantSpec(
            label="challenger_A",
            dimension=ExperimentDimension.CTA_TYPE,
            dimension_value=hypothesis.challenger_value,
            changes_from_control=[
                f"CTA type: {hypothesis.control_value} → {hypothesis.challenger_value}",
                "Caption afsluiting aangepast naar nieuw CTA type",
            ],
            generation_instruction=hypothesis.as_prompt_context(),
        )
        return Variant(
            variant_id=spec.variant_id,
            experiment_id="",
            label="challenger_A",
            spec=spec,
            idea=bundle.get("idea", {}),
            script=bundle.get("script", {}),
            caption=new_caption,
            video_path=bundle.get("video_path"),  # Zelfde video — alleen caption verschilt
        )

    def _generate_caption_variant(
        self, bundle: dict, hypothesis: Hypothesis, app: dict, memory: dict
    ) -> Variant:
        from agents.caption_writer import CaptionWriterAgent

        agent = CaptionWriterAgent()
        new_caption = agent.generate_with_style_override(
            script=bundle.get("script", {}),
            app=app,
            memory=memory,
            style_override=hypothesis.challenger_value,
        )

        spec = VariantSpec(
            label="challenger_A",
            dimension=ExperimentDimension.CAPTION_STYLE,
            dimension_value=hypothesis.challenger_value,
            changes_from_control=[
                f"Caption stijl: {hypothesis.control_value} → {hypothesis.challenger_value}",
            ],
            generation_instruction=hypothesis.as_prompt_context(),
        )
        return Variant(
            variant_id=spec.variant_id,
            experiment_id="",
            label="challenger_A",
            spec=spec,
            idea=bundle.get("idea", {}),
            script=bundle.get("script", {}),
            caption=new_caption,
            video_path=bundle.get("video_path"),
        )

    # ── Context loaders ───────────────────────────────────────────────

    @staticmethod
    def _load_app(app_id: str) -> dict:
        configs_dir = ROOT / "configs"
        registry_path = configs_dir / "app_registry.json"
        if not registry_path.exists():
            return {"id": app_id, "name": app_id}
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            for app in registry.get("apps", []):
                if app.get("id") == app_id:
                    return app
        except Exception:
            pass
        return {"id": app_id, "name": app_id}

    @staticmethod
    def _load_memory(app_id: str) -> dict:
        try:
            from agents import brand_memory as bm
            return bm.load(app_id)
        except Exception:
            return {}

    def _load_learnings(self, app_id: str) -> list[dict]:
        if self._tenant_id == "default":
            learnings_path = ROOT / "data" / "analytics" / "learnings" / app_id / "learnings_cumulative.json"
        else:
            learnings_path = ROOT / "data" / "tenants" / self._tenant_id / "analytics" / "learnings" / app_id / "learnings_cumulative.json"
        if not learnings_path.exists():
            return []
        try:
            data = json.loads(learnings_path.read_text(encoding="utf-8"))
            return data.get("learnings", [])
        except Exception:
            return []
