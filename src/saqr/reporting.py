from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import csv
import json
import sqlite3
from typing import Any

from .explain import explain_indicator


def _json_load(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)




def _calc_context(conn: sqlite3.Connection, calc_id: int) -> dict[str, Any]:
    calc = conn.execute("SELECT engine_version,operational_day,started_at,completed_at FROM calculation_runs WHERE id=?", (calc_id,)).fetchone()
    parameters = {r["parameter_key"]: _json_load(r["value_json"]) for r in conn.execute(
        "SELECT parameter_key,value_json FROM calculation_run_parameters WHERE calculation_run_id=? ORDER BY parameter_key", (calc_id,)
    )}
    return {
        "engine_version": calc["engine_version"] if calc else None,
        "operational_day": calc["operational_day"] if calc else None,
        "parameters": parameters,
    }


def _indicator_descriptor(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "indicator_code": row["indicator_code"],
        "value": row["value"],
        "unit": row["unit"],
        "confidence": row["confidence"],
        "rule_code": row["rule_code"],
        "rule_version": row["rule_version"],
    }

def _indicator_map(conn: sqlite3.Connection, calc_id: int, dimension_type: str = "OP") -> dict[tuple[str, str], sqlite3.Row]:
    return {(r["indicator_code"], r["dimension_key"]): r for r in conn.execute(
        "SELECT * FROM indicator_results WHERE calculation_run_id=? AND dimension_type=?", (calc_id, dimension_type)
    )}


