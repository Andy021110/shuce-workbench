import csv
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from app.api import routes
from app.config import Settings, BASE_DIR
from app.main import app
from app.metric_registry import definitions, metric_query, guidance, add_metric_totals, get_definition
from app.result_profile import build_profile
from app.model_client import ModelClient
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController
from app.services.askdata_service import AskDataService
from app.services.session_context import SessionContext

class RegistryBaselineTests(unittest.TestCase):
    def setUp(self): self.snapshots=[]
    def tearDown(self):
        for sid in self.snapshots: (BASE_DIR/'data/result_snapshots'/f'{sid}.json').unlink(missing_ok=True)
    def run_metric(self,metric,**options):
        sql,contract=metric_query(metric,**options)
        result=DuckDbEngine().execute('olist_ecommerce',sql,AccessController().resolve('demo_admin'))
        self.assertTrue(result.success,result.error);self.snapshots.append(result.snapshot_id)
        return result,contract
    def test_all_canonical_metric_baselines(self):
        expected={'order_count':{'orders':99441},'item_sales':{'item_sales':13591643.7},'payments':{'payments':16008872.12}}
        for metric in expected:
            result,contract=self.run_metric(metric);self.assertEqual(result.rows[0],expected[metric]);self.assertEqual(contract['version'],'1.0')
        result,_=self.run_metric('delivery_late');self.assertEqual(result.rows[0]['late'],6534);self.assertEqual(result.rows[0]['delivered'],96470)
        result,_=self.run_metric('repeat_buyers');self.assertEqual(result.rows[0]['buyers'],96096);self.assertEqual(result.rows[0]['repeat_buyers'],2997)
    def test_inclusive_day_boundaries_and_daily_rollup_equal_raw_csv(self):
        path=BASE_DIR/'data/databases/olist_ecommerce/olist_orders_dataset.csv'
        with path.open() as f: baseline=Counter(r['order_purchase_timestamp'][:10] for r in csv.DictReader(f) if '2017-11-01'<=r['order_purchase_timestamp'][:10]<='2017-11-30')
        result,_=self.run_metric('order_count',start_date='2017-11-01',end_date='2017-11-30',grain='day')
        self.assertEqual({r['period']:r['orders'] for r in result.rows},dict(baseline))
        self.assertEqual(int(result.result_profile['numeric'][0]['sum']),sum(baseline.values()))
    def test_invalid_parameters_and_injection_are_rejected(self):
        for args in [dict(metric_id='unknown'),dict(metric_id='order_count',start_date='2018-01-01',end_date='2017-01-01'),dict(metric_id='repeat_buyers',grain='month'),dict(metric_id='order_count',start_date="2017-01-01'; DROP TABLE x")]:
            with self.subTest(args=args),self.assertRaises(ValueError):metric_query(**args)
    def test_standard_overall_rate_uses_summed_denominator_not_group_mean(self):
        profile=build_profile(['period','delivered','late','late_rate'],[{'period':'2017-01','delivered':100,'late':10,'late_rate':.1},{'period':'2017-02','delivered':1,'late':1,'late_rate':1.0}],total_rows=2)
        add_metric_totals(profile,get_definition('delivery_late'))
        self.assertEqual(profile['metric_totals']['values']['late_rate'],'0.108911')
        self.assertNotEqual(profile['metric_totals']['values']['late_rate'],next(r['mean'] for r in profile['numeric'] if r['field']=='late_rate'))
    def test_guidance_is_scoped_and_definitions_declare_critical_rules(self):
        self.assertEqual(guidance('short_video_ops'),'')
        text=guidance('olist_ecommerce');self.assertIn('customer_unique_id',text);self.assertIn('DATE',text);self.assertIn('BRL',text)
        self.assertEqual(len(definitions()),5)

class CanonicalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();config=replace(Settings(),session_archive_enabled=True,session_archive_path=str(Path(self.temp.name)/'archive.db'),short_term_summary_enabled=False)
        self.service=AskDataService.__new__(AskDataService);self.service.context=SessionContext(ModelClient(config),config)
        self.service.access_controller=AccessController();self.service.tasks=self.service.context.tasks;self.service.session_tasks=self.service.context.session_tasks;self.service.workflow=Mock()
        self.patcher=patch.object(routes,'service',self.service);self.patcher.start();self.client=TestClient(app)
        self.admin=self.login('admin','admin123');self.growth=self.login('growth','growth123')
    def tearDown(self):
        for task in self.service.tasks.values():
            if task['result'].snapshot_id:(BASE_DIR/'data/result_snapshots'/f"{task['result'].snapshot_id}.json").unlink(missing_ok=True)
        self.patcher.stop();self.temp.cleanup()
    def login(self,name,password):
        response=self.client.post('/api/auth/login',json={'username':name,'password':password});return {'Authorization':'Bearer '+response.json()['access_token']}
    def payload(self): return {'query':'标准指标','metric_id':'order_count','metric_parameters':{'start_date':'2017-01-01','end_date':'2017-12-31','grain':'day'},'session_id':'metric-session','workspace':{'database_id':'olist_ecommerce'}}
    def test_standard_query_persists_profile_and_contract_without_model_call(self):
        response=self.client.post('/api/query',headers=self.admin,json=self.payload());self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertEqual(result['metric_basis'],'canonical');self.assertEqual(result['total_rows'],361);self.assertEqual(result['result_profile']['analyzed_rows'],361)
        self.assertEqual(result['metric_contracts'][0]['parameters']['grain'],'day');self.service.workflow.invoke.assert_not_called()
        self.assertEqual(self.client.get('/api/sessions',headers=self.admin).json()[0]['turns'][0]['result']['metric_basis'],'canonical')
        self.assertEqual(self.client.get('/api/tasks/'+result['task_id'],headers=self.growth).status_code,403)
    def test_catalog_and_canonical_query_cannot_cross_source_or_role(self):
        self.assertEqual(self.client.get('/api/metrics?database_id=olist_ecommerce',headers=self.growth).status_code,403)
        self.assertEqual(self.client.get('/api/metrics?database_id=short_video_ops',headers=self.growth).json(),[])
        self.assertEqual(self.client.post('/api/query',headers=self.growth,json=self.payload()).status_code,403)
        payload=self.payload();payload['workspace']['database_id']='short_video_ops'
        self.assertEqual(self.client.post('/api/jobs',headers=self.admin,json=payload).status_code,400)
    def test_job_rejects_bad_dates_before_launch(self):
        payload=self.payload();payload['metric_parameters']['end_date']='not-date'
        self.assertEqual(self.client.post('/api/jobs',headers=self.admin,json=payload).status_code,400)

if __name__=='__main__':unittest.main()
