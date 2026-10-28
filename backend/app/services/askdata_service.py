from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import Command

from ..config import settings
from ..database import SCHEMA
from ..result_profile import ensure_profile
from ..metric_registry import references, metric_query, add_metric_totals, DATABASE
from ..querying.duckdb_engine import DuckDbEngine
from ..progress import stage
from ..telemetry import observe_task
from ..errors import PipelineStageError
from ..analysis_basis import actual_basis
from ..analysis_scope import scope_card, filters_for_query, continuation_guard
from ..query_intent import intent_for, public_intent, reusable_cohort
from ..model_client import ModelClient
from ..business_requests import business_result
from ..models import QueryResult
from ..retrieval import SchemaIndex
from ..security import AccessController
from ..workflows.query_graph import QueryWorkflow
from .memory_store import MemoryStore
from .session_context import SessionContext


class AskDataService:
    """查询服务入口，负责会话管理和工作流调用。"""

    def __init__(
        self,
        model_client: ModelClient | None = None,
        schema_index: SchemaIndex | None = None,
    ) -> None:
        self.model_client = model_client or ModelClient(settings)
        self.schema_index = schema_index or SchemaIndex(self.model_client, settings)
        self.config = self.schema_index.config
        self.access_controller = AccessController()
        self.context = SessionContext(self.model_client, self.config)
        self.memories = MemoryStore(limit=self.config.max_saved_memories)
        self.workflow = QueryWorkflow(self.model_client, self.schema_index, self.config)
        self.tasks = self.context.tasks
        self.session_tasks = self.context.session_tasks

    @observe_task
    def submit(
        self,
        query: str,
        session_id: str,
        workspace: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> QueryResult:
        normalized = query.strip()
        access_scope = self.access_controller.resolve(user_id)
        database_id = (workspace or {}).get("database_id")
        access_scope = self.access_controller.select_database(access_scope, database_id)
        # 数据源也是会话边界，切换后不能继承另一数据库的待澄清任务或历史结果。
        scoped_session_id = f"{access_scope.user_id}:{session_id}" + (f":{database_id}" if database_id else "")
        retry_id=(workspace or {}).get('retry_task_id')
        if retry_id:
            original=self.tasks.get(retry_id)
            if not original or original.get('user_id')!=access_scope.user_id or (original.get('workspace') or {}).get('database_id')!=database_id:
                raise PermissionError('无权重试该报告')
            if original['result'].route!='data_qa' or original['query'].strip()!=normalized:
                raise ValueError('重试须使用原报告问题；修改问题请作为新任务提交')
            original_ids=(original.get('workspace') or {}).get('analysis_table_ids') or [source['task_id'] for source in original['result'].analysis_sources]
            workspace={**(workspace or {}),'analysis_table_ids':list(original_ids)}
        pending = self.context.latest_pending(scoped_session_id)
        if pending and pending.get("user_id") == access_scope.user_id:
            option_id = self.context.match_clarification(normalized, pending["result"])
            if option_id:
                return self.clarify(
                    pending["result"].task_id,
                    option_id,
                    user_message=normalized,
                    user_id=access_scope.user_id,
                )

        task_id = uuid.uuid4().hex[:12]
        resolved_workspace = self.context.normalize_workspace(workspace)
        confirmation=resolved_workspace.get('continuation_task_id')
        if confirmation:
            previous=self.context.latest_table_result(scoped_session_id)
            item=self.tasks.get(confirmation)
            if not item or item.get('user_id')!=access_scope.user_id or item.get('session_id')!=scoped_session_id:
                raise PermissionError('无权确认该分析范围')
            if not previous or previous.task_id!=confirmation or not reusable_cohort(previous.sql,previous.query_intent or {}):
                raise ValueError('该范围不能直接续查或结果已变化，请重新核对完整条件')
        # Client-supplied row payloads are never a source of truth for saved results.
        supplied = []
        for selected_id in resolved_workspace.get("analysis_table_ids", []):
            selected = self.tasks.get(selected_id)
            if not selected or selected.get("user_id") != access_scope.user_id:
                raise PermissionError("无权引用该历史查询结果")
            if (selected.get("workspace") or {}).get("database_id") != database_id:
                raise PermissionError("不能跨数据源引用历史查询结果")
            result_table = selected["result"]
            if result_table.route != 'database_query':raise ValueError('参考来源必须是已完成的查询结果')
            if result_table.status!='completed':raise ValueError('该历史结果未通过核对或尚未完成，请重新查询后引用')
            ensure_profile(result_table)
            supplied.append({"task_id": selected_id, "title": result_table.result_title,
                             "query": selected["query"], "columns": result_table.columns, "rows": result_table.rows,
                             "total_rows": result_table.total_rows, "truncated": result_table.truncated, "result_profile": result_table.result_profile})
        resolved_workspace["analysis_tables"] = supplied
        for field in resolved_workspace.get("schema_fields", []):
            if field.get("tableId") not in access_scope.allowed_tables:
                raise PermissionError("所选字段不在当前数据源权限范围内")
        self.context.archive.save_message(
            scoped_session_id,
            "user",
            normalized,
            task_id=task_id,
        )
        request_payload={}
        try:
            request_payload=self._payload(task_id,normalized,scoped_session_id,resolved_workspace,access_scope.public())
            result = business_result(task_id,normalized,request_payload['workspace'].get('business_intent') or {},access_scope) if database_id==DATABASE else None
            if result is None:
                result = self.workflow.invoke(request_payload,task_id)
        except PipelineStageError as exc:
            result = QueryResult(
                task_id=task_id,
                status="failed",
                route="data_qa" if exc.stage == "qa_answer" else "database_query",
                message=f"处理停止：{exc.stage}失败",
                analysis=(exc.message if exc.stage in ('scope_inheritance','query_validation') else '本次查询未能完成，原问题和已完成结果仍然保留。可以重试，或选择月度复盘的范围后查询。'),
                route_reason="未使用本地语义兜底",
                steps=[f"{exc.stage}失败，流程已停止"],
                execution_log=[{"stage": exc.stage, "success": False, "error": exc.message}],
                workflow_mode="failed_explicitly",
                analysis_sources=request_payload.get("analysis_sources") or [],
            )
            if exc.stage=='scope_inheritance':
                previous=self.context.latest_table_result(scoped_session_id)
                if previous:
                    result.continuation={'source_task_id':previous.task_id,
                        'source_query':self.tasks[previous.task_id]['query'],
                        'can_continue':reusable_cohort(previous.sql,previous.query_intent or {}),
                        'intent':intent_for(normalized,previous.query_intent,inherit_periods=True)}
        if database_id == "olist_ecommerce" and result.sql and result.workflow_mode != "buyer_cohort":
            result.metric_contracts = references(result.sql)
            result.metric_basis = "reference"
            result.analysis_basis = actual_basis(result.sql, self.context.analysis_basis(scoped_session_id, normalized))
            filters=self.context.analysis_filters(scoped_session_id,normalized)
            previous=self.context.latest_table_result(scoped_session_id)
            previous_filters=(previous.analysis_scope or {}).get('filters') if previous else None
            result.analysis_scope=scope_card(result.sql,filters,basis=result.analysis_basis,inherited=bool(any(filters.values()) and filters==previous_filters),exclusions=request_payload.get('workspace',{}).get('business_intent',{}).get('exclusions'))
            if result.status=='completed':
                result.query_intent=public_intent(request_payload['workspace'].get('business_intent') or {},result.sql)
                if result.query_intent.get('periods'):result.analysis_scope['period_label']='、'.join(result.query_intent['periods'])
                if result.query_intent.get('all_statuses'):result.analysis_scope['statuses']=['全部状态']
        self.context.remember(
            scoped_session_id,
            result,
            normalized,
            resolved_workspace,
            access_scope.user_id,
        )
        self.context.archive.save_message(
            scoped_session_id,
            "assistant",
            result.analysis or result.message,
            task_id=task_id,
            metadata={"route": result.route, "status": result.status},
        )
        return result

    @observe_task
    def submit_metric(self, metric_id, parameters, session_id, user_id):
        scope=self.access_controller.select_database(self.access_controller.resolve(user_id), DATABASE)
        sql,contract=metric_query(metric_id,**parameters)
        task_id=uuid.uuid4().hex[:12]
        period=contract["parameters"]
        query=f"{contract['label']} · 标准口径 v{contract['version']} · {period['start_date'] or '历史起始'} 至 {period['end_date'] or '历史结束'} · {period['grain']}"
        scoped_session_id=f"{scope.user_id}:{session_id}:{DATABASE}"
        workspace={"database_id":DATABASE}
        self.context.archive.save_message(scoped_session_id,"user",query,task_id=task_id)
        stage("metric_sql")
        execution=DuckDbEngine().execute(DATABASE,sql,scope)
        add_metric_totals(execution.result_profile,contract)
        result=QueryResult(task_id=task_id,status="completed" if execution.success else "failed",route="database_query",
            message="按标准口径计算完成" if execution.success else "标准指标计算失败",sql=execution.sql,
            columns=execution.columns,rows=execution.rows,total_rows=execution.total_rows,snapshot_id=execution.snapshot_id,
            truncated=execution.truncated,result_profile=execution.result_profile,metric_contracts=[contract],metric_basis="canonical",
            result_title=contract["label"]+"（标准口径）",workflow_mode="canonical_metric",
            analysis=(f"使用已确认的 {contract['label']} v{contract['version']} 口径，返回 {execution.total_rows} 行。完整结果统计由后端计算。" if execution.success else execution.error),
            steps=["确认版本和日期范围","按标准 SQL 执行","基于完整结果计算统计"])
        self.context.remember(scoped_session_id,result,query,workspace,scope.user_id)
        self.context.archive.save_message(scoped_session_id,"assistant",result.analysis or result.message,task_id=task_id)
        return result

    @observe_task
    def submit_review(self, month, step, session_id, user_id, targets=None, filters=None):
        from ..monthly_review import review_query, review_analysis, target_comparison, STEPS
        from ..analysis_basis import basis_for
        scope = self.access_controller.select_database(self.access_controller.resolve(user_id), DATABASE)
        sql, context, contracts = review_query(month, step, filters)
        task_id = uuid.uuid4().hex[:12]
        query = f"{month} 月度复盘 · {STEPS[step]} · 对比 {context['previous_month']}"
        scoped_session = f"{scope.user_id}:{session_id}:{DATABASE}"
        workspace = {"database_id": DATABASE}
        stage("metric_sql")
        execution = DuckDbEngine().execute(DATABASE, sql, scope)
        if step == 'overview' and execution.success:
            context['targets'] = targets or {}
            context['target_source'] = '用户为本次复盘填写；随结果快照保存，不代表已审批预算，不自动沿用到其他月份'
            context['target_comparison'] = target_comparison(execution.rows, targets)
        result = QueryResult(task_id=task_id, status="completed" if execution.success else "failed", route="database_query",
            message="月度复盘计算完成" if execution.success else "月度复盘计算失败",
            sql=execution.sql, columns=execution.columns, rows=execution.rows,
            total_rows=execution.total_rows, snapshot_id=execution.snapshot_id, truncated=execution.truncated,
            result_profile=execution.result_profile, metric_contracts=contracts, metric_basis="canonical",
            analysis_basis=actual_basis(sql, basis_for("按下单时间")), review_context=context,
            analysis=review_analysis(execution.rows, context) if execution.success else execution.error,
            result_title=f"{month} · {STEPS[step]}", workflow_mode="monthly_review",
            steps=["选择复盘月份与上月", "按下单时间与统一指标口径计算", "保存完整结果快照"])
        result.analysis_scope=scope_card(sql,context.get('filters'),context,result.analysis_basis)
        self.context.archive.save_message(scoped_session,"user",query,task_id=task_id)
        self.context.remember(scoped_session,result,query,workspace,scope.user_id)
        self.context.archive.save_message(scoped_session,"assistant",result.analysis or result.message,task_id=task_id)
        return result

    @observe_task
    def factual_report(self,session_id,database_id,user_id):
        from ..report_facts import build_facts,factual_brief
        from ..models import AnalysisReport
        scope=self.access_controller.select_database(self.access_controller.resolve(user_id),database_id)
        session=f'{scope.user_id}:{session_id}:{database_id}'
        context,sources=self.context.analysis_context(session,{})
        facts=build_facts({'selected_tables':context})
        if not facts:raise ValueError('当前分析没有可整理的查询事实，请先完成一次数据查询')
        markdown,refs=factual_brief({'selected_tables':context},facts)
        task_id=uuid.uuid4().hex[:12]
        query='整理已保存结果的事实报告'
        result=QueryResult(task_id=task_id,status='completed',route='data_qa',message='查询事实已整理，可补充人工结论',
            analysis='本报告由后端整理已保存的查询事实，未生成模型经营解读。',analysis_sources=sources,
            report=AnalysisReport(title='经营复盘事实报告',markdown=markdown,fact_references=refs,generation_mode='backend_facts'))
        self.context.archive.save_message(session,'user',query,task_id=task_id)
        self.context.remember(session,result,query,{'database_id':database_id},scope.user_id)
        self.context.archive.save_message(session,'assistant',result.analysis,task_id=task_id)
        return result

    @observe_task
    def clarify(
        self,
        task_id: str,
        option_id: str,
        user_message: str | None = None,
        user_id: str | None = None,
    ) -> QueryResult:
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        access_scope = self.access_controller.resolve(user_id)
        if task.get("user_id", AccessController.DEFAULT_USER) != access_scope.user_id:
            raise PermissionError("无权访问该查询任务")
        if task.get("restored"):
            raise ValueError("服务已重启，原澄清检查点不可用；请将原问题与补充口径一起重新提交")
        current: QueryResult = task["result"]
        if not current.clarification or option_id not in {item.id for item in current.clarification.options}:
            raise ValueError(option_id)
        option = next(item for item in current.clarification.options if item.id == option_id)
        self.context.archive.save_message(
            task["session_id"],
            "user",
            user_message or option.label,
            task_id=task_id,
            metadata={"type": "clarification", "option_id": option_id},
        )
        result = self.workflow.invoke(Command(resume={"option_id": option_id}), task_id)
        self.context.remember(
            task["session_id"],
            result,
            task["query"],
            task["workspace"],
            access_scope.user_id,
        )
        self.context.archive.save_message(
            task["session_id"],
            "assistant",
            result.analysis or result.message,
            task_id=task_id,
            metadata={"route": result.route, "status": result.status},
        )
        return result

    def search_schema(
        self,
        query: str,
        threshold: float | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        prepared = self.workflow.preprocessor.prepare(query, "")
        extraction = prepared.retrieval
        retrieval = self.schema_index.retrieve(
            query,
            retrieval_terms=extraction.retrieval_terms,
            threshold=threshold,
            access_scope=self.access_controller.resolve(user_id),
        )
        retrieval["extraction"] = extraction.public()
        retrieval["schema_graph"] = self.workflow.graph_builder.build(
            retrieval["hits"],
            self.access_controller.resolve(user_id),
        )
        return retrieval

    def rebuild_schema_index(self) -> dict[str, Any]:
        self.schema_index.ensure_built(force=True)
        return self.schema_index.status()

    def get_task(self, task_id: str, user_id: str | None = None) -> QueryResult:
        if task_id not in self.tasks:
            raise KeyError(task_id)
        scope = self.access_controller.resolve(user_id)
        if self.tasks[task_id].get("user_id", AccessController.DEFAULT_USER) != scope.user_id:
            raise PermissionError("无权访问该查询任务")
        result = self.tasks[task_id]["result"]
        if result.route == "database_query": ensure_profile(result)
        return result

    def save_memory(self, task_id: str, user_id: str | None = None) -> None:
        task = self.tasks.get(task_id)
        if not task or task["result"].status != "completed":
            raise KeyError(task_id)
        scope = self.access_controller.resolve(user_id)
        if task.get("user_id", AccessController.DEFAULT_USER) != scope.user_id:
            raise PermissionError("无权保存该查询结果")
        result: QueryResult = task["result"]
        self.memories.save_result(
            task_id,
            task["query"],
            result.analysis or result.message,
            result.result_title or "查询结果",
            result.columns,
            result.rows,
            scope.user_id,
        )
        result.saved = True
        self.context.archive.save_turn(task_id, task["session_id"], task["query"], task["workspace"], result.model_dump(mode="json"))

    def save_field_memory(
        self,
        table_id: str,
        name: str,
        label: str,
        field_type: str,
        user_id: str | None = None,
    ) -> None:
        scope = self.access_controller.resolve(user_id)
        table = next((item for item in SCHEMA if item["id"] == table_id), None)
        database = str(table.get("database") or "short_video_ops") if table else ""
        if not table or not scope.allows_table(database, table_id):
            raise PermissionError("无权保存该字段")
        if not any(field["name"] == name for field in table["fields"]):
            raise ValueError("字段不存在")
        self.memories.save_field(table_id, name, label, field_type, scope.user_id)

    def delete_memory(self, memory_id: str, user_id: str | None = None) -> None:
        scope = self.access_controller.resolve(user_id)
        self.memories.delete(
            memory_id,
            scope.user_id,
            include_all=False,
        )

    def list_memories(self, user_id: str | None = None) -> list[dict[str, Any]]:
        scope = self.access_controller.resolve(user_id)
        return self.memories.list(
            scope.user_id,
            include_all=False,
        )

    def list_mcp_tools(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """返回与模型看到的内容一致的MCP工具定义。"""
        scope = self.access_controller.resolve(user_id)
        return self.workflow.mcp_client(scope.public()).list_tools()

    def list_skills(self) -> list[dict[str, Any]]:
        """返回当前启用的应用级Skill配置。"""
        return self.workflow.skills.list()

    def visible_schema(self, user_id: str | None = None) -> list[dict[str, Any]]:
        return self.access_controller.filter_schema(
            self.access_controller.resolve(user_id)
        )

    def status(self) -> dict[str, Any]:
        return {
            **self.config.public_status(),
            "workflow_engine": "langgraph",
            "human_in_the_loop": "interrupt+checkpointer",
            "mcp": {
                "transport": "in_process",
                "tool_count": len(self.list_mcp_tools()),
            },
            "skills": [item["name"] for item in self.list_skills()],
            "schema_index": self.schema_index.status(),
        }

    def _payload(
        self,
        task_id: str,
        query: str,
        session_id: str,
        workspace: dict[str, Any],
        access_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        analysis_context, analysis_sources = self.context.analysis_context(session_id, workspace)
        query_workspace = self.context.query_workspace(workspace)
        if workspace.get("database_id") == DATABASE:
            basis = self.context.analysis_basis(session_id, query)
            query_workspace["analysis_basis"] = basis
            previous=self.context.latest_table_result(session_id)
            confirmed=workspace.get('continuation_task_id')
            previous_intent=(previous.query_intent or {}) if previous else {}
            try:
                intent=intent_for(query,previous_intent,inherit_periods=bool(confirmed))
                # Historical fixed reviews already own their three typed filters.
                intent['filters']=self.context.analysis_filters(session_id,query)
            except ValueError as exc:
                raise PipelineStageError('query_validation',str(exc)) from exc
            intent['time_field']=basis['time_field']
            filters=intent['filters']
            query_workspace['scope_guard']='' if confirmed else continuation_guard(query,previous)
            query_workspace['analysis_filters']=filters
            query_workspace['business_intent']=intent
            query_workspace['original_query']=query
            if confirmed:query_workspace['confirmed_scope_query']=self.tasks[confirmed]['query']
            # Include the order join even if retrieval picked only item/product dates.
            table = next(t for t in SCHEMA if t["database"] == DATABASE and any(f["name"] == basis["time_field"] for f in t["fields"]))
            query_workspace["schema_fields"] = [*query_workspace["schema_fields"], {"name":basis["time_field"],"tableId":table["id"],"aggregation":"auto"}]
            for field in ['order_status','customer_state','product_category_name_english']:
                if not any(filters.values()): break
                owner=next(t for t in SCHEMA if t['database']==DATABASE and any(f['name']==field for f in t['fields']))
                query_workspace['schema_fields'].append({'name':field,'tableId':owner['id'],'aggregation':'auto'})
        scope = access_scope or self.access_controller.resolve(None).public()
        catalog = [{"table":t["label"],"fields":[{"name":f["name"],"label":f["label"]} for f in t["fields"]]}
                   for t in SCHEMA if t["id"] in scope.get("allowed_tables", [])]
        return {
            "task_id": task_id,
            "query": query,
            "session_id": session_id,
            "workspace": query_workspace,
            "access_scope": access_scope or self.access_controller.resolve(None).public(),
            "route_context": self.context.route_context(session_id, workspace),
            "short_term_context": self.context.short_term_context(session_id),
            "recent_result_context": "" if analysis_sources else self.context.recent_result_context(session_id),
            "analysis_context": analysis_context,
            "analysis_sources": analysis_sources,
            "schema_catalog": catalog,
            "tool_facts": {},
            "execution_log": [],
            "tool_calls": [],
            "clarification": None,
        }

    _normalize_workspace = staticmethod(SessionContext.normalize_workspace)
    _query_workspace = staticmethod(SessionContext.query_workspace)
