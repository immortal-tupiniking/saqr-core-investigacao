from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from .analytical import (
    _combine_confidence,
    _convert_contribution_to_kg,
    _resolve_component_classification,
    calculate_analytical,
)
from .config import EngineConfig
from .utils import extract_capacity_kg

UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")

STRATEGY_LABELS = {
    "operational_window": "Janela operacional",
    "op_accumulated": "Acumulado completo da OP",
    "op_accumulated_prorated": "Acumulado rateado pela produção do dia",
}


def config_for_strategy(base: EngineConfig, strategy: str) -> EngineConfig:
    if strategy not in STRATEGY_LABELS:
        raise ValueError(f"Estratégia inválida: {strategy}")
    raw = deepcopy(base.raw)
    raw["engine_version"] = "0.3.0"
    if strategy == "operational_window":
        raw["parameters"]["consumption_strategy"] = "operational_window"
        raw["parameters"]["accumulated_allocation_mode"] = "production_ratio"
    elif strategy == "op_accumulated":
        raw["parameters"]["consumption_strategy"] = "op_accumulated"
        raw["parameters"]["accumulated_allocation_mode"] = "full_op"
    else:
        raw["parameters"]["consumption_strategy"] = "op_accumulated"
        raw["parameters"]["accumulated_allocation_mode"] = "production_ratio"
    raw["parameters"]["ui_strategy_key"] = strategy
    return EngineConfig(raw)


