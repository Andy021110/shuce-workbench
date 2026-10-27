"""Goal direction, persistence, evidence and driver reconciliation."""
import json
import unittest
from decimal import Decimal
from app.monthly_review import target_comparison
from app.models import QueryResult
from app.api import routes
from unittest.mock import patch
from app.report_facts import build_facts, factual_brief, validate_and_render
from app.result_profile import build_profile
from app.services.session_context import SessionContext
from app.model_client import ModelClient
from tests import test_business_tasks as base

class GoalMathTests(unittest.TestCase):
    def test_direction_completion_and_percentage_points(self):
        rows=target_comparison([{'orders':6512,'item_sales':854686.33,'late_rate':393/6351}],{'orders':6500,'item_sales':'900000','late_rate':'5'})
        self.assertEqual([r['status'] for r in rows],['已达标','未达标','未达标'])
        self.assertEqual(rows[0]['gap'],'12')
        self.assertEqual(Decimal(rows[1]['gap']),Decimal('-45313.67'))
        self.assertAlmostEqual(float(rows[2]['gap'])*100,1.188001889,places=7)
        self.assertIsNone(rows[2]['achievement_rate'])
    def test_unset_zero_and_null_are_distinct(self):
        self.assertEqual(target_comparison([{'orders':0}],{}),[])
        rows=target_comparison([{'orders':0,'late_rate':None}],{'orders':0,'late_rate':0})
        self.assertEqual(rows[0]['status'],'已达标');self.assertIsNone(rows[0]['achievement_rate'])
        self.assertEqual(rows[1]['status'],'无法判断');self.assertIsNone(rows[1]['gap'])
    def test_target_numbers_are_citable_and_exported(self):
        rows=[{'period':'2018-07','orders':6292},{'period':'2018-08','orders':6512}]
        item={'task_id':'owned','title':'月度复盘','columns':['period','orders'],'rows':rows,'result_profile':build_profile(['period','orders'],rows),'review_context':{'target_comparison':target_comparison(rows,{'orders':6500})}}
        context={'selected_tables':json.dumps([item])};facts=build_facts(context)
        self.assertEqual(facts['s0_target0_gap']['value'],'12')
        output=validate_and_render({'answer':'订单超过目标{{fact:s0_target0_gap}}笔。'},facts,[])
        self.assertIn('12笔',output['answer']);self.assertEqual(output['fact_references'][0]['source_task_id'],'owned')
        brief,refs=factual_brief(context,facts)
        self.assertIn('本月目标对比',brief);self.assertIn('6,500',brief);self.assertIn('已达标',brief)

class DriverTests(base.MonthlyTaskTests):
    def test_reconciliation_identity_and_nonadditive_means(self):
        overview=self.execute('overview');drivers=self.execute('sales_drivers')
        for a,b in zip(overview,drivers):
            self.assertEqual(a['orders'],b['orders']);self.assertEqual(a['item_sales'],b['item_sales'])
            self.assertLessEqual(b['orders_with_items'],b['orders'])
            self.assertAlmostEqual(b['sales_per_order'],b['items_per_order']*b['avg_item_price'],places=9)
            self.assertAlmostEqual(b['sales_per_order']*b['orders'],b['item_sales'],places=7)
        profile=build_profile(list(drivers[0]),drivers)
        for field in ('sales_per_order','items_per_order','avg_item_price'):
            self.assertIsNone(next(s for s in profile['numeric'] if s['field']==field)['sum'])
        self.assertGreater(drivers[1]['orders'],drivers[0]['orders']);self.assertLess(drivers[1]['sales_per_order'],drivers[0]['sales_per_order'])

