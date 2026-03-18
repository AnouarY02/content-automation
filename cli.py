"""
AY Marketing OS — Command Line Interface
Gebruik voor snelle tests en handmatige runs.

Gebruik:
  python cli.py run-campaign --app app_001
  python cli.py list-pending
  python cli.py approve <campaign_id>
  python cli.py reject <campaign_id> --reason "Te lang"

  # Analytics & Feedback
  python cli.py run-feedback --app app_001
  python cli.py show-learnings --app app_001
  python cli.py show-scores --app app_001
  python cli.py start-scheduler
"""

import json
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from loguru import logger
from rich.console import Console
from rich.table import Table

load_dotenv(Path(__file__).parent / ".env")
console = Console()


@click.group()
def cli():
    """AY Marketing Operating System CLI."""
    pass


@cli.command()
@click.option("--app", required=True, help="App ID (bijv. app_001)")
@click.option("--platform", default="tiktok", help="Platform (standaard: tiktok)")
@click.option("--idea", default=0, type=int, help="Idee index 0-4 (standaard: 0 = beste)")
def run_campaign(app: str, platform: str, idea: int):
    """Start een volledige campagne-pipeline voor een app."""
    from workflows.campaign_pipeline import run_pipeline

    console.print(f"\n[bold green]Start campagne pipeline[/bold green] voor [cyan]{app}[/cyan] op [cyan]{platform}[/cyan]\n")

    def on_progress(msg: str):
        console.print(f"  {msg}")

    try:
        bundle = run_pipeline(
            app_id=app,
            platform=platform,
            idea_index=idea,
            on_progress=on_progress,
        )
        console.print(f"\n[bold green]✓ Campagne klaar![/bold green]")
        console.print(f"  ID:     [cyan]{bundle.id}[/cyan]")
        console.print(f"  Status: [yellow]{bundle.status}[/yellow]")
        console.print(f"  Kosten: ${bundle.total_cost_usd:.4f}")
        if bundle.video_path:
            console.print(f"  Video:  {bundle.video_path}")
        console.print(f"\n  Gebruik [bold]python cli.py approve {bundle.id}[/bold] om goed te keuren\n")
    except Exception as e:
        console.print(f"\n[bold red]Pipeline mislukt:[/bold red] {e}\n")
        sys.exit(1)


@cli.command()
def list_pending():
    """Toon alle campagnes die wachten op goedkeuring."""
    from workflows.campaign_pipeline import list_pending_campaigns

    bundles = list_pending_campaigns()

    if not bundles:
        console.print("\n[yellow]Geen campagnes wachten op goedkeuring.[/yellow]\n")
        return

    table = Table(title="Wacht op goedkeuring")
    table.add_column("ID", style="cyan")
    table.add_column("App")
    table.add_column("Idee")
    table.add_column("Kosten")
    table.add_column("Aangemaakt")

    for b in bundles:
        table.add_row(
            b.id,
            b.app_id,
            b.idea.get("title", "?") if b.idea else "?",
            f"${b.total_cost_usd:.4f}",
            str(b.created_at)[:16],
        )

    console.print(table)


@cli.command()
@click.argument("campaign_id")
@click.option("--notes", default="", help="Optionele notitie")
def approve(campaign_id: str, notes: str):
    """Keur een campagne goed en publiceer deze."""
    from backend.models.campaign import ApprovalRequest, ApprovalDecision
    from backend.services.approval_service import process_approval

    console.print(f"\n[bold green]Goedkeuren:[/bold green] {campaign_id}")

    request = ApprovalRequest(
        campaign_id=campaign_id,
        decision=ApprovalDecision.APPROVE,
        notes=notes,
    )
    bundle = process_approval(request, approved_by="cli_user")
    console.print(f"[bold green]✓ Goedgekeurd en gepubliceerd![/bold green] Status: {bundle.status}\n")


@cli.command()
@click.argument("campaign_id")
@click.option("--reason", required=True, help="Reden voor afwijzing")
def reject(campaign_id: str, reason: str):
    """Wijs een campagne af."""
    from backend.models.campaign import ApprovalRequest, ApprovalDecision
    from backend.services.approval_service import process_approval

    request = ApprovalRequest(
        campaign_id=campaign_id,
        decision=ApprovalDecision.REJECT,
        notes=reason,
    )
    process_approval(request, approved_by="cli_user")
    console.print(f"[red]✗ Campagne {campaign_id} afgewezen.[/red]\n")


