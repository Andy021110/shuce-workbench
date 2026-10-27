"""Public deployment must fail closed without explicit credentials and scope."""
import os
import unittest
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from app.main import app
from app.security import AuthService, AccessController
from app.api import routes


class PublicDeploymentTests(unittest.TestCase):
    def test_local_demo_still_works_and_unconfigured_analyst_is_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            auth=AuthService()
            self.assertIsNotNone(auth.login('admin','admin123'))
            self.assertIsNone(auth.login('analyst',''))

    def test_public_mode_disables_all_builtin_passwords(self):
        with patch.dict(os.environ, {'SHUCE_PUBLIC_DEPLOYMENT':'true'}, clear=True):
            auth=AuthService()
            for name,password in [('admin','admin123'),('growth','growth123'),('channel','channel123'),('content','content123')]:
                self.assertIsNone(auth.login(name,password))

    def test_weak_or_missing_override_never_enables_public_account(self):
        for password in ['', 'short', 'admin123']:
            with patch.dict(os.environ, {'SHUCE_PUBLIC_DEPLOYMENT':'true','SHUCE_PASSWORD_ADMIN':password}, clear=True):
                self.assertIsNone(AuthService().login('admin',password))

    def test_explicit_strong_account_and_olist_only_scope(self):
        with patch.dict(os.environ, {'SHUCE_PUBLIC_DEPLOYMENT':'true','SHUCE_PASSWORD_ANALYST':'example-only-strong-test-password'}, clear=True):
            auth=AuthService()
            token,user=auth.login('analyst','example-only-strong-test-password')
            self.assertEqual(auth.authenticate('Bearer '+token),user)
            self.assertEqual(user.role,'olist_analyst')
            scope=AccessController().resolve(user.user_id)
            self.assertEqual(scope.allowed_databases,frozenset({'olist_ecommerce'}))
            self.assertEqual(len(scope.allowed_tables),9)
            self.assertIsNone(auth.login('analyst','wrong-password'))

    def test_online_analyst_cannot_import_or_rebuild_index(self):
        with patch.dict(os.environ, {'SHUCE_PUBLIC_DEPLOYMENT':'true','SHUCE_PASSWORD_ANALYST':'example-only-strong-test-password'}, clear=True):
            with TestClient(app) as client:
                response=client.post('/api/auth/login',json={'username':'analyst','password':'example-only-strong-test-password'})
                headers={'Authorization':'Bearer '+response.json()['access_token']}
                for route,body in [('/api/datasets/import',{'name':'x','content':'id\n1'}),('/api/schema/index/rebuild',{})]:
                    self.assertEqual(client.post(route,headers=headers,json=body).status_code,403)

    def test_public_job_capacity_blocks_before_scheduling_model_work(self):
        with patch.dict(os.environ, {'SHUCE_PUBLIC_DEPLOYMENT':'true','SHUCE_PASSWORD_ANALYST':'example-only-strong-test-password'}, clear=True):
            with TestClient(app) as client:
                token=client.post('/api/auth/login',json={'username':'analyst','password':'example-only-strong-test-password'}).json()['access_token']
                running={'id':'busy','user_id':'another','session_id':'other','status':'running'}
                with patch.dict(routes.JOBS, {'busy':running}, clear=True), patch.object(routes,'EXECUTOR',Mock()) as executor:
                    result=client.post('/api/jobs',headers={'Authorization':'Bearer '+token},json={'query':'查订单','session_id':'new','workspace':{'database_id':'olist_ecommerce'}})
                    self.assertEqual(result.status_code,429)
                    executor.submit.assert_not_called()

    def test_scoped_city_query_fits_public_memory_limit_and_preserves_results(self):
        from pathlib import Path
        from app.querying.duckdb_engine import DuckDbEngine
        sql=(Path(__file__).resolve().parents[2]/'docs/evidence/city-query.sql').read_text()
        with patch.dict(os.environ, {'SHUCE_PUBLIC_DEPLOYMENT':'true'}):
            result=DuckDbEngine().execute('olist_ecommerce',sql,AccessController().resolve('demo_olist_analyst'))
        self.assertTrue(result.success,result.error)
        self.assertEqual(result.total_rows,5)
        self.assertEqual(result.rows[0]['customer_city'],'rio de janeiro')
        self.assertEqual(result.rows[0]['order_count'],44)
        self.assertEqual(result.rows[0]['item_sales'],9234.59)
