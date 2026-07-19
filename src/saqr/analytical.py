from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
from typing import Any, Iterable

from .config import EngineConfig
from .utils import extract_capacity_kg, normalize_header, normalize_text

UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


INDICATOR_CATALOG = {
    "PRODUCTION_NET": {
        "name": "Produção líquida",
        "objective": "Quantificar a produção líquida apontada no dia operacional.",
        "formula": "SUM(90201) - SUM(90252)",
        "unit": "UNIDADE_DE_PRODUCAO",
        "source": "Movimentações",
        "rule": "PRODUCTION_NET",
    },
    "CONSUMPTION_NET": {
        "name": "Consumo líquido",
        "objective": "Quantificar o consumo contábil líquido sem misturar unidades.",
        "formula": "SUM(90251) - SUM(90202)",
        "unit": "UNIDADE_ORIGINAL",
        "source": "Movimentações",
        "rule": "CONSUMPTION_NET",
    },
    "THEORETICAL_CONSUMPTION_KG": {
        "name": "Consumo teórico de matéria-prima",
        "objective": "Quantificar a matéria-prima teórica correspondente à produção líquida.",
        "formula": "Produção líquida x conteúdo líquido identificado na descrição oficial do produto",
        "unit": "KG",
        "source": "Movimentações + Parâmetros",
        "rule": "THEORETICAL_CONSUMPTION",
    },
    "CONSUMPTION_VARIANCE_KG": {
        "name": "Diferença de consumo",
        "objective": "Comparar o consumo real de matéria-prima com o consumo teórico.",
        "formula": "Consumo real de MP - Consumo teórico de MP",
        "unit": "KG",
        "source": "Motor Analítico",
        "rule": "CONSUMPTION_VARIANCE",
    },
    "RAW_MATERIAL_CONSUMPTION_KG": {
        "name": "Consumo de matéria-prima",
        "objective": "Converter somente componentes classificados como matéria-prima para kg com regra explícita.",
        "formula": "Consumo líquido convertido para kg conforme unidade/capacidade/densidade parametrizada",
        "unit": "KG",
        "source": "Movimentações + Categorias + Parâmetros",
        "rule": "CONSUMPTION_RAW_KG",
    },
    "MPOP_LOSS_KG": {
        "name": "Lixo MPOP",
        "objective": "Quantificar perdas físicas registradas no MPOP301.",
        "formula": "SUM(Qtd. Perda MPOP x capacidade kg da descrição do componente)",
        "unit": "KG",
        "source": "MPOP301 + Categorias",
        "rule": "MPOP_LOSS_KG",
    },
    "REPASSE_KG": {
        "name": "Repasse",
        "objective": "Quantificar devoluções 90202 identificadas como REPASSE.",
        "formula": "SUM(Qtde 90202 x capacidade kg da descrição do próprio item)",
        "unit": "KG",
        "source": "Movimentações",
        "rule": "REPASSE_KG",
    },
    "MP_UTILIZED_KG": {
        "name": "MP Aproveitada",
        "objective": "Quantificar a parcela de matéria-prima considerada aproveitada segundo o modo parametrizado.",
        "formula": "Consumo de MP - Lixo [- Repasse, conforme parâmetro]",
        "unit": "KG",
        "source": "Motor Analítico",
        "rule": "MP_UTILIZED_KG",
    },
    "OPERATIONAL_YIELD": {
        "name": "Rendimento Operacional",
        "objective": "Medir a proporção da matéria-prima considerada aproveitada.",
        "formula": "MP Aproveitada / Consumo de MP",
        "unit": "%",
        "source": "Motor Analítico",
        "rule": "OPERATIONAL_YIELD",
    },
    "LOSS_STANDARD_PERCENT": {
        "name": "Perda Padrão",
        "objective": "Aplicar o padrão oficial vigente por classificação parametrizada do produto.",
        "formula": "Padrão oficial selecionado por regra de mapeamento parametrizada",
        "unit": "%",
        "source": "Cadastro de Perda Padrão + Parâmetros",
        "rule": "LOSS_STANDARD",
    },
    "ACTUAL_LOSS_RATE": {
        "name": "Perda Real",
        "objective": "Medir a parcela não aproveitada do consumo de matéria-prima.",
        "formula": "1 - Rendimento Operacional",
        "unit": "%",
        "source": "Motor Analítico",
        "rule": "OPERATIONAL_YIELD",
    },
    "EXCESS_OVER_STANDARD_PP": {
        "name": "Excesso sobre o Padrão (p.p.)",
        "objective": "Quantificar a diferença em pontos percentuais entre perda real e perda padrão.",
        "formula": "Perda Real - Perda Padrão",
        "unit": "PP",
        "source": "Motor Analítico",
        "rule": "EXCESS_OVER_STANDARD",
    },
    "EXCESS_OVER_STANDARD_KG": {
        "name": "Excesso sobre o Padrão (kg)",
        "objective": "Quantificar em kg a perda que excede o padrão oficial.",
        "formula": "MAX(0, (Perda Real - Perda Padrão) x Consumo de MP)",
        "unit": "KG",
        "source": "Motor Analítico",
        "rule": "EXCESS_OVER_STANDARD",
    },
    "EXCESS_OVER_STANDARD": {
        "name": "Excesso sobre o Padrão",
        "objective": "Quantificar quanto a perda calculada excede o padrão oficial.",
        "formula": "MAX(0, Perda Real - Perda Padrão)",
        "unit": "PP/KG",
        "source": "Motor Analítico",
        "rule": "EXCESS_OVER_STANDARD",
    },
}


def _catalog_template(code: str) -> dict[str, Any] | None:
    if code in INDICATOR_CATALOG:
        return INDICATOR_CATALOG[code]
    prefix_map = (
        ("CONSUMPTION_NET_", "CONSUMPTION_NET"),
        ("THEORETICAL_CONSUMPTION_KG", "THEORETICAL_CONSUMPTION_KG"),
        ("CONSUMPTION_VARIANCE_KG", "CONSUMPTION_VARIANCE_KG"),
        ("RAW_MATERIAL_CONSUMPTION_KG", "RAW_MATERIAL_CONSUMPTION_KG"),
        ("MP_UTILIZED_KG", "MP_UTILIZED_KG"),
        ("OPERATIONAL_YIELD", "OPERATIONAL_YIELD"),
        ("ACTUAL_LOSS_RATE", "ACTUAL_LOSS_RATE"),
        ("EXCESS_OVER_STANDARD_PP", "EXCESS_OVER_STANDARD_PP"),
        ("EXCESS_OVER_STANDARD_KG", "EXCESS_OVER_STANDARD_KG"),
    )
    for prefix, base in prefix_map:
        if code.startswith(prefix):
            return INDICATOR_CATALOG[base]
    return None


