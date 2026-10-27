import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.analysis_basis import actual_basis, basis_error, basis_for, period_fields
from app.api import routes
from app.config import BASE_DIR, Settings
from app.main import app
from app.model_client import ModelClient
from app.models import QueryResult
from app.monthly_review import review_query
from app.querying.duckdb_engine import DuckDbEngine
from app.querying.single_database_agent import SingleDatabaseAgent
from app.querying.data_qa_agent import DataQaAgent
from app.report_facts import build_facts, validate_and_render, factual_brief
from app.result_profile import build_profile
from app.security import AccessController
from app.services.askdata_service import AskDataService
from app.services.session_context import SessionContext
from app.skills import SkillRegistry


class BasisTests(unittest.TestCase):
    def test_period_detection_excludes_lateness_comparison(self):
        sql = "SELECT COUNT(*) FROM olist_orders_dataset o WHERE o.order_purchase_timestamp >= DATE '2018-07-01' AND CAST(o.order_delivered_customer_date AS DATE)>CAST(o.order_estimated_delivery_date AS DATE)"
        self.assertEqual(period_fields(sql), {"order_purchase_timestamp"})
        self.assertEqual(basis_error(sql, basis_for('')), '')

    def test_previous_time_basis_is_inherited_until_explicit_change(self):
        previous = basis_for('按下单时间')
        inherited = basis_for('继续查各品类8月销售额', previous)
        self.assertEqual(inherited['source'], 'inherited')
        bad = "SELECT SUM(price) FROM olist_order_items_dataset WHERE shipping_limit_date >= DATE '2018-08-01'"
        self.assertIn('统计时间口径发生变化', basis_error(bad, inherited))
        explicit = basis_for('改用最迟发货时间按月统计', previous)
        self.assertEqual(explicit['source'], 'explicit_change')
        self.assertEqual(basis_error(bad, explicit), '')

    def test_agent_retries_drift_before_executing_tool(self):
        model = Mock()
        invalid = {'action':'call_tool','tool_name':'query_olist_ecommerce','arguments':{'sql':"SELECT SUM(price) FROM olist_order_items_dataset WHERE shipping_limit_date >= DATE '2018-08-01'"}}
        valid = {'action':'call_tool','tool_name':'query_olist_ecommerce','arguments':{'sql':"SELECT COUNT(*) FROM olist_orders_dataset WHERE order_purchase_timestamp >= DATE '2018-08-01'"}}
        model.chat_json.side_effect = [invalid, valid]
        agent = SingleDatabaseAgent(model, Mock(), SkillRegistry().get('database_query'))
        decision = agent._validated_decision('', {'query':'继续查','analysis_basis':basis_for('')}, {'query_olist_ecommerce'})
        self.assertEqual(decision,valid)
        self.assertIn('统计时间口径发生变化', model.chat_json.call_args.args[1])


