import unittest
from unittest.mock import Mock
from app.querying.data_qa_agent import DataQaAgent
from app.skills import SkillRegistry
class ChartEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.agent=DataQaAgent(Mock(),Mock(),SkillRegistry().get('data_qa'))
        self.source={'task-1':{'task_id':'task-1','columns':['channel','users']}}
        self.args={'title':'注册用户','source_task_id':'task-1','category_field':'channel','value_field':'users'}
        self.client=Mock();self.client.call_tool.return_value={'type':'bar',**self.args}
    def test_chart_call_in_answer_is_normalized_to_valid_report(self):
        result=self.agent._execute({'action':'answer','answer':'信息流广告注册人数最多。','tool_calls':[{'name':'build_bar_chart','arguments':self.args}]},self.client,self.source)
        self.assertEqual(result.action,'report')
        self.assertEqual(result.report.markdown,'信息流广告注册人数最多。')
        self.assertEqual(result.report.visualizations[0].source_task_id,'task-1')
        self.client.call_tool.assert_called_once()
    def test_normalization_still_rejects_unavailable_data_source(self):
        args={**self.args,'source_task_id':'invented'}
        result=self.agent._execute({'action':'answer','answer':'摘要','tool_calls':[{'name':'build_bar_chart','arguments':args}]},self.client,self.source)
        self.assertTrue(result.report.validation_feedback)
        self.assertEqual(result.report.visualizations,[])
        self.client.call_tool.assert_not_called()
    def test_normalization_still_rejects_disallowed_tools(self):
        result=self.agent._execute({'action':'answer','answer':'摘要','tool_calls':[{'name':'query_database','arguments':self.args}]},self.client,self.source)
        self.assertTrue(result.report.validation_feedback)
        self.assertEqual(result.report.visualizations,[])
        self.client.call_tool.assert_not_called()
    def test_two_period_and_single_category_charts_are_valid(self):
        from app.mcp_runtime.tools.presentation_tools import build_bar_chart
        from app.models import VisualizationSpec
        from pydantic import TypeAdapter,ValidationError
        from app.mcp_runtime.tools.presentation_tools import MaxItems
        for count in (1,2,30):
            args={**self.args,'max_items':TypeAdapter(MaxItems).validate_python(count)}
            spec=build_bar_chart(**args)
            self.assertEqual(VisualizationSpec.model_validate(spec.model_dump()).max_items,count)
        for count in (0,31):
            with self.assertRaises(ValidationError):TypeAdapter(MaxItems).validate_python(count)
if __name__=='__main__': unittest.main()
