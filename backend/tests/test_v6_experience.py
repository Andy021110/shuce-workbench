import json
import unittest
from unittest.mock import Mock
from app.analysis_scope import continuation_guard, category_options
from app.category_labels import LABELS
from app.models import QueryResult
from app.querying.data_qa_agent import DataQaAgent
from app.report_facts import build_facts
from app.result_profile import build_profile
from app.skills import SkillRegistry
from app.services.session_context import SessionContext
from app.model_client import ModelClient
from tests import test_business_tasks as base

class NarrativeTests(unittest.TestCase):
    def setUp(self):
        rows=[{'category':'watches_gifts','sales_change':-3152.65}]
        self.context={'recent_result':json.dumps({'task_id':'category','title':'品类金额','columns':['category','sales_change'],'rows':rows,'result_profile':build_profile(['category','sales_change'],rows)})}
        self.model=Mock();self.client=Mock();self.client.list_tools.return_value=[]
        self.agent=DataQaAgent(self.model,lambda _:self.client,SkillRegistry().get('data_qa'))
        self.payload={'action':'report','title':'品类复盘','answer':'优先核查商品结构变化。','observations':[{'interpretation':'手表与礼品商品金额下降，原因尚待核查。','fact_ids':['s0_r0_c1']}],'hypotheses':['商品结构变化可能相关，当前结果无法确认原因。'],'actions':['继续查询明细价格与每单明细条数。'],'tool_calls':[]}
    def test_structured_narrative_renders_server_values_with_real_provenance(self):
        self.model.chat_json.return_value=self.payload
        result=self.agent.run('生成报告',self.context,{})
        self.assertEqual(result.report.generation_mode,'model_with_facts')
        self.assertIn('-3,152.65',result.report.markdown)
        self.assertIn('手表与礼品',result.report.markdown)
        self.assertNotIn('{{fact:',result.report.markdown)
        self.assertIn('s0_r0_c1',[r['id'] for r in result.report.fact_references])
        self.assertEqual(self.model.chat_json.call_count,1)
    def test_invented_reference_and_uncited_business_number_still_fail_closed(self):
        for key,value in [('fact_ids',['invented']),('interpretation','商品金额下降100元。')]:
            payload={**self.payload,'observations':[{**self.payload['observations'][0],key:value}]}
            self.model.chat_json.return_value=payload
            result=self.agent.run('生成报告',self.context,{})
            self.assertEqual(result.report.generation_mode,'facts_only')
            self.assertNotIn('下降100元',result.report.markdown)
    def test_single_dimension_scope_cardinality_is_checked_against_every_source(self):
        from app.report_facts import validate_and_render
        facts=build_facts(self.context)
        text={'answer':'当前仅覆盖SP一个州和watches_gifts一个品类。'}
        with self.assertRaises(ValueError): validate_and_render(text,facts,[])
        for f in facts.values():f['filters']={'states':['SP'],'categories':['watches_gifts']}
        self.assertIn('一个州',validate_and_render(text,facts,[])['answer'])
        facts[next(iter(facts))]['filters']['states']=['SP','RJ']
        with self.assertRaises(ValueError):validate_and_render(text,facts,[])

    def test_chart_execution_failure_keeps_valid_narrative(self):
        args={'title':'金额变化','source_task_id':'category','category_field':'category','value_field':'sales_change'}
        self.model.chat_json.return_value={**self.payload,'tool_calls':[{'name':'build_bar_chart','arguments':args}]}
        self.client.call_tool.side_effect=ValueError('图表参数无效')
        result=self.agent.run('生成报告',self.context,{})
        self.assertIn('手表与礼品',result.report.markdown)
        self.assertEqual(result.report.visualizations,[])
        self.assertTrue(result.report.validation_feedback)
    def test_category_dictionary_covers_actual_source_codes(self):
        self.assertFalse(set(category_options())-set(LABELS))
        self.assertEqual(LABELS['watches_gifts'],'手表与礼品')

class ScopeContinuationTests(unittest.TestCase):
    def test_ambiguous_cte_or_having_and_price_conditions_require_restatement(self):
        for sql in ["SELECT COUNT(*) FROM olist_orders_dataset WHERE order_status='delivered' OR order_status='canceled'", "SELECT order_id FROM olist_order_items_dataset GROUP BY order_id HAVING SUM(price)>100", "WITH x AS (SELECT * FROM olist_order_items_dataset WHERE price>100) SELECT COUNT(*) FROM x"]:
            previous=QueryResult(task_id='a',status='completed',route='database_query',message='ok',sql=sql)
            self.assertIn('重述',continuation_guard('继续按品类拆解',previous))
            self.assertFalse(continuation_guard('清除全部筛选后继续',previous))
            self.assertFalse(continuation_guard('查询2018年8月所有订单数',previous))
            previous.review_context={'month':'2018-08','step':'overview'}
            self.assertFalse(continuation_guard('继续按品类拆解',previous))

    def test_guard_stops_before_any_database_tool_is_discovered(self):
        from app.querying.single_database_agent import SingleDatabaseAgent
        from app.errors import PipelineStageError
        factory=Mock()
        agent=SingleDatabaseAgent(Mock(),factory,SkillRegistry().get('database_query'))
        with self.assertRaisesRegex(PipelineStageError,'重述'):
            agent.prepare('继续按州拆解','olist_ecommerce',{},'',{}, {'scope_guard':'请重述全部条件'}, {})
        factory.assert_not_called()

