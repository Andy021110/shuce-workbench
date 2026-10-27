import test from 'node:test'
import assert from 'node:assert/strict'
import { executionOutcome, noModelReason, traceSummary } from '../src/executionTrace.ts'
test('按能力统计实际请求，重试不混作业务重跑，缓存不重复计 Token', () => {
 const trace={tool_calls:[{operation:'discovery'},{operation:'call'}],model_calls:[
  {capability:'chat',success:false,attempt:1},
  {capability:'chat',success:true,attempt:2,usage:{prompt_tokens:100,completion_tokens:20,prompt_cache_hit_tokens:50}},
  {capability:'embedding',success:true,attempt:1,usage:{total_tokens:500}},
  {capability:'rerank',success:true,attempt:1},
 ]}
 assert.deepEqual(traceSummary(trace),{chat:2,embedding:1,rerank:1,tools:1,retries:1,failed:1,input:100,output:20,reported:1,outputReported:1})
})
test('正在请求不算失败，模型接口成功仍可出现报告业务回退', () => {
 const trace={tool_calls:[],model_calls:[{capability:'chat',attempt:1},{capability:'chat',success:true,attempt:1}]}
 assert.equal(traceSummary(trace).failed,0)
 const result={status:'completed',execution_trace:trace,report:{generation_mode:'facts_only'}}
 assert.equal(executionOutcome(result).label,'报告解读校验未通过')
 assert.equal(executionOutcome(result).tone,'warning')
})
test('未知历史、标准 SQL 零请求、请求前失败和范围保护分别解释', () => {
 const empty={model_calls:[],tool_calls:[]}
 assert.match(noModelReason({}),/无法判断/)
 assert.match(noModelReason({execution_trace:empty,workflow_mode:'monthly_review'}),/标准 SQL/)
 assert.match(noModelReason({execution_trace:empty,status:'failed',workflow_mode:'single_database_agent'}),/发出模型请求前结束/)
 assert.equal(executionOutcome({status:'failed',execution_log:[{stage:'scope_inheritance',success:false}]}).label,'等待确认，查询尚未执行')
 assert.equal(executionOutcome({status:'failed'}).tone,'danger')
 assert.equal(executionOutcome({status:'completed'}).label,'结果已返回')
})
test('没有供应商用量不能显示零 Token，标准 SQL 的模型请求为零', () => {
 const unknown=traceSummary({tool_calls:[],model_calls:[{capability:'chat',success:true,attempt:1}]})
 assert.equal(unknown.input,null);assert.equal(unknown.output,null)
 const canonical=traceSummary({tool_calls:[],model_calls:[]})
 assert.equal(canonical.chat,0);assert.equal(canonical.tools,0);assert.equal(canonical.input,null)
})
