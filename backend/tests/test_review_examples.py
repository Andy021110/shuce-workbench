import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import BASE_DIR
from app.querying.duckdb_engine import DuckDbEngine
from app.review_examples import example_detail, example_catalog
from app.security import AccessController
from tests import test_business_tasks as base


class ExampleDataTests(unittest.TestCase):
    def setUp(self): self.scope=AccessController().resolve('demo_admin')
    def test_full_example_real_totals_and_reconciliations(self):
        data=example_detail(self.scope,'monthly-review')
        overview=data['results'][0]['rows']
        self.assertEqual([r['orders'] for r in overview],[6292,6512])
        self.assertEqual([r['item_sales'] for r in overview],[895507.22,854686.33])
        self.assertEqual([r['row_count'] for r in data['results']],[2,2,69,27])
        self.assertTrue(all(c['passed'] for c in data['reconciliations']))
        self.assertTrue(data['report']['fact_references'])
        self.assertNotIn('{{fact:',data['report']['markdown'])
        states=data['results'][3]['rows']
        self.assertEqual(sum(r['new_late_orders'] for r in states),185)
        self.assertEqual(data['source']['currency'],'BRL')
    def test_scoped_example_keeps_same_cohort_and_numerical_provenance(self):
        data=example_detail(self.scope,'sp-watches')
        overview=data['results'][0]['rows']
        self.assertEqual([r['orders'] for r in overview],[179,168])
        self.assertEqual([r['item_sales'] for r in overview],[29398.09,26245.44])
        self.assertEqual([r['late'] for r in overview],[5,18])
        self.assertEqual(data['results'][2]['rows'][0]['category'],'watches_gifts')
        self.assertEqual(data['results'][3]['rows'][0]['customer_state'],'SP')
        self.assertEqual(data['results'][2]['rows'][0]['sales_change'],-3152.65)
        self.assertTrue(all(r['analysis_scope']['filters']==data['filters'] for r in data['results']))
        self.assertIn('手表与礼品',data['report']['markdown'])
    def test_catalog_and_access_never_mutate_or_leak_results(self):
        catalog=example_catalog(self.scope)
        self.assertEqual(len(catalog),3)
        catalog[1]['filters']['states'].append('RJ')
        self.assertEqual(example_catalog(self.scope)[1]['filters']['states'],['SP'])
        with patch('app.review_examples.DuckDbEngine.execute') as execute:
            with self.assertRaises(PermissionError):example_detail(AccessController().resolve('demo_growth_ops'),'sp-watches')
            with self.assertRaises(KeyError):example_detail(self.scope,'missing')
            execute.assert_not_called()
        limited=replace(self.scope,allowed_tables=frozenset({'olist_ecommerce.olist_orders_dataset'}))
        with self.assertRaisesRegex(ValueError,'场景数据计算失败'):example_detail(limited,'monthly-review')
    def test_failed_second_step_cleans_preview_snapshot(self):
        from app.monthly_review import review_query
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);snapshots=root/'data/result_snapshots';snapshots.mkdir(parents=True)
            saved=snapshots/'preview.json';saved.write_text('{}')
            execution=SimpleNamespace(success=True,sql=review_query('2018-08')[0],snapshot_id='preview',columns=['period','orders','item_sales','delivered','late','late_rate'],rows=[{'period':'2018-07','orders':1,'item_sales':10,'delivered':1,'late':0,'late_rate':0},{'period':'2018-08','orders':2,'item_sales':20,'delivered':2,'late':0,'late_rate':0}],total_rows=2,result_profile=None)
            failure=SimpleNamespace(success=False,error='不可计算',snapshot_id=None)
            with patch('app.review_examples.BASE_DIR',root),patch('app.review_examples.DuckDbEngine.execute',side_effect=[execution,failure]):
                with self.assertRaisesRegex(ValueError,'不可计算'):example_detail(self.scope,'monthly-review')
            self.assertFalse(saved.exists())


class ExampleApiTests(unittest.TestCase):
    setUp=base.MonthlyApiTests.setUp
    tearDown=base.MonthlyApiTests.tearDown
    login=base.MonthlyApiTests.login
    def test_browsing_never_creates_analysis_or_persistent_preview_snapshots(self):
        ids=[];original=DuckDbEngine.execute
        def record(engine,*args,**kwargs):
            result=original(engine,*args,**kwargs)
            if result.snapshot_id: ids.append(result.snapshot_id)
            return result
        with patch('app.review_examples.DuckDbEngine.execute',autospec=True,side_effect=record):
            response=self.client.get('/api/review/examples/sp-watches',headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(ids),4)
        self.assertFalse(any((BASE_DIR/'data/result_snapshots'/f'{sid}.json').exists() for sid in ids))
        self.assertEqual(self.client.get('/api/sessions',headers=self.admin).json(),[])
        self.assertEqual(self.service.context.archive.load_turns(),[])
        self.service.workflow.invoke.assert_not_called()
        result=self.client.post('/api/query',headers=self.admin,json={'query':'月度复盘','review_month':'2018-08','review_filters':response.json()['filters'],'session_id':'my-example','workspace':{'database_id':'olist_ecommerce'}})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['rows'],response.json()['results'][0]['rows'])
        self.assertFalse(result.json()['task_id'].startswith('example_'))
        self.assertEqual(len(self.client.get('/api/sessions',headers=self.admin).json()),1)
    def test_auth_roles_unknown_scene_and_catalog(self):
        for path in ('/api/review/examples','/api/review/examples/sp-watches'):
            self.assertEqual(self.client.get(path).status_code,401)
            self.assertEqual(self.client.get(path,headers=self.growth).status_code,403)
        self.assertEqual(self.client.get('/api/review/examples/missing',headers=self.admin).status_code,404)
        items=self.client.get('/api/review/examples',headers=self.admin).json()
        self.assertEqual([i['id'] for i in items],['monthly-review','sp-watches','delivery-investigation'])
        self.assertFalse(any('results' in i for i in items))
