from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _decode(value: Any) -> Any:
    if not value:
        return value
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _indicator_summary(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["metadata_json"] = _decode(item.get("metadata_json"))
    return item


def _dependency_tree(conn: sqlite3.Connection, indicator_id: int, visited: set[int] | None = None) -> dict[str, Any]:
    visited = set() if visited is None else set(visited)
    indicator = conn.execute("SELECT * FROM indicator_results WHERE id=?", (indicator_id,)).fetchone()
    if not indicator:
        return {"indicator_id": indicator_id, "missing": True}
    node = {"indicator": _indicator_summary(indicator), "dependencies": []}
    if indicator_id in visited:
        node["cycle_detected"] = True
        return node
    visited.add(indicator_id)
    for dep in conn.execute(
        """SELECT d.*,i.indicator_code,i.dimension_type,i.dimension_key,i.value,i.unit,i.confidence
        FROM indicator_dependencies d
        JOIN indicator_results i ON i.id=d.depends_on_indicator_id
        WHERE d.indicator_result_id=? ORDER BY d.id""",
        (indicator_id,),
    ):
        child = _dependency_tree(conn, dep["depends_on_indicator_id"], visited)
        node["dependencies"].append({
            "role": dep["role"],
            "weight": dep["weight"],
            "metadata": _decode(dep["metadata_json"]),
            "input": child,
        })
    return node


def _collect_indicator_ids(tree: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()

    def walk(node: dict[str, Any]) -> None:
        indicator = node.get("indicator") or {}
        iid = indicator.get("id")
        if iid is not None and iid not in seen:
            seen.add(iid)
            ids.append(int(iid))
        for dep in node.get("dependencies", []):
            walk(dep.get("input", {}))

    walk(tree)
    return ids


def explain_indicator(db_path: str | Path, indicator_id: int) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        indicator = conn.execute("SELECT * FROM indicator_results WHERE id=?", (indicator_id,)).fetchone()
        if not indicator:
            raise KeyError(f"Indicator result not found: {indicator_id}")
        rule = conn.execute(
            "SELECT * FROM rule_versions WHERE rule_code=? AND version=?",
            (indicator["rule_code"], indicator["rule_version"]),
        ).fetchone()
        catalog = conn.execute("SELECT * FROM indicator_catalog WHERE indicator_code=?", (indicator["indicator_code"],)).fetchone()
        calc = conn.execute("SELECT * FROM calculation_runs WHERE id=?", (indicator["calculation_run_id"],)).fetchone()
        parameters = {r["parameter_key"]: _decode(r["value_json"]) for r in conn.execute(
            "SELECT parameter_key,value_json FROM calculation_run_parameters WHERE calculation_run_id=? ORDER BY parameter_key",
            (indicator["calculation_run_id"],),
        )}

        tree = _dependency_tree(conn, indicator_id)
        dependency_indicator_ids = _collect_indicator_ids(tree)
        placeholders = ",".join("?" for _ in dependency_indicator_ids) or "NULL"

        lineage = []
        source_files: dict[str, dict[str, Any]] = {}
        if dependency_indicator_ids:
            query = f"""SELECT l.*,l.indicator_result_id source_indicator_id,
                ir.indicator_code source_indicator_code,ir.dimension_type source_dimension_type,
                ir.dimension_key source_dimension_key,
                s.source_row,s.record_hash,s.raw_json,i.filename,i.source_type,i.sha256,
                e.event_type,e.occurred_at,e.confidence event_confidence
                FROM indicator_lineage l
                JOIN indicator_results ir ON ir.id=l.indicator_result_id
                LEFT JOIN source_records s ON s.id=l.source_record_id
                LEFT JOIN import_runs i ON i.id=s.import_run_id
                LEFT JOIN events e ON e.id=l.event_id
                WHERE l.indicator_result_id IN ({placeholders}) ORDER BY l.indicator_result_id,l.id"""
            for row in conn.execute(query, dependency_indicator_ids):
                item = dict(row)
                item["raw_json"] = _decode(item.get("raw_json"))
                item["metadata_json"] = _decode(item.get("metadata_json"))
                lineage.append(item)
                if item.get("filename"):
                    source_files[item["filename"]] = {
                        "source_type": item.get("source_type"),
                        "sha256": item.get("sha256"),
                    }

        related_audit = [dict(r) for r in conn.execute(
            """SELECT * FROM audit_log WHERE calculation_run_id=?
            AND (entity_key=? OR entity_key=? OR entity_key=? OR entity_key=? OR entity_key=?)
            ORDER BY severity DESC,id""",
            (indicator["calculation_run_id"], indicator["dimension_key"],
             indicator["dimension_key"].split("|")[0], indicator["indicator_code"], indicator["rule_code"], ""),
        )]
        for item in related_audit:
            item["metadata_json"] = _decode(item.get("metadata_json"))

        domain_context: dict[str, Any] = {}
        op = indicator["dimension_key"].split("|")[0] if indicator["dimension_type"] in {"OP", "OP_COMPONENT"} else None
        if op:
            domain_context["production_order"] = dict(conn.execute(
                "SELECT * FROM production_orders WHERE op_number=? ORDER BY id LIMIT 1", (op,)
            ).fetchone() or {})
            domain_context["resource_executions"] = [dict(r) for r in conn.execute(
                "SELECT * FROM resource_executions WHERE op=? ORDER BY start_at,id", (op,)
            )]
            domain_context["mpop_order"] = dict(conn.execute(
                "SELECT * FROM mpop_orders WHERE op=? ORDER BY id LIMIT 1", (op,)
            ).fetchone() or {})

        result = {
            "question": "Como este valor foi calculado?",
            "indicator": _indicator_summary(indicator),
            "indicator_catalog": dict(catalog) if catalog else None,
            "formula_and_rule": dict(rule) if rule else None,
            "calculation_execution": dict(calc) if calc else None,
            "parameters_applied": parameters,
            "indicator_dependency_tree": tree,
            "source_files": source_files,
            "source_records_and_events": lineage,
            "related_audit": related_audit,
            "domain_context": domain_context,
            "confidence": indicator["confidence"],
            "lineage_summary": {
                "indicator_nodes": len(dependency_indicator_ids),
                "source_records": len(lineage),
                "source_files": len(source_files),
            },
        }
        if result["indicator_catalog"]:
            result["indicator_catalog"]["metadata_json"] = _decode(result["indicator_catalog"].get("metadata_json"))
        if result["formula_and_rule"]:
            result["formula_and_rule"]["config_json"] = _decode(result["formula_and_rule"].get("config_json"))
        if result["calculation_execution"]:
            result["calculation_execution"]["config_snapshot_json"] = _decode(result["calculation_execution"].get("config_snapshot_json"))
        return result
    finally:
        conn.close()
