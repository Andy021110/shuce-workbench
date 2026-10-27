<script setup lang="ts">
import { computed, ref } from "vue"
import type { AnalysisReport, ReportDataSource, ReportEdit } from "../types"
import AnalysisChart from "./AnalysisChart.vue"
import ReportEditor from "./ReportEditor.vue"
import MarkdownReport from "./MarkdownReport.vue"

const props = defineProps<{
  report: AnalysisReport
  sources: ReportDataSource[]
  taskId:string
  draftOwner?:string
  edit?:ReportEdit|null
}>()
const emit=defineEmits<{edited:[edit:ReportEdit]}>()
const title=computed(()=>props.edit?.title || props.report.title)
const manualMarkdown=computed(()=>{
  const e=props.edit;if(!e)return ''
  const parts=['## 人工补充','作者：'+e.editor_label+'；保存时间：'+e.updated_at+'。以下为人工内容，未做自动事实校验。']
  for(const [label,value] of [['人工结论',e.conclusion],['待验证原因',e.hypotheses],['下一步动作',e.actions]])if(value)parts.push('### '+label+'\n\n'+value.split('\n').map(line=>'> '+line).join('\n'))
  return parts.join('\n\n')
})
const reportElement = ref<HTMLElement | null>(null)

const displayMarkdown = computed(() => {
  const lines = props.report.markdown.replace(/\r\n/g, "\n").split("\n")
  const firstContent = lines.findIndex((line) => line.trim())
  if (firstContent >= 0) {
    const heading = lines[firstContent].trim().match(/^#\s+(.+)$/)
    if (heading?.[1].trim() === props.report.title.trim()) {
      lines.splice(firstContent, 1)
    }
  }
  return lines.join("\n").trim()
})

function exportMarkdown() {
  const evidence=(props.report.fact_references || []).map(f=>`- ${f.label}：${f.display}；来源：${f.source_title}；范围：${f.scope}`).join('\n')
  const blob = new Blob(['# '+title.value+'\n\n'+displayMarkdown.value + (manualMarkdown.value?'\n\n'+manualMarkdown.value:'') + (evidence ? '\n\n## 数值引用依据\n\n' + evidence : '')], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `${title.value.replace(/[\\/:*?\"<>|]/g, "-") || "数据分析报告"}.md`
  anchor.click()
  URL.revokeObjectURL(url)
}

function exportPdf() {
  if (!reportElement.value) return

  const printable = reportElement.value.cloneNode(true) as HTMLElement
  printable.classList.add("pdf-print-root")
  printable.querySelectorAll(".report-actions,.report-edit-controls").forEach((element) => element.remove())
  printable.querySelectorAll("details").forEach(element=>element.open=true)

  const previousTitle = document.title
  let cleaned = false
  const cleanup = () => {
    if (cleaned) return
    cleaned = true
    printable.remove()
    document.body.classList.remove("printing-report")
    document.title = previousTitle
    window.removeEventListener("afterprint", cleanup)
  }

  document.title = title.value.replace(/[\\/:*?"<>|]/g, "-") || "数据分析报告"
  document.body.appendChild(printable)
  document.body.classList.add("printing-report")
  window.addEventListener("afterprint", cleanup, { once: true })

  requestAnimationFrame(() => {
    try {
      window.print()
    } finally {
      window.setTimeout(cleanup, 100)
    }
  })
}
</script>

<template>
  <article ref="reportElement" class="report-card">
    <header class="report-header">
      <div><small>{{ report.generation_mode==='backend_facts' ? '后端事实报告 · 可人工补充' : report.generation_mode==='facts_only' ? '后端事实报告 · 模型解读尚未通过校验' : report.fact_references?.length ? '后端数值事实 · 模型经营解读' : '模型分析 · 需结合数据核对' }}</small><h2>{{ title }}</h2></div>
      <div class="report-actions">
        <button @click="exportMarkdown"><span>↓</span> 导出 .md</button>
        <button class="pdf-export" title="打开系统打印窗口并保存为PDF" @click="exportPdf"><span>▣</span> 导出 PDF</button>
      </div>
    </header>
    <div v-if="report.visualizations.length" class="report-charts">
      <AnalysisChart v-for="(spec, index) in report.visualizations" :key="`${spec.source_task_id}:${index}`" :spec="spec" :sources="sources" />
    </div>
    <div v-if="report.validation_feedback?.length" class="report-feedback" role="status"><strong>解读校验反馈</strong><p v-for="message in report.validation_feedback" :key="message">{{ message }}</p></div>
    <ReportEditor :draft-owner="draftOwner" :task-id="taskId" :original-title="report.title" :edit="edit" @saved="emit('edited',$event)" />
    <section v-if="edit" class="manual-notes" aria-label="人工补充内容"><h3>人工补充</h3><p>作者：{{ edit.editor_label }} · {{ new Date(edit.updated_at).toLocaleString('zh-CN') }} · 未做自动事实校验</p><template v-for="item in [{label:'人工结论',text:edit.conclusion},{label:'待验证原因',text:edit.hypotheses},{label:'下一步动作',text:edit.actions}]" :key="item.label"><div v-if="item.text"><h4>{{ item.label }}</h4><p class="manual-content">{{ item.text }}</p></div></template></section>
    <details class="original-report" :open="!edit"><summary>原始报告与查询事实 · 保留原文</summary><MarkdownReport :markdown="displayMarkdown" /></details>
    <details v-if="report.fact_references?.length" class="report-evidence">
      <summary>查看数值依据 · {{ report.fact_references.length }} 项引用</summary>
      <p>{{ report.generation_mode==='facts_only' || report.generation_mode==='backend_facts' ? '原始报告仅包含后端统计事实，不包含模型经营解读。' : '数值可回到原结果核对；原因判断与建议仍是模型解读。' }}</p>
      <ul><li v-for="fact in report.fact_references" :key="fact.id"><strong>{{ fact.label }}：{{ fact.display }}</strong><span>{{ fact.scope }} · </span><a :href="`#result-${fact.source_task_id}`">{{ fact.source_title }}</a></li></ul>
    </details>
  </article>
</template>
<style scoped>
.report-evidence{margin:18px;padding:16px;border:1px solid #cad8cf;border-radius:8px;color:#294c39;font-size:13px;line-height:1.8}.report-evidence summary{cursor:pointer;min-height:28px}.report-evidence p{color:#52685e}.report-evidence ul{padding-left:20px}.report-evidence li{margin:10px 0;overflow-wrap:anywhere}.report-evidence strong{display:block;font-weight:500}.report-evidence a{color:#21654f;text-underline-offset:3px}
.manual-notes{margin:0 20px 20px;padding:18px;border:1px solid #c5d5c9;border-radius:8px;background:#f7faf8;color:#294c39}.manual-notes h3{font-size:16px;margin:0 0 12px}.manual-notes h4{font-size:14px;margin:18px 0 8px}.manual-notes p{font-size:13px;line-height:1.8;overflow-wrap:anywhere}.manual-notes .manual-content{white-space:pre-wrap}.original-report>summary{padding:12px 20px;color:#21654f;font-size:13px;cursor:pointer;min-height:44px;box-sizing:border-box}
.report-feedback{margin:0 20px 18px;padding:14px 16px;border:1px solid #d9ccb0;border-radius:7px;background:#fbf8f0;color:#65532d;font-size:13px;line-height:1.8}.report-feedback p{margin:6px 0}
</style>
