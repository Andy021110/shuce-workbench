import unittest
from unittest.mock import Mock,patch
from decimal import Decimal
from app.analysis_scope import apply_filters,filters_for_query,simple_sql_filters
from app.config import BASE_DIR
from app.model_client import ModelClient
from app.models import QueryResult,AnalysisReport
from app.monthly_review import review_query
from app.querying.duckdb_engine import DuckDbEngine
from app.querying.single_database_agent import SingleDatabaseAgent
from app.security import AccessController
from app.services.session_context import SessionContext
from app.skills import SkillRegistry
from tests import test_business_tasks as base

class ScopeSqlTests(unittest.TestCase):
    def setUp(self):self.snapshots=[]
    def tearDown(self):
        for sid in self.snapshots:(BASE_DIR/'data/result_snapshots'/f'{sid}.json').unlink(missing_ok=True)
    def execute(self,sql):
        r=DuckDbEngine().execute('olist_ecommerce',sql,AccessController().resolve('demo_admin'))
        self.assertTrue(r.success,r.error);self.snapshots.append(r.snapshot_id);return r.rows
    def test_scoped_steps_reconcile_and_match_independent_raw_join(self):
        filters={'states':['SP'],'order_statuses':['delivered'],'categories':['watches_gifts']}
        data={step:self.execute(review_query('2018-08',step,filters)[0]) for step in ('overview','sales_drivers','sales_categories','delivery_states')}
        independent=self.execute("SELECT strftime(o.order_purchase_timestamp,'%Y-%m') AS period, COUNT(DISTINCT o.order_id) AS orders, SUM(CAST(i.price AS DECIMAL(18,2))) AS item_sales FROM olist_orders_dataset o JOIN olist_customers_dataset c USING(customer_id) JOIN olist_order_items_dataset i USING(order_id) JOIN olist_products_dataset p USING(product_id) JOIN product_category_name_translation t USING(product_category_name) WHERE c.customer_state='SP' AND o.order_status='delivered' AND t.product_category_name_english='watches_gifts' AND o.order_purchase_timestamp>=DATE '2018-07-01' AND o.order_purchase_timestamp<DATE '2018-09-01' GROUP BY period ORDER BY period")
        for n,expected in enumerate(independent):
            a=data['overview'][n];b=data['sales_drivers'][n];prefix='previous' if n==0 else 'current'
            self.assertEqual(a['orders'],expected['orders']);self.assertEqual(a['item_sales'],expected['item_sales'])
            self.assertEqual(a['orders'],b['orders']);self.assertEqual(a['item_sales'],b['item_sales'])
            self.assertEqual(sum(Decimal(str(r[prefix+'_sales'])) for r in data['sales_categories']),Decimal(str(a['item_sales'])))
            for field in ('late','delivered'):self.assertEqual(sum(r[prefix+'_'+field] for r in data['delivery_states']),a[field])
            self.assertAlmostEqual(b['sales_per_order'],b['items_per_order']*b['avg_item_price'])
        self.assertEqual([r['orders'] for r in data['overview']],[179,168])
    def test_outer_or_and_union_cannot_remove_cohort_filter(self):
        filters={'states':['SP'],'order_statuses':['delivered']}
        sql="SELECT COUNT(DISTINCT o.order_id) AS orders FROM olist_orders_dataset o WHERE o.order_status='canceled' OR 1=1"
        expected=self.execute("SELECT COUNT(DISTINCT o.order_id) AS orders FROM olist_orders_dataset o JOIN olist_customers_dataset c USING(customer_id) WHERE c.customer_state='SP' AND o.order_status='delivered'")[0]['orders']
        self.assertEqual([r['orders'] for r in self.execute(apply_filters(sql+' UNION ALL '+sql,filters))],[expected,expected])
    def test_standalone_dimension_and_item_queries_are_also_cohort_scoped(self):
        filters={'states':['SP'],'order_statuses':['delivered']}
        expected=self.execute("SELECT COUNT(DISTINCT o.customer_id) AS n FROM olist_orders_dataset o JOIN olist_customers_dataset c USING(customer_id) WHERE c.customer_state='SP' AND o.order_status='delivered'")[0]['n']
        actual=self.execute(apply_filters('SELECT COUNT(DISTINCT customer_id) AS n FROM olist_customers_dataset',filters))[0]['n']
        self.assertEqual(actual,expected)
        expected_sales=self.execute("SELECT SUM(i.price) AS sales FROM olist_order_items_dataset i JOIN olist_orders_dataset o USING(order_id) JOIN olist_customers_dataset c USING(customer_id) WHERE c.customer_state='SP' AND o.order_status='delivered'")[0]['sales']
        actual_sales=self.execute(apply_filters('SELECT SUM(price) AS sales FROM olist_order_items_dataset',filters))[0]['sales']
        self.assertAlmostEqual(actual_sales,expected_sales,places=6)
        with self.assertRaisesRegex(ValueError,'引用'):apply_filters('SELECT 123 AS orders',filters)
    def test_scoped_rewrite_preserves_monetary_join_guard(self):
        sql='SELECT SUM(i.price) AS sales FROM olist_order_items_dataset i JOIN olist_order_payments_dataset p USING(order_id) JOIN olist_orders_dataset o USING(order_id)'
        with self.assertRaisesRegex(ValueError,'一对多'):apply_filters(sql,{'states':['SP'],'categories':['watches_gifts']})
        _,ctx,_=review_query('2018-08','sales_drivers',{'order_statuses':['delivered']})
        self.assertNotIn('分母含全部状态',ctx['decomposition_note'])
    def test_unclassified_and_empty_delivery_scope(self):
        overview=self.execute(review_query('2018-08','overview',{'categories':['未分类']})[0])
        categories=self.execute(review_query('2018-08','sales_categories',{'categories':['未分类']})[0])
        self.assertEqual(categories[0]['current_sales'],overview[1]['item_sales'])
        canceled=self.execute(review_query('2018-08','overview',{'order_statuses':['canceled']})[0])
        self.assertTrue(all(r['delivered']==0 and r['late_rate'] is None for r in canceled))
    def test_explicit_scope_changes_simple_sql_and_agent_enforcement(self):
        previous={'states':['SP'],'order_statuses':['delivered'],'categories':['watches_gifts']}
        self.assertEqual(filters_for_query('继续',previous),previous)
        self.assertEqual(filters_for_query('改查 RJ，只看已取消订单，全部品类',previous),{'states':['RJ'],'order_statuses':['canceled'],'categories':[]})
        self.assertFalse(any(filters_for_query('清除全部筛选',previous).values()))
        sql="SELECT COUNT(order_id) FROM olist_orders_dataset WHERE order_status='delivered'"
        self.assertEqual(simple_sql_filters(sql)['order_statuses'],['delivered'])
        self.assertFalse(any(simple_sql_filters(sql+' UNION ALL '+sql).values()))
        model=Mock();model.chat_json.return_value={'action':'call_tool','tool_name':'query_olist_ecommerce','arguments':{'sql':'SELECT COUNT(order_id) FROM olist_orders_dataset'}}
        agent=SingleDatabaseAgent(model,Mock(),SkillRegistry().get('database_query'))
        decision=agent._validated_decision('',{'query':'继续','analysis_filters':{'states':['SP']}},{'query_olist_ecommerce'})
        self.assertIn('customer_state',decision['arguments']['sql']);self.assertIn("'SP'",decision['arguments']['sql'])

