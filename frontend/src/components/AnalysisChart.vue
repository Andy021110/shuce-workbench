<script setup lang="ts">
import { computed } from "vue"
import { businessCell, businessLabel } from "../businessFormat"
import type { ReportDataSource, VisualizationSpec } from "../types"

const props = defineProps<{
  spec: VisualizationSpec
  sources: ReportDataSource[]
}>()

const colors = ["#277456", "#62a07f", "#98bca8", "#d2aa60", "#6983a8", "#a77b8c", "#7e9b5c", "#c27c5a"]
const source = computed(() => props.sources.find((item) => item.taskId === props.spec.source_task_id))
const points = computed(() => (source.value?.rows ?? [])
  .filter(row => row[props.spec.value_field] !== null && row[props.spec.value_field] !== undefined && row[props.spec.value_field] !== "")
  .map((row) => ({
    label: businessCell(row[props.spec.category_field],props.spec.category_field),
    value: Number(row[props.spec.value_field]),
  }))
  .filter((item) => item.label && Number.isFinite(item.value))
  .slice(0, props.spec.max_items))
const maximum = computed(() => Math.max(...points.value.map((item) => Math.abs(item.value)), 1))
const total = computed(() => points.value.reduce((sum, item) => sum + Math.max(item.value, 0), 0))
const pieBackground = computed(() => {
  if (!total.value) return "#e8ece9"
  let start = 0
  const stops = points.value.map((item, index) => {
    const end = start + Math.max(item.value, 0) / total.value * 100
    const stop = `${colors[index % colors.length]} ${start}% ${end}%`
    start = end
    return stop
  })
  return `conic-gradient(${stops.join(", ")})`
})

function formatNumber(value: number) {
  return businessCell(value,props.spec.value_field,source.value?.currency || '')
}
</script>

<template>
  <section class="analysis-chart">
    <header><div><small>{{ spec.type === "bar" ? "柱状图" : "饼图" }}</small><strong>{{ businessLabel(spec.value_field) }} · 按{{ businessLabel(spec.category_field) }}</strong></div><span>{{ source?.title || '查询结果' }}</span></header>
    <p v-if="source?.coverage==='full_result_summary'" class="chart-range">基于完整 SQL 结果 {{ source.underlyingRows }} 行的期间汇总{{ source.excludedRows ? `（其中 ${source.excludedRows} 行缺失日期，未纳入期间汇总）` : '' }}；展示 {{ points.length }} / {{ source.rows.length }} 期{{ points.length < source.rows.length ? '，其余期间未展示' : '' }}。缺失数值不按零显示。</p>
    <p v-else class="chart-range">{{ source?.totalRows != null ? `查询共 ${source.totalRows} 行；` : '' }}图表按查询顺序展示当前预览前 {{ spec.max_items }} 项{{ source?.previewOnly ? '，不代表完整结果' : '' }}。</p>
    <div v-if="!source || !points.length" class="chart-empty">找不到该图表引用的数据或数值字段。</div>
    <div v-else-if="spec.type === 'bar'" class="bar-chart">
      <div v-for="point in points" :key="point.label" class="bar-row">
        <span :title="point.label">{{ point.label }}</span>
        <div><i :style="{ width: `${Math.abs(point.value) / maximum * 100}%` }"></i></div>
        <strong>{{ formatNumber(point.value) }}</strong>
      </div>
    </div>
    <div v-else class="pie-chart">
      <div class="pie-disc" :style="{ background: pieBackground }"><span>{{ points.length }}<small>项</small></span></div>
      <div class="pie-legend">
        <div v-for="(point, index) in points" :key="point.label">
          <i :style="{ background: colors[index % colors.length] }"></i>
          <span>{{ point.label }}</span>
          <strong>{{ total ? (Math.max(point.value, 0) / total * 100).toFixed(1) : "0.0" }}%</strong>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>.chart-range{font-size:12px;color:#52685e;line-height:1.7;padding:0 18px}</style>