def register_indicator_catalog(conn: sqlite3.Connection) -> None:
    for code, item in INDICATOR_CATALOG.items():
        conn.execute(
            """INSERT OR REPLACE INTO indicator_catalog(indicator_code,name,objective,formula_description,default_unit,
            frequency,official_source,responsible,confidence_policy,rule_code,active,metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, item["name"], item["objective"], item["formula"], item["unit"], "DIA_OPERACIONAL",
             item["source"], "ENGENHARIA_DE_PROCESSOS", "Propagação do menor nível de confiança das entradas",
             item["rule"], 1, _json({"module": "SAQR"})),
        )


def _audit(conn: sqlite3.Connection, calc_id: int, severity: str, code: str, entity_type: str, entity_key: str,
           message: str, official: Any = None, reference: Any = None, difference: Any = None,
           metadata: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO audit_log(calculation_run_id,severity,audit_code,entity_type,entity_key,message,official_value,
        reference_value,difference,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (calc_id, severity, code, entity_type, entity_key, message,
         None if official is None else str(official), None if reference is None else str(reference),
         None if difference is None else str(difference), _json(metadata or {}), UTC_NOW()),
    )


def _selected_ops(conn: sqlite3.Connection, cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    placeholders = ",".join("?" for _ in cfg.considered_origins)
    params: list[Any] = [cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds"), *sorted(cfg.considered_origins)]
    sql = f"""
    SELECT document_op AS op,product_code,product_description,unit,op_origin,COALESCE(derivation,'') derivation,
           SUM(CASE WHEN transaction_code='90201' THEN quantity WHEN transaction_code='90252' THEN -quantity ELSE 0 END) production_net
    FROM movements
    WHERE recorded_at>=? AND recorded_at<=?
      AND op_origin IN ({placeholders})
      AND transaction_code IN ('90201','90252')
    GROUP BY document_op,product_code,product_description,unit,op_origin,COALESCE(derivation,'')
    HAVING ABS(production_net)>0.0000001
    """
    return {r["op"]: dict(r) for r in conn.execute(sql, params)}


def _official_centers(conn: sqlite3.Connection, ops: Iterable[str], cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for op in ops:
        window = [r[0] for r in conn.execute(
            """SELECT DISTINCT center_resource FROM resource_executions
            WHERE op=? AND TRIM(COALESCE(center_resource,''))<>''
              AND COALESCE(end_at,start_at)>=? AND start_at<=? ORDER BY center_resource""",
            (op, cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds")),
        )]
        all_centers = [r[0] for r in conn.execute(
            "SELECT DISTINCT center_resource FROM resource_executions WHERE op=? AND TRIM(COALESCE(center_resource,''))<>'' ORDER BY center_resource",
            (op,),
        )]
        selected = window if window else all_centers
        if not selected:
            key = "CENTRO_NAO_ENCONTRADO"
            confidence = "PENDENTE_CENTRO"
        elif len(selected) == 1:
            key = selected[0]
            confidence = "CONFIRMADO"
        else:
            key = "MULTICENTRO:" + "|".join(selected)
            confidence = "PENDENTE_RATEIO_LINHA"
        out[op] = {"centers": selected, "all_centers": all_centers, "window_centers": window, "line_key": key, "confidence": confidence}
    return out


def _confidence_rank(value: str) -> int:
    upper = (value or "").upper()
    if "PENDENTE" in upper or "CONFLITO" in upper:
        return 4
    if "ESTIMADO" in upper or "RATEADO" in upper or "PARAMETRIZADO" in upper:
        return 3
    if "SEM_HORARIO" in upper or "CONTABILMENTE" in upper:
        return 2
    return 1


def _combine_confidence(values: Iterable[str], default: str = "CONFIRMADO") -> str:
    values = [v for v in values if v]
    if not values:
        return default
    worst = max(values, key=_confidence_rank)
    if _confidence_rank(worst) == 4:
        return worst if "PENDENTE" in worst.upper() else "PENDENTE"
    if _confidence_rank(worst) == 3:
        return worst if "ESTIMADO" in worst.upper() else "ESTIMADO_PARAMETRIZADO"
    if _confidence_rank(worst) == 2:
        return worst
    return "CONFIRMADO"


def _lineage_rows(conn: sqlite3.Connection, indicator_id: int, role: str, scale: float = 1.0) -> list[dict[str, Any]]:
    rows = []
    for r in conn.execute("SELECT source_record_id,event_id,contribution,metadata_json FROM indicator_lineage WHERE indicator_result_id=?", (indicator_id,)):
        meta = json.loads(r["metadata_json"] or "{}")
        meta["role"] = role
        rows.append({
            "source_record_id": r["source_record_id"],
            "event_id": r["event_id"],
            "contribution": None if r["contribution"] is None else r["contribution"] * scale,
            "metadata": meta,
        })
    return rows


def _resolve_component_classification(conn: sqlite3.Connection, component_code: str, description: str,
                                      cfg: EngineConfig, cache: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    key = (component_code, description)
    if key in cache:
        return cache[key]
    rows = conn.execute(
        "SELECT DISTINCT TRIM(group_1) group_1,source_record_id FROM component_classifications WHERE component_code=? AND TRIM(COALESCE(group_1,''))<>''",
        (component_code,),
    ).fetchall()
    names: dict[str, tuple[str, int]] = {}
    for r in rows:
        name = normalize_text(r["group_1"])
        names[name.casefold()] = (name, r["source_record_id"])
    if len(names) == 1:
        name, src = next(iter(names.values()))
        result = {"group": name, "confidence": "CONFIRMADO_CLASSIFICACAO", "source_record_id": src, "method": "Categorias.xlsx"}
    elif len(names) > 1:
        result = {"group": None, "confidence": "PENDENTE_CONFLITO_CLASSIFICACAO", "source_record_id": None,
                  "method": "conflict", "candidates": [x[0] for x in names.values()]}
    else:
        result = {"group": None, "confidence": "PENDENTE_CLASSIFICACAO", "source_record_id": None, "method": "none"}
        text = normalize_text(description).upper()
        for rule in cfg.raw.get("parameters", {}).get("component_classification_fallback_patterns", []):
            if re.search(rule["pattern"], text, flags=re.IGNORECASE):
                result = {"group": rule["group"], "confidence": "ESTIMADO_CLASSIFICACAO_PARAMETRIZADA",
                          "source_record_id": None, "method": f"parameter:{rule.get('id', rule['pattern'])}"}
                break
    cache[key] = result
    return result


def _convert_contribution_to_kg(component_code: str, description: str, unit: str, contribution: float,
                                classification_group: str | None, cfg: EngineConfig) -> dict[str, Any]:
    unit = (unit or "").upper()
    if unit == "KG":
        return {"kg": contribution, "factor": 1.0, "confidence": "CONFIRMADO_CONVERSAO", "method": "KG=KG"}
    if unit in {"BR", "BD"}:
        capacity = extract_capacity_kg(description)
        if capacity is None:
            return {"kg": None, "factor": None, "confidence": "PENDENTE_CONVERSAO", "method": "capacity_not_found"}
        return {"kg": contribution * capacity, "factor": capacity, "confidence": "CONFIRMADO_CAPACIDADE_DESCRICAO",
                "method": "capacidade_da_descricao"}
    if unit == "LT":
        conversions = cfg.raw.get("parameters", {}).get("density_kg_per_l", {})
        by_component = conversions.get("by_component", {})
        by_group = conversions.get("by_classification", {})
        factor = by_component.get(component_code)
        method = f"parameter:component:{component_code}"
        if factor is None and classification_group:
            factor = by_group.get(classification_group)
            method = f"parameter:classification:{classification_group}"
        if factor is None:
            return {"kg": None, "factor": None, "confidence": "PENDENTE_DENSIDADE_LT_KG", "method": "density_not_configured"}
        return {"kg": contribution * float(factor), "factor": float(factor), "confidence": "ESTIMADO_DENSIDADE_PARAMETRIZADA", "method": method}
    return {"kg": None, "factor": None, "confidence": "PENDENTE_UNIDADE_NAO_CONVERTIVEL", "method": f"unit:{unit}"}


def _extract_theoretical_factor_kg(product_code: str, description: str, production_unit: str,
                                   cfg: EngineConfig) -> dict[str, Any]:
    """Return kg per production unit from the official product description.

    The extraction is explicit and auditable. It never changes the source description.
    """
    text = normalize_text(description).upper().replace("GR", "G")
    unit = (production_unit or "").upper()
    if unit == "KG":
        return {"factor_kg": 1.0, "confidence": "CONFIRMADO", "method": "KG=KG"}
    if unit in {"BR", "BD"}:
        capacity = extract_capacity_kg(text)
        if capacity is None:
            return {"factor_kg": None, "confidence": "PENDENTE_PESO_PRODUTO", "method": "capacity_not_found"}
        return {"factor_kg": capacity, "confidence": "CONFIRMADO_CAPACIDADE_DESCRICAO",
                "method": "capacidade_da_descricao"}
    if unit == "CX":
        pack = re.search(r"(?<![0-9])([0-9]+)\s*/\s*([0-9]+(?:[\.,][0-9]+)?)\s*(KG|G|ML|L)\b", text)
        if not pack:
            return {"factor_kg": None, "confidence": "PENDENTE_CONTEUDO_PRODUTO", "method": "pack_not_found"}
        count = float(pack.group(1))
        amount = float(pack.group(2).replace(",", "."))
        measure = pack.group(3)
        if measure == "KG":
            return {"factor_kg": count * amount, "confidence": "CONFIRMADO_CONTEUDO_DESCRICAO",
                    "method": f"{count:g}x{amount:g}KG"}
        if measure == "G":
            return {"factor_kg": count * amount / 1000.0, "confidence": "CONFIRMADO_CONTEUDO_DESCRICAO",
                    "method": f"{count:g}x{amount:g}G"}
        liters = count * amount / (1000.0 if measure == "ML" else 1.0)
        densities = cfg.raw.get("parameters", {}).get("finished_product_density_kg_per_l", {})
        density = densities.get("by_product", {}).get(product_code, densities.get("default"))
        if density is None:
            return {"factor_kg": None, "confidence": "PENDENTE_DENSIDADE_PRODUTO", "method": "density_not_configured"}
        return {"factor_kg": liters * float(density), "confidence": "ESTIMADO_DENSIDADE_PARAMETRIZADA",
                "method": f"{count:g}x{amount:g}{measure};densidade={float(density):g}"}
    return {"factor_kg": None, "confidence": "PENDENTE_UNIDADE_PRODUCAO", "method": f"unit:{unit}"}


def _find_loss_standard(conn: sqlite3.Connection, product_code: str, description: str,
                        cfg: EngineConfig) -> dict[str, Any]:
    overrides = cfg.raw.get("parameters", {}).get("loss_standard_product_overrides", {})
    mapping = None
    if product_code in overrides:
        mapping = {"id": f"product_override:{product_code}", "standard_name": overrides[product_code]}
    else:
        text = normalize_text(description).upper()
        for rule in cfg.raw.get("parameters", {}).get("loss_standard_mapping_rules", []):
            if re.search(rule["pattern"], text, flags=re.IGNORECASE):
                mapping = rule
                break
    if not mapping:
        return {"value": None, "confidence": "PENDENTE_MAPEAMENTO_PERDA_PADRAO", "source_record_id": None, "mapping": None}
    standard_code = normalize_header(mapping["standard_name"])
    row = conn.execute(
        "SELECT * FROM loss_standards WHERE standard_code=? AND status='ATIVO' ORDER BY effective_from DESC LIMIT 1",
        (standard_code,),
    ).fetchone()
    if not row:
        return {"value": None, "confidence": "PENDENTE_PADRAO_NAO_ENCONTRADO", "source_record_id": None, "mapping": mapping}
    return {"value": row["standard_percent"], "confidence": "ESTIMADO_MAPEAMENTO_PARAMETRIZADO",
            "source_record_id": row["source_record_id"], "mapping": mapping, "standard_name": row["name"]}


def calculate_analytical(conn: sqlite3.Connection, cfg: EngineConfig) -> int:
    register_indicator_catalog(conn)
    cur = conn.execute(
        "INSERT INTO calculation_runs(started_at,operational_day,engine_version,config_snapshot_json,status) VALUES(?,?,?,?,?)",
        (UTC_NOW(), cfg.operational_day, cfg.raw["engine_version"], _json(cfg.raw), "RUNNING"),
    )
    calc_id = int(cur.lastrowid)
    for key, value in cfg.raw.get("parameters", {}).items():
        conn.execute("INSERT INTO calculation_run_parameters(calculation_run_id,parameter_key,value_json) VALUES(?,?,?)",
                     (calc_id, key, _json(value)))

    selected = _selected_ops(conn, cfg)
    centers = _official_centers(conn, selected, cfg)
    indicator_ids: dict[tuple[str, str, str], int] = {}

    def add_indicator(code: str, dimension: str, key: str, value: float | None, unit: str, confidence: str,
                      rule_code: str, metadata: dict[str, Any], lineage: list[dict[str, Any]] | None = None,
                      dependencies: list[dict[str, Any]] | None = None) -> int:
        rule = cfg.rule(rule_code)
        catalog_item = _catalog_template(code)
        if catalog_item:
            conn.execute(
                """INSERT OR IGNORE INTO indicator_catalog(indicator_code,name,objective,formula_description,default_unit,
                frequency,official_source,responsible,confidence_policy,rule_code,active,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, catalog_item["name"], catalog_item["objective"], catalog_item["formula"], catalog_item["unit"],
                 "DIA_OPERACIONAL", catalog_item["source"], "ENGENHARIA_DE_PROCESSOS",
                 "Propagação do menor nível de confiança das entradas", rule_code, 1,
                 _json({"module": "SAQR", "base_indicator": next((k for k, v in INDICATOR_CATALOG.items() if v is catalog_item), code)})),
            )
        c = conn.execute(
            """INSERT INTO indicator_results(calculation_run_id,indicator_code,dimension_type,dimension_key,value,unit,confidence,
            rule_code,rule_version,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (calc_id, code, dimension, key, value, unit, confidence, rule_code, rule["version"], _json(metadata)),
        )
        iid = int(c.lastrowid)
        indicator_ids[(code, dimension, key)] = iid
        for source in lineage or []:
            source_record_id = source.get("source_record_id")
            event_id = source.get("event_id")
            if event_id is None and source_record_id is not None:
                event = conn.execute("SELECT id FROM events WHERE source_record_id=? ORDER BY id LIMIT 1", (source_record_id,)).fetchone()
                event_id = event[0] if event else None
            conn.execute(
                "INSERT INTO indicator_lineage(indicator_result_id,source_record_id,event_id,contribution,metadata_json) VALUES(?,?,?,?,?)",
                (iid, source_record_id, event_id, source.get("contribution"), _json(source.get("metadata", {}))),
            )
        for dep in dependencies or []:
            conn.execute(
                """INSERT OR IGNORE INTO indicator_dependencies
                (indicator_result_id,depends_on_indicator_id,role,weight,metadata_json) VALUES(?,?,?,?,?)""",
                (iid, dep["indicator_id"], dep.get("role", "input"), dep.get("weight"), _json(dep.get("metadata", {}))),
            )
        return iid

    # 1. Production ----------------------------------------------------------
    for op, item in selected.items():
        rows = conn.execute(
            """SELECT source_record_id,transaction_code,quantity,
            CASE WHEN transaction_code='90201' THEN quantity ELSE -quantity END contribution
            FROM movements WHERE document_op=? AND recorded_at>=? AND recorded_at<=?
              AND transaction_code IN ('90201','90252') ORDER BY recorded_at,source_record_id""",
            (op, cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds")),
        ).fetchall()
        lineage = [{"source_record_id": r["source_record_id"], "contribution": r["contribution"],
                    "metadata": {"transaction": r["transaction_code"], "role": "production"}} for r in rows]
        add_indicator("PRODUCTION_NET", "OP", op, item["production_net"], item["unit"], "CONFIRMADO",
                      "PRODUCTION_NET", {"product_code": item["product_code"], "product_description": item["product_description"],
                                         "origin": item["op_origin"], **centers[op]}, lineage)

    # 1b. Theoretical consumption from official product description ---------
    theoretical_ids: dict[str, int] = {}
    theoretical_values: dict[str, float | None] = {}
    theoretical_confidence: dict[str, str] = {}
    for op, item in selected.items():
        factor = _extract_theoretical_factor_kg(item["product_code"], item["product_description"], item["unit"], cfg)
        value = None if factor["factor_kg"] is None else item["production_net"] * factor["factor_kg"]
        confidence = factor["confidence"]
        production_id = indicator_ids[("PRODUCTION_NET", "OP", op)]
        iid = add_indicator(
            "THEORETICAL_CONSUMPTION_KG", "OP", op, value, "KG", confidence,
            "THEORETICAL_CONSUMPTION",
            {"product_code": item["product_code"], "product_description": item["product_description"],
             "production_unit": item["unit"], "factor_kg_per_unit": factor["factor_kg"],
             "extraction_method": factor["method"]},
            dependencies=[{"indicator_id": production_id, "role": "production_base", "weight": factor["factor_kg"],
                           "metadata": {"operation": "multiply_by_theoretical_factor"}}],
        )
        theoretical_ids[op] = iid
        theoretical_values[op] = value
        theoretical_confidence[op] = confidence
        if value is None:
            _audit(conn, calc_id, "WARNING", "THEORETICAL_WEIGHT_NOT_IDENTIFIED", "OP", op,
                   "The theoretical product weight could not be identified from the official description.",
                   metadata={"product_code": item["product_code"], "description": item["product_description"],
                             "production_unit": item["unit"], "method": factor["method"]})
        elif factor["confidence"] == "ESTIMADO_DENSIDADE_PARAMETRIZADA":
            _audit(conn, calc_id, "WARNING", "PROVISIONAL_DENSITY_PARAMETER", "PRODUCT", item["product_code"],
                   "The theoretical product weight uses a provisional density parameter.",
                   official=factor["factor_kg"], metadata={"op": op, "description": item["product_description"],
                                                         "method": factor["method"]})

    # 2. Consumption in original units, both strategies ---------------------
    raw_material_groups = {normalize_text(x).casefold() for x in cfg.raw["parameters"].get("raw_material_groups", [])}
    class_cache: dict[tuple[str, str], dict[str, Any]] = {}
    raw_indicators: dict[tuple[str, str], int] = {}
    raw_values: dict[tuple[str, str], float | None] = {}
    raw_confidence: dict[tuple[str, str], str] = {}

    for strategy in cfg.raw["parameters"]["consumption_strategies"]:
        for op in selected:
            where = "AND recorded_at>=? AND recorded_at<=?" if strategy == "operational_window" else ""
            args: list[Any] = [op]
            if where:
                args += [cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds")]
            rows = conn.execute(
                f"""SELECT source_record_id,transaction_code,product_code,product_description,unit,quantity,recorded_at,
                CASE WHEN transaction_code='90251' THEN quantity ELSE -quantity END contribution
                FROM movements WHERE document_op=? AND transaction_code IN ('90251','90202') {where}
                ORDER BY recorded_at,source_record_id""", args,
            ).fetchall()
            grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
            for row in rows:
                grouped[(row["product_code"], row["product_description"], row["unit"])].append(row)
            for (component, desc, unit), group in grouped.items():
                value = sum(r["contribution"] for r in group)
                lineage = [{"source_record_id": r["source_record_id"], "contribution": r["contribution"],
                            "metadata": {"transaction": r["transaction_code"], "role": "consumption_original_unit",
                                         "strategy": strategy}} for r in group]
                add_indicator(f"CONSUMPTION_NET_{strategy.upper()}", "OP_COMPONENT", f"{op}|{component}|{unit}",
                              value, unit, "CONFIRMADO_CONTABILMENTE", "CONSUMPTION_NET",
                              {"op": op, "component_code": component, "description": desc, "strategy": strategy,
                               "active": strategy == cfg.consumption_strategy}, lineage)

            kg_total = 0.0
            kg_lineage: list[dict[str, Any]] = []
            confidence_parts: list[str] = []
            pending_records = 0
            included_records = 0
            for row in rows:
                classification = _resolve_component_classification(conn, row["product_code"], row["product_description"], cfg, class_cache)
                group = classification.get("group")
                if not group or normalize_text(group).casefold() not in raw_material_groups:
                    if classification["confidence"].startswith("PENDENTE"):
                        pending_records += 1
                        _audit(conn, calc_id, "WARNING", "CONSUMPTION_CLASSIFICATION_PENDING", "MOVEMENT",
                               str(row["source_record_id"]), "Movement was not included in raw-material kg because classification is unresolved",
                               metadata={"op": op, "component": row["product_code"], "description": row["product_description"],
                                         "strategy": strategy, "classification": classification})
                    continue
                conversion = _convert_contribution_to_kg(row["product_code"], row["product_description"], row["unit"],
                                                         row["contribution"], group, cfg)
                confidence_parts.extend([classification["confidence"], conversion["confidence"]])
                if conversion["kg"] is None:
                    pending_records += 1
                    _audit(conn, calc_id, "WARNING", "CONSUMPTION_CONVERSION_PENDING", "MOVEMENT",
                           str(row["source_record_id"]), "Raw-material movement could not be converted to kg",
                           metadata={"op": op, "component": row["product_code"], "unit": row["unit"],
                                     "description": row["product_description"], "conversion": conversion})
                    continue
                included_records += 1
                kg_total += conversion["kg"]
                kg_lineage.append({"source_record_id": row["source_record_id"], "contribution": conversion["kg"],
                                   "metadata": {"role": "raw_material_consumption", "strategy": strategy,
                                                "component_code": row["product_code"], "description": row["product_description"],
                                                "original_quantity": row["contribution"], "original_unit": row["unit"],
                                                "classification": classification, "conversion": conversion}})
                if classification.get("source_record_id"):
                    kg_lineage.append({"source_record_id": classification["source_record_id"], "contribution": 0.0,
                                       "metadata": {"role": "classification_evidence", "component_code": row["product_code"]}})
            if strategy == "operational_window" and included_records:
                confidence_parts.append("PENDENTE_ATRIBUICAO_TEMPORAL_BAIXA_MANUAL")
            confidence = _combine_confidence(confidence_parts, "PENDENTE_SEM_CONSUMO_MP" if not included_records else "CONFIRMADO")
            code = f"RAW_MATERIAL_CONSUMPTION_KG_{strategy.upper()}"
            empty_scope_dependencies = [] if kg_lineage else [{
                "indicator_id": indicator_ids[("PRODUCTION_NET", "OP", op)],
                "role": "evaluated_op_scope",
                "metadata": {"evidence_type": "absence_of_matching_consumption_records", "strategy": strategy},
            }]
            iid = add_indicator(code, "OP", op, kg_total if included_records or kg_total else 0.0, "KG", confidence,
                                "CONSUMPTION_RAW_KG", {"strategy": strategy, "included_records": included_records,
                                                       "pending_records": pending_records, "raw_material_groups": sorted(raw_material_groups),
                                                       "active": strategy == cfg.consumption_strategy,
                                                       "zero_result_evidence": "no matching eligible records" if not kg_lineage else None},
                                kg_lineage, dependencies=empty_scope_dependencies)
            raw_indicators[(op, strategy)] = iid
            raw_values[(op, strategy)] = kg_total if included_records or kg_total else 0.0
            raw_confidence[(op, strategy)] = confidence

    # 3. Accumulated OP consumption prorated to operational production -------
    prorated_ids: dict[str, int] = {}
    prorated_values: dict[str, float | None] = {}
    prorated_confidence: dict[str, str] = {}
    for op, item in selected.items():
        accumulated_id = raw_indicators[(op, "op_accumulated")]
        accumulated_value = raw_values[(op, "op_accumulated")]
        mpop = conn.execute("SELECT * FROM mpop_orders WHERE op=? ORDER BY id LIMIT 1", (op,)).fetchone()
        direct_lineage: list[dict[str, Any]] = []
        if not mpop or not mpop["actual_quantity"]:
            value = None
            factor = None
            confidence = "PENDENTE_DENOMINADOR_RATEIO"
            _audit(conn, calc_id, "WARNING", "MPOP_ACTUAL_NOT_FOUND_FOR_RATEIO", "OP", op,
                   "Accumulated OP consumption could not be prorated because MPOP actual quantity was not found")
        else:
            factor = item["production_net"] / mpop["actual_quantity"]
            value = accumulated_value * factor if accumulated_value is not None else None
            confidence = _combine_confidence([raw_confidence[(op, "op_accumulated")], "ESTIMADO_RATEADO_PRODUCAO"])
            direct_lineage.append({"source_record_id": mpop["source_record_id"], "contribution": 0.0,
                                   "metadata": {"role": "rateio_denominator", "mpop_actual_quantity": mpop["actual_quantity"],
                                                "operational_production": item["production_net"], "rateio_factor": factor}})
        iid = add_indicator(
            "RAW_MATERIAL_CONSUMPTION_KG_PRORATED", "OP", op, value, "KG", confidence,
            "CONSUMPTION_PRORATED",
            {"strategy": "op_accumulated", "allocation_mode": "production_ratio", "factor": factor,
             "mpop_actual_quantity": mpop["actual_quantity"] if mpop else None},
            lineage=direct_lineage,
            dependencies=[{"indicator_id": accumulated_id, "role": "accumulated_consumption", "weight": factor,
                           "metadata": {"operation": "multiply_by_rateio_factor"}}],
        )
        prorated_ids[op] = iid
        prorated_values[op] = value
        prorated_confidence[op] = confidence

    # 4. Active consumption alias --------------------------------------------
    active_consumption_ids: dict[str, int] = {}
    active_consumption_values: dict[str, float | None] = {}
    active_consumption_confidence: dict[str, str] = {}
    allocation_mode = cfg.raw["parameters"].get("accumulated_allocation_mode", "production_ratio")
    for op in selected:
        if cfg.consumption_strategy == "operational_window":
            source_id = raw_indicators[(op, "operational_window")]
            value = raw_values[(op, "operational_window")]
            confidence = raw_confidence[(op, "operational_window")]
            selected_strategy = "operational_window"
        elif allocation_mode == "production_ratio":
            source_id = prorated_ids[op]
            value = prorated_values[op]
            confidence = prorated_confidence[op]
            selected_strategy = "op_accumulated_prorated"
        else:
            source_id = raw_indicators[(op, "op_accumulated")]
            value = raw_values[(op, "op_accumulated")]
            confidence = raw_confidence[(op, "op_accumulated")]
            selected_strategy = "op_accumulated"
        iid = add_indicator(
            "RAW_MATERIAL_CONSUMPTION_KG", "OP", op, value, "KG", confidence,
            "CONSUMPTION_RAW_KG",
            {"active_strategy": selected_strategy, "configured_consumption_strategy": cfg.consumption_strategy,
             "allocation_mode": allocation_mode},
            dependencies=[{"indicator_id": source_id, "role": "active_consumption", "weight": 1.0,
                           "metadata": {"alias": True}}],
        )
        active_consumption_ids[op] = iid
        active_consumption_values[op] = value
        active_consumption_confidence[op] = confidence

    # 4b. Consumption variance against theoretical ---------------------------
    variance_ids: dict[str, int] = {}
    for op in selected:
        real_value = active_consumption_values[op]
        theoretical_value = theoretical_values[op]
        value = None if real_value is None or theoretical_value is None else real_value - theoretical_value
        confidence = _combine_confidence([active_consumption_confidence[op], theoretical_confidence[op]])
        variance_ids[op] = add_indicator(
            "CONSUMPTION_VARIANCE_KG", "OP", op, value, "KG", confidence,
            "CONSUMPTION_VARIANCE", {"active_consumption_strategy": cfg.consumption_strategy},
            dependencies=[
                {"indicator_id": active_consumption_ids[op], "role": "actual_consumption", "weight": 1.0},
                {"indicator_id": theoretical_ids[op], "role": "theoretical_consumption", "weight": -1.0},
            ],
        )

    # 5. Repasse --------------------------------------------------------------
    repasse_ids: dict[str, int] = {}
    repasse_values: dict[str, float] = {}
    repasse_confidence: dict[str, str] = {}
    keyword = cfg.raw["parameters"]["repasse_keyword"].upper()
    for op in selected:
        rows = conn.execute(
            """SELECT source_record_id,transaction_code,product_code,product_description,unit,quantity,recorded_at
            FROM movements WHERE document_op=? AND transaction_code='90202' AND UPPER(product_description) LIKE ?
              AND recorded_at>=? AND recorded_at<=? ORDER BY recorded_at,source_record_id""",
            (op, f"%{keyword}%", cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds")),
        ).fetchall()
        total = 0.0
        lineage = []
        pending = 0
        for row in rows:
            capacity = extract_capacity_kg(row["product_description"])
            if capacity is None:
                pending += 1
                _audit(conn, calc_id, "ERROR", "REPASSE_CAPACITY_NOT_FOUND", "OP", op,
                       f"Capacity not found in repasse description {row['product_description']}", metadata={"source_record_id": row["source_record_id"]})
                continue
            contribution = row["quantity"] * capacity
            total += contribution
            lineage.append({"source_record_id": row["source_record_id"], "contribution": contribution,
                            "metadata": {"role": "repasse", "transaction": "90202", "capacity_kg": capacity,
                                         "original_quantity": row["quantity"], "original_unit": row["unit"],
                                         "description": row["product_description"]}})
        confidence = "CONFIRMADO" if pending == 0 else "PENDENTE_CONVERSAO_REPASSE"
        repasse_dependencies = [] if lineage else [{
            "indicator_id": indicator_ids[("PRODUCTION_NET", "OP", op)],
            "role": "evaluated_op_scope",
            "metadata": {"evidence_type": "absence_of_repasse_90202_in_window"},
        }]
        iid = add_indicator("REPASSE_KG", "OP", op, total, "KG", confidence, "REPASSE_KG",
                            {"records": len(rows), "valid_records": len(rows) - pending, "scope": "operational_window",
                             "zero_result_evidence": "no matching 90202 REPASSE records" if not lineage else None},
                            lineage, dependencies=repasse_dependencies)
        repasse_ids[op] = iid
        repasse_values[op] = total
        repasse_confidence[op] = confidence

    # 6. MPOP Loss ------------------------------------------------------------
    loss_ids: dict[str, int] = {}
    loss_values: dict[str, float] = {}
    loss_confidence: dict[str, str] = {}
    for op in selected:
        rows = conn.execute(
            """SELECT source_record_id,component_code,component_description,qty_loss,capacity_kg,loss_kg
            FROM mpop_components WHERE op=? AND qty_loss>0 AND classification_status='CONFIRMADO'
              AND LOWER(classification_group_1)=LOWER('Azeitonas') ORDER BY id""", (op,),
        ).fetchall()
        total = sum((r["loss_kg"] or 0.0) for r in rows)
        confidence = "CONFIRMADO_SEM_HORARIO" if all(r["loss_kg"] is not None for r in rows) else "PENDENTE_CONVERSAO"
        lineage = [{"source_record_id": r["source_record_id"], "contribution": r["loss_kg"] or 0.0,
                    "metadata": {"role": "mpop_loss", "qty_loss_original": r["qty_loss"], "capacity_kg": r["capacity_kg"],
                                 "component_code": r["component_code"], "description": r["component_description"]}} for r in rows]
        loss_dependencies = [] if lineage else [{
            "indicator_id": indicator_ids[("PRODUCTION_NET", "OP", op)],
            "role": "evaluated_op_scope",
            "metadata": {"evidence_type": "absence_of_eligible_mpop_loss_records"},
        }]
        iid = add_indicator("MPOP_LOSS_KG", "OP", op, total, "KG", confidence, "MPOP_LOSS_KG",
                            {"records": len(rows), "scope": "accumulated_op_snapshot", "timestamp_available": False,
                             "zero_result_evidence": "no eligible MPOP loss records" if not lineage else None},
                            lineage, dependencies=loss_dependencies)
        loss_ids[op] = iid
        loss_values[op] = total
        loss_confidence[op] = confidence

    # 7. Loss standard --------------------------------------------------------
    standard_ids: dict[str, int] = {}
    standard_values: dict[str, float | None] = {}
    standard_confidence: dict[str, str] = {}
    for op, item in selected.items():
        standard = _find_loss_standard(conn, item["product_code"], item["product_description"], cfg)
        lineage = []
        if standard.get("source_record_id"):
            lineage.append({"source_record_id": standard["source_record_id"], "contribution": standard["value"],
                            "metadata": {"role": "loss_standard_source", "mapping": standard.get("mapping")}})
        iid = add_indicator("LOSS_STANDARD_PERCENT", "OP", op, standard["value"], "%", standard["confidence"],
                            "LOSS_STANDARD", {"product_code": item["product_code"], "mapping": standard.get("mapping"),
                                              "standard_name": standard.get("standard_name")}, lineage)
        standard_ids[op] = iid
        standard_values[op] = standard["value"]
        standard_confidence[op] = standard["confidence"]
        if standard["value"] is None:
            _audit(conn, calc_id, "WARNING", "LOSS_STANDARD_PENDING", "OP", op,
                   "Official loss standard could not be mapped to the output product", metadata=standard)

    # 8. MP utilized, yield and excess. Both methodological scenarios are kept
    # so a future approval changes only a parameter, never the core code.
    mode_active = cfg.raw["parameters"].get("mp_utilized_formula_mode", "net_minus_loss_minus_repasse")
    modes = cfg.raw["parameters"].get("mp_utilized_formula_modes", ["net_minus_loss", "net_minus_loss_minus_repasse"])
    scenario_ids: dict[tuple[str, str], dict[str, int]] = {}
    scenario_values: dict[tuple[str, str], dict[str, float | None]] = {}

    def calculate_scenario(op: str, consumption_id: int, consumption_value: float | None, consumption_conf: str,
                           scenario_name: str, mode: str) -> dict[str, int]:
        loss = loss_values[op]
        repasse = repasse_values[op]
        if consumption_value is None:
            mp = None
            yield_value = None
            actual_loss = None
        else:
            mp = consumption_value - loss - (repasse if mode == "net_minus_loss_minus_repasse" else 0.0)
            yield_value = mp / consumption_value if abs(consumption_value) > 1e-12 else None
            actual_loss = 1.0 - yield_value if yield_value is not None else None
        conf = _combine_confidence([consumption_conf, loss_confidence[op], repasse_confidence[op]])
        mp_dependencies = [
            {"indicator_id": consumption_id, "role": "consumption", "weight": 1.0},
            {"indicator_id": loss_ids[op], "role": "loss", "weight": -1.0},
        ]
        if mode == "net_minus_loss_minus_repasse":
            mp_dependencies.append({"indicator_id": repasse_ids[op], "role": "repasse", "weight": -1.0})
        mp_id = add_indicator(
            f"MP_UTILIZED_KG_{scenario_name}_{mode.upper()}", "OP", op, mp, "KG", conf,
            "MP_UTILIZED_KG",
            {"scenario": scenario_name, "formula_mode": mode,
             "warning": "Repasse is already part of consumption net; this mode is kept configurable and audited" if mode.endswith("repasse") else None},
            dependencies=mp_dependencies,
        )
        yield_id = add_indicator(
            f"OPERATIONAL_YIELD_{scenario_name}_{mode.upper()}", "OP", op, yield_value, "%", conf,
            "OPERATIONAL_YIELD", {"scenario": scenario_name, "formula_mode": mode},
            dependencies=[
                {"indicator_id": mp_id, "role": "numerator", "metadata": {"operation": "division"}},
                {"indicator_id": consumption_id, "role": "denominator", "metadata": {"operation": "division"}},
            ],
        )
        loss_rate_id = add_indicator(
            f"ACTUAL_LOSS_RATE_{scenario_name}_{mode.upper()}", "OP", op, actual_loss, "%", conf,
            "OPERATIONAL_YIELD", {"scenario": scenario_name, "formula_mode": mode, "formula": "1 - rendimento"},
            dependencies=[{"indicator_id": yield_id, "role": "yield", "metadata": {"operation": "one_minus"}}],
        )
        standard = standard_values[op]
        excess_pp = None if actual_loss is None or standard is None else actual_loss - standard
        excess_kg = None if consumption_value is None or excess_pp is None else max(0.0, excess_pp * consumption_value)
        excess_conf = _combine_confidence([conf, standard_confidence[op]])
        excess_pp_id = add_indicator(
            f"EXCESS_OVER_STANDARD_PP_{scenario_name}_{mode.upper()}", "OP", op, excess_pp, "PP", excess_conf,
            "EXCESS_OVER_STANDARD", {"scenario": scenario_name, "formula_mode": mode, "signed": True},
            dependencies=[
                {"indicator_id": loss_rate_id, "role": "actual_loss_rate", "weight": 1.0},
                {"indicator_id": standard_ids[op], "role": "loss_standard", "weight": -1.0},
            ],
        )
        excess_kg_id = add_indicator(
            f"EXCESS_OVER_STANDARD_KG_{scenario_name}_{mode.upper()}", "OP", op, excess_kg, "KG", excess_conf,
            "EXCESS_OVER_STANDARD", {"scenario": scenario_name, "formula_mode": mode, "floor_at_zero": True},
            dependencies=[
                {"indicator_id": excess_pp_id, "role": "excess_rate", "metadata": {"operation": "multiply"}},
                {"indicator_id": consumption_id, "role": "consumption_base", "metadata": {"operation": "multiply"}},
            ],
        )
        scenario_values[(op, f"{scenario_name}:{mode}")] = {"mp": mp, "yield": yield_value, "loss_rate": actual_loss,
                                                              "excess_pp": excess_pp, "excess_kg": excess_kg}
        return {"mp": mp_id, "yield": yield_id, "loss_rate": loss_rate_id, "excess_pp": excess_pp_id, "excess_kg": excess_kg_id}

    for op in selected:
        for mode in modes:
            scenario_ids[(op, f"ACTIVE:{mode}")] = calculate_scenario(
                op, active_consumption_ids[op], active_consumption_values[op], active_consumption_confidence[op], "ACTIVE", mode)
            scenario_ids[(op, f"PRORATED:{mode}")] = calculate_scenario(
                op, prorated_ids[op], prorated_values[op], prorated_confidence[op], "PRORATED", mode)

        active = scenario_ids[(op, f"ACTIVE:{mode_active}")]
        active_vals = scenario_values[(op, f"ACTIVE:{mode_active}")]
        active_conf = conn.execute("SELECT confidence FROM indicator_results WHERE id=?", (active["mp"],)).fetchone()[0]
        add_indicator("MP_UTILIZED_KG", "OP", op, active_vals["mp"], "KG", active_conf,
                      "MP_UTILIZED_KG", {"active_formula_mode": mode_active, "active_consumption_strategy": cfg.consumption_strategy},
                      dependencies=[{"indicator_id": active["mp"], "role": "active_scenario", "weight": 1.0}])
        add_indicator("OPERATIONAL_YIELD", "OP", op, active_vals["yield"], "%", active_conf,
                      "OPERATIONAL_YIELD", {"active_formula_mode": mode_active, "active_consumption_strategy": cfg.consumption_strategy},
                      dependencies=[{"indicator_id": active["yield"], "role": "active_scenario", "weight": 1.0}])
        add_indicator("ACTUAL_LOSS_RATE", "OP", op, active_vals["loss_rate"], "%", active_conf,
                      "OPERATIONAL_YIELD", {"active_formula_mode": mode_active},
                      dependencies=[{"indicator_id": active["loss_rate"], "role": "active_scenario", "weight": 1.0}])
        add_indicator("EXCESS_OVER_STANDARD_PP", "OP", op, active_vals["excess_pp"], "PP",
                      _combine_confidence([active_conf, standard_confidence[op]]), "EXCESS_OVER_STANDARD",
                      {"active_formula_mode": mode_active},
                      dependencies=[{"indicator_id": active["excess_pp"], "role": "active_scenario", "weight": 1.0}])
        add_indicator("EXCESS_OVER_STANDARD_KG", "OP", op, active_vals["excess_kg"], "KG",
                      _combine_confidence([active_conf, standard_confidence[op]]), "EXCESS_OVER_STANDARD",
                      {"active_formula_mode": mode_active},
                      dependencies=[{"indicator_id": active["excess_kg"], "role": "active_scenario", "weight": 1.0}])

    # Explicit audit for the possible double deduction of repasse.
    if mode_active == "net_minus_loss_minus_repasse":
        _audit(conn, calc_id, "WARNING", "MP_UTILIZED_REPASSE_DOUBLE_DEDUCTION_RISK", "RULE", "MP_UTILIZED_KG",
               "Active mode subtracts repasse from consumption net even though 90202 is already deducted in CONSUMPTION_NET. The mode is configurable and was not silently changed.",
               metadata={"active_mode": mode_active, "alternative_mode": "net_minus_loss"})

    # 9. Line and operational-day aggregates --------------------------------
    aggregate_codes = ["THEORETICAL_CONSUMPTION_KG", "CONSUMPTION_VARIANCE_KG", "RAW_MATERIAL_CONSUMPTION_KG", "MPOP_LOSS_KG", "REPASSE_KG", "MP_UTILIZED_KG", "EXCESS_OVER_STANDARD_KG"]
    line_ops: dict[str, list[str]] = defaultdict(list)
    for op in selected:
        line_ops[centers[op]["line_key"]].append(op)

    for line_key, ops in line_ops.items():
        for code in aggregate_codes:
            op_rows = [conn.execute(
                "SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code=? AND dimension_type='OP' AND dimension_key=?",
                (calc_id, code, op)).fetchone() for op in ops]
            op_rows = [r for r in op_rows if r]
            value = None if any(r["value"] is None for r in op_rows) else sum(r["value"] for r in op_rows)
            conf = _combine_confidence([r["confidence"] for r in op_rows] + [centers[op]["confidence"] for op in ops])
            add_indicator(
                code, "LINE", line_key, value, op_rows[0]["unit"] if op_rows else "KG", conf,
                op_rows[0]["rule_code"] if op_rows else INDICATOR_CATALOG[code]["rule"],
                {"ops": sorted(ops), "center_assignment": "official_no_distribution"},
                dependencies=[{"indicator_id": r["id"], "role": f"op:{r['dimension_key']}", "weight": 1.0} for r in op_rows],
            )

        # Production is aggregated separately per unit.
        units = {selected[op]["unit"] for op in ops}
        for unit in units:
            rows = [conn.execute(
                "SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='PRODUCTION_NET' AND dimension_type='OP' AND dimension_key=?",
                (calc_id, op)).fetchone() for op in ops if selected[op]["unit"] == unit]
            value = sum(r["value"] for r in rows)
            conf = _combine_confidence([r["confidence"] for r in rows] + [centers[op]["confidence"] for op in ops])
            add_indicator(
                "PRODUCTION_NET", "LINE_UNIT", f"{line_key}|{unit}", value, unit, conf,
                "PRODUCTION_NET", {"line_key": line_key, "ops": sorted(ops), "unit_separated": True},
                dependencies=[{"indicator_id": r["id"], "role": f"op:{r['dimension_key']}", "weight": 1.0} for r in rows],
            )

        cons = conn.execute("SELECT value,confidence,id FROM indicator_results WHERE calculation_run_id=? AND indicator_code='RAW_MATERIAL_CONSUMPTION_KG' AND dimension_type='LINE' AND dimension_key=?", (calc_id, line_key)).fetchone()
        mp = conn.execute("SELECT value,confidence,id FROM indicator_results WHERE calculation_run_id=? AND indicator_code='MP_UTILIZED_KG' AND dimension_type='LINE' AND dimension_key=?", (calc_id, line_key)).fetchone()
        y = None if not cons or cons["value"] in (None, 0) or not mp else mp["value"] / cons["value"]
        conf = _combine_confidence([cons["confidence"] if cons else "PENDENTE", mp["confidence"] if mp else "PENDENTE"])
        dependencies = []
        if mp:
            dependencies.append({"indicator_id": mp["id"], "role": "numerator"})
        if cons:
            dependencies.append({"indicator_id": cons["id"], "role": "denominator"})
        line_yield_id = add_indicator("OPERATIONAL_YIELD", "LINE", line_key, y, "%", conf, "OPERATIONAL_YIELD",
                      {"aggregation": "sum(mp_utilized)/sum(consumption)"}, dependencies=dependencies)
        line_loss = None if y is None else 1.0 - y
        line_loss_id = add_indicator("ACTUAL_LOSS_RATE", "LINE", line_key, line_loss, "%", conf, "OPERATIONAL_YIELD",
                      {"aggregation": "1-line_yield"},
                      dependencies=[{"indicator_id": line_yield_id, "role": "line_yield", "weight": -1.0}])
        standard_rows = []
        for op in ops:
            st = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='LOSS_STANDARD_PERCENT' AND dimension_type='OP' AND dimension_key=?", (calc_id, op)).fetchone()
            co = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='RAW_MATERIAL_CONSUMPTION_KG' AND dimension_type='OP' AND dimension_key=?", (calc_id, op)).fetchone()
            if st and co:
                standard_rows.append((st, co))
        total_weight = sum((co["value"] or 0.0) for st, co in standard_rows)
        line_standard = None if not standard_rows or total_weight <= 0 or any(st["value"] is None for st, co in standard_rows) else sum(st["value"] * co["value"] for st, co in standard_rows) / total_weight
        line_standard_conf = _combine_confidence([st["confidence"] for st, co in standard_rows] + [co["confidence"] for st, co in standard_rows]) if standard_rows else "PENDENTE_PADRAO"
        line_standard_id = add_indicator("LOSS_STANDARD_PERCENT", "LINE", line_key, line_standard, "%", line_standard_conf, "LOSS_STANDARD",
                      {"aggregation": "weighted_by_active_consumption", "ops": sorted(ops)},
                      dependencies=[{"indicator_id": st["id"], "role": f"standard:{st['dimension_key']}"} for st, co in standard_rows] +
                                   [{"indicator_id": co["id"], "role": f"weight:{co['dimension_key']}"} for st, co in standard_rows])
        line_excess_pp = None if line_loss is None or line_standard is None else max(0.0, line_loss - line_standard)
        add_indicator("EXCESS_OVER_STANDARD_PP", "LINE", line_key, line_excess_pp, "PP",
                      _combine_confidence([conf, line_standard_conf]), "EXCESS_OVER_STANDARD",
                      {"aggregation": "max(0,line_loss-line_standard)"},
                      dependencies=[{"indicator_id": line_loss_id, "role": "actual_loss"},
                                    {"indicator_id": line_standard_id, "role": "loss_standard"}])

    # Day totals keep production split by unit.
    for code in aggregate_codes:
        rows = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code=? AND dimension_type='OP'", (calc_id, code)).fetchall()
        value = None if any(r["value"] is None for r in rows) else sum(r["value"] for r in rows)
        conf = _combine_confidence([r["confidence"] for r in rows])
        add_indicator(
            code, "DAY", cfg.operational_day, value, rows[0]["unit"] if rows else "KG", conf,
            rows[0]["rule_code"] if rows else INDICATOR_CATALOG[code]["rule"], {"ops": sorted(selected)},
            dependencies=[{"indicator_id": r["id"], "role": f"op:{r['dimension_key']}", "weight": 1.0} for r in rows],
        )
    for unit in sorted({x["unit"] for x in selected.values()}):
        rows = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='PRODUCTION_NET' AND dimension_type='OP' AND unit=?", (calc_id, unit)).fetchall()
        add_indicator(
            "PRODUCTION_NET", "DAY_UNIT", f"{cfg.operational_day}|{unit}", sum(r["value"] for r in rows), unit,
            _combine_confidence([r["confidence"] for r in rows]), "PRODUCTION_NET", {"unit_separated": True},
            dependencies=[{"indicator_id": r["id"], "role": f"op:{r['dimension_key']}", "weight": 1.0} for r in rows],
        )
    day_cons = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='RAW_MATERIAL_CONSUMPTION_KG' AND dimension_type='DAY'", (calc_id,)).fetchone()
    day_mp = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='MP_UTILIZED_KG' AND dimension_type='DAY'", (calc_id,)).fetchone()
    day_yield = None if not day_cons or day_cons["value"] in (None, 0) or not day_mp else day_mp["value"] / day_cons["value"]
    day_dependencies = []
    if day_mp:
        day_dependencies.append({"indicator_id": day_mp["id"], "role": "numerator"})
    if day_cons:
        day_dependencies.append({"indicator_id": day_cons["id"], "role": "denominator"})
    day_conf = _combine_confidence([day_cons["confidence"] if day_cons else "PENDENTE", day_mp["confidence"] if day_mp else "PENDENTE"])
    day_yield_id = add_indicator("OPERATIONAL_YIELD", "DAY", cfg.operational_day, day_yield, "%",
                  day_conf, "OPERATIONAL_YIELD", {"aggregation": "sum(mp_utilized)/sum(consumption)"}, dependencies=day_dependencies)
    day_loss = None if day_yield is None else 1.0 - day_yield
    day_loss_id = add_indicator("ACTUAL_LOSS_RATE", "DAY", cfg.operational_day, day_loss, "%", day_conf,
                  "OPERATIONAL_YIELD", {"aggregation": "1-day_yield"},
                  dependencies=[{"indicator_id": day_yield_id, "role": "day_yield", "weight": -1.0}])
    standard_rows = []
    for op in selected:
        st = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='LOSS_STANDARD_PERCENT' AND dimension_type='OP' AND dimension_key=?", (calc_id, op)).fetchone()
        co = conn.execute("SELECT * FROM indicator_results WHERE calculation_run_id=? AND indicator_code='RAW_MATERIAL_CONSUMPTION_KG' AND dimension_type='OP' AND dimension_key=?", (calc_id, op)).fetchone()
        if st and co:
            standard_rows.append((st, co))
    total_weight = sum((co["value"] or 0.0) for st, co in standard_rows)
    day_standard = None if not standard_rows or total_weight <= 0 or any(st["value"] is None for st, co in standard_rows) else sum(st["value"] * co["value"] for st, co in standard_rows) / total_weight
    day_standard_conf = _combine_confidence([st["confidence"] for st, co in standard_rows] + [co["confidence"] for st, co in standard_rows]) if standard_rows else "PENDENTE_PADRAO"
    day_standard_id = add_indicator("LOSS_STANDARD_PERCENT", "DAY", cfg.operational_day, day_standard, "%", day_standard_conf,
                  "LOSS_STANDARD", {"aggregation": "weighted_by_active_consumption", "ops": sorted(selected)},
                  dependencies=[{"indicator_id": st["id"], "role": f"standard:{st['dimension_key']}"} for st, co in standard_rows] +
                               [{"indicator_id": co["id"], "role": f"weight:{co['dimension_key']}"} for st, co in standard_rows])
    day_excess_pp = None if day_loss is None or day_standard is None else max(0.0, day_loss - day_standard)
    add_indicator("EXCESS_OVER_STANDARD_PP", "DAY", cfg.operational_day, day_excess_pp, "PP",
                  _combine_confidence([day_conf, day_standard_conf]), "EXCESS_OVER_STANDARD",
                  {"aggregation": "max(0,day_loss-day_standard)"},
                  dependencies=[{"indicator_id": day_loss_id, "role": "actual_loss"},
                                {"indicator_id": day_standard_id, "role": "loss_standard"}])

    # 10. Center audit and reference comparison ------------------------------
    for op in selected:
        center = centers[op]
        if not center["centers"]:
            _audit(conn, calc_id, "WARNING", "CENTER_RESOURCE_NOT_FOUND", "OP", op,
                   "No center resource was found in the official resource file")
        elif len(center["all_centers"]) > 1:
            _audit(conn, calc_id, "WARNING", "MULTI_CENTER_OP", "OP", op,
                   "OP has more than one official center resource in its execution history and was not automatically distributed",
                   official=",".join(center["all_centers"]), metadata=center)

    _compare_reference(conn, calc_id, selected, centers, mode_active)

    conn.execute(
        "INSERT INTO events(event_type,occurred_at,recorded_at,operational_day,confidence,payload_json) VALUES(?,?,?,?,?,?)",
        ("DIA_OPERACIONAL_FECHADO", cfg.end.isoformat(timespec="seconds"), UTC_NOW(), cfg.operational_day, "CALCULADO",
         _json({"calculation_run_id": calc_id, "selected_ops": sorted(selected), "engine": "analytical"})),
    )
    conn.execute("UPDATE calculation_runs SET completed_at=?,status='SUCCESS' WHERE id=?", (UTC_NOW(), calc_id))
    return calc_id


def _compare_reference(conn: sqlite3.Connection, calc_id: int, selected: dict[str, dict[str, Any]],
                       centers: dict[str, dict[str, Any]], active_mode: str) -> None:
    refs = {r["op"]: r for r in conn.execute("SELECT * FROM reference_op_results")}
    for op, official in selected.items():
        ref = refs.get(op)
        if not ref:
            _audit(conn, calc_id, "WARNING", "OP_NOT_IN_REFERENCE", "OP", op,
                   "Official OP is absent from the reference report", official=official["production_net"])
            continue
        comparisons = [
            ("PRODUCTION_REFERENCE", "PRODUCTION_NET", official["production_net"], ref["production"]),
            ("CONSUMPTION_PRORATED_REFERENCE", "RAW_MATERIAL_CONSUMPTION_KG_PRORATED", None, ref["real_kg"]),
            ("LOSS_REFERENCE", "MPOP_LOSS_KG", None, ref["loss_kg"]),
            ("REPASSE_REFERENCE", "REPASSE_KG", None, ref["repasse_kg"]),
            ("STANDARD_REFERENCE", "LOSS_STANDARD_PERCENT", None, ref["standard_percent"]),
            ("YIELD_PRORATED_REFERENCE", f"OPERATIONAL_YIELD_PRORATED_{active_mode.upper()}", None, ref["yield_operational"]),
        ]
        for audit_prefix, code, direct, reference in comparisons:
            value = direct
            if direct is None:
                row = conn.execute("SELECT value FROM indicator_results WHERE calculation_run_id=? AND indicator_code=? AND dimension_type='OP' AND dimension_key=?",
                                   (calc_id, code, op)).fetchone()
                value = row[0] if row else None
            diff = None if value is None or reference is None else value - reference
            severity = "INFO" if diff is not None and abs(diff) <= 1e-5 else "WARNING"
            _audit(conn, calc_id, severity, f"{audit_prefix}_COMPARISON", "OP", op,
                   "Official-source result compared with reference; reference never changes the result",
                   official=value, reference=reference, difference=diff, metadata={"indicator_code": code})

        official_center = ",".join(centers[op]["centers"])
        ref_center = ref["center_resource"] or ""
        if official_center != ref_center:
            _audit(conn, calc_id, "WARNING", "CENTER_REFERENCE_DIVERGENCE", "OP", op,
                   "Official center resource differs from reference; official source prevails",
                   official=official_center, reference=ref_center, metadata=centers[op])

    for op in sorted(set(refs) - set(selected)):
        _audit(conn, calc_id, "WARNING", "REFERENCE_OP_NOT_SELECTED", "OP", op,
               "Reference OP was not selected by official origin/window parameters", reference=refs[op]["production"])