def build_op_view(conn: sqlite3.Connection, calc_id: int) -> list[dict[str, Any]]:
    indicators = _indicator_map(conn, calc_id, "OP")
    context = _calc_context(conn, calc_id)
    production_ops = sorted(k[1] for k in indicators if k[0] == "PRODUCTION_NET")
    rows: list[dict[str, Any]] = []
    for op in production_ops:
        prod = indicators[("PRODUCTION_NET", op)]
        prod_meta = _json_load(prod["metadata_json"])
        product_code = prod_meta.get("product_code")
        product_description = prod_meta.get("product_description")
        reference = conn.execute("SELECT * FROM reference_op_results WHERE op=?", (op,)).fetchone()

        def i(code: str) -> sqlite3.Row | None:
            return indicators.get((code, op))

        window = i("RAW_MATERIAL_CONSUMPTION_KG_OPERATIONAL_WINDOW")
        accumulated = i("RAW_MATERIAL_CONSUMPTION_KG_OP_ACCUMULATED")
        prorated = i("RAW_MATERIAL_CONSUMPTION_KG_PRORATED")
        active = i("RAW_MATERIAL_CONSUMPTION_KG")
        loss = i("MPOP_LOSS_KG")
        repasse = i("REPASSE_KG")
        mp = i("MP_UTILIZED_KG")
        yield_row = i("OPERATIONAL_YIELD")
        loss_rate = i("ACTUAL_LOSS_RATE")
        standard = i("LOSS_STANDARD_PERCENT")
        excess_pp = i("EXCESS_OVER_STANDARD_PP")
        excess_kg = i("EXCESS_OVER_STANDARD_KG")
        row = {
            "calculation_run_id": calc_id,
            "op": op,
            "origin_type": prod_meta.get("origin"),
            "product_code": product_code,
            "product_description": product_description,
            "center_resource": ",".join(prod_meta.get("centers", [])),
            "line_key": prod_meta.get("line_key"),
            "production": prod["value"],
            "production_unit": prod["unit"],
            "production_confidence": prod["confidence"],
            "consumption_window_kg": window["value"] if window else None,
            "consumption_window_confidence": window["confidence"] if window else "PENDENTE",
            "consumption_accumulated_kg": accumulated["value"] if accumulated else None,
            "consumption_accumulated_confidence": accumulated["confidence"] if accumulated else "PENDENTE",
            "consumption_prorated_kg": prorated["value"] if prorated else None,
            "consumption_prorated_confidence": prorated["confidence"] if prorated else "PENDENTE",
            "consumption_active_kg": active["value"] if active else None,
            "consumption_active_confidence": active["confidence"] if active else "PENDENTE",
            "lixo_mpop_kg": loss["value"] if loss else None,
            "lixo_confidence": loss["confidence"] if loss else "PENDENTE",
            "repasse_kg": repasse["value"] if repasse else None,
            "repasse_confidence": repasse["confidence"] if repasse else "PENDENTE",
            "mp_aproveitada_kg": mp["value"] if mp else None,
            "mp_aproveitada_confidence": mp["confidence"] if mp else "PENDENTE",
            "rendimento_operacional": yield_row["value"] if yield_row else None,
            "rendimento_confidence": yield_row["confidence"] if yield_row else "PENDENTE",
            "perda_real_percent": loss_rate["value"] if loss_rate else None,
            "perda_padrao_percent": standard["value"] if standard else None,
            "perda_padrao_confidence": standard["confidence"] if standard else "PENDENTE",
            "excesso_sobre_padrao_pp": excess_pp["value"] if excess_pp else None,
            "excesso_sobre_padrao_kg": excess_kg["value"] if excess_kg else None,
            "excesso_confidence": excess_kg["confidence"] if excess_kg else "PENDENTE",
            "reference_production": reference["production"] if reference else None,
            "reference_real_kg": reference["real_kg"] if reference else None,
            "reference_lixo_kg": reference["loss_kg"] if reference else None,
            "reference_repasse_kg": reference["repasse_kg"] if reference else None,
            "reference_rendimento": reference["yield_operational"] if reference else None,
            "engine_version": context["engine_version"],
            "parameters_applied_json": json.dumps(context["parameters"], ensure_ascii=False, sort_keys=True),
            "indicator_metadata_json": json.dumps({
                "production": _indicator_descriptor(prod),
                "consumption_window": _indicator_descriptor(window),
                "consumption_accumulated": _indicator_descriptor(accumulated),
                "consumption_prorated": _indicator_descriptor(prorated),
                "consumption_active": _indicator_descriptor(active),
                "lixo": _indicator_descriptor(loss),
                "repasse": _indicator_descriptor(repasse),
                "mp_aproveitada": _indicator_descriptor(mp),
                "rendimento": _indicator_descriptor(yield_row),
                "perda_real": _indicator_descriptor(loss_rate),
                "perda_padrao": _indicator_descriptor(standard),
                "excesso_pp": _indicator_descriptor(excess_pp),
                "excesso_kg": _indicator_descriptor(excess_kg),
            }, ensure_ascii=False, sort_keys=True),
            "indicator_ids_json": json.dumps({
                "production": prod["id"],
                "consumption_window": window["id"] if window else None,
                "consumption_accumulated": accumulated["id"] if accumulated else None,
                "consumption_prorated": prorated["id"] if prorated else None,
                "consumption_active": active["id"] if active else None,
                "lixo": loss["id"] if loss else None,
                "repasse": repasse["id"] if repasse else None,
                "mp_aproveitada": mp["id"] if mp else None,
                "rendimento": yield_row["id"] if yield_row else None,
                "perda_padrao": standard["id"] if standard else None,
                "excesso_kg": excess_kg["id"] if excess_kg else None,
            }, ensure_ascii=False, sort_keys=True),
        }
        rows.append(row)
    return rows


