from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Any
import re
import zipfile
import xml.etree.ElementTree as ET

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships", "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


def col_to_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def excel_serial_to_datetime(value: float) -> datetime:
    # Excel 1900 date system including the historic leap-year bug.
    origin = datetime(1899, 12, 30)
    return origin + timedelta(days=float(value))

@dataclass(frozen=True)
class SheetInfo:
    name: str
    path: str

class XlsxWorkbook:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.zf = zipfile.ZipFile(self.path)
        self._member_map = {name.replace("\\", "/"): name for name in self.zf.namelist()}
        self.shared_strings = self._load_shared_strings()
        self.sheets = self._load_sheets()

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> "XlsxWorkbook":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _read_member(self, normalized_path: str) -> bytes:
        actual = self._member_map.get(normalized_path.replace("\\", "/"))
        if actual is None:
            raise KeyError(normalized_path)
        return self.zf.read(actual)

    def _open_member(self, normalized_path: str):
        actual = self._member_map.get(normalized_path.replace("\\", "/"))
        if actual is None:
            raise KeyError(normalized_path)
        return self.zf.open(actual)

    def _load_shared_strings(self) -> list[str]:
        try:
            root = ET.fromstring(self._read_member("xl/sharedStrings.xml"))
        except KeyError:
            return []
        out: list[str] = []
        for si in root.findall("m:si", _NS):
            texts = [t.text or "" for t in si.findall(".//m:t", _NS)]
            out.append("".join(texts))
        return out

    def _load_sheets(self) -> list[SheetInfo]:
        wb_root = ET.fromstring(self._read_member("xl/workbook.xml"))
        rel_root = ET.fromstring(self._read_member("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rel_root.findall("p:Relationship", _NS)}
        sheets: list[SheetInfo] = []
        for s in wb_root.findall("m:sheets/m:sheet", _NS):
            rid = s.attrib[f"{{{_NS['r']}}}id"]
            target = rel_map[rid]
            if target.startswith("/"):
                path = target.lstrip("/")
            elif target.startswith("xl/"):
                path = target
            else:
                path = "xl/" + target
            sheets.append(SheetInfo(s.attrib["name"], path))
        return sheets

    def sheet_names(self) -> list[str]:
        return [s.name for s in self.sheets]

    def iter_rows(self, sheet_name: str) -> Iterator[list[Any]]:
        info = next((s for s in self.sheets if s.name == sheet_name), None)
        if info is None:
            raise KeyError(f"Sheet not found: {sheet_name}")
        with self._open_member(info.path) as fh:
            # iterparse keeps memory bounded for the 10k x 81 movements workbook.
            for event, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag == f"{{{_NS['m']}}}row":
                    cells: dict[int, Any] = {}
                    max_idx = -1
                    for c in elem.findall("m:c", _NS):
                        ref = c.attrib.get("r", "A1")
                        idx = col_to_index(ref)
                        max_idx = max(max_idx, idx)
                        t = c.attrib.get("t")
                        v = c.find("m:v", _NS)
                        inline = c.find("m:is", _NS)
                        if inline is not None:
                            text = "".join((x.text or "") for x in inline.findall(".//m:t", _NS))
                            value: Any = text
                        elif v is None or v.text is None:
                            value = None
                        elif t == "s":
                            value = self.shared_strings[int(v.text)]
                        elif t == "b":
                            value = v.text == "1"
                        elif t in {"str", "e"}:
                            value = v.text
                        else:
                            raw = v.text
                            try:
                                num = float(raw)
                                value = int(num) if num.is_integer() else num
                            except ValueError:
                                value = raw
                        cells[idx] = value
                    row = [None] * (max_idx + 1)
                    for idx, value in cells.items():
                        row[idx] = value
                    yield row
                    elem.clear()

    def read_rows(self, sheet_name: str, limit: int | None = None) -> list[list[Any]]:
        rows = []
        for i, row in enumerate(self.iter_rows(sheet_name)):
            rows.append(row)
            if limit is not None and i + 1 >= limit:
                break
        return rows
