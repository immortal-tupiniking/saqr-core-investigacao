from __future__ import annotations

import json
import sqlite3
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from saqr.database import connect as connect_data_db
from saqr.explain import explain_indicator
from saqr.investigation.database import get_investigation_connection
from saqr.investigation.repository import InvestigationRepository

UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


class InvestigationService:
    def __init__(self, data_db_path: str | Path, investigation_db_path: str | Path | None = None) -> None:
        self.data_db_path = Path(data_db_path)
        self.investigation_db_path = Path(investigation_db_path) if investigation_db_path is not None else None

    def _connect_data(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.data_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_investigation(self) -> sqlite3.Connection:
        return get_investigation_connection(self.investigation_db_path)

    def _generate_id(self, conn: sqlite3.Connection) -> str:
        while True:
            letters = "".join(__import__("random").choice(string.ascii_uppercase) for _ in range(4))
            digits = "".join(__import__("random").choice(string.digits) for _ in range(4))
            token = f"INV-{letters}-{digits}"
            existing = conn.execute("SELECT 1 FROM investigations WHERE investigation_id=?", (token,)).fetchone()
            if not existing:
                return token

    def create_investigation(self, payload: dict[str, Any]) -> dict[str, Any]:
        data_conn = self._connect_data()
        try:
            calc_row = data_conn.execute("SELECT id, status, engine_version FROM calculation_runs WHERE id=?", (payload["calculation_id"],)).fetchone()
            if not calc_row:
                raise LookupError("calculation_id not found")
            if calc_row["status"] != "SUCCESS":
                raise ValueError("calculation_id is not successful")

            indicator_row = data_conn.execute(
                "SELECT id, indicator_code, dimension_type, dimension_key, value, unit, confidence, rule_code, rule_version, metadata_json FROM indicator_results WHERE id=? AND calculation_run_id=?",
                (payload["indicator_id"], payload["calculation_id"]),
            ).fetchone()
            if not indicator_row:
                raise LookupError("indicator_id not found")

            rule_row = data_conn.execute(
                "SELECT version, formula, config_json FROM rule_versions WHERE rule_code=? AND version=?",
                (indicator_row["rule_code"], indicator_row["rule_version"]),
            ).fetchone()
            catalog_row = data_conn.execute(
                "SELECT name, formula_description, official_source FROM indicator_catalog WHERE indicator_code=?",
                (indicator_row["indicator_code"],),
            ).fetchone()
            lineage_rows = data_conn.execute(
                "SELECT l.*, s.source_row, s.raw_json, i.filename, i.source_type, i.sha256, e.event_type, e.confidence AS event_confidence FROM indicator_lineage l LEFT JOIN source_records s ON s.id=l.source_record_id LEFT JOIN import_runs i ON i.id=s.import_run_id LEFT JOIN events e ON e.id=l.event_id WHERE l.indicator_result_id=? ORDER BY l.id",
                (indicator_row["id"],),
            ).fetchall()
        finally:
            data_conn.close()

        investigation_conn = self._connect_investigation()
        try:
            repository = InvestigationRepository(investigation_conn)
            investigation_id = self._generate_id(investigation_conn)
            now = UTC_NOW()
            line_value = payload.get("line") or (indicator_row["dimension_key"] if indicator_row["dimension_type"] == "OP" else None)
            indicator_snapshot = {
                "indicator_id": int(indicator_row["id"]),
                "indicator_code": indicator_row["indicator_code"],
                "dimension_type": indicator_row["dimension_type"],
                "dimension_key": indicator_row["dimension_key"],
                "value": indicator_row["value"],
                "unit": indicator_row["unit"],
                "confidence": indicator_row["confidence"],
                "rule_code": indicator_row["rule_code"],
                "rule_version": indicator_row["rule_version"],
                "metadata": json.loads(indicator_row["metadata_json"] or "{}"),
                "catalog": dict(catalog_row) if catalog_row else None,
                "rule": dict(rule_row) if rule_row else None,
                "lineage": [dict(r) for r in lineage_rows],
            }
            rule_versions = []
            if rule_row:
                rule_versions.append({"rule_code": indicator_row["rule_code"], "version": indicator_row["rule_version"], "formula": rule_row["formula"], "config_json": json.loads(rule_row["config_json"] or "{}")})
            origin_snapshot = {
                "loss_id": payload.get("loss_id"),
                "calculation_id": payload["calculation_id"],
                "engine_version": calc_row["engine_version"],
                "rule_versions": rule_versions,
                "lineage": [dict(r) for r in lineage_rows],
            }
            investigation_payload = {
                "investigation_id": investigation_id,
                "calculation_id": payload["calculation_id"],
                "primary_indicator_id": int(indicator_row["id"]),
                "operational_day": payload.get("operational_day"),
                "company": payload.get("company"),
                "production_order": payload.get("production_order"),
                "resource_center": payload.get("resource_center"),
                "line": line_value,
                "product_code": payload.get("product_code"),
                "product_description": payload.get("product_description"),
                "title": payload["title"],
                "description": payload.get("description"),
                "origin": payload.get("origin"),
                "stage": payload.get("stage"),
                "status": "OPEN",
                "confidence": indicator_row["confidence"],
                "owner": payload.get("owner"),
                "due_date": payload.get("due_date"),
                "engine_version": calc_row["engine_version"],
                "rule_versions_json": json.dumps(rule_versions, ensure_ascii=False, sort_keys=True),
                "indicator_snapshot_json": json.dumps(indicator_snapshot, ensure_ascii=False, sort_keys=True),
                "origin_snapshot_json": json.dumps(origin_snapshot, ensure_ascii=False, sort_keys=True),
                "created_at": now,
                "updated_at": now,
            }
            repository.create_investigation(investigation_payload)
            repository.create_indicator_snapshot(investigation_id, {
                "indicator_id": int(indicator_row["id"]),
                "indicator_value": indicator_row["value"],
                "unit": indicator_row["unit"],
                "confidence": indicator_row["confidence"],
                "rule_version": indicator_row["rule_version"],
                "snapshot_json": indicator_snapshot,
                "created_at": now,
            })
            repository.create_history_event(investigation_id, {
                "event_type": "INVESTIGATION_CREATED",
                "payload_json": {"calculation_id": payload["calculation_id"], "indicator_id": payload["indicator_id"], "loss_id": payload.get("loss_id")},
                "created_at": now,
            })
            repository.commit()
            return self.get_investigation(investigation_id)
        except Exception:
            investigation_conn.rollback()
            raise
        finally:
            investigation_conn.close()

    def list_investigations(self, filters: dict[str, Any], page: int, page_size: int) -> dict[str, Any]:
        investigation_conn = self._connect_investigation()
        try:
            repository = InvestigationRepository(investigation_conn)
            rows, total_count = repository.list_investigations(filters, page, page_size)
            return {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "items": [
                    {
                        "investigation_id": row["investigation_id"],
                        "title": row["title"],
                        "status": row["status"],
                        "confidence": row["confidence"],
                        "operational_day": row["operational_day"],
                        "company": row["company"],
                        "line": row["line"],
                        "production_order": row["production_order"],
                        "product_code": row["product_code"],
                        "stage": row["stage"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ],
            }
        finally:
            investigation_conn.close()

    def get_investigation(self, investigation_id: str) -> dict[str, Any]:
        investigation_conn = self._connect_investigation()
        try:
            repository = InvestigationRepository(investigation_conn)
            row = repository.get_investigation(investigation_id)
            if not row:
                raise LookupError("investigation not found")
            indicator_rows = repository.get_indicator_rows(investigation_id)
            history_rows = repository.get_history(investigation_id)
            indicator_row = indicator_rows[0] if indicator_rows else None
            rule_versions = json.loads(row["rule_versions_json"] or "[]")
            indicator_snapshot = json.loads(row["indicator_snapshot_json"] or "{}")
            origin_snapshot = json.loads(row["origin_snapshot_json"] or "{}")
            return {
                "investigation_id": row["investigation_id"],
                "loss_id": origin_snapshot.get("loss_id"),
                "calculation_id": row["calculation_id"],
                "primary_indicator_id": row["primary_indicator_id"],
                "operational_day": row["operational_day"],
                "company": row["company"],
                "production_order": row["production_order"],
                "resource_center": row["resource_center"],
                "line": row["line"],
                "product_code": row["product_code"],
                "product_description": row["product_description"],
                "title": row["title"],
                "description": row["description"],
                "origin": row["origin"],
                "stage": row["stage"],
                "status": row["status"],
                "confidence": row["confidence"],
                "owner": row["owner"],
                "due_date": row["due_date"],
                "versions": {
                    "engine_version": row["engine_version"],
                    "rules": rule_versions,
                },
                "indicator_id": indicator_row["indicator_id"] if indicator_row else row["primary_indicator_id"],
                "indicator_value": indicator_row["indicator_value"] if indicator_row else None,
                "unit": indicator_row["unit"] if indicator_row else None,
                "confidence_value": indicator_row["confidence"] if indicator_row else None,
                "engine_version": row["engine_version"],
                "rule_version": (rule_versions[0]["version"] if rule_versions and isinstance(rule_versions[0], dict) and rule_versions[0].get("version") else None),
                "rule_versions": rule_versions,
                "indicator_snapshot": indicator_snapshot,
                "origin_snapshot": origin_snapshot,
                "indicator": {
                    "indicator_id": indicator_row["indicator_id"] if indicator_row else row["primary_indicator_id"],
                    "value": indicator_row["indicator_value"] if indicator_row else None,
                    "unit": indicator_row["unit"] if indicator_row else None,
                    "confidence": indicator_row["confidence"] if indicator_row else None,
                    "rule_version": indicator_row["rule_version"] if indicator_row else None,
                    "snapshot": indicator_snapshot,
                },
                "history": [
                    {
                        "event_type": history_row["event_type"],
                        "payload": json.loads(history_row["payload_json"] or "{}"),
                        "created_at": history_row["created_at"],
                    }
                    for history_row in history_rows
                ],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        finally:
            investigation_conn.close()