class ReadinessAndTemplateTests(unittest.TestCase):
    setUp=base.MonthlyApiTests.setUp
    tearDown=base.MonthlyApiTests.tearDown
    login=base.MonthlyApiTests.login
    def test_readiness_actual_missing_dates_repeated_review_ids_and_authorization(self):
        r=self.client.get('/api/data-readiness?database_id=olist_ecommerce',headers=self.admin)
        self.assertEqual(r.status_code,200,r.text);data=r.json()
        self.assertEqual(data['coverage']['delivered_missing_comparison_dates'],8)
        self.assertEqual(data['coverage']['boundary_months'],['2016-09','2018-10'])
        months=data['coverage']['months']
        self.assertEqual(sum(m['orders'] for m in months),99441)
        november=next(m for m in months if m['month']=='2016-11')
        self.assertEqual(november,{'month':'2016-11','orders':0,'active_days':0,'calendar_days':30})
        orders=next(t for t in data['tables'] if t['name']=='olist_orders_dataset')
        reviews=next(t for t in data['tables'] if t['name']=='olist_order_reviews_dataset')
        self.assertEqual(orders['rows'],99441);self.assertEqual(orders['duplicate_identifier_rows'],0)
        self.assertEqual(reviews['duplicate_identifier_rows'],814)
        self.assertEqual(self.client.get('/api/data-readiness?database_id=olist_ecommerce',headers=self.growth).status_code,403)
        self.assertEqual(self.client.get('/api/data-readiness?database_id=not_a_database',headers=self.admin).status_code,403)
    def test_templates_persist_only_current_users_scope_and_contract_not_month_or_goals(self):
        body={'name':'SP手表礼品','filters':{'states':['SP'],'categories':['watches_gifts'],'order_statuses':['delivered']}}
        r=self.client.post('/api/review/templates',headers=self.admin,json=body)
        self.assertEqual(r.status_code,200,r.text);tid=r.json()['id']
        restored=SessionContext(ModelClient(self.config),self.config)
        items=restored.archive.review_templates('demo_admin','olist_ecommerce')
        self.assertEqual(items[0]['filters'],body['filters']);self.assertEqual(items[0]['contract_version'],'1.0')
        self.assertFalse(restored.archive.review_templates('demo_growth','olist_ecommerce'))
        self.assertEqual(self.client.delete('/api/review/templates/'+tid,headers=self.growth).status_code,404)
        self.assertEqual(self.client.get('/api/review/templates',headers=self.growth).status_code,403)
        for extra in ({'month':'2018-08'},{'targets':{'orders':999}}):
            self.assertEqual(self.client.post('/api/review/templates',headers=self.admin,json={**body,**extra}).status_code,422)
        self.assertEqual(self.client.post('/api/review/templates',headers=self.admin,json={'name':'未知','filters':{'categories':['invented']}}).status_code,400)
        self.assertEqual(self.client.delete('/api/review/templates/'+tid,headers=self.admin).status_code,200)
        self.assertFalse(restored.archive.review_templates('demo_admin','olist_ecommerce'))
    def test_chinese_table_filter_preserves_original_codes(self):
        result=QueryResult(task_id='category-filter',status='completed',route='database_query',message='ok',columns=['category','sales'],rows=[{'category':'watches_gifts','sales':12.5},{'category':'toys','sales':9}])
        self.service.context.remember('demo_admin:filters:olist_ecommerce',result,'品类',{'database_id':'olist_ecommerce'},'demo_admin')
        response=self.client.get('/api/tasks/category-filter/rows',headers=self.admin,params={'filter_text':'手表'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['rows'],[{'category':'watches_gifts','sales':12.5}])

    def test_template_limit_retains_saved_templates(self):
        for i in range(20):
            self.assertEqual(self.client.post('/api/review/templates',headers=self.admin,json={'name':f'模板{i}'}).status_code,200)
        r=self.client.post('/api/review/templates',headers=self.admin,json={'name':'超过上限'})
        self.assertEqual(r.status_code,400)
        self.assertEqual(len(self.client.get('/api/review/templates',headers=self.admin).json()),20)
