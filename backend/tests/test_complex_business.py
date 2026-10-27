import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.analysis_scope import apply_filters,filters_for_query
from app.config import BASE_DIR
from app.errors import PipelineStageError
from app.models import QueryResult
from app.query_intent import intent_for,business_sql_error,public_intent,reusable_cohort,month_periods
from app.querying.duckdb_engine import DuckDbEngine
from app.querying.single_database_agent import SingleDatabaseAgent
from app.querying.response_generator import ResponseGenerator
from app.querying.models import SqlExecution
from app.services.session_context import SessionContext
from app.model_client import ModelClient
from app.skills import SkillRegistry
from tests import test_business_tasks as base

QUESTION='核对2018年7月和8月SP州已送达且含watches_gifts商品的订单。每月返回去重订单数、仅手表礼品商品价格合计、同批订单全部支付金额。'
FINANCE="""WITH items AS (SELECT order_id,SUM(price) AS item_sales FROM olist_order_items_dataset GROUP BY order_id), pay AS (SELECT order_id,SUM(payment_value) AS payments FROM olist_order_payments_dataset GROUP BY order_id)
SELECT strftime(o.order_purchase_timestamp,'%Y-%m') AS month,COUNT(DISTINCT o.order_id) AS orders,ROUND(SUM(i.item_sales),2) AS item_sales,ROUND(SUM(p.payments),2) AS payments,ROUND(SUM(p.payments)-SUM(i.item_sales),2) AS payment_minus_item
FROM olist_orders_dataset o JOIN items i USING(order_id) LEFT JOIN pay p USING(order_id) GROUP BY month ORDER BY month"""
FIXTURES=Path(__file__).parent/'fixtures'

