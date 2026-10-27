import unittest
from pydantic import ValidationError
from app.models import QueryRequest
from app.retrieval.graph import SchemaGraphBuilder
from app.security import AccessController

class QueryInputTests(unittest.TestCase):
    def test_blank_queries_are_rejected_before_processing(self):
        for query in ['', ' \t\n']:
            with self.subTest(query=repr(query)), self.assertRaises(ValidationError): QueryRequest(query=query)
    def test_user_text_is_trimmed(self):
        self.assertEqual(QueryRequest(query='  统计注册人数  ').query,'统计注册人数')

class TemporalContextTests(unittest.TestCase):
    def setUp(self):
        self.builder=SchemaGraphBuilder()
        self.hit={'table_id':'short_video_ops.user_registrations','database_id':'short_video_ops',
                  'doc_id':'short_video_ops.user_registrations.user_id','field_name':'user_id',
                  'field_label':'用户编号','field_type':'文本'}
    def test_recalled_table_keeps_its_timestamp_for_followup_filters(self):
        graph=self.builder.build([self.hit])
        time_fields=[f for f in graph['fields'] if f['name']=='registered_at']
        self.assertEqual(len(time_fields),1)
        self.assertEqual(time_fields[0]['source'],'table_time_context')
        self.assertEqual({f['table_id'] for f in graph['fields']},{self.hit['table_id']})
    def test_existing_timestamp_is_not_duplicated(self):
        timestamp={**self.hit,'doc_id':'short_video_ops.user_registrations.registered_at',
                   'field_name':'registered_at','field_label':'注册时间','field_type':'日期'}
        graph=self.builder.build([self.hit,timestamp])
        self.assertEqual(sum(f['name']=='registered_at' for f in graph['fields']),1)
    def test_supplement_does_not_expose_denied_tables(self):
        scope=AccessController().resolve('demo_content_ops')
        graph=self.builder.build([self.hit],scope)
        self.assertEqual(graph['tables'],[])
        self.assertEqual(graph['fields'],[])
if __name__=='__main__': unittest.main()
