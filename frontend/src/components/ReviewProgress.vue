<script setup lang="ts">
import { computed } from 'vue'
import type { QueryResult } from '../types'
import { deriveReviewProgress } from '../reviewProgress'
const props=defineProps<{turns:{result:QueryResult}[];review:NonNullable<QueryResult['review_context']>;busy:boolean}>()
const emit=defineEmits<{run:[step:string];report:[];jump:[taskId:string]}>()
const steps=[['overview','看变化'],['sales_drivers','拆量价'],['sales_categories','查品类'],['delivery_states','查配送']]
const progress=computed(()=>deriveReviewProgress(props.turns,props.review))
const results=computed(()=>progress.value.results)
const report=computed(()=>progress.value.report)
const count=computed(()=>steps.filter(([step])=>results.value.has(step!)).length+(report.value?1:0))
</script>
<template>
<section class="review-progress" aria-label="我的复盘任务进展">
  <header><div><h2>我的复盘任务</h2><p>{{review.month}} · 当前分析范围 · 已完成 {{count}} / 5 项分析步骤</p></div><span>业务原因与交付结论仍需人工核查</span></header>
  <ol><li v-for="[step,label] in steps" :key="step"><button type="button" :disabled="busy" @click="results.has(step!)?emit('jump',results.get(step!)!.task_id):emit('run',step!)"><span :class="{complete:results.has(step!)}">{{results.has(step!)?'已完成':'待执行'}}</span><strong>{{label}}</strong><small>{{results.has(step!)?'回看结果':'按当前范围计算'}}</small></button></li><li><button type="button" :disabled="busy || !results.size" @click="report?emit('jump',report.task_id):emit('report')"><span :class="{complete:!!report}">{{report?'已整理':'待整理'}}</span><strong>形成报告</strong><small>{{report?'回看报告':'整理当前已有结果'}}</small></button></li></ol>
  <p v-if="!report && count===4" class="progress-hint">四步结果已齐，可以整理报告；已有旧报告时，新查询结果需要重新纳入报告。</p>
</section>
</template>
<style scoped>
.review-progress{padding:18px;margin:0 0 26px;border:1px solid #c5d5c9;border-radius:9px;background:#f7faf8;color:#294c39}.review-progress header{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center}.review-progress h2{font-size:15px;margin:0}.review-progress p{font-size:12px;color:#52685e;line-height:1.7;margin:6px 0}.review-progress header>span{font-size:12px;line-height:1.7;color:#795626}.review-progress ol{list-style:none;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));padding:0;gap:9px;margin:14px 0 0}.review-progress button{display:flex;flex-direction:column;gap:6px;min-height:100px;width:100%;text-align:left;padding:11px;border:1px solid #d3dfd6;border-radius:7px;background:#fff;color:#294c39;cursor:pointer;font:inherit}.review-progress button:hover{background:#edf5ef}.review-progress button:disabled{opacity:.5;cursor:default}.review-progress button:focus-visible{outline:3px solid #5a9279;outline-offset:3px}.review-progress button span,.review-progress small{font-size:11px;line-height:1.6;color:#52685e}.review-progress strong{font-size:13px;line-height:1.6}.review-progress button .complete{color:#21654f}.review-progress .progress-hint{margin:14px 0 0}@media(max-width:620px){.review-progress{padding:15px}.review-progress ol{grid-template-columns:repeat(2,minmax(0,1fr))}.review-progress button{min-height:94px}.review-progress li:last-child{grid-column:1/-1}.review-progress li:last-child button{min-height:44px;flex-direction:row;align-items:center;gap:12px;flex-wrap:wrap}.review-progress button span,.review-progress small{font-size:12px}}
</style>
