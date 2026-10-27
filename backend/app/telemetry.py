"""Task-local, public execution metadata. Never retain prompts, headers or responses."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from time import monotonic
from typing import Any


active_trace: ContextVar = ContextVar('active_task_trace', default=None)
trace_observer: ContextVar = ContextVar('task_trace_observer', default=None)


class TaskTrace:
    def __init__(self):
        self.started = monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.current_stage = 'prepare_request'
        self.model_calls: list[dict] = []
        self.tool_calls: list[dict] = []
        self.stages: list[dict] = []
        self.observer = trace_observer.get()

    def publish(self):
        if self.observer:
            try:
                self.observer(self.snapshot())
            except Exception:
                # Observability must never interrupt a business request.
                pass

    def offset(self):
        return round((monotonic() - self.started) * 1000)

    def snapshot(self, previous=None):
        previous = previous or {}
        offset = previous.get('duration_ms', 0)
        def combine(key):
            return [*previous.get(key, []), *[{**event, 'offset_ms': event['offset_ms'] + offset} for event in getattr(self, key)]]
        return {'version': 1, 'started_at': previous.get('started_at', self.started_at),
                'duration_ms': offset + self.offset(), 'model_calls': combine('model_calls'),
                'tool_calls': combine('tool_calls'), 'stages': combine('stages')}


def observe_task(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        if active_trace.get() is not None:
            return fn(*args, **kwargs)
        trace = TaskTrace()
        token = active_trace.set(trace)
        trace.publish()
        try:
            return fn(*args, **kwargs)
        finally:
            trace.publish()
            active_trace.reset(token)
    return call


def trace_stage(name):
    trace = active_trace.get()
    if trace:
        trace.current_stage = name
        trace.stages.append({'stage': name, 'offset_ms': trace.offset()})
        trace.publish()


def begin_call(kind: str, **metadata) -> dict[str, Any] | None:
    trace = active_trace.get()
    if trace is None:
        return None
    event = {'stage': trace.current_stage, 'offset_ms': trace.offset(), **metadata}
    getattr(trace, kind + '_calls').append(event)
    trace.publish()
    return event


def finish_call(event, started, success, **metadata):
    if event is not None:
        event.update(duration_ms=round((monotonic() - started) * 1000), success=success, **metadata)
        trace = active_trace.get()
        if trace:
            trace.publish()


def public_usage(result):
    if not isinstance(result, dict):
        return None
    usage = result.get('usage')
    if not isinstance(usage, dict):
        return None
    # Vendor-specific metadata is intentionally excluded by this allowlist.
    fields = ('prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens',
              'total_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
    clean = {key: value for key in fields if isinstance(value := usage.get(key), int)
             and not isinstance(value, bool) and value >= 0}
    return clean or None
