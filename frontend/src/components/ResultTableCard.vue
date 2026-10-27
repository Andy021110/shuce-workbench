<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { api } from "../api"
import { product } from '../brand'
import { businessLabel, businessCell, businessDefinition } from '../businessFormat'
const props = withDefaults(defineProps<{title: string; columns: string[]; rows: Record<string,unknown>[]; taskId?: string; totalRows?: number | null; snapshotId?: string | null; currency?: string; initialPageSize?: number; updatedAt?: string; loading?: boolean; error?: string}>(), {currency: "CNY",initialPageSize:20})
const selectedColumns=ref<string[]>([...props.columns])
const displayColumns=computed(()=>props.columns.filter(c=>selectedColumns.value.includes(c)))
const cardView=ref(false)
watch(()=>props.columns,columns=>selectedColumns.value=[...columns])
function toggleColumn(col:string){if(selectedColumns.value.includes(col)){if(selectedColumns.value.length>1)selectedColumns.value=selectedColumns.value.filter(c=>c!==col)}else selectedColumns.value=[...selectedColumns.value,col]}
const page = ref(1), size = ref(props.initialPageSize), filter = ref(""), appliedFilter = ref(""), sort = ref(""), direction = ref("asc")
const remote = ref<any>(null), busy = ref(false), failure = ref(""), exporting = ref(false)
let version = 0
const total = computed(() => remote.value?.total_rows ?? props.rows.length)
const pages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))
const visible = computed<Record<string,unknown>[]>(() => remote.value?.rows ?? props.rows.slice((page.value-1)*size.value,page.value*size.value))
const legacy = computed(() => !props.snapshotId)
function params() {return new URLSearchParams({page: String(page.value),size: String(size.value),sort:sort.value,direction:direction.value,filter_text:appliedFilter.value})}
async function load() {
  if (!props.taskId) return
  const current = ++version
  busy.value=true; failure.value=""
  try {const data = await api.rows(props.taskId,params());if(current===version) remote.value=data}
  catch(e) {if(current===version) failure.value=String(e)}
  finally {if(current===version) busy.value=false}
}
watch([page,size,appliedFilter,sort,direction,() => props.taskId], load, {immediate:true})
watch(size, () => page.value=1)
function apply() {page.value=1; appliedFilter.value=filter.value; load()}
function sortBy(column: string) {page.value=1;if(sort.value===column) direction.value=direction.value==='asc'?'desc':'asc';else {sort.value=column;direction.value='asc'}}
function cell(value: unknown, col: string) {
  return businessCell(value,col,props.currency)
}
function download(blob: Blob, suffix: string) {const url=URL.createObjectURL(blob); const a=document.createElement('a');a.href=url;a.download=`${product.name}-${props.taskId || 'result'}-${suffix}.csv`;a.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function exportAll() {
  if(!props.taskId || exporting.value) return
  exporting.value=true;failure.value=''
  try {download(await api.exportRows(props.taskId,params()),'filtered')}
  catch(e) {failure.value=String(e)} finally {exporting.value=false}
}
function exportPage() {
  const escape=(v:unknown)=> {let text=String(v??'');if(typeof v==='string' && /^[\s]*[=+@-]/.test(text)) text="'"+text;return '"'+text.replaceAll('"','""')+'"'}
  const csv=[props.columns,...visible.value.map(r=>props.columns.map(c=>r[c]))].map(r=>r.map(escape).join(',')).join('\r\n')
  download(new Blob(['\ufeff',csv],{type:'text/csv;charset=utf-8'}),'page-'+page.value)
}
</script>
<template>
<section class="data-card" aria-label="表格查询结果">
  <header class="data-card-header"><div class="data-card-heading"><div><strong>{{ title }}</strong><small>{{ snapshotId ? `查询结果 ${(totalRows ?? rows.length).toLocaleString('zh-CN')} 行 · 固定查询快照` : `已保存 ${rows.length} 行预览 · 原始总数未知` }}</small></div></div><div class="data-card-actions"><slot name="actions"></slot><button :disabled="busy || !visible.length" @click="exportPage">导出本页</button><button :disabled="busy || exporting || !total" @click="exportAll">{{ exporting ? '正在导出…' : legacy ? '导出已保存预览' : '导出筛选结果' }}</button></div></header>
  <slot name="details"></slot>
  <details v-if="columns.some(c=>businessDefinition(c))" class="export-details"><summary>指标含义与原字段</summary><dl class="metric-definitions"><template v-for="col in columns.filter(c=>businessDefinition(c))" :key="col"><dt>{{businessLabel(col)}} <code>{{col}}</code></dt><dd>{{businessDefinition(col)}}</dd></template></dl></details>
  <form class="table-filter" @submit.prevent="apply"><label>筛选全部列 <input v-model="filter" aria-label="筛选查询结果" placeholder="输入包含的文本"></label><button type="submit" :disabled="busy">应用筛选</button><span>点击列名排序 · {{ total }} 行匹配{{ legacy ? '（仅限已保存预览）' : '' }}</span></form>
  <div class="table-display"><button type="button" :aria-pressed="cardView" @click="cardView=!cardView">{{cardView?'切换为表格':'切换为逐行卡片'}}</button><details><summary>选择显示列（{{displayColumns.length}} / {{columns.length}}）</summary><div class="column-options"><label v-for="col in columns" :key="col"><input type="checkbox" :checked="selectedColumns.includes(col)" :disabled="selectedColumns.length===1 && selectedColumns.includes(col)" @change="toggleColumn(col)">{{businessLabel(col)}}</label></div><button type="button" @click="selectedColumns=[...columns]">显示全部列</button></details><span>仅改变阅读方式，导出仍包含全部列。</span></div>
  <details class="export-details"><summary>浏览和导出范围说明</summary><p class="export-scope">{{ snapshotId ? '初始预览最多 200 行；这里可浏览完整 SQL 查询结果。导出为 UTF-8 CSV（Excel 可打开）；筛选结果包含全部匹配行，保持当前排序；若 SQL 包含 LIMIT，范围以该 SQL 为准。' : '旧记录仅保存了预览，无法判断是否完整。重新运行查询可生成完整快照。' }}</p></details>
  <p v-if="failure" role="alert" class="export-scope">{{ failure }} <button @click="load">重试加载</button></p>
  <p v-if="busy" role="status" class="export-scope">正在读取结果…</p>
  <div v-if="cardView" class="row-cards" :aria-busy="busy"><article v-for="(row,index) in visible" :key="index"><h3>第 {{(page-1)*size+index+1}} 行</h3><dl><template v-for="col in displayColumns" :key="col"><dt>{{businessLabel(col)}}</dt><dd :title="String(row[col]??'')">{{cell(row[col],col)}}<small v-if="/category|品类/.test(col) && row[col]">{{row[col]}}</small></dd></template></dl></article></div>
  <div v-else class="data-table-wrap" :aria-busy="busy" tabindex="0" aria-label="结果表，可左右滚动。首列固定。"><table class="data-table"><thead><tr><th v-for="col in displayColumns" :key="col" :aria-sort="sort===col ? direction==='asc'?'ascending':'descending' : 'none'"><button :title="col" @click="sortBy(col)">{{ businessLabel(col) }} {{ sort===col ? direction==='asc'?'↑':'↓':'' }}</button></th></tr></thead><tbody><tr v-for="(row,index) in visible" :key="index"><td v-for="col in displayColumns" :key="col" :title="String(row[col]??'')">{{ cell(row[col],col) }}</td></tr></tbody></table></div>
  <p v-if="!busy && !total" class="export-scope">{{(totalRows ?? rows.length)===0 ? 'SQL返回0行，请核对查询范围和SQL条件。' : '当前文本筛选没有匹配结果，可清除表格筛选。'}}</p>
  <footer class="data-card-footer"><label>每页<select v-model.number="size" aria-label="每页显示数量"><option :value="20">20</option><option :value="50">50</option><option :value="200">200</option></select>行</label><span>第 {{ page }} / {{ pages }} 页 · {{ total }} 行</span><div class="page-actions"><button :disabled="busy || page<=1" aria-label="上一页" @click="page--">‹</button><button :disabled="busy || page>=pages" aria-label="下一页" @click="page++">›</button></div></footer>
</section>
</template>
<style scoped>
.data-card { overflow: hidden; border: 1px solid #dfe7e2; border-radius: 15px; background: #fff; box-shadow: 0 3px 14px rgba(34, 68, 49, .035); }
.data-card-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 64px; padding: 13px 16px; border-bottom: 1px solid #e9eeea; background: linear-gradient(135deg, #fbfdfb, #f4f9f6); }
.data-card-heading, .data-card-actions { display: flex; align-items: center; gap: 10px; }.data-card-heading { min-width: 0; }
.sheet-icon { display: grid; place-items: center; width: 34px; height: 34px; flex: none; border-radius: 10px; color: #24714f; background: #e3f1e8; font-size: 14px; }
.data-card-heading strong, .data-card-heading small { display: block; }.data-card-heading strong { overflow: hidden; color: #28382f; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }.data-card-heading small { margin-top: 4px; color: #8a968f; font-size: 11px; }
.data-card-actions :deep(button), .export-button { min-height: 30px; padding: 0 10px; border: 1px solid #dce4df; border-radius: 8px; color: #5f6d65; background: #fff; font-size: 11px; }
.export-button { display: flex; align-items: center; gap: 5px; border-color: #246b4e; color: #fff; background: #246b4e; cursor: pointer; font-weight: 600; text-decoration: none; }.export-button span { font-size: 14px; }.export-button.disabled { cursor: default; opacity: .45; }
.data-table-wrap { max-height: 430px; overflow: auto; }
.data-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; }
.data-table th { position: sticky; top: 0; z-index: 1; padding: 11px 14px; border-bottom: 1px solid #dfe6e1; color: #6e7b74; background: #f6f8f6; text-align: left; font-size: 11px; font-weight: 700; white-space: nowrap; }
.data-table td { max-width: 240px; padding: 11px 14px; overflow: hidden; border-bottom: 1px solid #edf0ee; color: #3f4b44; text-overflow: ellipsis; white-space: nowrap; }
.data-table tbody tr:nth-child(even) { background: #fbfcfb; }.data-table tbody tr:hover { background: #edf7f1; }
.data-card-footer { display: flex; align-items: center; min-height: 50px; gap: 16px; padding: 9px 14px; color: #7c8881; background: #fbfcfb; font-size: 11px; }
.data-card-footer label { display: flex; align-items: center; gap: 5px; }.data-card-footer select { padding: 4px 16px 4px 6px; border: 1px solid #dce3de; border-radius: 6px; color: #4e5e55; background: #fff; font-size: 11px; }
.data-card-footer > span { margin-left: auto; }.page-actions { display: flex; gap: 5px; }.page-actions button { display: grid; place-items: center; width: 28px; height: 28px; border: 1px solid #dbe3de; border-radius: 7px; color: #486057; background: #fff; font-size: 15px; }.page-actions button:disabled { opacity: .35; }
.table-state { display: flex; min-height: 180px; flex-direction: column; align-items: center; justify-content: center; gap: 7px; color: #87928c; }.table-state > span { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 9px; background: #edf3ef; }.table-state strong { font-size: 12px; }.table-state small { font-size: 11px; }.table-state.error { color: #a45143; }
.state-spinner { border: 2px solid #dbe9e0; border-top-color: #277454; border-radius: 50% !important; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 620px) { .data-card-header { align-items: flex-start; flex-direction: column; }.data-card-actions { width: 100%; justify-content: flex-end; }.data-card-footer { gap: 8px; }.data-table td, .data-table th { padding: 10px; } }
</style>

<style scoped>
.table-filter {display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:12px 16px;font-size:12px}.table-filter input{border:1px solid #aebcb4;padding:8px;border-radius:6px;max-width:180px}.table-filter button,.data-table th button{border:0;background:transparent;color:#205e47}.table-filter button{min-height:40px}.table-filter span,.export-scope{color:#52655b}.export-scope{font-size:12px;line-height:1.7;padding:0 16px}.data-table th button{padding:0;min-height:28px;white-space:nowrap;font-weight:600}.data-card-heading small{color:#576b5f;font-size:12px}.data-card-actions button{font-size:12px;min-height:40px}.page-actions button{width:40px;height:40px}.data-card-footer{font-size:12px;color:#52655b}
</style>

<style scoped>
.data-table th:first-child{left:0;z-index:3;box-shadow:1px 0 #dfe6e1}.data-table td:first-child{position:sticky;left:0;z-index:1;background:#fff;box-shadow:1px 0 #dfe6e1;max-width:180px}.data-table tr:nth-child(even) td:first-child{background:#fbfcfb}.data-table-wrap:focus-visible{outline:3px solid #5a9279;outline-offset:-3px}.table-display{display:flex;flex-wrap:wrap;align-items:start;gap:12px;padding:0 16px 8px;font-size:12px;color:#52655b}.table-display button,.table-display summary{min-height:44px;align-content:center;cursor:pointer}.table-display button{border:1px solid #b4c9bc;border-radius:6px;background:#fff;color:#21654f;padding:8px 12px}.table-display span{line-height:1.7;padding-top:12px}.column-options{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:4px;padding:8px 0}.column-options label{display:flex;align-items:center;min-height:44px;gap:6px}.column-options input{width:18px;height:18px}.row-cards{padding:0 16px;max-height:600px;overflow:auto}.row-cards article{border:1px solid #dfe7e2;border-radius:7px;margin-bottom:12px;padding:14px}.row-cards h3{font-size:13px;margin:0 0 12px;color:#52655b}.row-cards dl{margin:0;display:grid;grid-template-columns:minmax(100px,1fr) minmax(0,1.2fr);gap:12px;font-size:13px;line-height:1.6}.row-cards dt{color:#52655b}.row-cards dd{margin:0;text-align:right;overflow-wrap:anywhere;color:#294c39}.row-cards small{display:block;color:#52655b}.table-display :is(button,summary,input):focus-visible{outline:3px solid #5a9279;outline-offset:3px}@media(max-width:620px){.data-table td:first-child{max-width:120px}.row-cards dl{gap:10px}}
</style>
<style scoped>.export-details{margin:0 16px 12px;color:#52655b;font-size:12px}.export-details summary{min-height:44px;align-content:center;cursor:pointer}.export-details p{padding:0;margin:4px 0;line-height:1.7}.data-card-actions{flex-wrap:wrap}.data-card-actions button{min-height:44px;white-space:nowrap}.data-table th{z-index:2}.data-table th:first-child{z-index:3}.export-details summary:focus-visible{outline:3px solid #5a9279;outline-offset:3px}</style>
<style scoped>@media(max-width:620px){.table-filter input{font-size:16px}}</style>
<style scoped>.metric-definitions{margin:8px 0;line-height:1.7}.metric-definitions dt{font-weight:600;color:#294c39}.metric-definitions code{display:block;font-weight:400;overflow-wrap:anywhere;color:#52655b}.metric-definitions dd{margin:4px 0 16px;color:#52655b}</style>
