from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

from mcp import Client
from mcp.server import MCPServer
from ..telemetry import begin_call, finish_call


class LocalMcpClient:
    """通过MCP进程内传输发现并调用工具。"""

    def __init__(self, server: MCPServer) -> None:
        self.server = server

    def list_tools(self) -> list[dict[str, Any]]:
        started = monotonic()
        event = begin_call('tool', name='tools/list', operation='discovery')
        try:
            result = asyncio.run(self._list_tools())
            finish_call(event, started, True, tool_count=len(result))
            return result
        except Exception:
            finish_call(event, started, False, error='工具发现失败')
            raise

    async def _list_tools(self) -> list[dict[str, Any]]:
        async with Client(self.server) as client:
            result = await client.list_tools()
            return [
                {
                    "name": tool.name,
                    "title": tool.title,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "output_schema": tool.output_schema,
                    "annotations": (
                        tool.annotations.model_dump(by_alias=True, exclude_none=True)
                        if tool.annotations
                        else {}
                    ),
                }
                for tool in result.tools
            ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = monotonic()
        event = begin_call('tool', name=name, operation='call')
        try:
            result = asyncio.run(self._call_tool(name, arguments))
            success = result.get('success') is not False and not bool(result.get('error'))
            finish_call(event, started, success, row_count=result.get('total_rows'),
                        error=None if success else '工具返回执行失败，见本次结果')
            return result
        except Exception:
            finish_call(event, started, False, error='工具调用或参数校验失败')
            raise

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with Client(self.server) as client:
            result = await client.call_tool(name, arguments)
        # 退出MCP会话后再抛出错误，避免异步任务组包装业务异常。
        if result.is_error:
            messages = [
                str(getattr(block, "text", ""))
                for block in result.content
                if getattr(block, "text", "")
            ]
            raise RuntimeError("; ".join(messages) or f"MCP工具调用失败：{name}")
        if result.structured_content is None:
            raise RuntimeError(f"MCP工具未返回结构化结果：{name}")
        return dict(result.structured_content)