@cli.command()
@click.argument("campaign_id")
@click.option("--changes", required=True, help="Welke wijzigingen gevraagd worden")
def request_changes(campaign_id: str, changes: str):
    """Vraag wijzigingen aan voor een campagne (stuurt terug naar draft)."""
    from backend.models.campaign import ApprovalRequest, ApprovalDecision
    from backend.services.approval_service import process_approval

    request = ApprovalRequest(
        campaign_id=campaign_id,
        decision=ApprovalDecision.REQUEST_CHANGES,
        notes=changes,
    )
    process_approval(request, approved_by="cli_user")
    console.print(f"[yellow]↩ Campagne {campaign_id} teruggestuurd voor wijzigingen.[/yellow]\n")


@cli.command()
@click.argument("app_id")
def show_memory(app_id: str):
    """Toon de huidige brand memory voor een app."""
    from agents import brand_memory as bm
    memory = bm.load(app_id)
    console.print_json(json.dumps(memory, ensure_ascii=False, indent=2))


@cli.command()
@click.option("--app", required=True, help="App ID (bijv. app_001)")
@click.option("--force", is_flag=True, help="Forceer analyse ook met <3 posts")
def run_feedback(app: str, force: bool):
    """Voer een volledige feedback cyclus uit voor een app."""
    from analytics.learning_engine import LearningEngine

    console.print(f"\n[bold cyan]Feedback cyclus starten[/bold cyan] voor [cyan]{app}[/cyan]\n")
    engine = LearningEngine()
    result = engine.run_cycle(app_id=app, force_reanalyze=force)

    console.print(f"\n[bold green]Cyclus klaar![/bold green]")
    console.print(f"  Nieuwe posts geanalyseerd: {result['new_posts_analyzed']}")
    console.print(f"  Totaal in store:           {result['total_posts_in_store']}")
    console.print(f"  Analyse uitgevoerd:        {result['analysis_performed']}")
    console.print(f"  Leerpunten gegenereerd:    {result['learnings_generated']}")
    if result["errors"]:
        console.print(f"\n[yellow]Fouten:[/yellow]")
        for err in result["errors"]:
            console.print(f"  - {err}")


@cli.command()
@click.option("--app", required=True, help="App ID")
def show_learnings(app: str):
    """Toon alle actieve leerpunten voor een app."""
    from workflows.feedback_loop import get_learning_summary

    summary = get_learning_summary(app)

    console.print(f"\n[bold]Learnings voor {app}[/bold]")
    console.print(f"Posts geanalyseerd: {summary['total_posts_analyzed']}")
    console.print(f"Actieve learnings:  {summary['active_learnings']} (HIGH={summary['high_confidence']}, MED={summary['medium_confidence']})")

    b = summary["benchmark"]
    console.print(f"\n[bold]Benchmark:[/bold]")
    console.print(f"  Gem. score:  {b['avg_score']}")
    console.print(f"  Gem. views:  {b['avg_views']:,}")
    console.print(f"  Beste score: {b['best_score']}")

    if summary["top_learnings"]:
        console.print(f"\n[bold green]Wat werkt:[/bold green]")
        for l in summary["top_learnings"]:
            console.print(f"  [{l['confidence']}] {l['category']}: {l['finding']}")

    if summary["avoid_learnings"]:
        console.print(f"\n[bold red]Vermijd:[/bold red]")
        for l in summary["avoid_learnings"]:
            console.print(f"  [{l['confidence']}] {l['category']}: {l['finding']}")
    console.print()


@cli.command()
@click.option("--app", required=True, help="App ID")
@click.option("--limit", default=10, help="Aantal posts om te tonen")
def show_scores(app: str, limit: int):
    """Toon performance scores van geanalyseerde posts."""
    from analytics.metrics_store import MetricsStore

    ms = MetricsStore()
    analyses = ms.load_all_post_analyses(app, limit=limit)

    if not analyses:
        console.print(f"\n[yellow]Geen geanalyseerde posts gevonden voor {app}[/yellow]\n")
        return

    table = Table(title=f"Performance scores — {app}")
    table.add_column("Post ID", style="cyan")
    table.add_column("Score", style="bold")
    table.add_column("Views")
    table.add_column("ER%")
    table.add_column("Completion%")
    table.add_column("Hook")
    table.add_column("Format")
    table.add_column("Confidence")

    for a in analyses:
        score_color = "green" if a.score.composite_score >= 70 else ("yellow" if a.score.composite_score >= 50 else "red")
        table.add_row(
            a.post_id[:12],
            f"[{score_color}]{a.score.composite_score:.1f}[/{score_color}]",
            f"{a.raw.views:,}",
            f"{a.normalized.engagement_rate:.1%}",
            f"{a.normalized.completion_rate:.1%}",
            a.tags.hook_type,
            a.tags.content_format,
            a.score.confidence_level,
        )

    console.print(table)


@cli.command()
def start_scheduler():
    """Start de achtergrond scheduler voor automatische feedback runs."""
    from workflows.scheduler import start
    console.print("\n[bold cyan]Scheduler starten...[/bold cyan]")
    console.print("Druk Ctrl+C om te stoppen\n")
    start()


