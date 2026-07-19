from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import unicodedata


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text)


def normalize_header(value: Any) -> str:
    text = normalize_text(value).lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def as_text_code(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip().replace(".0", "") if str(value).strip().endswith(".0") else str(value).strip()


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = normalize_text(value)
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})*,\d+", text):
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def excel_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime(1899, 12, 30) + timedelta(days=float(value))
    text = normalize_text(value)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def excel_datetime(date_value: Any, time_value: Any = 0) -> datetime | None:
    base = excel_date(date_value)
    if base is None:
        return None
    if isinstance(time_value, (int, float)):
        return base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=float(time_value))
    text = normalize_text(time_value)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt).time()
            return datetime.combine(base.date(), t)
        except ValueError:
            pass
    return base


def parse_br_number(text: str) -> float:
    text = normalize_text(text)
    return float(text.replace(".", "").replace(",", "."))


def extract_capacity_kg(description: str) -> float | None:
    text = normalize_text(description).upper()
    # Last KG mention is preferred because descriptions may contain package weights before barrel capacity.
    matches = re.findall(r"(?<![A-Z0-9])([0-9]+(?:[\.,][0-9]+)?)\s*KGS?\b", text)
    if not matches:
        return None
    value = matches[-1].replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def record_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None