def build_line_view(conn: sqlite3.Connection, calc_id: int) -> list[dict[str, Any]]:
    line_indicators = _indicator_map(conn, calc_id, "LINE")
    context = _calc_context(conn, calc_id)
    production_by_line: dict[str, dict[str, float]] = defaultdict(dict)
    for r in conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND dimension_type='LINE_UNIT' AND indicator_code='PRODUCTION_NET'", (calc_id,)):
        line_key = r["dimension_key"].rsplit("|", 1)[0]
        production_by_line[line_key][r["unit"]] = r["value"]
    line_keys = sorted({k[1] for k in line_indicators} | set(production_by_line))
    op_view = build_op_view(conn, calc_id)
    ops_by_line: dict[str, list[str]] = defaultdict(list)
    for row in op_view:
        ops_by_line[row["line_key"]].append(row["op"])
    rows = []
    for line in line_keys:
        def i(code: str) -> sqlite3.Row | None:
            return line_indicators.get((code, line))
        cons = i("RAW_MATERIAL_CONSUMPTION_KG")
        loss = i("MPOP_LOSS_KG")
        rep = i("REPASSE_KG")
        mp = i("MP_UTILIZED_KG")
        y = i("OPERATIONAL_YIELD")
        ex = i("EXCESS_OVER_STANDARD_KG")
        rows.append({
            "calculation_run_id": calc_id,
            "line_key": line,
            "ops": ",".join(sorted(ops_by_line.get(line, []))),
            "op_count": len(ops_by_line.get(line, [])),
            "production_by_unit_json": json.dumps(production_by_line.get(line, {}), ensure_ascii=False, sort_keys=True),
            "consumption_active_kg": cons["value"] if cons else None,
            "consumption_confidence": cons["confidence"] if cons else "PENDENTE",
            "lixo_mpop_kg": loss["value"] if loss else None,
            "repasse_kg": rep["value"] if rep else None,
            "mp_aproveitada_kg": mp["value"] if mp else None,
            "rendimento_operacional": y["value"] if y else None,
            "rendimento_confidence": y["confidence"] if y else "PENDENTE",
            "excesso_sobre_padrao_kg": ex["value"] if ex else None,
            "engine_version": context["engine_version"],
            "parameters_applied_json": json.dumps(context["parameters"], ensure_ascii=False, sort_keys=True),
            "indicator_metadata_json": json.dumps({
                "consumption": _indicator_descriptor(cons),
                "lixo": _indicator_descriptor(loss),
                "repasse": _indicator_descriptor(rep),
                "mp_aproveitada": _indicator_descriptor(mp),
                "rendimento": _indicator_descriptor(y),
                "excesso": _indicator_descriptor(ex),
            }, ensure_ascii=False, sort_keys=True),
            "indicator_ids_json": json.dumps({
                "consumption": cons["id"] if cons else None,
                "lixo": loss["id"] if loss else None,
                "repasse": rep["id"] if rep else None,
                "mp_aproveitada": mp["id"] if mp else None,
                "rendimento": y["id"] if y else None,
                "excesso": ex["id"] if ex else None,
            }, ensure_ascii=False, sort_keys=True),
        })
    return rows


def build_executive_summary(conn: sqlite3.Connection, calc_id: int) -> list[dict[str, Any]]:
    rows = []
    for r in conn.execute(
        """SELECT i.*,c.name,c.objective,c.formula_description,c.frequency,c.official_source,c.responsible
        FROM indicator_results i LEFT JOIN indicator_catalog c ON c.indicator_code=i.indicator_code
        WHERE i.calculation_run_id=? AND i.dimension_type IN ('DAY','DAY_UNIT')
        ORDER BY i.indicator_code,i.dimension_key""", (calc_id,)
    ):
        item = dict(r)
        item["metadata_json"] = r["metadata_json"]
        rows.append(item)
    return rows


def build_mpop_loss_view(conn: sqlite3.Connection, calc_id: int) -> list[dict[str, Any]]:
    selected_ops = {r[0] for r in conn.execute("SELECT dimension_key FROM indicator_results WHERE calculation_run_id=? AND indicator_code='PRODUCTION_NET' AND dimension_type='OP'", (calc_id,))}
    rows = []
    for r in conn.execute(
        """SELECT m.*,s.source_row,s.record_hash,i.filename,i.sha256
        FROM mpop_components m JOIN source_records s ON s.id=m.source_record_id
        JOIN import_runs i ON i.id=s.import_run_id
        WHERE m.qty_loss>0 ORDER BY m.op,m.id"""
    ):
        included = r["op"] in selected_ops and r["classification_status"] == "CONFIRMADO" and (r["classification_group_1"] or "").casefold() == "azeitonas".casefold()
        rows.append({
            "op": r["op"],
            "output_product_code": r["output_product_code"],
            "component_code": r["component_code"],
            "component_description": r["component_description"],
            "qty_loss_original": r["qty_loss"],
            "capacity_kg": r["capacity_kg"],
            "loss_kg": r["loss_kg"],
            "classification_group_1": r["classification_group_1"],
            "classification_status": r["classification_status"],
            "included_in_lixo_indicator": "SIM" if included else "NAO",
            "confidence": "CONFIRMADO_SEM_HORARIO" if included and r["loss_kg"] is not None else "PENDENTE",
            "source_file": r["filename"],
            "source_row": r["source_row"],
            "source_sha256": r["sha256"],
            "record_hash": r["record_hash"],
        })
    return rows


