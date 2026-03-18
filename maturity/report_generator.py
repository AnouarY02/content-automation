"""
Report Generator — genereert een leesbaar tekstrapport van een MaturityScorecard.

Gebruik:
    from maturity.report_generator import generate_report
    report = generate_report(scorecard)
    print(report)

Output: plain-text rapport met:
  - Samenvatting (score, status, datum)
  - Per-metric tabel (score, target, delta, evidence)
  - Dimensie-detail tabel (voor replication)
  - Statusbepaling toelichting
  - Concrete aanbevelingen (gebaseerd op scores)
"""

from __future__ import annotations

from maturity.models import DimensionMaturity, MaturityMetric, MaturityScorecard, MaturityStatus

# Status labels in leesbaar Nederlands
_STATUS_NL = {
    MaturityStatus.EARLY:            "EARLY (te weinig data)",
    MaturityStatus.VALIDATED:        "VALIDATED (patronen aantoonbaar)",
    MaturityStatus.INTERN_VOLWASSEN: "INTERN VOLWASSEN ✓",
}

_BAR_WIDTH = 20  # breedte van ASCII progress-bar


def generate_report(scorecard: MaturityScorecard) -> str:
    """
    Genereer een volledig tekstrapport van een MaturityScorecard.

    Args:
        scorecard: berekende scorecard

    Returns:
        Plain-text rapport als string
    """
    lines: list[str] = []

    lines += _header(scorecard)
    lines += _metric_table(scorecard.metrics)
    lines += _dimension_table(scorecard.dimension_details)
    lines += _status_explanation(scorecard)
    lines += _recommendations(scorecard)
    lines += _footer(scorecard)

    return "\n".join(lines)


# ── Secties ────────────────────────────────────────────────────────────

def _header(sc: MaturityScorecard) -> list[str]:
    status_label = _STATUS_NL.get(sc.status, sc.status.value)
    bar          = _progress_bar(sc.maturity_score)
    ts           = sc.computed_at.strftime("%Y-%m-%d %H:%M UTC")

    return [
        "=" * 60,
        "  INTERN VOLWASSEN SCORECARD",
        f"  App: {sc.app_id}",
        f"  Berekend: {ts}",
        "=" * 60,
        "",
        f"  MATURITY SCORE  {sc.maturity_score:.1f}/100",
        f"  {bar}",
        f"  STATUS: {status_label}",
        "",
        f"  Experimenten geanalyseerd : {sc.experiments_analyzed}",
        f"  Posts geanalyseerd        : {sc.posts_analyzed}",
        f"  Audit entries geanalyseerd: {sc.audit_entries_analyzed}",
        "",
    ]


def _metric_table(metrics: list[MaturityMetric]) -> list[str]:
    lines = [
        "-" * 60,
        "  SUBSCORES",
        "-" * 60,
        f"  {'Metric':<22} {'Score':>6}  {'Target':>6}  {'Δ':>6}  {'n':>4}",
        f"  {'-'*22} {'-'*6}  {'-'*6}  {'-'*6}  {'-'*4}",
    ]
    for m in metrics:
        delta = m.score - m.target
        delta_str = f"{delta:+.1f}"
        bar   = _mini_bar(m.score)
        lines.append(
            f"  {m.name:<22} {m.score:>5.1f}  {m.target:>6.1f}  "
            f"{delta_str:>6}  {m.evidence_count:>4}  {bar}"
        )
        if m.notes:
            lines.append(f"    ↳ {m.notes}")
    lines.append("")
    return lines


def _dimension_table(dims: list[DimensionMaturity]) -> list[str]:
    if not dims:
        return ["  Nog geen dimensie-data beschikbaar.", ""]

    lines = [
        "-" * 60,
        "  DIMENSIE REPLICATIE",
        "-" * 60,
        f"  {'Dimensie':<18} {'#Exp':>5}  {'Winner':<16} {'Cons':>5}  {'Conf':>5}  {'OK':>3}",
        f"  {'-'*18} {'-'*5}  {'-'*16} {'-'*5}  {'-'*5}  {'-'*3}",
    ]
    for d in dims:
        winner  = (d.winner_value or "—")[:16]
        cons    = f"{d.winner_consistency:.0%}" if d.winner_consistency else " —"
        conf    = f"{d.causal_confidence_avg:.0%}" if d.causal_confidence_avg else " —"
        ok      = "✓" if d.contributes_to_replication else "✗"
        lines.append(
            f"  {d.dimension:<18} {d.experiment_count:>5}  {winner:<16} "
            f"{cons:>5}  {conf:>5}  {ok:>3}"
        )
    lines.append("")
    return lines


def _status_explanation(sc: MaturityScorecard) -> list[str]:
    lines = [
        "-" * 60,
        "  STATUSBEPALING",
        "-" * 60,
    ]

    if sc.status == MaturityStatus.INTERN_VOLWASSEN:
        lines.append("  ✓ Alle vier criteria voor INTERN VOLWASSEN zijn voldaan:")
        lines.append("    composite ≥ 75, replication ≥ 70, stability ≥ 90, adoption ≥ 70")
    elif sc.status == MaturityStatus.VALIDATED:
        lines.append("  ✓ VALIDATED criteria voldaan (composite ≥ 50, replication ≥ 40)")
        lines.append("  → Verhoog adoption en stability voor INTERN VOLWASSEN")
    else:
        lines.append("  ✗ EARLY — nog te weinig data of te lage scores")
        lines.append("  → Genereer meer geconcludeerde experimenten om te starten")

    lines.append("")
    return lines


def _recommendations(sc: MaturityScorecard) -> list[str]:
    lines = [
        "-" * 60,
        "  AANBEVELINGEN",
        "-" * 60,
    ]
    recs: list[str] = []

    if sc.replication_score < 70:
        recs.append(
            f"  [replication {sc.replication_score:.0f}/70] "
            "Laat meer experimenten concluderen per dimensie (doel: ≥ 3/dim)."
        )
    if sc.prediction_accuracy < 65:
        recs.append(
            f"  [prediction  {sc.prediction_accuracy:.0f}/65] "
            "Quality scorer slecht gekalibreerd — check gewichten in quality/scorer.py."
        )
    if sc.learning_delta < 55:
        recs.append(
            f"  [delta       {sc.learning_delta:.0f}/55] "
            "Geen aantoonbare verbetering — controleer of winners echt sterker zijn."
        )
    if sc.operator_adoption < 80:
        recs.append(
            f"  [adoption    {sc.operator_adoption:.0f}/80] "
            "Meer campagnes via experiment-flow starten (EXPERIMENTS_ENABLED=true)."
        )
    if sc.stability_index < 95:
        recs.append(
            f"  [stability   {sc.stability_index:.0f}/95] "
            "Verhoog betrouwbaarheid — bekijk observability/audit logs op failures."
        )

    if not recs:
        lines.append("  Geen actiepunten — alle metrics op of boven target.")
    else:
        lines += recs

    lines.append("")
    return lines


def _footer(sc: MaturityScorecard) -> list[str]:
    return [
        "=" * 60,
        f"  Scorecard ID: {sc.scorecard_id}",
        "=" * 60,
    ]


# ── Hulpfuncties ───────────────────────────────────────────────────────

def _progress_bar(score: float, width: int = _BAR_WIDTH) -> str:
    """ASCII progress bar van 0–100."""
    filled = int(round(score / 100 * width))
    bar    = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {score:.1f}%"


def _mini_bar(score: float, width: int = 10) -> str:
    """Smalle ASCII bar voor in de tabel."""
    filled = int(round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)
