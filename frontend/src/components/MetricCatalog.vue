<script setup lang="ts">
import { computed, ref, watch } from "vue"
import type { MetricContract, MetricSelection } from "../types"
const props=defineProps<{definitions: MetricContract[]; busy?: boolean}>()
const emit=defineEmits<{query:[selection: MetricSelection]}>()
const selected=ref("order_count"), start=ref("2017-01-01"), end=ref("2017-12-31"), grain=ref("month")
const metric=computed(()=>props.definitions.find(d=>d.id===selected.value) || props.definitions[0])
watch(metric, value=>{if(value && !value.grains.includes(grain.value)) grain.value=value.grains[0] || "total"})
function submit(){if(metric.value) emit('query',{metric_id:metric.value.id,start_date:start.value || null,end_date:end.value || null,grain:grain.value})}
</script>
<template>
<details v-if="definitions.length" class="metric-catalog">
  <summary>统一指标口径 <span>v{{ definitions[0]?.version }} · {{ definitions.length }} 个指标</span></summary>
  <p>首页与下方标准查询共用这些定义。自然语言查询会参考同一套规则，修改范围或状态后的口径需另行核对。</p>
  <div class="metric-choices" aria-label="选择标准指标"><button v-for="item in definitions" :key="item.id" :aria-pressed="metric?.id===item.id" :disabled="busy" @click="selected=item.id">{{ item.label }}</button></div>
  <section v-if="metric" class="metric-definition"><h3>{{ metric.label }} <small>v{{ metric.version }}</small></h3><p>{{ metric.definition }}</p><dl><dt>时间字段</dt><dd>{{ metric.time_field }}</dd><dt>原始粒度</dt><dd>{{ metric.grain }}</dd><dt>订单状态</dt><dd>{{ metric.status_scope }}</dd><dt>金额单位</dt><dd>{{ metric.currency || '不适用' }}</dd></dl></section>
  <form v-if="metric" @submit.prevent="submit"><label>开始日期<input v-model="start" type="date" :max="end || undefined" :disabled="busy"></label><label>结束日期<input v-model="end" type="date" :min="start || undefined" :disabled="busy"></label><label>统计粒度<select v-model="grain" aria-label="统计粒度" :disabled="busy"><option v-for="value in metric.grains" :key="value" :value="value">{{ ({total:'期间汇总',month:'按月',day:'按日'} as Record<string,string>)[value] }}</option></select></label><button type="button" :disabled="busy" @click="start='';end=''">全部历史</button><button type="submit" :disabled="busy">确认口径并查询</button></form>
  <small>日期均按下单时间，结束日期包含当天。留空表示不设置该日期边界。</small>
</details>
</template>
<style scoped>
.metric-catalog{margin:0 0 26px;padding:18px 20px;border:1px solid #c9d9ce;border-radius:9px;background:#fff;color:#294c39}.metric-catalog>summary{font-size:15px;font-weight:650;cursor:pointer;min-height:28px}.metric-catalog>summary span{font-size:12px;color:#52685e;margin-left:10px;font-weight:400}.metric-catalog p{font-size:13px;line-height:1.8;margin:12px 0;color:#52685e}.metric-choices{display:flex;gap:8px;flex-wrap:wrap}.metric-choices button{border:1px solid #bdcdc3;background:#fff;border-radius:6px;padding:10px 13px;min-height:44px;color:#235c46;font-size:13px}.metric-choices button[aria-pressed=true]{background:#e4f0e9;border-color:#37815d}.metric-definition{margin:18px 0;padding:16px;background:#f3f7f4;border-radius:6px}.metric-definition h3{margin:0;font-size:15px}.metric-definition h3 small{font-weight:400;color:#52685e}.metric-definition dl{display:grid;grid-template-columns:80px minmax(0,1fr);gap:7px;font-size:12px;line-height:1.7;margin-bottom:0}.metric-definition dd{margin:0;overflow-wrap:anywhere}.metric-catalog form{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.metric-catalog label{display:block;font-size:12px;color:#52685e}.metric-catalog input,.metric-catalog select{display:block;min-height:42px;margin-top:7px;padding:8px;border:1px solid #bdcdc3;border-radius:6px;background:#fff;color:#294c39;max-width:100%;min-width:0}.metric-catalog form button{border:1px solid #bdcdc3;border-radius:6px;min-height:42px;background:#fff;padding:9px 14px;font-size:13px;color:#235c46}.metric-catalog form button[type=submit]{background:#21654f;color:#fff;border-color:#21654f}.metric-catalog>small{display:block;margin-top:12px;font-size:12px;color:#52685e;line-height:1.7}@media(max-width:720px){.metric-catalog{padding:15px}.metric-catalog label{flex:1;min-width:125px}.metric-catalog input,.metric-catalog select{width:100%;font-size:16px}.metric-catalog>summary span{display:block;margin:6px 0 0}}
</style>
