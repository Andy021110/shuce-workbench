<script setup lang="ts">
import { computed } from 'vue'
import type { QueryResult } from '../types'
import { capabilityName, elapsed, executionOutcome, executionStage, noModelReason, traceSummary } from '../executionTrace'
const props = defineProps<{result: QueryResult}>()
const trace = computed(() => props.result.execution_trace)
const stats = computed(() => trace.value ? traceSummary(trace.value) : null)
const outcome = computed(() => executionOutcome(props.result))
const mode = computed(() => ({single_database_agent:'模型 Agent 查询',qa:'模型辅助分析',qa_report:'模型辅助报告',
  canonical_metric:'标准指标 SQL',monthly_review:'月度复盘 SQL',buyer_cohort:'老客标准 SQL',
  date_confirmation:'日期确认',cohort_confirmation:'群体口径确认'}[props.result.workflow_mode || ''] || '业务工作流'))
const stageName = (stage: string) => executionStage[stage] || stage
</script>
<template>
  <section class="agent-execution" aria-label="Agent 调用情况">
    <div class="execution-summary">
      <div class="summary-heading"><strong>Agent 调用</strong><span class="invocation-label">{{ !trace ? '历史未采集' : trace.model_calls.length ? '已调用模型' : '未调用模型' }}</span><span class="outcome-label" :class="outcome.tone">{{ outcome.label }}</span></div>
      <p v-if="trace && stats" class="summary-counts">Chat {{ stats.chat }} · Embedding {{ stats.embedding }} · Rerank {{ stats.rerank }}<span>工具调用 {{ stats.tools }} 次 · {{ elapsed(trace.duration_ms) }}</span></p>
      <p v-if="trace && stats" class="request-status" :class="{failed:stats.failed > 0}">模型接口：成功 {{ trace.model_calls.filter(call=>call.success===true).length }} 次 · 失败 {{ stats.failed }} 次 · 网络重试 {{ stats.retries }} 次<span v-if="trace.model_calls.some(call=>call.success==null)"> · 有请求未记录结束状态</span><span v-if="trace.tool_calls.some(call=>call.success===false)"> · 工具执行有失败，展开查看</span></p>
      <p v-if="!trace || !trace.model_calls.length" class="trace-note">{{ noModelReason(result) }}</p>
      <p v-if="outcome.detail" class="outcome-note" :class="outcome.tone">{{ outcome.detail }}</p>
    </div>
    <details class="execution-disclosure">
    <summary>查看模型、工具与 Token 明细</summary>
    <div class="execution-content">
      <p class="run-mode"><strong>{{ mode }}</strong> · 任务 <code>{{ result.task_id }}</code></p>
      <p v-if="trace" class="trace-note">执行时间：{{ new Date(trace.started_at).toLocaleString('zh-CN') }}</p>
      <template v-if="trace && stats">
        <dl class="call-overview">
          <div><dt>模型请求</dt><dd>Chat {{ stats.chat }} · Embedding {{ stats.embedding }} · Rerank {{ stats.rerank }}</dd></div>
          <div><dt>请求情况</dt><dd>{{ stats.failed }} 次失败 · {{ stats.retries }} 次网络重试</dd></div>
          <div><dt>Chat Token</dt><dd v-if="stats.chat">输入 {{ stats.input === null ? '未返回' : stats.input.toLocaleString() }} · 输出 {{ stats.output === null ? '未返回' : stats.output.toLocaleString() }}<small>输入已记录 {{ stats.reported }}/{{ stats.chat }} 次 · 输出已记录 {{ stats.outputReported }}/{{ stats.chat }} 次</small></dd><dd v-else>未调用 Chat，无用量</dd></div>
          <div><dt>费用</dt><dd>未接入供应商账单，暂无金额</dd></div>
        </dl>
        <p v-if="!trace.model_calls.length">本次没有发出模型请求。标准 SQL、口径确认和事实整理可能直接由后端完成。</p>
        <p v-if="result.sql && ['canonical_metric','monthly_review','buyer_cohort'].includes(result.workflow_mode || '')">本次 SQL 由后端标准流程执行，不经过 MCP 工具。</p>
        <p class="trace-note">耗时为后端本次业务执行时间，不含排队与人工补充等待。请求数包含失败和重试；Token 仅汇总供应商实际返回的 Chat 用量，缓存 Token 已包含在输入内。异步历史摘要不计入本次。</p>
        <details v-if="trace.stages.length"><summary>执行阶段</summary><ol class="stage-list"><li v-for="(stage,index) in trace.stages" :key="index"><span>{{ stageName(stage.stage) }}</span><small>+{{ elapsed(stage.offset_ms) }}</small></li></ol></details>
        <h3 v-if="trace.model_calls.length">模型请求明细</h3>
        <ol class="call-list">
          <li v-for="(call,index) in trace.model_calls" :key="index">
            <div class="call-heading"><strong>{{ index + 1 }}. {{ stageName(call.stage) }} · {{ capabilityName(call.capability) }}</strong><span :class="{failed:call.success===false}">{{ call.success===true?'请求成功':call.success===false?'请求失败':'未记录结束状态' }} · {{ elapsed(call.duration_ms) }}</span></div>
            <p>{{ call.model }} · {{ call.provider }}<span v-if="call.attempt > 1"> · 第 {{ call.attempt }} 次尝试</span></p>
            <p v-if="call.error" class="failed">{{ call.error }}</p>
            <p v-if="call.usage">输入 {{ call.usage.prompt_tokens ?? call.usage.input_tokens ?? '未返回' }} · 输出 {{ call.usage.completion_tokens ?? call.usage.output_tokens ?? '未返回' }}<span v-if="call.usage.total_tokens != null"> · 总 Token {{ call.usage.total_tokens }}</span><span v-if="call.usage.prompt_cache_hit_tokens != null"> · 输入缓存命中 {{ call.usage.prompt_cache_hit_tokens }}</span></p>
            <p v-else>Token 用量未返回</p>
          </li>
        </ol>
        <h3 v-if="trace.tool_calls.length">MCP 工具与发现</h3>
        <ol class="call-list"><li v-for="(call,index) in trace.tool_calls" :key="index">
          <div class="call-heading"><strong>{{ call.operation==='discovery'?'发现工具':'调用工具' }} · {{ call.name }}</strong><span :class="{failed:call.success===false}">{{ call.success===true?'成功':call.success===false?'失败':'未记录结束状态' }} · {{ elapsed(call.duration_ms) }}</span></div>
          <p v-if="call.row_count != null">返回 {{ call.row_count }} 行</p><p v-if="call.tool_count != null">发现 {{ call.tool_count }} 个工具，实际可用范围仍受权限与 Skill 限制</p><p v-if="call.error" class="failed">{{ call.error }}</p>
        </li></ol>
        <p v-if="result.sql">执行的 SQL 可在下方结果表的「SQL」中查看。</p>
      </template>
      <template v-else>
        <p>该历史任务未记录模型请求数、Token 和耗时；不能从查询结果倒推调用用量。</p>
        <p v-for="call in result.tool_calls" :key="call.call_index">已保存的数据库执行：{{ call.database }} · {{ call.success?'成功':'失败' }} · {{ call.row_count }} 行</p>
      </template>
      <p v-if="result.status==='failed'" class="failed">本次业务未完成，请结合下方失败信息判断；模型接口请求成功不代表业务校验通过。</p>
    </div>
    </details>
  </section>