def build_repasse_view(conn: sqlite3.Connection, calc_id: int) -> list[dict[str, Any]]:
    calc = conn.execute("SELECT config_snapshot_json FROM calculation_runs WHERE id=?", (calc_id,)).fetchone()
    cfg = json.loads(calc[0])
    start = cfg["operational_day"]["start"]
    end = cfg["operational_day"]["end"]
    keyword = cfg["parameters"]["repasse_keyword"].upper()
    selected_ops = {r[0] for r in conn.execute("SELECT dimension_key FROM indicator_results WHERE calculation_run_id=? AND indicator_code='PRODUCTION_NET' AND dimension_type='OP'", (calc_id,))}
    rows = []
    for r in conn.execute(
        """SELECT m.*,s.source_row,s.record_hash,i.filename,i.sha256
        FROM movements m JOIN source_records s ON s.id=m.source_record_id
        JOIN import_runs i ON i.id=s.import_run_id
        WHERE m.transaction_code='90202' AND UPPER(m.product_description) LIKE ?
          AND m.recorded_at>=? AND m.recorded_at<=? ORDER BY m.document_op,m.recorded_at,m.id""",
        (f"%{keyword}%", start, end),
    ):
        import re
        matches = re.findall(r"(?<![A-Z0-9])([0-9]+(?:[\.,][0-9]+)?)\s*KGS?\b", (r["product_description"] or "").upper())
        capacity = float(matches[-1].replace(",", ".")) if matches else None
        rows.append({
            "op": r["document_op"],
            "component_code": r["product_code"],
            "description": r["product_description"],
            "recorded_at": r["recorded_at"],
            "quantity_original": r["quantity"],
            "unit_original": r["unit"],
            "capacity_kg": capacity,
            "repasse_kg": r["quantity"] * capacity if capacity is not None else None,
            "included_in_indicator": "SIM" if r["document_op"] in selected_ops and capacity is not None else "NAO",
            "confidence": "CONFIRMADO" if capacity is not None else "PENDENTE_CONVERSAO",
            "source_file": r["filename"],
            "source_row": r["source_row"],
            "source_sha256": r["sha256"],
            "record_hash": r["record_hash"],
        })
    return rows


