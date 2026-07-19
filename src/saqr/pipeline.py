from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import csv
import json
import sqlite3

from .config import EngineConfig
from .database import connect
from .domain import materialize_domain
from .analytical import calculate_analytical
from .reporting import export_analytical_outputs
from .mpop_parser import parse_mpop_report
from .utils import (
    as_float,
    as_text_code,
    excel_datetime,
    extract_capacity_kg,
    file_sha256,
    iso,
    normalize_header,
    normalize_text,
    record_hash,
)
from .xlsx_reader import XlsxWorkbook

UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")

EXPECTED = {
    "movements": {
        "sheet": "Plan1",
        "required": {
            "produto", "descricao_produto", "data", "data_digitacao", "hora_digitacao",
            "trans", "qtde_mov", "documento", "e_s", "origem", "u_m", "origem_o_p"
        },
    },
    "resources": {
        "sheet": "Plan1",
        "required": {
            "o_p_o_s", "produto_servico", "data_inicio", "h_inicio", "data_fim", "h_fim",
            "c_recurso", "oper", "seq", "qtde_1a_qual"
        },
    },
    "categories": {
        "sheet": "Planilha1",
        "required": {"familia", "agrup_i", "agrup_ii", "agrup_iii", "componente", "descricao"},
    },
    "loss_standards": {"sheet": "Planilha1", "required": set()},
    "reference": {"sheet": "Por OP", "required": set()},
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _insert_import_run(conn: sqlite3.Connection, source_type: str, path: Path) -> int:
    cur = conn.execute(
        "INSERT INTO import_runs(source_type,filename,sha256,imported_at,status,metadata_json) VALUES(?,?,?,?,?,?)",
        (source_type, path.name, file_sha256(path), UTC_NOW(), "RUNNING", _json({"path": str(path)})),
    )
    return int(cur.lastrowid)


def _finish_import(conn: sqlite3.Connection, run_id: int, rows: int, errors: int = 0) -> None:
    conn.execute(
        "UPDATE import_runs SET status=?,rows_read=?,validation_errors=? WHERE id=?",
        ("SUCCESS" if errors == 0 else "SUCCESS_WITH_ISSUES", rows, errors, run_id),
    )


def _issue(conn: sqlite3.Connection, run_id: int | None, severity: str, code: str, entity: str, message: str, context: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO validation_issues(import_run_id,severity,code,entity,message,context_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (run_id, severity, code, entity, message, _json(context or {}), UTC_NOW()),
    )


def _source_record(conn: sqlite3.Connection, run_id: int, source_row: str, payload: dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO source_records(import_run_id,source_row,record_hash,raw_json) VALUES(?,?,?,?)",
        (run_id, source_row, record_hash(payload), _json(payload)),
    )
    return int(cur.lastrowid)


def _row_dict(header: list[Any], row: list[Any]) -> dict[str, Any]:
    normalized = [normalize_header(h) for h in header]
    return {normalized[i]: row[i] if i < len(row) else None for i in range(len(normalized)) if normalized[i]}


def _validate_headers(conn: sqlite3.Connection, run_id: int, source_type: str, header: list[Any]) -> dict[str, int]:
    names = [normalize_header(x) for x in header]
    index = {name: i for i, name in enumerate(names) if name}
    missing = EXPECTED[source_type]["required"] - set(index)
    for name in sorted(missing):
        _issue(conn, run_id, "ERROR", "MISSING_REQUIRED_COLUMN", source_type, f"Required column not found: {name}")
    duplicates = {name for name in names if name and names.count(name) > 1}
    for name in sorted(duplicates):
        _issue(conn, run_id, "WARNING", "DUPLICATE_COLUMN_NAME", source_type, f"Duplicate normalized header: {name}")
    if missing:
        raise ValueError(f"{source_type}: missing columns {sorted(missing)}")
    return index


def import_movements(conn: sqlite3.Connection, path: Path, cfg: EngineConfig) -> int:
    run_id = _insert_import_run(conn, "movements", path)
    count = 0
    with XlsxWorkbook(path) as wb:
        if EXPECTED["movements"]["sheet"] not in wb.sheet_names():
            _issue(conn, run_id, "ERROR", "MISSING_SHEET", "movements", "Sheet Plan1 not found")
            raise ValueError("movements sheet missing")
        rows = wb.iter_rows("Plan1")
        header = next(rows)
        _validate_headers(conn, run_id, "movements", header)
        for excel_row, row in enumerate(rows, start=2):
            payload = _row_dict(header, row)
            if not any(v not in (None, "") for v in payload.values()):
                continue
            src_id = _source_record(conn, run_id, str(excel_row), payload)
            recorded = excel_datetime(payload.get("data_digitacao"), payload.get("hora_digitacao"))
            movement_date = excel_datetime(payload.get("data"), 0)
            trans = as_text_code(payload.get("trans"))
            op = as_text_code(payload.get("documento"))
            quantity = as_float(payload.get("qtde_mov"))
            description = normalize_text(payload.get("descricao_produto"))
            conn.execute(
                """INSERT INTO movements(source_record_id,transaction_code,document_op,op_origin,product_code,product_description,
                movement_date,recorded_at,quantity,unit,direction,family,product_origin,sequence,derivation,branch)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    src_id, trans, op, as_text_code(payload.get("origem_o_p")), as_text_code(payload.get("produto")), description,
                    iso(movement_date), iso(recorded), quantity, normalize_text(payload.get("u_m")).upper(), normalize_text(payload.get("e_s")).upper(),
                    as_text_code(payload.get("familia")), as_text_code(payload.get("origem")), as_text_code(payload.get("seq")),
                    normalize_text(payload.get("derivacao")), as_text_code(payload.get("filial")),
                ),
            )
            event_type = {
                "90201": "PRODUCAO_APONTADA",
                "90252": "PRODUCAO_ESTORNADA",
                "90251": "CONSUMO_REALIZADO",
                "90202": "REPASSE_REGISTRADO" if cfg.raw["parameters"]["repasse_keyword"].upper() in description.upper() else "DEVOLUCAO_REALIZADA",
            }.get(trans, "MOVIMENTACAO_REGISTRADA")
            confidence = "CONFIRMADO_CONTABILMENTE" if trans in {"90251", "90202"} else "CONFIRMADO"
            conn.execute(
                """INSERT INTO events(event_type,occurred_at,recorded_at,operational_day,op,product_code,quantity,unit,confidence,
                source_record_id,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (event_type, iso(recorded), UTC_NOW(), cfg.operational_day, op, as_text_code(payload.get("produto")), quantity,
                 normalize_text(payload.get("u_m")).upper(), confidence, src_id, _json({"transaction": trans, "description": description})),
            )
            count += 1
    _finish_import(conn, run_id, count)
    return run_id


def import_resources(conn: sqlite3.Connection, path: Path, cfg: EngineConfig) -> int:
    run_id = _insert_import_run(conn, "resources", path)
    count = 0
    current_op = ""
    with XlsxWorkbook(path) as wb:
        rows = wb.iter_rows("Plan1")
        header = next(rows)
        _validate_headers(conn, run_id, "resources", header)
        for excel_row, row in enumerate(rows, start=2):
            payload = _row_dict(header, row)
            if not any(v not in (None, "") for v in payload.values()):
                continue
            raw_op = as_text_code(payload.get("o_p_o_s"))
            if raw_op:
                current_op = raw_op
            if not current_op:
                src_id = _source_record(conn, run_id, str(excel_row), payload)
                _issue(conn, run_id, "ERROR", "RESOURCE_ROW_WITHOUT_OP", "resource_execution", "Execution row cannot inherit an OP", {"row": excel_row})
                continue
            payload["op_inherited"] = current_op
            src_id = _source_record(conn, run_id, str(excel_row), payload)
            start_at = excel_datetime(payload.get("data_inicio"), payload.get("h_inicio"))
            end_at = excel_datetime(payload.get("data_fim"), payload.get("h_fim"))
            center = as_text_code(payload.get("c_recurso"))
            conn.execute(
                """INSERT INTO resource_executions(source_record_id,op,product_code,stage,operation,sequence,center_resource,start_at,end_at,
                quantity_first_quality,operator,shift) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (src_id, current_op, as_text_code(payload.get("produto_servico")), as_text_code(payload.get("estagio")),
                 as_text_code(payload.get("oper")), as_text_code(payload.get("seq")), center, iso(start_at), iso(end_at),
                 as_float(payload.get("qtde_1a_qual")), normalize_text(payload.get("nome_operador")), as_text_code(payload.get("turno"))),
            )
            conn.execute(
                """INSERT INTO events(event_type,occurred_at,recorded_at,operational_day,op,product_code,center_resource,quantity,unit,
                confidence,source_record_id,rule_code,rule_version,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("EXECUCAO_RECURSO_REGISTRADA", iso(start_at), UTC_NOW(), cfg.operational_day, current_op,
                 as_text_code(payload.get("produto_servico")), center, as_float(payload.get("qtde_1a_qual")), "UNIDADE_ORIGINAL",
                 "CONFIRMADO", src_id, "LINE_FROM_RESOURCE_CENTER", cfg.rule("LINE_FROM_RESOURCE_CENTER")["version"],
                 _json({"end_at": iso(end_at), "operation": as_text_code(payload.get("oper"))})),
            )
            count += 1
    _finish_import(conn, run_id, count)
    return run_id


def import_categories(conn: sqlite3.Connection, path: Path) -> int:
    run_id = _insert_import_run(conn, "categories", path)
    count = 0
    grouped: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    with XlsxWorkbook(path) as wb:
        rows = wb.iter_rows("Planilha1")
        header = next(rows)
        # Only A:F is the canonical table; I:K is an auxiliary list.
        header = header[:6]
        _validate_headers(conn, run_id, "categories", header)
        for excel_row, row in enumerate(rows, start=2):
            payload = _row_dict(header, row[:6])
            code = as_text_code(payload.get("componente"))
            if not code:
                continue
            src_id = _source_record(conn, run_id, str(excel_row), payload)
            classification = (
                normalize_text(payload.get("agrup_i")), normalize_text(payload.get("agrup_ii")),
                normalize_text(payload.get("agrup_iii")), normalize_text(payload.get("descricao")),
            )
            grouped[code].append(classification)
            conn.execute(
                """INSERT INTO component_classifications(source_record_id,component_code,description,family,group_1,group_2,group_3)
                VALUES(?,?,?,?,?,?,?)""",
                (src_id, code, classification[3], as_text_code(payload.get("familia")), classification[0], classification[1], classification[2]),
            )
            count += 1
    for code, rows in grouped.items():
        unique = set(rows)
        if len(rows) > 1:
            severity = "ERROR" if len(unique) > 1 else "WARNING"
            _issue(conn, run_id, severity, "DUPLICATE_COMPONENT_CLASSIFICATION", "component", f"Component {code} has {len(rows)} classifications and {len(unique)} distinct definitions", {"definitions": sorted(unique)})
    errors = conn.execute("SELECT COUNT(*) FROM validation_issues WHERE import_run_id=? AND severity='ERROR'", (run_id,)).fetchone()[0]
    _finish_import(conn, run_id, count, errors)
    return run_id


def import_loss_standards(conn: sqlite3.Connection, path: Path) -> int:
    run_id = _insert_import_run(conn, "loss_standards", path)
    count = 0
    structured = 0
    with XlsxWorkbook(path) as wb:
        rows = wb.iter_rows("Planilha1")
        header = next(rows)
        # The official parameter list is the first two columns. Other blocks are
        # preserved as raw observations but never used as calculation inputs.
        for excel_row, row in enumerate(rows, start=2):
            if not any(v not in (None, "") for v in row):
                continue
            payload = {f"col_{i+1}": value for i, value in enumerate(row)}
            src_id = _source_record(conn, run_id, str(excel_row), payload)
            conn.execute("INSERT INTO loss_standard_rows(source_record_id,row_json) VALUES(?,?)", (src_id, _json(payload)))
            label = normalize_text(row[0] if len(row) > 0 else None)
            value = row[1] if len(row) > 1 else None
            percent = as_float(value) if value not in (None, "") else None
            if label and percent is not None:
                standard_code = normalize_header(label)
                conn.execute(
                    """INSERT OR REPLACE INTO loss_standards(standard_code,name,standard_percent,source_record_id,
                    effective_from,effective_to,status,confidence,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (standard_code, label, percent, src_id, "2026-03-01", None, "ATIVO", "CONFIRMADO_FONTE_OFICIAL",
                     _json({"source_columns": ["A", "B"], "workbook": path.name})),
                )
                structured += 1
            count += 1
    _issue(conn, run_id, "INFO", "LOSS_STANDARD_CANONICALIZED", "loss_standard",
           f"{structured} official loss standards were materialized from columns A:B; auxiliary blocks were preserved only as raw records",
           {"structured_rows": structured, "raw_rows": count})
    _finish_import(conn, run_id, count)
    return run_id

def import_mpop(conn: sqlite3.Connection, path: Path, cfg: EngineConfig) -> int:
    run_id = _insert_import_run(conn, "mpop", path)
    orders, records = parse_mpop_report(path)
    count = 0

    for order in orders:
        payload = order.to_dict()
        src_id = _source_record(conn, run_id, f"order:page:{order.page}:line:{order.source_line}", payload)
        conn.execute(
            """INSERT INTO mpop_orders(source_record_id,origin,op,output_product_code,output_product_description,
            forecast_quantity,actual_quantity,period,hours_production,hours_setup) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (src_id, order.origin, order.op, order.output_product_code, order.output_product_description,
             order.forecast_quantity, order.actual_quantity, order.period, order.hours_production, order.hours_setup),
        )
        count += 1

    for rec in records:
        payload = rec.to_dict()
        src_id = _source_record(conn, run_id, f"component:page:{rec.page}:line:{rec.source_line}", payload)
        classifications = conn.execute(
            "SELECT DISTINCT group_1 FROM component_classifications WHERE component_code=? AND TRIM(COALESCE(group_1,''))<>''",
            (rec.component_code,),
        ).fetchall()
        groups = sorted({normalize_text(r[0]) for r in classifications if normalize_text(r[0])})
        if len(groups) == 1:
            class_group = groups[0]
            class_status = "CONFIRMADO"
        elif len(groups) == 0:
            class_group = ""
            class_status = "PENDENTE"
        else:
            class_group = ""
            class_status = "CONFLITO"

        conn.execute(
            """INSERT INTO mpop_components(source_record_id,origin,op,output_product_code,output_product_description,component_code,
            component_description,qty_consumed,qty_returned,qty_net,qty_loss,loss_percent,nonconform,capacity_kg,loss_kg,
            classification_group_1,classification_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (src_id, rec.origin, rec.op, rec.output_product_code, rec.output_product_description, rec.component_code,
             rec.component_description, rec.qty_consumed, rec.qty_returned, rec.qty_net, rec.qty_loss, rec.loss_percent,
             rec.nonconform, rec.capacity_kg, rec.loss_kg, class_group, class_status),
        )
        if rec.qty_loss > 0:
            is_raw_material = class_status == "CONFIRMADO" and class_group.casefold() == "azeitonas".casefold()
            if is_raw_material:
                confidence = "CONFIRMADO_SEM_HORARIO" if rec.loss_kg is not None else "PENDENTE_CONVERSAO"
                conn.execute(
                    """INSERT INTO events(event_type,occurred_at,recorded_at,operational_day,op,product_code,component_code,quantity,unit,
                    confidence,source_record_id,rule_code,rule_version,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("PERDA_REGISTRADA", None, UTC_NOW(), cfg.operational_day, rec.op, rec.output_product_code, rec.component_code,
                     rec.loss_kg if rec.loss_kg is not None else rec.qty_loss, "KG" if rec.loss_kg is not None else "UNIDADE_MPOP",
                     confidence, src_id, "MPOP_LOSS_KG", cfg.rule("MPOP_LOSS_KG")["version"],
                     _json({"qty_loss_original": rec.qty_loss, "capacity_kg": rec.capacity_kg, "description": rec.component_description,
                            "classification_group_1": class_group})),
                )
                if rec.capacity_kg is None:
                    _issue(conn, run_id, "ERROR", "MPOP_CAPACITY_NOT_FOUND", "mpop_component",
                           f"Capacity KG not found for raw-material loss component {rec.component_code}",
                           {"op": rec.op, "description": rec.component_description})
            else:
                conn.execute(
                    """INSERT INTO events(event_type,occurred_at,recorded_at,operational_day,op,product_code,component_code,quantity,unit,
                    confidence,source_record_id,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("PERDA_MPOP_NAO_INCLUIDA_NO_LIXO_MP", None, UTC_NOW(), cfg.operational_day, rec.op, rec.output_product_code,
                     rec.component_code, rec.qty_loss, "UNIDADE_MPOP", "PENDENTE" if class_status != "CONFIRMADO" else "CONFIRMADO",
                     src_id, _json({"classification_group_1": class_group, "classification_status": class_status,
                                    "description": rec.component_description})),
                )
                if class_status != "CONFIRMADO":
                    _issue(conn, run_id, "WARNING", "MPOP_LOSS_CLASSIFICATION_PENDING", "mpop_component",
                           f"Loss component {rec.component_code} was not included in raw-material loss because classification is {class_status}",
                           {"op": rec.op, "description": rec.component_description, "groups": groups})
        count += 1
    errors = conn.execute("SELECT COUNT(*) FROM validation_issues WHERE import_run_id=? AND severity='ERROR'", (run_id,)).fetchone()[0]
    _finish_import(conn, run_id, count, errors)
    return run_id

def import_reference(conn: sqlite3.Connection, path: Path) -> int:
    run_id = _insert_import_run(conn, "reference", path)
    count = 0
    with XlsxWorkbook(path) as wb:
        rows = list(wb.iter_rows("Por OP"))
        header_row = next(i for i, r in enumerate(rows) if r and normalize_text(r[0]) == "Centro" and len(r) > 2 and normalize_text(r[2]) == "OP")
        header = rows[header_row]
        for excel_idx, row in enumerate(rows[header_row + 1:], start=header_row + 2):
            if len(row) < 3 or not as_text_code(row[2]):
                continue
            payload = _row_dict(header, row)
            src_id = _source_record(conn, run_id, str(excel_idx), payload)
            conn.execute(
                """INSERT INTO reference_op_results(op,center_resource,line_name,product_code,family,production,unit,theoretical_kg,
                real_kg,loss_kg,repasse_kg,yield_operational,standard_percent,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (as_text_code(payload.get("op")), as_text_code(payload.get("centro")), normalize_text(payload.get("linha")),
                 as_text_code(payload.get("produto")), normalize_text(payload.get("familia")), as_float(payload.get("producao")),
                 normalize_text(payload.get("u_m")).upper(), as_float(payload.get("teorico_kg")), as_float(payload.get("real_kg")),
                 as_float(payload.get("lixo_mpop_kg")), as_float(payload.get("repasse_90202_kg")), as_float(payload.get("rend_operacional")),
                 as_float(payload.get("padrao")), normalize_text(payload.get("status"))),
            )
            count += 1
    _finish_import(conn, run_id, count)
    return run_id


def register_config(conn: sqlite3.Connection, cfg: EngineConfig) -> None:
    now = UTC_NOW()
    for key, value in cfg.raw["parameters"].items():
        conn.execute("INSERT OR REPLACE INTO parameters(key,value_json,updated_at) VALUES(?,?,?)", (key, _json(value), now))
    for code, rule in cfg.raw["rules"].items():
        conn.execute(
            "INSERT OR REPLACE INTO rule_versions(rule_code,version,formula,effective_from,config_json) VALUES(?,?,?,?,?)",
            (code, rule["version"], rule["formula"], cfg.start.isoformat(), _json(rule)),
        )


def _selected_ops(conn: sqlite3.Connection, cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    params = [cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds"), *sorted(cfg.considered_origins)]
    placeholders = ",".join("?" for _ in cfg.considered_origins)
    sql = f"""
    SELECT document_op AS op, product_code, product_description, unit, op_origin,
           SUM(CASE WHEN transaction_code='90201' THEN quantity WHEN transaction_code='90252' THEN -quantity ELSE 0 END) AS production_net
    FROM movements
    WHERE recorded_at >= ? AND recorded_at <= ?
      AND op_origin IN ({placeholders})
      AND transaction_code IN ('90201','90252')
    GROUP BY document_op,product_code,product_description,unit,op_origin
    HAVING ABS(production_net) > 0.0000001
    """
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute(sql, params):
        result[row["op"]] = dict(row)
    return result


def _official_centers(conn: sqlite3.Connection, ops: Iterable[str]) -> dict[str, list[str]]:
    ops = list(ops)
    if not ops:
        return {}
    placeholders = ",".join("?" for _ in ops)
    rows = conn.execute(
        f"SELECT op,center_resource,MIN(COALESCE(start_at,'')) first_start FROM resource_executions WHERE op IN ({placeholders}) GROUP BY op,center_resource ORDER BY first_start",
        ops,
    ).fetchall()
    out: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["center_resource"] and row["center_resource"] not in out[row["op"]]:
            out[row["op"]].append(row["center_resource"])
    return dict(out)


def calculate(conn: sqlite3.Connection, cfg: EngineConfig) -> int:
    cur = conn.execute(
        "INSERT INTO calculation_runs(started_at,operational_day,engine_version,config_snapshot_json,status) VALUES(?,?,?,?,?)",
        (UTC_NOW(), cfg.operational_day, cfg.raw["engine_version"], _json(cfg.raw), "RUNNING"),
    )
    calc_id = int(cur.lastrowid)
    selected = _selected_ops(conn, cfg)
    centers = _official_centers(conn, selected)

    def add_indicator(code: str, dimension: str, key: str, value: float | None, unit: str, confidence: str, rule_code: str, metadata: dict, source_rows: list[sqlite3.Row] | None = None) -> int:
        rule = cfg.rule(rule_code)
        c = conn.execute(
            """INSERT INTO indicator_results(calculation_run_id,indicator_code,dimension_type,dimension_key,value,unit,confidence,
            rule_code,rule_version,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (calc_id, code, dimension, key, value, unit, confidence, rule_code, rule["version"], _json(metadata)),
        )
        indicator_id = int(c.lastrowid)
        if source_rows:
            for source in source_rows:
                conn.execute(
                    "INSERT INTO indicator_lineage(indicator_result_id,source_record_id,contribution,metadata_json) VALUES(?,?,?,?)",
                    (indicator_id, source["source_record_id"], source["contribution"], _json({"transaction": source.get("transaction_code") if hasattr(source, "get") else None})),
                )
        return indicator_id

    # Production net per OP with exact movement lineage.
    for op, item in selected.items():
        source_rows = conn.execute(
            """SELECT source_record_id,transaction_code,quantity,
            CASE WHEN transaction_code='90201' THEN quantity ELSE -quantity END contribution
            FROM movements WHERE document_op=? AND recorded_at>=? AND recorded_at<=? AND transaction_code IN ('90201','90252')""",
            (op, cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds")),
        ).fetchall()
        add_indicator("PRODUCTION_NET", "OP", op, item["production_net"], item["unit"], "CONFIRMADO", "PRODUCTION_NET",
                      {"product_code": item["product_code"], "op_origin": item["op_origin"], "centers": centers.get(op, [])}, source_rows)

    # Consumption both strategies are calculated; one is marked active.
    for strategy in cfg.raw["parameters"]["consumption_strategies"]:
        for op in selected:
            where_window = "AND recorded_at>=? AND recorded_at<=?" if strategy == "operational_window" else ""
            args: list[Any] = [op]
            if where_window:
                args += [cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds")]
            rows = conn.execute(
                f"""SELECT source_record_id,transaction_code,product_code,product_description,unit,quantity,
                CASE WHEN transaction_code='90251' THEN quantity ELSE -quantity END contribution
                FROM movements WHERE document_op=? AND transaction_code IN ('90251','90202') {where_window}""",
                args,
            ).fetchall()
            grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
            for row in rows:
                grouped[(row["product_code"], row["product_description"], row["unit"])].append(row)
            for (product_code, desc, unit), group in grouped.items():
                value = sum(r["contribution"] for r in group)
                add_indicator(
                    f"CONSUMPTION_NET_{strategy.upper()}", "OP_COMPONENT", f"{op}|{product_code}|{unit}", value, unit,
                    "CONFIRMADO_CONTABILMENTE", "CONSUMPTION_NET",
                    {"op": op, "component_code": product_code, "description": desc, "strategy": strategy, "active": strategy == cfg.consumption_strategy}, group,
                )

    # Repasse official rule: 90202 + REPASSE + capacity from own description.
    for op in selected:
        rows = conn.execute(
            """SELECT source_record_id,transaction_code,product_code,product_description,unit,quantity,recorded_at
            FROM movements WHERE document_op=? AND transaction_code='90202' AND UPPER(product_description) LIKE ?
            AND recorded_at>=? AND recorded_at<=?""",
            (op, f"%{cfg.raw['parameters']['repasse_keyword'].upper()}%", cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds")),
        ).fetchall()
        total = 0.0
        valid = []
        for row in rows:
            capacity = extract_capacity_kg(row["product_description"])
            if capacity is None:
                _audit(conn, calc_id, "ERROR", "REPASSE_CAPACITY_NOT_FOUND", "OP", op,
                       f"Capacity not found in repasse description {row['product_description']}", None, None, None,
                       {"source_record_id": row["source_record_id"]})
                continue
            contribution = row["quantity"] * capacity
            total += contribution
            valid.append({"source_record_id": row["source_record_id"], "contribution": contribution, "transaction_code": row["transaction_code"]})
        add_indicator("REPASSE_KG", "OP", op, total, "KG", "CONFIRMADO" if len(valid) == len(rows) else "PENDENTE",
                      "REPASSE_KG", {"records": len(rows), "valid_records": len(valid)}, valid)

    # MPOP loss uses all accumulated loss rows of selected OPs, as approved for the initial strategy.
    for op in selected:
        rows = conn.execute(
            "SELECT source_record_id,component_code,component_description,qty_loss,capacity_kg,loss_kg FROM mpop_components WHERE op=? AND qty_loss>0 AND classification_status='CONFIRMADO' AND LOWER(classification_group_1)=LOWER('Azeitonas')",
            (op,),
        ).fetchall()
        total = sum((r["loss_kg"] or 0.0) for r in rows)
        confidence = "CONFIRMADO_SEM_HORARIO" if all(r["loss_kg"] is not None for r in rows) else "PENDENTE"
        lineage = [{"source_record_id": r["source_record_id"], "contribution": r["loss_kg"] or 0.0, "transaction_code": "MPOP"} for r in rows]
        add_indicator("MPOP_LOSS_KG", "OP", op, total, "KG", confidence, "MPOP_LOSS_KG",
                      {"records": len(rows), "scope": "accumulated_op_snapshot"}, lineage)

    # Center resource is represented as metadata and audited, not mathematically aggregated.
    for op in selected:
        center_list = centers.get(op, [])
        if not center_list:
            _audit(conn, calc_id, "WARNING", "CENTER_RESOURCE_NOT_FOUND", "OP", op, "No center resource was found in the official resource file", None, None, None, {})
        elif len(center_list) > 1:
            _audit(conn, calc_id, "WARNING", "MULTI_CENTER_OP", "OP", op, "OP has more than one official center resource", ",".join(center_list), None, None, {})

    compare_reference(conn, calc_id, selected, centers)
    conn.execute(
        "INSERT INTO events(event_type,occurred_at,recorded_at,operational_day,confidence,payload_json) VALUES(?,?,?,?,?,?)",
        ("DIA_OPERACIONAL_FECHADO", cfg.end.isoformat(timespec="seconds"), UTC_NOW(), cfg.operational_day, "CALCULADO", _json({"calculation_run_id": calc_id, "selected_ops": sorted(selected)})),
    )
    conn.execute("UPDATE calculation_runs SET completed_at=?,status='SUCCESS' WHERE id=?", (UTC_NOW(), calc_id))
    return calc_id


def _audit(conn: sqlite3.Connection, calc_id: int, severity: str, code: str, entity_type: str, entity_key: str, message: str,
           official: Any, reference: Any, difference: Any, metadata: dict) -> None:
    conn.execute(
        """INSERT INTO audit_log(calculation_run_id,severity,audit_code,entity_type,entity_key,message,official_value,
        reference_value,difference,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (calc_id, severity, code, entity_type, entity_key, message,
         None if official is None else str(official), None if reference is None else str(reference),
         None if difference is None else str(difference), _json(metadata), UTC_NOW()),
    )


def compare_reference(conn: sqlite3.Connection, calc_id: int, selected: dict[str, dict[str, Any]], centers: dict[str, list[str]]) -> None:
    refs = {r["op"]: r for r in conn.execute("SELECT * FROM reference_op_results")}
    for op, official in selected.items():
        ref = refs.get(op)
        if not ref:
            _audit(conn, calc_id, "WARNING", "OP_NOT_IN_REFERENCE", "OP", op, "Official OP is absent from the reference report", official["production_net"], None, None, {})
            continue
        prod_diff = official["production_net"] - ref["production"]
        if abs(prod_diff) > 1e-6:
            _audit(conn, calc_id, "ERROR", "PRODUCTION_REFERENCE_DIVERGENCE", "OP", op, "Production differs from reference", official["production_net"], ref["production"], prod_diff, {})
        else:
            _audit(conn, calc_id, "INFO", "PRODUCTION_REFERENCE_MATCH", "OP", op, "Production matches reference", official["production_net"], ref["production"], prod_diff, {})

        official_loss = conn.execute(
            "SELECT value FROM indicator_results WHERE calculation_run_id=? AND indicator_code='MPOP_LOSS_KG' AND dimension_key=?",
            (calc_id, op),
        ).fetchone()
        if official_loss:
            diff = official_loss["value"] - ref["loss_kg"]
            _audit(conn, calc_id, "INFO" if abs(diff) <= 1e-5 else "WARNING", "LOSS_REFERENCE_COMPARISON", "OP", op,
                   "MPOP loss compared with reference", official_loss["value"], ref["loss_kg"], diff, {})

        official_repasse = conn.execute(
            "SELECT value FROM indicator_results WHERE calculation_run_id=? AND indicator_code='REPASSE_KG' AND dimension_key=?",
            (calc_id, op),
        ).fetchone()
        if official_repasse:
            diff = official_repasse["value"] - ref["repasse_kg"]
            _audit(conn, calc_id, "INFO" if abs(diff) <= 1e-5 else "WARNING", "REPASSE_REFERENCE_COMPARISON", "OP", op,
                   "Official repasse rule compared with reference; official source prevails", official_repasse["value"], ref["repasse_kg"], diff, {})

        official_centers = centers.get(op, [])
        official_center_text = ",".join(official_centers)
        ref_center = ref["center_resource"] or ""
        if official_center_text != ref_center:
            _audit(conn, calc_id, "WARNING", "CENTER_REFERENCE_DIVERGENCE", "OP", op,
                   "Official center resource differs from reference; official source prevails", official_center_text, ref_center, None, {})

    for op in sorted(set(refs) - set(selected)):
        _audit(conn, calc_id, "WARNING", "REFERENCE_OP_NOT_SELECTED", "OP", op, "Reference OP was not selected by official origin/window parameters", None, refs[op]["production"], None, {})


def export_outputs(conn: sqlite3.Connection, output_dir: Path, calc_id: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    exports = {
        "indicators.csv": "SELECT * FROM indicator_results WHERE calculation_run_id=? ORDER BY indicator_code,dimension_key",
        "audit_log.csv": "SELECT * FROM audit_log WHERE calculation_run_id=? ORDER BY severity DESC,audit_code,entity_key",
        "events.csv": "SELECT * FROM events ORDER BY id",
        "validation_issues.csv": "SELECT * FROM validation_issues ORDER BY severity DESC,import_run_id,id",
        "import_runs.csv": "SELECT * FROM import_runs ORDER BY id",
    }
    for filename, sql in exports.items():
        rows = conn.execute(sql, (calc_id,) if "?" in sql else ()).fetchall()
        with open(output_dir / filename, "w", newline="", encoding="utf-8-sig") as fh:
            if not rows:
                continue
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys(), delimiter=";")
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])

    summary = build_summary(conn, calc_id)
    with open(output_dir / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)


def build_summary(conn: sqlite3.Connection, calc_id: int) -> dict[str, Any]:
    calc = dict(conn.execute("SELECT * FROM calculation_runs WHERE id=?", (calc_id,)).fetchone())
    production = [dict(r) for r in conn.execute(
        "SELECT dimension_key op,value production,unit,confidence,metadata_json FROM indicator_results WHERE calculation_run_id=? AND indicator_code='PRODUCTION_NET' ORDER BY dimension_key", (calc_id,)
    )]
    repasse_total = conn.execute("SELECT COALESCE(SUM(value),0) FROM indicator_results WHERE calculation_run_id=? AND indicator_code='REPASSE_KG'", (calc_id,)).fetchone()[0]
    loss_total = conn.execute("SELECT COALESCE(SUM(value),0) FROM indicator_results WHERE calculation_run_id=? AND indicator_code='MPOP_LOSS_KG'", (calc_id,)).fetchone()[0]
    consumption: dict[str, dict[str, float]] = {}
    for strategy in ("OPERATIONAL_WINDOW", "OP_ACCUMULATED"):
        code = f"CONSUMPTION_NET_{strategy}"
        consumption[strategy.lower()] = {
            r["unit"]: r["value"] for r in conn.execute(
                "SELECT unit,SUM(value) value FROM indicator_results WHERE calculation_run_id=? AND indicator_code=? GROUP BY unit ORDER BY unit",
                (calc_id, code),
            )
        }
    repasse_by_op = {r["dimension_key"]: r["value"] for r in conn.execute(
        "SELECT dimension_key,value FROM indicator_results WHERE calculation_run_id=? AND indicator_code='REPASSE_KG' ORDER BY dimension_key",
        (calc_id,),
    )}
    loss_by_op = {r["dimension_key"]: r["value"] for r in conn.execute(
        "SELECT dimension_key,value FROM indicator_results WHERE calculation_run_id=? AND indicator_code='MPOP_LOSS_KG' ORDER BY dimension_key",
        (calc_id,),
    )}
    return {
        "calculation": calc,
        "counts": {
            "imports": conn.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0],
            "source_records": conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "selected_ops": len(production),
            "validation_issues": conn.execute("SELECT COUNT(*) FROM validation_issues").fetchone()[0],
            "audit_entries": conn.execute("SELECT COUNT(*) FROM audit_log WHERE calculation_run_id=?", (calc_id,)).fetchone()[0],
        },
        "initial_indicators": {
            "production_by_op": production,
            "repasse_kg_total_official": repasse_total,
            "repasse_kg_by_op": repasse_by_op,
            "mpop_loss_kg_total": loss_total,
            "mpop_loss_kg_by_op": loss_by_op,
            "consumption_net_by_unit": consumption,
        },
        "audit_by_severity": {r["severity"]: r["qty"] for r in conn.execute("SELECT severity,COUNT(*) qty FROM audit_log WHERE calculation_run_id=? GROUP BY severity", (calc_id,))},
    }


def run_pipeline(input_dir: Path, output_dir: Path, db_path: Path, cfg: EngineConfig) -> int:
    if db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    try:
        register_config(conn, cfg)
        files = {
            "movements": input_dir / "movimentação diárias.xlsx",
            "resources": input_dir / "Movimentações com centro de recurso.xlsx",
            "categories": input_dir / "Categorias.xlsx",
            "loss_standards": input_dir / "perda padrão atual VF 03.2026.xlsx",
            "mpop": input_dir / "MPOP301.PDF",
            "reference": input_dir / "Analise_MPOP_Diaria_10-07-2026_Por_Linha_e_OP_CORRIGIDA.xlsx",
        }
        required_keys = ("movements", "resources", "categories", "loss_standards", "mpop")
        missing = [str(files[key]) for key in required_keys if not files[key].exists()]
        if missing:
            raise FileNotFoundError(f"Missing input files: {missing}")
        import_movements(conn, files["movements"], cfg)
        import_resources(conn, files["resources"], cfg)
        import_categories(conn, files["categories"])
        import_loss_standards(conn, files["loss_standards"])
        import_mpop(conn, files["mpop"], cfg)
        if files["reference"].exists():
            import_reference(conn, files["reference"])
        else:
            _issue(conn, None, "INFO", "REFERENCE_NOT_PROVIDED", "reference",
                   "Optional reference report was not provided; official calculations continue without comparison.")
        materialize_domain(conn, cfg)
        calc_id = calculate_analytical(conn, cfg)
        conn.commit()
        export_analytical_outputs(conn, output_dir, calc_id, db_path)
        conn.commit()
        return calc_id
    finally:
        conn.close()
