from __future__ import annotations

import re
import json
import uuid
import time
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from ..config import BASE_DIR
from ..result_profile import build_profile
from ..database import SCHEMA, physical_table_name
from ..security import AccessScope
from .models import SqlExecution


DATABASE_ROOT = BASE_DIR / "data" / "databases"


class DuckDbEngine:
    """将 CSV 目录映射为只读 DuckDB 数据库。

    CSV 文件名作为 SQL 表名使用。
    """

    def __init__(self, database_root: Path | None = None) -> None:
        self.database_root = database_root or DATABASE_ROOT

    def execute(
        self,
        database: str,
        sql: str,
        access_scope: AccessScope | None = None,
    ) -> SqlExecution:
        started_at = time.perf_counter()
        try:
            safe_sql = self._validate_sql(database, sql, access_scope)
            with self.connect(database) as connection:
                cursor = connection.execute(safe_sql)
                columns = [item[0] for item in cursor.description or []]
                if len(set(columns)) != len(columns):
                    raise ValueError("结果字段名重复，请为各列指定不同别名")
                raw_rows = cursor.fetchmany(1000001)
                if len(raw_rows) > 1000000:
                    raise ValueError("查询超过本地快照上限 1,000,000 行，请缩小范围后重试")
                snapshot_id = uuid.uuid4().hex
                snapshot = {"columns": columns, "rows": [
                    {column: self._json_value(value) for column, value in zip(columns, row)}
                    for row in raw_rows
                ]}
                result_profile = build_profile(columns, snapshot["rows"], total_rows=len(raw_rows), sql=safe_sql)
                snapshot["result_profile"] = result_profile
                folder = BASE_DIR / "data" / "result_snapshots"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{snapshot_id}.json").write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
                columns = [item[0] for item in cursor.description or []]
                rows = [
                    {column: self._json_value(value) for column, value in zip(columns, row)}
                    for row in raw_rows[:200]
                ]
            return SqlExecution(
                safe_sql,
                True,
                columns,
                rows,
                total_rows=len(raw_rows), snapshot_id=snapshot_id, truncated=len(raw_rows) > 200, result_profile=result_profile,
                execution_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
        except (ValueError, TypeError, ParseError, duckdb.Error, OSError) as exc:
            return SqlExecution(
                sql,
                False,
                error=str(exc),
                execution_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )

    @contextmanager
    def connect(self, database: str) -> Iterator[duckdb.DuckDBPyConnection]:
        """创建内存连接并将 CSV 文件注册为只读视图。"""
        folder = self._database_folder(database)
        connection = duckdb.connect(":memory:")
        try:
            import os
            if os.getenv('SHUCE_PUBLIC_DEPLOYMENT', '').lower() in {'1','true','yes'}:
                connection.execute("SET memory_limit='256MB'")
                connection.execute("SET threads=2")
            csv_files = sorted(folder.glob("*.csv"))
            if not csv_files:
                raise ValueError(f"数据库文件夹没有CSV表：{folder}")
            for csv_path in csv_files:
                table = csv_path.stem
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                    raise ValueError(f"CSV文件名不能作为安全表名：{csv_path.name}")
                path = csv_path.resolve().as_posix().replace("'", "''")
                options = ""
                if database.startswith("local_"):
                    metadata = json.loads((folder / "_schema.json").read_text())
                    definition = next(t for t in metadata["tables"] if t["name"] == table)
                    types = {f["name"]: f.get("source_type", "VARCHAR") for f in definition["fields"]}
                    options = ", types=" + str(types)
                connection.execute(
                    f'CREATE VIEW "{table}" AS '
                    f"SELECT * FROM read_csv_auto('{path}', header=true, sample_size=-1{options})"
                )
            yield connection
        finally:
            connection.close()

    def _validate_sql(
        self,
        database: str,
        sql: str,
        access_scope: AccessScope | None = None,
    ) -> str:
        cleaned = self.clean_sql(sql).rstrip(";").strip()
        statements = [item for item in sqlglot.parse(cleaned, read="duckdb") if item]
        if len(statements) != 1:
            raise ValueError("一次只允许执行一条SQL")
        statement = statements[0]
        if not isinstance(statement, exp.Query):
            raise ValueError("只允许执行SELECT/WITH只读查询")
        forbidden_nodes = {
            "insert", "update", "delete", "merge", "create", "drop", "alter",
            "copy", "attach", "detach", "command", "transaction", "grant", "revoke",
        }
        if any(node.key in forbidden_nodes for node in statement.walk()):
            raise ValueError("SQL包含禁止的写入或管理操作")

        # 查询必须显式列出返回字段。
        for select in statement.find_all(exp.Select):
            if any(
                isinstance(projection, exp.Star)
                or isinstance(projection, exp.Column) and projection.is_star
                for projection in select.expressions
            ):
                raise ValueError("不允许使用SELECT *，必须明确列出查询字段")

        if database == "olist_ecommerce":
            # Prevent the common many-to-many monetary amplification at the SQL boundary.
            facts = {"olist_order_items_dataset", "olist_order_payments_dataset", "olist_order_reviews_dataset"}
            monetary = {"price", "payment_value", "freight_value"}
            for select in statement.find_all(exp.Select):
                direct_facts = {table.name for table in select.find_all(exp.Table)
                                if table.find_ancestor(exp.Select) is select and table.name in facts}
                sums_money = any(
                    aggregate.find_ancestor(exp.Select) is select
                    and any(column.name in monetary for column in aggregate.find_all(exp.Column))
                    for aggregate in select.find_all(exp.Sum)
                )
                if len(direct_facts) > 1 and sums_money:
                    raise ValueError("订单商品、支付、评价均为一对多；请分别按 order_id 预聚合后关联，避免金额放大")

        if access_scope and not access_scope.allows_database(database):
            raise ValueError("当前用户无权访问该数据库")

        database_tables = [
            table
            for table in SCHEMA
            if table.get("database", "short_video_ops") == database
        ]
        allowed = {
            physical_table_name(table): table["id"]
            for table in database_tables
        }
        cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
        referenced = {
            table.name
            for table in statement.find_all(exp.Table)
            if table.name not in cte_names
        }
        unknown = {name for name in referenced if name not in allowed}
        if unknown:
            raise ValueError(f"SQL引用了未知数据表：{', '.join(sorted(unknown))}")
        if access_scope:
            denied = {
                table
                for table in referenced
                if not access_scope.allows_table(database, allowed[table])
            }
            if denied:
                raise ValueError("SQL引用了当前用户无权访问的数据表")
        return cleaned

    def _database_folder(self, database: str) -> Path:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
            raise ValueError("数据库名称不合法")
        folder = (self.database_root / database).resolve()
        root = self.database_root.resolve()
        if root not in folder.parents or not folder.is_dir():
            raise ValueError(f"本地CSV数据库不存在：{database}")
        return folder

    @staticmethod
    def clean_sql(text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:sql)?\s*|\s*```$", "", cleaned, flags=re.I)
        return cleaned.strip()

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value
