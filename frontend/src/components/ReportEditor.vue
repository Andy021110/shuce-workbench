<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { reportDraftKey, readReportDraft, writeReportDraft } from '../reportDrafts'
import { api } from '../api'
import type { ReportEdit } from '../types'
const props=defineProps<{taskId:string;originalTitle:string;edit?:ReportEdit|null;draftOwner?:string}>()
const emit=defineEmits<{saved:[edit:ReportEdit]}>()
const open=ref(false),busy=ref(false),error=ref(''),notice=ref('')
const revision=ref(0)
const draft=ref({title:'',conclusion:'',hypotheses:'',actions:''})
const latest=ref<ReportEdit|null>(null)
const cacheKey=computed(()=>reportDraftKey(props.draftOwner,props.taskId))
const cached=()=>{try{return readReportDraft(localStorage,cacheKey.value)}catch{return null}}
watch([draft,revision],()=>{
  if(!open.value)return
  try{if(writeReportDraft(localStorage,cacheKey.value,draft.value,revision.value))return}catch{}
  notice.value='草稿暂不能保存在本浏览器；请先保存人工内容再离开。'
},{deep:true,flush:'sync'})
onMounted(()=>{if(cached())begin()})
function begin(){
  const saved=cached()
  if(saved){
    draft.value={...saved.draft};revision.value=saved.revision;error.value='';latest.value=null;open.value=true
    notice.value='已恢复本浏览器未保存的报告草稿，尚未保存到报告。'
    if(saved.revision!==(props.edit?.revision||0))error.value='其他页面已修改此报告。请加载最新备注对照；当前草稿保留。'
    return
  }
  const edit=props.edit
  draft.value={title:edit?.title || props.originalTitle,conclusion:edit?.conclusion || '',hypotheses:edit?.hypotheses || '',actions:edit?.actions || ''}
  revision.value=edit?.revision || 0;error.value='';notice.value='';latest.value=null;open.value=true
}
async function save(){
  busy.value=true;error.value='';notice.value=''
  try{const edit=await api.editReport(props.taskId,draft.value,revision.value);emit('saved',edit);open.value=false;try{localStorage.removeItem(cacheKey.value)}catch{};notice.value='人工内容已保存，原始报告和数值依据保留。'}
  catch(e){error.value=String(e)}finally{busy.value=false}
}
async function loadLatest(){
  busy.value=true
  try{const result=await api.task(props.taskId);latest.value=result.report_edit || null;revision.value=latest.value?.revision || 0;if(latest.value)emit('saved',latest.value);error.value='';notice.value='已加载最新版本，当前草稿保留。请对照下方最新内容，确认后再保存。'}
  catch(e){error.value=String(e)}finally{busy.value=false}
}
</script>
<template>
  <section class="report-edit-controls" :aria-label="`报告人工编辑 ${taskId}`">
    <button v-if="!open" type="button" @click="begin">编辑报告</button>
    <p v-if="notice" role="status">{{ notice }}</p>
    <form v-if="open" @submit.prevent="save">
      <h3>编辑报告</h3><p>未保存草稿按账号、数据源和报告保存在本浏览器，切页或刷新后可继续。导出仅包含已保存内容。</p><p>可修改交付标题并补充人工判断。原始事实、模型内容和数值引用保留；人工内容不会自动通过事实校验。</p>
      <label :for="`report-title-${taskId}`">交付标题<input :id="`report-title-${taskId}`" v-model="draft.title" maxlength="120" :disabled="busy"></label>
      <label :for="`report-conclusion-${taskId}`">人工结论<textarea :id="`report-conclusion-${taskId}`" v-model="draft.conclusion" maxlength="4000" :disabled="busy" placeholder="写明你确认的业务判断，并核对原始数据。"></textarea></label>
      <label :for="`report-hypotheses-${taskId}`">待验证原因<textarea :id="`report-hypotheses-${taskId}`" v-model="draft.hypotheses" maxlength="4000" :disabled="busy" placeholder="写明假设和需要补充的验证，避免把猜测写成事实。"></textarea></label>
      <label :for="`report-actions-${taskId}`">下一步动作<textarea :id="`report-actions-${taskId}`" v-model="draft.actions" maxlength="4000" :disabled="busy" placeholder="记录准备做什么；当前不会指派或通知他人。"></textarea></label>
      <p v-if="error" role="alert" class="edit-error">{{ error }}</p>
      <button v-if="error.includes('其他页面')" type="button" :disabled="busy" @click="loadLatest">加载最新备注并保留草稿</button>
      <details v-if="latest" open><summary>其他页面保存的最新内容</summary><p>标题：{{ latest.title }}</p><pre>{{ latest.conclusion }}\n{{ latest.hypotheses }}\n{{ latest.actions }}</pre></details>
      <div class="edit-actions"><button type="submit" :disabled="busy">{{ busy?'正在保存…':'保存人工内容' }}</button><button type="button" :disabled="busy" @click="open=false;error='';notice='编辑已收起，未保存草稿保留在本浏览器。'">收起编辑（保留草稿）</button></div>
    </form>
  </section>
</template>
<style scoped>
.report-edit-controls{padding:0 20px 18px;color:#294c39}.report-edit-controls h3{font-size:16px;margin:12px 0}.report-edit-controls p{font-size:13px;line-height:1.8}.report-edit-controls form{border:1px solid #c5d5c9;border-radius:8px;padding:16px;background:#f7faf8}.report-edit-controls label{display:block;font-size:13px;margin:14px 0}.report-edit-controls input,.report-edit-controls textarea{box-sizing:border-box;display:block;width:100%;margin-top:7px;border:1px solid #aebfb4;border-radius:6px;min-height:44px;padding:10px;font:inherit;background:#fff;color:#294c39}.report-edit-controls textarea{min-height:100px;resize:vertical;line-height:1.7}.report-edit-controls button{min-height:44px;padding:9px 14px;border:1px solid #afc5b7;border-radius:6px;background:#fff;color:#21654f;cursor:pointer;font-size:13px}.edit-actions{display:flex;gap:10px;flex-wrap:wrap}.edit-actions button:first-child{background:#21654f;color:#fff}.report-edit-controls .edit-error{color:#9a3028}.report-edit-controls pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.report-edit-controls :is(button,input,textarea):focus-visible{outline:3px solid #5a9279;outline-offset:3px}.report-edit-controls button:disabled{opacity:.5;cursor:default}
</style>
