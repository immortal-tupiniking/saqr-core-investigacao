from __future__ import annotations

import json
import sqlite3
from typing import Any

from .database import get_investigation_connection


class InvestigationRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self.conn = conn or get_investigation_connection()

    def create_investigation(self, payload: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO investigations (
                investigation_id, calculation_id, primary_indicator_id, operational_day, company, production_order,
                resource_center, line, product_code, product_description, title, description, origin, stage, status,
                confidence, owner, due_date, engine_version, rule_versions_json, indicator_snapshot_json,
                origin_snapshot_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["investigation_id"],
                payload["calculation_id"],
                payload["primary_indicator_id"],
                payload.get("operational_day"),
                payload.get("company"),
                payload.get("production_order"),
                payload.get("resource_center"),
                payload.get("line"),
                payload.get("product_code"),
                payload.get("product_description"),
                payload["title"],
                payload.get("description"),
                payload.get("origin"),
                payload.get("stage"),
                payload["status"],
                payload.get("confidence"),
                payload.get("owner"),
                payload.get("due_date"),
                payload.get("engine_version"),
                payload.get("rule_versions_json"),
                payload.get("indicator_snapshot_json"),
                payload.get("origin_snapshot_json"),
                payload["created_at"],
                payload["updated_at"],
            ),
        )

    def create_indicator_snapshot(self, investigation_id: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO investigation_indicators (
                investigation_id, indicator_id, indicator_value, unit, confidence, rule_version,
                snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                investigation_id,
                payload["indicator_id"],
                payload.get("indicator_value"),
                payload.get("unit"),
                payload.get("confidence"),
                payload.get("rule_version"),
                json.dumps(payload.get("snapshot_json") or {}, ensure_ascii=False, sort_keys=True),
                payload["created_at"],
            ),
        )

    def create_history_event(self, investigation_id: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO investigation_history (investigation_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (investigation_id, payload["event_type"], json.dumps(payload.get("payload_json") or {}, ensure_ascii=False, sort_keys=True), payload["created_at"]),
        )

    def get_investigation(self, investigation_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM investigations WHERE investigation_id=?",
            (investigation_id,),
        ).fetchone()

    def get_indicator_rows(self, investigation_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM investigation_indicators WHERE investigation_id=? ORDER BY id",
            (investigation_id,),
        ))

    def get_history(self, investigation_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM investigation_history WHERE investigation_id=? ORDER BY id",
            (investigation_id,),
        ))

    def list_investigations(self, filters: dict[str, Any], page: int, page_size: int) -> tuple[list[sqlite3.Row], int]:
        clauses: list[str] = []
        params: list[Any] = []
        field_map = {
            "product": "product_code",
        }
        for field, value in filters.items():
            if value is None or value == "":
                continue
            if field in {"page", "page_size"}:
                continue
            column = field_map.get(field, field)
            clauses.append(f"{column}=?")
            params.append(value)
        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        total_count = self.conn.execute(f"SELECT COUNT(*) FROM investigations{where_clause}", params).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT * FROM investigations{where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return rows, int(total_count)

    def commit(self) -> None:
        self.conn.commit()
