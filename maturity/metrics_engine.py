"""
Metrics Engine — berekent de 5 ruwe maturity-scores.

Alle berekeningen zijn:
  - Deterministisch (geen randomness, geen externe API-calls)
  - Read-only ten opzichte van bestaande stores
  - Robuust bij ontbrekende/lege data (geeft 0.0 terug met notes)

Beschikbare methoden:
  replication_score()    → (score: float, dims: list[DimensionMaturity], evidence: int)
  prediction_accuracy()  → (score: float, evidence: int, notes: str)
  learning_delta()       → (score: float, evidence: int, notes: str)
  operator_adoption()    → (score: float, evidence: int, notes: str)
  stability_index()      → (score: float, evidence: int, notes: str)
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from experiments.experiment_store import ExperimentStore
from experiments.models import ExperimentDimension, ExperimentStatus
from maturity.models import DimensionMaturity
from observability.audit_store import AuditStore

ROOT = Path(__file__).parent.parent

# Drempelwaarden voor replication kwalificatie
_MIN_EXPERIMENTS_PER_DIM = 3
_MIN_WINNER_CONSISTENCY  = 0.67   # ≥ 67% van concluded exps heeft dezelfde winnaar
_MIN_CAUSAL_CONFIDENCE   = 0.70   # gemiddeld causal_confidence


def _resolve_campaigns_dir(tenant_id: str) -> Path:
    """
    'default' → data/campaigns/        (backward compat)
    overig    → data/tenants/{tenant_id}/campaigns/
    """
    if tenant_id == "default":
        return ROOT / "data" / "campaigns"
    return ROOT / "data" / "tenants" / tenant_id / "campaigns"


class MetricsEngine:
    """Berekent alle 5 maturity-metrics voor één app_id — tenant-geïsoleerd."""

    def __init__(self, app_id: str, tenant_id: str = "default") -> None:
        self.app_id = app_id
        self._tenant_id = tenant_id
        self._store = ExperimentStore(tenant_id=tenant_id)
        self._campaigns_dir = _resolve_campaigns_dir(tenant_id)

    # ── 1. Replication Score ─────────────────────────────────────────

    def replication_score(
        self,
    ) -> tuple[float, list[DimensionMaturity], int]:
        """
        Percentage dimensies dat repliceerbare resultaten produceert.

        Kwalificatie per dimensie:
          ≥ 3 geconcludeerde experimenten
          Consistente winnende waarde in ≥ 67% van die experimenten
          Gemiddeld causal_confidence ≥ 0.70

        Returns:
          score (0–100), dimension_details, evidence_count
        """
        experiments = [
            e for e in self._store.list_by_app(self.app_id)
            if e.status == ExperimentStatus.CONCLUDED
        ]

        if not experiments:
            return 0.0, [], 0

        # Groepeer per dimensie
        by_dim: dict[str, list] = {}
        for exp in experiments:
            dim_val = exp.hypothesis.dimension.value
            by_dim.setdefault(dim_val, []).append(exp)

        qualifying    = 0
        eligible_dims = 0
        details: list[DimensionMaturity] = []

        for dim_val, exps in by_dim.items():
            count = len(exps)

            if count < _MIN_EXPERIMENTS_PER_DIM:
                details.append(DimensionMaturity(
                    dimension=dim_val,
                    experiment_count=count,
                    has_consistent_winner=False,
                    contributes_to_replication=False,
                ))
                continue

            eligible_dims += 1

            # Verzamel winnende waarden (dimension_value van de winner-variant)
            winner_values: list[str] = []
            confidences:   list[float] = []

            for exp in exps:
                if not exp.winning_variant_id:
                    continue
                winner = exp.get_variant(exp.winning_variant_id)
                if winner:
                    winner_values.append(winner.spec.dimension_value)
                if exp.causal_confidence is not None:
                    confidences.append(exp.causal_confidence)

            if not winner_values:
                details.append(DimensionMaturity(
                    dimension=dim_val,
                    experiment_count=count,
                    has_consistent_winner=False,
                    contributes_to_replication=False,
                ))
                continue

            # Consistentie: meest voorkomende winner / totaal met winner
            counter = Counter(winner_values)
            top_value, top_count = counter.most_common(1)[0]
            consistency = top_count / len(winner_values)

            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            qualifies = (
                consistency     >= _MIN_WINNER_CONSISTENCY and
                avg_confidence  >= _MIN_CAUSAL_CONFIDENCE
            )
            if qualifies:
                qualifying += 1

            details.append(DimensionMaturity(
                dimension=dim_val,
                experiment_count=count,
                has_consistent_winner=consistency >= _MIN_WINNER_CONSISTENCY,
                winner_value=top_value,
                winner_consistency=round(consistency, 3),
                causal_confidence_avg=round(avg_confidence, 3),
                contributes_to_replication=qualifies,
            ))

        if eligible_dims == 0:
            return 0.0, details, len(experiments)

        score = (qualifying / eligible_dims) * 100.0
        return round(score, 1), details, len(experiments)

    # ── 2. Prediction Accuracy ───────────────────────────────────────

    def prediction_accuracy(self) -> tuple[float, int, str]:
        """
        Hoe vaak voorspelt de quality scorer de correcte winnaar?

        Methode: voor geconcludeerde experimenten met beide varianten gescoord:
          predicted_winner = variant met hoogste quality_score["composite"]
          correct          = predicted_winner == actual winning_variant_id

        Returns:
          score (0–100), evidence_count, notes
        """
        experiments = [
            e for e in self._store.list_by_app(self.app_id)
            if e.status == ExperimentStatus.CONCLUDED
               and e.winning_variant_id is not None
        ]

        if not experiments:
            return 0.0, 0, "Geen geconcludeerde experimenten"

        correct = 0
        total   = 0

        for exp in experiments:
            # Alle varianten moeten een quality_score hebben
            if not all(v.quality_score is not None for v in exp.variants):
                continue
            if len(exp.variants) < 2:
                continue

            # Variant met hoogste quality composite = predicted winner
            scored = sorted(
                exp.variants,
                key=lambda v: (v.quality_score or {}).get("composite", 0.0),
                reverse=True,
            )
            predicted_winner_id = scored[0].variant_id

            total += 1
            if predicted_winner_id == exp.winning_variant_id:
                correct += 1

        if total < 3:
            note = f"Slechts {total} bruikbare datapunten — min. 3 vereist voor betrouwbare schatting"
            return 0.0, total, note

        score = (correct / total) * 100.0
        note  = f"{correct}/{total} correcte voorspellingen"
        return round(score, 1), total, note

    # ── 3. Learning Delta ────────────────────────────────────────────

    def learning_delta(self) -> tuple[float, int, str]:
        """
        Positieve trend in performance_score van winnende varianten over tijd?

        Methode: per dimensie de performance_score van winners over concluded_at sorteren.
        Lineaire regressie-slope per dimensie. Gemiddeld geaggregeerd.
        Genormaliseerd: slope 0 → score 50, slope +5/ronde → 100, slope -5/ronde → 0.

        Returns:
          score (0–100), evidence_count, notes
        """
        experiments = [
            e for e in self._store.list_by_app(self.app_id)
            if (
                e.status      == ExperimentStatus.CONCLUDED
                and e.winning_variant_id is not None
                and e.concluded_at is not None
            )
        ]

        if not experiments:
            return 50.0, 0, "Geen geconcludeerde experimenten — neutraal (50)"

        # Verzamel per dimensie: (concluded_at, performance_score)
        by_dim: dict[str, list[tuple]] = {}
        for exp in experiments:
            winner = exp.get_variant(exp.winning_variant_id)
            if winner is None or winner.performance_score is None:
                continue
            dim = exp.hypothesis.dimension.value
            by_dim.setdefault(dim, []).append((exp.concluded_at, winner.performance_score))

        if not by_dim:
            total_exps = len(experiments)
            return 50.0, total_exps, "Winners hebben nog geen performance_score — neutraal (50)"

        slopes:          list[float] = []
        evidence_count:  int         = 0

        for dim, data_points in by_dim.items():
            if len(data_points) < 2:
                continue
            data_points.sort(key=lambda x: x[0])
            scores = [s for _, s in data_points]
            evidence_count += len(scores)

            slope = _linear_slope(scores)
            slopes.append(slope)

        if not slopes:
            return 50.0, evidence_count, "Te weinig datapunten per dimensie (min. 2 per dim)"

        avg_slope = sum(slopes) / len(slopes)
        # Normalisatie: slope 0 = 50, ±5 per ronde = ±50 punten (gecapped 0–100)
        score = 50.0 + (avg_slope * 10.0)
        score = max(0.0, min(100.0, score))

        direction = "stijgend" if avg_slope > 0 else "dalend" if avg_slope < 0 else "stabiel"
        note = f"Gemiddelde slope: {avg_slope:.2f}/ronde ({direction})"
        return round(score, 1), evidence_count, note

    # ── 4. Operator Adoption ─────────────────────────────────────────

    def operator_adoption(self) -> tuple[float, int, str]:
        """
        Percentage campagnes dat via de experiment-flow is goedgekeurd.

        Methode: lees alle CampaignBundle JSON-bestanden in data/campaigns/.
        Filter op app_id en status in (approved, published).
        Tel hoeveel experiment_id hebben (= via experiment-flow).

        Returns:
          score (0–100), evidence_count (total approved bundles), notes
        """
        if not self._campaigns_dir.exists():
            return 0.0, 0, f"campaigns map bestaat niet voor tenant '{self._tenant_id}'"

        approved_total    = 0
        via_experiment    = 0
        APPROVED_STATUSES = {"approved", "published"}

        for path in self._campaigns_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if data.get("app_id") != self.app_id:
                continue

            status = str(data.get("status", "")).lower()
            if status not in APPROVED_STATUSES:
                continue

            approved_total += 1
            if data.get("experiment_id"):
                via_experiment += 1

        if approved_total == 0:
            return 0.0, 0, "Geen goedgekeurde campagnes gevonden voor deze app"

        score = (via_experiment / approved_total) * 100.0
        note  = f"{via_experiment}/{approved_total} campagnes via experiment-flow"
        return round(score, 1), approved_total, note

    # ── 5. Stability Index ───────────────────────────────────────────

    def stability_index(self) -> tuple[float, int, str]:
        """
        Technische betrouwbaarheid: 1 - failure_rate over de laatste 7 dagen.

        Methode: AuditStore.get_failure_rate(app_id, hours=168).
        Geen audit data → aanname: stabiel (score 100).

        Returns:
          score (0–100), evidence_count (relevante audit entries), notes
        """
        try:
            audit = AuditStore(tenant_id=self._tenant_id)
            # Lees entries voor dit app om evidence_count te bepalen
            entries = audit.read_recent(app_id=self.app_id, limit=500)
            recent  = [
                e for e in entries
                if e.timestamp is not None
            ]
            evidence_count = len(recent)

            failure_rate = audit.get_failure_rate(
                app_id=self.app_id,
                hours=168,       # 7 dagen
            )
            score = (1.0 - failure_rate) * 100.0
            note  = (
                f"Failure rate: {failure_rate:.1%} over laatste 7 dagen "
                f"({evidence_count} audit entries)"
            )
            return round(score, 1), evidence_count, note

        except Exception as exc:
            logger.warning(f"[MaturityEngine] stability_index fout: {exc}")
            return 100.0, 0, "Geen audit data — aanname stabiel (100)"


# ── Hulpfunctie ────────────────────────────────────────────────────────

def _linear_slope(values: list[float]) -> float:
    """
    Berekent de lineaire regressie-slope van een reeks waarden.
    x = tijdindex (0, 1, 2, ...), y = values.
    """
    n = len(values)
    if n < 2:
        return 0.0
    x       = list(range(n))
    x_mean  = sum(x) / n
    y_mean  = sum(values) / n
    num     = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, values))
    denom   = sum((xi - x_mean) ** 2 for xi in x)
    return num / denom if denom != 0 else 0.0
