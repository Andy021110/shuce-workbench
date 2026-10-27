<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../api'
const props=defineProps<{database:string}>()
const data=ref<any>(null),busy=ref(false),error=ref('')
let version=0
async function load(){const v=++version;busy.value=true;error.value='';try{const value=await api.dataReadiness(props.database);if(v===version)data.value=value}catch(e){if(v===version)error.value=String(e)}finally{if(v===version)busy.value=false}}
watch(()=>props.database,()=>{data.value=null;version++})
</script>
<template>
<section class="readiness" aria-label="数据可用性检查">
  <header><div><h2>复盘前，先检查数据</h2><p>核对时间覆盖、空值和标识重复，确认结果能否用于这次分析。</p></div><button :disabled="busy" @click="load">{{busy?'正在检查…':data?'重新检查数据':'检查数据可用性'}}</button></header>
  <p v-if="error" role="alert">{{error}}</p><p v-if="busy" role="status">正在检查当前账号可见的本地数据表…</p>
  <template v-if="data">
    <div v-if="data.coverage" class="coverage"><strong>下单日期 {{data.coverage.start}} 至 {{data.coverage.end}}</strong><p>{{data.coverage.boundary_months.join('、')}} 是样本边缘月份，可能不完整。{{data.coverage.note}}</p><p>已送达订单中，有 {{data.coverage.delivered_missing_comparison_dates}} 笔缺少超时率所需日期，计算超时率时排除。</p></div>
    <details v-if="data.coverage"><summary>查看各月记录覆盖</summary><p>无订单日可能是业务零值，也可能是未采集，需结合业务核对；不自动判定完整月。</p><ul><li v-for="m in data.coverage.months" :key="m.month">{{m.month}}：{{m.orders.toLocaleString('zh-CN')}} 笔订单，有下单记录 {{m.active_days}} / {{m.calendar_days}} 天</li></ul></details>
    <p role="status">{{data.tables.length}} 张可见表已检查；{{data.warning_tables}} 张有待核对项。检查不会修改数据。</p>
    <details v-for="t in data.tables" :key="t.name"><summary>{{t.label}} · {{t.rows.toLocaleString('zh-CN')}} 行 · {{t.missing.length || t.duplicate_identifier_rows ? '有待核对项' : '已检查'}}</summary>
      <p>{{t.identifier_fields.length ? `检查标识：${t.identifier_fields.join(' + ')}；标识重复的多余行 ${t.duplicate_identifier_rows}，标识缺失 ${t.missing_identifier_rows}。` : '此表未配置唯一标识，未判断重复行。'}}</p><p v-if="t.identifier_note">{{t.identifier_note}}</p>
      <ul v-if="t.missing.length"><li v-for="m in t.missing" :key="m.field">{{m.label}}（{{m.field}}）：{{m.count.toLocaleString('zh-CN')}} 行为空</li></ul><p v-else>未发现字段空值。</p>
    </details>
    <p class="checked-at">检查时间：{{new Date(data.checked_at).toLocaleString('zh-CN')}} · 导入时间：{{data.imported_at?new Date(data.imported_at).toLocaleString('zh-CN'):'源文件未记录'}}。{{data.note}}</p>
  </template>
</section>
</template>
<style scoped>
.readiness{margin:22px 0;padding:20px;border:1px solid #cbd9d0;border-radius:10px;background:#fff;color:#294c39}.readiness header{display:flex;align-items:start;justify-content:space-between;gap:16px}.readiness h2{margin:0;font-size:17px}.readiness p,.readiness li{font-size:13px;line-height:1.8;color:#52685e}.readiness button{min-height:44px;padding:9px 14px;border:1px solid #21654f;border-radius:6px;background:#fff;color:#21654f;cursor:pointer;flex-shrink:0}.readiness button:disabled{opacity:.6;cursor:default}.readiness details{border-top:1px solid #e0e8e2;padding:8px 0}.readiness summary{cursor:pointer;min-height:44px;display:list-item;align-content:center;line-height:1.7;font-size:13px}.readiness ul{padding-left:20px;overflow-wrap:anywhere}.coverage{padding:14px;background:#f2f7f3;border-radius:6px;margin-top:16px}.coverage strong{font-size:14px}.checked-at{margin-bottom:0}.readiness :is(button,summary):focus-visible{outline:3px solid #5a9279;outline-offset:3px}@media(max-width:620px){.readiness{padding:16px}.readiness header{flex-direction:column}.readiness button{width:100%}}
</style>