class ReportFactsTests(unittest.TestCase):
    def context(self):
        rows = [{'state':str(i),'change':v} for i,v in enumerate([185,25,8,4,4,4,2,1,0,-1,-2,-2,-2,-2,-3,-4,-4,-5,-9,-14])]
        return {'recent_result':json.dumps({'task_id':'states','title':'州级结果','columns':['state','change'],'rows':rows,'result_profile':build_profile(['state','change'],rows)})}

    def test_regression_state_distribution_is_calculated_not_guessed(self):
        facts = build_facts(self.context())
        self.assertEqual(facts['s0_n0_positive']['value'],'8')
        self.assertEqual(facts['s0_n0_zero']['value'],'1')
        self.assertEqual(facts['s0_n0_negative']['value'],'11')
        result = validate_and_render({'markdown':'增加{{fact:s0_n0_positive}}个州，持平{{fact:s0_n0_zero}}个州，下降{{fact:s0_n0_negative}}个州。'},facts,[])
        self.assertEqual(result['markdown'],'增加8个州，持平1个州，下降11个州。')
        self.assertEqual(len(result['fact_references']),3)
        for bad in ['20个州中，10个正向或持平，10个下降。','十个州下降。','下降{{fact:invented}}个州。']:
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate_and_render({'markdown':bad},facts,[])

    def test_factual_tables_have_contiguous_markdown_rows(self):
        context=self.context();facts=build_facts(context)
        brief,refs=factual_brief(context,facts)
        self.assertIn('| state | change |\n| --- | --- |\n',brief)
        self.assertIn('大于零 8 行，等于零 1 行，小于零 11 行',brief)
        self.assertTrue(refs)

    def test_rejected_model_prose_yields_explicit_factual_report_without_false_claim(self):
        model=Mock();model.chat_json.return_value={'action':'report','title':'报告','markdown':'有10个州下降。'}
        client=Mock();client.list_tools.return_value=[]
        result=DataQaAgent(model,lambda _:client,SkillRegistry().get('data_qa')).run('生成复盘报告',self.context(),{})
        self.assertEqual(result.report.generation_mode,'facts_only')
        self.assertIn('模型文字未通过',result.report.markdown)
        self.assertNotIn('有10个州下降',result.report.markdown)
        self.assertEqual(model.chat_json.call_count,2)

    def test_missing_result_column_is_not_missing_source_field(self):
        catalog=[{'table':'商品','fields':[{'name':'product_category_name'},{'name':'price'},{'name':'freight_value'}]}]
        with self.assertRaisesRegex(ValueError,'数据目录存在'):
            validate_and_render({'answer':'数据中缺少商品品类、价格和运费。'}, {}, catalog)
        result=validate_and_render({'answer':'当前结果未查询品类，可继续查询现有商品表。'}, {}, catalog)
        self.assertIn('继续查询',result['answer'])
        self.assertIn('没有成本',validate_and_render({'answer':'已有品类和价格，但没有成本。'}, {}, catalog)['answer'])

    def test_zero_count_does_not_prove_missing_data(self):
        with self.assertRaisesRegex(ValueError,'零不能'):
            validate_and_render({'answer':'RR本月配送量为零，属于数据缺失。'}, {}, [])
        self.assertIn('不等于',validate_and_render({'answer':'计数为零不等于数据缺失。'}, {}, [])['answer'])

    def test_enumerated_states_have_a_checked_cardinality(self):
        rows=[{'state':'SP','change':185},{'state':'RJ','change':25}]
        facts=build_facts({'recent_result':json.dumps({'task_id':'states','columns':['state','change'],'rows':rows,'result_profile':build_profile(['state','change'],rows)})})
        self.assertIn('两个州',validate_and_render({'answer':'主要来自 SP 与 RJ 两个州。'},facts,[])['answer'])
        with self.assertRaises(ValueError):validate_and_render({'answer':'主要来自 SP 与 RJ 三个州。'},facts,[])

    def test_calendar_outline_and_authorized_product_code_are_not_measurements(self):
        facts=build_facts(self.context())
        facts['product']={**facts['s0_rows'],'label':'home_appliances_2 · sales'}
        result=validate_and_render({'markdown':'# 2018 年 8 月报告\n### 1. 变化\n按 v1.0 口径查询 home_appliances_2。'},facts,[])
        self.assertIn('home_appliances_2',result['markdown'])
        with self.assertRaises(ValueError):validate_and_render({'markdown':'### 1. 变化\n共有69个品类。'},facts,[])
        self.assertIn('下降最多的五个品类',validate_and_render({'markdown':'建议优先核查下降最多的五个品类。'},facts,[])['markdown'])

    def test_missing_month_does_not_generate_change_facts(self):
        rows=[{'period':'2018-07','orders':2}]
        facts=build_facts({'recent_result':json.dumps({'task_id':'one','columns':['period','orders'],'rows':rows,'result_profile':build_profile(['period','orders'],rows)})})
        self.assertFalse(any('_change_' in f for f in facts))

    def test_two_period_changes_use_backend_decimal_arithmetic(self):
        rows=[{'period':'2018-07','orders':100,'late_rate':.01},{'period':'2018-08','orders':110,'late_rate':.03}]
        facts=build_facts({'recent_result':json.dumps({'task_id':'two','columns':['period','orders','late_rate'],'rows':rows,'result_profile':build_profile(['period','orders','late_rate'],rows)})})
        self.assertEqual(facts['s0_change_rate_c1']['display'],'10.00%')
        self.assertEqual(facts['s0_change_c2']['display'],'2.00 个百分点')

    def test_average_price_change_is_money_not_percentage_points(self):
        rows=[{'period':'2018-07','avg_price':10},{'period':'2018-08','avg_price':15}]
        facts=build_facts({'recent_result':json.dumps({'task_id':'two','columns':['period','avg_price'],'rows':rows,'result_profile':build_profile(['period','avg_price'],rows)})})
        self.assertEqual(facts['s0_change_c1']['display'],'5.00')

    def test_different_dimensions_are_not_implicitly_compared_as_monthly_totals(self):
        rows=[{'period':'2018-07','state':'SP','orders':10},{'period':'2018-08','state':'RJ','orders':15}]
        facts=build_facts({'recent_result':json.dumps({'task_id':'two','columns':['period','state','orders'],'rows':rows,'result_profile':build_profile(['period','state','orders'],rows)})})
        self.assertFalse(any('_change_' in f for f in facts))


