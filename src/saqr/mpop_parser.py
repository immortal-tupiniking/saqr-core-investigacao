from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
import shutil
import subprocess

from .utils import parse_br_number, extract_capacity_kg, normalize_text

NUM = r"[0-9.]+,[0-9]+"
COMPONENT_RE = re.compile(
    rf"^\s*({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+(\d{{6}})\s+(.+?)\s*$"
)
OP_RE = re.compile(
    rf"^\s*(\d{{2}})\s+([0-9.]+)\s+(\d{{6}})\s+(.+?)\s+({NUM})\s+({NUM})\s+(\d{{2}}/\d{{4}})\s+(\d+:\d+)\s+(\d+:\d+)\s*$"
)


@dataclass
class MpopOrderRecord:
    page: int
    source_line: int
    origin: str
    op: str
    output_product_code: str
    output_product_description: str
    forecast_quantity: float
    actual_quantity: float
    period: str
    hours_production: str
    hours_setup: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MpopComponentRecord:
    page: int
    source_line: int
    origin: str
    op: str
    output_product_code: str
    output_product_description: str
    component_code: str
    component_description: str
    qty_consumed: float
    qty_returned: float
    qty_net: float
    qty_loss: float
    loss_percent: float
    nonconform: float
    capacity_kg: float | None

    @property
    def loss_kg(self) -> float | None:
        if self.capacity_kg is None:
            return None
        return self.qty_loss * self.capacity_kg

    def to_dict(self) -> dict:
        d = asdict(self)
        d["loss_kg"] = self.loss_kg
        return d


def _extract_layout_text(pdf_path: str | Path) -> str:
    executable = shutil.which("pdftotext")
    if executable:
        result = subprocess.run(
            [executable, "-layout", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout
    # Portable fallback for Windows/local environments without Poppler.
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    return "\f".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)


def parse_mpop_report(pdf_path: str | Path) -> tuple[list[MpopOrderRecord], list[MpopComponentRecord]]:
    """Parse order headers and component rows from the MPOP301 snapshot.

    The header facts and component facts are kept separate so that analytical
    rateio can reference the official accumulated quantity of the OP without
    reading the reference workbook.
    """
    text = _extract_layout_text(pdf_path)
    pages = text.split("\f")
    orders: list[MpopOrderRecord] = []
    components: list[MpopComponentRecord] = []
    current_op: dict | None = None
    pending: dict | None = None

    def flush_pending() -> None:
        nonlocal pending
        if not pending or not current_op:
            pending = None
            return
        desc = normalize_text(pending["description"])
        components.append(MpopComponentRecord(
            page=pending["page"],
            source_line=pending["source_line"],
            origin=current_op["origin"],
            op=current_op["op"],
            output_product_code=current_op["product_code"],
            output_product_description=current_op["description"],
            component_code=pending["component_code"],
            component_description=desc,
            qty_consumed=pending["qty_consumed"],
            qty_returned=pending["qty_returned"],
            qty_net=pending["qty_net"],
            qty_loss=pending["qty_loss"],
            loss_percent=pending["loss_percent"],
            nonconform=pending["nonconform"],
            capacity_kg=extract_capacity_kg(desc),
        ))
        pending = None

    for page_no, page in enumerate(pages, start=1):
        for line_no, line in enumerate(page.splitlines(), start=1):
            op_match = OP_RE.match(line)
            if op_match:
                flush_pending()
                origin, op, product_code, desc, forecast, actual, period, hrs, setup = op_match.groups()
                current_op = {
                    "origin": origin,
                    "op": op.replace(".", ""),
                    "product_code": product_code,
                    "description": normalize_text(desc),
                }
                orders.append(MpopOrderRecord(
                    page=page_no,
                    source_line=line_no,
                    origin=origin,
                    op=op.replace(".", ""),
                    output_product_code=product_code,
                    output_product_description=normalize_text(desc),
                    forecast_quantity=parse_br_number(forecast),
                    actual_quantity=parse_br_number(actual),
                    period=period,
                    hours_production=hrs,
                    hours_setup=setup,
                ))
                continue

            comp_match = COMPONENT_RE.match(line)
            if comp_match and current_op:
                flush_pending()
                consumed, returned, net, loss, pct, nonconf, code, desc = comp_match.groups()
                pending = {
                    "page": page_no,
                    "source_line": line_no,
                    "qty_consumed": parse_br_number(consumed),
                    "qty_returned": parse_br_number(returned),
                    "qty_net": parse_br_number(net),
                    "qty_loss": parse_br_number(loss),
                    "loss_percent": parse_br_number(pct),
                    "nonconform": parse_br_number(nonconf),
                    "component_code": code,
                    "description": desc,
                }
                continue

            if pending:
                stripped = normalize_text(line)
                if not stripped:
                    continue
                if stripped.startswith("MPOP301.GER") or "Resumo de produção" in stripped or stripped.startswith("Qtd. Consumida"):
                    continue
                if not OP_RE.match(line) and not COMPONENT_RE.match(line):
                    pending["description"] += " " + stripped
        flush_pending()
    return orders, components


def parse_mpop(pdf_path: str | Path) -> list[MpopComponentRecord]:
    """Backward-compatible component-only parser."""
    return parse_mpop_report(pdf_path)[1]
