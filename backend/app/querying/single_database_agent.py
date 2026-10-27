from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from typing import Any, Callable

from ..errors import PipelineStageError
from ..metric_registry import guidance
from ..analysis_basis import basis_error
from ..analysis_scope import apply_filters
from ..query_intent import business_sql_error
from sqlglot.errors import ParseError
from ..mcp_runtime.client import LocalMcpClient
from ..model_client import ModelClient
from ..skills import SkillDefinition


class SingleDatabaseAgent:
    """让模型通过MCP工具完成单数据库查询。"""

    def __init__(
        self,
        model_client: ModelClient,
        mcp_client_factory: Callable[[dict[str, Any]], LocalMcpClient],
        skill: SkillDefinition,
        max_tool_calls: int = 3,
    ) -> None:
        self.model_client = model_client
        self.mcp_client_factory = mcp_client_factory
        self.skill = skill
        self.max_tool_calls = min(max_tool_calls, skill.max_tool_calls)

    def prepare(
        self,
        query: str,
        database: str,
        schema_graph: dict[str, Any],
        schema_context: str,
        retrieval: dict[str, Any],
        workspace: dict[str, Any],
        access_scope: dict[str, Any],
    ) -> dict[str, Any]:
        if workspace.get('scope_guard'):
            raise PipelineStageError('scope_inheritance',workspace['scope_guard'])
        database_tool = f"query_{database}"
        mcp_client = self.mcp_client_factory(access_scope)

        # 每次请求都通过MCP tools/list获取当前工具定义。
        catalog = mcp_client.list_tools()
        tools = [
            tool
            for tool in catalog
            if any(fnmatch(tool["name"], pattern) for pattern in self.skill.allowed_tools)
            and (not tool["name"].startswith("query_") or tool["name"] == database_tool)
        ]
        tool_names = {tool["name"] for tool in tools}
        if database_tool not in tool_names:
            raise PipelineStageError(
                "mcp_tool_discovery",
                f"没有找到数据库工具：{database_tool}",
            )

        system = f"你是单数据库问数智能体。\n\n{self.skill.instructions}\n\n{guidance(database)}"
        if database=='olist_ecommerce':
            system+='\n后端统一施加business_intent中的正向群体、exclusions排除范围和月份。all_statuses为真时订单总量不得有状态WHERE，超时率仅使用CASE/FILTER定义样本。不要复制或展开系统范围SQL；可以省略这些WHERE以保持SQL简短。品类金额仍须只统计选中品类商品。每月结果必须保留实际月份列和分组。watches_gifts等英文值属于翻译表product_category_name_english，原始product_category_name为葡语。商品、支付须按订单分别预聚合。时长别名使用p2c_avg_days/p2c_p90_days/c2d_avg_days/c2d_p90_days及对应valid_samples。'

        observations: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []
        base_payload = {
            "query": query,
            "database": database,
            "schema_graph": schema_graph,
            "retrieval": {
                "threshold": retrieval.get("threshold"),
                "selected_fields": retrieval.get("hits", []),
                "low_confidence_candidates": retrieval.get("low_confidence_candidates", []),
            },
            "confirmed_fields": workspace.get("schema_fields", []),
            "confirmed_parameters": workspace.get("confirmed_parameters", {}),
            "schema_text": schema_context,
            "analysis_basis": workspace.get("analysis_basis") or {},
            "analysis_filters": workspace.get("analysis_filters") or {},
            "business_intent": workspace.get("business_intent") or {},
            "original_query": workspace.get("original_query") or query,
            "confirmed_scope_query": workspace.get("confirmed_scope_query") or '',
            "mcp_tools": tools,
        }

        try:
            for call_index in range(1, self.max_tool_calls + 1):
                payload = {**base_payload, "tool_results": observations}
                decision = self._validated_decision(system, payload, tool_names)
                action = str(decision["action"])

                if action == "clarify":
                    clarification = decision.get("clarification")
                    if not isinstance(clarification, dict) or len(
                        clarification.get("options") or []
                    ) < 2:
                        raise ValueError("智能体返回的澄清信息不完整")
                    return {
                        "action": "clarify",
                        "clarification": clarification,
                        "tool_trace": tool_trace,
                    }

                if action != "call_tool":
                    raise ValueError("智能体必须返回call_tool或clarify")

                tool_name = str(decision["tool_name"])
                arguments = decision["arguments"]

                try:
                    tool_result = mcp_client.call_tool(tool_name, arguments)
                except (RuntimeError,ValueError) as exc:
                    # Treat tool validation as a repairable observation. Never
                    # expose raw schema-validation payloads in product copy.
                    if call_index < self.max_tool_calls:
                        observations.append({'tool':tool_name,'result':{'success':False,'error':'查询工具拒绝了参数，请简化SQL并保持业务条件，返回完整JSON。'}})
                        continue
                    raise PipelineStageError('query_validation','查询未通过执行前检查。请保留当前问题重试，或从月度复盘选择范围后查询。') from exc
                trace = {
                    "call_index": call_index,
                    "tool": tool_name,
                    "arguments": arguments,
                    "query_contract": decision.get("query_contract", {}),
                    "result": tool_result,
                    "reason": str(decision.get("reason") or ""),
                }
                tool_trace.append(trace)

                if tool_name == database_tool:
                    if not bool(tool_result.get("success")) and call_index < self.max_tool_calls:
                        observations.append({
                            "tool": tool_name,
                            "result": tool_result,
                            "instruction": (
                                "上一条SQL执行失败。只根据数据库错误、原问题和Schema修正SQL，"
                                "不得改变查询口径，然后重新调用同一个数据库工具。"
                            ),
                        })
                        continue
                    return {
                        "action": "executed",
                        "execution": tool_result,
                        "tool_trace": tool_trace,
                        "source": "model_mcp",
                    }

                observations.append({"tool": tool_name, "result": tool_result})

            raise ValueError(f"MCP工具调用超过上限：{self.max_tool_calls}")
        except PipelineStageError:
            raise
        except (RuntimeError, KeyError, TypeError, ValueError) as exc:
            raise PipelineStageError("single_database_agent", str(exc)) from exc

    def _validated_decision(
        self,
        system: str,
        payload: dict[str, Any],
        tool_names: set[str],
    ) -> dict[str, Any]:
        """结构化结果不完整时让模型最多修正两次，不执行不完整的工具调用。"""
        validation_error = ""
        previous_decision: dict[str, Any] | None = None
        for attempt in range(3):
            request_payload = dict(payload)
            if validation_error:
                request_payload["previous_invalid_output"] = previous_decision
                request_payload["output_validation_error"] = validation_error
                request_payload["output_validation_instruction"] = (
                    "上一次输出未通过调用前校验。根据错误修正JSON字段或SQL，"
                    "保持原查询口径并重新输出完整对象。"
                )
            try:
                decision = self.model_client.chat_json(system,json.dumps(request_payload,ensure_ascii=False))
            except RuntimeError as exc:
                if 'JSON' not in str(exc):raise
                validation_error='上一条不是有效JSON。只返回一个完整的JSON对象；SQL使用原始表，勿重复展开范围。'
                if attempt<2:continue
                raise PipelineStageError('query_validation','查询方案格式未通过检查，尚未执行。原问题已保留，可以重试或选择月度复盘范围。') from exc
            if not isinstance(decision,dict):
                validation_error='只返回一个JSON对象，不能返回数组或文字。'
                if attempt<2:continue
                raise PipelineStageError('query_validation','查询方案格式未通过检查，尚未执行。请重试。')
            previous_decision = decision
            validation_error = self._decision_error(decision, tool_names)
            if not validation_error:
                validation_error = self._explicit_filter_error(decision, payload)
            if not validation_error and decision.get("action") == "call_tool" and str(decision.get("tool_name", "")).startswith("query_"):
                validation_error = basis_error(str((decision.get("arguments") or {}).get("sql") or ""), payload.get("analysis_basis") or {})
            if not validation_error:
                if decision.get('action')=='call_tool' and decision.get('tool_name')=='query_olist_ecommerce':
                    # The server applies inherited cohort restrictions before any database call.
                    try:
                        sql=decision['arguments']['sql']
                        intent=payload.get('business_intent') or {}
                        validation_error=business_sql_error(sql,intent)
                        if not validation_error:
                            scoped=apply_filters(sql,payload.get('analysis_filters'),intent.get('periods'),intent.get('time_field','order_purchase_timestamp'),intent.get('exclusions'))
                            if len(scoped)>12000:validation_error='查询范围展开后超过执行长度，请简化原始SQL并复用中间表，不能删除业务条件。'
                            else:decision['arguments']['sql']=scoped
                    except (ValueError,ParseError) as exc:validation_error=str(exc)
                if not validation_error:return decision
            if attempt == 2:
                raise PipelineStageError('query_validation','查询条件核对未通过，尚未执行：'+validation_error)
        raise ValueError(validation_error)

    def _decision_error(self, decision: dict[str, Any], tool_names: set[str]) -> str:
        action = str(decision.get("action") or "")
        if action not in self.skill.output_actions:
            return f"Skill不允许输出动作：{action}"
        if action == "clarify":
            clarification = decision.get("clarification")
            if not isinstance(clarification, dict) or len(clarification.get("options") or []) < 2:
                return "智能体返回的澄清信息不完整"
            return ""
        if action != "call_tool":
            return "智能体必须返回call_tool或clarify"
        tool_name = str(decision.get("tool_name") or "")
        if tool_name not in tool_names:
            return f"智能体选择了未提供的MCP工具：{tool_name}"
        if not isinstance(decision.get("arguments"), dict):
            return "MCP工具参数必须是JSON对象"
        return ""

    @staticmethod
    def _explicit_filter_error(
        decision: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        """用户明确写出Schema样例值时，SQL必须使用该值。"""
        if decision.get("action") != "call_tool":
            return ""
        if not str(decision.get("tool_name") or "").startswith("query_"):
            return ""
        sql = str((decision.get("arguments") or {}).get("sql") or "")
        if not sql:
            return "数据库工具调用缺少SQL参数"
        query = str(payload.get("query") or "")
        selected_fields = (
            (payload.get("retrieval") or {}).get("selected_fields") or []
        )
        matched_samples: list[tuple[str, str, str, bool]] = []
        for field in selected_fields:
            field_identity = " ".join(
                str(field.get(key) or "")
                for key in ("field_name", "field_label", "field_type")
            ).casefold()
            if any(
                token in field_identity
                for token in ("date", "time", "month", "year", "日期", "时间", "月份", "年度")
            ):
                continue
            if payload.get('business_intent') and field.get('field_name') in {'customer_state','order_status','product_category_name_english','product_category_name'}:continue
            samples = list(field.get("samples") or [])
            if len(samples) > 20:
                continue
            for sample in samples:
                value = str(sample).strip()
                if len(value) < 2:
                    continue
                if isinstance(sample, (int, float)) and not isinstance(sample, bool):
                    query_pattern = rf"(?<![\d.]){re.escape(value)}(?![\d.])"
                    if not re.search(query_pattern, query):
                        continue
                    pattern = rf"(?<![\w.]){re.escape(value)}(?![\w.])"
                else:
                    # A state such as AM must not match the letters in "name".
                    mentioned=(bool(re.search(r'(?<![A-Za-z0-9_])'+re.escape(value)+r'(?![A-Za-z0-9_])',query,re.I))
                               if re.fullmatch(r'[A-Za-z0-9_ -]+',value) else value.casefold() in query.casefold())
                    if not mentioned:
                        continue
                    escaped = re.escape(value.replace("'", "''"))
                    pattern = rf"'\s*{escaped}\s*'"
                matched_samples.append(
                    (
                        str(field.get("field_name") or ""),
                        value,
                        pattern,
                        not isinstance(sample, (int, float)) or isinstance(sample, bool),
                    )
                )

        # 同一段文本可能同时命中“侵权”和“侵权搬运”等重叠枚举，
        # 此时只校验信息更完整的最长值，避免把正确SQL误判为缺少短枚举。
        precise_samples: list[tuple[str, str, str, bool]] = []
        for candidate in matched_samples:
            _, value, _, is_text = candidate
            if is_text and any(
                other_is_text
                and len(other_value) > len(value)
                and value.casefold() in other_value.casefold()
                for _, other_value, _, other_is_text in matched_samples
            ):
                continue
            precise_samples.append(candidate)

        missing = [
            f"{field_name}={value}"
            for field_name, value, pattern, _ in precise_samples
            if not (field_name in {'customer_state','order_status','product_category_name_english'} and value in (payload.get('analysis_filters') or {}).get({'customer_state':'states','order_status':'order_statuses','product_category_name_english':'categories'}[field_name],[]))
            if not re.search(pattern, sql, flags=re.IGNORECASE)
        ]
        if missing:
            return "SQL遗漏用户明确给出的Schema取值：" + "、".join(dict.fromkeys(missing))
        return ""
