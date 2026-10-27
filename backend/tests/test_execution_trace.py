import io
import json
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from app.config import Settings
from app.model_client import ModelClient
from app.models import QueryResult
from app.progress import stage
from app.services.session_context import SessionContext
from app.telemetry import TaskTrace, active_trace, begin_call, finish_call, observe_task, public_usage, trace_observer
from app.mcp_runtime.client import LocalMcpClient


class ExecutionTraceTests(unittest.TestCase):
    def test_live_observer_sees_pending_and_finished_calls_without_mutating_old_snapshot(self):
        from time import monotonic
        snapshots = []
        token = trace_observer.set(snapshots.append)
        @observe_task
        def task():
            event = begin_call('model', capability='chat', model='demo', attempt=1)
            finish_call(event, monotonic(), False, error='HTTP 429')
        try:
            task()
        finally:
            trace_observer.reset(token)
        self.assertEqual(snapshots[0]['model_calls'], [])
        self.assertNotIn('success', snapshots[1]['model_calls'][0])
        self.assertFalse(snapshots[-1]['model_calls'][0]['success'])
        self.assertEqual(snapshots[-1]['model_calls'][0]['error'], 'HTTP 429')
        self.assertIsNone(active_trace.get())

    def test_observer_failure_does_not_fail_business(self):
        def broken(_): raise RuntimeError('observer failure')
        token = trace_observer.set(broken)
        @observe_task
        def task():
            stage('preprocess')
            begin_call('model', capability='chat')
            return 'ok'
        try:
            self.assertEqual(task(), 'ok')
        finally:
            trace_observer.reset(token)

    def test_invalid_or_untrusted_usage_does_not_change_response_handling(self):
        for response in ([], None, {'usage': []}, {'usage': {'total_tokens': -1, 'input_tokens': True}}):
            self.assertIsNone(public_usage(response))
        self.assertEqual(public_usage({'usage': {'total_tokens': 0, 'private': 'hidden'}}), {'total_tokens': 0})

    def config(self, **kwargs):
        return Settings(api_key='secret-test', llm_model='test-model', max_retries=kwargs.pop('max_retries', 0),
                        session_archive_enabled=False, short_term_summary_enabled=False, **kwargs)

    def test_http_retry_usage_and_no_private_payload(self):
        trace = TaskTrace()
        token = active_trace.set(trace)
        client = ModelClient(self.config(max_retries=1))
        reply = {'choices': [{'message': {'content': 'private answer'}}],
                 'usage': {'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15,
                           'prompt_cache_hit_tokens': 8, 'private_data': 'secret-test'}}
        error = urllib.error.HTTPError('https://example.com', 429, 'busy', {}, io.BytesIO(b'secret-test'))
        try:
            stage('result_analysis')
            with patch('urllib.request.urlopen', side_effect=[error, io.BytesIO(json.dumps(reply).encode())]), patch('time.sleep'):
                self.assertEqual(client.chat('private prompt', 'private question'), 'private answer')
            output = trace.snapshot()
        finally:
            active_trace.reset(token)
        calls = output['model_calls']
        self.assertEqual([c['success'] for c in calls], [False, True])
        self.assertEqual([c['attempt'] for c in calls], [1, 2])
        self.assertEqual(calls[0]['error'], 'HTTP 429')
        self.assertEqual(calls[1]['usage']['total_tokens'], 15)
        self.assertEqual(calls[1]['stage'], 'result_analysis')
        for private in ('secret-test', 'private prompt', 'private question', 'private answer', 'private_data', 'Authorization'):
            self.assertNotIn(private, json.dumps(output))

    def test_missing_usage_is_unknown_and_missing_key_is_zero_requests(self):
        @observe_task
        def call():
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')):
                ModelClient(self.config()).chat('s', 'u')
            return active_trace.get().snapshot()
        result = call()
        self.assertIsNone(result['model_calls'][0]['usage'])
        self.assertIsNone(active_trace.get())
        client = ModelClient(Settings(api_key='', max_retries=0))
        token = active_trace.set(TaskTrace())
        try:
            with self.assertRaises(RuntimeError): client.chat('s', 'u')
            self.assertEqual(active_trace.get().snapshot()['model_calls'], [])
        finally: active_trace.reset(token)

    def test_concurrent_tasks_do_not_share_calls(self):
        barrier = Barrier(2)
        @observe_task
        def task(name):
            stage(name)
            barrier.wait()
            begin_call('model', capability='chat', model=name)
            return active_trace.get().snapshot()
        with ThreadPoolExecutor(2) as pool:
            a, b = list(pool.map(task, ['a', 'b']))
        self.assertEqual([c['model'] for c in a['model_calls']], ['a'])
        self.assertEqual([c['model'] for c in b['model_calls']], ['b'])

    def test_tool_exception_and_failed_result_are_visible(self):
        client = LocalMcpClient(None)
        async def rejected(*args): raise RuntimeError('private error')
        async def failed(*args): return {'success': False, 'error': 'private SQL'}
        token = active_trace.set(TaskTrace())
        try:
            with patch.object(client, '_call_tool', rejected):
                with self.assertRaises(RuntimeError): client.call_tool('query_demo', {'sql': 'private SQL'})
            with patch.object(client, '_call_tool', failed): client.call_tool('query_demo', {})
            trace = active_trace.get().snapshot()
        finally: active_trace.reset(token)
        self.assertEqual([c['success'] for c in trace['tool_calls']], [False, False])
        self.assertNotIn('private', json.dumps(trace))

    def test_trace_persisted_and_clarification_accumulates_without_wait_time(self):
        with tempfile.TemporaryDirectory() as directory:
            from dataclasses import replace
            config = replace(self.config(session_archive_path=str(Path(directory) / 'sessions.db')), session_archive_enabled=True)
            context = SessionContext(ModelClient(config), config)
            @observe_task
            def save():
                event = begin_call('model', capability='chat', model='test', attempt=1)
                from time import monotonic
                finish_call(event, monotonic(), True)
                result = QueryResult(task_id='t', status='completed', route='direct_response', message='ok')
                context.remember('demo_admin:s:olist_ecommerce', result, 'q', {'database_id':'olist_ecommerce'}, 'demo_admin')
                return result
            first = save()
            second = save()
            self.assertEqual(len(first.execution_trace['model_calls']), 1)
            self.assertEqual(len(second.execution_trace['model_calls']), 2)
            restored = SessionContext(ModelClient(config), config)
            self.assertEqual(restored.tasks['t']['result'].execution_trace, second.execution_trace)


if __name__ == '__main__': unittest.main()
