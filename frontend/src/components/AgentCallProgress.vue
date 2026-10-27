<script setup lang="ts">
import { computed } from 'vue'
import type { ExecutionTrace } from '../types'
import { capabilityName, elapsed, executionStage, traceSummary } from '../executionTrace'
const props = defineProps<{trace?:ExecutionTrace|null; stage?:string; failed?:boolean; paused?:boolean}>()
const stats = computed(()=>props.trace ? traceSummary(props.trace) : null)
const recentCalls = computed(()=>[
  ...(props.trace?.model_calls || []).map((call,index)=>({...call,label:`${capabilityName(call.capability)} · ${call.model}`,kind:'模型',index})),
  ...(props.trace?.tool_calls || []).map((call,index)=>({...call,label:call.name,kind:call.operation==='discovery'?'发现工具':'工具',index})),
].sort((a,b)=>a.offset_ms-b.offset_ms).slice(-5))
const stateLabel = (success?:boolean) => success===true?'成功':success===false?'失败':props.failed?'结束状态未记录':props.paused?'暂停更新时未结束':'请求中'
</script>
<template>
  <section class="live-calls" aria-label="Agent 实时调用">
    <div class="live-summary" role="status" aria-live="polite" aria-atomic="true">
      <strong>Agent 调用 · {{ failed?'任务已中断':paused?'已暂停页面更新':'运行中' }}</strong>
      <span v-if="stats">{{ executionStage[stage || ''] || '准备请求' }} · 已发出 {{ trace?.model_calls.length }} 次模型请求 · {{ stats.failed }} 次接口失败 · {{ stats.tools }} 次工具调用</span>
      <span v-else>{{ failed?'中断前未采集到调用记录':'等待后端调用记录' }}</span>
    </div>
    <p v-if="!trace?.model_calls.length">尚未发出模型请求。排队、口径确认或标准 SQL 处理阶段可能无需模型。</p>
    <ol v-if="recentCalls.length">
      <li v-for="call in recentCalls" :key="`${call.kind}-${call.index}`">
        <div><span>{{ call.kind }}</span><strong>{{ call.label }}</strong><span class="call-state" :class="{failed:call.success===false}">{{ stateLabel(call.success) }}<template v-if="call.duration_ms!=null"> · {{ elapsed(call.duration_ms) }}</template></span></div>
        <small>{{ executionStage[call.stage] || call.stage }}<template v-if="call.error"> · {{ call.error }}</template></small>
      </li>
    </ol>
    <small class="update-note">{{ paused?'恢复查看后继续同步；后台任务仍在运行。':failed?'已保留中断前采集的实际调用记录。':'约每秒同步，展示最近 5 条实际请求；短请求可能在两次更新间结束，完整明细随结果保存。' }}</small>
  </section>
</template>
<style scoped>
.live-calls{margin-top:10px;padding:12px 14px;border:1px solid #ccd7d0;border-radius:8px;background:#f8faf8;color:#263d30;font-size:13px;line-height:1.65;overflow-wrap:anywhere}.live-summary{display:grid;gap:4px}.live-summary>span,.live-calls p,.update-note{font-size:12px;color:#4e6357}.live-calls p{margin:6px 0}.live-calls ol{list-style:none;padding:0;margin:10px 0}.live-calls li{padding:7px 0;border-top:1px solid #dce3de}.live-calls li>div{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px}.live-calls li strong{font-weight:500}.live-calls li span,.live-calls li small{font-size:12px}.call-state{margin-left:auto}.failed{color:#97332b}.update-note{display:block}@media(max-width:650px){.call-state{margin-left:0}}
</style>
