from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from .config import EngineConfig
from .utils import normalize_header, normalize_text

UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _upsert_classification(conn: sqlite3.Connection, name: str, level: int, parent_id: int | None) -> int:
    code = f"L{level}:{normalize_header(name)}"
    conn.execute(
        """INSERT OR IGNORE INTO classifications(classification_code,name,level,parent_id,metadata_json)
        VALUES(?,?,?,?,?)""",
        (code, name, level, parent_id, _json({"origin": "Categorias.xlsx"})),
    )
    row = conn.execute("SELECT id FROM classifications WHERE classification_code=?", (code,)).fetchone()
    return int(row[0])


def materialize_domain(conn: sqlite3.Connection, cfg: EngineConfig) -> dict[str, int]:
    """Build the canonical domain projection from immutable normalized facts.

    This function does not calculate business indicators. It creates stable
    domain identities so that future modules can reuse the same Core.
    """
    now = UTC_NOW()
    conn.execute(
        """INSERT OR REPLACE INTO operational_days(operational_date,start_at,end_at,status,created_at,closed_at,metadata_json)
        VALUES(?,?,?,?,?,?,?)""",
        (cfg.operational_day, cfg.start.isoformat(timespec="seconds"), cfg.end.isoformat(timespec="seconds"),
         "FECHADO_PARA_CALCULO", now, cfg.end.isoformat(timespec="seconds"), _json({"source": "configuration"})),
    )
    day_id = int(conn.execute("SELECT id FROM operational_days WHERE operational_date=?", (cfg.operational_day,)).fetchone()[0])

    # Families are official ERP family codes. Analytical loss classes remain a
    # separate Classification concept to avoid semantic collision.
    for row in conn.execute("SELECT DISTINCT TRIM(COALESCE(family,'')) family FROM movements WHERE TRIM(COALESCE(family,''))<>''"):
        code = row["family"]
        conn.execute(
            "INSERT OR IGNORE INTO families(family_code,name,source_type,confidence,metadata_json) VALUES(?,?,?,?,?)",
            (code, f"FAMILIA ERP {code}", "MOVIMENTACOES", "CONFIRMADO", _json({})),
        )

    family_ids = {r["family_code"]: r["id"] for r in conn.execute("SELECT id,family_code FROM families")}

    # Products are output items observed in production movements and MPOP.
    product_candidates: dict[tuple[str, str], list[tuple[str, str, int | None]]] = defaultdict(list)
    for row in conn.execute(
        """SELECT product_code,COALESCE(derivation,'' ) derivation,product_description,family,source_record_id
        FROM movements WHERE transaction_code IN ('90201','90252') AND TRIM(COALESCE(product_code,''))<>''"""
    ):
        product_candidates[(row["product_code"], row["derivation"])].append((row["product_description"], row["family"], row["source_record_id"]))
    for row in conn.execute(
        "SELECT output_product_code,output_product_description,source_record_id FROM mpop_orders WHERE TRIM(COALESCE(output_product_code,''))<>''"
    ):
        product_candidates[(row["output_product_code"], "")].append((row["output_product_description"], "", row["source_record_id"]))

    for (code, derivation), values in product_candidates.items():
        descriptions = [normalize_text(v[0]) for v in values if normalize_text(v[0])]
        preferred = Counter(descriptions).most_common(1)[0][0] if descriptions else ""
        family_code = next((v[1] for v in values if v[1]), "")
        src = next((v[2] for v in values if v[2]), None)
        confidence = "CONFIRMADO" if len(set(descriptions)) <= 1 else "CONFIRMADO_COM_VARIACOES_DESCRICAO"
        conn.execute(
            """INSERT OR REPLACE INTO products(product_code,derivation,description,family_id,source_record_id,confidence,metadata_json)
            VALUES(?,?,?,?,?,?,?)""",
            (code, derivation, preferred, family_ids.get(family_code), src, confidence,
             _json({"observed_descriptions": sorted(set(descriptions)), "family_code": family_code})),
        )

    # Components are input items. No source classification is silently selected.
    component_candidates: dict[str, list[tuple[str, int | None]]] = defaultdict(list)
    for row in conn.execute(
        """SELECT product_code code,product_description description,source_record_id FROM movements
        WHERE transaction_code IN ('90251','90202') AND TRIM(COALESCE(product_code,''))<>''"""
    ):
        component_candidates[row["code"]].append((row["description"], row["source_record_id"]))
    for row in conn.execute("SELECT component_code code,description,source_record_id FROM component_classifications"):
        component_candidates[row["code"]].append((row["description"], row["source_record_id"]))
    for row in conn.execute("SELECT component_code code,component_description description,source_record_id FROM mpop_components"):
        component_candidates[row["code"]].append((row["description"], row["source_record_id"]))

    for code, values in component_candidates.items():
        descriptions = [normalize_text(v[0]) for v in values if normalize_text(v[0])]
        preferred = Counter(descriptions).most_common(1)[0][0] if descriptions else ""
        status = "UNICA" if len(set(descriptions)) <= 1 else "MULTIPLAS_DESCRICOES"
        src = next((v[1] for v in values if v[1]), None)
        conn.execute(
            """INSERT OR REPLACE INTO components(component_code,derivation,preferred_description,description_status,source_record_id,metadata_json)
            VALUES(?,?,?,?,?,?)""",
            (code, "", preferred, status, src, _json({"observed_descriptions": sorted(set(descriptions))})),
        )

    component_ids = {r["component_code"]: r["id"] for r in conn.execute("SELECT id,component_code FROM components")}

    # Classification observations are materialized as assignments. Conflicting
    # assignments coexist and are resolved only by the analytical confidence logic.
    for row in conn.execute("SELECT * FROM component_classifications ORDER BY id"):
        component_id = component_ids.get(row["component_code"])
        if not component_id:
            continue
        parent_id = None
        for level, field in ((1, "group_1"), (2, "group_2"), (3, "group_3")):
            name = normalize_text(row[field])
            if not name:
                continue
            class_id = _upsert_classification(conn, name, level, parent_id)
            conn.execute(
                """INSERT INTO component_classification_assignments(component_id,classification_id,level_name,
                assignment_status,confidence,source_record_id,effective_from,effective_to,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (component_id, class_id, f"GROUP_{level}", "OBSERVADO", "CONFIRMADO_FONTE",
                 row["source_record_id"], None, None, _json({"category_observation_id": row["id"]})),
            )
            parent_id = class_id

    # Resource Center and Line are distinct domain entities even when the
    # initial mapping is one-to-one.
    configured_names = cfg.raw.get("parameters", {}).get("line_names", {})
    center_codes = {r[0] for r in conn.execute("SELECT DISTINCT center_resource FROM resource_executions WHERE TRIM(COALESCE(center_resource,''))<>''")}
    center_codes.update(str(k) for k in configured_names)
    for code in sorted(center_codes):
        name = configured_names.get(code, f"CENTRO {code}")
        conn.execute(
            "INSERT OR IGNORE INTO resource_centers(center_code,name,source_type,status,metadata_json) VALUES(?,?,?,?,?)",
            (code, name, "CENTRO_RECURSO", "ATIVO", _json({})),
        )
        conn.execute(
            "INSERT OR IGNORE INTO lines(line_code,name,status,metadata_json) VALUES(?,?,?,?)",
            (code, name, "ATIVA", _json({"initial_mapping": "one_center_one_line"})),
        )
        center_id = int(conn.execute("SELECT id FROM resource_centers WHERE center_code=?", (code,)).fetchone()[0])
        line_id = int(conn.execute("SELECT id FROM lines WHERE line_code=?", (code,)).fetchone()[0])
        conn.execute(
            """INSERT OR IGNORE INTO resource_center_lines(resource_center_id,line_id,effective_from,effective_to,confidence,metadata_json)
            VALUES(?,?,?,?,?,?)""",
            (center_id, line_id, cfg.start.date().isoformat(), None, "PARAMETRIZADO", _json({"parameter": "line_names"})),
        )

    # Production Orders are canonical identities. The key contains origin to
    # prevent collisions between companies/origins with similar numbering.
    product_ids = {(r["product_code"], r["derivation"]): r["id"] for r in conn.execute("SELECT id,product_code,derivation FROM products")}
    selected_origins = set(cfg.considered_origins)
    rows = conn.execute(
        """SELECT document_op op,op_origin,product_code,COALESCE(derivation,'') derivation,
        MIN(recorded_at) first_record,MAX(recorded_at) last_record
        FROM movements WHERE TRIM(COALESCE(document_op,''))<>'' GROUP BY document_op,op_origin,product_code,COALESCE(derivation,'')"""
    ).fetchall()
    for row in rows:
        op_key = f"{row['op_origin']}:{row['op']}"
        in_window = bool(row["last_record"] and row["first_record"] and not (row["last_record"] < cfg.start.isoformat(timespec="seconds") or row["first_record"] > cfg.end.isoformat(timespec="seconds")))
        status = "NO_ESCOPO_DIA" if in_window and row["op_origin"] in selected_origins else "IMPORTADA"
        conn.execute(
            """INSERT OR REPLACE INTO production_orders(op_key,op_number,origin_type,output_product_id,operational_day_id,status,confidence,metadata_json)
            VALUES(?,?,?,?,?,?,?,?)""",
            (op_key, row["op"], row["op_origin"], product_ids.get((row["product_code"], row["derivation"])), day_id,
             status, "CONFIRMADO", _json({"first_record": row["first_record"], "last_record": row["last_record"]})),
        )

    return {
        "operational_days": conn.execute("SELECT COUNT(*) FROM operational_days").fetchone()[0],
        "families": conn.execute("SELECT COUNT(*) FROM families").fetchone()[0],
        "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "components": conn.execute("SELECT COUNT(*) FROM components").fetchone()[0],
        "classifications": conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
        "classification_assignments": conn.execute("SELECT COUNT(*) FROM component_classification_assignments").fetchone()[0],
        "resource_centers": conn.execute("SELECT COUNT(*) FROM resource_centers").fetchone()[0],
        "lines": conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0],
        "production_orders": conn.execute("SELECT COUNT(*) FROM production_orders").fetchone()[0],
    }
