from __future__ import annotations

import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = ROOT / "investigation.sqlite"


def get_investigation_db_path() -> Path:
    configured = os.environ.get("SAQR_INVESTIGATION_DB_PATH")
    if configured:
        db_path = Path(configured).expanduser()
        if not db_path.is_absolute():
            db_path = ROOT / db_path
        return db_path
    return DEFAULT_DB_PATH


def get_investigation_connection(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else get_investigation_db_path()
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investigations (
            investigation_id TEXT PRIMARY KEY,
            calculation_id INTEGER NOT NULL,
            primary_indicator_id INTEGER NOT NULL,
            operational_day TEXT,
            company TEXT,
            production_order TEXT,
            resource_center TEXT,
            line TEXT,
            product_code TEXT,
            product_description TEXT,
            title TEXT NOT NULL,
            description TEXT,
            origin TEXT,
            stage TEXT,
            status TEXT NOT NULL,
            confidence TEXT,
            owner TEXT,
            due_date TEXT,
            engine_version TEXT,
            rule_versions_json TEXT,
            indicator_snapshot_json TEXT,
            origin_snapshot_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS investigation_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            investigation_id TEXT NOT NULL REFERENCES investigations(investigation_id),
            indicator_id INTEGER NOT NULL,
            indicator_value REAL,
            unit TEXT,
            confidence TEXT,
            rule_version TEXT,
            snapshot_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS investigation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            investigation_id TEXT NOT NULL REFERENCES investigations(investigation_id),
            event_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    return conn
