from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from saqr.investigation.schemas import CreateInvestigationRequest, InvestigationListOut, InvestigationOut
from saqr.investigation.service import InvestigationService

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


def _get_service() -> InvestigationService:
    from app.main import DB_PATH, INVESTIGATION_DB_PATH, app

    data_db_path = getattr(app.state, "data_db_path", DB_PATH)
    investigation_db_path = getattr(app.state, "investigation_db_path", INVESTIGATION_DB_PATH)
    return InvestigationService(data_db_path, investigation_db_path)


@router.post("", response_model=InvestigationOut)
def create_investigation(payload: CreateInvestigationRequest) -> dict[str, Any]:
    service = _get_service()
    try:
        return service.create_investigation(payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=InvestigationListOut)
def list_investigations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    operational_day: str | None = None,
    company: str | None = None,
    line: str | None = None,
    production_order: str | None = None,
    product: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    confidence: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    service = _get_service()
    filters = {
        "operational_day": operational_day,
        "company": company,
        "line": line,
        "production_order": production_order,
        "product_code": product,
        "stage": stage,
        "status": status,
        "confidence": confidence,
        "owner": owner,
    }
    return service.list_investigations(filters, page, page_size)


@router.get("/{investigation_id}", response_model=InvestigationOut)
def get_investigation(investigation_id: str) -> dict[str, Any]:
    service = _get_service()
    try:
        return service.get_investigation(investigation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="investigation not found") from exc
