"""
Maturity Datamodellen.

Pydantic v2 modellen voor het Intern Volwassen Scorecard systeem.

Voorbeeld JSON MaturityScorecard:
{
  "scorecard_id": "msc_2026-03-10_app_demo",
  "app_id": "app_demo",
  "computed_at": "2026-03-10T12:00:00",
  "maturity_score": 67.4,
  "status": "validated",
  "replication_score": 60.0,
  "prediction_accuracy": 72.5,
  "learning_delta": 58.0,
  "operator_adoption": 75.0,
  "stability_index": 95.0,
  "experiments_analyzed": 12,
  "posts_analyzed": 47,
  "audit_entries_analyzed": 210,
  "metrics": [ {...}, {...}, {...}, {...}, {...} ],
  "dimension_details": [ {...}, {...} ]
}
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Status classificatie ───────────────────────────────────────────────

class MaturityStatus(str, Enum):
    """
    Drie niveaus van systeemvolwassenheid.

    EARLY            → systeem draait, maar te weinig data voor conclusies.
    VALIDATED        → patronen zijn reproduceerbaar, adoptie groeit.
    INTERN_VOLWASSEN → systeem werkt consistent en wordt actief gebruikt.
    """
    EARLY            = "early"
    VALIDATED        = "validated"
    INTERN_VOLWASSEN = "intern_volwassen"


# ── Enkel metric ───────────────────────────────────────────────────────

class MaturityMetric(BaseModel):
    """
    Één gemeten dimensie van systeemvolwassenheid.

    Voorbeeld JSON:
    {
      "name": "replication_score",
      "raw_value": 0.60,
      "score": 60.0,
      "weight": 0.25,
      "target": 60.0,
      "evidence_count": 9,
      "notes": "3 van 5 dimensies hebben ≥3 geconcludeerde experimenten met consistente winnaar"
    }
    """
    name:            str
    raw_value:       float           # Ongeschaald (0.0–1.0 of feitelijke waarde)
    score:           float           # 0–100 genormaliseerde score
    weight:          float           # Bijdrage aan composite (som = 1.0)
    target:          float           # Drempelwaarde voor VALIDATED-niveau
    evidence_count:  int             # Aantal datapunten gebruikt bij berekening
    notes:           str = ""        # Toelichting of waarschuwing bij te weinig data


# ── Dimensie-niveau detail ─────────────────────────────────────────────

class DimensionMaturity(BaseModel):
    """
    Per-dimensie analyse voor de replication_score berekening.

    Voorbeeld JSON:
    {
      "dimension": "hook_type",
      "experiment_count": 4,
      "has_consistent_winner": true,
      "winner_value": "bold_claim",
      "winner_consistency": 0.75,
      "causal_confidence_avg": 0.72,
      "contributes_to_replication": true
    }
    """
    dimension:               str
    experiment_count:        int
    has_consistent_winner:   bool
    winner_value:            Optional[str] = None   # Meest voorkomende winnende waarde
    winner_consistency:      float = 0.0            # Fractie experimenten gewonnen door winner_value
    causal_confidence_avg:   float = 0.0            # Gemiddeld causal_confidence over concludende exps
    contributes_to_replication: bool = False        # True als aan alle drempels voldaan


# ── Scorecard (één meting) ─────────────────────────────────────────────

class MaturityScorecard(BaseModel):
    """
    Volledige scorecard voor één app op één moment.

    Opslaglocatie: data/maturity/{app_id}/latest.json

    Voorbeeld JSON: zie module docstring.
    """
    scorecard_id:            str = Field(
        default_factory=lambda: f"msc_{uuid4().hex[:8]}"
    )
    app_id:                  str
    tenant_id:               str = "default"  # Tenant isolatie — "default" = backward compat
    computed_at:             datetime = Field(default_factory=datetime.utcnow)

    # Samengestelde score
    maturity_score:          float           # 0–100 gewogen composite
    status:                  MaturityStatus

    # Subscores (0–100) — directe toegang naast metrics-lijst
    replication_score:       float
    prediction_accuracy:     float
    learning_delta:          float
    operator_adoption:       float
    stability_index:         float

    # Gedetailleerde metrics (5 items, één per subscore)
    metrics:                 list[MaturityMetric] = []

    # Dimensie-niveau detail (voor replication)
    dimension_details:       list[DimensionMaturity] = []

    # Metadata
    experiments_analyzed:    int = 0
    posts_analyzed:          int = 0
    audit_entries_analyzed:  int = 0


# ── Snapshot (historisch record) ──────────────────────────────────────

class MaturitySnapshot(BaseModel):
    """
    Historisch record van een scorecard-berekening.
    Opgeslagen in data/maturity/{app_id}/history.jsonl.

    Voorbeeld JSON:
    {
      "snapshot_id": "snp_a1b2c3d4",
      "app_id": "app_demo",
      "saved_at": "2026-03-10T12:00:00",
      "maturity_score": 67.4,
      "status": "validated",
      "scorecard": { ... }
    }
    """
    snapshot_id:     str = Field(default_factory=lambda: f"snp_{uuid4().hex[:8]}")
    app_id:          str
    tenant_id:       str = "default"   # Tenant isolatie — "default" = backward compat
    saved_at:        datetime = Field(default_factory=datetime.utcnow)

    # Samenvatting (voor snelle lijstweergave zonder scorecard te parsen)
    maturity_score:  float
    status:          MaturityStatus

    # Volledige scorecard
    scorecard:       MaturityScorecard


# ── Thresholds (centrale definitie) ──────────────────────────────────

class MaturityThresholds:
    """
    Exacte drempelwaarden voor status-classificatie.

    INTERN_VOLWASSEN vereist:
      composite       >= 75.0
      replication     >= 70.0
      stability       >= 90.0
      operator_adoption >= 70.0

    VALIDATED vereist:
      composite       >= 50.0
      replication     >= 40.0

    EARLY: alles hieronder.
    """
    INTERN_VOLWASSEN_COMPOSITE      = 75.0
    INTERN_VOLWASSEN_REPLICATION    = 70.0
    INTERN_VOLWASSEN_STABILITY      = 90.0
    INTERN_VOLWASSEN_ADOPTION       = 70.0

    VALIDATED_COMPOSITE             = 50.0
    VALIDATED_REPLICATION           = 40.0

    # Metric targets (= drempel voor VALIDATED per metric)
    METRIC_TARGETS: dict[str, float] = {
        "replication_score":   60.0,
        "prediction_accuracy": 65.0,
        "learning_delta":      55.0,
        "operator_adoption":   80.0,
        "stability_index":     95.0,
    }

    # Gewichten (som = 1.0)
    WEIGHTS: dict[str, float] = {
        "replication_score":   0.25,
        "prediction_accuracy": 0.20,
        "learning_delta":      0.20,
        "operator_adoption":   0.20,
        "stability_index":     0.15,
    }
