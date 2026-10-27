export interface ReportDraft { title:string; conclusion:string; hypotheses:string; actions:string }
export interface StoredReportDraft { version:1; revision:number; draft:ReportDraft }
export function reportDraftKey(owner:string|undefined,taskId:string) {
  return owner && taskId ? `askdata-report-draft:${owner}:${taskId}` : ''
}
export function readReportDraft(storage:Pick<Storage,'getItem'>,key:string):StoredReportDraft|null {
  if(!key)return null
  try {
    const value=JSON.parse(storage.getItem(key)||'null')
    if(value?.version!==1 || !Number.isInteger(value.revision) || value.revision<0)return null
    const limits={title:120,conclusion:4000,hypotheses:4000,actions:4000}
    for(const [field,max] of Object.entries(limits))if(typeof value.draft?.[field]!=='string' || value.draft[field].length>max)return null
    return value
  } catch{return null}
}
export function writeReportDraft(storage:Pick<Storage,'setItem'>,key:string,draft:ReportDraft,revision:number) {
  if(!key)return false
  try{storage.setItem(key,JSON.stringify({version:1,revision,draft}));return true}catch{return false}
}