class ComplexBusinessTests(unittest.TestCase):
    def setUp(self):self.snapshots=[]
    def tearDown(self):
        for sid in self.snapshots:(BASE_DIR/'data/result_snapshots'/f'{sid}.json').unlink(missing_ok=True)
    def execute(self,sql):
        result=DuckDbEngine().execute('olist_ecommerce',sql)
        self.assertTrue(result.success,result.error);self.snapshots.append(result.snapshot_id);return result
    def decision(self,sql):return {'action':'call_tool','tool_name':'query_olist_ecommerce','arguments':{'sql':sql}}
    def agent(self,model):return SingleDatabaseAgent(model,Mock(),SkillRegistry().get('database_query'))
    def payload(self):
        intent=intent_for(QUESTION)
        return {'query':QUESTION,'business_intent':intent,'analysis_filters':intent['filters']}

    def test_initial_scope_and_chinese_code_portuguese_edits_share_category(self):
        original=intent_for(QUESTION)['filters']
        self.assertEqual(original,{'states':['SP'],'order_statuses':['delivered'],'categories':['watches_gifts']})
        for text in ['改看健康美容品类，保持SP和已送达','改看health_beauty品类','改看beleza_saude品类']:
            self.assertEqual(filters_for_query(text,original)['categories'],['health_beauty'])
        self.assertEqual(filters_for_query('清除全部筛选，只看RJ已取消订单',original),{'states':['RJ'],'order_statuses':['canceled'],'categories':[]})

    def test_compact_cohort_enforces_finance_scope_even_when_model_omits_filters(self):
        intent=intent_for(QUESTION)
        sql=apply_filters(FINANCE,intent['filters'],intent['periods'])
        self.assertLess(len(sql),4000)
        rows=self.execute(sql).rows
        self.assertEqual(rows,[{'month':'2018-07','orders':179,'item_sales':29398.09,'payments':31581.20,'payment_minus_item':2183.11},
                               {'month':'2018-08','orders':168,'item_sales':26245.44,'payments':28327.12,'payment_minus_item':2081.68}])
        health=intent_for('改看健康美容，每月比较2018年7月和8月',intent)
        health_rows=self.execute(apply_filters(FINANCE,health['filters'],health['periods'])).rows
        self.assertEqual([r['orders'] for r in health_rows],[313,370])
        self.assertEqual([r['item_sales'] for r in health_rows],[35838.01,49676.60])

    def test_real_counterexamples_fail_before_database_call_and_can_be_repaired(self):
        wrong=(FIXTURES/'finance_wrong_category.sql').read_text()
        missing=(FIXTURES/'finance_missing_scope.sql').read_text()
        self.assertIn('品类字段和值不匹配',business_sql_error(wrong,intent_for(QUESTION)))
        self.assertIn('每月',business_sql_error(missing,intent_for(QUESTION)))
        model=Mock();model.chat_json.side_effect=[self.decision(wrong),self.decision(missing),self.decision(FINANCE)]
        client=Mock();client.list_tools.return_value=[{'name':'query_olist_ecommerce'}]
        client.call_tool.side_effect=lambda name,args: {'success':True,'sql':args['sql'],'rows':self.execute(args['sql']).rows}
        agent=SingleDatabaseAgent(model,lambda _:client,SkillRegistry().get('database_query'))
        result=agent.prepare(QUESTION,'olist_ecommerce',{},'',{},self.payload_workspace(),{})
        self.assertEqual(client.call_tool.call_count,1)
        self.assertEqual([r['orders'] for r in result['execution']['rows']],[179,168])
        self.assertEqual(model.chat_json.call_count,3)

    def payload_workspace(self):
        p=self.payload();return {'analysis_filters':p['analysis_filters'],'business_intent':p['business_intent']}

    def test_scope_conflicts_do_not_intersect_into_false_zero(self):
        intent=intent_for(QUESTION)
        for predicate in ["customer_state='RJ'","customer_state IN ('RJ')","product_category_name_english='health_beauty'","product_category_name='beleza_saude'","order_status='canceled'"]:
            sql="SELECT strftime(order_purchase_timestamp,'%Y-%m') AS month,COUNT(*) AS n FROM olist_orders_dataset WHERE "+predicate+' GROUP BY month'
            self.assertIn('冲突',business_sql_error(sql,intent),predicate)

    def test_month_output_is_traced_not_claimed_by_model_or_unused_cte(self):
        intent=intent_for(QUESTION)
        valid=[FINANCE,"SELECT DATE_TRUNC('month',order_purchase_timestamp) AS month,COUNT(*) AS n FROM olist_orders_dataset GROUP BY 1",
               "WITH monthly AS (SELECT strftime(order_purchase_timestamp,'%Y-%m') AS month,COUNT(*) AS n FROM olist_orders_dataset GROUP BY month) SELECT month,n FROM monthly"]
        for sql in valid:self.assertEqual(business_sql_error(sql,intent),'',sql)
        for sql in ["SELECT 'month' AS month,COUNT(*) FROM olist_orders_dataset", "WITH unused AS (SELECT strftime(order_purchase_timestamp,'%Y-%m') AS month FROM olist_orders_dataset) SELECT COUNT(*) FROM olist_orders_dataset"]:
            self.assertIn('每月',business_sql_error(sql,intent))

    def test_requested_months_reject_narrower_date_conditions(self):
        intent=intent_for(QUESTION)
        sql="SELECT strftime(order_purchase_timestamp,'%Y-%m') AS month,COUNT(*) AS n FROM olist_orders_dataset WHERE order_purchase_timestamp>=TIMESTAMP '2018-08-01 00:00:00' GROUP BY month"
        self.assertIn('日期条件',business_sql_error(sql,intent))
        good=sql.replace('2018-08-01','2018-07-01')
        self.assertEqual(business_sql_error(good,intent),'')
        self.assertEqual(month_periods('继续只看8月',intent),['2018-08'])
        self.assertEqual(month_periods('2018-07-01至2018-08-15'),[])

    def test_malformed_json_is_retried_but_never_executes_unvalidated_plan(self):
        model=Mock();model.chat_json.side_effect=[RuntimeError('大模型未返回有效 JSON'),['wrong'],self.decision(FINANCE)]
        decision=self.agent(model)._validated_decision('',self.payload(),{'query_olist_ecommerce'})
        self.assertIn('__ask_scope_orders',decision['arguments']['sql'])
        self.assertEqual(model.chat_json.call_count,3)
        model=Mock();model.chat_json.side_effect=RuntimeError('大模型未返回有效 JSON')
        with self.assertRaisesRegex(PipelineStageError,'尚未执行'):self.agent(model)._validated_decision('',self.payload(),{'query_olist_ecommerce'})

    def test_state_sample_does_not_match_letters_inside_schema_name_or_duplicate_enforced_scope(self):
        payload={**self.payload(),'query':QUESTION+'，使用product_category_name_english。',
                 'retrieval':{'selected_fields':[{'field_name':'customer_state','samples':['AM','SP','RJ']},{'field_name':'order_status','samples':['delivered']}]}}
        self.assertEqual(SingleDatabaseAgent._explicit_filter_error(self.decision(FINANCE),payload),'')

    def test_exclusions_and_focus_are_not_turned_into_positive_scope(self):
        self.assertEqual(filters_for_query('2018年8月统计SP以外的已送达订单')['states'],[])
        self.assertEqual(filters_for_query('排除健康美容品类')['categories'],[])
        self.assertEqual(filters_for_query('电脑配件以外的品类')['categories'],[])
        self.assertEqual(filters_for_query('按州统计，重点关注SP')['states'],[])
        with self.assertRaisesRegex(ValueError,'排除条件'):filters_for_query('排除SP',{'states':['SP']})

    def test_longer_category_label_does_not_select_its_shorter_prefix(self):
        self.assertEqual(filters_for_query('只看电脑配件品类')['categories'],['computers_accessories'])
        self.assertEqual(set(filters_for_query('对比电脑和电脑配件品类')['categories']),{'computers','computers_accessories'})

    def test_compact_scope_reuses_cohort_and_does_not_bypass_money_guard(self):
        intent=intent_for(QUESTION)
        repeated=' UNION ALL '.join(['SELECT COUNT(order_id) AS n FROM olist_orders_dataset']*12)
        sql=apply_filters(repeated,intent['filters'],intent['periods'])
        self.assertLess(len(sql),4000)
        self.assertEqual([r['n'] for r in self.execute(sql).rows],[347]*12)
        with self.assertRaisesRegex(ValueError,'一对多'):
            apply_filters('SELECT SUM(i.price) AS amount FROM olist_order_items_dataset i JOIN olist_order_payments_dataset p USING(order_id)',intent['filters'])

    def test_only_verified_known_cohorts_qualify_for_confirmation(self):
        intent=intent_for(QUESTION);sql=apply_filters(FINANCE,intent['filters'],intent['periods'])
        saved=public_intent(intent,sql)
        self.assertTrue(saved['can_continue'])
        self.assertFalse(reusable_cohort(sql,intent))
        for unsafe in [sql.replace('FROM __ask_scope_olist_order_payments_dataset AS olist_order_payments_dataset','FROM __ask_scope_olist_order_payments_dataset AS olist_order_payments_dataset WHERE payment_value>100'),FINANCE+' HAVING SUM(i.item_sales)>100']:
            self.assertFalse(reusable_cohort(unsafe,saved))

    def test_aggregate_sample_filters_are_not_mistaken_for_order_cohort_predicates(self):
        intent=intent_for(QUESTION)
        sql="SELECT strftime(order_purchase_timestamp,'%Y-%m') AS month,COUNT(*) FILTER(WHERE order_delivered_carrier_date IS NULL) AS missing,AVG(epoch(order_delivered_customer_date-order_delivered_carrier_date)/86400.0) FILTER(WHERE order_delivered_customer_date>=order_delivered_carrier_date) AS avg_days FROM olist_orders_dataset GROUP BY month"
        scoped=apply_filters(sql,intent['filters'],intent['periods'])
        self.assertTrue(public_intent(intent,scoped)['can_continue'])
        restricted=sql.replace(' GROUP BY month',' WHERE order_delivered_customer_date IS NULL GROUP BY month')
        self.assertFalse(public_intent(intent,restricted)['can_continue'])

    def test_unrepresented_exclusions_and_literal_order_subsets_cannot_be_confirmed(self):
        intent=intent_for(QUESTION)
        for predicate in ["customer_state<>'RJ'","customer_state LIKE 'S%'","order_id='single-order'"]:
            sql=FINANCE.replace('GROUP BY month ORDER BY month','WHERE '+predicate+' GROUP BY month ORDER BY month')
            self.assertFalse(public_intent(intent,apply_filters(sql,intent['filters'],intent['periods']))['can_continue'])

    def test_empty_execution_has_inspectable_sql_without_model_absence_claim(self):
        model=Mock()
        result=ResponseGenerator(model).finalize('财务核对',SqlExecution('SELECT 1 WHERE FALSE',True,columns=['n'],total_rows=0),'','')
        self.assertTrue(result['valid']);self.assertIn('不能证明',result['analysis']);model.chat_json.assert_not_called()


