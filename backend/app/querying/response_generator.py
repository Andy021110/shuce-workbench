from __future__ import annotations

from typing import Any
import json
import re

from ..config import Settings, settings
from ..errors import PipelineStageError
from ..model_client import ModelClient
from .models import SqlExecution


class ResponseGenerator:
    """生成问答内容和查询结果说明。"""

    def __init__(
        self,
        model_client: ModelClient,
        config: Settings | None = None,
    ) -> None:
        self.model_client = model_client
        self.table_row_limit = max(1, (config or settings).context_table_row_limit)

    def finalize(
        self,
        query: str,
        execution: SqlExecution,
        schema_context: str,
        analysis_context: str,
    ) -> dict[str, Any]:
        if execution.total_rows==0:
            return {'valid':True,'reason':'仅陈述执行范围内的空结果','title':'查询结果（0行）',
                    'answer_checks':[{'question':q,'status':'insufficient_evidence','answer':'本次查询为空，不能据此确定业务原因或作总体判断。'} for q in self.decision_questions(query)],
                    'analysis':'本次SQL返回0行。请核对范围、日期与SQL条件；仅凭空结果不能证明业务数据不存在，也不能生成金额或利润结论。'}
        checks = self.decision_questions(query)
        system = """你是查询结果整理器。检查结果能否回答原始业务问题，并生成简短标题和说明。
原始问题可能同时要求查数和业务判断，不能因独立查询改写丢弃判断。对每个回答检查项逐项回应：能由当前结果直接支持的写 answered，不能支持的写 insufficient_evidence，说明需要核对什么。商品明细条数不能改称实物件数，平均明细价格不能改称实际每件价格；请纠正原问题中的不准确术语。平均价格上升不能证明同款涨价，小样本比例不能证明因果，按实际时间先后解释个案不能外推总体。
只能使用完整结果统计和行预览中真实存在的数值。result_profile.coverage 为 full_result 时，数值汇总、极值所在行和期间汇总覆盖 SQL 完整结果，可据此解释整个查询范围；不得再声称仅能分析前若干行。行预览仅用于理解字段，不得据此推断整体。数值列之和不一定是业务指标总量；比率和平均值不得直接加总，缺失期间不得视为零。没有汇总支持的维度、因果和未查询范围必须说明不足。只返回JSON：
{"valid":true,"reason":"...","title":"...","analysis":"...","answer_checks":[{"question":"逐字复制检查项","status":"answered|insufficient_evidence","answer":"直接判断及依据或证据不足说明"}]}。"""
        user = (
            f"原始问题：{query}\n回答检查项：{json.dumps(checks,ensure_ascii=False)}\nSQL：{execution.sql}\n列：{execution.columns}\n"
            f"完整结果统计 result_profile：{json.dumps(execution.result_profile, ensure_ascii=False)}\n查询总行数：{execution.total_rows}，行预览截断：{execution.truncated}。\n行预览：{execution.rows[: self.table_row_limit]}\nSchema：{schema_context}\n"
            f"用户保存的分析表格：{analysis_context or '无'}"
        )
        try:
            payload = self.model_client.chat_json(system, user)
            contradiction = bool(re.search(r'与.{0,25}(?:要求|问题|口径).{0,25}(?:冲突|不符)|并非.{0,25}(?:问题|要求)|不能作为.{0,30}(?:答案|口径)|SQL.{0,20}(?:遗漏|写错)|结果中另含.{0,30}排除',str(payload.get('analysis') or '')))
            return {
                "answer_checks": self.checked_answers(checks, payload.get("answer_checks")),
                "valid": bool(payload.get("valid", True)) and not contradiction,
                "reason": "查询结果与问题口径冲突，未按完成交付。" if contradiction else str(payload.get("reason") or "结果检查通过"),
                "title": str(payload.get("title") or "查询结果"),
                "analysis": str(payload.get("analysis") or f"查询结果共{execution.total_rows if execution.total_rows is not None else len(execution.rows)}行，当前预览{len(execution.rows)}行。"),
            }
        except RuntimeError as exc:
            raise PipelineStageError("result_analysis", str(exc)) from exc

    @staticmethod
    def decision_questions(query: str) -> list[str]:
        clauses = re.split(r'[。！？；;\n]', query)
        return list(dict.fromkeys(c.strip(' ，,') for c in clauses
            if re.search(r'是否|能否|能不能|可否|说明什么|解释|因果|导致|足够|站得住|判断|涨价|先查|优先|先后|矛盾', c)))[-6:]

    @staticmethod
    def checked_answers(questions, raw):
        entries = raw if isinstance(raw, list) else []
        result=[]
        for question in questions:
            match=next((e for e in entries if isinstance(e,dict) and e.get('question')==question and e.get('status') in {'answered','insufficient_evidence'} and str(e.get('answer') or '').strip()),None)
            result.append({'question':question,'status':match['status'] if match else 'not_answered',
                'answer':str(match['answer'])[:1600] if match else '本次已返回查询数据，但该判断尚未得到完整回应。请继续核对相关维度，不能仅凭当前数值直接确认原因。'})
        return result
