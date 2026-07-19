from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import app.main as web


def approx(value: float | None, expected: float, tolerance: float = 1e-8) -> bool:
    return value is not None and abs(float(value) - expected) <= tolerance


def check(name: str, passed: bool, expected=None, obtained=None, details: str = "") -> dict:
    return {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "expected": expected,
        "obtained": obtained,
        "details": details,
    }


def main() -> int:
    checks: list[dict] = []
    required_sections = [
        "Produção", "Matéria-prima", "Salmoura", "Aditivos",
        "Azeites e óleos", "Embalagens", "Lixo", "Repasse",
    ]

    with tempfile.TemporaryDirectory() as tmp:
        temp_db = Path(tmp) / "saqr.sqlite"
        shutil.copy2(ROOT / "data.sqlite", temp_db)
        original_db = web.DB_PATH
        web.DB_PATH = temp_db
        try:
            with TestClient(web.app) as client:
                endpoints = [
                    "/api/health", "/api/state", "/api/imports", "/api/summary",
                    "/api/lines", "/api/ops", "/api/losses", "/api/repasse", "/api/audit",
                ]
                statuses = {path: client.get(path).status_code for path in endpoints}
                checks.append(check("Endpoints principais disponíveis", all(v == 200 for v in statuses.values()), "HTTP 200", statuses))

                state = client.get("/api/state").json()
                imports = state["imports"]
                checks.append(check("Seis fontes registradas", len(imports) == 6, 6, len(imports)))
                checks.append(check(
                    "Cinco fontes oficiais importadas com estrutura processável",
                    all(x["status"].startswith("SUCCESS") for x in imports if not x["optional"]),
                    "SUCCESS ou SUCCESS_WITH_ISSUES", {x["source_type"]: x["status"] for x in imports if not x["optional"]},
                ))
                checks.append(check("Hashes SHA-256 disponíveis", all(len(x.get("sha256") or "") == 64 for x in imports), "64 caracteres por fonte", [len(x.get("sha256") or "") for x in imports]))

                summary = client.get("/api/summary").json()
                metrics = {m["code"]: m for m in summary["metrics"]}
                production = metrics["PRODUCTION_BY_UNIT"]["value_by_unit"]
                checks.append(check("16 OPs navegáveis", summary["row_count"] == 16, 16, summary["row_count"]))
                checks.append(check("Unidades de produção separadas", set(production) == {"BD", "BR", "CX"}, ["BD", "BR", "CX"], sorted(production)))
                checks.append(check("Produção BD", approx(production.get("BD"), 80.0), 80.0, production.get("BD")))
                checks.append(check("Produção BR", approx(production.get("BR"), 45.0), 45.0, production.get("BR")))
                checks.append(check("Produção CX", approx(production.get("CX"), 12970.0), 12970.0, production.get("CX")))
                checks.append(check("Lixo MPOP homologado", approx(metrics["MPOP_LOSS_KG"]["value"], 292.90955, 1e-7), 292.90955, metrics["MPOP_LOSS_KG"]["value"]))

                ops = client.get("/api/ops").json()
                checks.append(check("Visão por OP completa", len(ops) == 16, 16, len(ops)))
                detail = client.get("/api/ops/39629/components").json()
                sections = list(detail["sections"].keys())
                checks.append(check("Oito categorias no detalhamento", all(x in sections for x in required_sections), required_sections, sections))

                indicator_ids = metrics["RAW_MATERIAL_CONSUMPTION_KG"]["source_indicator_ids"]
                explanation = client.post("/api/explain-aggregate", json={
                    "metric_code": "RAW_MATERIAL_CONSUMPTION_KG",
                    "indicator_ids": indicator_ids,
                    "filters": {},
                }).json()
                checks.append(check("KPI explicável até os registros", explanation.get("source_record_count", 0) > 0, "> 0", explanation.get("source_record_count")))
                checks.append(check("Indicadores-fonte versionados", all(i.get("rule_version") for i in explanation.get("source_indicators", [])), "versão em todos", len(explanation.get("source_indicators", []))))

                audit = client.get("/api/audit").json()
                required_audits = [
                    "componentes_conflitantes", "ops_sem_centro", "ops_multicentro",
                    "perdas_sem_classificacao", "movimentos_fora_janela",
                    "divergencias_referencia", "parametros_provisorios",
                    "itens_sem_peso", "possiveis_duplas_deducoes",
                ]
                checks.append(check("Categorias de auditoria disponíveis", all(k in audit for k in required_audits), required_audits, list(audit)))
                checks.append(check("Divergências preservadas", audit["divergencias_referencia"]["count"] > 0, "> 0", audit["divergencias_referencia"]["count"]))

                strategy_keys = {item["key"] for item in state["strategies"]}
                expected_strategies = {"operational_window", "op_accumulated", "op_accumulated_prorated"}
                checks.append(check("Três estratégias publicadas pelo Motor Analítico", strategy_keys == expected_strategies, sorted(expected_strategies), sorted(strategy_keys), "A execução de troca é coberta pela suíte automatizada."))
        finally:
            web.DB_PATH = original_db

    forbidden = ("90201", "90202", "90251", "90252", "Qtd. Perda")
    frontend_files = [ROOT / "app/templates/index.html", ROOT / "app/static/app.js"]
    found = {str(p.relative_to(ROOT)): [token for token in forbidden if token in p.read_text(encoding="utf-8")] for p in frontend_files}
    checks.append(check("Interface sem regras industriais codificadas", not any(found.values()), "nenhum token de regra", found))

    conn = sqlite3.connect(ROOT / "data.sqlite")
    reference_lineage = conn.execute(
        """SELECT COUNT(*) FROM indicator_lineage l
        JOIN source_records s ON s.id=l.source_record_id
        JOIN import_runs i ON i.id=s.import_run_id
        WHERE i.source_type='reference'"""
    ).fetchone()[0]
    confidence_values = {r[0] for r in conn.execute("SELECT DISTINCT confidence FROM indicator_results") if r[0]}
    conn.close()
    checks.append(check("Relatório de referência fora da linhagem de cálculo", reference_lineage == 0, 0, reference_lineage))
    checks.append(check(
        "Classificações de confiança preservadas",
        any("CONFIRM" in x for x in confidence_values) and any("ESTIM" in x for x in confidence_values) and any("PENDENTE" in x for x in confidence_values),
        "confirmado, estimado e pendente", sorted(confidence_values),
    ))

    passed = sum(1 for item in checks if item["status"] == "PASS")
    result = {
        "increment": "03",
        "version": "0.3.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "PASS" if passed == len(checks) else "FAIL",
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }
    target = ROOT / "validation_result.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
