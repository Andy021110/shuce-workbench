import test from 'node:test'
import assert from 'node:assert/strict'
import {draftKey,recoverSubmittedDraft} from '../src/drafts.ts'
import {businessLabel,businessDefinition,statusLabel,businessCell} from '../src/businessFormat.ts'

test('预设报告、失败重试和后台任务不能覆盖已编辑的业务草稿',()=>{
 const note='财务尚未核实，保留待确认事项'
 assert.equal(recoverSubmittedDraft(note),note)
 assert.equal(recoverSubmittedDraft(note,'之前失败的问题'),note)
 assert.equal(recoverSubmittedDraft('','用户主动提交的问题'),'用户主动提交的问题')
 assert.equal(recoverSubmittedDraft(''),'')
})
test('草稿按用户、数据源和分析记录隔离',()=>{
 const key=draftKey('admin','olist','one')
 for(const parts of [['growth','olist','one'],['admin','other','one'],['admin','olist','two']])assert.notEqual(draftKey(...parts),key)
 assert.equal(draftKey(undefined,'olist','one'),'')
 assert.equal(draftKey('admin','olist',''),'')
})
test('真实履约和商家查询的缩写具备环节、单位和解释',()=>{
 const fields=['carrier_date_missing','delivered_date_missing','p2c_valid_samples','p2c_avg_days','p2c_p90_days','c2d_valid_samples','c2d_avg_days','c2d_p90_days','seller_id','valid_orders','avg_days','p90_days']
 for(const field of fields){assert.notEqual(businessLabel(field),field);assert.ok(businessDefinition(field),field)}
 assert.match(businessLabel('p2c_p90_days'),/下单至交接/)
 assert.match(businessDefinition('c2d_p90_days'),/第90百分位/)
 assert.equal(statusLabel('delivered'),'已送达')
 assert.equal(businessLabel('unknown_custom_metric'),'unknown_custom_metric')
 assert.equal(businessDefinition('unknown_custom_metric'),'')
 assert.equal(businessCell(null,'p90_days'),'—')
 assert.equal(businessCell(0,'p90_days'),'0')
})
