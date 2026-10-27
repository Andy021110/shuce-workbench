import csv, io, json, tempfile, unittest
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from app.config import Settings, BASE_DIR
from app.database import SCHEMA
from app.main import app
from app.api import routes
from app.models import QueryResult
from app.model_client import ModelClient
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController
from app.services.askdata_service import AskDataService
from app.services.session_context import SessionContext
from app.workbench import CsvInput, inspect_csv, import_csv, dashboard

class CsvValidationTests(unittest.TestCase):
    def test_invalid_csv_inputs_are_rejected(self):
        for content in ['', 'id,id\n1,2', 'id,amount\n1', 'id\n', 'id\n"unclosed', 'bad header\na', 'id\n\x00', 'id\n\ud800']:
            with self.subTest(content=content), self.assertRaises(ValueError): inspect_csv(CsvInput(name='orders', content=content))
        for name in ['../../escape','12orders','中文','a-b']:
            with self.assertRaises(ValueError): inspect_csv(CsvInput(name=name,content='id\n1'))
    def test_preview_validates_quoted_cells(self):
        result=inspect_csv(CsvInput(name='orders',content='id,note\n1,"a,b"\n2,"a\nline"'))
        self.assertEqual(result['row_count'],2);self.assertEqual(result['rows'][0]['note'],'a,b')
    def test_import_persists_fields_and_rejects_overwrite(self):
        import app.workbench as module
        with tempfile.TemporaryDirectory() as directory, patch.object(module,'ROOT',Path(directory)):
            before=len(SCHEMA)
            try:
                payload=CsvInput(name='test_orders',content='id,amount\n1,3.50',description='BRL historical',fields={'amount':'商品金额 BRL'})
                result=import_csv(payload)
                stored=json.loads((Path(directory)/result['database_id']/'_schema.json').read_text())
                self.assertEqual(stored['tables'][0]['fields'][1]['description'],'商品金额 BRL')
                self.assertIn(result['database_id'],AccessController().resolve('demo_admin').allowed_databases)
                self.assertNotIn(result['database_id'],AccessController().resolve('demo_growth_ops').allowed_databases)
                with self.assertRaises(ValueError): import_csv(payload)
            finally: del SCHEMA[before:]

class WorkbenchApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=replace(Settings(),session_archive_path=str(Path(self.temp.name)/'archive.db'),session_archive_enabled=True,short_term_summary_enabled=False)
        self.service=AskDataService.__new__(AskDataService);self.service.access_controller=AccessController()
        self.service.context=SessionContext(ModelClient(self.config),self.config)
        self.service.tasks=self.service.context.tasks;self.service.session_tasks=self.service.context.session_tasks
        self.service.workflow=Mock();self.service.config=self.config
        self.patcher=patch.object(routes,'service',self.service);self.patcher.start();self.client=TestClient(app)
        self.admin=self.login('admin','admin123');self.growth=self.login('growth','growth123');self.snapshot_ids=[]
    def tearDown(self):
        self.patcher.stop();self.temp.cleanup()
        for sid in self.snapshot_ids: (BASE_DIR/'data/result_snapshots'/f'{sid}.json').unlink(missing_ok=True)
    def login(self,name,password):
        token=self.client.post('/api/auth/login',json={'username':name,'password':password}).json()['access_token'];return {'Authorization':'Bearer '+token}
    def remember(self,owner,session,database,tid='test-result',execution=None):
        result=QueryResult(task_id=tid,status='completed',route='database_query',message='done',columns=execution.columns if execution else ['value'],rows=execution.rows if execution else [{'value':1}],total_rows=execution.total_rows if execution else 1,snapshot_id=execution.snapshot_id if execution else None,truncated=execution.truncated if execution else False)
        self.service.context.remember(f'{owner}:{session}:{database}',result,'测试查询',{'database_id':database},owner)
    def test_history_restores_owner_and_blocks_other_users(self):
        self.remember('demo_admin','same','olist_ecommerce');self.remember('demo_growth_ops','same','short_video_ops','growth-task')
        restored=SessionContext(ModelClient(self.config),self.config)
        self.service.context=restored;self.service.tasks=restored.tasks;self.service.session_tasks=restored.session_tasks
        self.assertEqual(self.service.tasks['test-result']['user_id'],'demo_admin')
        self.assertEqual(len(self.client.get('/api/sessions',headers=self.admin).json()),1)
        self.assertEqual(self.client.get('/api/sessions',headers=self.growth).json()[0]['workspace']['database_id'],'short_video_ops')
        for suffix in ['', '/rows', '/export']:
            self.assertEqual(self.client.get('/api/tasks/test-result'+suffix,headers=self.growth).status_code,403)
        self.client.delete('/api/sessions/same',headers=self.growth)
        self.assertEqual(len(self.client.get('/api/sessions',headers=self.admin).json()),1);self.assertEqual(self.client.get('/api/sessions',headers=self.growth).json(),[])
    def test_restarted_clarification_returns_actionable_error(self):
        self.remember('demo_admin','paused','olist_ecommerce')
        self.service.tasks['test-result']['restored']=True
        response=self.client.post('/api/tasks/test-result/clarify',headers=self.admin,json={'option_id':'one'})
        self.assertEqual(response.status_code,400);self.assertIn('服务已重启',response.json()['detail'])
    def test_backend_blocks_cross_source_and_cross_user_reference(self):
        self.remember('demo_admin','a','olist_ecommerce')
        for owner in ['demo_admin','demo_growth_ops']:
            with self.assertRaises(PermissionError): self.service.submit('分析','b',{'database_id':'short_video_ops','analysis_table_ids':['test-result'],'analysis_tables':[{'task_id':'test-result','rows':[{'forged':100}]}]},owner)
        self.service.workflow.invoke.assert_not_called()
    def test_csv_dashboard_and_jobs_enforce_permissions(self):
        self.assertEqual(self.client.post('/api/datasets/preview',headers=self.growth,json={'name':'x','content':'id\n1'}).status_code,403)
        self.assertEqual(self.client.post('/api/datasets/preview',headers=self.admin,json={'name':'x','content':'id,id\n1,2'}).status_code,400)
        self.assertEqual(self.client.get('/api/dashboard?database_id=olist_ecommerce',headers=self.growth).status_code,403)
        self.assertEqual(self.client.post('/api/jobs',headers=self.growth,json={'query':'订单','workspace':{'database_id':'olist_ecommerce'}}).status_code,403)
    def test_full_result_pagination_sort_filter_export_are_consistent(self):
        execution=DuckDbEngine().execute('olist_ecommerce','SELECT order_id, order_status FROM olist_orders_dataset ORDER BY order_id LIMIT 505',AccessController().resolve('demo_admin'))
        self.assertTrue(execution.success,execution.error);self.snapshot_ids.append(execution.snapshot_id)
        self.assertEqual(len(execution.rows),200);self.assertEqual(execution.total_rows,505);self.assertTrue(execution.truncated)
        self.remember('demo_admin','pagination','olist_ecommerce',execution=execution)
        collected=[]
        for page in range(1,4):
            body=self.client.get(f'/api/tasks/test-result/rows?page={page}&size=200',headers=self.admin).json();self.assertEqual(body['total_rows'],505);collected.extend(body['rows'])
        from urllib.parse import unquote
        download=self.client.get('/api/tasks/test-result/export',headers=self.admin)
        self.assertEqual(download.status_code,200)
        disposition=download.headers['content-disposition']
        self.assertIn('filename="shuce-test-result.csv"',disposition)
        self.assertEqual(unquote(disposition.split("filename*=UTF-8''",1)[1]),'数策-test-result.csv')
        def export(query=''):
            return list(csv.DictReader(io.StringIO(self.client.get('/api/tasks/test-result/export'+query,headers=self.admin).content.decode('utf-8-sig'))))
        self.assertEqual(export(),collected);self.assertEqual(len(collected),505)
        query='?sort=order_id&direction=desc&filter_text=delivered'
        rows=self.client.get('/api/tasks/test-result/rows'+query+'&size=200',headers=self.admin).json();filtered=export(query)
        self.assertEqual(rows['rows'],filtered[:200]);self.assertEqual(rows['total_rows'],len(filtered));self.assertEqual(filtered,sorted(filtered,key=lambda r:r['order_id'],reverse=True))
        self.assertEqual(self.client.get('/api/tasks/test-result/rows?sort=invalid',headers=self.admin).status_code,400)
    def test_valid_reference_uses_server_rows_instead_of_client_payload(self):
        self.remember('demo_admin','a','olist_ecommerce')
        self.service.workflow.invoke.return_value=QueryResult(task_id='reply',status='completed',route='direct_response',message='ok')
        self.service.submit('解释','b',{'database_id':'olist_ecommerce','analysis_table_ids':['test-result'],'analysis_tables':[{'task_id':'test-result','rows':[{'forged_value':99999}]}]},'demo_admin')
        payload=self.service.workflow.invoke.call_args.args[0]
        self.assertNotIn('forged_value',payload['analysis_context']);self.assertIn('"value": 1',payload['analysis_context'])
    def test_jobs_are_owned_and_duplicate_running_requests_reuse_job(self):
        import threading, time
        from app.progress import stage
        from app.telemetry import begin_call, finish_call, observe_task
        started=threading.Event();release=threading.Event()
        @observe_task
        def run(*args):
            stage('preprocess')
            call=begin_call('model',capability='chat',provider='example.com',model='demo',attempt=1)
            started.set();release.wait(3)
            finish_call(call,time.monotonic(),True)
            return QueryResult(task_id='job-result',status='completed',route='direct_response',message='ok')
        with patch.object(self.service,'submit',side_effect=run):
            try:
                request={'query':'测试阶段','session_id':'job-session','workspace':{'database_id':'olist_ecommerce'}}
                first=self.client.post('/api/jobs',headers=self.admin,json=request).json();self.assertTrue(started.wait(2))
                second=self.client.post('/api/jobs',headers=self.admin,json=request).json();self.assertEqual(first['id'],second['id'])
                self.assertEqual(self.client.get('/api/jobs/'+first['id'],headers=self.growth).status_code,404)
                self.assertEqual(self.client.delete('/api/sessions/job-session',headers=self.admin).status_code,409)
                running=self.client.get('/api/jobs/'+first['id'],headers=self.admin).json()
                self.assertEqual(running['stage'],'preprocess')
                self.assertEqual(len(running['execution_trace']['model_calls']),1)
                self.assertNotIn('success',running['execution_trace']['model_calls'][0])
            finally: release.set()
            for _ in range(100):
                if routes.JOBS[first['id']]['status']!='running': break
                time.sleep(.01)
            completed=routes.JOBS.pop(first['id'])
            self.assertEqual(completed['status'],'completed')
            self.assertTrue(completed['execution_trace']['model_calls'][0]['success'])

    def test_job_exception_keeps_safe_call_failure_for_owner(self):
        import time
        from app.telemetry import begin_call, finish_call, observe_task
        @observe_task
        def run(*args):
            call=begin_call('model',capability='chat',provider='example.com',model='demo',attempt=1)
            finish_call(call,time.monotonic(),False,error='HTTP 503')
            raise RuntimeError('private implementation error')
        with patch.object(self.service,'submit',side_effect=run):
            job=self.client.post('/api/jobs',headers=self.admin,json={'query':'测试请求失败','session_id':'trace-failure','workspace':{'database_id':'olist_ecommerce'}}).json()
            try:
                for _ in range(100):
                    if routes.JOBS[job['id']]['status']!='running':break
                    time.sleep(.01)
                failed=self.client.get('/api/jobs/'+job['id'],headers=self.admin).json()
                self.assertEqual(failed['status'],'failed')
                self.assertEqual(failed['execution_trace']['model_calls'][0]['error'],'HTTP 503')
                self.assertNotIn('private implementation error',json.dumps(failed))
                self.assertEqual(self.client.get('/api/jobs/'+job['id'],headers=self.growth).status_code,404)
            finally:routes.JOBS.pop(job['id'],None)
    def test_failed_job_can_be_retried_as_new_attempt(self):
        import time
        request={'query':'需要重试','session_id':'retry-session','workspace':{'database_id':'olist_ecommerce'}}
        def wait(job):
            for _ in range(100):
                if routes.JOBS[job['id']]['status']!='running': return routes.JOBS[job['id']]
                time.sleep(.01)
            self.fail('job did not finish')
        failed=QueryResult(task_id='failed-task',status='failed',route='database_query',message='测试模型失败')
        success=QueryResult(task_id='retry-task',status='completed',route='database_query',message='ok')
        with patch.object(self.service,'submit',side_effect=[failed,success]):
            first=self.client.post('/api/jobs',headers=self.admin,json=request).json();self.assertEqual(wait(first)['status'],'failed')
            second=self.client.post('/api/jobs',headers=self.admin,json=request).json();self.assertEqual(wait(second)['status'],'completed')
            self.assertNotEqual(first['id'],second['id'])
            routes.JOBS.pop(first['id']);routes.JOBS.pop(second['id'])
    def test_sql_cannot_read_external_files(self):
        for sql in ["SELECT content FROM read_text('/etc/passwd')", "SELECT order_id FROM olist_orders_dataset", "COPY (SELECT 1) TO '/tmp/no'"]:
            result=DuckDbEngine().execute('short_video_ops',sql,AccessController().resolve('demo_growth_ops'));self.assertFalse(result.success,sql)

