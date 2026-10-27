from contextvars import ContextVar
from .telemetry import trace_stage
progress_callback = ContextVar("progress_callback", default=None)
def stage(name):
    trace_stage(name)
    callback = progress_callback.get()
    if callback: callback(name)
