from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from ..database import SCHEMA


DATABASE_ID = "short_video_ops"
ALL_DATABASES = frozenset(str(table["database"]) for table in SCHEMA)
ALL_TABLES = frozenset(str(table["id"]) for table in SCHEMA)


def _scenario_tables(role: str) -> frozenset[str]:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "databases"
        / DATABASE_ID
        / "_schema.json"
    )
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    names = set((payload.get("role_tables") or {}).get(role) or [])
    return frozenset(
        str(table["id"])
        for table in SCHEMA
        if str(table.get("name") or table["id"]).split(".")[-1] in names
    )


GROWTH_OPS_TABLES = _scenario_tables("growth_ops")
CHANNEL_OPS_TABLES = _scenario_tables("channel_ops")
CONTENT_OPS_TABLES = _scenario_tables("content_ops")


@dataclass(frozen=True)
class AccessScope:
    """由后端生成并贯穿查询链路的权限范围。"""

    user_id: str
    roles: tuple[str, ...]
    allowed_databases: frozenset[str]
    allowed_tables: frozenset[str]

    def allows_database(self, database: str) -> bool:
        return database in self.allowed_databases

    def allows_table(self, database: str, table_id: str) -> bool:
        return self.allows_database(database) and table_id in self.allowed_tables

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["roles"] = list(self.roles)
        payload["allowed_databases"] = sorted(self.allowed_databases)
        payload["allowed_tables"] = sorted(self.allowed_tables)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> AccessScope:
        raw = payload or {}
        return cls(
            user_id=str(raw.get("user_id") or "anonymous"),
            roles=tuple(str(item) for item in raw.get("roles") or []),
            allowed_databases=frozenset(
                str(item) for item in raw.get("allowed_databases") or []
            ),
            allowed_tables=frozenset(str(item) for item in raw.get("allowed_tables") or []),
        )


class AccessController:
    """根据用户角色返回数据库和表权限。"""

    DEFAULT_USER = "demo_growth_ops"
    USER_ROLES = {
        "demo_olist_analyst": ("olist_analyst",),
        "demo_admin": ("admin",),
        "demo_growth_ops": ("growth_ops",),
        "demo_channel_ops": ("channel_ops",),
        "demo_content_ops": ("content_ops",),
    }
    ROLE_POLICIES = {
        "olist_analyst": {
            "databases": frozenset({"olist_ecommerce"}),
            "tables": frozenset(str(t["id"]) for t in SCHEMA if t["database"] == "olist_ecommerce"),
        },
        "admin": {"databases": ALL_DATABASES, "tables": ALL_TABLES},
        "growth_ops": {"databases": frozenset({DATABASE_ID}), "tables": GROWTH_OPS_TABLES},
        "channel_ops": {"databases": frozenset({DATABASE_ID}), "tables": CHANNEL_OPS_TABLES},
        "content_ops": {"databases": frozenset({DATABASE_ID}), "tables": CONTENT_OPS_TABLES},
    }

    def resolve(self, user_id: str | None) -> AccessScope:
        resolved_user = (user_id or self.DEFAULT_USER).strip() or self.DEFAULT_USER
        roles = self.USER_ROLES.get(resolved_user, ())
        if "admin" in roles:
            return AccessScope(resolved_user, roles, frozenset(str(t["database"]) for t in SCHEMA), frozenset(str(t["id"]) for t in SCHEMA))
        databases: set[str] = set()
        tables: set[str] = set()
        for role in roles:
            policy = self.ROLE_POLICIES[role]
            databases.update(policy["databases"])
            tables.update(policy["tables"])
        return AccessScope(
            user_id=resolved_user,
            roles=roles,
            allowed_databases=frozenset(databases),
            allowed_tables=frozenset(tables),
        )

    @staticmethod
    def select_database(scope: AccessScope, database: str | None) -> AccessScope:
        if not database:
            return scope
        if not scope.allows_database(database):
            raise PermissionError("当前用户无权访问所选数据库")
        return AccessScope(
            user_id=scope.user_id,
            roles=scope.roles,
            allowed_databases=frozenset({database}),
            allowed_tables=frozenset(
                table["id"] for table in SCHEMA
                if table["database"] == database and table["id"] in scope.allowed_tables
            ),
        )

    @staticmethod
    def filter_schema(scope: AccessScope) -> list[dict[str, Any]]:
        return [
            table
            for table in SCHEMA
            if scope.allows_table(str(table["database"]), str(table["id"]))
        ]
