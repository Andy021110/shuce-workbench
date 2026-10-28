from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import uuid
import csv
import io
import time
import json
import os
from urllib.parse import quote
from ..brand import PRODUCT_NAME
from ..workbench import CsvInput, inspect_csv, import_csv, catalog, dashboard, browse_result
from ..progress import progress_callback
from ..telemetry import trace_observer
from ..metric_registry import definitions, metric_query, DATABASE
from ..monthly_review import review_query, STEPS

from ..errors import PipelineStageError
from ..models import (
    ClarificationRequest,
    LoginRequest,
    QueryRequest,
    QueryResult,
    SaveFieldMemoryRequest,
    SaveMemoryRequest,
    SessionRename, ReportEditRequest, FactReportRequest, ReviewTemplateRequest,
    SchemaTable,
)
from ..security import AuthService, AuthUser
from ..services.askdata_service import AskDataService


router = APIRouter(prefix="/api")
service = AskDataService()
auth_service = AuthService()


def require_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthUser:
    user = auth_service.authenticate(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(user: AuthUser = Depends(require_user)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以执行该操作")
    return user


@router.get("/health")
def health() -> dict:
    return {"status": "ok", **service.status()}


@router.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    result = auth_service.login(payload.username, payload.password)
    if not result:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token, user = result
    return {"access_token": token, "token_type": "bearer", "user": user.public()}


@router.get("/auth/me")
def me(user: AuthUser = Depends(require_user)) -> dict[str, str]:
    return user.public()


@router.post("/auth/logout")
def logout(
    authorization: str | None = Header(default=None, alias="Authorization"),
    _: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    auth_service.logout(authorization)
    return {"logged_out": True}


@router.get("/schema", response_model=list[SchemaTable])
def schema(
    user: AuthUser = Depends(require_user),
) -> list[dict]:
    return service.visible_schema(user.user_id)


@router.get("/schema/search")
def search_schema(
    q: str,
    threshold: float | None = None,
    user: AuthUser = Depends(require_user),
) -> dict:
    if not q.strip():
        raise HTTPException(status_code=400, detail="检索问题不能为空")
    if threshold is not None and not 0 <= threshold <= 1:
        raise HTTPException(status_code=400, detail="阈值必须在0到1之间")
    try:
        return service.search_schema(q.strip(), threshold, user.user_id)
    except PipelineStageError as error:
        raise HTTPException(
            status_code=502,
            detail=f"{error.stage}失败：{error.message}",
        ) from error


@router.post("/schema/index/rebuild")
def rebuild_schema_index(_: AuthUser = Depends(require_admin)) -> dict:
    try:
        return service.rebuild_schema_index()
    except PipelineStageError as error:
        raise HTTPException(
            status_code=502,
            detail=f"{error.stage}失败：{error.message}",
        ) from error


@router.get("/config")
def config(_: AuthUser = Depends(require_user)) -> dict:
    return service.status()


@router.get("/mcp/tools")
def mcp_tools(
    user: AuthUser = Depends(require_user),
) -> list[dict]:
    """查看模型可用的MCP工具及其输入输出Schema。"""
    return service.list_mcp_tools(user.user_id)


@router.get("/skills")
def skills(_: AuthUser = Depends(require_user)) -> list[dict]:
    """查看当前启用的应用级Skill。"""
    return service.list_skills()


@router.post("/query", response_model=QueryResult)
def query(
    payload: QueryRequest,
    user: AuthUser = Depends(require_user),
) -> QueryResult:
    workspace = payload.workspace.model_dump(exclude_none=True) if payload.workspace else None
    if payload.review_filters and not payload.review_month:
        raise HTTPException(400,'结构化筛选仅随月度复盘提交')
    if payload.review_targets and (not payload.review_month or payload.review_step != 'overview'):
        raise HTTPException(400, '目标只能随月度经营对比提交')
    try:
        if payload.review_month:
            if (workspace or {}).get("database_id") != DATABASE: raise HTTPException(400,"月度复盘仅适用于 Olist 数据源")
            if payload.metric_id: raise HTTPException(400,"不能同时选择月度复盘和单项指标")
            return service.submit_review(payload.review_month,payload.review_step,payload.session_id,user.user_id,payload.review_targets.model_dump(mode="json",exclude_none=True) if payload.review_targets else None,payload.review_filters.model_dump() if payload.review_filters else None)
        if payload.metric_id:
            if (workspace or {}).get("database_id") != DATABASE: raise HTTPException(400,"标准指标仅适用于 Olist 数据源")
            return service.submit_metric(payload.metric_id,payload.metric_parameters.model_dump() if payload.metric_parameters else {"grain":"total"},payload.session_id,user.user_id)
        return service.submit(payload.query, payload.session_id, workspace, user.user_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(400,str(error)) from error


@router.post("/tasks/{task_id}/clarify", response_model=QueryResult)
def clarify(
    task_id: str,
    payload: ClarificationRequest,
    user: AuthUser = Depends(require_user),
) -> QueryResult:
    try:
        return service.clarify(task_id, payload.option_id, user_id=user.user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="查询任务不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error) or "无效的澄清选项") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.get("/tasks/{task_id}", response_model=QueryResult)
def task(
    task_id: str,
    user: AuthUser = Depends(require_user),
) -> QueryResult:
    try:
        return service.get_task(task_id, user.user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="查询任务不存在") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.post("/memories")
def save_memory(
    payload: SaveMemoryRequest,
    user: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    try:
        service.save_memory(payload.task_id, user.user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="没有可保存的查询结果") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return {"saved": True}


@router.get("/memories")
def memories(user: AuthUser = Depends(require_user)) -> list[dict]:
    return service.list_memories(user.user_id)


@router.post("/memories/fields")
def save_field_memory(
    payload: SaveFieldMemoryRequest,
    user: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    try:
        service.save_field_memory(
            payload.table_id,
            payload.name,
            payload.label,
            payload.field_type,
            user.user_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"saved": True}


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: str,
    user: AuthUser = Depends(require_user),
) -> dict[str, bool]:
    service.delete_memory(memory_id, user.user_id)
    return {"deleted": True}


@router.get("/sessions")
def sessions(user: AuthUser = Depends(require_user)):
    grouped = {}
    titles=service.context.archive.session_titles(user.user_id)
    scope = service.access_controller.resolve(user.user_id)
    for item in service.context.archive.load_turns():
        # Old unowned sessions are never assigned to a user by default.
        if not item["session_id"].startswith(user.user_id + ":"): continue
        item["result"] = service.get_task(item["task_id"], user.user_id).model_dump(mode="json")
        workspace = item["workspace"]
        database = workspace.get("database_id")
        if not database or not scope.allows_database(database): continue
        session_id = item["session_id"][len(user.user_id)+1:]
        suffix = ":" + database
        if session_id.endswith(suffix): session_id = session_id[:-len(suffix)]
        record = grouped.setdefault((session_id,database), {"id": session_id, "title": titles.get((session_id,database),item["query"]),
            "updatedAt": 0, "workspace": workspace, "turns": []})
        from datetime import datetime, timezone
        record["updatedAt"] = datetime.fromisoformat(item["updated_at"]).replace(tzinfo=timezone.utc).timestamp()*1000
        record["workspace"] = workspace
        replies = [m["content"] for m in service.context.archive.load_messages(item["session_id"])
                   if m.get("task_id") == item["task_id"] and m.get("metadata", {}).get("type") == "clarification"]
        question = item["query"] + ("\n补充口径：" + "；".join(replies) if replies else "")
        record["turns"].append({"query": question, "result": item["result"]})
    return sorted(grouped.values(), key=lambda r: r["updatedAt"], reverse=True)

@router.post('/sessions/{session_id}/fact-report',response_model=QueryResult)
def fact_report(session_id:str,payload:FactReportRequest,user:AuthUser=Depends(require_user)):
    try:return service.factual_report(session_id,payload.database_id,user.user_id)
    except PermissionError as error:raise HTTPException(403,str(error))
    except ValueError as error:raise HTTPException(400,str(error))


@router.patch('/sessions/{session_id}')
def rename_session(session_id: str,payload:SessionRename,user:AuthUser=Depends(require_user)):
    scope=service.access_controller.resolve(user.user_id)
    if not scope.allows_database(payload.database_id): raise HTTPException(403,'无权访问该数据源')
    owned=f'{user.user_id}:{session_id}:{payload.database_id}'
    if not service.session_tasks.get(owned): raise HTTPException(404,'分析不存在或不属于当前用户')
    service.context.archive.rename_session(user.user_id,session_id,payload.database_id,payload.title)
    return {'id':session_id,'title':payload.title}


REPORT_EDIT_LOCK=Lock()

@router.patch('/tasks/{task_id}/report')
def edit_report(task_id:str,payload:ReportEditRequest,user:AuthUser=Depends(require_user)):
    from datetime import datetime, timezone
    with REPORT_EDIT_LOCK:
        try: result=service.get_task(task_id,user.user_id)
        except KeyError: raise HTTPException(404,'报告不存在')
        except PermissionError: raise HTTPException(403,'无权编辑该报告')
        task=service.tasks[task_id]
        database=(task.get('workspace') or {}).get('database_id')
        if not service.access_controller.resolve(user.user_id).allows_database(database):raise HTTPException(403,'无权访问该数据源')
        if not result.report or result.status!='completed':raise HTTPException(400,'仅可编辑已完成报告的人工内容')
        revision=(result.report_edit or {}).get('revision',0)
        if payload.expected_revision!=revision:raise HTTPException(409,'报告已在其他页面更新，请刷新后重新编辑；本页草稿仍保留')
        edit=payload.model_dump(exclude={'expected_revision'})
        edit.update(revision=revision+1,editor_label=user.display_name,updated_at=datetime.now(timezone.utc).isoformat())
        updated=result.model_copy(update={'report_edit':edit})
        service.context.archive.save_turn(task_id,task['session_id'],task['query'],task['workspace'],updated.model_dump(mode='json'))
        result.report_edit=edit
        return edit


@router.get('/review/options')
def review_options(user:AuthUser=Depends(require_user)):
    from ..analysis_scope import category_options,STATES,STATUSES
    if not service.access_controller.resolve(user.user_id).allows_database(DATABASE):raise HTTPException(403,'无权访问该数据源')
    return {'states':STATES,'statuses':[{'value':k,'label':v} for k,v in STATUSES.items()],'categories':category_options()}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, database_id:str|None=None, user: AuthUser = Depends(require_user)):
    if any(j["user_id"] == user.user_id and j["session_id"] == session_id and (database_id is None or j.get("database_id")==database_id) and j["status"] == "running" for j in JOBS.values()):
        raise HTTPException(409, "该分析仍在后台执行，请完成后再删除记录")
    owned = [sid for sid in service.session_tasks if (sid==f'{user.user_id}:{session_id}:{database_id}' if database_id else sid.startswith(user.user_id + ":" + session_id + ":"))]
    archive = service.context.archive
    with archive._lock, archive._connect() as conn:
        conn.execute('DELETE FROM session_metadata WHERE user_id=? AND session_id=?'+(' AND database_id=?' if database_id else ''),(user.user_id,session_id,database_id) if database_id else (user.user_id,session_id))
        for sid in owned:
            conn.execute("DELETE FROM session_turns WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM session_messages WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM session_summaries WHERE session_id=?", (sid,))
            for tid in service.session_tasks.pop(sid, []): service.tasks.pop(tid, None)
    return {"deleted": True}

@router.get("/datasets")
def datasets(user: AuthUser = Depends(require_user)):
    return catalog(service.access_controller.resolve(user.user_id))

@router.get("/dashboard")
def dashboard_route(database_id: str, user: AuthUser = Depends(require_user)):
    try: return dashboard(service.access_controller.resolve(user.user_id), database_id)
    except PermissionError as error: raise HTTPException(403, str(error))
    except ValueError as error: raise HTTPException(400, str(error))

@router.post("/datasets/preview")
def preview_csv(payload: CsvInput, _: AuthUser = Depends(require_admin)):
    try: return inspect_csv(payload)
    except ValueError as error: raise HTTPException(400, str(error))

@router.post("/datasets/import")
def ingest_csv(payload: CsvInput, _: AuthUser = Depends(require_admin)):
    try: return import_csv(payload)
    except ValueError as error: raise HTTPException(400, str(error))

def result_data(task_id, user, page, size, sort, direction, filter_text):
    try: return browse_result(service, task_id, user.user_id, page, size, sort, direction, filter_text)
    except PermissionError as error: raise HTTPException(403, str(error))
    except KeyError: raise HTTPException(404, "结果不存在")
    except ValueError as error: raise HTTPException(400, str(error))

@router.get("/tasks/{task_id}/rows")
def result_rows(task_id: str, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200),
                sort: str = "", direction: str = Query("asc", pattern="^(asc|desc)$"), filter_text: str = Query("", max_length=200), user: AuthUser = Depends(require_user)):
    return result_data(task_id, user, page, size, sort, direction, filter_text)

@router.get("/tasks/{task_id}/export")
def export_result(task_id: str, sort: str = "", direction: str = Query("asc", pattern="^(asc|desc)$"), filter_text: str = Query("", max_length=200), user: AuthUser = Depends(require_user)):
    data = result_data(task_id, user, 1, 1000001, sort, direction, filter_text)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(data["columns"])
    # Prevent spreadsheet formulas from imported text.
    def cell(value):
        return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
    writer.writerows([[cell(row.get(c)) for c in data["columns"]] for row in data["rows"]])
    filename = quote(f'{PRODUCT_NAME}-{task_id}.csv', safe='')
    return Response("\ufeff"+output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=\"shuce-{task_id}.csv\"; filename*=UTF-8''{filename}", "X-Export-Rows": str(data["total_rows"])})

EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="askdata-job")
JOBS = {}
JOBS_LOCK = Lock()

@router.get("/metrics")
def metric_definitions(database_id: str, user: AuthUser = Depends(require_user)):
    scope=service.access_controller.resolve(user.user_id)
    if not scope.allows_database(database_id): raise HTTPException(403,"无权访问该数据源")
    return definitions() if database_id==DATABASE else []

@router.post("/jobs")
def start_job(payload: QueryRequest, user: AuthUser = Depends(require_user)):
    if payload.review_filters and not payload.review_month:
        raise HTTPException(400,'结构化筛选仅随月度复盘提交')
    if payload.review_targets and (not payload.review_month or payload.review_step != 'overview'):
        raise HTTPException(400, '目标只能随月度经营对比提交')
    workspace = payload.workspace.model_dump(exclude_none=True) if payload.workspace else {}
    try: service.access_controller.select_database(service.access_controller.resolve(user.user_id), workspace.get("database_id"))
    except PermissionError as error: raise HTTPException(403, str(error))
    metric_parameters = payload.metric_parameters.model_dump() if payload.metric_parameters else {"grain":"total"}
    query_text = payload.query
    if payload.review_month:
        if workspace.get("database_id") != DATABASE: raise HTTPException(400,"月度复盘仅适用于 Olist 数据源")
        if payload.metric_id: raise HTTPException(400,"不能同时选择月度复盘和单项指标")
        try:
            _,review_context,_=review_query(payload.review_month,payload.review_step,payload.review_filters.model_dump() if payload.review_filters else None)
            query_text=f"{payload.review_month} 月度复盘 · {STEPS[payload.review_step]} · 对比 {review_context['previous_month']}"
        except ValueError as error: raise HTTPException(400,str(error))
    if payload.metric_id:
        if workspace.get("database_id") != DATABASE: raise HTTPException(400,"标准指标仅适用于 Olist 数据源")
        try:
            _,contract=metric_query(payload.metric_id,**metric_parameters)
            period=contract["parameters"]
            query_text=f"{contract['label']} · 标准口径 v{contract['version']} · {period['start_date'] or '历史起始'} 至 {period['end_date'] or '历史结束'} · {period['grain']}"
        except ValueError as error: raise HTTPException(400,str(error))
    request_key = json.dumps([query_text, payload.review_targets.model_dump(mode="json",exclude_none=True) if payload.review_targets else None,payload.review_filters.model_dump() if payload.review_filters else None,{key:workspace.get(key) for key in ('continuation_task_id','retry_task_id','schema_fields','analysis_table_ids')}],sort_keys=True)
    with JOBS_LOCK:
        existing = next((j for j in JOBS.values() if j["user_id"] == user.user_id and j["session_id"] == payload.session_id and j.get("database_id") == workspace.get("database_id") and j["status"] == "running"), None)
        if existing:
            if existing.get("request_key", json.dumps([existing["query"],None,None])) != request_key: raise HTTPException(409, "该分析已有运行中的问题，请先查看已有任务")
            return existing
        max_running = 1 if os.getenv('SHUCE_PUBLIC_DEPLOYMENT', '').lower() in {'1','true','yes'} else 4
        if sum(j["status"] == "running" for j in JOBS.values()) >= max_running:
            raise HTTPException(429, "当前已有多个分析任务，请稍后重试")
        job = {"id": uuid.uuid4().hex, "user_id": user.user_id, "session_id": payload.session_id,
               "database_id": workspace.get("database_id"), "query": query_text, "request_key":request_key, "stage": "queued", "status": "running", "started_at": time.time(), "events": []}
        JOBS[job["id"]] = job
    def run():
        def update(name):
            job["stage"] = name
            job["events"].append({"stage": name, "at": time.time()})
        token = progress_callback.set(update)
        trace_token = trace_observer.set(lambda trace: job.update(execution_trace=trace))
        try:
            if payload.review_month:
                result = service.submit_review(payload.review_month,payload.review_step,payload.session_id,user.user_id,payload.review_targets.model_dump(mode="json",exclude_none=True) if payload.review_targets else None,payload.review_filters.model_dump() if payload.review_filters else None)
            elif payload.metric_id:
                result = service.submit_metric(payload.metric_id, metric_parameters, payload.session_id, user.user_id)
            else:
                result = service.submit(payload.query, payload.session_id, workspace, user.user_id)
            job["result"] = result.model_dump(mode="json")
            if result.execution_trace is not None:
                job["execution_trace"] = result.execution_trace
            job["status"] = "failed" if result.status == "failed" else "completed"
        except Exception:
            job["status"] = "failed"
            job["error"] = "任务执行失败，请检查后端日志或重试"
        finally:
            job["finished_at"] = time.time()
            progress_callback.reset(token)
            trace_observer.reset(trace_token)
    EXECUTOR.submit(run)
    return job

@router.get("/jobs")
def running_jobs(user: AuthUser = Depends(require_user)):
    return [j for j in JOBS.values() if j["user_id"] == user.user_id and j["status"] == "running"]

@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: AuthUser = Depends(require_user)):
    job = JOBS.get(job_id)
    if not job or job["user_id"] != user.user_id: raise HTTPException(404, "任务不存在")
    return job


@router.get('/review/templates')
def review_templates(user: AuthUser = Depends(require_user)):
    if not service.access_controller.resolve(user.user_id).allows_database(DATABASE): raise HTTPException(403,'无权访问该数据源')
    return service.context.archive.review_templates(user.user_id,DATABASE)


@router.post('/review/templates')
def save_review_template(payload: ReviewTemplateRequest, user: AuthUser = Depends(require_user)):
    from ..analysis_scope import normalize_filters
    scope=service.access_controller.resolve(user.user_id)
    if not scope.allows_database(DATABASE): raise HTTPException(403,'无权访问该数据源')
    try:
        filters=normalize_filters(payload.filters.model_dump())
        tid=service.context.archive.save_review_template(user.user_id,DATABASE,payload.name,filters)
        return {'id':tid,'name':payload.name,'filters':filters,'contract_version':'1.0'}
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc


@router.delete('/review/templates/{template_id}')
def delete_review_template(template_id: str, user: AuthUser = Depends(require_user)):
    if not service.context.archive.delete_review_template(user.user_id,DATABASE,template_id): raise HTTPException(404,'模板不存在或无权访问')
    return {'deleted':True}


@router.get('/data-readiness')
def data_readiness_route(database_id: str, user: AuthUser = Depends(require_user)):
    from ..data_readiness import inspect_readiness
    try: return inspect_readiness(service.access_controller.resolve(user.user_id),database_id)
    except PermissionError as exc: raise HTTPException(403,str(exc)) from exc
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc


@router.get('/review/examples')
def review_examples_route(user: AuthUser = Depends(require_user)):
    from ..review_examples import example_catalog
    try: return example_catalog(service.access_controller.resolve(user.user_id))
    except PermissionError as exc: raise HTTPException(403,str(exc)) from exc


@router.get('/review/examples/{example_id}')
def review_example_route(example_id: str, user: AuthUser = Depends(require_user)):
    from ..review_examples import example_detail
    try: return example_detail(service.access_controller.resolve(user.user_id),example_id)
    except PermissionError as exc: raise HTTPException(403,str(exc)) from exc
    except KeyError as exc: raise HTTPException(404,'场景不存在，请返回场景目录选择') from exc
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
