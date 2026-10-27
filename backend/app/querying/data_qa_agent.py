from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Callable

from ..errors import PipelineStageError
from ..mcp_runtime import LocalMcpClient
from ..model_client import ModelClient
from ..models import AnalysisReport, VisualizationSpec
from ..skills import SkillDefinition
from ..report_facts import build_facts, validate_and_render, source_items, factual_brief, LABELS


@dataclass
class DataQaResult:
    action: str
    answer: str
    report: AnalysisReport | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class DataQaAgent:
    """分析已有数据，并按需生成Markdown报告和图表配置。"""

    def __init__(
        self,
        model_client: ModelClient,
        mcp_client_factory: Callable[[dict[str, Any]], LocalMcpClient],
        skill: SkillDefinition,
    ) -> None:
        self.model_client = model_client
        self.mcp_client_factory = mcp_client_factory
        self.skill = skill

    def run(
        self,
        query: str,
        contexts: dict[str, str],
        access_scope: dict[str, Any],
    ) -> DataQaResult:
        client = self.mcp_client_factory(access_scope)
        tools = [
            tool for tool in client.list_tools()
            if self._tool_allowed(str(tool.get("name") or ""))
        ]
        source_catalog = self._source_catalog(contexts)
        facts = build_facts(contexts)
        brief, brief_refs = factual_brief(contexts, facts)
        # Short aliases are selection handles, not invented numerical facts. Every
        # alias maps back to the immutable original reference before rendering.
        grouped: dict[str,list] = {}
        for key,value in facts.items(): grouped.setdefault(value['source_task_id'],[]).append((key,value))
        selected=[]
        for items in grouped.values():
            items.sort(key=lambda item:(0 if '_change' in item[0] or '_target' in item[0] else 1 if re.search(r'_r[0-4]_c',item[0]) else 2))
            selected.extend(items[:60])
        model_facts={f'F{i:03d}':value for i,(_,value) in enumerate(selected,1)}
        schema_catalog = json.loads(contexts.get("schema_catalog") or "[]")
        schema_text=json.dumps(schema_catalog,ensure_ascii=False)
        derivations=''
        if all(field in schema_text for field in ('order_purchase_timestamp','order_delivered_carrier_date','order_delivered_customer_date')):
            derivations+='目录已有下单、交给承运商和实际送达时间，可以继续查询交运前耗时和运输耗时；不得称整个数据源没有发货时效。'
        if all(field in schema_text for field in ('order_id','price')):
            derivations+='已有订单与商品金额，可进一步查询单均商品金额；不要称整个数据源缺少客单价。'
        system = (
            "你是数据问答智能体。严格执行下面的Skill，并只返回JSON。\n\n"
            "业务术语：商品金额不是净收入或利润；每单明细条数不能改称商品件数。所选范围内只有某州时，不得推断其他州是否正常。\n"
            "事实优先级：后端完整统计和标准指标定义高于历史助手解读；过去助手的因果判断不能当作数据证据。"
            "没有活动、促销或实验字段时，禁止把峰值归因为促销、黑五、营销或运营措施；不得使用受促销拉动、说明订单集中于促销日等措辞。"
            "末尾补充不构成因果的声明不能修正正文的归因。只描述统计变化，原因不明时直说数据不能确定。\n"
            "业务数值规则：所有计数、金额、比率、差值、极值必须使用 fact_catalog 中的 {{fact:id}} 占位符，"
            "禁止自行写业务数字或自行心算，包括州的增加/持平/下降数量。没有相应事实时略去该数值。结构化报告只通过fact_ids选择事实，正文不需要占位符。"
            "日历日期可直接写，例如2018年8月。严禁把不同时间依据的数据当同一口径比较。"
            "不要在正文输出task_id等内部标识；来源用结果标题描述。只引用目录中逐字存在的fact ID，严禁按行号猜测ID。"
            "行数、品类数量也必须引用事实。优先级用中文或P标记；报告应简洁，优先解释重要变化。"
            "生成报告时，后端会自动附上数值事实表和变化统计。observations、hypotheses、actions只写定性文字，"
            "不要重复数字表格，不要写品类数量或零值；如需数值仍必须使用事实引用。"
            "schema_catalog 是当前用户可见的完整数据目录；结果表没有某字段，不等于数据源没有。"
            "目录已存在的维度应建议继续查询，不要要求用户补交。不要把模型判断标为已证实或已核验。\n"
            "计数为零不等于缺失数据；null/不可计算与零值必须区分，不能把某州零笔可比较已送达订单称为数据缺失。"
            f"{derivations}\n"
            f"{self.skill.instructions}\n\n"
            "可用前端展示工具：\n"
            f"{json.dumps(tools, ensure_ascii=False)}\n\n"
            "输出格式：\n"
            "普通回答：{\"action\":\"answer\",\"answer\":\"...\",\"tool_calls\":[]}\n"
            "分析报告必须使用结构化格式，不填写markdown。observations包含一至八条，每条只写定性观察，fact_ids逐字选择一至二十条事实ID；后端负责插入数值。hypotheses只写待验证假设，actions只写核查步骤。各项不写业务数字。\n"
            "分析报告：{\"action\":\"report\",\"answer\":\"一句话定性摘要\","
            "\"title\":\"...\",\"observations\":[{\"interpretation\":\"...\",\"fact_ids\":[\"逐字复制的ID\"]}],\"hypotheses\":[\"...\"],\"actions\":[\"...\"],"
            "\"tool_calls\":[{\"name\":\"build_bar_chart\",\"arguments\":{...}}]}"
        )
        user = json.dumps(
            {
                "query": query,
                "available_data_sources": [{k:v for k,v in source.items() if k not in {"row_count","rows","covered_rows","input_rows","excluded_rows"}} for source in source_catalog.values()],
                "fact_catalog": {key:{k:value[k] for k in ('label','display','source_task_id','scope')} for key,value in model_facts.items()},
                "context": {
                    "result_scopes":[{k:item.get(k) for k in ('task_id','title','query','analysis_basis','analysis_scope','metric_contracts')} for item in source_items(contexts)],
                    "schema_catalog":schema_catalog,
                },
            },
            ensure_ascii=False,
        )
        try:
            for attempt in range(2):
                payload = self.model_client.chat_json(system, user)
                if not isinstance(payload,dict): payload={'action':'report','observations':None}
                try:
                    payload = self._structured_payload(payload, {**facts,**model_facts})
                    # Ordinary answers use the same valid F handles as reports.
                    # Map only advertised aliases; never guess an invalid ID.
                    for field_name in ('answer','markdown','title'):
                        if isinstance(payload.get(field_name),str):
                            payload[field_name]=re.sub(r'\{\{fact:(F[0-9]+)\}\}',lambda m:'{{fact:'+model_facts[m[1]]['id']+'}}' if m[1] in model_facts else m[0],payload[field_name])
                    structured_issue = ""
                except (ValueError, TypeError) as exc:
                    structured_issue = str(exc)
                issue = structured_issue or self._unsupported_causal_claim(payload)
                if not issue:
                    try:
                        payload = validate_and_render(payload, facts, schema_catalog)
                    except ValueError as exc:
                        issue = str(exc)
                if not issue:
                    result = self._execute(payload, client, source_catalog)
                    if result.report and facts:
                        model_markdown=result.report.markdown
                        lines=model_markdown.splitlines()
                        if lines and lines[0].strip()=='# '+result.report.title:
                            model_markdown='\n'.join(lines[1:]).strip()
                        result.report.markdown = '## 经营解读与建议\n\n' + model_markdown + '\n\n' + brief
                        refs = {r['id']:r for r in [*brief_refs,*result.report.fact_references]}
                        result.report.fact_references = list(refs.values())
                    return result
                if attempt == 0:
                    user += "\n输出校验失败：" + issue + " 请用结构化报告格式重写。fact_ids只能逐字复制fact_catalog的F编号，不得自己编写。interpretation、hypotheses、actions不写业务数字；不得作经营原因归因。"
            if facts:
                report = AnalysisReport(title='经营复盘事实报告',
                    markdown='模型文字未通过数值引用或数据范围校验。以下仅提供后端整理的查询事实，未包含模型经营解读；可重试生成解读。\n\n'+brief,
                    fact_references=brief_refs,generation_mode='facts_only',validation_feedback=[self._validation_feedback(issue)])
                return DataQaResult(action='report',answer='查询事实已整理；模型经营解读尚未通过校验。',report=report)
            raise ValueError("文字解读未通过数值引用或数据范围校验；已完成的查询仍可查看，请重试。原因：" + issue)
        except PipelineStageError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise PipelineStageError("qa_answer", str(exc)) from exc

    @staticmethod
    def _structured_payload(payload, facts):
        """Render model-selected evidence on the server; never infer or repair numbers."""
        if 'observations' not in payload:
            return payload  # Compatibility for ordinary answers and archived model fixtures.
        observations = payload['observations']
        if not isinstance(observations, list) or not 1 <= len(observations) <= 8:
            raise ValueError('报告需要包含有事实依据的观察')
        lines = ['### 观察与依据']
        for observation in observations:
            if not isinstance(observation, dict): raise ValueError('报告观察格式无效')
            text = str(observation.get('interpretation') or '').strip()
            ids = observation.get('fact_ids')
            if not text or len(text) > 1200 or not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                raise ValueError('每条观察需要定性说明和一至二十条事实引用')
            if any(not isinstance(fid, str) or fid not in facts for fid in ids):
                raise ValueError('数值引用不存在，请逐字复制目录中的事实ID')
            lines.append('- ' + text)
            lines.extend('  - ' + DataQaAgent._evidence_label(facts[fid]) + '：{{fact:' + facts[fid]['id'] + '}}' for fid in dict.fromkeys(ids))
        for field, heading in [('hypotheses','待验证假设'),('actions','建议核查步骤')]:
            values=payload.get(field,[])
            if not isinstance(values,list) or len(values)>8 or any(not isinstance(t,str) or len(t)>1200 for t in values):
                raise ValueError('报告建议格式无效')
            lines.extend(['', '### '+heading, *('- '+t for t in values if t.strip())])
        return {**payload,'action':'report','markdown':'\n'.join(lines)}

    @staticmethod
    def _evidence_label(fact):
        key=fact['id']
        field=fact.get('field','')
        if '_change_rate_' in key:
            return LABELS.get(fact['label'].split(' · ')[0],'指标')+'相对变化率'
        if '_change_' in key: return LABELS.get(field,'指标')+'变化'
        if key.endswith('_rows'): return '结果行数'
        return LABELS.get(field,'统计依据')

    @staticmethod
    def _validation_feedback(issue):
        if '引用不存在' in issue or '引用格式' in issue:
            return '部分数值引用未能对应到已保存结果。可先查看事实表并人工补充结论，或重试生成。'
        if '未引用' in issue:
            return '部分业务数值没有引用已保存的统计依据。已保留事实表，可人工补充或重试生成。'
        if '数据目录' in issue:
            return '解读对数据是否可用的说明与当前目录不一致。可查看已有字段后继续查询。'
        if '零不能' in issue:
            return '解读混淆了零值与数据缺失。事实表保留真实空值和计数，可先据此核对。'
        if '原因缺乏证据' in issue:
            return '解读中的业务原因缺少证据。请将原因作为待验证假设，或人工补充验证结果。'
        return '模型解读未通过数值或范围检查。原始查询仍可使用，可查看事实表或人工补充报告。'

    @staticmethod
    def _unsupported_causal_claim(payload):
        text="\n".join(str(payload.get(k) or "") for k in ("answer","markdown"))
        for sentence in re.split(r"[。！？\n]",text):
            if re.search(r"不能|无法|不构成|未提供|没有|不足以|不可|不应",sentence): continue
            if re.search(r"(?:受|因|由).{0,40}(?:促销|营销|活动|黑五|黑色星期五).{0,30}(?:拉动|带动|导致|推动|影响|造成)",sentence):
                return "经营原因缺乏证据：统计结果不能证明促销或营销导致订单变化。"
            if re.search(r"(?:说明|证明|表明).{0,40}(?:促销日|营销活动|促销活动)",sentence):
                return "经营原因缺乏证据：订单统计不能证明订单集中于促销日或营销活动。"
        return ""

    def _execute(
        self,
        payload: dict[str, Any],
        client: LocalMcpClient,
        source_catalog: dict[str, dict[str, Any]],
    ) -> DataQaResult:
        action = str(payload.get("action") or "")
        if action not in self.skill.output_actions:
            raise ValueError(f"问答Skill不支持输出动作：{action or '空'}")

        answer = str(payload.get("answer") or "").strip()
        raw_calls = payload.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ValueError("tool_calls必须是数组")

        # A presentation tool call implies a report even if the model labels its envelope as an answer.
        # Every tool, source and field is still validated below before execution.
        if action == "answer" and raw_calls and "report" in self.skill.output_actions:
            action = "report"
            payload = {
                **payload,
                "title": payload.get("title") or "数据分析报告",
                "markdown": payload.get("markdown") or answer,
            }

        if action == "answer":
            if raw_calls:
                raise ValueError("普通回答不能调用前端展示工具")
            if not answer:
                raise ValueError("普通回答缺少answer")
            return DataQaResult(action="answer", answer=answer)


        title = str(payload.get("title") or "").strip()
        markdown = str(payload.get("markdown") or "").strip()
        if not title or not markdown:
            raise ValueError("分析报告缺少title或markdown")

        traces: list[dict[str, Any]] = []
        visualizations: list[VisualizationSpec] = []
        chart_feedback: list[str] = []
        if len(raw_calls)>self.skill.max_tool_calls:
            chart_feedback.append('图表数量超过限制，已保留允许数量内的有效图表。')
        for index, raw_call in enumerate(raw_calls[:self.skill.max_tool_calls], start=1):
            try:
                if not isinstance(raw_call,dict): raise ValueError('图表格式无效')
                name = str(raw_call.get('name') or '')
                arguments = raw_call.get('arguments') or {}
                if not self._tool_allowed(name) or not isinstance(arguments,dict):
                    raise ValueError('图表工具不合法')
                self._validate_chart_source(arguments, source_catalog)
                result = client.call_tool(name, arguments)
                visualization = VisualizationSpec.model_validate(result)
            except (RuntimeError, ValueError, TypeError, KeyError):
                chart_feedback.append('部分图表未通过来源或参数检查，已保留校验通过的正文和原始结果表。')
                continue
            visualizations.append(visualization)
            traces.append({'call_index':index,'tool':name,'arguments':arguments,'result':visualization.model_dump(mode='json')})

        report = AnalysisReport(
            title=title,
            markdown=markdown,
            visualizations=visualizations,
            fact_references=payload.get("fact_references") or [],
            validation_feedback=list(dict.fromkeys(chart_feedback)),
        )
        return DataQaResult(
            action="report",
            answer=answer or title,
            report=report,
            tool_calls=traces,
        )

    def _tool_allowed(self, name: str) -> bool:
        return any(fnmatch(name, pattern) for pattern in self.skill.allowed_tools)

    @staticmethod
    def _source_catalog(contexts: dict[str, str]) -> dict[str, dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {}
        for key in ("recent_result", "selected_tables"):
            raw = contexts.get(key) or ""
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict) or not item.get("task_id"):
                    continue
                task_id = str(item["task_id"])
                catalog[task_id] = {
                    "task_id": task_id,
                    "title": str(item.get("title") or "查询结果"),
                    "query": str(item.get("query") or ""),
                    "columns": [str(column) for column in item.get("columns") or []],
                    "row_count": int(item.get("row_count") or len(item.get("rows") or [])),
                    "coverage": (item.get("result_profile") or {}).get("coverage", "preview_only"),
                    "row_preview_only": True,
                }
                for derived in item.get("summary_sources") or []:
                    if derived.get("task_id") == task_id+":summary:time" and derived.get("coverage")=="full_result_summary":
                        catalog[derived["task_id"]] = derived
        return catalog

    @staticmethod
    def _validate_chart_source(
        arguments: dict[str, Any],
        source_catalog: dict[str, dict[str, Any]],
    ) -> None:
        task_id = str(arguments.get("source_task_id") or "")
        source = source_catalog.get(task_id)
        if not source:
            raise ValueError(f"图表引用了不可用的数据来源：{task_id or '空'}")
        columns = set(source["columns"])
        category = str(arguments.get("category_field") or "")
        value = str(arguments.get("value_field") or "")
        missing = [field for field in (category, value) if field not in columns]
        if missing:
            raise ValueError(f"图表字段不在查询结果中：{', '.join(missing)}")
