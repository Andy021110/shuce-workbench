"""Local workbench: deterministic metrics, validated CSV and immutable result browsing."""
from __future__ import annotations
import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from fastapi import HTTPException
from pydantic import BaseModel, Field
from .config import BASE_DIR
from .category_labels import category_label
from .database import SCHEMA
from .metric_registry import metric_query, definitions
from .querying.duckdb_engine import DuckDbEngine

ROOT = BASE_DIR / "data" / "databases"
LOCK = Lock()

class CsvInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    content: str = Field(max_length=10_000_000)
    description: str = Field(default="", max_length=500)
    fields: dict[str, str] = Field(default_factory=dict)

def inspect_csv(payload: CsvInput):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", payload.name):
        raise ValueError("名称只允许英文字母、数字和下划线，且不能以数字开头")
    content = payload.content.removeprefix("\ufeff")
    try: content.encode("utf-8")
    except UnicodeEncodeError as error: raise ValueError("CSV 含无效 Unicode 字符，请使用 UTF-8 编码") from error
    if "\x00" in content or not content.strip():
        raise ValueError("CSV 为空或含无效空字符，请使用 UTF-8 编码")
    reader = csv.reader(io.StringIO(content), strict=True)
    try:
        headers = next(reader)
        if not 1 <= len(headers) <= 100 or len(set(headers)) != len(headers):
            raise ValueError("需要 1–100 个不重复的字段")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", h) for h in headers):
            raise ValueError("字段名只允许英文字母、数字和下划线，不能以数字开头")
        rows = []
        for index, row in enumerate(reader, 2):
            if len(row) != len(headers):
                raise ValueError(f"第 {index} 行有 {len(row)} 列，应为 {len(headers)} 列")
            if len(rows) >= 100000:
                raise ValueError("单次导入最多 100,000 行")
            rows.append(dict(zip(headers, row)))
    except (csv.Error, StopIteration) as error:
        raise ValueError("CSV 格式无效：请检查表头、引号与分隔符") from error
    if not rows:
        raise ValueError("CSV 至少需要一行数据")
    types = {}
    for header in headers:
        values = [row[header].strip() for row in rows if row[header].strip()]
        if values and all(re.fullmatch(r"-?(?:0|[1-9][0-9]*)", v) for v in values) and all(len(v.lstrip("-")) < 18 for v in values):
            types[header] = "BIGINT"
        elif values and all(re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", v) for v in values):
            types[header] = "DOUBLE"
        else: types[header] = "VARCHAR"
    return {"columns": headers, "rows": rows[:5], "types": types, "row_count": len(rows), "status": "validated"}

def import_csv(payload: CsvInput):
    preview = inspect_csv(payload)
    database = "local_" + payload.name.lower()
    with LOCK:
        folder = ROOT / database
        if folder.exists():
            raise ValueError("该名称已存在，请使用新的名称；已有数据不会被覆盖")
        fields = [{"name": h, "sql_name": h, "label": payload.fields.get(h) or h,
                   "description": payload.fields.get(h) or "用户导入字段，业务口径待确认",
                   "type": {"BIGINT":"整数", "DOUBLE":"数值"}.get(preview["types"][h], "文本"), "source_type": preview["types"][h],
                   "aggregation": "none", "role": "dimension"} for h in preview["columns"]]
        table = {"id": database + "." + payload.name, "name": payload.name, "database": database,
                 "label": payload.name, "description": payload.description or "本地 CSV 导入（用户提供）",
                 "fields": fields, "row_count": preview["row_count"], "domain": "local_csv"}
        folder.mkdir()
        try:
            (folder / (payload.name + ".csv")).write_text(payload.content.removeprefix("\ufeff"), encoding="utf-8")
            schema = {"tables": [table], "relations": [], "imported_at": datetime.now(timezone.utc).isoformat(),
                      "source": "local_csv", "description": payload.description}
            (folder / "_schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2))
            SCHEMA.append(table)
        except Exception:
            for item in folder.iterdir(): item.unlink()
            folder.rmdir()
            raise
    return {**preview, "database_id": database, "status": "imported"}

def catalog(scope):
    result = []
    for database in sorted(scope.allowed_databases):
        tables = [t for t in SCHEMA if t["database"] == database and t["id"] in scope.allowed_tables]
        meta = json.loads((ROOT / database / "_schema.json").read_text())
        result.append({"id": database, "table_count": len(tables), "row_count": sum(t.get("row_count",0) for t in tables),
                       "source": "Kaggle Olist" if database == "olist_ecommerce" else "合成样例" if database == "short_video_ops" else "本地 CSV",
                       "imported_at": meta.get("imported_at"), "status": "ready", "tables": tables})
    return result

def dashboard(scope, database):
    if not scope.allows_database(database): raise PermissionError("无权访问该数据源")
    engine = DuckDbEngine()
    def run(sql):
        result = engine.execute(database, sql, scope)
        if not result.success: raise ValueError(result.error)
        # dashboard results are small and do not need persistent result snapshots
        if result.snapshot_id: (BASE_DIR / "data/result_snapshots" / (result.snapshot_id + ".json")).unlink(missing_ok=True)
        return result.rows
    if database != "olist_ecommerce":
        return {"source": database, "metrics": [], "monthly": [], "status": "ready",
                "note": "此数据源未预设经营指标。请查看字段说明后指定统计口径。"}
    metrics = run("""SELECT MIN(CAST(order_purchase_timestamp AS DATE)) AS start_date,
          MAX(CAST(order_purchase_timestamp AS DATE)) AS end_date FROM olist_orders_dataset""")[0]
    for metric_id in ("order_count","item_sales","payments","delivery_late","repeat_buyers"):
        sql,_=metric_query(metric_id)
        metrics.update(run(sql)[0])
    metrics.update(run("SELECT ROUND(SUM(freight_value),2) AS freight FROM olist_order_items_dataset")[0])
    monthly=[{"month":r["period"],"orders":r["orders"]} for r in run(metric_query("order_count",grain="month")[0])]
    # Preserve empty months so equal chart spacing means equal time intervals.
    indexed = {r["month"]: r["orders"] for r in monthly}
    year, month = map(int, monthly[0]["month"].split("-"))
    end = monthly[-1]["month"]
    monthly = []
    while f"{year:04}-{month:02}" <= end:
        key = f"{year:04}-{month:02}"
        monthly.append({"month": key, "orders": indexed.get(key, 0)})
        year, month = (year+1, 1) if month == 12 else (year, month+1)
    return {"source": "Olist Brazilian E-Commerce", "currency": "BRL", "metrics": metrics, "monthly": monthly,
            "metric_definitions": definitions(), "computed_at": datetime.now(timezone.utc).isoformat(), "note": "固定历史样本；无自动更新。包含全部订单状态。商品金额、运费与支付金额分别查询原表，不做多对多关联。"}

def browse_result(service, task_id, user_id, page=1, size=20, sort="", direction="asc", filter_text=""):
    result = service.get_task(task_id, user_id)
    if not result.snapshot_id:
        data = {"columns": result.columns, "rows": result.rows}
        legacy = True
    else:
        path = BASE_DIR / "data/result_snapshots" / (result.snapshot_id + ".json")
        if not path.exists(): raise ValueError("结果快照已不可用，请重新运行查询")
        data = json.loads(path.read_text())
        legacy = False
    rows = data["rows"]
    if filter_text:
        rows = [r for r in rows if any(filter_text.casefold() in (str(v)+' '+category_label(str(v))).casefold() for v in r.values())]
    if sort:
        if sort not in data["columns"]: raise ValueError("排序字段不存在")
        missing = [r for r in rows if r.get(sort) is None]
        rows = [r for r in rows if r.get(sort) is not None]
        rows.sort(key=lambda r: (0, r[sort]) if isinstance(r[sort], (int,float)) else (1, str(r[sort])), reverse=direction=="desc")
        rows += missing
    return {"columns": data["columns"], "rows": rows[(page-1)*size:page*size], "total_rows": len(rows),
            "source_total": len(data["rows"]), "legacy_preview": legacy, "page": page, "page_size": size}
