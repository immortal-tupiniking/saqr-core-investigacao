from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from saqr.config import EngineConfig, load_config
from saqr.database import connect
from saqr.explain import explain_indicator
from saqr.mvp import STRATEGY_LABELS, config_for_strategy, ensure_mvp_schema, find_or_create_calculation, materialize_component_results
from saqr.pipeline import run_pipeline
from saqr.query import (
    audit_overview,
    calculation_context,
    executive_summary,
    filter_options,
    filtered_line_rows,
    filtered_op_rows,
    import_status,
    line_rows,
    op_components,
    op_rows,
)

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data.sqlite"
DEFAULT_CONFIG = ROOT / "config" / "default.json"
WORKSPACE = ROOT / "workspace"
UPLOADS = WORKSPACE / "uploads"
OUTPUT = WORKSPACE / "output"
DB_PATH = Path(os.environ.get("SAQR_DB_PATH", DEFAULT_DB))
CONFIG_PATH = Path(os.environ.get("SAQR_CONFIG_PATH", DEFAULT_CONFIG))

@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_application()
    yield


app = FastAPI(title="SAQR MVP", version="0.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
engine_lock = threading.Lock()


@contextmanager
def db_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Banco SAQR ainda não foi criado. Importe as fontes.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_mvp_schema(conn)
        yield conn
    finally:
        conn.close()


def latest_calc_id(conn: sqlite3.Connection) -> int:
    # A abertura do MVP usa a estratégia padrão configurada, mesmo quando
    # outros cenários já foram pré-calculados no mesmo banco.
    try:
        base_cfg = load_config(CONFIG_PATH)
        default_strategy = (
            "operational_window"
            if base_cfg.consumption_strategy == "operational_window"
            else "op_accumulated_prorated"
            if base_cfg.raw.get("parameters", {}).get("accumulated_allocation_mode") == "production_ratio"
            else "op_accumulated"
        )
        preferred = config_for_strategy(base_cfg, default_strategy)
        snapshot = json.dumps(preferred.raw, ensure_ascii=False, sort_keys=True)
        row = conn.execute(
            "SELECT id FROM calculation_runs WHERE status='SUCCESS' AND config_snapshot_json=? ORDER BY id DESC LIMIT 1",
            (snapshot,),
        ).fetchone()
        if row:
            return int(row[0])
    except Exception:
        pass
    row = conn.execute("SELECT id FROM calculation_runs WHERE status='SUCCESS' ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        raise HTTPException(status_code=503, detail="Nenhuma execução analítica concluída.")
    return int(row[0])


def resolve_calc_id(conn: sqlite3.Connection, calc_id: int | None) -> int:
    return calc_id if calc_id is not None else latest_calc_id(conn)


def common_filters(company: str | None, line: str | None, op: str | None, product: str | None,
                   confidence: str | None) -> dict[str, str | None]:
    return {"company": company, "line": line, "op": op, "product": product, "confidence": confidence}


def initialize_application() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            ensure_mvp_schema(conn)
            calc = conn.execute("SELECT id,config_snapshot_json FROM calculation_runs WHERE status='SUCCESS' ORDER BY id DESC LIMIT 1").fetchone()
            if calc:
                cfg = EngineConfig(json.loads(calc["config_snapshot_json"]))
                count = conn.execute("SELECT COUNT(*) FROM component_analytical_results WHERE calculation_run_id=?", (calc["id"],)).fetchone()[0]
                if count == 0:
                    materialize_component_results(conn, int(calc["id"]), cfg)
            conn.commit()
        finally:
            conn.close()


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"version": "0.3.0"})


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": "0.3.0", "database": str(DB_PATH), "database_exists": DB_PATH.exists()}


@app.get("/api/state")
def state() -> dict[str, Any]:
    with db_conn() as conn:
        calc_id = latest_calc_id(conn)
        return {
            "calculation_id": calc_id,
            "context": calculation_context(conn, calc_id),
            "imports": import_status(conn),
            "strategies": [{"key": k, "label": v} for k, v in STRATEGY_LABELS.items()],
            "filters": filter_options(conn, calc_id),
        }


@app.get("/api/imports")
def imports() -> list[dict[str, Any]]:
    with db_conn() as conn:
        return import_status(conn)


class StrategyRequest(BaseModel):
    strategy: str


@app.post("/api/strategy")
def switch_strategy(payload: StrategyRequest) -> dict[str, Any]:
    if payload.strategy not in STRATEGY_LABELS:
        raise HTTPException(status_code=422, detail="Estratégia de consumo inválida.")
    with engine_lock:
        base_cfg = load_config(CONFIG_PATH)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            ensure_mvp_schema(conn)
            calc_id, created = find_or_create_calculation(conn, base_cfg, payload.strategy)
            conn.commit()
            return {
                "calculation_id": calc_id,
                "created": created,
                "strategy": payload.strategy,
                "strategy_label": STRATEGY_LABELS[payload.strategy],
                "context": calculation_context(conn, calc_id),
            }
        finally:
            conn.close()