@cli.command()
def health():
    """Toon de health status van alle systeem-componenten."""
    from observability.health_checker import get_health_checker
    from observability.models import HealthStatus

    checker = get_health_checker()
    snapshot = checker.check_all(force=True)

    status_colors = {
        HealthStatus.HEALTHY: "green",
        HealthStatus.DEGRADED: "yellow",
        HealthStatus.UNHEALTHY: "red",
        HealthStatus.UNKNOWN: "grey50",
    }

    console.print(f"\n[bold]Systeem Health — {snapshot.taken_at.strftime('%H:%M:%S')}[/bold]")
    overall_color = status_colors.get(snapshot.overall_status, "white")
    console.print(f"Overall: [{overall_color}]{snapshot.overall_status.upper()}[/{overall_color}]")
    console.print(f"  Healthy={snapshot.healthy_count} | Degraded={snapshot.degraded_count} | Unhealthy={snapshot.unhealthy_count}\n")

    table = Table()
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Latency (ms)")
    table.add_column("Details")

    for name, comp in snapshot.components.items():
        color = status_colors.get(comp.status, "white")
        latency = f"{comp.latency_ms:.0f}" if comp.latency_ms else "-"
        detail = comp.error_message or (str(comp.details)[:50] if comp.details else "-")
        table.add_row(
            name,
            f"[{color}]{comp.status.upper()}[/{color}]",
            latency,
            detail,
        )

    console.print(table)


@cli.command()
@click.option("--app", default=None, help="Filter op app ID")
@click.option("--limit", default=10)
def show_alerts(app: str, limit: int):
    """Toon actieve alerts."""
    from observability.alerting import get_alerting_service

    alerts = get_alerting_service().get_active_alerts(app_id=app)[:limit]
    if not alerts:
        console.print("\n[green]Geen actieve alerts.[/green]\n")
        return

    table = Table(title="Actieve Alerts")
    table.add_column("ID", style="cyan")
    table.add_column("Severity", style="bold")
    table.add_column("Titel")
    table.add_column("App")
    table.add_column("Tijd")

    for a in alerts:
        sev_color = {"critical": "red", "high": "red", "warning": "yellow", "info": "blue"}.get(a.severity, "white")
        table.add_row(
            a.alert_id,
            f"[{sev_color}]{a.severity.upper()}[/{sev_color}]",
            a.title[:50],
            a.app_id or "-",
            a.triggered_at.strftime("%m-%d %H:%M"),
        )
    console.print(table)


@cli.command()
@click.option("--app", default=None, help="Filter op app ID")
@click.option("--limit", default=20)
def show_audit(app: str, limit: int):
    """Toon recente audit trail entries."""
    from observability.audit_store import get_audit_store

    entries = get_audit_store().read_recent(app_id=app, limit=limit)
    if not entries:
        console.print("\n[yellow]Geen audit entries gevonden.[/yellow]\n")
        return

    table = Table(title="Audit Trail")
    table.add_column("Tijd")
    table.add_column("Job Type", style="cyan")
    table.add_column("Outcome", style="bold")
    table.add_column("Duur (s)")
    table.add_column("App")

    for e in entries:
        color = "green" if e.outcome == "success" else "red" if e.outcome in ("failure", "dead_lettered") else "yellow"
        table.add_row(
            e.timestamp.strftime("%m-%d %H:%M:%S"),
            e.job_type,
            f"[{color}]{e.outcome}[/{color}]",
            f"{e.duration_sec:.1f}" if e.duration_sec else "-",
            e.app_id or "-",
        )
    console.print(table)


@cli.command()
@click.option("--app", default=None, help="Filter op app ID")
def show_dead_letters(app: str):
    """Toon dead letter queue inhoud."""
    import json
    from pathlib import Path

    dl_dir = Path("C:/AY-automatisering/data/dead_letter")
    search = dl_dir / app if app else dl_dir
    entries = []

    if search.exists():
        for path in search.rglob("*.json"):
            if path.name == "idempotency_keys.json":
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    e = json.load(f)
                if not e.get("resolution"):
                    entries.append(e)
            except Exception:
                pass

    if not entries:
        console.print("\n[green]Geen onopgeloste dead letters.[/green]\n")
        return

    console.print(f"\n[bold red]Dead Letter Queue — {len(entries)} onopgeloste items[/bold red]\n")
    for e in entries:
        console.print(f"  [cyan]{e['dl_id']}[/cyan] | {e['job_type']} | {e.get('app_id', '-')}")
        console.print(f"    Fout: {e['final_error_type']}: {e['final_error'][:80]}")
        console.print(f"    Pogingen: {e['total_attempts']} | Laatste: {e.get('last_attempt', '?')[:16]}")
        console.print()


if __name__ == "__main__":
    cli()
