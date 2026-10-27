<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { api } from '../api'
import { categoryLabel } from '../businessFormat'
import type { ReviewTargets, ReviewFilters } from '../types'
defineProps<{busy?: boolean}>()
const emit=defineEmits<{start:[month:string,targets:ReviewTargets,filters:ReviewFilters]}>()
const month=ref('2018-08')
const options=ref<{states:string[];statuses:{value:string;label:string}[];categories:string[]}>({states:[],statuses:[],categories:[]})
const optionError=ref(''),optionsBusy=ref(false)
const selectedState=ref(''),selectedStatus=ref(''),selectedCategory=ref('')
async function loadOptions(){optionsBusy.value=true;optionError.value='';try{options.value=await api.reviewOptions()}catch(e){optionError.value=String(e)}finally{optionsBusy.value=false}}
onMounted(loadOptions)
const templates=ref<{id:string;name:string;filters:ReviewFilters;contract_version:string}[]>([])
const selectedTemplate=ref(''),templateName=ref(''),templateError=ref(''),templateNotice=ref(''),templateBusy=ref(false)
function currentFilters():ReviewFilters{return {states:selectedState.value?[selectedState.value]:[],order_statuses:selectedStatus.value?[selectedStatus.value]:[],categories:selectedCategory.value?[selectedCategory.value]:[]}}
async function loadTemplates(){try{templates.value=await api.reviewTemplates()}catch(e){templateError.value=String(e)}}
onMounted(loadTemplates)
function applyTemplate(){
  const t=templates.value.find(t=>t.id===selectedTemplate.value);if(!t)return
  if(t.contract_version!=='1.0'){templateError.value='该模板口径版本不兼容，请重新选择范围后保存。';return}
  if(Object.values(t.filters).some(values=>values.length>1)){templateError.value='此模板含多选范围，当前表单只支持单选，未应用。请重新选择范围后另存模板。';return}
  selectedState.value=t.filters.states[0]||'';selectedStatus.value=t.filters.order_statuses[0]||'';selectedCategory.value=t.filters.categories[0]||''
  goals.value={orders:'',item_sales:'',late_rate:''};templateNotice.value='已应用范围；请选择本次月份并重新填写目标。模板不包含历史结果和目标。';templateError.value=''
}
async function saveTemplate(){
  if(!templateName.value.trim())return
  templateBusy.value=true;templateError.value='';templateNotice.value=''
  try{const t=await api.saveReviewTemplate(templateName.value.trim(),currentFilters());await loadTemplates();selectedTemplate.value=t.id;templateName.value='';templateNotice.value='模板已保存到当前账号。复用时仍需确认月份和目标。'}catch(e){templateError.value=String(e)}finally{templateBusy.value=false}
}
async function deleteTemplate(){
  if(!selectedTemplate.value)return
  templateBusy.value=true;templateError.value=''
  try{await api.deleteReviewTemplate(selectedTemplate.value);selectedTemplate.value='';await loadTemplates();templateNotice.value='模板已删除，当前选择的范围保留。'}catch(e){templateError.value=String(e)}finally{templateBusy.value=false}
}
const goals=ref({orders:'',item_sales:'',late_rate:''})
watch(month,()=>{goals.value={orders:'',item_sales:'',late_rate:''}})
const goalErrors=computed(()=>({
  orders:goals.value.orders!=='' && (!/^\d+$/.test(String(goals.value.orders)) || Number(goals.value.orders)>1e9) ? '订单目标须为0至10亿的整数。' : '',
  item_sales:goals.value.item_sales!=='' && (!/^\d+(?:\.\d{1,2})?$/.test(String(goals.value.item_sales)) || Number(goals.value.item_sales)>1e12) ? '金额目标须为非负数，最多两位小数，上限1万亿 BRL。' : '',
  late_rate:goals.value.late_rate!=='' && (!/^\d+(?:\.\d{1,2})?$/.test(String(goals.value.late_rate)) || Number(goals.value.late_rate)>100) ? '超时率上限须在0至100之间，最多两位小数。' : ''
}))
const targetsValid=computed(()=>!Object.values(goalErrors.value).some(Boolean))
function start(){
  if(!valid.value || !targetsValid.value) return
  const targets:ReviewTargets={}
  for(const key of ['orders','item_sales','late_rate'] as const){
    if(goals.value[key]!=='') targets[key]=Number(goals.value[key])
  }
  emit('start',month.value,targets,currentFilters())
}
const valid=computed(()=>/^\d{4}-(0[1-9]|1[0-2])$/.test(month.value) && month.value>='2016-10' && month.value<='2018-10')
const previous=computed(()=>{
  if(!valid.value) return '上月'
  const [y,m]=month.value.split('-').map(Number)
  return `${m===1?y!-1:y}-${String(m===1?12:m!-1).padStart(2,'0')}`
})
</script>
<template>
  <section class="monthly-review" aria-labelledby="monthly-review-title">
    <div><h2 id="monthly-review-title">月度经营复盘</h2><p>先看是否达标，再比较上月，沿着量价、品类和地区继续排查。</p></div>
    <form @submit.prevent="start">
      <label for="review-month">复盘月份<input id="review-month" v-model="month" type="month" min="2016-10" max="2018-10" required :disabled="busy" :aria-invalid="!valid" aria-describedby="review-month-hint"></label>
      <span v-if="valid" class="comparison">对比 {{ previous }}</span>
      <button type="submit" :disabled="busy || !valid || !targetsValid">开始复盘</button>
      <details class="goal-settings"><summary>选择分析范围（可选）</summary>
        <p>筛选会用于本次总览和后续下钻。选中品类时，订单数统计包含该品类的订单，金额仅统计所选品类。</p>
        <p v-if="optionsBusy" role="status">正在读取可用筛选项…</p>
        <p v-if="optionError" role="alert">{{ optionError }} <button type="button" @click="loadOptions">重新加载筛选项</button></p>
        <div class="goal-fields"><label for="review-state">客户州<select id="review-state" v-model="selectedState" :disabled="busy || optionsBusy"><option value="">全部地区</option><option v-for="state in options.states" :key="state">{{ state }}</option></select></label>
        <label for="review-status">订单状态<select id="review-status" v-model="selectedStatus" :disabled="busy || optionsBusy"><option value="">全部状态</option><option v-for="status in options.statuses" :key="status.value" :value="status.value">{{ status.label }}</option></select></label>
        <label for="review-category">商品品类<select id="review-category" v-model="selectedCategory" :disabled="busy || optionsBusy"><option value="">全部品类</option><option v-for="category in options.categories" :key="category" :value="category">{{ categoryLabel(category) }} · {{ category }}</option></select></label></div>
        <p>目标只适用于当前选择的范围。配送超时率仍只比较已送达且日期齐全的订单。</p>
      </details>
      <details class="goal-settings"><summary>填写本月目标（可选）</summary>
        <p>留空则不判断达标。目标仅用于当前月份，随本次结果保存；更换月份会清空，不自动当作预算。</p>
        <div class="goal-fields">
          <div><label for="goal-orders">订单数目标（笔，不低于）<input id="goal-orders" v-model="goals.orders" type="number" min="0" max="1000000000" step="1" :disabled="busy" :aria-invalid="!!goalErrors.orders" aria-describedby="goal-orders-error"></label><span id="goal-orders-error" class="field-error" v-if="goalErrors.orders" role="alert">{{ goalErrors.orders }}</span></div>
          <div><label for="goal-sales">商品金额目标（BRL，不低于）<input id="goal-sales" v-model="goals.item_sales" type="number" min="0" max="1000000000000" step="0.01" :disabled="busy" :aria-invalid="!!goalErrors.item_sales" aria-describedby="goal-sales-error"></label><span id="goal-sales-error" class="field-error" v-if="goalErrors.item_sales" role="alert">{{ goalErrors.item_sales }}</span></div>
          <div><label for="goal-late">配送超时率上限（%，不高于）<input id="goal-late" v-model="goals.late_rate" type="number" min="0" max="100" step="0.01" :disabled="busy" :aria-invalid="!!goalErrors.late_rate" aria-describedby="goal-late-error"></label><span id="goal-late-error" class="field-error" v-if="goalErrors.late_rate" role="alert">{{ goalErrors.late_rate }}</span></div>
        </div>
      </details>
      <details class="goal-settings"><summary>复用我的范围模板</summary>
        <p>保存地区、状态、品类和标准口径版本。月份、目标及历史结果不存入模板。</p>
        <div class="template-controls"><label for="review-template">已存模板<select id="review-template" v-model="selectedTemplate" :disabled="busy || templateBusy" @change="applyTemplate"><option value="">选择模板</option><option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option></select></label><button type="button" :disabled="busy || templateBusy || !selectedTemplate" @click="deleteTemplate">删除所选模板</button></div>
        <div class="template-controls"><label for="template-name">模板名称<input id="template-name" v-model="templateName" maxlength="60" placeholder="例如：SP已送达手表礼品" :disabled="busy || templateBusy"></label><button type="button" :disabled="busy || templateBusy || !templateName.trim()" @click="saveTemplate">保存当前范围为模板</button></div>
        <p v-if="templateError" role="alert">{{ templateError }} <button type="button" @click="loadTemplates">重新加载模板</button></p><p v-if="templateNotice" role="status">{{ templateNotice }}</p>
      </details>
    </form>
    <p id="review-month-hint" :role="!valid?'alert':undefined">{{ !valid ? '请选择2016年10月至2018年10月；需要上月数据进行比较。' : '统一按下单时间 · 商品金额不含运费 · BRL。历史样本无自动更新，边缘月份可能不完整。' }}</p>
    <details><summary>任务目录 · 在这里完成什么工作</summary>
      <ol><li><strong>看变化与目标</strong>：选月份，可填写目标。</li><li><strong>定位异常</strong>：先拆解单均金额，再查品类；配送问题按州排查。</li><li><strong>形成复盘材料</strong>：选择“生成复盘报告”，补充人工结论、查看数值依据并导出。</li><li><strong>下个月复用</strong>：选择范围模板，确认月份和目标后重新计算；旧分析保留为当时的快照，可重命名并搜索。</li></ol>
      <p><strong>核对运行记录</strong>：每条结果的“Agent 调用”常显请求情况；展开“查看模型、工具与 Token 明细”核对模型、耗时、Token 和工具执行。标准复盘由后端 SQL 计算，模型请求为 0；自然语言问数与模型报告按实际请求记录。</p>
      <p>范围模板已可复用；数据定时更新和负责人跟进尚未提供。</p>
    </details>
  </section>
