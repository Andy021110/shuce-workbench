from __future__ import annotations

"""加载各本地数据库的表、字段及表关系定义。"""

import json
from pathlib import Path


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "databases"
    / "short_video_ops"
    / "_schema.json"
)


def _load_schema() -> tuple[list[dict], list[dict]]:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"未找到运营场景Schema：{SCHEMA_PATH}")
    tables, relations = [], []
    for path in sorted(SCHEMA_PATH.parent.parent.glob("*/_schema.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        tables.extend(payload.get("tables") or [])
        relations.extend(payload.get("relations") or [])
    return tables, relations


SCHEMA, RELATIONS = _load_schema()


def physical_table_name(table: dict) -> str:
    return str(table.get("name") or table["id"])
