from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateInvestigationRequest(BaseModel):
    loss_id: str | None = Field(default=None, min_length=1)
    calculation_id: int
    indicator_id: int
    title: str = Field(min_length=1)
    description: str | None = None
    company: str | None = None
    production_order: str | None = None
    resource_center: str | None = None
    line: str | None = None
    product_code: str | None = None
    product_description: str | None = None
    origin: str | None = None
    stage: str | None = None
    owner: str | None = None
    due_date: str | None = None


class InvestigationIndicatorOut(BaseModel):
    indicator_id: int
    value: float | None
    unit: str | None
    confidence: str | None
    rule_version: str | None
    snapshot: dict[str, Any] | None = None


class InvestigationHistoryOut(BaseModel):
    event_type: str
    payload: dict[str, Any] | None = None
    created_at: str


class InvestigationOut(BaseModel):
    loss_id: str | None = None
    investigation_id: str
    calculation_id: int
    primary_indicator_id: int
    operational_day: str | None = None
    company: str | None = None
    production_order: str | None = None
    resource_center: str | None = None
    line: str | None = None
    product_code: str | None = None
    product_description: str | None = None
    title: str
    description: str | None = None
    origin: str | None = None
    stage: str | None = None
    status: str
    confidence: str | None = None
    owner: str | None = None
    due_date: str | None = None
    versions: dict[str, Any]
    indicator: InvestigationIndicatorOut
    history: list[InvestigationHistoryOut]
    created_at: str
    updated_at: str


class InvestigationListItem(BaseModel):
    investigation_id: str
    title: str
    status: str
    confidence: str | None = None
    operational_day: str | None = None
    company: str | None = None
    line: str | None = None
    production_order: str | None = None
    product_code: str | None = None
    stage: str | None = None
    created_at: str


class InvestigationListOut(BaseModel):
    page: int
    page_size: int
    total_count: int
    items: list[InvestigationListItem]