</template>
<style scoped>
.agent-execution{margin:12px 0;border:1px solid #ccd7d0;border-radius:8px;background:#f8faf8;color:#263d30;font-size:13px;line-height:1.65;overflow-wrap:anywhere}.agent-execution summary{cursor:pointer;padding:10px 14px;min-height:24px}.agent-execution summary>span{margin-left:10px;color:#4e6357;font-size:12px}.agent-execution summary:focus-visible{outline:3px solid #5a9279;outline-offset:2px}.execution-summary{padding:12px 14px 4px}.summary-heading{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.summary-heading>strong{font-size:14px}.invocation-label,.outcome-label{font-size:12px;padding:1px 7px;border-radius:4px;background:#e7eee9}.outcome-label.neutral{color:#4e6357;background:transparent}.outcome-label.warning{background:#fff0d5;color:#81521a}.outcome-label.danger{background:#fae6e3;color:#97332b}.summary-counts{margin:8px 0 2px;font-variant-numeric:tabular-nums}.summary-counts>span{display:inline-block;margin-left:16px}.request-status{margin:2px 0;color:#4e6357;font-size:12px}.outcome-note{margin:8px 0 4px}.outcome-note.warning{color:#81521a}.outcome-note.danger{color:#97332b}.execution-summary .trace-note{margin:6px 0}.execution-content{padding:0 16px 14px;border-top:1px solid #dce3de}.execution-content p{margin:8px 0}.run-mode code{font-size:12px}.call-overview{display:grid;grid-template-columns:1fr 1fr;gap:12px 24px;margin:14px 0}.call-overview dt{color:#4e6357;font-size:12px}.call-overview dd{margin:2px 0 0}.call-overview small{display:block;color:#4e6357}.trace-note{color:#4e6357;font-size:12px}.execution-content h3{font-size:13px;margin:18px 0 8px}.call-list{padding:0;list-style:none;margin:0}.call-list li{padding:10px 0;border-bottom:1px solid #dce3de}.call-list p{color:#4e6357;font-size:12px}.call-heading{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.call-heading strong{font-weight:500}.call-heading>span{font-size:12px}.failed,.call-list p.failed{color:#97332b}.stage-list{padding-left:22px}.stage-list li{padding:4px 0}.stage-list small{margin-left:12px;color:#4e6357}@media(max-width:650px){.summary-counts>span{display:block;margin-left:0}.call-overview{grid-template-columns:1fr}.agent-execution summary>span{display:block;margin-left:16px}.call-heading{display:block}.call-heading>span{display:block}}
</style>
