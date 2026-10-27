export interface SchemaField {
  name: string
  label: string
  type: string
  description?: string
  aliases?: string[]
  role?: string
  aggregation?: string
  source_type?: string
  data_profile?: Record<string, unknown>
  index_content?: Record<string, string>
}

export interface SchemaTable {
  id: string
  name?: string
  label: string
  description: string
  fields: SchemaField[]
  database?: string
  domain?: string
  business_terms?: string[]
  primary_key?: string[]
  row_count?: number
}

export interface ClarificationOption {
  id: string
  label: string
  description: string
  recommended: boolean
}

export interface SchemaHit {
  doc_id: string
  table_id: string
  table_label: string
  field_name: string
  field_label: string
  score: number
  source?: "retrieval" | "user_confirmed" | "relation_key"
  bm25_rank?: number | null
  dense_rank?: number | null
  rerank_score?: number
}

export interface SchemaGraph {
  database: string
  graph_version: string
  tables: { id: string; name?: string; label: string; description: string; domain: string; database: string }[]
  fields: {
    id: string
    table_id: string
    table_name?: string
    sql_name?: string
    name: string
    label: string
    type: string
    description: string
    role: string
    source: string
    score: number
  }[]
  joins: {
    left_table: string
    left_field: string
    right_table: string
    right_field: string
    description: string
    relation_type: string
  }[]
}

export interface DatabaseHandoff {
  database: { name: string; dialect: string; route: string }
  required_context: {
    schema_graph: SchemaGraph
    resolved_parameters: Record<string, unknown>
    tool_facts: { tool: string; arguments: Record<string, unknown>; result: Record<string, unknown> }[]
  }
  instruction: {
    task: string
    filters: string[]
    joins: string[]
    group_by: string[]
    metrics: string[]
    order_by: string[]
  }
  output_contract: {
    row_grain: string
    columns: { name: string; alias: string; type: string }[]
    max_rows: number
    empty_result_policy: string
  }
}

export interface ExecutionTrace {
  version: number
  started_at: string
  duration_ms: number
  stages: {stage: string; offset_ms: number}[]
  model_calls: {stage: string; offset_ms: number; duration_ms?: number; capability: string;
    provider: string; model: string; attempt: number; success?: boolean; error?: string;
    usage?: Record<string, number> | null}[]
  tool_calls: {stage: string; offset_ms: number; duration_ms?: number; name: string;
    operation: string; success?: boolean; error?: string | null; row_count?: number | null; tool_count?: number}[]
}

export interface CoderToolCall {
  call_index: number
  database: string
  arguments: DatabaseHandoff | {
    mode: "single_database_agent"
    transport: "mcp_in_process"
    tool_name?: string
    schema_graph_version?: string
    sql_source?: string
  }
  sql: string
  success: boolean
  row_count: number
  error?: string | null
}

export interface VisualizationSpec {
  type: "bar" | "pie"
  title: string
  source_task_id: string
  category_field: string
  value_field: string
  max_items: number
}

export interface AnalysisReport {
  title: string
  markdown: string
  visualizations: VisualizationSpec[]
  fact_references?: {id:string; source_task_id:string; source_title:string; label:string; display:string; scope:string}[]
  generation_mode?: 'model_with_facts' | 'facts_only' | 'backend_facts'
  validation_feedback?:string[]
  evidence_markdown?:string
  validation_codes?:string[]
}

export interface ReportToolCall {
  call_index: number
  tool: "build_bar_chart" | "build_pie_chart"
  arguments: Record<string, unknown>
  result: VisualizationSpec
}

