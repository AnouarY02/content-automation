"""
SQLite Database — gedeelde connection factory.

Ontwerp:
  - Eén database per tenant (data/db/{tenant_id}.db)
  - WAL mode voor concurrente reads + writes
  - Timeout 10s om lock contention te hanteren
  - Geen extra dependencies — gebruikt Python stdlib sqlite3

Switchover:
  Zet REPO_BACKEND=sqlite in .env om van file → SQLite te wisselen.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent  # content-automation/
DB_DIR = ROOT / "data" / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)


def get_connection(tenant_id: str = "default") -> sqlite3.Connection:
    """
    Maak of hergebruik een SQLite-connectie voor een tenant.

    WAL mode + busy_timeout zorgen voor goede concurrency.
    Row factory → sqlite3.Row voor dict-achtige access.
    """
    db_path = DB_DIR / f"{tenant_id}.db"
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_tables(conn: sqlite3.Connection) -> None:
    """Maak alle tabellen aan als ze nog niet bestaan."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            app_id      TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'draft',
            created_at  TEXT NOT NULL,
            data_json   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_campaigns_app ON campaigns(app_id);
        CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);

        CREATE TABLE IF NOT EXISTS experiments (
            experiment_id TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL DEFAULT 'default',
            app_id        TEXT NOT NULL,
            campaign_id   TEXT,
            status        TEXT NOT NULL DEFAULT 'pending',
            created_at    TEXT NOT NULL,
            dimension     TEXT,
            data_json     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_experiments_app ON experiments(app_id);
        CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
        CREATE INDEX IF NOT EXISTS idx_experiments_campaign ON experiments(campaign_id);

        CREATE TABLE IF NOT EXISTS maturity_scorecards (
            scorecard_id TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL DEFAULT 'default',
            app_id       TEXT NOT NULL,
            computed_at  TEXT NOT NULL,
            score        REAL NOT NULL,
            status       TEXT NOT NULL,
            data_json    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_maturity_app ON maturity_scorecards(app_id);

        CREATE TABLE IF NOT EXISTS maturity_history (
            snapshot_id  TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL DEFAULT 'default',
            app_id       TEXT NOT NULL,
            saved_at     TEXT NOT NULL,
            score        REAL NOT NULL,
            status       TEXT NOT NULL,
            data_json    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_maturity_history_app ON maturity_history(app_id);
    """)
