from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import json

@dataclass(frozen=True)
class EngineConfig:
    raw: dict[str, Any]

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.raw["operational_day"]["start"])

    @property
    def end(self) -> datetime:
        return datetime.fromisoformat(self.raw["operational_day"]["end"])

    @property
    def operational_day(self) -> str:
        return self.raw["operational_day"]["date"]

    @property
    def considered_origins(self) -> set[str]:
        return {str(x) for x in self.raw["parameters"]["considered_origin_types"]}

    @property
    def consumption_strategy(self) -> str:
        return self.raw["parameters"]["consumption_strategy"]

    def rule(self, code: str) -> dict[str, Any]:
        return self.raw["rules"][code]


def load_config(path: str | Path) -> EngineConfig:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    allowed = set(data["parameters"]["consumption_strategies"])
    active = data["parameters"]["consumption_strategy"]
    if active not in allowed:
        raise ValueError(f"Invalid consumption_strategy={active}; allowed={sorted(allowed)}")
    return EngineConfig(data)
