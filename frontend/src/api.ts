import type { BusinessExample, BusinessExampleDetail } from './exampleTypes'
import type { AuthUser, LoginResponse, QueryResult, SavedMemory, SchemaField, SchemaTable, WorkspaceConfig, ReviewTargets, ReviewFilters, ReportEdit } from "./types"

export const API_BASE = import.meta.env.VITE_API_BASE ?? ""
const TOKEN_KEY = "askdata_access_token"

function token() {
  return sessionStorage.getItem(TOKEN_KEY)
}

export async function request<T>(path: string, options?: RequestInit, timeoutMs = 90_000): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        ...options?.headers,
      },
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("请求处理超时，后端可能仍在构建Schema索引或等待模型响应")
    }
    throw new Error(import.meta.env.VITE_PUBLIC_DEPLOYMENT === 'true' ? "暂时无法连接分析服务，请稍后重试" : "无法连接后端服务，请先启动 FastAPI（127.0.0.1:8000）")
  } finally {
    window.clearTimeout(timeout)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? body.detail.map((item: {msg?: string}) => item.msg || "输入格式无效").join("；") : "服务暂时不可用"
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  reviewExamples:()=>request<BusinessExample[]>('/api/review/examples'),
  reviewExample:(id:string)=>request<BusinessExampleDetail>(`/api/review/examples/${encodeURIComponent(id)}`),
  dataReadiness:(database:string)=>request<any>(`/api/data-readiness?database_id=${encodeURIComponent(database)}`),
  reviewTemplates:()=>request<{id:string;name:string;filters:ReviewFilters;contract_version:string}[]>('/api/review/templates'),
  saveReviewTemplate:(name:string,filters:ReviewFilters)=>request<{id:string}>('/api/review/templates',{method:'POST',body:JSON.stringify({name,filters})}),
  deleteReviewTemplate:(id:string)=>request(`/api/review/templates/${encodeURIComponent(id)}`,{method:'DELETE'}),
  factReport:(id:string,databaseId:string)=>request<QueryResult>(`/api/sessions/${encodeURIComponent(id)}/fact-report`,{method:'POST',body:JSON.stringify({database_id:databaseId})}),
  task:(id:string)=>request<QueryResult>(`/api/tasks/${encodeURIComponent(id)}`),
  sessions: () => request<any[]>("/api/sessions"),
  renameSession: (id:string,databaseId:string,title:string)=>request<{id:string;title:string}>(`/api/sessions/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({database_id:databaseId,title})}),
  editReport: (taskId:string,edit:Omit<ReportEdit,'revision'|'updated_at'|'editor_label'>,revision:number)=>request<ReportEdit>(`/api/tasks/${encodeURIComponent(taskId)}/report`,{method:'PATCH',body:JSON.stringify({...edit,expected_revision:revision})}),
  reviewOptions: ()=>request<{states:string[];statuses:{value:string;label:string}[];categories:string[]}>('/api/review/options'),
  deleteSession: (id: string,databaseId:string) => request(`/api/sessions/${encodeURIComponent(id)}?database_id=${encodeURIComponent(databaseId)}`, {method: "DELETE"}),
  dashboard: (database: string) => request<any>(`/api/dashboard?database_id=${encodeURIComponent(database)}`),
  datasets: () => request<any[]>("/api/datasets"),
  csvPreview: (payload: unknown) => request<any>("/api/datasets/preview", {method: "POST", body: JSON.stringify(payload)}),
  csvImport: (payload: unknown) => request<any>("/api/datasets/import", {method: "POST", body: JSON.stringify(payload)}),
  startReviewJob: (month:string,step:string,sessionId:string,targets?:ReviewTargets,filters?:ReviewFilters) => request<any>('/api/jobs',{method:'POST',body:JSON.stringify({query:'月度经营复盘',session_id:sessionId,workspace:{database_id:'olist_ecommerce'},review_month:month,review_step:step,review_targets:targets,review_filters:filters})}),
  startMetricJob: (metric: {metric_id: string; start_date: string | null; end_date: string | null; grain: string}, sessionId: string) => request<any>("/api/jobs", {method:"POST",body:JSON.stringify({query:"按标准指标口径查询",session_id:sessionId,workspace:{database_id:"olist_ecommerce"},metric_id:metric.metric_id,metric_parameters:{start_date:metric.start_date,end_date:metric.end_date,grain:metric.grain}})}),
  startJob: (query: string, workspace: WorkspaceConfig, sessionId: string) => request<any>("/api/jobs", {method: "POST", body: JSON.stringify({query, workspace, session_id: sessionId})}),
  job: (id: string) => request<any>(`/api/jobs/${id}`),
  jobs: () => request<any[]>("/api/jobs"),
  rows: (id: string, params: URLSearchParams) => request<any>(`/api/tasks/${id}/rows?${params}`),
  exportRows: async (id: string, params: URLSearchParams) => {
    const response = await fetch(`${API_BASE}/api/tasks/${id}/export?${params}`, {headers: {Authorization: `Bearer ${token()}`}})
    if (!response.ok) throw new Error("导出失败，请重试")
    return response.blob()
  },
  hasSession: () => Boolean(token()),
  login: async (username: string, password: string) => {
    const result = await request<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    })
    sessionStorage.setItem(TOKEN_KEY, result.access_token)
    return result.user
  },
  me: () => request<AuthUser>("/api/auth/me"),
  logout: async () => {
    try {
      if (token()) await request<{ logged_out: boolean }>("/api/auth/logout", { method: "POST" })
    } finally {
      sessionStorage.removeItem(TOKEN_KEY)
    }
  },
  clearSession: () => sessionStorage.removeItem(TOKEN_KEY),
  schema: () => request<SchemaTable[]>("/api/schema"),
  query: (query: string, workspace: WorkspaceConfig, sessionId: string) =>
    request<QueryResult>("/api/query", {
      method: "POST",
      body: JSON.stringify({ query, session_id: sessionId, workspace }),
    }, 300_000),
  clarify: (taskId: string, optionId: string) =>
    request<QueryResult>(`/api/tasks/${taskId}/clarify`, {
      method: "POST",
      body: JSON.stringify({ option_id: optionId }),
    }, 300_000),
  save: (taskId: string) =>
    request<{ saved: boolean }>("/api/memories", {
      method: "POST",
      body: JSON.stringify({ task_id: taskId }),
    }),
  memories: () => request<SavedMemory[]>("/api/memories"),
  saveField: (tableId: string, field: SchemaField) =>
    request<{ saved: boolean }>("/api/memories/fields", {
      method: "POST",
      body: JSON.stringify({
        table_id: tableId,
        name: field.name,
        label: field.label,
        field_type: field.type,
      }),
    }),
  deleteMemory: (memoryId: string) =>
    request<{ deleted: boolean }>(`/api/memories/${encodeURIComponent(memoryId)}`, {
      method: "DELETE",
    }),
}
