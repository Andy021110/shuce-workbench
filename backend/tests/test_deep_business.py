import json,unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock
from app.analysis_scope import apply_filters,filters_for_query,exclusions_for_query
from app.query_intent import intent_for,business_sql_error,legacy_result_error,month_periods,public_intent
from app.querying.duckdb_engine import DuckDbEngine
from app.querying.response_generator import ResponseGenerator
from app.querying.models import SqlExecution
from app.querying.data_qa_agent import DataQaAgent
from app.skills import SkillRegistry
from app.business_requests import business_result
from app.models import QueryResult
from app.services.session_context import SessionContext
from app.model_client import ModelClient
from tests.test_complex_business import FINANCE
from tests import test_business_tasks as base
CASES=json.loads((Path(__file__).parent/'fixtures/deep_business.json').read_text())
class DeepIntentTests(unittest.TestCase):
 def execute(self,sql):
  engine=DuckDbEngine();engine._validate_sql('olist_ecommerce',sql)
  with engine.connect('olist_ecommerce') as c:
   cur=c.execute(sql);cols=[d[0] for d in cur.description];return [dict(zip(cols,row)) for row in cur.fetchall()]
 def test_original_excluded_month_is_not_positive(self):
  i=intent_for(CASES['months']['query']);self.assertEqual(i['periods'],['2017-12','2018-02'])
  self.assertTrue(business_sql_error(CASES['months']['old_sql'],i))
  rows=self.execute(apply_filters(FINANCE.replace('o JOIN items','o LEFT JOIN items'),{},i['periods']))
  self.assertEqual([r['orders'] for r in rows],[5673,6728]);self.assertEqual([r['item_sales'] for r in rows],[743914.17,844178.71])
 def test_sample_cohort_and_emphasis_are_not_negative_or_measure_only(self):
  self.assertEqual(filters_for_query('非常关注SP州订单')['states'],['SP'])
  i=filters_for_query('只看SP州已送达样本，2018年8月平均时长')
  self.assertEqual(i['states'],['SP']);self.assertEqual(i['order_statuses'],['delivered'])
 def test_month_range_with_excluded_middle_month(self):
  self.assertEqual(month_periods('2017年12月至2018年2月每月订单，排除2018年1月'),['2017-12','2018-02'])
  self.assertEqual(month_periods('2018年7月和8月每月订单'),['2018-07','2018-08'])
 def test_negative_lists_and_meta_corrections(self):
  for key in ['exclude','exclude_note']:
   i=intent_for(CASES[key]['query']);self.assertEqual(i['filters']['states'],[]);self.assertEqual(i['exclusions']['states'],['RJ','SP']);self.assertTrue(business_sql_error(CASES[key]['old_sql'],i))
  for q in ['排除SP、RJ两个州','排除SP, RJ','SP和RJ以外的地区']:
   self.assertEqual(exclusions_for_query(q)['states'],['RJ','SP'],q);self.assertEqual(filters_for_query(q)['states'],[],q)
  self.assertEqual(filters_for_query('只看SP，不是RJ')['states'],['SP']);self.assertEqual(exclusions_for_query('只看SP，不是RJ')['states'],['RJ'])
 def test_status_and_category_negative_lists(self):
  q='2018年8月排除已取消和不可用订单；排除手表礼品和健康美容';self.assertEqual(filters_for_query(q),{'states':[],'order_statuses':[],'categories':[]})
  e=exclusions_for_query(q);self.assertEqual(e['order_statuses'],['canceled','unavailable']);self.assertEqual(e['categories'],['health_beauty','watches_gifts'])
 def test_server_enforces_exclusion_if_model_omits_it(self):
  i=intent_for(CASES['exclude']['query']);raw='SELECT c.customer_state,COUNT(DISTINCT o.order_id) AS orders FROM olist_orders_dataset o JOIN olist_customers_dataset c USING(customer_id) GROUP BY c.customer_state ORDER BY orders DESC,c.customer_state'
  sql=apply_filters(raw,i['filters'],i['periods'],exclusions=i['exclusions']);rows=self.execute(sql)
  self.assertEqual(len(rows),24);self.assertEqual(rows[0],{'customer_state':'MG','orders':708});self.assertNotIn('RJ',[r['customer_state'] for r in rows]);self.assertNotIn('SP',[r['customer_state'] for r in rows]);self.assertFalse(public_intent(i,sql)['can_continue'])
 def test_measure_sample_does_not_narrow_all_statuses(self):
  for key in ['samples','zero']:
   i=intent_for(CASES[key]['query']);self.assertEqual(i['filters']['order_statuses'],[]);self.assertTrue(i['all_statuses']);self.assertTrue(business_sql_error(CASES[key]['old_sql'],i))
  i=intent_for(CASES['samples']['query']);sql="SELECT COUNT(*) AS orders,COUNT(*) FILTER(WHERE order_status='delivered' AND order_delivered_customer_date IS NOT NULL AND order_estimated_delivery_date IS NOT NULL) AS delivered FROM olist_orders_dataset"
  self.assertEqual(business_sql_error(sql,i),'');self.assertEqual(self.execute(apply_filters(sql,i['filters'],i['periods'])),[{'orders':6512,'delivered':6351}])
 def test_all_statuses_rejects_hidden_cte_narrowing(self):
  i=intent_for(CASES['samples']['query'])
  for sql in ["SELECT COUNT(*) FROM olist_orders_dataset WHERE order_status='delivered'","WITH narrow AS(SELECT order_id FROM olist_orders_dataset WHERE order_status='delivered') SELECT COUNT(*) FROM narrow"]:self.assertIn('全部状态',business_sql_error(sql,i))
 def test_old_counterexamples_require_rechecking(self):
  for key in ['months','exclude','exclude_note','samples','relative']:self.assertTrue(legacy_result_error(CASES[key]['old_sql'],CASES[key]['query']),key)
 def test_model_prose_conflict_stops_business_completion(self):
  m=Mock();m.chat_json.return_value={'valid':True,'analysis':'SQL将订单范围限定为已送达状态，因此订单总数并非问题要求的全部状态口径。'}
  self.assertFalse(ResponseGenerator(m).finalize('全部状态',SqlExecution('SELECT 1',True,total_rows=1),'','')['valid'])
 def test_profit_boundary_is_not_scope_conflict(self):
  m=Mock();m.chat_json.return_value={'valid':True,'analysis':'支付减商品金额不能作为利润，缺少成本依据。'}
  self.assertTrue(ResponseGenerator(m).finalize('财务对账',SqlExecution('SELECT 1',True,total_rows=1),'','')['valid'])
 def test_relative_dates_use_today_and_confirm_instead_of_query(self):
  for today,expected in [(date(2026,10,27),'2026-09'),(date(2026,1,3),'2025-12')]:
   r=business_result('relative',CASES['relative']['query'],{},None,today=today);self.assertEqual(r.workflow_mode,'date_confirmation');self.assertIsNone(r.sql);self.assertIn(expected,r.analysis)
  self.assertIsNone(business_result('absolute','2018年8月订单数',{},None))