export interface MetricContract {
  id: string
  label: string
  version: string
  database_id: string
  definition: string
  time_field: string
  grain: string
  currency: string | null
  status_scope: string
  grains: string[]
  outputs: string[]
  parameters?: {start_date: string | null; end_date: string | null; grain: string}
}
export interface MetricSelection {metric_id: string; start_date: string | null; end_date: string | null; grain: string}
export interface ResultProfile {
  metric_totals?: {values: Record<string,string|null>; note: string}
  coverage: string
  row_count: number | null
  analyzed_rows: number
  numeric: {field: string; count: number; missing: number; sum: string | null; mean: string; min: number; max: number; min_rows: Record<string,unknown>[]; max_rows: Record<string,unknown>[]}[]
  categories: {field: string; count: number; missing: number; distinct: number; top: {value: string; rows: number; row_share: number}[]}[]
  temporal: null | {field: string; grain: string; covered_rows: number; missing_dates: number; measure_columns: string[]; buckets: Record<string,string|number|null>[]; note: string}
  omitted_columns: string[]
  scope_note: string
  aggregation_note: string
}
export interface ReviewFilters {states:string[]; order_statuses:string[]; categories:string[]}
export interface ReportEdit {title:string;conclusion:string;hypotheses:string;actions:string;revision:number;updated_at:string;editor_label:string}
export interface AnalysisScope {database_id:string;mode:string;filters:ReviewFilters;exclusions?:ReviewFilters;inherited:boolean;time_label:string;period_label:string;statuses:string[];states:string[];categories:string[];currency:string;predicates:string[];note:string}
export interface ReviewTargets { orders?:number; item_sales?:number; late_rate?:number }
export interface TargetComparison {
  metric:string; label:string; unit:string; direction:'at_least'|'at_most';
  actual:string|null; target:string; gap:string|null; achievement_rate:string|null; status:string
}
export interface QueryResult {
  task_id: string
  status: "waiting_clarification" | "completed" | "failed"
  route: "database_query" | "data_qa" | "direct_response"
  message: string
  interpretation: null | {
    metric: string
    dimension: string
    time_range: string
    table: string
    assumptions: string[]
  }
  clarification: null | {
    parameter?: string
    question: string
    reason: string
    options: ClarificationOption[]
  }
  steps: string[]
  sql: string | null
  columns: string[]
  rows: Record<string, string | number | null>[]
  analysis: string | null
  total_rows?: number | null
  result_profile?: ResultProfile | null
  metric_contracts?: MetricContract[]
  metric_basis?: string | null
  execution_log?: {stage:string;success:boolean;error?:string}[]
  execution_trace?: ExecutionTrace | null
  analysis_scope?:AnalysisScope
  query_intent?: {filters:ReviewFilters;periods:string[];grain?:string|null;validated?:boolean;can_continue?:boolean}
  continuation?: {source_task_id:string;source_query:string;can_continue:boolean;intent?:{filters:ReviewFilters;periods:string[]}}
  query_validation?: {reason:string;original_analysis?:string}
  report_edit?:ReportEdit|null
  analysis_basis?: {time_field:string; time_label:string; source:string; verified?:boolean; previous_time_label?:string|null}
  review_context?: {month:string; previous_month:string; step:string; version:string; targets?:ReviewTargets; filters?:ReviewFilters; target_comparison?:TargetComparison[]}
  snapshot_id?: string | null
  truncated?: boolean
  saved: boolean
  result_title?: string | null
  standalone_query?: string | null
  schema_graph?: SchemaGraph | null
  retrieval?: null | {
    threshold: number
    bm25_count: number
    dense_count: number
    rrf_count: number
    candidate_count: number
    selected_count: number
    embedding_source: string
    rerank_source: string
    hits: SchemaHit[]
    schema_graph?: SchemaGraph
  }
  tool_calls?: CoderToolCall[]
  analysis_sources?: AnalysisSource[]
  answer_checks?: {question:string;status:string;answer:string}[]
  workflow_mode?: "qa" | "single_database_fast_path" | "multi_database_handoff" | "langgraph_hitl" | string | null
  report?: AnalysisReport | null
  report_tool_calls?: ReportToolCall[]
}

export interface ReportDataSource {
  currency?: string
  totalRows?: number | null
  previewOnly?: boolean
  coverage?: string
  underlyingRows?: number
  excludedRows?: number
  taskId: string
  title: string
  columns: string[]
  rows: Record<string, string | number | null>[]
}

export interface WorkspaceConfig {
  continuation_task_id?:string
  retry_task_id?:string
  database_id?: string
  schema_fields?: ConfirmedField[]
  analysis_table_ids?: string[]
  analysis_tables?: AnalysisTablePayload[]
  fields?: ConfirmedField[]
}

export interface AnalysisTablePayload {
  task_id: string
  title: string
  query: string
  columns: string[]
  rows: Record<string, string | number | null>[]
}

export interface AnalysisSource {
  task_id: string
  title: string
  query: string
  row_count: number
  columns: string[]
}

export interface ConfirmedField {
  name: string
  aggregation?: "auto"
  tableId?: string
}

export interface SavedMemory {
  id: string
  kind: "result_table" | "schema_field"
  created_at: string
  task_id?: string
  query?: string
  summary?: string
  title?: string
  columns?: string[]
  rows?: Record<string, string | number | null>[]
  table_id?: string
  name?: string
  label?: string
  field_type?: string
  user_id?: string
}

export interface AuthUser {
  user_id: string
  username: string
  display_name: string
  role: "admin" | "current_sales" | string
}

export interface LoginResponse {
  access_token: string
  token_type: "bearer"
  user: AuthUser
}
