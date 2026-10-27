import type { AnalysisReport } from './types'

export function reportSections(report: Pick<AnalysisReport,'markdown'|'evidence_markdown'>) {
  // Older archived reports appended the same fact appendix to the narrative.
  const marker=report.markdown.search(/^## 查询事实\s*$/m)
  return {summary:marker>=0 ? report.markdown.slice(0,marker).trim() : report.markdown.trim(),
    evidence:report.evidence_markdown || (marker>=0 ? report.markdown.slice(marker).trim() : '')}
}
export function reportMarkdown(report: AnalysisReport,title:string,manual:string,summaryOnly=false) {
  const parts=reportSections(report)
  const summary=parts.summary || '已整理查询事实，详见完整报告的依据附录。'
  const refs=(report.fact_references || []).map(f=>`- ${f.label}：${f.display}；来源：${f.source_title}；范围：${f.scope}`).join('\n')
  return '# '+title+'\n\n'+summary+(manual?'\n\n'+manual:'')+
    (summaryOnly?'':(parts.evidence?'\n\n'+parts.evidence:'')+(refs?'\n\n## 数值引用依据\n\n'+refs:''))
}