CANONICAL_FILES = {
    "movements": "movimentação diárias.xlsx",
    "resources": "Movimentações com centro de recurso.xlsx",
    "mpop": "MPOP301.PDF",
    "loss_standards": "perda padrão atual VF 03.2026.xlsx",
    "categories": "Categorias.xlsx",
    "reference": "Analise_MPOP_Diaria_10-07-2026_Por_Linha_e_OP_CORRIGIDA.xlsx",
}


async def _save_upload(upload: UploadFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            fh.write(chunk)
    await upload.close()


@app.post("/api/import")
async def import_files(
    movements: Annotated[UploadFile, File(...)],
    resources: Annotated[UploadFile, File(...)],
    mpop: Annotated[UploadFile, File(...)],
    loss_standards: Annotated[UploadFile, File(...)],
    categories: Annotated[UploadFile, File(...)],
    reference: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    uploads = {
        "movements": movements,
        "resources": resources,
        "mpop": mpop,
        "loss_standards": loss_standards,
        "categories": categories,
        "reference": reference,
    }
    for key, upload in uploads.items():
        if upload is None:
            continue
        expected = Path(CANONICAL_FILES[key]).suffix.lower()
        if Path(upload.filename or "").suffix.lower() != expected:
            raise HTTPException(status_code=422, detail=f"Formato inválido para {key}: esperado {expected}.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORKSPACE / f"import_{job_id}"
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    temp_db = job_dir / "saqr.sqlite"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, upload in uploads.items():
        if upload is not None:
            await _save_upload(upload, input_dir / CANONICAL_FILES[key])

    def execute() -> tuple[int, dict[str, Any]]:
        with engine_lock:
            cfg = load_config(CONFIG_PATH)
            calc_id = run_pipeline(input_dir, output_dir, temp_db, cfg)
            conn = sqlite3.connect(temp_db)
            conn.row_factory = sqlite3.Row
            try:
                ensure_mvp_schema(conn)
                materialize_component_results(conn, calc_id, cfg)
                conn.commit()
            finally:
                conn.close()
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            replacement = DB_PATH.with_suffix(DB_PATH.suffix + ".new")
            shutil.copy2(temp_db, replacement)
            os.replace(replacement, DB_PATH)
            current_uploads = UPLOADS / "current"
            if current_uploads.exists():
                shutil.rmtree(current_uploads)
            shutil.copytree(input_dir, current_uploads)
            if OUTPUT.exists():
                shutil.rmtree(OUTPUT)
            shutil.copytree(output_dir, OUTPUT)
            conn2 = sqlite3.connect(DB_PATH)
            conn2.row_factory = sqlite3.Row
            try:
                return calc_id, {"imports": import_status(conn2), "context": calculation_context(conn2, calc_id)}
            finally:
                conn2.close()

    try:
        calc_id, result = await asyncio.to_thread(execute)
        return {"status": "SUCCESS", "calculation_id": calc_id, **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao importar fontes: {exc}") from exc
    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/api/filters")
def filters(calc_id: int | None = None) -> dict[str, Any]:
    with db_conn() as conn:
        return filter_options(conn, resolve_calc_id(conn, calc_id))


@app.get("/api/summary")
def summary(
    calc_id: int | None = None,
    company: str | None = None,
    line: str | None = None,
    op: str | None = None,
    product: str | None = None,
    confidence: str | None = None,
) -> dict[str, Any]:
    with db_conn() as conn:
        cid = resolve_calc_id(conn, calc_id)
        return executive_summary(conn, cid, common_filters(company, line, op, product, confidence))


@app.get("/api/lines")
def lines(
    calc_id: int | None = None,
    company: str | None = None,
    line: str | None = None,
    op: str | None = None,
    product: str | None = None,
    confidence: str | None = None,
) -> list[dict[str, Any]]:
    with db_conn() as conn:
        cid = resolve_calc_id(conn, calc_id)
        return filtered_line_rows(conn, cid, common_filters(company, line, op, product, confidence))


@app.get("/api/ops")
def ops(
    calc_id: int | None = None,
    company: str | None = None,
    line: str | None = None,
    op: str | None = None,
    product: str | None = None,
    confidence: str | None = None,
) -> list[dict[str, Any]]:
    with db_conn() as conn:
        cid = resolve_calc_id(conn, calc_id)
        return filtered_op_rows(conn, cid, common_filters(company, line, op, product, confidence))


@app.get("/api/ops/{op}/components")
def components(op: str, calc_id: int | None = None) -> dict[str, Any]:
    with db_conn() as conn:
        cid = resolve_calc_id(conn, calc_id)
        return op_components(conn, cid, op)


@app.get("/api/losses")
def losses(calc_id: int | None = None, op: str | None = None) -> list[dict[str, Any]]:
    with db_conn() as conn:
        cid = resolve_calc_id(conn, calc_id)
        sql = """SELECT * FROM component_analytical_results WHERE calculation_run_id=? AND section='Lixo'"""
        args: list[Any] = [cid]
        if op:
            sql += " AND op=?"
            args.append(op)
        sql += " ORDER BY op,id"
        return [dict(r) for r in conn.execute(sql, args)]


@app.get("/api/repasse")
def repasse(calc_id: int | None = None, op: str | None = None) -> list[dict[str, Any]]:
    with db_conn() as conn:
        cid = resolve_calc_id(conn, calc_id)
        sql = """SELECT * FROM component_analytical_results WHERE calculation_run_id=? AND section='Repasse'"""
        args: list[Any] = [cid]
        if op:
            sql += " AND op=?"
            args.append(op)
        sql += " ORDER BY op,occurred_at,id"
        return [dict(r) for r in conn.execute(sql, args)]


@app.get("/api/audit")
def audit(calc_id: int | None = None) -> dict[str, Any]:
    with db_conn() as conn:
        return audit_overview(conn, resolve_calc_id(conn, calc_id))


@app.get("/api/explain/{indicator_id}")
def explain(indicator_id: int) -> dict[str, Any]:
    try:
        return explain_indicator(DB_PATH, indicator_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class AggregateExplanationRequest(BaseModel):
    metric_code: str
    indicator_ids: list[int]
    filters: dict[str, Any] = {}


@app.post("/api/explain-aggregate")
def explain_aggregate(payload: AggregateExplanationRequest) -> dict[str, Any]:
    ids = sorted({int(x) for x in payload.indicator_ids if x})
    if not ids:
        return {
            "question": "Como este valor foi calculado?",
            "metric_code": payload.metric_code,
            "filters": payload.filters,
            "message": "O escopo foi avaliado, mas não há indicadores-fonte para este resultado.",
            "source_indicators": [],
            "source_records": [],
        }
    seed_placeholders = ",".join("?" for _ in ids)
    with db_conn() as conn:
        recursive_sql = f"""
        WITH RECURSIVE dependency_ids(id) AS (
            SELECT id FROM indicator_results WHERE id IN ({seed_placeholders})
            UNION
            SELECT d.depends_on_indicator_id
            FROM indicator_dependencies d
            JOIN dependency_ids x ON x.id=d.indicator_result_id
        )
        SELECT DISTINCT id FROM dependency_ids ORDER BY id
        """
        all_ids = [int(r[0]) for r in conn.execute(recursive_sql, ids)]
        placeholders = ",".join("?" for _ in all_ids)
        indicators = [dict(r) for r in conn.execute(
            f"""SELECT i.*,c.name,c.formula_description,c.official_source
            FROM indicator_results i LEFT JOIN indicator_catalog c ON c.indicator_code=i.indicator_code
            WHERE i.id IN ({placeholders}) ORDER BY i.dimension_type,i.dimension_key,i.id""",
            all_ids,
        )]
        source_rows = [dict(r) for r in conn.execute(
            f"""SELECT DISTINCT l.indicator_result_id,s.source_row,s.record_hash,s.raw_json,
            ir.filename,ir.source_type,ir.sha256,e.event_type,e.occurred_at,e.confidence event_confidence
            FROM indicator_lineage l
            LEFT JOIN source_records s ON s.id=l.source_record_id
            LEFT JOIN import_runs ir ON ir.id=s.import_run_id
            LEFT JOIN events e ON e.id=l.event_id
            WHERE l.indicator_result_id IN ({placeholders})
            ORDER BY l.indicator_result_id,s.source_row LIMIT 1000""",
            all_ids,
        )]
        for row in source_rows:
            try:
                row["raw_json"] = json.loads(row.get("raw_json") or "{}")
            except json.JSONDecodeError:
                pass

        calc_ids = sorted({int(i["calculation_run_id"]) for i in indicators})
        dimension_keys = sorted({str(i["dimension_key"]) for i in indicators if i.get("dimension_key")})
        audit_rows: list[dict[str, Any]] = []
        if calc_ids:
            calc_ph = ",".join("?" for _ in calc_ids)
            conditions = ["audit_code LIKE '%' || ? || '%'"]
            args: list[Any] = [*calc_ids, payload.metric_code]
            if dimension_keys:
                key_ph = ",".join("?" for _ in dimension_keys)
                conditions.append(f"entity_key IN ({key_ph})")
                args.extend(dimension_keys)
            audit_rows = [dict(r) for r in conn.execute(
                f"""SELECT DISTINCT * FROM audit_log
                WHERE calculation_run_id IN ({calc_ph})
                  AND ({' OR '.join(conditions)})
                ORDER BY severity DESC,id LIMIT 300""",
                args,
            )]
        return {
            "question": "Como este valor foi calculado?",
            "metric_code": payload.metric_code,
            "filters": payload.filters,
            "aggregation": "Resultado solicitado ao Motor Analítico a partir dos indicadores por OP; nenhuma soma ou fórmula foi executada na interface.",
            "requested_indicator_ids": ids,
            "dependency_indicator_ids": all_ids,
            "source_indicators": indicators,
            "source_records": source_rows,
            "source_record_count": len(source_rows),
            "related_audit": audit_rows,
        }