def ensure_mvp_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS component_analytical_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            calculation_run_id INTEGER NOT NULL REFERENCES calculation_runs(id),
            op TEXT NOT NULL,
            section TEXT NOT NULL,
            component_code TEXT,
            description TEXT,
            classification_group TEXT,
            movement_code TEXT,
            quantity_original REAL,
            unit_original TEXT,
            quantity_converted REAL,
            unit_converted TEXT,
            allocation_factor REAL NOT NULL DEFAULT 1.0,
            source_origin TEXT,
            occurred_at TEXT,
            confidence TEXT NOT NULL,
            source_record_id INTEGER REFERENCES source_records(id),
            source_file TEXT,
            source_row TEXT,
            source_sha256 TEXT,
            record_hash TEXT,
            metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_component_results_calc_op
            ON component_analytical_results(calculation_run_id, op, section);
        """
    )


def _section(group: str | None, transaction: str, description: str) -> str:
    if transaction in {"90201", "90252"}:
        return "Produção"
    if transaction == "MPOP_LOSS":
        return "Lixo"
    if transaction == "90202" and "REPASSE" in (description or "").upper():
        return "Repasse"
    normalized = (group or "").casefold()
    if normalized in {"azeitonas", "outras mps", "outras mp", "matéria-prima", "materia-prima"}:
        return "Matéria-prima"
    if normalized == "salmoura":
        return "Salmoura"
    if normalized == "aditivos":
        return "Aditivos"
    if normalized in {"azeites e óleos", "azeites e oleos"}:
        return "Azeites e óleos"
    if normalized == "embalagens":
        return "Embalagens"
    return group or "Não classificado"


def _source_meta(conn: sqlite3.Connection, source_record_id: int) -> sqlite3.Row:
    row = conn.execute(
        """SELECT s.source_row,s.record_hash,i.filename,i.sha256
        FROM source_records s JOIN import_runs i ON i.id=s.import_run_id WHERE s.id=?""",
        (source_record_id,),
    ).fetchone()
    if row is None:
        raise KeyError(source_record_id)
    return row


def _proration_factor(conn: sqlite3.Connection, calc_id: int, op: str, strategy: str) -> float:
    if strategy != "op_accumulated_prorated":
        return 1.0
    row = conn.execute(
        """SELECT metadata_json FROM indicator_results
        WHERE calculation_run_id=? AND indicator_code='RAW_MATERIAL_CONSUMPTION_KG_PRORATED'
          AND dimension_type='OP' AND dimension_key=?""",
        (calc_id, op),
    ).fetchone()
    if not row:
        return 1.0
    metadata = json.loads(row[0] or "{}")
    factor = metadata.get("factor")
    return float(factor) if factor is not None else 1.0


def materialize_component_results(conn: sqlite3.Connection, calc_id: int, cfg: EngineConfig) -> None:
    ensure_mvp_schema(conn)
    conn.execute("DELETE FROM component_analytical_results WHERE calculation_run_id=?", (calc_id,))
    strategy = cfg.raw.get("parameters", {}).get("ui_strategy_key")
    if not strategy:
        if cfg.consumption_strategy == "operational_window":
            strategy = "operational_window"
        elif cfg.raw.get("parameters", {}).get("accumulated_allocation_mode") == "production_ratio":
            strategy = "op_accumulated_prorated"
        else:
            strategy = "op_accumulated"
    selected_ops = [r[0] for r in conn.execute(
        """SELECT dimension_key FROM indicator_results WHERE calculation_run_id=?
        AND indicator_code='PRODUCTION_NET' AND dimension_type='OP' ORDER BY dimension_key""",
        (calc_id,),
    )]
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    start = cfg.start.isoformat(timespec="seconds")
    end = cfg.end.isoformat(timespec="seconds")

    def insert_row(**row: Any) -> None:
        columns = [
            "calculation_run_id", "op", "section", "component_code", "description",
            "classification_group", "movement_code", "quantity_original", "unit_original",
            "quantity_converted", "unit_converted", "allocation_factor", "source_origin",
            "occurred_at", "confidence", "source_record_id", "source_file", "source_row",
            "source_sha256", "record_hash", "metadata_json",
        ]
        conn.execute(
            f"INSERT INTO component_analytical_results({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            [row.get(c) for c in columns],
        )

    for op in selected_ops:
        factor = _proration_factor(conn, calc_id, op, strategy)
        production_rows = conn.execute(
            """SELECT * FROM movements WHERE document_op=? AND transaction_code IN ('90201','90252')
            AND recorded_at>=? AND recorded_at<=? ORDER BY recorded_at,id""",
            (op, start, end),
        ).fetchall()
        for r in production_rows:
            meta = _source_meta(conn, r["source_record_id"])
            sign = 1.0 if r["transaction_code"] == "90201" else -1.0
            insert_row(
                calculation_run_id=calc_id, op=op, section="Produção", component_code=r["product_code"],
                description=r["product_description"], classification_group="Produto acabado",
                movement_code=r["transaction_code"], quantity_original=r["quantity"], unit_original=r["unit"],
                quantity_converted=sign * r["quantity"], unit_converted=r["unit"], allocation_factor=1.0,
                source_origin="Movimentações", occurred_at=r["recorded_at"], confidence="CONFIRMADO",
                source_record_id=r["source_record_id"], source_file=meta["filename"], source_row=meta["source_row"],
                source_sha256=meta["sha256"], record_hash=meta["record_hash"],
                metadata_json=json.dumps({"effect_sign": sign, "strategy": strategy}, ensure_ascii=False),
            )

        time_clause = "AND recorded_at>=? AND recorded_at<=?" if strategy == "operational_window" else ""
        args: list[Any] = [op]
        if time_clause:
            args.extend([start, end])
        movement_rows = conn.execute(
            f"""SELECT * FROM movements WHERE document_op=? AND transaction_code IN ('90251','90202')
            {time_clause} ORDER BY recorded_at,id""",
            args,
        ).fetchall()
        for r in movement_rows:
            classification = _resolve_component_classification(
                conn, r["product_code"], r["product_description"], cfg, cache
            )
            group = classification.get("group")
            contribution = r["quantity"] if r["transaction_code"] == "90251" else -r["quantity"]
            conversion = _convert_contribution_to_kg(
                r["product_code"], r["product_description"], r["unit"], contribution, group, cfg
            )
            converted = conversion["kg"]
            if converted is not None:
                converted *= factor
            confidence = _combine_confidence([
                classification.get("confidence", "PENDENTE_CLASSIFICACAO"),
                conversion.get("confidence", "PENDENTE_CONVERSAO"),
                "ESTIMADO_RATEADO_PRODUCAO" if strategy == "op_accumulated_prorated" else "CONFIRMADO",
            ])
            meta = _source_meta(conn, r["source_record_id"])
            section = _section(group, r["transaction_code"], r["product_description"])
            insert_row(
                calculation_run_id=calc_id, op=op, section=section, component_code=r["product_code"],
                description=r["product_description"], classification_group=group,
                movement_code=r["transaction_code"], quantity_original=r["quantity"], unit_original=r["unit"],
                quantity_converted=converted, unit_converted="KG" if converted is not None else None,
                allocation_factor=factor, source_origin="Movimentações", occurred_at=r["recorded_at"],
                confidence=confidence, source_record_id=r["source_record_id"], source_file=meta["filename"],
                source_row=meta["source_row"], source_sha256=meta["sha256"], record_hash=meta["record_hash"],
                metadata_json=json.dumps({
                    "classification_method": classification.get("method"),
                    "conversion_method": conversion.get("method"),
                    "conversion_factor": conversion.get("factor"),
                    "effect_sign": 1 if r["transaction_code"] == "90251" else -1,
                    "strategy": strategy,
                }, ensure_ascii=False),
            )

        for r in conn.execute(
            """SELECT * FROM mpop_components WHERE op=? AND qty_loss>0 ORDER BY id""", (op,)
        ):
            meta = _source_meta(conn, r["source_record_id"])
            included = (
                r["classification_status"] == "CONFIRMADO"
                and (r["classification_group_1"] or "").casefold() == "azeitonas"
                and r["loss_kg"] is not None
            )
            confidence = "CONFIRMADO_SEM_HORARIO" if included else "PENDENTE_CLASSIFICACAO_OU_CONVERSAO"
            insert_row(
                calculation_run_id=calc_id, op=op, section="Lixo", component_code=r["component_code"],
                description=r["component_description"], classification_group=r["classification_group_1"],
                movement_code="MPOP_LOSS", quantity_original=r["qty_loss"],
                unit_original="BR" if r["capacity_kg"] is not None else None,
                quantity_converted=r["loss_kg"] if included else None, unit_converted="KG" if included else None,
                allocation_factor=1.0, source_origin="MPOP301", occurred_at=None, confidence=confidence,
                source_record_id=r["source_record_id"], source_file=meta["filename"], source_row=meta["source_row"],
                source_sha256=meta["sha256"], record_hash=meta["record_hash"],
                metadata_json=json.dumps({"included_in_lixo": included, "capacity_kg": r["capacity_kg"]}, ensure_ascii=False),
            )
    conn.commit()


def find_or_create_calculation(conn: sqlite3.Connection, base_cfg: EngineConfig, strategy: str) -> tuple[int, bool]:
    cfg = config_for_strategy(base_cfg, strategy)
    snapshot = json.dumps(cfg.raw, ensure_ascii=False, sort_keys=True)
    row = conn.execute(
        """SELECT id FROM calculation_runs WHERE config_snapshot_json=? AND status='SUCCESS'
        ORDER BY id DESC LIMIT 1""",
        (snapshot,),
    ).fetchone()
    if row:
        ensure_mvp_schema(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM component_analytical_results WHERE calculation_run_id=?", (row[0],)
        ).fetchone()[0]
        if count == 0:
            materialize_component_results(conn, int(row[0]), cfg)
        return int(row[0]), False
    calc_id = calculate_analytical(conn, cfg)
    materialize_component_results(conn, calc_id, cfg)
    conn.commit()
    return calc_id, True
