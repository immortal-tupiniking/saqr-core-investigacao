from __future__ import annotations

from pathlib import Path
import sqlite3

SCHEMA = r"""
PRAGMA foreign_keys = ON;

-- Provenance / ingestion -----------------------------------------------------
CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_read INTEGER NOT NULL DEFAULT 0,
    validation_errors INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS source_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    source_row TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(import_run_id, source_row)
);
CREATE TABLE IF NOT EXISTS validation_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_run_id INTEGER REFERENCES import_runs(id),
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    entity TEXT,
    message TEXT NOT NULL,
    context_json TEXT,
    created_at TEXT NOT NULL
);

-- Canonical domain ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS operational_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operational_date TEXT NOT NULL UNIQUE,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_code TEXT NOT NULL UNIQUE,
    name TEXT,
    source_type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code TEXT NOT NULL,
    derivation TEXT NOT NULL DEFAULT '',
    description TEXT,
    family_id INTEGER REFERENCES families(id),
    source_record_id INTEGER REFERENCES source_records(id),
    confidence TEXT NOT NULL,
    metadata_json TEXT,
    UNIQUE(product_code, derivation)
);
CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component_code TEXT NOT NULL,
    derivation TEXT NOT NULL DEFAULT '',
    preferred_description TEXT,
    description_status TEXT NOT NULL,
    source_record_id INTEGER REFERENCES source_records(id),
    metadata_json TEXT,
    UNIQUE(component_code, derivation)
);
CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classification_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    level INTEGER NOT NULL,
    parent_id INTEGER REFERENCES classifications(id),
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS component_classification_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id INTEGER NOT NULL REFERENCES components(id),
    classification_id INTEGER REFERENCES classifications(id),
    level_name TEXT NOT NULL,
    assignment_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_record_id INTEGER REFERENCES source_records(id),
    effective_from TEXT,
    effective_to TEXT,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_component_assignments_component ON component_classification_assignments(component_id);

CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS resource_centers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    center_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS resource_center_lines (
    resource_center_id INTEGER NOT NULL REFERENCES resource_centers(id),
    line_id INTEGER NOT NULL REFERENCES lines(id),
    effective_from TEXT,
    effective_to TEXT,
    confidence TEXT NOT NULL,
    metadata_json TEXT,
    PRIMARY KEY(resource_center_id, line_id, effective_from)
);
CREATE TABLE IF NOT EXISTS production_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_key TEXT NOT NULL UNIQUE,
    op_number TEXT NOT NULL,
    origin_type TEXT,
    output_product_id INTEGER REFERENCES products(id),
    operational_day_id INTEGER REFERENCES operational_days(id),
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    metadata_json TEXT
);

-- Source-normalized facts ---------------------------------------------------
CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    transaction_code TEXT NOT NULL,
    document_op TEXT,
    op_origin TEXT,
    product_code TEXT,
    product_description TEXT,
    movement_date TEXT,
    recorded_at TEXT,
    quantity REAL NOT NULL,
    unit TEXT,
    direction TEXT,
    family TEXT,
    product_origin TEXT,
    sequence TEXT,
    derivation TEXT,
    branch TEXT
);
CREATE INDEX IF NOT EXISTS idx_movements_op ON movements(document_op);
CREATE INDEX IF NOT EXISTS idx_movements_recorded ON movements(recorded_at);
CREATE INDEX IF NOT EXISTS idx_movements_trans ON movements(transaction_code);

-- Execucao da OP is represented by this canonical fact table. No duplicate
-- order-execution table is created.
CREATE TABLE IF NOT EXISTS resource_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    op TEXT NOT NULL,
    product_code TEXT,
    stage TEXT,
    operation TEXT,
    sequence TEXT,
    center_resource TEXT,
    start_at TEXT,
    end_at TEXT,
    quantity_first_quality REAL,
    operator TEXT,
    shift TEXT
);
CREATE INDEX IF NOT EXISTS idx_resource_op ON resource_executions(op);
CREATE INDEX IF NOT EXISTS idx_resource_center ON resource_executions(center_resource);

-- Category source observations. These remain immutable observations; the
-- canonical assignments above preserve conflicts instead of choosing silently.
CREATE TABLE IF NOT EXISTS component_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    component_code TEXT NOT NULL,
    description TEXT,
    family TEXT,
    group_1 TEXT,
    group_2 TEXT,
    group_3 TEXT
);
CREATE INDEX IF NOT EXISTS idx_component_code ON component_classifications(component_code);

CREATE TABLE IF NOT EXISTS loss_standard_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    row_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loss_standards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_code TEXT NOT NULL,
    name TEXT NOT NULL,
    standard_percent REAL NOT NULL,
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    effective_from TEXT,
    effective_to TEXT,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    metadata_json TEXT,
    UNIQUE(standard_code, effective_from)
);
CREATE INDEX IF NOT EXISTS idx_loss_standard_code ON loss_standards(standard_code);

CREATE TABLE IF NOT EXISTS mpop_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    origin TEXT,
    op TEXT NOT NULL,
    output_product_code TEXT,
    output_product_description TEXT,
    forecast_quantity REAL,
    actual_quantity REAL,
    period TEXT,
    hours_production TEXT,
    hours_setup TEXT,
    UNIQUE(op, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_mpop_orders_op ON mpop_orders(op);
CREATE TABLE IF NOT EXISTS mpop_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    origin TEXT,
    op TEXT NOT NULL,
    output_product_code TEXT,
    output_product_description TEXT,
    component_code TEXT NOT NULL,
    component_description TEXT,
    qty_consumed REAL,
    qty_returned REAL,
    qty_net REAL,
    qty_loss REAL,
    loss_percent REAL,
    nonconform REAL,
    capacity_kg REAL,
    loss_kg REAL,
    classification_group_1 TEXT,
    classification_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_mpop_op ON mpop_components(op);

-- Reference is deliberately isolated. No analytical query is allowed to use
-- it as a calculation input.
CREATE TABLE IF NOT EXISTS reference_op_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op TEXT NOT NULL,
    center_resource TEXT,
    line_name TEXT,
    product_code TEXT,
    family TEXT,
    production REAL,
    unit TEXT,
    theoretical_kg REAL,
    real_kg REAL,
    loss_kg REAL,
    repasse_kg REAL,
    yield_operational REAL,
    standard_percent REAL,
    status TEXT
);

-- Event-driven core ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    occurred_at TEXT,
    recorded_at TEXT NOT NULL,
    operational_day TEXT,
    op TEXT,
    product_code TEXT,
    component_code TEXT,
    center_resource TEXT,
    quantity REAL,
    unit TEXT,
    confidence TEXT NOT NULL,
    source_record_id INTEGER REFERENCES source_records(id),
    rule_code TEXT,
    rule_version TEXT,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_op ON events(op);

-- Rules, parameters, calculations, indicators and audit --------------------
CREATE TABLE IF NOT EXISTS rule_versions (
    rule_code TEXT NOT NULL,
    version TEXT NOT NULL,
    formula TEXT NOT NULL,
    effective_from TEXT,
    config_json TEXT NOT NULL,
    PRIMARY KEY(rule_code, version)
);
CREATE TABLE IF NOT EXISTS parameters (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calculation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    operational_day TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    config_snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calculation_run_parameters (
    calculation_run_id INTEGER NOT NULL REFERENCES calculation_runs(id),
    parameter_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY(calculation_run_id, parameter_key)
);
CREATE TABLE IF NOT EXISTS indicator_catalog (
    indicator_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    objective TEXT NOT NULL,
    formula_description TEXT NOT NULL,
    default_unit TEXT,
    frequency TEXT NOT NULL,
    official_source TEXT NOT NULL,
    responsible TEXT NOT NULL,
    confidence_policy TEXT NOT NULL,
    rule_code TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS indicator_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calculation_run_id INTEGER NOT NULL REFERENCES calculation_runs(id),
    indicator_code TEXT NOT NULL,
    dimension_type TEXT NOT NULL,
    dimension_key TEXT NOT NULL,
    value REAL,
    unit TEXT,
    confidence TEXT NOT NULL,
    rule_code TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_indicator_code ON indicator_results(indicator_code);
CREATE INDEX IF NOT EXISTS idx_indicator_dimension ON indicator_results(dimension_type, dimension_key);
CREATE TABLE IF NOT EXISTS indicator_lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_result_id INTEGER NOT NULL REFERENCES indicator_results(id),
    source_record_id INTEGER REFERENCES source_records(id),
    event_id INTEGER REFERENCES events(id),
    contribution REAL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_lineage_indicator ON indicator_lineage(indicator_result_id);
CREATE TABLE IF NOT EXISTS indicator_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_result_id INTEGER NOT NULL REFERENCES indicator_results(id),
    depends_on_indicator_id INTEGER NOT NULL REFERENCES indicator_results(id),
    role TEXT NOT NULL,
    weight REAL,
    metadata_json TEXT,
    UNIQUE(indicator_result_id, depends_on_indicator_id, role)
);
CREATE INDEX IF NOT EXISTS idx_indicator_dependencies_result ON indicator_dependencies(indicator_result_id);
CREATE INDEX IF NOT EXISTS idx_indicator_dependencies_parent ON indicator_dependencies(depends_on_indicator_id);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calculation_run_id INTEGER REFERENCES calculation_runs(id),
    severity TEXT NOT NULL,
    audit_code TEXT NOT NULL,
    entity_type TEXT,
    entity_key TEXT,
    message TEXT NOT NULL,
    official_value TEXT,
    reference_value TEXT,
    difference TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_calc ON audit_log(calculation_run_id);

-- Domain projections. These views are semantic names over canonical facts,
-- preventing duplicate persistence of Produção, Consumo, Lixo and Repasse.
CREATE VIEW IF NOT EXISTS industrial_events AS
SELECT * FROM events;
CREATE VIEW IF NOT EXISTS production_facts AS
SELECT * FROM events WHERE event_type IN ('PRODUCAO_APONTADA','PRODUCAO_ESTORNADA');
CREATE VIEW IF NOT EXISTS consumption_facts AS
SELECT * FROM events WHERE event_type IN ('CONSUMO_REALIZADO','DEVOLUCAO_REALIZADA');
CREATE VIEW IF NOT EXISTS loss_facts AS
SELECT * FROM events WHERE event_type IN ('PERDA_REGISTRADA','PERDA_MPOP_NAO_INCLUIDA_NO_LIXO_MP');
CREATE VIEW IF NOT EXISTS repasse_facts AS
SELECT * FROM events WHERE event_type='REPASSE_REGISTRADO';
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
