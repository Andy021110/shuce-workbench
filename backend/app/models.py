from __future__ import annotations

from typing import Annotated, Any, Literal
from decimal import Decimal

from pydantic import BaseModel, Field, StringConstraints


class ConfirmedField(BaseModel):
    name: str
    aggregation: Literal["auto", "group", "sum", "avg", "count", "max", "min"] = "auto"
    tableId: str | None = None


class AnalysisTableInput(BaseModel):
    task_id: str
    title: str
    query: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class WorkspaceConfig(BaseModel):
    database_id: str | None = None
    schema_fields: list[ConfirmedField] = Field(default_factory=list)
    analysis_table_ids: list[str] = Field(default_factory=list)
    analysis_tables: list[AnalysisTableInput] = Field(default_factory=list)
    continuation_task_id: str | None = Field(default=None,max_length=64)
    # 兼容旧版前端的 fields 属性。
    fields: list[ConfirmedField] = Field(default_factory=list)


class MetricParameters(BaseModel):
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    grain: Literal["total", "month", "day"] = "total"


class ReviewFilters(BaseModel):
    model_config = {"extra": "forbid"}
    states: list[Literal['AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO']] = Field(default_factory=list,max_length=27)
    order_statuses: list[Literal['delivered','shipped','canceled','unavailable','invoiced','processing','approved','created']] = Field(default_factory=list,max_length=8)
    categories: list[Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=80,pattern=r'^(?:[a-z0-9_]+|未分类)$')]] = Field(default_factory=list,max_length=20)


class ReviewTemplateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=60)]
    filters: ReviewFilters = Field(default_factory=ReviewFilters)


class FactReportRequest(BaseModel):
    database_id: str = Field(min_length=1,max_length=100)


class SessionRename(BaseModel):
    database_id: str = Field(min_length=1,max_length=100)
    title: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=80)]


class ReportEditRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    title: Annotated[str,StringConstraints(strip_whitespace=True,max_length=120)] = ''
    conclusion: str = Field(default='',max_length=4000)
    hypotheses: str = Field(default='',max_length=4000)
    actions: str = Field(default='',max_length=4000)


class ReviewTargets(BaseModel):
    """User-defined monthly goals. Empty fields are not inferred from history."""
    model_config = {"extra": "forbid"}
    orders: int | None = Field(default=None, ge=0, le=1_000_000_000, strict=True)
    item_sales: Decimal | None = Field(default=None, ge=0, le=1_000_000_000_000, decimal_places=2, allow_inf_nan=False)
    late_rate: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2, allow_inf_nan=False)


class QueryRequest(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    metric_id: str | None = Field(default=None, max_length=64)
    metric_parameters: MetricParameters | None = None
    review_month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    review_step: Literal["overview", "delivery_states", "sales_categories", "sales_drivers"] = "overview"
    review_targets: ReviewTargets | None = None
    review_filters: ReviewFilters | None = None
    session_id: str = "demo-session"
    workspace: WorkspaceConfig | None = None


class ClarificationRequest(BaseModel):
    option_id: str


class ClarificationOption(BaseModel):
    id: str
    label: str
    description: str
    recommended: bool = False


class Clarification(BaseModel):
    parameter: str = "other"
    question: str
    reason: str
    options: list[ClarificationOption]


class Interpretation(BaseModel):
    metric: str
    dimension: str
    time_range: str
    table: str
    assumptions: list[str] = []


class VisualizationSpec(BaseModel):
    type: Literal["bar", "pie"]
    title: str
    source_task_id: str
    category_field: str
    value_field: str
    max_items: int = Field(default=12, ge=1, le=30)


class AnalysisReport(BaseModel):
    title: str
    markdown: str
    visualizations: list[VisualizationSpec] = Field(default_factory=list)
    fact_references: list[dict[str, Any]] = Field(default_factory=list)
    generation_mode: Literal["model_with_facts", "facts_only", "backend_facts"] = "model_with_facts"
    validation_feedback: list[str] = Field(default_factory=list)


class QueryResult(BaseModel):
    task_id: str
    status: Literal["waiting_clarification", "completed", "failed"]
    route: Literal["database_query", "data_qa", "direct_response"]
    message: str
    interpretation: Interpretation | None = None
    clarification: Clarification | None = None
    steps: list[str] = []
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    analysis: str | None = None
    saved: bool = False
    total_rows: int | None = None
    snapshot_id: str | None = None
    truncated: bool = False
    result_profile: dict[str, Any] | None = None
    metric_contracts: list[dict[str, Any]] = Field(default_factory=list)
    metric_basis: str | None = None
    analysis_basis: dict[str, Any] | None = None
    review_context: dict[str, Any] | None = None
    analysis_scope: dict[str, Any] | None = None
    query_intent: dict[str, Any] | None = None
    continuation: dict[str, Any] | None = None
    query_validation: dict[str, Any] | None = None
    report_edit: dict[str, Any] | None = None
    route_reason: str | None = None
    retrieval: dict[str, Any] | None = None
    execution_log: list[dict[str, Any]] = Field(default_factory=list)
    execution_trace: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    result_title: str | None = None
    analysis_sources: list[dict[str, Any]] = Field(default_factory=list)
    standalone_query: str | None = None
    schema_graph: dict[str, Any] | None = None
    workflow_mode: str | None = None
    report: AnalysisReport | None = None
    report_tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class SchemaField(BaseModel):
    name: str
    label: str
    type: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    role: str = ""
    aggregation: str = "none"
    source_type: str = ""
    data_profile: dict[str, Any] = Field(default_factory=dict)
    index_content: dict[str, str] = Field(default_factory=dict)


class SchemaTable(BaseModel):
    id: str
    name: str = ""
    label: str
    description: str
    fields: list[SchemaField]
    database: str = "short_video_ops"
    domain: str = ""
    business_terms: list[str] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    row_count: int = 0


class SaveMemoryRequest(BaseModel):
    task_id: str


class SaveFieldMemoryRequest(BaseModel):
    table_id: str
    name: str
    label: str
    field_type: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
