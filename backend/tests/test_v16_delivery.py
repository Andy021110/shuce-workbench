import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from app.config import Settings
from app.models import QueryResult, WorkspaceConfig
from app.model_client import ModelClient
from app.querying.data_qa_agent import DataQaAgent
from app.querying.response_generator import ResponseGenerator
from app.querying.models import SqlExecution
from app.report_delivery import preferences
from app.report_facts import build_facts, source_items, validate_and_render
from app.result_profile import build_profile
from app.services.session_context import SessionContext
from app.skills import SkillRegistry

FIXTURE=json.loads((Path(__file__).parent/'fixtures/regional_review_sources.json').read_text())

def contexts():
    sources=[]
    for entry in FIXTURE['sources']:
        r=entry['result'];sources.append({'task_id':r['task_id'],'title':r['result_title'],'columns':r['columns'],'rows':r['rows'],'row_count':r['total_rows'],'query':entry['query'],'analysis_scope':r.get('analysis_scope'),'result_profile':build_profile(r['columns'],r['rows'],total_rows=r['total_rows'])})
    return {'selected_tables':json.dumps(sources,ensure_ascii=False),'recent_result':json.dumps({'task_id':'unselected','columns':['orders'],'rows':[{'orders':999999}]}),'schema_catalog':'[]'}

