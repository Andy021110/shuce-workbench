import json
import unittest
from datetime import date, timedelta
from unittest.mock import Mock
from app.result_profile import build_profile, chart_source, ensure_profile
from app.models import QueryResult
from app.querying.models import SqlExecution
from app.querying.response_generator import ResponseGenerator
from app.querying.data_qa_agent import DataQaAgent

class CompleteProfileTests(unittest.TestCase):
    def test_extreme_after_preview_and_month_totals_cover_all_rows(self):
        rows=[{'order_date':str(date(2017,1,1)+timedelta(days=i)),'orders':i+1} for i in range(300)]
        profile=build_profile(['order_date','orders'],rows,total_rows=300)
        self.assertEqual(profile['coverage'],'full_result');self.assertEqual(profile['analyzed_rows'],300)
        stat=profile['numeric'][0]
        self.assertEqual(stat['sum'],'45150');self.assertEqual(stat['max_rows'],[{'order_date':'2017-10-27','orders':300}])
        self.assertEqual(sum(int(r['orders_sum']) for r in profile['temporal']['buckets']),45150)
        derived=chart_source('task','Daily',profile)
        self.assertEqual(derived['task_id'],'task:summary:time');self.assertEqual(derived['coverage'],'full_result_summary')
        self.assertEqual(len(derived['rows']),10)
    def test_rates_are_not_summed_or_treated_as_weighted_rates(self):
        profile=build_profile(['day','late_rate'],[{'day':'2017-01-01','late_rate':.1},{'day':'2017-01-02','late_rate':.9}],total_rows=2)
        self.assertIsNone(profile['numeric'][0]['sum']);self.assertEqual(profile['numeric'][0]['mean'],'0.5')
        self.assertEqual(profile['temporal']['buckets'][0]['late_rate_mean'],'0.5')
        self.assertIn('非加权',profile['aggregation_note'])
    def test_missing_dates_are_not_filled_with_zero_and_nulls_remain_visible(self):
        profile=build_profile(['day','orders'],[{'day':'2017-01-01','orders':1},{'day':'2017-03-01','orders':3},{'day':None,'orders':None}],total_rows=3)
        self.assertEqual([r['period'] for r in profile['temporal']['buckets']],['2017-01','2017-03'])
        self.assertEqual(profile['temporal']['missing_dates'],1);self.assertEqual(profile['numeric'][0]['missing'],1)
        source=chart_source('missing','Missing',profile);self.assertEqual(source['covered_rows'],2);self.assertEqual(source['excluded_rows'],1);self.assertEqual(source['input_rows'],3)
        self.assertEqual(profile['temporal']['buckets'][1]['orders_sum_change_rate'],'2')
    def test_declared_total_larger_than_available_rows_cannot_claim_full_coverage(self):
        profile=build_profile(['n'],[{'n':1}],total_rows=100)
        self.assertEqual(profile['coverage'],'preview_only')
    def test_legacy_preview_and_missing_snapshot_cannot_claim_full_coverage(self):
        result=QueryResult(task_id='old',status='completed',route='database_query',message='ok',columns=['n'],rows=[{'n':5}])
        self.assertEqual(ensure_profile(result)['coverage'],'preview_only')
        result.result_profile=None;result.snapshot_id='0'*32
        self.assertEqual(ensure_profile(result)['coverage'],'snapshot_unavailable')
        self.assertIsNone(chart_source('old','Old',result.result_profile))
    def test_empty_results_and_column_bounds_are_explicit(self):
        profile=build_profile([f'v{i}' for i in range(20)],[],total_rows=0)
        self.assertEqual(profile['analyzed_rows'],0);self.assertEqual(len(profile['omitted_columns']),4)
        self.assertEqual(profile['numeric'],[])
    def test_high_cardinality_ids_do_not_produce_fake_distribution_or_average(self):
        profile=build_profile(['id','customer_id','state'],[{'id':i,'customer_id':f'customer{i}','state':'SP'} for i in range(300)],total_rows=300)
        self.assertEqual(profile['numeric'],[])
        self.assertEqual([c['field'] for c in profile['categories']],['state'])
        self.assertEqual(profile['categories'][0]['top'][0]['rows'],300)
    def test_result_explanation_receives_full_profile_beyond_row_sample(self):
        client=Mock();client.chat_json.return_value={'valid':True,'title':'ok','analysis':'full'}
        profile=build_profile(['orders'],[{'orders':i} for i in range(300)],total_rows=300)
        execution=SqlExecution('SELECT n',True,['orders'],[{'orders':i} for i in range(200)],total_rows=300,result_profile=profile)
        ResponseGenerator(client).finalize('总体如何',execution,'','')
        system,payload=client.chat_json.call_args.args
        self.assertIn('full_result',system);self.assertIn('44850',payload);self.assertIn('完整结果统计',payload)
    def test_unsupported_marketing_attribution_is_rejected_but_limitations_are_allowed(self):
        self.assertTrue(DataQaAgent._unsupported_causal_claim({'markdown':'11月受促销节点拉动，创全年峰值。'}))
        self.assertTrue(DataQaAgent._unsupported_causal_claim({'answer':'这说明订单高度集中于促销日。'}))
        self.assertEqual(DataQaAgent._unsupported_causal_claim({'answer':'11月订单更多，但没有促销字段，无法确认原因。'}),'')
    def test_qa_repairs_unsupported_claim_once_before_returning(self):
        client=Mock();client.list_tools.return_value=[]
        model=Mock();model.chat_json.side_effect=[{'action':'answer','answer':'11月受促销拉动。'},{'action':'answer','answer':'11月订单更多，原因无法由统计确定。'}]
        skill=Mock(instructions='规则',allowed_tools=[],output_actions=['answer'])
        agent=DataQaAgent(model,lambda _:client,skill)
        result=agent.run('解释',{},{});self.assertIn('原因无法',result.answer);self.assertEqual(model.chat_json.call_count,2)
        self.assertIn('输出校验失败',model.chat_json.call_args.args[1])
    def test_qa_chart_catalog_accepts_only_server_derived_sources(self):
        profile=build_profile(['day','orders'],[{'day':'2017-01-01','orders':10},{'day':'2017-02-01','orders':20}],total_rows=2)
        derived=chart_source('owned','Owned',profile)
        catalog=DataQaAgent._source_catalog({'recent_result':json.dumps({'task_id':'owned','columns':['day','orders'],'result_profile':profile,'summary_sources':[derived]})})
        DataQaAgent._validate_chart_source({'source_task_id':'owned:summary:time','category_field':'period','value_field':'orders_sum'},catalog)
        with self.assertRaises(ValueError): DataQaAgent._validate_chart_source({'source_task_id':'other:summary:time','category_field':'period','value_field':'orders_sum'},catalog)

if __name__=='__main__': unittest.main()
