import type { ExecutionTrace, QueryResult } from './types'

export const executionStage: Record<string, string> = {
  prepare_request: '准备请求', preprocess: '理解问题与路由', retrieve_schema: '检索数据结构',
  prepare_single_database: 'Agent 规划与工具执行', execute_single_database: '核对查询结果',
  result_analysis: '生成结果说明', answer_qa: '综合分析与报告', respond_directly: '直接回答',
  human_clarification: '等待补充信息', metric_sql: '标准 SQL 计算', run_multi_database: '多库检查',
}
export function elapsed(ms?: number) { return ms == null ? '耗时未记录' : `${(ms / 1000).toFixed(1)} 秒` }
export function traceSummary(trace: ExecutionTrace) {
  const chat = trace.model_calls.filter(call => call.capability === 'chat')
  const reported = chat.filter(call => call.usage?.prompt_tokens != null || call.usage?.input_tokens != null)
  const input = reported.reduce((sum, call) => sum + (call.usage?.prompt_tokens ?? call.usage?.input_tokens ?? 0), 0)
  const outputCalls = chat.filter(call => call.usage?.completion_tokens != null || call.usage?.output_tokens != null)
  const output = outputCalls.reduce((sum, call) => sum + (call.usage?.completion_tokens ?? call.usage?.output_tokens ?? 0), 0)
  return { chat: chat.length, embedding: trace.model_calls.filter(call => call.capability === 'embedding').length,
    rerank: trace.model_calls.filter(call => call.capability === 'rerank').length,
    tools: trace.tool_calls.filter(call => call.operation === 'call').length,
    retries: trace.model_calls.filter(call => call.attempt > 1).length,
    failed: trace.model_calls.filter(call => call.success === false).length,
    input: reported.length ? input : null, output: outputCalls.length ? output : null,
    reported: reported.length, outputReported: outputCalls.length }
}

export const capabilityName = (name: string) => ({chat:'Chat',embedding:'Embedding',rerank:'Rerank'}[name] || name)

export function executionOutcome(result: QueryResult) {
  if (result.report?.generation_mode === 'facts_only') return {
    tone:'warning', label:'报告解读校验未通过',
    detail:'模型已返回内容，但数值或范围校验未通过，自动生成部分仅保留后端事实。请查看报告反馈或重试解读。',
  }
  if (result.status === 'waiting_clarification' || result.execution_log?.some(event => event.stage === 'scope_inheritance' && !event.success)) return {
    tone:'warning', label:'等待确认，查询尚未执行', detail:'请在下方补充口径或确认分析范围后继续。',
  }
  if (result.status === 'failed') return {tone:'danger', label:'业务未完成', detail:'请结合失败信息判断原因；接口请求成功仍可能出现 SQL 或业务校验失败。'}
  return {tone:'neutral', label:'结果已返回', detail:''}
}

export function noModelReason(result: QueryResult) {
  if (!result.execution_trace) return '历史任务未采集调用记录，无法判断是否调用。'
  if (['canonical_metric','monthly_review','buyer_cohort'].includes(result.workflow_mode || '')) return '本次由后端直接执行标准 SQL，无需调用模型，也不经过 MCP 工具。'
  if (result.report?.generation_mode === 'backend_facts') return '本次由后端整理查询事实，无需调用模型。'
  if (['date_confirmation','cohort_confirmation'].includes(result.workflow_mode || '') || result.status === 'waiting_clarification') return '本次先确认业务口径，尚未发出模型请求。'
  if (result.status === 'failed') return '任务在发出模型请求前结束，请查看下方失败信息。'
  return '本次未记录到模型请求，由后端业务流程处理；可展开明细核对执行方式。'
}
