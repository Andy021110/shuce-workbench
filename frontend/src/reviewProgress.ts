import type { QueryResult, ReviewFilters } from './types'

function scopeKey(filters?:ReviewFilters){
  return JSON.stringify(['states','order_statuses','categories'].map(field=>[...((filters as unknown as Record<string,string[]>|undefined)?.[field]||[])].sort()))
}

export function deriveReviewProgress(turns:{result:QueryResult}[],review:NonNullable<QueryResult['review_context']>){
  const results=new Map<string,QueryResult>()
  for(const {result} of turns){
    const context=result.review_context
    if(result.status==='completed' && context?.month===review.month && scopeKey(context.filters)===scopeKey(review.filters))results.set(context.step,result)
  }
  const required=[...results.values()].map(result=>result.task_id)
  const report=required.length?[...turns].reverse().map(turn=>turn.result).find(result=>result.status==='completed' && result.report && required.every(id=>result.analysis_sources?.some(source=>source.task_id===id))):undefined
  return {results,report}
}
