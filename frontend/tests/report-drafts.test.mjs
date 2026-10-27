import test from 'node:test'
import assert from 'node:assert/strict'
import { reportDraftKey,readReportDraft,writeReportDraft } from '../src/reportDrafts.ts'
const draft={title:'质量例会',conclusion:'已保存内容保持',hypotheses:'未保存的原因\n需要继续核对',actions:''}
function memory(){const items=new Map();return {getItem:key=>items.get(key)||null,setItem:(key,value)=>items.set(key,value),removeItem:key=>items.delete(key)}}
test('报告草稿恢复内容与基准版本，按用户数据源和报告隔离',()=>{
 const store=memory(),key=reportDraftKey('admin:olist','report-1')
 assert.ok(writeReportDraft(store,key,draft,3));assert.deepEqual(readReportDraft(store,key),{version:1,revision:3,draft})
 for(const [owner,id] of [['growth:olist','report-1'],['admin:csv','report-1'],['admin:olist','report-2']])assert.equal(readReportDraft(store,reportDraftKey(owner,id)),null)
 assert.equal(reportDraftKey('','report-1'),'')
})
test('损坏或超长的本地草稿不成为报告内容，存储失败有返回值',()=>{
 const store=memory(),key='draft';store.setItem(key,'broken');assert.equal(readReportDraft(store,key),null)
 store.setItem(key,JSON.stringify({version:1,revision:2,draft:{...draft,title:'x'.repeat(121)}}));assert.equal(readReportDraft(store,key),null)
 assert.equal(writeReportDraft({setItem(){throw Error('quota')}},key,draft,0),false)
 assert.equal(readReportDraft({getItem(){throw Error('disabled')}},key),null)
})
test('报告成功保存后可以清理本地草稿，不复活旧内容',()=>{
 const store=memory(),key=reportDraftKey('admin:olist','report-1');writeReportDraft(store,key,draft,0);store.removeItem(key);assert.equal(readReportDraft(store,key),null)
})
