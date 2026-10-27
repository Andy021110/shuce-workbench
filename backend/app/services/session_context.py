from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from ..config import Settings
from ..model_client import ModelClient
from ..models import QueryResult
from ..telemetry import active_trace
from ..result_profile import ensure_profile, chart_source

MAX_ANALYSIS_SOURCES = 12
from ..analysis_basis import actual_basis, basis_for
from .session_archive import SessionArchive
from .short_term_memory import ShortTermMemory


logger = logging.getLogger(__name__)


class SessionContext:
    """管理会话历史和前端上下文。"""

    def __init__(self, model_client: ModelClient, config: Settings) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.session_tasks: dict[str, list[str]] = defaultdict(list)
        self.table_row_limit = max(1, config.context_table_row_limit)
        self.route_context_turns = max(1, config.route_context_turns)
        self.archive = SessionArchive(
            config.session_archive_file,
            enabled=config.session_archive_enabled,
        )
        self.short_term_memory = ShortTermMemory(
            model_client,
            trigger_tokens=config.short_term_summary_trigger_tokens,
            batch_tokens=config.short_term_summary_batch_tokens,
            min_recent_turns=config.short_term_min_recent_turns,
            summary_enabled=config.short_term_summary_enabled,
            on_summary_updated=self.archive.save_summary,
        )
        self._restore_archive()

    def remember(
        self,
        session_id: str,
        result: QueryResult,
        query: str,
        workspace: dict[str, Any],
        user_id: str = "demo_growth_ops",
    ) -> None:
        trace = active_trace.get()
        if trace is not None:
            previous = self.tasks.get(result.task_id, {}).get('result')
            result.execution_trace = trace.snapshot(previous.execution_trace if previous else None)
        self.tasks[result.task_id] = {
            "query": query,
            "session_id": session_id,
            "workspace": workspace,
            "user_id": user_id,
            "result": result,
        }
        if result.task_id not in self.session_tasks[session_id]:
            self.session_tasks[session_id].append(result.task_id)
        self.archive.save_turn(
            result.task_id,
            session_id,
            query,
            workspace,
            result.model_dump(mode="json"),
        )
        self.short_term_memory.maybe_schedule(session_id, self._memory_items(session_id))

    def _restore_archive(self) -> None:
        for item in self.archive.load_turns():
            try:
                result = QueryResult.model_validate(item["result"])
            except ValueError as exc:
                logger.warning(
                    "session_turn_restore_failed task_id=%s error=%s",
                    item.get("task_id"),
                    exc,
                )
                continue
            if ((item.get('workspace') or {}).get('database_id')=='olist_ecommerce' and result.route=='database_query'
                and result.status=='completed' and result.sql and not result.review_context and result.metric_basis!='canonical' and (result.query_intent or {}).get('validation_version',0)<2):
                from ..query_intent import legacy_result_error
                mismatch=legacy_result_error(result.sql,item['query'])
                if mismatch:
                    result.query_validation={'reason':mismatch,'original_analysis':result.analysis}
                    result.status='failed';result.message='历史结果需要重新核对'
                    result.analysis='该历史结果未通过新的业务条件核对，请按原问题重新运行。原SQL及生成说明保留在历史核对信息中。'
            task_id = str(item["task_id"])
            session_id = str(item["session_id"])
            self.tasks[task_id] = {
                "query": item["query"],
                "session_id": session_id,
                "workspace": item["workspace"],
                "result": result,
                "restored": True,
                "updated_at": item.get("updated_at"),
                "user_id": session_id.split(":", 1)[0] if ":" in session_id else "unowned_legacy",
            }
            self.session_tasks[session_id].append(task_id)

        for state in self.archive.load_summaries():
            self.short_term_memory.restore(
                state["session_id"],
                state["summary"],
                state["summarized_ids"],
            )

    def latest_pending(self, session_id: str) -> dict[str, Any] | None:
        ids = self.session_tasks.get(session_id, [])
        if not ids:
            return None
        task = self.tasks.get(ids[-1])
        return (
            task
            if task
            and not task.get("restored")
            and task["result"].status == "waiting_clarification"
            else None
        )

    def latest_table_result(self, session_id: str) -> QueryResult | None:
        for task_id in reversed(self.session_tasks.get(session_id, [])):
            result: QueryResult = self.tasks[task_id]["result"]
            if result.status == "completed" and result.route == "database_query" and (result.rows or result.sql):
                return result
        return None

    def analysis_basis(self, session_id: str, query: str) -> dict:
        previous = self.latest_table_result(session_id)
        basis = previous.analysis_basis if previous else None
        if previous and not basis and previous.sql:
            from ..analysis_basis import period_fields, TIME_FIELDS
            fields = period_fields(previous.sql)
            if len(fields) == 1:
                field = next(iter(fields))
                basis = {"time_field":field,"time_label":TIME_FIELDS[field]}
        return basis_for(query, basis)

    def analysis_filters(self,session_id,query):
        from ..analysis_scope import filters_for_query
        previous=self.latest_table_result(session_id)
        filters=((previous.analysis_scope or {}).get('filters') or (previous.review_context or {}).get('filters')) if previous else None
        return filters_for_query(query,filters)

    def route_context(
        self, session_id: str, workspace: dict[str, Any] | None = None
    ) -> str:
        # 路由上下文只读取近期轮次，不读取异步摘要。
        recent_items = self._memory_items(session_id)[-self.route_context_turns :]
        context = {
            "recent_turns": [self._route_item(item) for item in recent_items],
        }
        selected_headers = self._selected_table_headers(session_id, workspace or {})
        if selected_headers:
            context["selected_table_headers"] = selected_headers
        return json.dumps(context, ensure_ascii=False) if recent_items or selected_headers else ""

    def short_term_context(self, session_id: str) -> str:
        """返回滑动窗口和异步摘要，供已有数据分析流程使用。"""
        return self.short_term_memory.context(session_id, self._memory_items(session_id))

    @staticmethod
    def _route_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "turn_id": str(item.get("task_id") or ""),
            "user_message": item.get("query", ""),
            "route": item.get("route", ""),
            "status": item.get("status", ""),
            "result_title": item.get("result_title"),
            "assistant_summary": str(item.get("analysis") or "")[:240],
            "columns": list(item.get("columns") or []),
            "row_count": int(item.get("row_count") or 0),
        }

    def _selected_table_headers(
        self, session_id: str, workspace: dict[str, Any]
    ) -> list[dict[str, Any]]:
        supplied = {
            str(item.get("task_id")): item
            for item in workspace.get("analysis_tables", [])
            if item.get("task_id")
        }
        headers = []
        for task_id in list(dict.fromkeys(workspace.get("analysis_table_ids") or [])):
            provided = supplied.get(task_id)
            if provided:
                headers.append({
                    "task_id": task_id,
                    "title": str(provided.get("title") or "参考表格"),
                    "query": str(provided.get("query") or ""),
                    "columns": list(provided.get("columns") or []),
                    "row_count": provided.get("total_rows") or len(provided.get("rows") or []),
                    "preview_only": provided.get("truncated") or len(provided.get("rows") or []) > self.table_row_limit,
                })
                continue
            task = self.tasks.get(task_id)
            if not task or task.get("session_id") != session_id:
                continue
            result: QueryResult = task["result"]
            headers.append({
                "task_id": task_id,
                "title": result.result_title or "参考表格",
                "query": task.get("query", ""),
                "columns": result.columns,
                "row_count": result.total_rows if result.total_rows is not None else len(result.rows),
                "preview_only": result.truncated or len(result.rows) > self.table_row_limit,
            })
        return headers

    def _memory_items(self, session_id: str) -> list[dict[str, Any]]:
        output = []
        for task_id in self.session_tasks.get(session_id, []):
            task = self.tasks[task_id]
            result: QueryResult = task["result"]
            output.append({
                "task_id": task_id,
                "query": task["query"],
                "route": result.route,
                "status": result.status,
                "result_title": result.result_title,
                "analysis": result.analysis or "",
                "columns": result.columns,
                "row_count": result.total_rows if result.total_rows is not None else len(result.rows),
                "preview_only": result.truncated or len(result.rows) > self.table_row_limit,
            })
        return output

    def result_context(self, result, query=""):
        profile = ensure_profile(result)
        derived = chart_source(result.task_id, result.result_title or "查询结果", profile)
        return {"task_id":result.task_id,"title":result.result_title or "查询结果","query":query,
                "row_count":result.total_rows if result.total_rows is not None else len(result.rows),
                "preview_only":True,"analysis":result.analysis,"columns":result.columns,
                "rows":result.rows[:self.table_row_limit],"sql":result.sql,
                "result_profile":profile,"metric_contracts":result.metric_contracts,"metric_basis":result.metric_basis,
                "analysis_basis":result.analysis_basis,"review_context":result.review_context,"analysis_scope":result.analysis_scope,
                "summary_sources":[derived] if derived else []}

    def recent_result_context(self, session_id: str) -> str:
        result=self.latest_table_result(session_id)
        return json.dumps(self.result_context(result),ensure_ascii=False) if result else ""

    def analysis_context(self, session_id: str, workspace: dict[str, Any]):
        payloads=[];sources=[]
        if len(set(workspace.get("analysis_table_ids") or [])) > MAX_ANALYSIS_SOURCES:
            raise ValueError(f"最多选择 {MAX_ANALYSIS_SOURCES} 份参考结果，请减少选择后重试")
        # Selected tables have already been authorized by AskDataService. Ignore supplied rows here.
        ids = list(dict.fromkeys(workspace.get("analysis_table_ids") or []))
        if not ids:
            completed = [task_id for task_id in self.session_tasks.get(session_id, [])
                         if self.tasks[task_id]["result"].route == "database_query"
                         and self.tasks[task_id]["result"].status == "completed"]
            latest_context=self.tasks[completed[-1]]['result'].review_context if completed else None
            if latest_context:
                completed=[t for t in completed if not self.tasks[t]['result'].review_context or (
                    self.tasks[t]['result'].review_context.get('month')==latest_context.get('month') and
                    (self.tasks[t]['result'].review_context.get('filters') or {})==(latest_context.get('filters') or {}))]
            # Re-running one monthly step replaces its automatic report source;
            # manually selected snapshots remain untouched.
            ids=[]; seen=set()
            for task_id in reversed(completed):
                review=self.tasks[task_id]['result'].review_context
                key=(review['month'],review['step'],json.dumps(review.get('filters') or {},sort_keys=True)) if review else ('task',task_id)
                if key in seen: continue
                seen.add(key);ids.append(task_id)
                if len(ids)==4: break
            ids.reverse()
            # Keep the latest monthly overview and its goals through repeated drilldowns.
            latest_review = self.tasks[ids[-1]]['result'].review_context if ids else None
            if latest_review:
                overview = next((t for t in reversed(self.session_tasks.get(session_id, []))
                    if self.tasks[t]['result'].status=='completed'
                    and (self.tasks[t]['result'].review_context or {}).get('month')==latest_review['month']
                    and (self.tasks[t]['result'].review_context or {}).get('step')=='overview'
                    and ((self.tasks[t]['result'].review_context or {}).get('filters') or {})==(latest_review.get('filters') or {})), None)
                if overview and overview not in ids:
                    ids.insert(0,overview)
        for task_id in ids:
            task=self.tasks.get(task_id)
            if not task: continue
            owner=session_id.split(":",1)[0]
            current_database=session_id.rsplit(":",1)[-1]
            if task.get("user_id")!=owner or (task.get("workspace") or {}).get("database_id")!=current_database: continue
            result=task["result"]
            if result.status!="completed" or result.route!="database_query": continue
            item=self.result_context(result,task.get("query",""))
            payloads.append(item)
            sources.append({k:item[k] for k in ("task_id","title","query","row_count","columns")})
        return json.dumps(payloads,ensure_ascii=False) if payloads else "",sources

    @staticmethod
    def normalize_workspace(workspace: dict[str, Any] | None) -> dict[str, Any]:
        resolved = dict(workspace or {})
        resolved["schema_fields"] = list(resolved.get("schema_fields") or resolved.get("fields") or [])
        resolved["analysis_table_ids"] = list(dict.fromkeys(resolved.get("analysis_table_ids") or []))
        if len(resolved["analysis_table_ids"]) > MAX_ANALYSIS_SOURCES:
            raise ValueError(f"最多选择 {MAX_ANALYSIS_SOURCES} 份参考结果，请减少选择后重试")
        resolved["analysis_tables"] = list(resolved.get("analysis_tables") or [])
        resolved["confirmed_schema_tables"] = list(dict.fromkeys(resolved.get("confirmed_schema_tables") or []))
        resolved["confirmed_parameters"] = dict(resolved.get("confirmed_parameters") or {})
        return resolved

    @staticmethod
    def query_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_fields": list(workspace.get("schema_fields") or []),
            "confirmed_schema_tables": list(workspace.get("confirmed_schema_tables") or []),
            "confirmed_parameters": dict(workspace.get("confirmed_parameters") or {}),
        }

    @staticmethod
    def match_clarification(text: str, result: QueryResult) -> str | None:
        if not result.clarification:
            return None
        normalized = text.strip().lower().replace(" ", "")
        options = result.clarification.options
        for option in options:
            if normalized == option.id.lower().replace(" ", "") or option.label.lower().replace(" ", "") in normalized:
                return option.id
        ordinal = {"第一个": 0, "第一项": 0, "选一": 0, "1": 0, "第二个": 1, "第二项": 1, "选二": 1, "2": 1}
        if normalized in ordinal and ordinal[normalized] < len(options):
            return options[ordinal[normalized]].id
        return None