</template>
<style scoped>
.monthly-review{padding:22px;margin:22px 0;border:1px solid #bdcdc3;border-radius:10px;background:#fff;color:#294c39}.monthly-review h2{margin:0;font-size:18px}.monthly-review p{font-size:13px;line-height:1.8;color:#52685e;margin:9px 0}.monthly-review form{display:flex;flex-wrap:wrap;align-items:end;gap:14px;margin:18px 0}.monthly-review label{font-size:13px}.monthly-review input{display:block;margin-top:7px;min-height:44px;border:1px solid #aebfb4;border-radius:6px;padding:9px;background:#fff;color:#294c39}.monthly-review button{min-height:44px;padding:10px 18px;background:#21654f;border:1px solid #21654f;border-radius:6px;color:#fff}.comparison{padding-bottom:12px;font-size:13px;color:#52685e}.monthly-review summary{cursor:pointer;font-size:13px;min-height:36px;padding-top:8px}.monthly-review li{font-size:13px;line-height:1.9;margin:7px 0}.monthly-review ol{padding-left:22px}.monthly-review button:disabled{opacity:.5;cursor:default}@media(max-width:620px){.monthly-review{padding:16px}.monthly-review form{gap:10px}.monthly-review input{font-size:16px;max-width:100%}.comparison{padding-bottom:0;align-self:center}}
.goal-settings{flex-basis:100%;border-top:1px solid #dae3dc;padding-top:8px}.goal-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:16px;margin:14px 0}.goal-fields input{width:100%;box-sizing:border-box}.field-error{display:block;margin-top:7px;color:#9a3028;font-size:12px;line-height:1.6}.monthly-review input[aria-invalid=true]{border-color:#9a3028}.monthly-review :is(button,input,summary):focus-visible{outline:3px solid #5a9279;outline-offset:3px}
.monthly-review select{display:block;box-sizing:border-box;width:100%;min-height:44px;margin-top:7px;padding:9px;border:1px solid #aebfb4;border-radius:6px;background:#fff;color:#294c39;max-width:100%;font-size:13px}.monthly-review select:focus-visible{outline:3px solid #5a9279;outline-offset:3px}@media(max-width:620px){.monthly-review select{font-size:16px}}
</style>

<style scoped>.template-controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:12px 0}.template-controls label{flex:1;min-width:min(100%,220px)}.template-controls input{width:100%;box-sizing:border-box}</style>
