"""
Migratie script: file-based → SQLite.

Leest alle bestaande JSON-bestanden en importeert ze in SQLite databases.
Veilig om meerdere keren te draaien (upsert).

Gebruik:
    python scripts/migrate_to_sqlite.py
    python scripts/migrate_to_sqlite.py --tenant acme
    python scripts/migrate_to_sqlite.py --dry-run

Na migratie: zet REPO_BACKEND=sqlite in .env
"""

import argparse
import json
import sys
from pathlib import Path

# Voeg project root toe aan sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger

from backend.repository.file_campaigns import FileCampaignRepository
from backend.repository.file_experiments import FileExperimentRepository
from backend.repository.file_maturity import FileMaturityRepository
from backend.repository.sqlite_campaigns import SqliteCampaignRepository
from backend.repository.sqlite_experiments import SqliteExperimentRepository
from backend.repository.sqlite_maturity import SqliteMaturityRepository


def discover_tenants() -> list[str]:
    """Ontdek alle tenants uit data/tenants/ + altijd 'default'."""
    tenants = ["default"]
    tenants_dir = ROOT / "data" / "tenants"
    if tenants_dir.exists():
        for d in tenants_dir.iterdir():
            if d.is_dir() and d.name not in tenants:
                tenants.append(d.name)
    return tenants


def migrate_campaigns(tenant_id: str, dry_run: bool = False) -> int:
    file_repo = FileCampaignRepository(tenant_id=tenant_id)
    bundles = file_repo.list(tenant_id=tenant_id)
    if not bundles:
        return 0
    if dry_run:
        logger.info(f"  [DRY RUN] {len(bundles)} campagnes gevonden")
        return len(bundles)
    sqlite_repo = SqliteCampaignRepository(tenant_id=tenant_id)
    for b in bundles:
        sqlite_repo.save(b)
    return len(bundles)


def migrate_experiments(tenant_id: str, dry_run: bool = False) -> int:
    file_repo = FileExperimentRepository(tenant_id=tenant_id)
    # Lees alle experiment bestanden direct
    from backend.repository.file_experiments import _store_dir
    store_dir = _store_dir(tenant_id)
    if not store_dir.exists():
        return 0
    count = 0
    sqlite_repo = None if dry_run else SqliteExperimentRepository(tenant_id=tenant_id)
    for path in store_dir.glob("exp_*.json"):
        try:
            from experiments.models import Experiment
            exp = Experiment(**json.loads(path.read_text(encoding="utf-8")))
            if not dry_run:
                sqlite_repo.save(exp)
            count += 1
        except Exception as exc:
            logger.warning(f"  Overgeslagen: {path.name}: {exc}")
    if dry_run and count:
        logger.info(f"  [DRY RUN] {count} experimenten gevonden")
    return count


def migrate_maturity(tenant_id: str, dry_run: bool = False) -> int:
    file_repo = FileMaturityRepository(tenant_id=tenant_id)
    from backend.repository.file_maturity import _maturity_dir
    mat_dir = _maturity_dir(tenant_id)
    if not mat_dir.exists():
        return 0
    count = 0
    sqlite_repo = None if dry_run else SqliteMaturityRepository(tenant_id=tenant_id)
    for app_dir in mat_dir.iterdir():
        if not app_dir.is_dir():
            continue
        latest = file_repo.get_latest(app_dir.name, tenant_id=tenant_id)
        if latest and not dry_run:
            sqlite_repo.save_scorecard(latest)
            count += 1
        elif latest:
            count += 1
    if dry_run and count:
        logger.info(f"  [DRY RUN] {count} scorecards gevonden")
    return count


def main():
    parser = argparse.ArgumentParser(description="Migreer file-based data naar SQLite")
    parser.add_argument("--tenant", help="Migreer alleen deze tenant", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Alleen tellen, niet schrijven")
    args = parser.parse_args()

    tenants = [args.tenant] if args.tenant else discover_tenants()
    total = {"campaigns": 0, "experiments": 0, "maturity": 0}

    for tenant_id in tenants:
        logger.info(f"\n{'='*50}")
        logger.info(f"Tenant: {tenant_id}")
        logger.info(f"{'='*50}")

        n = migrate_campaigns(tenant_id, args.dry_run)
        total["campaigns"] += n
        logger.info(f"  Campagnes: {n}")

        n = migrate_experiments(tenant_id, args.dry_run)
        total["experiments"] += n
        logger.info(f"  Experimenten: {n}")

        n = migrate_maturity(tenant_id, args.dry_run)
        total["maturity"] += n
        logger.info(f"  Maturity scorecards: {n}")

    logger.info(f"\n{'='*50}")
    logger.info(f"TOTAAL: {sum(total.values())} records gemigreerd")
    logger.info(f"  Campagnes:    {total['campaigns']}")
    logger.info(f"  Experimenten: {total['experiments']}")
    logger.info(f"  Maturity:     {total['maturity']}")
    if not args.dry_run:
        logger.info("\nZet REPO_BACKEND=sqlite in .env om over te schakelen.")
    else:
        logger.info("\n[DRY RUN] Geen data geschreven.")


if __name__ == "__main__":
    main()
