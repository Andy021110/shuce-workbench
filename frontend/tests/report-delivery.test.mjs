import test from 'node:test'
import assert from 'node:assert/strict'
import {reportSections,reportMarkdown} from '../src/reportPresentation.ts'
import {businessLabel,businessDefinition,businessCell} from '../src/businessFormat.ts'

test('archived and new reports expose summary and evidence separately',()=>{
 const old={markdown:'## 判断\n不能认定涨价。\n\n## 查询事实\n|金额|\n|---|\n|10|'}
 assert.equal(reportSections(old).summary,'## 判断\n不能认定涨价。')
 assert.match(reportSections(old).evidence,/10/)
 assert.equal(reportSections({...old,evidence_markdown:'完整附录'}).evidence,'完整附录')
})
test('summary export omits appendix while full export preserves evidence and saved manual notes',()=>{
 const report={title:'经营摘要',markdown:'不能将均价变化当作同款涨价。',evidence_markdown:'## 查询事实\n金额9234.59',fact_references:[{label:'金额',display:'9234.59',source_title:'城市表',scope:'完整结果'}]}
 const short=reportMarkdown(report,'经营摘要','人工待办：核查同款价格',true)
 assert.match(short,/人工待办/);assert.doesNotMatch(short,/9234|查询事实|数值引用依据/)
 const full=reportMarkdown(report,'经营摘要','人工待办：核查同款价格')
 assert.match(full,/9234.59/);assert.match(full,/数值引用依据/)
})
test('known regional business metrics and delivery groups are readable without changing raw exports',()=>{
 for(const f of ['qty_jul','qty_aug','sales_delta','top5_share_of_total_delta','delivery_group','review_answer_timestamp'])assert.notEqual(businessLabel(f),f)
 assert.equal(businessCell(0.8339,'top5_share_of_total_delta'),'83.39%')
 assert.equal(businessCell('late','delivery_group'),'超时送达');assert.equal(businessCell('on_time','delivery_group'),'按时送达')
 assert.match(businessDefinition('qty_jul'),/不能.*件数/)
 assert.equal(businessLabel('unknown_special_metric'),'unknown_special_metric')
 assert.equal(businessCell('unknown','delivery_group'),'unknown')
})
