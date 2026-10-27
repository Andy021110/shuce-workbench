<script setup lang="ts">
import type { AnalysisScope } from '../types'
import { categoryLabel, statusLabel } from '../businessFormat'
defineProps<{scope:AnalysisScope}>()
</script>
<template>
  <section class="analysis-scope" aria-label="本次分析范围">
    <header><strong>本次分析范围</strong><span>{{ scope.inherited?'结构化筛选沿用上一条结果':'结果范围已保存' }}</span></header>
    <dl><div><dt>时间与单位</dt><dd>{{ scope.period_label }} · {{ scope.time_label }} · {{ scope.currency }}</dd></div>
      <div><dt>订单状态</dt><dd>{{ scope.statuses.length?scope.statuses.join('、'):scope.mode==='monthly_review'?'全部状态':'未设置继承限制，见 SQL 条件' }}</dd></div>
      <div><dt>地区</dt><dd>{{ scope.states.length?scope.states.join('、'):scope.mode==='monthly_review'?'全部地区':'未设置继承限制，见 SQL 条件' }}</dd></div>
      <div><dt>品类</dt><dd>{{ scope.categories.length?scope.categories.map(c=>categoryLabel(c)+'（'+c+'）').join('、'):scope.mode==='monthly_review'?'全部品类（含未分类）':'未设置继承限制，见 SQL 条件' }}</dd></div></dl>
    <dl v-if="scope.exclusions && (scope.exclusions.states.length || scope.exclusions.order_statuses.length || scope.exclusions.categories.length)">
      <div v-if="scope.exclusions.states.length"><dt>排除地区</dt><dd>{{ scope.exclusions.states.join('、') }}</dd></div>
      <div v-if="scope.exclusions.order_statuses.length"><dt>排除状态</dt><dd>{{ scope.exclusions.order_statuses.map(statusLabel).join('、') }}</dd></div>
      <div v-if="scope.exclusions.categories.length"><dt>排除品类订单</dt><dd>{{ scope.exclusions.categories.map(categoryLabel).join('、') }}</dd></div>
    </dl>
    <p>{{ scope.note }}</p>
    <details v-if="scope.predicates.length"><summary>查看已识别的 SQL 范围条件</summary><ul><li v-for="predicate in scope.predicates" :key="predicate"><code>{{ predicate }}</code></li></ul></details>
  </section>
</template>
<style scoped>
.analysis-scope{padding:16px 18px;margin:14px 0;border:1px solid #c5d5c9;border-radius:8px;background:#f7faf8;color:#294c39}.analysis-scope header{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:13px}.analysis-scope header span{color:#52685e;font-size:12px}.analysis-scope dl{font-size:13px;line-height:1.8;margin:12px 0}.analysis-scope dl>div{display:grid;grid-template-columns:82px minmax(0,1fr);gap:10px;margin:6px 0}.analysis-scope dt{color:#52685e}.analysis-scope dd{margin:0;overflow-wrap:anywhere}.analysis-scope p,.analysis-scope summary,.analysis-scope li{font-size:12px;line-height:1.8;overflow-wrap:anywhere}.analysis-scope p{color:#52685e}.analysis-scope summary{cursor:pointer;min-height:32px}.analysis-scope ul{padding-left:18px}.analysis-scope code{white-space:pre-wrap}.analysis-scope summary:focus-visible{outline:3px solid #5a9279;outline-offset:3px}
</style>