class WorkflowApiTests(unittest.TestCase):
    setUp=base.MonthlyApiTests.setUp
    tearDown=base.MonthlyApiTests.tearDown
    login=base.MonthlyApiTests.login
    payload=base.MonthlyApiTests.payload
    def seed(self,tid='report',database='olist_ecommerce',report=True):
        result=QueryResult(task_id=tid,status='completed',route='data_qa' if report else 'database_query',message='ok',rows=[] if report else [{'orders':1}],columns=[] if report else ['orders'],report=AnalysisReport(title='原报告',markdown='原始事实',generation_mode='facts_only',fact_references=[{'id':'a','display':'1'}]) if report else None)
        self.service.context.remember(f'demo_admin:monthly:{database}',result,'原始问题',{'database_id':database},'demo_admin')
        return result
    def test_rename_persists_and_is_owned(self):
        self.seed(report=False)
        payload={'database_id':'olist_ecommerce','title':'  SP月度复盘  '}
        response=self.client.patch('/api/sessions/monthly',headers=self.admin,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/sessions',headers=self.admin).json()[0]['title'],'SP月度复盘')
        restored=SessionContext(ModelClient(self.config),self.config)
        self.assertEqual(restored.archive.session_titles('demo_admin')[('monthly','olist_ecommerce')],'SP月度复盘')
        self.assertEqual(self.client.patch('/api/sessions/monthly',headers=self.growth,json=payload).status_code,403)
        payload['title']=' '
        self.assertEqual(self.client.patch('/api/sessions/monthly',headers=self.admin,json=payload).status_code,422)
    def test_same_id_sources_do_not_mix_titles_or_delete_other_source(self):
        self.seed(tid='olist',report=False);self.seed(tid='video',database='short_video_ops',report=False)
        self.client.patch('/api/sessions/monthly',headers=self.admin,json={'database_id':'olist_ecommerce','title':'电商复盘'})
        items=self.client.get('/api/sessions',headers=self.admin).json();self.assertEqual(len(items),2)
        self.assertEqual({i['workspace']['database_id']:i['title'] for i in items},{'olist_ecommerce':'电商复盘','short_video_ops':'原始问题'})
        self.client.delete('/api/sessions/monthly?database_id=short_video_ops',headers=self.admin)
        self.assertEqual(self.client.get('/api/sessions',headers=self.admin).json()[0]['title'],'电商复盘')
    def test_edit_persists_original_and_rejects_stale_revision(self):
        result=self.seed();original=result.report.model_dump()
        body={'expected_revision':0,'title':'例会交付版','conclusion':'核查配送','hypotheses':'原因待验证','actions':'对照时间'}
        response=self.client.patch('/api/tasks/report/report',headers=self.admin,json=body)
        self.assertEqual(response.status_code,200,response.text);edit=response.json()
        self.assertEqual(edit['revision'],1);self.assertEqual(result.report.model_dump(),original)
        self.assertEqual(self.client.patch('/api/tasks/report/report',headers=self.admin,json=body).status_code,409)
        self.assertEqual(self.client.patch('/api/tasks/report/report',headers=self.growth,json=body).status_code,403)
        restored=SessionContext(ModelClient(self.config),self.config)
        self.assertEqual(restored.tasks['report']['result'].report_edit,edit)
        self.assertEqual(restored.tasks['report']['result'].report.model_dump(),original)
        body['expected_revision']=1;body['actions']='复查结果'
        self.assertEqual(self.client.patch('/api/tasks/report/report',headers=self.admin,json=body).json()['revision'],2)
    def test_report_storage_failure_keeps_cache_revision_unchanged(self):
        result=self.seed()
        with patch.object(self.service.context.archive,'save_turn',side_effect=OSError('存档失败')):
            with self.assertRaises(OSError):self.client.patch('/api/tasks/report/report',headers=self.admin,json={'expected_revision':0,'conclusion':'草稿'})
        self.assertIsNone(result.report_edit)
        self.assertEqual(self.client.patch('/api/tasks/report/report',headers=self.admin,json={'expected_revision':0,'conclusion':'草稿'}).json()['revision'],1)
    def test_report_sources_do_not_mix_monthly_scopes(self):
        for tid,state in [('sp','SP'),('rj','RJ')]:
            result=QueryResult(task_id=tid,status='completed',route='database_query',message='ok',rows=[{'orders':1}],columns=['orders'],review_context={'month':'2018-08','step':'overview','filters':{'states':[state]}})
            self.service.context.remember('demo_admin:monthly:olist_ecommerce',result,'范围查询',{'database_id':'olist_ecommerce'},'demo_admin')
        _,sources=self.service.context.analysis_context('demo_admin:monthly:olist_ecommerce',{})
        self.assertEqual([s['task_id'] for s in sources],['rj'])
    def test_direct_fact_report_uses_owned_saved_results_and_remains_editable(self):
        self.seed(report=False)
        response=self.client.post('/api/sessions/monthly/fact-report',headers=self.admin,json={'database_id':'olist_ecommerce'})
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertEqual(result['report']['generation_mode'],'backend_facts')
        self.assertTrue(result['report']['fact_references'])
        self.service.workflow.invoke.assert_not_called()
        self.assertEqual(self.client.patch('/api/tasks/'+result['task_id']+'/report',headers=self.admin,json={'expected_revision':0,'conclusion':'人工判断'}).status_code,200)
        self.assertEqual(self.client.post('/api/sessions/monthly/fact-report',headers=self.growth,json={'database_id':'olist_ecommerce'}).status_code,403)
        self.assertEqual(self.client.post('/api/sessions/missing/fact-report',headers=self.admin,json={'database_id':'olist_ecommerce'}).status_code,400)
    def test_nonreport_and_oversized_edit_rejected(self):
        self.seed(report=False)
        self.assertEqual(self.client.patch('/api/tasks/report/report',headers=self.admin,json={'expected_revision':0}).status_code,400)
        self.assertEqual(self.client.patch('/api/tasks/report/report',headers=self.admin,json={'expected_revision':0,'conclusion':'x'*4001}).status_code,422)
    def test_scope_inputs_options_and_restoration(self):
        self.assertEqual(self.client.get('/api/review/options',headers=self.growth).status_code,403)
        self.assertIn('watches_gifts',self.client.get('/api/review/options',headers=self.admin).json()['categories'])
        for filters,code in [({'states':['ZZ']},422),({'categories':["x';DROP"]},422),({'categories':['not_real_category']},400),({'extra':['SP']},422)]:
            for route in ('query','jobs'):
                payload=self.payload();payload['review_filters']=filters
                with self.subTest(filters=filters,route=route):self.assertEqual(self.client.post('/api/'+route,json=payload,headers=self.admin).status_code,code)
        payload=self.payload();payload['review_filters']={'states':['SP'],'categories':['watches_gifts'],'order_statuses':['delivered']}
        response=self.client.post('/api/query',json=payload,headers=self.admin)
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['analysis_scope']['states'],['SP'])
        restored=SessionContext(ModelClient(self.config),self.config)
        self.assertEqual(restored.analysis_filters('demo_admin:monthly:olist_ecommerce','继续'),payload['review_filters'])
        self.assertFalse(any(restored.analysis_filters('demo_growth_ops:monthly:short_video_ops','继续').values()))

if __name__=='__main__':unittest.main()
