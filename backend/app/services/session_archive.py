from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock
from typing import Any


class SessionArchive:
    """持久化保存完整会话轮次和摘要状态。"""

    def __init__(self, path: Path, *, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = Lock()
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def save_turn(
        self,
        task_id: str,
        session_id: str,
        query: str,
        workspace: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO session_turns (
                    task_id, session_id, query, workspace_json, result_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    query = excluded.query,
                    workspace_json = excluded.workspace_json,
                    result_json = excluded.result_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    task_id,
                    session_id,
                    query,
                    json.dumps(workspace, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                ),
            )

    def load_turns(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT task_id, session_id, query, workspace_json, result_json, updated_at
                FROM session_turns
                ORDER BY created_at, rowid
                """
            ).fetchall()
        return [
            {
                "task_id": row[0],
                "session_id": row[1],
                "query": row[2],
                "workspace": json.loads(row[3]),
                "result": json.loads(row[4]),
                "updated_at": row[5],
            }
            for row in rows
        ]

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO session_messages (
                    session_id, task_id, role, content, metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    task_id,
                    role,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT task_id, role, content, metadata_json
                FROM session_messages
                WHERE session_id = ?
                ORDER BY message_id
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "task_id": row[0],
                "role": row[1],
                "content": row[2],
                "metadata": json.loads(row[3]),
            }
            for row in rows
        ]

    def save_summary(
        self, session_id: str, summary: str, summarized_ids: set[str]
    ) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO session_summaries (
                    session_id, summary, summarized_ids_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    summarized_ids_json = excluded.summarized_ids_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    session_id,
                    summary,
                    json.dumps(sorted(summarized_ids), ensure_ascii=False),
                ),
            )

    def load_summaries(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT session_id, summary, summarized_ids_json FROM session_summaries"
            ).fetchall()
        return [
            {
                "session_id": row[0],
                "summary": row[1],
                "summarized_ids": set(json.loads(row[2])),
            }
            for row in rows
        ]

    def rename_session(self, user_id, session_id, database_id, title):
        if not self.enabled: raise ValueError('会话存档未启用，无法保存名称')
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO session_metadata(user_id,session_id,database_id,title) VALUES(?,?,?,?) ON CONFLICT(user_id,session_id,database_id) DO UPDATE SET title=excluded.title,updated_at=CURRENT_TIMESTAMP",(user_id,session_id,database_id,title))

    def session_titles(self, user_id):
        if not self.enabled:return {}
        with self._lock, closing(self._connect()) as connection:
            rows=connection.execute('SELECT session_id,database_id,title FROM session_metadata WHERE user_id=?',(user_id,)).fetchall()
        return {(row[0],row[1]):row[2] for row in rows}

    def review_templates(self, user_id, database_id):
        if not self.enabled: return []
        with self._lock, closing(self._connect()) as c:
            rows=c.execute('SELECT template_id,name,filters_json,contract_version FROM review_templates WHERE user_id=? AND database_id=? ORDER BY updated_at DESC, rowid DESC',(user_id,database_id)).fetchall()
        return [dict(id=r[0],name=r[1],filters=json.loads(r[2]),contract_version=r[3]) for r in rows]

    def save_review_template(self, user_id, database_id, name, filters):
        import uuid
        if not self.enabled: raise ValueError('存档未启用，无法保存模板')
        tid=uuid.uuid4().hex[:12]
        with self._lock, closing(self._connect()) as c, c:
            if c.execute('SELECT COUNT(*) FROM review_templates WHERE user_id=? AND database_id=?',(user_id,database_id)).fetchone()[0]>=20:
                raise ValueError('每个数据源最多保存20个模板，请先删除不再使用的模板')
            c.execute('INSERT INTO review_templates(template_id,user_id,database_id,name,filters_json,contract_version) VALUES(?,?,?,?,?,?)',(tid,user_id,database_id,name,json.dumps(filters,ensure_ascii=False),'1.0'))
        return tid

    def delete_review_template(self, user_id, database_id, template_id):
        with self._lock, closing(self._connect()) as c, c:
            return c.execute('DELETE FROM review_templates WHERE template_id=? AND user_id=? AND database_id=?',(template_id,user_id,database_id)).rowcount > 0

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_templates (
                    template_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, database_id TEXT NOT NULL,
                    name TEXT NOT NULL, filters_json TEXT NOT NULL, contract_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS session_metadata (
                    user_id TEXT NOT NULL, session_id TEXT NOT NULL, database_id TEXT NOT NULL,
                    title TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id,session_id,database_id)
                );
                CREATE TABLE IF NOT EXISTS session_turns (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    workspace_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_session_turns_session
                    ON session_turns(session_id, created_at);

                CREATE TABLE IF NOT EXISTS session_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_session_messages_session
                    ON session_messages(session_id, message_id);

                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    summarized_ids_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)
