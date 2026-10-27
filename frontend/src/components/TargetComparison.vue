<script setup lang="ts">
import type { TargetComparison } from '../types'
import { businessCell } from '../businessFormat'
defineProps<{items:TargetComparison[]}>()
function format(value:string|null,metric:string,gap=false){
  if(value===null) return '—'
  if(metric==='late_rate' && gap) return `${Number(value)>0?'+':''}${(Number(value)*100).toFixed(2)} 个百分点`
  const display=businessCell(Number(value),metric,'BRL')
  return gap && Number(value)>0 ? '+'+display : display
}
</script>
<template>
  <section class="target-comparison" aria-label="本月目标对比">
    <h3>本月是否达标</h3>
    <p v-if="!items.length">本次未填写目标，暂不判断达标。可回经营概览填写目标后重新复盘。</p>
    <template v-else>
      <p>目标由用户为本次复盘填写，随快照保存。差额 = 实际 − 目标；配送超时率越低越好。</p>
      <div class="target-grid">
        <article v-for="item in items" :key="item.metric">
          <header><h4>{{ item.label }}</h4><strong :class="{'not-met':item.status==='未达标'}">{{ item.status }}</strong></header>
          <dl><div><dt>本月实际</dt><dd>{{ format(item.actual,item.metric) }}</dd></div>
          <div><dt>{{ item.direction==='at_most'?'上限':'目标' }}</dt><dd>{{ format(item.target,item.metric) }}</dd></div>
          <div><dt>差额</dt><dd>{{ format(item.gap,item.metric,true) }}</dd></div>
          <div v-if="item.achievement_rate!==null"><dt>完成率</dt><dd>{{ businessCell(Number(item.achievement_rate),'rate') }}</dd></div></dl>
          <p v-if="item.actual===null">无可比较数据，不能据此判断是否达标。</p>
          <p v-else-if="item.target==='0' && item.direction==='at_least'">目标为零，不计算完成率。</p>
        </article>
      </div>
    </template>
  </section>
</template>
<style scoped>
.target-comparison{border:1px solid #c5d5c9;border-radius:9px;padding:18px;margin:20px 0;background:#f7faf8;color:#294c39}.target-comparison h3{font-size:15px;margin:0}.target-comparison p{font-size:13px;line-height:1.8;color:#52685e;margin:9px 0}.target-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin-top:14px}.target-grid article{border:1px solid #d6e0d8;background:#fff;border-radius:7px;padding:15px;min-width:0}.target-grid header{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.target-grid h4{font-size:14px;margin:0;flex:1}.target-grid strong{font-size:12px;color:#21654f}.target-grid strong.not-met{color:#9a3028}.target-grid dl{margin:15px 0 0}.target-grid dl>div{display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;margin:10px 0;font-size:13px;line-height:1.6}.target-grid dt{color:#52685e}.target-grid dd{margin:0;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
</style>
