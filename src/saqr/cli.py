from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import json

from .config import load_config
from .pipeline import run_pipeline


def build_parser() -> ArgumentParser:
    p = ArgumentParser(description="SAQR Motor de Dados v0.1")
    p.add_argument("--input-dir", required=True, help="Directory containing the six SAQR source files")
    p.add_argument("--config", required=True, help="JSON configuration file")
    p.add_argument("--output-dir", required=True, help="Directory for CSV/JSON outputs")
    p.add_argument("--database", required=True, help="SQLite output database")
    return p


def main() -> None:
    args = build_parser().parse_args()
    cfg = load_config(args.config)
    calc_id = run_pipeline(Path(args.input_dir), Path(args.output_dir), Path(args.database), cfg)
    summary_path = Path(args.output_dir) / "summary.json"
    with open(summary_path, encoding="utf-8") as fh:
        summary = json.load(fh)
    print(json.dumps({"status": "SUCCESS", "calculation_run_id": calc_id, "summary": summary}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
