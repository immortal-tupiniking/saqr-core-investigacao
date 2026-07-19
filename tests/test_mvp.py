from __future__ import annotations

from pathlib import Path
import os
import shutil

import pytest
from fastapi.testclient import TestClient

import app.main as web

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SOURCE_FILES = (
    'movimentação diárias.xlsx',
    'Movimentações com centro de recurso.xlsx',
    'MPOP301.PDF',
    'perda padrão atual VF 03.2026.xlsx',
    'Categorias.xlsx',
    'Analise_MPOP_Diaria_10-07-2026_Por_Linha_e_OP_CORRIGIDA.xlsx',
)


def _resolve_test_source_root():
    candidates = []
    env_root = os.getenv('SAQR_TEST_SOURCE_ROOT')
    if env_root:
        candidates.append(Path(env_root).expanduser())

    candidates.append(ROOT / 'workspace' / 'test_sources')

    if Path('/mnt/data').exists():
        candidates.append(Path('/mnt/data'))

    seen = set()
    for candidate in candidates:
        candidate_path = candidate.expanduser()
        if not candidate_path.exists() or not candidate_path.is_dir():
            continue

        resolved_path = candidate_path.resolve()
        if resolved_path in seen:
            continue
        seen.add(resolved_path)

        missing = [name for name in REQUIRED_SOURCE_FILES if not (resolved_path / name).exists()]
        if not missing:
            return resolved_path

    checked_paths = [path.resolve() for path in candidates if path.exists() and path.is_dir()]
    missing_files = sorted({
        name
        for path in checked_paths
        for name in REQUIRED_SOURCE_FILES
        if not (path / name).exists()
    })
    pytest.skip(
        'O teste integral depende das fontes externas de homologação. '
        f'Arquivos ausentes: {", ".join(missing_files) if missing_files else "nenhum arquivo localizado"}.'
    )


def test_navigation_and_analytical_views():
    with TestClient(web.app) as client:
        state = client.get('/api/state')
        assert state.status_code == 200
        payload = state.json()
        assert len(payload['imports']) == 6
        calc_id = payload['calculation_id']

        summary = client.get(f'/api/summary?calc_id={calc_id}').json()
        production = next(x for x in summary['metrics'] if x['code'] == 'PRODUCTION_BY_UNIT')
        assert set(production['value_by_unit']) == {'BD', 'BR', 'CX'}
        assert summary['row_count'] == 16

        lines = client.get(f'/api/lines?calc_id={calc_id}').json()
        assert lines
        assert all('production_by_unit' in row for row in lines)

        ops = client.get(f'/api/ops?calc_id={calc_id}').json()
        assert len(ops) == 16
        assert all('theoretical_consumption_kg' in row for row in ops)
        assert all('indicator_ids' in row for row in ops)

        detail = client.get(f'/api/ops/39629/components?calc_id={calc_id}').json()
        assert 'Produção' in detail['sections']
        assert 'Matéria-prima' in detail['sections']
        assert 'Lixo' in detail['sections']
        assert 'Repasse' in detail['sections']

        indicator_id = ops[0]['indicator_ids']['production']
        explanation = client.get(f'/api/explain/{indicator_id}').json()
        assert explanation['lineage_summary']['source_records'] > 0
        assert explanation['source_files']


def test_three_consumption_strategies_are_motor_requests():
    with TestClient(web.app) as client:
        values = {}
        for strategy in ('operational_window', 'op_accumulated', 'op_accumulated_prorated'):
            response = client.post('/api/strategy', json={'strategy': strategy})
            assert response.status_code == 200
            calc_id = response.json()['calculation_id']
            summary = client.get(f'/api/summary?calc_id={calc_id}').json()
            metric = next(x for x in summary['metrics'] if x['code'] == 'RAW_MATERIAL_CONSUMPTION_KG')
            values[strategy] = metric['value']
        assert values['operational_window'] != values['op_accumulated']
        assert values['op_accumulated_prorated'] != values['op_accumulated']


def test_upload_endpoint_imports_six_sources(tmp_path, monkeypatch):
    db = tmp_path / 'saqr.sqlite'
    workspace = tmp_path / 'workspace'
    uploads = workspace / 'uploads'
    output = workspace / 'output'
    uploads.mkdir(parents=True)
    output.mkdir(parents=True)
    monkeypatch.setattr(web, 'DB_PATH', db)
    monkeypatch.setattr(web, 'WORKSPACE', workspace)
    monkeypatch.setattr(web, 'UPLOADS', uploads)
    monkeypatch.setattr(web, 'OUTPUT', output)

    source_root = _resolve_test_source_root()
    files = {
        'movements': ('movimentação diárias.xlsx', open(source_root / 'movimentação diárias.xlsx', 'rb'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'resources': ('Movimentações com centro de recurso.xlsx', open(source_root / 'Movimentações com centro de recurso.xlsx', 'rb'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'mpop': ('MPOP301.PDF', open(source_root / 'MPOP301.PDF', 'rb'), 'application/pdf'),
        'loss_standards': ('perda padrão atual VF 03.2026.xlsx', open(source_root / 'perda padrão atual VF 03.2026.xlsx', 'rb'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'categories': ('Categorias.xlsx', open(source_root / 'Categorias.xlsx', 'rb'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'reference': ('Analise_MPOP_Diaria_10-07-2026_Por_Linha_e_OP_CORRIGIDA.xlsx', open(source_root / 'Analise_MPOP_Diaria_10-07-2026_Por_Linha_e_OP_CORRIGIDA.xlsx', 'rb'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    }
    try:
        with TestClient(web.app) as client:
            response = client.post('/api/import', files=files)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload['status'] == 'SUCCESS'
            assert len(payload['imports']) == 6
            assert all(x['status'].startswith('SUCCESS') for x in payload['imports'])
            summary = client.get(f"/api/summary?calc_id={payload['calculation_id']}").json()
            assert summary['row_count'] == 16
    finally:
        for _, file_tuple in files.items():
            file_tuple[1].close()


def test_mode_auditor_explains_aggregate_kpi():
    with TestClient(web.app) as client:
        summary = client.get('/api/summary').json()
        metric = next(x for x in summary['metrics'] if x['code'] == 'RAW_MATERIAL_CONSUMPTION_KG')
        response = client.post('/api/explain-aggregate', json={
            'metric_code': metric['code'],
            'indicator_ids': metric['source_indicator_ids'],
            'filters': {},
        })
        assert response.status_code == 200
        payload = response.json()
        assert payload['source_indicators']
        assert payload['source_record_count'] > 0
        assert 'nenhuma soma ou fórmula foi executada na interface' in payload['aggregation']


def test_frontend_contains_no_business_formulas():
    forbidden = ('90201', '90202', '90251', '90252', 'Qtd. Perda')
    for relative in ('app/templates/index.html', 'app/static/app.js'):
        content = (ROOT / relative).read_text(encoding='utf-8')
        assert not any(token in content for token in forbidden)
    with TestClient(web.app) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert 'Valores preparados pelo Motor Analítico' in page.text