class OlistDashboardBaselineTests(unittest.TestCase):
    def test_metrics_match_independent_raw_csv(self):
        root=BASE_DIR/'data/databases/olist_ecommerce'
        def read(name):
            with (root/(name+'.csv')).open() as stream: return list(csv.DictReader(stream))
        orders=read('olist_orders_dataset');items=read('olist_order_items_dataset');payments=read('olist_order_payments_dataset');customers=read('olist_customers_dataset')
        data=dashboard(AccessController().resolve('demo_admin'),'olist_ecommerce');m=data['metrics']
        self.assertEqual(m['orders'],len({o['order_id'] for o in orders}));self.assertEqual(Decimal(str(m['item_sales'])),sum(Decimal(i['price']) for i in items));self.assertEqual(Decimal(str(m['payments'])),sum(Decimal(i['payment_value']) for i in payments))
        delivered=[o for o in orders if o['order_status']=='delivered' and o['order_delivered_customer_date'] and o['order_estimated_delivery_date']]
        self.assertEqual(m['delivered'],len(delivered));self.assertEqual(m['late'],sum(o['order_delivered_customer_date'][:10]>o['order_estimated_delivery_date'][:10] for o in delivered))
        self.assertEqual({r['month']:r['orders'] for r in data['monthly'] if r['orders']},dict(Counter(o['order_purchase_timestamp'][:7] for o in orders)))
        mapping={c['customer_id']:c['customer_unique_id'] for c in customers};buyers=Counter(mapping[o['customer_id']] for o in orders)
        self.assertEqual(m['repeat_buyers'],sum(n>1 for n in buyers.values()));self.assertEqual(m['buyers'],len(buyers))

if __name__=='__main__': unittest.main()