class ComplexRecoveryApiTests(unittest.TestCase):
    setUp=base.MonthlyApiTests.setUp
    tearDown=base.MonthlyApiTests.tearDown
    login=base.MonthlyApiTests.login
    def seed(self,tid='previous',owner='demo_admin',session='complex'):
        intent=intent_for(QUESTION);sql=apply_filters(FINANCE,intent['filters'],intent['periods'])
        result=QueryResult(task_id=tid,status='completed',route='database_query',message='ok',sql=sql,columns=['month','orders'],rows=[{'month':'2018-08','orders':168}],query_intent=public_intent(intent,sql),analysis_scope={'filters':intent['filters']})
        self.service.context.remember(f'{owner}:{session}:olist_ecommerce',result,QUESTION,{'database_id':'olist_ecommerce'},owner)
        return result
    def test_confirmation_is_owned_current_and_does_not_trust_client_conditions(self):
        previous=self.seed()
        self.service.workflow.invoke.return_value=QueryResult(task_id='next',status='failed',route='database_query',message='test')
        data={'query':'继续只看8月，按商家拆解','session_id':'complex','workspace':{'database_id':'olist_ecommerce','continuation_task_id':previous.task_id}}
        response=self.client.post('/api/query',headers=self.admin,json=data)
        self.assertEqual(response.status_code,200,response.text)
        payload=self.service.workflow.invoke.call_args.args[0]['workspace']
        self.assertEqual(payload['scope_guard'],'');self.assertEqual(payload['business_intent']['periods'],['2018-08'])
        self.assertEqual(payload['business_intent']['filters']['states'],['SP'])
        data['session_id']='other'
        self.assertEqual(self.client.post('/api/query',headers=self.admin,json=data).status_code,403)
        self.seed('newer');data['session_id']='complex'
        self.assertEqual(self.client.post('/api/query',headers=self.admin,json=data).status_code,400)

    def test_old_wrong_results_are_flagged_and_excluded_without_rewriting_archive(self):
        for name,sql in [('empty',(FIXTURES/'finance_wrong_category.sql').read_text()),('wrong-scope',(FIXTURES/'finance_missing_scope.sql').read_text())]:
            result=QueryResult(task_id=name,status='completed',route='database_query',message='查询完成',sql=sql,analysis='旧模型原始说明',rows=[{'orders':894}])
            self.service.context.remember('demo_admin:legacy:olist_ecommerce',result,QUESTION,{'database_id':'olist_ecommerce'},'demo_admin')
        restored=SessionContext(ModelClient(self.config),self.config)
        for name in ['empty','wrong-scope']:
            result=restored.tasks[name]['result']
            self.assertEqual(result.status,'failed');self.assertEqual(result.query_validation['original_analysis'],'旧模型原始说明')
        self.assertEqual(restored.analysis_context('demo_admin:legacy:olist_ecommerce',{})[1],[])
        self.assertTrue(all(x['result']['status']=='completed' for x in restored.archive.load_turns()))

    def test_ambiguous_exclusion_of_existing_scope_is_recorded_without_running_sql(self):
        self.seed()
        data={'query':'排除SP州，继续统计订单','session_id':'complex','workspace':{'database_id':'olist_ecommerce'}}
        response=self.client.post('/api/query',headers=self.admin,json=data)
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertEqual(result['status'],'failed');self.assertIsNone(result['sql'])
        self.assertIn('排除条件',result['analysis']);self.service.workflow.invoke.assert_not_called()

if __name__=='__main__':unittest.main()