class SourceConsistencyTests(unittest.TestCase):
    def test_six_selected_sources_include_timeline_and_exclude_unselected_recent(self):
        c=contexts();items=source_items(c);self.assertEqual(len(items),6)
        facts=build_facts(c);owners={f['source_task_id'] for f in facts.values()}
        self.assertEqual(owners,{i['task_id'] for i in items})
        timeline=[f for f in facts.values() if f['field']=='review_answer_timestamp']
        self.assertEqual(len(timeline),1);self.assertIn('2018-08-29',timeline[0]['display'])
        self.assertTrue(any(f['field']=='order_id' for f in facts.values()))
        self.assertEqual(set(DataQaAgent._source_catalog(c)),owners)
    def test_wide_first_source_does_not_consume_later_source_fact_budget(self):
        cols=['metric'+str(i) for i in range(16)];rows=[{c:i for c in cols} for i in range(50)]
        c={'selected_tables':json.dumps([{'task_id':'t'+str(i),'columns':cols,'rows':rows,'result_profile':build_profile(cols,rows)} for i in range(12)])}
        facts=build_facts(c);self.assertLessEqual(len(facts),600);self.assertEqual(len({f['source_task_id'] for f in facts.values()}),12)
    def test_model_cannot_forge_date_or_value_references(self):
        facts=build_facts(contexts())
        self.assertIn('2018-08-29',validate_and_render({'answer':'评价时间{{fact:s5_r0_c5}}。'},facts,[])['answer'])
        for text in ['金额增长999999元。','有五个商品贡献增长。','{{fact:F9999}}']:
            with self.assertRaises(ValueError):validate_and_render({'answer':text},facts,[])
    def test_source_context_has_all_six_and_explicit_capacity_error(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg=replace(Settings(),session_archive_enabled=False,session_archive_path=folder+'/archive.db',short_term_summary_enabled=False)
            ctx=SessionContext(ModelClient(cfg),cfg);session='demo_admin:six:olist_ecommerce'
            for e in FIXTURE['sources']:
                r=QueryResult(status='completed',route='database_query',message='ok',**e['result']);ctx.remember(session,r,e['query'],{'database_id':'olist_ecommerce'},'demo_admin')
            ids=[e['result']['task_id'] for e in FIXTURE['sources']]
            selected,sources=ctx.analysis_context(session,{'analysis_table_ids':ids})
            self.assertEqual([s['task_id'] for s in sources],ids);self.assertEqual(len(json.loads(selected)),6)
            with self.assertRaises(ValueError):ctx.normalize_workspace({'analysis_table_ids':['t'+str(i) for i in range(13)]})
            with self.assertRaises(ValueError):WorkspaceConfig(analysis_table_ids=['t'+str(i) for i in range(13)])

class ReportDeliveryTests(unittest.TestCase):
    def agent(self,reply):
        client=Mock();client.list_tools.return_value=[{'name':'build_bar_chart'}]
        client.call_tool.side_effect=lambda name,args:{'type':'bar',**args}
        model=Mock();model.chat_json.side_effect=reply
        return DataQaAgent(model,lambda _:client,SkillRegistry().get('data_qa')),model
    def test_original_six_source_report_delivers_bounded_summary_and_requested_chart(self):
        def reply(system,user):
            catalog=json.loads(user)['fact_catalog'];ids=list(catalog)
            return {'action':'report','title':'RJ经营月会','answer':'增长构成与个案边界需要分别核对。','observations':[{'interpretation':'当前统计显示增长，不能直接认定同款涨价或新品带动。','fact_ids':[ids[min(i,len(ids)-1)]]} for i in range(8)],'hypotheses':['商品结构变化仍待验证。'],'actions':['核对同款历史售价。']*8,'tool_calls':[]}
        agent,model=self.agent(reply);r=agent.run(FIXTURE['report_query'],contexts(),{}).report
        self.assertEqual(r.generation_mode,'model_with_facts');self.assertEqual(len(r.visualizations),1)
        self.assertEqual(r.visualizations[0].value_field,'item_sales')
        self.assertNotIn('## 查询事实',r.markdown);self.assertIn('## 查询事实',r.evidence_markdown)
        self.assertEqual(r.markdown.count('当前统计显示增长'),5);self.assertEqual(r.markdown.count('核对同款历史售价'),3)
        self.assertEqual(model.chat_json.call_count,1)
    def test_invalid_numbers_still_fall_back_with_safe_diagnostic_code(self):
        agent,_=self.agent(lambda *_:{'action':'report','title':'错误报告','answer':'金额999999元。','observations':[{'interpretation':'增长。','fact_ids':['F001']}], 'tool_calls':[]})
        r=agent.run(FIXTURE['report_query'],contexts(),{}).report
        self.assertEqual(r.generation_mode,'facts_only');self.assertIn('uncited_business_value',r.validation_codes)
        self.assertNotIn('999999',r.markdown);self.assertTrue(r.evidence_markdown)
    def test_rank_selection_label_allowed_but_measured_product_count_is_not(self):
        facts=build_facts(contexts());self.assertIn('Top5',validate_and_render({'answer':'先核查Top5商品。'},facts,[])['answer'])
        with self.assertRaises(ValueError):validate_and_render({'answer':'共有5个商品增长。'},facts,[])
    def test_selected_product_count_is_verified_but_wrong_or_population_counts_fail(self):
        facts=build_facts(contexts())
        result=validate_and_render({'answer':'这5个商品需要继续核查。'},facts,[])
        self.assertTrue(any(f['id']=='s2_rows' for f in result['fact_references']))
        for text in ['这6个商品需要核查。','共有5个商品增长。']:
            with self.assertRaises(ValueError):validate_and_render({'answer':text},facts,[])
        self.assertIn('6份',validate_and_render({'answer':'基于6份结果整理。'},facts,[])['answer'])
        with self.assertRaises(ValueError):validate_and_render({'answer':'基于7份结果整理。'},facts,[])

    def test_selected_result_gap_is_not_a_claim_that_the_database_lacks_prices(self):
        facts=build_facts(contexts());schema=[{'fields':[{'name':'price'}]}]
        text='当前选中的结果缺少同款历史价格对比，尚不能认定涨价。'
        self.assertEqual(validate_and_render({'answer':text},facts,schema)['answer'],text)
        with self.assertRaises(ValueError):validate_and_render({'answer':'整个数据源没有价格字段。'},facts,schema)

    def test_requested_single_chart_reuses_equivalent_full_period_source(self):
        from app.report_delivery import merge_charts
        c=DataQaAgent._source_catalog(contexts())
        raw={'name':'build_bar_chart','arguments':{'source_task_id':'32436cdbf5a0:summary:time','category_field':'period','value_field':'item_sales_sum','title':'金额'}}
        result=merge_charts('附一个商品金额对比图',[raw],c)
        self.assertEqual(result,[raw])
        self.assertEqual(merge_charts('不要图表',[raw],c),[])

    def test_delivery_limits_are_explicit_and_chinese_numbers_supported(self):
        p=preferences('一页报告，最多三条结论、下一步动作不超过两条')
        self.assertEqual(p['observations'],3);self.assertEqual(p['actions'],2)

class BusinessGoalTests(unittest.TestCase):
    def test_result_explanation_receives_original_goal_and_returns_coverage(self):
        self.assertTrue(ResponseGenerator.decision_questions('样本足够支持配送超时导致差评吗'))
        question='统计每月平均明细价格，是否能直接证明涨价驱动增长'
        model=Mock();model.chat_json.return_value={'valid':True,'title':'量价拆解','analysis':'仅均价变化不足以确认原因。','answer_checks':[{'question':question,'status':'insufficient_evidence','answer':'不能确认同款涨价，需比较同商品的历史价格与销售结构。'}]}
        r=ResponseGenerator(model).finalize(question,SqlExecution('SELECT price',True,['price'],[{'price':10}],total_rows=1),'','')
        self.assertIn(question,model.chat_json.call_args.args[1]);self.assertEqual(r['answer_checks'][0]['status'],'insufficient_evidence')
    def test_empty_query_still_explains_that_decision_evidence_is_insufficient(self):
        model=Mock();r=ResponseGenerator(model).finalize('是否可以认定涨价',SqlExecution('SELECT t',True,['t'],[],total_rows=0),'','')
        self.assertEqual(r['answer_checks'][0]['status'],'insufficient_evidence');model.chat_json.assert_not_called()

    def test_omitted_goal_is_visible_as_unanswered_instead_of_silent_success(self):
        q='评价在收货之前，是否矛盾';model=Mock();model.chat_json.return_value={'valid':True,'analysis':'已返回时间。'}
        r=ResponseGenerator(model).finalize(q,SqlExecution('SELECT t',True,['t'],[{'t':1}],total_rows=1),'','')
        self.assertEqual(r['answer_checks'][0]['status'],'not_answered')

from tests import test_business_tasks as base
from app.models import AnalysisReport

class RetryBindingTests(unittest.TestCase):
    login=base.MonthlyApiTests.login
    setUp=base.MonthlyApiTests.setUp
    tearDown=base.MonthlyApiTests.tearDown

    def seed(self):
        ids=[];session='demo_admin:retry:olist_ecommerce'
        for entry in FIXTURE['sources']:
            r=QueryResult(status='completed',route='database_query',message='ok',**entry['result'])
            self.service.context.remember(session,r,entry['query'],{'database_id':'olist_ecommerce'},'demo_admin');ids.append(r.task_id)
        report=QueryResult(task_id='old-report',status='completed',route='data_qa',message='old',report=AnalysisReport(title='old',markdown='原事实',generation_mode='facts_only'),analysis_sources=[{'task_id':tid} for tid in ids[:5]])
        self.service.context.remember(session,report,'原报告问题',{'database_id':'olist_ecommerce','analysis_table_ids':ids},'demo_admin')
        return ids

    def test_retry_uses_original_six_even_if_old_report_list_or_current_selection_has_five(self):
        ids=self.seed();output=QueryResult(task_id='retry-output',status='completed',route='data_qa',message='ok')
        with patch.object(self.service.workflow,'invoke',return_value=output) as invoke:
            self.service.submit('原报告问题','retry',{'database_id':'olist_ecommerce','retry_task_id':'old-report','analysis_table_ids':ids[:5]},'demo_admin')
        payload=invoke.call_args.args[0]
        self.assertEqual([s['task_id'] for s in payload['analysis_sources']],ids)
        self.assertEqual(len(json.loads(payload['analysis_context'])),6)
        self.assertEqual(payload['recent_result_context'],'')

    def test_retry_rejects_other_user_other_database_and_changed_question_before_model(self):
        self.seed()
        with patch.object(self.service.workflow,'invoke') as invoke:
            for user,db,question,error in [('demo_growth_ops','short_video_ops','原报告问题',PermissionError),('demo_admin','short_video_ops','原报告问题',PermissionError),('demo_admin','olist_ecommerce','被修改的问题',ValueError)]:
                with self.assertRaises(error):self.service.submit(question,'retry',{'database_id':db,'retry_task_id':'old-report'},user)
            invoke.assert_not_called()

    def test_provider_failure_preserves_automatic_sources_for_later_retry(self):
        from app.errors import PipelineStageError
        self.seed()
        with patch.object(self.service.workflow,'invoke',side_effect=PipelineStageError('qa_answer','测试接口不可用')):
            failed=self.service.submit('原报告问题','retry',{'database_id':'olist_ecommerce'},'demo_admin')
        expected=[source['task_id'] for source in failed.analysis_sources]
        self.assertEqual(len(expected),4)
        extra=QueryResult(task_id='newer-result',status='completed',route='database_query',message='new',columns=['orders'],rows=[{'orders':999}])
        self.service.context.remember('demo_admin:retry:olist_ecommerce',extra,'后来新增的查询',{'database_id':'olist_ecommerce'},'demo_admin')
        result=QueryResult(task_id='network-retry',status='completed',route='data_qa',message='ok')
        with patch.object(self.service.workflow,'invoke',return_value=result) as invoke:
            self.service.submit('原报告问题','retry',{'database_id':'olist_ecommerce','retry_task_id':failed.task_id},'demo_admin')
        self.assertEqual([source['task_id'] for source in invoke.call_args.args[0]['analysis_sources']],expected)

    def test_running_job_does_not_reuse_a_different_original_report_retry(self):
        from app.api import routes
        ids=self.seed()
        original=self.service.tasks['old-report']['result'].model_copy(deep=True)
        original.task_id='other-report'
        self.service.context.remember('demo_admin:retry:olist_ecommerce',original,'原报告问题',{'database_id':'olist_ecommerce','analysis_table_ids':ids[:4]},'demo_admin')
        with patch.dict(routes.JOBS,{},clear=True),patch.object(routes,'EXECUTOR',Mock()) as executor:
            body={'query':'原报告问题','session_id':'retry','workspace':{'database_id':'olist_ecommerce','retry_task_id':'old-report'}}
            first=self.client.post('/api/jobs',headers=self.admin,json=body)
            same=self.client.post('/api/jobs',headers=self.admin,json=body)
            different=self.client.post('/api/jobs',headers=self.admin,json={**body,'workspace':{**body['workspace'],'retry_task_id':'other-report'}})
            self.assertEqual(first.status_code,200)
            self.assertEqual(same.json()['id'],first.json()['id'])
            self.assertEqual(different.status_code,409)
            self.assertEqual(executor.submit.call_count,1)
