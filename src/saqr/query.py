from __future__ import annotations

from collections import defaultdict
import json
import sqlite3
from typing import Any, Iterable

from .analytical import _combine_confidence
from .reporting import build_line_view, build_op_view


def _decode(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def confidence_bucket(value: str | None) -> str:
    text = (value or "").upper()
    if "CONFLITO" in text or "DIVERG" in text or "CRIT" in text:
        return "critical"
    if "PENDENTE" in text or "SEM_DADO" in text:
        return "pending"
    if "ESTIMADO" in text or "RATEADO" in text or "ATENCAO" in text or "ATENÇÃO" in text:
        return "estimated"
    return "confirmed"


def calculation_context(conn: sqlite3.Connection, calc_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM calculation_runs WHERE id=?", (calc_id,)).fetchone()
    if not row:
        raise KeyError(calc_id)
    cfg = _decode(row["config_snapshot_json"], {})
    strategy = cfg.get("parameters", {}).get("ui_strategy_key")
    if not strategy:
        if cfg.get("parameters", {}).get("consumption_strategy") == "operational_window":
            strategy = "operational_window"
        elif cfg.get("parameters", {}).get("accumulated_allocation_mode") == "production_ratio":
            strategy = "op_accumulated_prorated"
        else:
            strategy = "op_accumulated"
    return {"calculation": dict(row), "config": cfg, "strategy": strategy}


def _indicator(conn: sqlite3.Connection, calc_id: int, code: str, dimension: str, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT i.*,c.name,c.objective,c.formula_description,c.official_source,c.responsible
        FROM indicator_results i LEFT JOIN indicator_catalog c ON c.indicator_code=i.indicator_code
        WHERE i.calculation_run_id=? AND i.indicator_code=? AND i.dimension_type=? AND i.dimension_key=?
        ORDER BY i.id DESC LIMIT 1""",
        (calc_id, code, dimension, key),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["metadata"] = _decode(item.pop("metadata_json", None), {})
    item["confidence_bucket"] = confidence_bucket(item.get("confidence"))
    return item


def op_rows(conn: sqlite3.Connection, calc_id: int) -> list[dict[str, Any]]:
    context = calculation_context(conn, calc_id)
    company_map = context["config"].get("parameters", {}).get("company_by_origin", {})
    line_names = context["config"].get("parameters", {}).get("line_names", {})
    rows = build_op_view(conn, calc_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        op = row["op"]
        theo = _indicator(conn, calc_id, "THEORETICAL_CONSUMPTION_KG", "OP", op)
        variance = _indicator(conn, calc_id, "CONSUMPTION_VARIANCE_KG", "OP", op)
        ids = _decode(row.get("indicator_ids_json"), {})
        ids["theoretical"] = theo["id"] if theo else None
        ids["variance"] = variance["id"] if variance else None
        origin = str(row.get("origin_type") or "")
        line_key = row.get("line_key") or "CENTRO_NAO_ENCONTRADO"
        line_name = line_names.get(line_key, line_key.replace("_", " ").title())
        confidence = _combine_confidence([
            row.get("production_confidence"), row.get("consumption_active_confidence"),
            row.get("lixo_confidence"), row.get("repasse_confidence"),
            row.get("rendimento_confidence"), row.get("perda_padrao_confidence"),
        ])
        item = dict(row)
        item.update({
            "company": company_map.get(origin, f"Origem {origin}" if origin else "Empresa não identificada"),
            "line_name": line_name,
            "theoretical_consumption_kg": theo["value"] if theo else None,
            "theoretical_confidence": theo["confidence"] if theo else "PENDENTE",
            "consumption_variance_kg": variance["value"] if variance else None,
            "consumption_variance_confidence": variance["confidence"] if variance else "PENDENTE",
            "overall_confidence": confidence,
            "confidence_bucket": confidence_bucket(confidence),
            "indicator_ids": ids,
        })
        item.pop("indicator_ids_json", None)
        item.pop("indicator_metadata_json", None)
        item.pop("parameters_applied_json", None)
        out.append(item)
    return out


def line_rows(conn: sqlite3.Connection, calc_id: int) -> list[dict[str, Any]]:
    context = calculation_context(conn, calc_id)
    line_names = context["config"].get("parameters", {}).get("line_names", {})
    rows = build_line_view(conn, calc_id)
    out = []
    for row in rows:
        key = row["line_key"]
        actual = _indicator(conn, calc_id, "ACTUAL_LOSS_RATE", "LINE", key)
        standard = _indicator(conn, calc_id, "LOSS_STANDARD_PERCENT", "LINE", key)
        excess_pp = _indicator(conn, calc_id, "EXCESS_OVER_STANDARD_PP", "LINE", key)
        ids = _decode(row.get("indicator_ids_json"), {})
        ids.update({
            "actual_loss": actual["id"] if actual else None,
            "loss_standard": standard["id"] if standard else None,
            "excess_pp": excess_pp["id"] if excess_pp else None,
        })
        ops = [x for x in (row.get("ops") or "").split(",") if x]
        placeholders = ",".join("?" for _ in ops) or "NULL"
        pending_count = 0
        if ops:
            pending_count = conn.execute(
                f"""SELECT COUNT(*) FROM audit_log WHERE calculation_run_id=? AND entity_type='OP'
                AND entity_key IN ({placeholders}) AND severity IN ('WARNING','ERROR')""",
                [calc_id, *ops],
            ).fetchone()[0]
        confidence = _combine_confidence([row.get("consumption_confidence"), row.get("rendimento_confidence"), standard["confidence"] if standard else "PENDENTE"])
        item = dict(row)
        item.update({
            "center_resource": key,
            "line_name": line_names.get(key, key.replace("_", " ").title()),
            "actual_loss_percent": actual["value"] if actual else None,
            "loss_standard_percent": standard["value"] if standard else None,
            "excess_over_standard_pp": excess_pp["value"] if excess_pp else None,
            "pending_count": pending_count,
            "overall_confidence": confidence,
            "confidence_bucket": confidence_bucket(confidence),
            "indicator_ids": ids,
            "production_by_unit": _decode(row.get("production_by_unit_json"), {}),
        })
        for key_to_remove in ("production_by_unit_json", "indicator_ids_json", "indicator_metadata_json", "parameters_applied_json"):
            item.pop(key_to_remove, None)
        out.append(item)
    return out


def _filter_ops(rows: list[dict[str, Any]], filters: dict[str, str | None]) -> list[dict[str, Any]]:
    out = rows
    if filters.get("company"):
        out = [r for r in out if r.get("company") == filters["company"]]
    if filters.get("line"):
        out = [r for r in out if r.get("line_key") == filters["line"]]
    if filters.get("op"):
        out = [r for r in out if r.get("op") == filters["op"]]
    if filters.get("product"):
        out = [r for r in out if r.get("product_code") == filters["product"]]
    if filters.get("confidence"):
        out = [r for r in out if r.get("confidence_bucket") == filters["confidence"]]
    return out


def filter_options(conn: sqlite3.Connection, calc_id: int) -> dict[str, Any]:
    rows = op_rows(conn, calc_id)
    days = [r[0] for r in conn.execute("SELECT DISTINCT operational_day FROM calculation_runs WHERE status='SUCCESS' ORDER BY operational_day DESC")]
    return {
        "operational_days": days,
        "companies": sorted({r["company"] for r in rows}),
        "lines": sorted({(r["line_key"], r["line_name"]) for r in rows}),
        "ops": sorted({r["op"] for r in rows}),
        "products": sorted({(r["product_code"], r["product_description"]) for r in rows}),
        "confidence_levels": [
            {"value": "confirmed", "label": "Confirmado"},
            {"value": "estimated", "label": "Estimado / atenção"},
            {"value": "pending", "label": "Pendente / sem dado"},
            {"value": "critical", "label": "Crítico / divergente"},
        ],
    }


def _sum_nullable(values: Iterable[float | None]) -> float | None:
    values = list(values)
    if any(v is None for v in values):
        return None
    return sum(float(v) for v in values)


def executive_summary(conn: sqlite3.Connection, calc_id: int, filters: dict[str, str | None]) -> dict[str, Any]:
    all_rows = op_rows(conn, calc_id)
    rows = _filter_ops(all_rows, filters)
    production: dict[str, float] = defaultdict(float)
    for row in rows:
        production[row["production_unit"]] += float(row["production"] or 0)
    cons = _sum_nullable(r.get("consumption_active_kg") for r in rows)
    loss = _sum_nullable(r.get("lixo_mpop_kg") for r in rows)
    repasse = _sum_nullable(r.get("repasse_kg") for r in rows)
    mp = _sum_nullable(r.get("mp_aproveitada_kg") for r in rows)
    yield_value = None if cons in (None, 0) or mp is None else mp / cons
    actual_loss = None if yield_value is None else 1.0 - yield_value
    weighted_pairs = [(r.get("perda_padrao_percent"), r.get("consumption_active_kg")) for r in rows]
    weight = sum(float(w or 0) for s, w in weighted_pairs if s is not None and w is not None)
    standard = None if weight <= 0 or any(s is None or w is None for s, w in weighted_pairs) else sum(float(s) * float(w) for s, w in weighted_pairs) / weight
    excess_pp = None if actual_loss is None or standard is None else max(0.0, actual_loss - standard)
    excess_kg = _sum_nullable(r.get("excesso_sobre_padrao_kg") for r in rows)
    confidence = _combine_confidence([r.get("overall_confidence") for r in rows]) if rows else "PENDENTE_SEM_DADOS"

    def source_ids(key: str) -> list[int]:
        return [int(r["indicator_ids"][key]) for r in rows if r.get("indicator_ids", {}).get(key)]

    metrics = [
        {"code": "OPS_ANALYZED", "name": "OPs analisadas", "value": len(rows), "unit": "OP", "confidence": confidence, "source_indicator_ids": source_ids("production")},
        {"code": "LINES_ACTIVE", "name": "Linhas em operação", "value": len({r["line_key"] for r in rows}), "unit": "LINHA", "confidence": confidence, "source_indicator_ids": source_ids("production")},
        {"code": "PRODUCTION_BY_UNIT", "name": "Produção por unidade", "value_by_unit": dict(sorted(production.items())), "unit": "MULTI", "confidence": _combine_confidence([r.get("production_confidence") for r in rows]), "source_indicator_ids": source_ids("production")},
        {"code": "RAW_MATERIAL_CONSUMPTION_KG", "name": "Consumo real de MP", "value": cons, "unit": "KG", "confidence": _combine_confidence([r.get("consumption_active_confidence") for r in rows]), "source_indicator_ids": source_ids("consumption_active")},
        {"code": "MPOP_LOSS_KG", "name": "Lixo", "value": loss, "unit": "KG", "confidence": _combine_confidence([r.get("lixo_confidence") for r in rows]), "source_indicator_ids": source_ids("lixo")},
        {"code": "REPASSE_KG", "name": "Repasse", "value": repasse, "unit": "KG", "confidence": _combine_confidence([r.get("repasse_confidence") for r in rows]), "source_indicator_ids": source_ids("repasse")},
        {"code": "MP_UTILIZED_KG", "name": "MP aproveitada", "value": mp, "unit": "KG", "confidence": confidence, "source_indicator_ids": source_ids("mp_aproveitada")},
        {"code": "OPERATIONAL_YIELD", "name": "Rendimento operacional", "value": yield_value, "unit": "%", "confidence": confidence, "source_indicator_ids": source_ids("rendimento")},
        {"code": "ACTUAL_LOSS_RATE", "name": "Perda real", "value": actual_loss, "unit": "%", "confidence": confidence, "source_indicator_ids": source_ids("rendimento")},
        {"code": "LOSS_STANDARD_PERCENT", "name": "Perda padrão", "value": standard, "unit": "%", "confidence": _combine_confidence([r.get("perda_padrao_confidence") for r in rows]), "source_indicator_ids": source_ids("perda_padrao")},
        {"code": "EXCESS_OVER_STANDARD_PP", "name": "Excesso sobre o padrão", "value": excess_pp, "unit": "PP", "confidence": confidence, "source_indicator_ids": source_ids("excesso_kg")},
        {"code": "EXCESS_OVER_STANDARD_KG", "name": "Excesso sobre o padrão", "value": excess_kg, "unit": "KG", "confidence": confidence, "source_indicator_ids": source_ids("excesso_kg")},
    ]
    for metric in metrics:
        metric["confidence_bucket"] = confidence_bucket(metric["confidence"])
    return {
        "metrics": metrics,
        "selected_ops": [r["op"] for r in rows],
        "filters": filters,
        "row_count": len(rows),
        "calculation_context": calculation_context(conn, calc_id),
    }


def filtered_op_rows(conn: sqlite3.Connection, calc_id: int, filters: dict[str, str | None]) -> list[dict[str, Any]]:
    return _filter_ops(op_rows(conn, calc_id), filters)


def filtered_line_rows(conn: sqlite3.Connection, calc_id: int, filters: dict[str, str | None]) -> list[dict[str, Any]]:
    op_set = {r["op"] for r in _filter_ops(op_rows(conn, calc_id), filters)}
    rows = line_rows(conn, calc_id)
    if not any(filters.values()):
        return rows
    out = []
    for row in rows:
        row_ops = set((row.get("ops") or "").split(","))
        if row_ops & op_set:
            out.append(row)
    return out


def op_components(conn: sqlite3.Connection, calc_id: int, op: str) -> dict[str, Any]:
    rows = [dict(r) for r in conn.execute(
        """SELECT * FROM component_analytical_results WHERE calculation_run_id=? AND op=?
        ORDER BY CASE section WHEN 'Produção' THEN 1 WHEN 'Matéria-prima' THEN 2 WHEN 'Salmoura' THEN 3
        WHEN 'Aditivos' THEN 4 WHEN 'Azeites e óleos' THEN 5 WHEN 'Embalagens' THEN 6
        WHEN 'Lixo' THEN 7 WHEN 'Repasse' THEN 8 ELSE 9 END,occurred_at,id""",
        (calc_id, op),
    )]
    for row in rows:
        row["metadata"] = _decode(row.pop("metadata_json", None), {})
        row["confidence_bucket"] = confidence_bucket(row.get("confidence"))
    required_sections = [
        "Produção", "Matéria-prima", "Salmoura", "Aditivos",
        "Azeites e óleos", "Embalagens", "Lixo", "Repasse",
    ]
    grouped: dict[str, list[dict[str, Any]]] = {section: [] for section in required_sections}
    for row in rows:
        grouped.setdefault(row["section"], []).append(row)
    return {"op": op, "sections": grouped, "rows": rows}


def audit_overview(conn: sqlite3.Connection, calc_id: int) -> dict[str, Any]:
    context = calculation_context(conn, calc_id)
    cfg = context["config"]
    start_at = cfg.get("operational_day", {}).get("start")
    end_at = cfg.get("operational_day", {}).get("end")
    selected_ops = [r[0] for r in conn.execute(
        """SELECT dimension_key FROM indicator_results WHERE calculation_run_id=?
        AND indicator_code='PRODUCTION_NET' AND dimension_type='OP' ORDER BY dimension_key""", (calc_id,)
    )]

    result: dict[str, Any] = {}

    # Conflicts only: equivalent duplicate rows are not treated as conflicting.
    conflicting = []
    for row in conn.execute("SELECT * FROM validation_issues WHERE code='DUPLICATE_COMPONENT_CLASSIFICATION' ORDER BY id"):
        item = dict(row)
        ctx = _decode(item.pop("context_json", None), {})
        definitions = ctx.get("definitions", [])
        if len(definitions) > 1:
            item["context"] = ctx
            conflicting.append(item)
    result["componentes_conflitantes"] = {"count": len(conflicting), "rows": conflicting}

    def audit_rows(codes: list[str], severities: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in codes)
        args: list[Any] = [calc_id, *codes]
        sql = f"SELECT * FROM audit_log WHERE calculation_run_id=? AND audit_code IN ({placeholders})"
        if severities:
            sql += f" AND severity IN ({','.join('?' for _ in severities)})"
            args.extend(severities)
        sql += " ORDER BY severity DESC,id"
        rows = [dict(r) for r in conn.execute(sql, args)]
        for row in rows:
            row["metadata"] = _decode(row.pop("metadata_json", None), {})
        return rows

    def validation_rows(codes: list[str]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in codes)
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM validation_issues WHERE code IN ({placeholders}) ORDER BY severity DESC,id", codes
        )]
        for row in rows:
            row["context"] = _decode(row.pop("context_json", None), {})
        return rows

    mapping = {
        "ops_sem_centro": audit_rows(["CENTER_RESOURCE_NOT_FOUND"]),
        "ops_multicentro": audit_rows(["MULTI_CENTER_OP"]),
        "perdas_sem_classificacao": validation_rows(["MPOP_LOSS_CLASSIFICATION_PENDING"]),
        "divergencias_referencia": audit_rows([
            "CENTER_REFERENCE_DIVERGENCE", "REPASSE_REFERENCE_COMPARISON", "STANDARD_REFERENCE_COMPARISON",
            "YIELD_PRORATED_REFERENCE_COMPARISON", "CONSUMPTION_PRORATED_REFERENCE_COMPARISON",
            "LOSS_REFERENCE_COMPARISON", "PRODUCTION_REFERENCE_COMPARISON",
        ], ("WARNING", "ERROR")),
        "parametros_provisorios": audit_rows(["PROVISIONAL_DENSITY_PARAMETER"]),
        "itens_sem_peso": audit_rows(["THEORETICAL_WEIGHT_NOT_IDENTIFIED", "REPASSE_CAPACITY_NOT_FOUND", "CONSUMPTION_CONVERSION_PENDING"]),
        "possiveis_duplas_deducoes": audit_rows(["MP_UTILIZED_REPASSE_DOUBLE_DEDUCTION_RISK"]),
    }
    for key, rows in mapping.items():
        result[key] = {"count": len(rows), "rows": rows}

    # Movements outside the window are derived from official movement timestamps,
    # not from the reference report.
    outside: list[dict[str, Any]] = []
    if selected_ops and start_at and end_at:
        placeholders = ",".join("?" for _ in selected_ops)
        query = f"""SELECT m.document_op entity_key,m.transaction_code,m.product_code,m.product_description,
            m.quantity,m.unit,m.recorded_at,s.source_row,s.record_hash,i.filename,i.sha256
            FROM movements m JOIN source_records s ON s.id=m.source_record_id
            JOIN import_runs i ON i.id=s.import_run_id
            WHERE m.document_op IN ({placeholders}) AND m.transaction_code IN ('90251','90202')
              AND (m.recorded_at<? OR m.recorded_at>?) ORDER BY m.document_op,m.recorded_at,m.id"""
        for r in conn.execute(query, [*selected_ops, start_at, end_at]):
            outside.append({
                "severity": "INFO", "code": "MOVEMENT_OUTSIDE_OPERATIONAL_WINDOW", "entity": "MOVEMENT",
                "entity_key": r["entity_key"],
                "message": f"Movimento {r['transaction_code']} registrado fora da janela operacional",
                "official_value": f"{r['quantity']} {r['unit']}", "reference_value": None, "difference": None,
                "context": {"product_code": r["product_code"], "description": r["product_description"],
                            "recorded_at": r["recorded_at"], "source_file": r["filename"],
                            "source_row": r["source_row"], "record_hash": r["record_hash"], "sha256": r["sha256"]},
            })
    result["movimentos_fora_janela"] = {"count": len(outside), "rows": outside}
    result["all_audit"] = [dict(r) for r in conn.execute(
        "SELECT * FROM audit_log WHERE calculation_run_id=? ORDER BY severity DESC,id", (calc_id,)
    )]
    return result


def import_status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    expected = [
        ("movements", "Movimentações"), ("resources", "Centro de Recurso"), ("mpop", "MPOP301"),
        ("loss_standards", "Perdas padrão"), ("categories", "Categorias"), ("reference", "Relatório de referência"),
    ]
    latest = {r["source_type"]: dict(r) for r in conn.execute(
        "SELECT * FROM import_runs WHERE id IN (SELECT MAX(id) FROM import_runs GROUP BY source_type)"
    )}
    result = []
    for source_type, label in expected:
        row = latest.get(source_type)
        if not row:
            result.append({"source_type": source_type, "label": label, "status": "NOT_IMPORTED", "optional": source_type == "reference", "rows_read": 0, "validation_errors": 0, "warnings": 0})
            continue
        warnings = conn.execute(
            "SELECT COUNT(*) FROM validation_issues WHERE import_run_id=? AND severity='WARNING'", (row["id"],)
        ).fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM validation_issues WHERE import_run_id=? AND severity='ERROR'", (row["id"],)
        ).fetchone()[0]
        row.update({"label": label, "optional": source_type == "reference", "warnings": warnings, "errors": errors})
        row["metadata"] = _decode(row.pop("metadata_json", None), {})
        result.append(row)
    return result
