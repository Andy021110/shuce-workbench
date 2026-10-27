<script setup lang="ts">
import { computed } from 'vue'
const props=defineProps<{busy:boolean;step:string;overview?:Record<string,string|number|null>[]}>()
const emit=defineEmits<{step:[step:string]}>()
const mismatch=computed(()=>{
  if(props.overview?.length!==2) return false
  const [a,b]=props.overview
  return Number(b?.orders)>Number(a?.orders) && Number(b?.item_sales)<Number(a?.item_sales)
})
</script>
<template>
  <section class="review-guide" aria-label="下一步排查引导">
    <h3>接下来查什么</h3>
    <p>{{ step==='delivery_states'?'已获得州级配送结果。结合超时增量、订单基数和超时率确定核查顺序，再生成复盘报告。':step==='sales_drivers'?'已完成量价分解。下一步按品类查看销售变化集中在哪里，再决定需要补充什么验证。':step==='sales_categories'?'已定位品类变化。可以继续排查配送，或生成报告并区分事实与待验证原因。':mismatch?'订单增加、商品金额下降：先看单均商品金额，再区分每单明细条数和平均明细价格的变化。':'销售变化可从订单量、每单明细条数和平均明细价格逐步拆解；配送变化可按地区排查。' }}</p>
    <div class="guide-options">
      <div><strong>先拆量价 <small v-if="step==='sales_drivers'">当前结果</small></strong><p>回答：每笔订单带来的商品金额为何不同？查看构成变化，不直接认定业务原因。</p><button :disabled="busy" @click="emit('step','sales_drivers')">拆解单均金额</button></div>
      <div><strong>再看品类 <small v-if="step==='sales_categories'">当前结果</small></strong><p>回答：哪些品类贡献了销售变化？包含未分类金额，可与月度总额核对。</p><button :disabled="busy" @click="emit('step','sales_categories')">按品类排查销售</button></div>
      <div><strong>排查配送 <small v-if="step==='delivery_states'">当前结果</small></strong><p>回答：超时增量集中在哪些州？同时看订单基数和超时率。</p><button :disabled="busy" @click="emit('step','delivery_states')">按州排查配送</button></div>
    </div>
  </section>
</template>
<style scoped>
.review-guide{flex-basis:100%;padding:18px;border:1px solid #c5d5c9;border-radius:9px;background:#f7faf8}.review-guide h3{font-size:15px;color:#294c39;margin:0}.review-guide>p{margin:9px 0 16px!important}.guide-options{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:14px}.guide-options>div{display:flex;flex-direction:column;gap:10px;padding:14px;border:1px solid #d6e0d8;border-radius:7px;background:#fff;min-width:0}.guide-options strong{font-size:14px;color:#294c39}.guide-options p{flex:1;margin:0!important}.guide-options button{align-self:flex-start;background:#fff!important;color:#21654f!important;min-height:44px;border:1px solid #afc5b7;border-radius:7px;padding:10px 15px;font-size:13px;cursor:pointer}.guide-options button:disabled{opacity:.5;cursor:default}.guide-options button:focus-visible{outline:3px solid #5a9279;outline-offset:3px}
.guide-options small{font-size:11px;font-weight:400;color:#52685e;margin-left:6px}
</style>