class GoalApiTests(base.MonthlyApiTests):
    def test_targets_persist_with_snapshot_and_report_context(self):
        payload=self.payload();payload['review_targets']={'orders':6500,'item_sales':900000,'late_rate':5}
        response=self.client.post('/api/query',json=payload,headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        comparisons=response.json()['review_context']['target_comparison']
        self.assertEqual([r['status'] for r in comparisons],['已达标','未达标','未达标'])
        restored=SessionContext(ModelClient(self.config),self.config)
        result=restored.latest_table_result('demo_admin:monthly:olist_ecommerce')
        self.assertEqual(result.review_context['target_comparison'],comparisons)
        self.assertEqual(restored.result_context(result)['review_context']['targets']['orders'],6500)
        self.assertEqual(self.client.get('/api/tasks/'+result.task_id,headers=self.growth).status_code,403)
    def test_invalid_and_misplaced_targets_rejected_before_query_or_job(self):
        for targets in ({'orders':-1},{'orders':1.5},{'orders':True},{'item_sales':'NaN'},{'item_sales':'1.001'},{'late_rate':101},{'late_rate':'Infinity'},{'unknown':3}):
            for route in ('query','jobs'):
                payload=self.payload();payload['review_targets']=targets
                with self.subTest(targets=targets,route=route):self.assertEqual(self.client.post('/api/'+route,json=payload,headers=self.admin).status_code,422)
        for route in ('query','jobs'):
            payload=self.payload();payload.update(review_step='sales_drivers',review_targets={'orders':1})
            self.assertEqual(self.client.post('/api/'+route,json=payload,headers=self.admin).status_code,400)
            payload.pop('review_month');payload['review_step']='overview'
            self.assertEqual(self.client.post('/api/'+route,json=payload,headers=self.admin).status_code,400)
    def test_repeated_drilldowns_keep_overview_goal_source(self):
        session='demo_admin:monthly:olist_ecommerce'
        for i in range(6):
            result=QueryResult(task_id=f'pin{i}',status='completed',route='database_query',message='ok',
                rows=[{'orders':1}],columns=['orders'],review_context={'month':'2018-08','step':'overview' if i==0 else 'sales_drivers','targets':{'orders':2} if i==0 else {}} if i in (0,5) else None)
            self.service.context.remember(session,result,'query',{'database_id':'olist_ecommerce'},'demo_admin')
        context,sources=self.service.context.analysis_context(session,{})
        items=json.loads(context)
        self.assertEqual(len(items),5);self.assertEqual(items[0]['task_id'],'pin0')
        self.assertEqual(items[0]['review_context']['targets'],{'orders':2})
    def test_repeated_monthly_step_uses_latest_snapshot_once(self):
        session='demo_admin:monthly:olist_ecommerce'
        for i in range(3):
            result=QueryResult(task_id=f'dedup{i}',status='completed',route='database_query',message='ok',rows=[{'orders':i}],columns=['orders'],review_context={'month':'2018-08','step':'sales_drivers'})
            self.service.context.remember(session,result,'query',{'database_id':'olist_ecommerce'},'demo_admin')
        context,sources=self.service.context.analysis_context(session,{})
        self.assertEqual([s['task_id'] for s in sources],['dedup2'])
    def test_inflight_job_cannot_silently_reuse_different_targets(self):
        payload=self.payload();payload['review_targets']={'orders':6500}
        job_id=None
        try:
            with patch.object(routes.EXECUTOR,'submit'):
                first=self.client.post('/api/jobs',json=payload,headers=self.admin)
                self.assertEqual(first.status_code,200,first.text);job_id=first.json()['id']
                same=self.client.post('/api/jobs',json=payload,headers=self.admin)
                self.assertEqual(same.json()['id'],job_id)
                payload['review_targets']={'orders':7000}
                self.assertEqual(self.client.post('/api/jobs',json=payload,headers=self.admin).status_code,409)
        finally:
            if job_id:routes.JOBS.pop(job_id,None)
    def test_empty_month_has_null_means_and_unknown_delivery_goal(self):
        payload=self.payload();payload.update(review_month='2016-11',review_targets={'late_rate':0})
        result=self.client.post('/api/query',json=payload,headers=self.admin).json()
        self.assertEqual(result['review_context']['target_comparison'][0]['status'],'无法判断')
        payload.pop('review_targets');payload['review_step']='sales_drivers'
        result=self.client.post('/api/query',json=payload,headers=self.admin).json()
        self.assertEqual(result['rows'][1]['orders'],0)
        self.assertIsNone(result['rows'][1]['sales_per_order'])
        self.assertIsNone(result['rows'][1]['avg_item_price'])
    def test_drivers_are_canonical_and_do_not_invent_targets(self):
        payload=self.payload();payload['review_step']='sales_drivers'
        response=self.client.post('/api/query',json=payload,headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertEqual(result['status'],'completed');self.assertTrue(result['analysis_basis']['verified'])
        self.assertNotIn('target_comparison',result['review_context']);self.service.workflow.invoke.assert_not_called()

if __name__=='__main__':unittest.main()