def export_analytical_outputs(conn: sqlite3.Connection, output_dir: Path, calc_id: int, db_path: Path | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generic_exports = {
        "indicators.csv": "SELECT * FROM indicator_results WHERE calculation_run_id=? ORDER BY indicator_code,dimension_type,dimension_key,id",
        "indicator_catalog.csv": "SELECT * FROM indicator_catalog ORDER BY indicator_code",
        "indicator_lineage.csv": "SELECT * FROM indicator_lineage ORDER BY indicator_result_id,id",
        "indicator_dependencies.csv": "SELECT * FROM indicator_dependencies ORDER BY indicator_result_id,id",
        "rule_versions.csv": "SELECT * FROM rule_versions ORDER BY rule_code,version",
        "calculation_parameters.csv": "SELECT * FROM calculation_run_parameters WHERE calculation_run_id=? ORDER BY parameter_key",
        "audit_log.csv": "SELECT * FROM audit_log WHERE calculation_run_id=? ORDER BY severity DESC,audit_code,entity_key,id",
        "events.csv": "SELECT * FROM events ORDER BY id",
        "validation_issues.csv": "SELECT * FROM validation_issues ORDER BY severity DESC,import_run_id,id",
        "import_runs.csv": "SELECT * FROM import_runs ORDER BY id",
        "domain_operational_days.csv": "SELECT * FROM operational_days ORDER BY operational_date",
        "domain_production_orders.csv": "SELECT * FROM production_orders ORDER BY origin_type,op_number",
        "domain_products.csv": "SELECT * FROM products ORDER BY product_code",
        "domain_components.csv": "SELECT * FROM components ORDER BY component_code",
        "domain_resource_centers.csv": "SELECT * FROM resource_centers ORDER BY center_code",
        "domain_lines.csv": "SELECT * FROM lines ORDER BY line_code",
        "domain_loss_standards.csv": "SELECT * FROM loss_standards ORDER BY standard_code",
    }
    for filename, sql in generic_exports.items():
        query_args = (calc_id,) if "?" in sql else ()
        _write_csv(output_dir / filename, [dict(r) for r in conn.execute(sql, query_args)])

    op_view = build_op_view(conn, calc_id)
    line_view = build_line_view(conn, calc_id)
    executive = build_executive_summary(conn, calc_id)
    losses = build_mpop_loss_view(conn, calc_id)
    repasse = build_repasse_view(conn, calc_id)
    _write_csv(output_dir / "view_consumption_by_op.csv", op_view)
    _write_csv(output_dir / "view_consumption_by_line.csv", line_view)
    _write_csv(output_dir / "view_executive_summary.csv", executive)
    _write_csv(output_dir / "view_mpop_losses.csv", losses)
    _write_csv(output_dir / "view_repasse.csv", repasse)

    explanation_index = []
    for r in conn.execute(
        "SELECT id,indicator_code,dimension_type,dimension_key,value,unit,confidence,rule_code,rule_version FROM indicator_results WHERE calculation_run_id=? ORDER BY id",
        (calc_id,),
    ):
        explanation_index.append({**dict(r), "explain_command": f"PYTHONPATH=src python explain_indicator.py --database output/saqr.sqlite --indicator-id {r['id']}"})
    _write_csv(output_dir / "explanation_index.csv", explanation_index)

    counts = {}
    for table in ["operational_days", "production_orders", "events", "resource_executions", "lines", "resource_centers",
                  "products", "components", "families", "classifications", "movements", "loss_standards",
                  "indicator_results", "indicator_lineage", "indicator_dependencies", "audit_log"]:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    summary = {
        "release": "SAQR Motor Analitico v0.2.0",
        "calculation_run_id": calc_id,
        "domain_counts": counts,
        "views": {
            "executive_summary_rows": len(executive),
            "consumption_by_line_rows": len(line_view),
            "consumption_by_op_rows": len(op_view),
            "mpop_loss_rows": len(losses),
            "repasse_rows": len(repasse),
            "audit_rows": conn.execute("SELECT COUNT(*) FROM audit_log WHERE calculation_run_id=?", (calc_id,)).fetchone()[0],
        },
        "executive_summary": executive,
        "confidence_counts": {r["confidence"]: r["qty"] for r in conn.execute(
            "SELECT confidence,COUNT(*) qty FROM indicator_results WHERE calculation_run_id=? GROUP BY confidence ORDER BY confidence", (calc_id,)
        )},
        "audit_counts": {r["severity"]: r["qty"] for r in conn.execute(
            "SELECT severity,COUNT(*) qty FROM audit_log WHERE calculation_run_id=? GROUP BY severity ORDER BY severity", (calc_id,)
        )},
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Pre-generate representative explanations. Every result remains available
    # through the same function/CLI.
    if db_path:
        example_ids = []
        for code, dim in (("OPERATIONAL_YIELD", "DAY"), ("RAW_MATERIAL_CONSUMPTION_KG", "OP"), ("MPOP_LOSS_KG", "OP")):
            row = conn.execute("SELECT id FROM indicator_results WHERE calculation_run_id=? AND indicator_code=? AND dimension_type=? ORDER BY id LIMIT 1",
                               (calc_id, code, dim)).fetchone()
            if row:
                example_ids.append(row[0])
        for iid in example_ids:
            (output_dir / f"explain_indicator_{iid}.json").write_text(
                json.dumps(explain_indicator(db_path, iid), ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return summary