class MonthlyTaskTests(unittest.TestCase):
    def setUp(self):self.snapshots=[]
    def tearDown(self):
        for sid in self.snapshots:(BASE_DIR/'data/result_snapshots'/f'{sid}.json').unlink(missing_ok=True)
    def execute(self,step):
        sql,_,_=review_query('2018-08',step)
        result=DuckDbEngine().execute('olist_ecommerce',sql,AccessController().resolve('demo_admin'))
        self.assertTrue(result.success,result.error);self.snapshots.append(result.snapshot_id)
        self.assertEqual(period_fields(sql),{'order_purchase_timestamp'})
        return result.rows
    def test_full_drilldowns_reconcile_with_overview_including_unknown_categories(self):
        overview=self.execute('overview');regions=self.execute('delivery_states');categories=self.execute('sales_categories')
        self.assertEqual([r['orders'] for r in overview],[6292,6512])
        self.assertEqual([r['item_sales'] for r in overview],[895507.22,854686.33])
        for index,prefix in [(0,'previous'),(1,'current')]:
            self.assertEqual(sum(r[prefix+'_late'] for r in regions),overview[index]['late'])
            self.assertEqual(sum(r[prefix+'_delivered'] for r in regions),overview[index]['delivered'])
            self.assertEqual(sum(Decimal(str(r[prefix+'_sales'])) for r in categories),Decimal(str(overview[index]['item_sales'])))
        self.assertIn('未分类',[r['category'] for r in categories])
    def test_period_input_cannot_inject_sql_or_overflow_dates(self):
        for month in ['2018-13',"2018-08'; DROP TABLE t",'0001-01','9999-12','2026-10']:
            with self.subTest(month=month),self.assertRaises(ValueError):review_query(month)


class MonthlyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=replace(Settings(),session_archive_enabled=True,session_archive_path=str(Path(self.temp.name)/'archive.db'),short_term_summary_enabled=False)
        self.service=AskDataService.__new__(AskDataService)
        self.service.context=SessionContext(ModelClient(self.config),self.config)
        self.service.access_controller=AccessController();self.service.tasks=self.service.context.tasks;self.service.session_tasks=self.service.context.session_tasks;self.service.workflow=Mock()
        self.patch=patch.object(routes,'service',self.service);self.patch.start();self.client=TestClient(app)
        self.admin=self.login('admin','admin123');self.growth=self.login('growth','growth123')
    def login(self,u,p):return {'Authorization':'Bearer '+self.client.post('/api/auth/login',json={'username':u,'password':p}).json()['access_token']}
    def tearDown(self):
        for task in self.service.tasks.values():
            sid=task['result'].snapshot_id
            if sid:(BASE_DIR/'data/result_snapshots'/f'{sid}.json').unlink(missing_ok=True)
        self.patch.stop();self.temp.cleanup()
    def payload(self):return {'query':'月度复盘','review_month':'2018-08','session_id':'monthly','workspace':{'database_id':'olist_ecommerce'}}
    def test_monthly_task_is_owned_persistent_and_does_not_use_model_sql(self):
        response=self.client.post('/api/query',json=self.payload(),headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertEqual(result['status'],'completed');self.assertTrue(result['analysis_basis']['verified'])
        self.service.workflow.invoke.assert_not_called()
        restored=SessionContext(ModelClient(self.config),self.config)
        self.assertEqual(restored.latest_table_result('demo_admin:monthly:olist_ecommerce').review_context['month'],'2018-08')
        self.assertEqual(self.client.get('/api/tasks/'+result['task_id'],headers=self.growth).status_code,403)
    def test_role_source_and_ambiguous_workflow_are_rejected(self):
        payload=self.payload()
        self.assertEqual(self.client.post('/api/jobs',json=payload,headers=self.growth).status_code,403)
        payload['workspace']['database_id']='short_video_ops'
        self.assertEqual(self.client.post('/api/jobs',json=payload,headers=self.admin).status_code,400)
        payload=self.payload();payload['metric_id']='item_sales'
        self.assertEqual(self.client.post('/api/query',json=payload,headers=self.admin).status_code,400)
    def test_automatic_report_sources_only_use_current_user_and_source(self):
        for owner,db,sid in [('demo_admin','olist_ecommerce','monthly'),('demo_growth_ops','short_video_ops','monthly'),('demo_admin','short_video_ops','other')]:
            task=owner+db
            self.service.context.remember(f'{owner}:{sid}:{db}',QueryResult(task_id=task,status='completed',route='database_query',message='ok',rows=[{'orders':1}],columns=['orders']), '问题',{'database_id':db},owner)
        context,sources=self.service.context.analysis_context('demo_admin:monthly:olist_ecommerce',{})
        self.assertEqual(len(sources),1);self.assertNotIn('short_video_ops',context)


if __name__=='__main__':unittest.main()
