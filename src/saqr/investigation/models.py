from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class InvestigationRecord:
    investigation_id: str
    calculation_id: int
    primary_indicator_id: int
    operational_day: str | None
    company: str | None
    production_order: str | None
    resource_center: str | None
    line: str | None
    product_code: str | None
    product_description: str | None
    title: str
    description: str | None
    origin: str | None
    stage: str | None
    status: str
    confidence: str | None
    owner: str | None
    due_date: str | None
    engine_version: str | None
    rule_versions_json: str | None
    indicator_snapshot_json: str | None
    origin_snapshot_json: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class InvestigationIndicatorRecord:
    id: int
    investigation_id: str
    indicator_id: int
    indicator_value: float | None
    unit: str | None
    confidence: str | None
    rule_version: str | None
    snapshot_json: str | None
    created_at: str


@dataclass(slots=True)
class InvestigationHistoryRecord:
    id: int
    investigation_id: str
    event_type: str
    payload_json: str | None
    created_at: str
