<script setup lang="ts">
import { ref, watch } from "vue"
import MetricCatalog from "./MetricCatalog.vue"
import MonthlyReview from "./MonthlyReview.vue"
import DataReadiness from "./DataReadiness.vue"
import type { MetricSelection, ReviewTargets, ReviewFilters } from "../types"
import { api } from "../api"
const props = defineProps<{database: string; tableCount: number; busy?: boolean; recent: {id: string; title: string; turns: unknown[]}[]}>()
const emit = defineEmits<{examples: []; open: [id: string]; metric: [selection: MetricSelection]; review:[month:string,targets:ReviewTargets,filters:ReviewFilters]}>()
const data = ref<any>(null)
const error = ref("")
const busy = ref(false)
let version = 0
async function load() {
  const id = ++version
  busy.value = true; error.value = ""; data.value = null
  try { const value = await api.dashboard(props.database); if (id === version) data.value = value }
  catch (e) { if (id === version) error.value = String(e) }
  finally { if (id === version) busy.value = false }
}
watch(() => props.database, load, {immediate: true})
const number = (v: number) => new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 0}).format(v)
const money = (v: number) => new Intl.NumberFormat("zh-CN", {style: "currency", currency: "BRL"}).format(v)
const percent = (a: number, b: number) => (a / b * 100).toFixed(2) + "%"
</script>
<template>
  <section class="workbench-home">
    <header class="overview-title"><div><h1>{{ database === 'olist_ecommerce' ? 'Olist 经营概览' : '数据源概览' }}</h1><p>{{ database === 'olist_ecommerce' ? '巴西电商历史订单 · 全部订单状态' : '按业务口径开始分析，查询结果会保存到分析记录。' }}</p></div><button :disabled="busy" @click="load">{{ busy ? '正在查询…' : '重新计算' }}</button></header>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
    <section v-if="database==='olist_ecommerce'" class="example-entry" aria-label="从业务场景开始"><div><span>第一次用，也能直接看懂</span><h2>先浏览一份完整的经营复盘</h2><p>真实历史数据、完整分析步骤、待核查事项与交付确认，串成一套可回看的示例。</p></div><button type="button" @click="emit('examples')">浏览三个业务场景 →</button></section>
    <DataReadiness :database="database" />
    <MonthlyReview v-if="database==='olist_ecommerce'" :busy="props.busy" @start="(month,targets,filters)=>emit('review',month,targets,filters)" />
    <div v-if="busy" class="overview-loading" role="status">正在读取数据源并计算指标…</div>
    <template v-else-if="data?.metrics?.orders">
      <div class="metric-grid">
        <div><small>订单数</small><strong>{{ number(data.metrics.orders) }}</strong><span>order_id 去重 · 全部状态</span></div>
        <div><small>商品金额 · BRL</small><strong>{{ money(data.metrics.item_sales) }}</strong><span>商品明细 price 之和 · 不含运费</span></div>
        <div><small>支付金额 · BRL</small><strong>{{ money(data.metrics.payments) }}</strong><span>支付记录之和 · 非净收入</span></div>
        <div><small>配送超时率</small><strong>{{ percent(data.metrics.late, data.metrics.delivered) }}</strong><span>{{ number(data.metrics.late) }} / {{ number(data.metrics.delivered) }} 笔可比较的已送达订单</span></div>
      </div>
      <section class="trend-panel">
        <header><div><h2>月度下单趋势</h2><p>{{ data.metrics.start_date }} 至 {{ data.metrics.end_date }}</p></div><span>去重订单数</span></header>
        <div class="month-bars" role="img" aria-label="Olist 月度订单数柱状图，详细数值见下方展开表格">
          <div v-for="row in data.monthly" :key="row.month" class="month-bar"><span :style="{height: Math.max(2, row.orders / Math.max(...data.monthly.map((r: any) => r.orders)) * 140) + 'px'}" :title="`${row.month}：${number(row.orders)} 笔`"></span><small>{{ row.month.slice(2) }}</small></div>
        </div>
        <details><summary>查看每月数值与统计口径</summary><p>以 order_purchase_timestamp 下单月份分组，COUNT(DISTINCT order_id)，包含全部订单状态。首尾月份为不完整月。</p><div class="monthly-values"><span v-for="row in data.monthly" :key="row.month">{{ row.month }} <b>{{ number(row.orders) }}</b></span></div></details>
      </section>
      <div class="source-ledger"><div><h2>数据与口径</h2><p>{{ data.note }}</p><p>复购买家 {{ number(data.metrics.repeat_buyers) }} / {{ number(data.metrics.buyers) }}（{{ percent(data.metrics.repeat_buyers, data.metrics.buyers) }}），以 customer_unique_id 识别，跨历史样本订单数大于 1。</p></div><dl><dt>可用数据</dt><dd>{{ tableCount }} 张表 · BRL</dd><dt>本次计算</dt><dd>{{ new Date(data.computed_at).toLocaleString('zh-CN') }}</dd><dt>来源与许可</dt><dd><a href="https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce" target="_blank" rel="noreferrer">Olist / Kaggle</a> · CC BY-NC-SA 4.0</dd></dl></div>
    </template>
    <div v-else-if="data" class="source-ledger"><div><h2>{{ tableCount }} 张可用表</h2><p>{{ data.note }}</p><p>{{ database === 'short_video_ops' ? '短视频运营为合成样例，不能用于证明真实经营表现。' : '本地 CSV 由管理员导入，字段说明由导入者提供。' }}</p></div></div>
    <MetricCatalog v-if="data?.metric_definitions?.length" :definitions="data.metric_definitions" :busy="props.busy" @query="emit('metric', $event)" />
    <section class="recent-analyses"><h2>最近分析</h2><p v-if="!recent.length">还没有分析记录。在下方输入要分析的指标、时间和维度。</p><button v-for="item in recent.slice(0, 4)" :key="item.id" @click="emit('open', item.id)"><span>{{ item.title }}</span><small>{{ item.turns.length }} 轮分析</small></button></section>
  </section>
</template>

<style scoped>.example-entry{display:flex;align-items:center;justify-content:space-between;gap:22px;background:#eaf3ed;border:1px solid #b9cfc0;border-radius:10px;padding:22px;margin:22px 0;color:#294c39}.example-entry span{font-size:12px;color:#21654f}.example-entry h2{font-size:19px;line-height:1.6;margin:7px 0}.example-entry p{font-size:13px;line-height:1.8;color:#52685e;margin:0}.example-entry button{flex-shrink:0;min-height:44px;border:1px solid #21654f;border-radius:7px;background:#21654f;color:#fff;padding:11px 16px;font-size:13px;cursor:pointer}.example-entry button:hover{background:#194f3e}.example-entry button:focus-visible{outline:3px solid #5a9279;outline-offset:3px}@media(max-width:620px){.example-entry{padding:18px;flex-direction:column;align-items:stretch}.example-entry button{width:100%}}</style>