class DeepServiceTests(unittest.TestCase):
 setUp=base.MonthlyApiTests.setUp
 tearDown=base.MonthlyApiTests.tearDown
 login=base.MonthlyApiTests.login
 def post(self,q,headers=None):return self.client.post('/api/query',headers=headers or self.admin,json={'query':q,'session_id':'deep','workspace':{'database_id':'olist_ecommerce'}})
 def test_buyer_cohort_current_and_history_are_independent(self):
  response=self.post(CASES['cohort']['query']);self.assertEqual(response.status_code,200,response.text);r=response.json();self.assertEqual(r['status'],'completed');self.assertEqual(r['workflow_mode'],'buyer_cohort')
  row=r['rows'][0];self.assertEqual(row['buyers'],6460);self.assertEqual(row['returning_buyers'],189);self.assertAlmostEqual(row['returning_rate'],189/6460);self.assertFalse(r['query_intent']['can_continue']);self.service.workflow.invoke.assert_not_called()
  restored=SessionContext(ModelClient(self.config),self.config);self.assertEqual(restored.tasks[r['task_id']]['result'].rows,r['rows'])
 def test_empty_cohort_keeps_null_rate(self):
  r=self.post('2016年11月的买家中，在此前历史买过的老客有多少及占比？全部订单状态。').json();self.assertEqual(r['rows'],[{'buyers':0,'returning_buyers':0,'returning_rate':None}]);self.assertIn('不可计算',r['analysis'])
 def test_cohort_does_not_bypass_permissions_or_existing_scope(self):
  self.assertEqual(self.post(CASES['cohort']['query'],self.growth).status_code,403)
  r=self.post(CASES['cohort']['query']+' 只看SP州').json();self.assertEqual(r['workflow_mode'],'cohort_confirmation');self.assertIsNone(r['sql']);self.service.workflow.invoke.assert_not_called()
 def test_relative_question_archived_without_model_or_sql(self):
  r=self.post(CASES['relative']['query']).json();self.assertEqual(r['workflow_mode'],'date_confirmation');self.assertIsNone(r['sql']);self.service.workflow.invoke.assert_not_called();self.assertIn(r['task_id'],self.service.tasks)
 def test_old_saved_validated_flag_does_not_exempt_wrong_scope(self):
  case=CASES['samples'];old=QueryResult(task_id='v8bad',status='completed',route='database_query',message='ok',sql=case['old_sql'],rows=[{'orders':6351}],query_intent={'validated':True})
  self.service.context.remember('demo_admin:old:olist_ecommerce',old,case['query'],{'database_id':'olist_ecommerce'},'demo_admin');restored=SessionContext(ModelClient(self.config),self.config)
  self.assertEqual(restored.tasks['v8bad']['result'].status,'failed');self.assertEqual(restored.analysis_context('demo_admin:old:olist_ecommerce',{})[1],[])
class FactAliasTests(unittest.TestCase):
 def agent(self,payload):
  model=Mock();model.chat_json.return_value=payload;client=Mock();client.list_tools.return_value=[];return DataQaAgent(model,lambda _:client,SkillRegistry().get('data_qa'))
 def context(self):return {'recent_result':json.dumps({'task_id':'csv','title':'人工CSV汇总','columns':['channel','amount'],'rows':[{'channel':'web','amount':42.5},{'channel':'store','amount':7.25}]})}
 def test_valid_advertised_alias_works_in_ordinary_answer(self):
  result=self.agent({'action':'answer','answer':'金额为{{fact:F001}}。','tool_calls':[]}).run('解释选中结果',self.context(),{});self.assertEqual(result.answer,'金额为42.50。')
 def test_invalid_alias_is_not_guessed_and_yields_same_source_facts(self):
  result=self.agent({'action':'answer','answer':'金额{{fact:F999}}。','tool_calls':[]}).run('解释选中结果',self.context(),{});self.assertEqual(result.report.generation_mode,'facts_only');self.assertIn('42.50',result.report.evidence_markdown);self.assertNotIn('F999',result.report.markdown);self.assertTrue(all(f['source_task_id']=='csv' for f in result.report.fact_references))
if __name__=='__main__':unittest.main()
