from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as web
from saqr.database import connect


@pytest.fixture()
def client_and_dbs(tmp_path, monkeypatch):
    data_db = tmp_path / "data.sqlite"
    investigation_dir = tmp_path / "investigations"
    investigation_dir.mkdir(parents=True)
    investigation_db = investigation_dir / "investigation.sqlite"
    conn = connect(data_db)
    conn.execute(
        """
        INSERT INTO calculation_runs (
            id, started_at, completed_at, operational_day, engine_version, config_snapshot_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            10,
            "2026-07-19T00:00:00",
            "2026-07-19T00:10:00",
            "2026-07-19",
            "1.2.3",
            json.dumps({"parameters": {"ui_strategy_key": "operational_window"}}),
            "SUCCESS",
        ),
    )
    conn.execute(
        """
        INSERT INTO indicator_results (
            id, calculation_run_id, indicator_code, dimension_type, dimension_key, value, unit, confidence,
            rule_code, rule_version, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            101,
            10,
            "RAW_MATERIAL_CONSUMPTION_KG",
            "OP",
            "OP-1001",
            123.45,
            "KG",
            "CONFIRMADO",
            "CONSUMPTION_RAW_KG",
            "v1",
            json.dumps({"product_code": "P1", "product_description": "Produto X"}),
        ),
    )
    conn.execute(
        "INSERT INTO rule_versions (rule_code, version, formula, effective_from, config_json) VALUES (?, ?, ?, ?, ?)",
        ("CONSUMPTION_RAW_KG", "v1", "SUM(...)", "2026-01-01", json.dumps({"mode": "test"})),
    )
    conn.execute(
        "INSERT INTO indicator_catalog (indicator_code, name, objective, formula_description, default_unit, frequency, official_source, responsible, confidence_policy, rule_code, active, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "RAW_MATERIAL_CONSUMPTION_KG",
            "Consumo real",
            "Objetivo",
            "SOMA",
            "KG",
            "DIA_OPERACIONAL",
            "Movimentações",
            "Equipe",
            "Propagação",
            "CONSUMPTION_RAW_KG",
            1,
            json.dumps({"module": "test"}),
        ),
    )
    conn.execute(
        "INSERT INTO import_runs (id, source_type, filename, sha256, imported_at, status, rows_read, validation_errors, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "movements", "movements.xlsx", "abc", "2026-07-19T00:00:00", "SUCCESS", 2, 0, json.dumps({"path": "x"})),
    )
    conn.execute(
        "INSERT INTO source_records (id, import_run_id, source_row, record_hash, raw_json) VALUES (?, ?, ?, ?, ?)",
        (1, 1, "1", "hash1", json.dumps({"row": 1})),
    )
    conn.execute(
        "INSERT INTO events (id, event_type, occurred_at, recorded_at, operational_day, op, product_code, component_code, center_resource, quantity, unit, confidence, source_record_id, rule_code, rule_version, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "PRODUCAO_APONTADA", "2026-07-19T00:01:00", "2026-07-19T00:01:00", "2026-07-19", "OP-1001", "P1", None, "RC1", 1.0, "KG", "CONFIRMADO", 1, "CONSUMPTION_RAW_KG", "v1", json.dumps({"source": "test"})),
    )
    conn.execute(
        "INSERT INTO indicator_lineage (id, indicator_result_id, source_record_id, event_id, contribution, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (1, 101, 1, 1, 1.0, json.dumps({"role": "source"})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(web, "DB_PATH", data_db)
    monkeypatch.setattr(web, "INVESTIGATION_DB_PATH", investigation_db)
    monkeypatch.setenv("SAQR_INVESTIGATION_DB_PATH", str(investigation_db))
    web.app.state.data_db_path = data_db
    web.app.state.investigation_db_path = investigation_db

    return TestClient(web.app), data_db, investigation_db


def test_create_investigation_success(client_and_dbs):
    client, _, _ = client_and_dbs
    response = client.post(
        "/api/investigations",
        json={"calculation_id": 10, "indicator_id": 101, "title": "Investigar consumo"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["calculation_id"] == 10
    assert payload["primary_indicator_id"] == 101
    assert payload["title"] == "Investigar consumo"
    assert payload["status"] == "OPEN"
    assert payload["indicator"]["value"] == 123.45
    assert payload["indicator"]["confidence"] == "CONFIRMADO"
    assert payload["versions"]["engine_version"] == "1.2.3"
    assert payload["history"][0]["event_type"] == "INVESTIGATION_CREATED"
    assert re.fullmatch(r"INV-[A-Z]{4}-\d{4}", payload["investigation_id"])


def test_create_investigation_with_unknown_calculation(client_and_dbs):
    client, _, _ = client_and_dbs
    response = client.post(
        "/api/investigations",
        json={"calculation_id": 999, "indicator_id": 101, "title": "Falha"},
    )
    assert response.status_code == 404


def test_create_investigation_with_unsuccessful_calculation(client_and_dbs):
    client, _, _ = client_and_dbs
    conn = connect(web.app.state.data_db_path)
    conn.execute("UPDATE calculation_runs SET status='RUNNING' WHERE id=10")
    conn.commit()
    conn.close()

    response = client.post(
        "/api/investigations",
        json={"calculation_id": 10, "indicator_id": 101, "title": "Falha"},
    )
    assert response.status_code == 422


def test_create_investigation_with_unknown_indicator(client_and_dbs):
    client, _, _ = client_and_dbs
    response = client.post(
        "/api/investigations",
        json={"calculation_id": 10, "indicator_id": 999, "title": "Falha"},
    )
    assert response.status_code == 404


def test_list_investigations_is_paginated_and_filtered(client_and_dbs):
    client, _, _ = client_and_dbs
    client.post("/api/investigations", json={"calculation_id": 10, "indicator_id": 101, "title": "Primeira"})
    client.post("/api/investigations", json={"calculation_id": 10, "indicator_id": 101, "title": "Segunda"})

    response = client.get("/api/investigations?page=1&page_size=1&status=OPEN")
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 1
    assert payload["total_count"] == 2
    assert len(payload["items"]) == 1

    response = client.get("/api/investigations?line=OP-1001")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_count"] == 2


def test_get_investigation_by_id(client_and_dbs):
    client, _, _ = client_and_dbs
    created = client.post(
        "/api/investigations",
        json={"calculation_id": 10, "indicator_id": 101, "title": "Detalhes"},
    ).json()

    detail = client.get(f"/api/investigations/{created['investigation_id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["investigation_id"] == created["investigation_id"]
    assert payload["calculation_id"] == 10
    assert payload["indicator"]["indicator_id"] == 101
    assert payload["history"][0]["event_type"] == "INVESTIGATION_CREATED"


def test_get_unknown_investigation_returns_404(client_and_dbs):
    client, _, _ = client_and_dbs
    response = client.get("/api/investigations/INV-XXXX-9999")
    assert response.status_code == 404


def test_investigation_db_is_separate_from_data_db(client_and_dbs):
    client, data_db, investigation_db = client_and_dbs
    response = client.post(
        "/api/investigations",
        json={"calculation_id": 10, "indicator_id": 101, "title": "Banco separado"},
    )
    assert response.status_code == 200
    assert data_db.exists()
    assert investigation_db.exists()
    assert data_db != investigation_db

    data_conn = connect(data_db)
    investigation_conn = connect(investigation_db)
    try:
        data_tables = {row[0] for row in data_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        investigation_tables = {row[0] for row in investigation_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"investigations", "investigation_indicators", "investigation_history"} & data_tables
        assert {"investigations", "investigation_indicators", "investigation_history"}.issubset(investigation_tables)
    finally:
        data_conn.close()
        investigation_conn.close()


def test_snapshot_preserves_value_confidence_and_versions(client_and_dbs):
    client, _, _ = client_and_dbs
    response = client.post(
        "/api/investigations",
        json={"calculation_id": 10, "indicator_id": 101, "title": "Snapshot"},
    )
    payload = response.json()
    assert payload["indicator"]["value"] == 123.45
    assert payload["indicator"]["confidence"] == "CONFIRMADO"
    assert payload["versions"]["engine_version"] == "1.2.3"
    assert payload["versions"]["rules"][0]["version"] == "v1"


def test_replacing_data_db_does_not_delete_investigations(client_and_dbs):
    client, data_db, _ = client_and_dbs
    created = client.post(
        "/api/investigations",
        json={"calculation_id": 10, "indicator_id": 101, "title": "Persistente"},
    ).json()

    replacement_db = data_db.parent / "replacement.sqlite"
    replacement_conn = connect(replacement_db)
    replacement_conn.commit()
    replacement_conn.close()
    web.app.state.data_db_path = replacement_db

    detail = client.get(f"/api/investigations/{created['investigation_id']}")
    assert detail.status_code == 200
    assert detail.json()["investigation_id"] == created["investigation_id"]


def test_loss_id_survives_create_list_and_open_without_replacing_indicator(client_and_dbs):
    client, _, _ = client_and_dbs
    created = client.post('/api/investigations', json={
        'calculation_id': 10, 'indicator_id': 101, 'title': 'Rastreabilidade isolada',
        'loss_id': '000007', 'production_order': '001001', 'line': '830',
    })
    assert created.status_code == 200
    payload = created.json()
    assert payload['loss_id'] == '000007'
    assert payload['calculation_id'] == 10
    assert payload['primary_indicator_id'] == 101
    assert payload['production_order'] == '001001'
    listed = client.get('/api/investigations', params={'production_order': '001001'}).json()
    assert listed['total_count'] == 1
    investigation_id = listed['items'][0]['investigation_id']
    reopened = client.get(f'/api/investigations/{investigation_id}').json()
    assert reopened == payload
    assert reopened['history'][0]['payload']['loss_id'] == '000007'
    assert reopened['indicator']['snapshot']['lineage'][0]['source_record_id'] == 1


def test_loss_id_alone_is_rejected_without_writing(client_and_dbs):
    client, _, _ = client_and_dbs
    response = client.post('/api/investigations', json={'loss_id': '101'})
    assert response.status_code == 422
    assert client.get('/api/investigations').json()['total_count'] == 0


def test_indicator_must_belong_to_selected_calculation(client_and_dbs):
    client, data_db, _ = client_and_dbs
    conn = connect(data_db)
    conn.execute("INSERT INTO calculation_runs (id, started_at, operational_day, engine_version, config_snapshot_json, status) VALUES (11, '2026-07-20', '2026-07-20', '1.2.3', '{}', 'SUCCESS')")
    conn.commit()
    conn.close()
    response = client.post('/api/investigations', json={'calculation_id': 11, 'indicator_id': 101, 'title': 'Vínculo inválido'})
    assert response.status_code == 404
    assert client.get('/api/investigations').json()['total_count'] == 0
