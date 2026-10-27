import test from 'node:test'
import assert from 'node:assert/strict'
import { deriveReviewProgress } from '../src/reviewProgress.ts'
const filters={states:['SP'],order_statuses:['delivered'],categories:['watches_gifts']}
const review={month:'2018-08',step:'overview',filters}
const query=(id,step='overview',extra={})=>({result:{task_id:id,status:'completed',route:'database_query',review_context:{...review,step},...extra}})
const report=(id,sources)=>({result:{task_id:id,status:'completed',route:'data_qa',report:{title:'报告'},analysis_sources:sources.map(task_id=>({task_id}))}})
test('只统计相同月份、相同范围和成功的真实步骤',()=>{
 const turns=[query('overview'),query('wrong-month','sales_drivers',{review_context:{...review,month:'2018-07',step:'sales_drivers'}}),query('wrong-scope','sales_categories',{review_context:{...review,filters:{...filters,states:['RJ']},step:'sales_categories'}}),query('failed','delivery_states',{status:'failed'})]
 const progress=deriveReviewProgress(turns,review)
 assert.deepEqual([...progress.results.keys()],['overview'])
 assert.equal(progress.report,undefined)
})
test('后续重算步骤会让未包含新结果的旧报告回到待整理',()=>{
 const turns=[query('o'),query('d','sales_drivers'),report('old',['o','d'])]
 assert.equal(deriveReviewProgress(turns,review).report.task_id,'old')
 turns.push(query('new-d','sales_drivers'))
 assert.equal(deriveReviewProgress(turns,review).report,undefined)
 turns.push(report('new',['o','new-d']))
 assert.equal(deriveReviewProgress(turns,review).report.task_id,'new')
})
test('不同月份或其他分析的报告不会误标为本次任务完成',()=>{
 const progress=deriveReviewProgress([query('o'),report('other',['other-o'])],review)
 assert.equal(progress.report,undefined)
})
test('范围排序不影响匹配，但缺少任何当前结果都不算报告已更新',()=>{
 const wider={...review,filters:{...filters,states:['SP','RJ']}}
 const turns=[query('o','overview',{review_context:{...wider,filters:{...wider.filters,states:['RJ','SP']}}}),query('s','sales_categories',{review_context:{...wider,step:'sales_categories'}}),report('missing-source',['o'])]
 const progress=deriveReviewProgress(turns,wider)
 assert.equal(progress.results.size,2)
 assert.equal(progress.report,undefined)
})
