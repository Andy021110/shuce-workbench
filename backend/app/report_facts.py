"""A bounded catalogue of values calculated by the backend, with source references.

The language model writes prose and cites placeholders; it does not do arithmetic.
This checks numeric provenance, not the truth of arbitrary prose or causal claims.
"""
from __future__ import annotations

import json
from .category_labels import category_label
import re
from decimal import Decimal

from .result_profile import numeric

TOKEN = re.compile(r"\{\{fact:([a-zA-Z0-9_]+)\}\}")

LABELS = {'buyers':'买家数','returning_buyers':'本期历史老客数','returning_rate':'本期老客占比','reviewed_order_count':'有评价订单数','review_record_count':'评价记录数','avg_order_review_score':'订单级平均评分','union_orders':'至少购买一个品类的订单数','intersection_orders':'同时购买两个品类的订单数','period':'月份','month':'月份','orders':'订单数','order_count':'订单数','item_sales':'商品金额',
          'delivered':'可比较已送达数','late':'超时订单数','late_rate':'超时率','customer_state':'客户州',
          'category':'商品品类','previous_sales':'上月商品金额','current_sales':'本月商品金额','sales_change':'商品金额变化',
          'previous_delivered':'上月可比较已送达数','current_delivered':'本月可比较已送达数',
          'previous_late':'上月超时数','current_late':'本月超时数','new_late_orders':'新增超时数',
          'previous_late_rate':'上月超时率','current_late_rate':'本月超时率','orders_with_items':'有商品明细的订单数','item_rows':'商品明细条数','sales_per_order':'单均商品金额','items_per_order':'每单明细条数','avg_item_price':'平均明细价格'}

LABELS.update({'qty_jul': '7月商品明细条数', 'qty_aug': '8月商品明细条数', 'sales_jul': '7月商品金额', 'sales_aug': '8月商品金额', 'sales_delta': '商品金额增量', 'qty_delta': '商品明细条数变化', 'top5_share_of_total_delta': '前五商品占总增量比重', 'contribution_share': '增量贡献占比', 'delivery_group': '配送分组', 'review_answer_timestamp': '评价回复时间', 'review_creation_date': '评价创建日期', 'order_purchase_timestamp': '下单时间', 'order_estimated_delivery_date': '预计送达日期', 'order_delivered_customer_date': '实际送达时间', 'order_delivered_carrier_date': '交给承运商时间', 'late_days': '超时天数', 'product_id': '商品编号', 'order_id': '订单编号', 'customer_city': '客户城市', 'item_sales_per_order': '单均商品金额', 'review_score': '评价星级', 'review_comment_message': '评价正文', 'review_comment_title': '评价标题', 'reviewed_orders': '有评价订单数', 'low_score_orders': '低星订单数', 'low_score_rate': '低星订单占比', 'avg_price_jul': '7月平均明细价格', 'avg_price_aug': '8月平均明细价格', 'total_delta': '商品金额总增量', 'jul_qty': '7月商品明细条数', 'aug_qty': '8月商品明细条数', 'jul_price': '7月平均明细价格', 'aug_price': '8月平均明细价格'})
LABELS.update({'avg_item_sales_per_order': '单均商品金额', 'late_orders': '超时订单数', 'item_count': '商品明细条数', 'unreviewed_orders': '未评价订单数', 'low_star_orders': '低星订单数', 'low_star_rate': '低星订单占比', 'min_review_score': '最低评价星级', 'hb_item_sales': '健康美容商品金额'})

def small_integer(text):
    if text.isdigit():
        return int(text)
    digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if text in digits:
        return digits[text]
    if text.count('十')==1:
        a,b=text.split('十')
        if (not a or a in digits) and (not b or b in digits):
            return digits.get(a,1)*10+digits.get(b,0)
    return None


def factual_brief(contexts, facts):
    """Render a usable factual handoff even when model-written prose is rejected."""
    parts = ['## 查询事实', '以下表格和变化统计由后端整理；范围限于各条查询结果。']
    refs = {}
    def use(fact_id):
        fact = facts.get(fact_id)
        if not fact:
            return '—'
        refs[fact_id] = fact
        return fact['display']
    def escape(value):
        return str(value).replace('|','\\|').replace('\n',' ')
    for index,item in enumerate(source_items(contexts)):
        prefix=f's{index}'
        title=escape(item.get('title') or '查询结果')
        profile=item.get('result_profile') or {}
        columns=(item.get('columns') or [])[:8]
        rows=item.get('rows') or []
        if not columns:
            continue
        parts.append('### '+title)
        basis=item.get('analysis_basis') or {}
        if basis.get('verified'):
            parts.append('统计时间：'+basis['time_label']+'。'+('金额单位：BRL。' if basis.get('currency')=='BRL' else ''))
        scope=item.get('analysis_scope') or {}
        if scope:
            filters=scope.get('filters') or {}
            if scope.get('period_label'):parts.append('查询期间：'+scope['period_label']+'。')
            parts.append('订单状态：'+('、'.join(scope.get('statuses') or []) or ('全部' if scope.get('mode')=='monthly_review' else '按 SQL 条件'))+'；地区：'+('、'.join(filters.get('states') or []) or '未另限地区')+'；品类：'+('、'.join(category_label(c)+'（'+c+'）' for c in filters.get('categories') or []) or '未另限品类')+'。'+scope.get('note',''))
        if prefix+'_rows' in facts:
            parts.append(f"完整 SQL 结果共 {use(prefix+'_rows')} 行；下表展示查询顺序前 {min(5,len(rows))} 行，其他行仍可在原结果中查看。")
        else:
            parts.append('本节来自已保存的行预览，不能据此推断完整结果。')
        table_lines=['| '+' | '.join(escape(LABELS.get(c,c)) for c in columns)+' |', '| '+' | '.join('---' for c in columns)+' |']
        for r,row in enumerate(rows[:5]):
            values=[]
            for c,field in enumerate(columns):
                value=row.get(field)
                values.append(use(f'{prefix}_r{r}_c{c}') if numeric(value) else escape(category_label(str(value))+'（'+str(value)+'）' if 'category' in field and category_label(str(value))!=str(value) else value) if value is not None else '—')
            table_lines.append('| '+' | '.join(values)+' |')
        parts.append('\n'.join(table_lines))
        comparisons=(item.get('review_context') or {}).get('target_comparison') or []
        if comparisons:
            parts.append('#### 本月目标对比\n目标由用户为本次复盘填写；不是系统推测值或已审批预算。差额为实际减目标，超时率越低越好。')
            target_lines=['| 指标 | 实际 | 目标 | 差额 | 完成率 | 判断 |','| --- | --- | --- | --- | --- | --- |']
            for t,comparison in enumerate(comparisons):
                values=[use(f'{prefix}_target{t}_{key}') if comparison.get(key) is not None else '—' for key in ('actual','target','gap','achievement_rate')]
                target_lines.append('| '+escape(comparison['label'])+' | '+' | '.join(values)+' | '+comparison['status']+' |')
            parts.append('\n'.join(target_lines))
        for n,stat in enumerate(profile.get('numeric') or []):
            if re.search(r'change|^new_|delta|变化|增量|增减',stat['field'],re.I) and f'{prefix}_n{n}_positive' in facts:
                parts.append(f"{LABELS.get(stat['field'],stat['field'])}列：大于零 {use(f'{prefix}_n{n}_positive')} 行，等于零 {use(f'{prefix}_n{n}_zero')} 行，小于零 {use(f'{prefix}_n{n}_negative')} 行。")
        for c,field in enumerate(columns):
            delta=f'{prefix}_change_c{c}'
            if delta in facts:
                parts.append(f"{LABELS.get(field,field)}：后一期减前一期 {use(delta)}" + (f"；相对变化 {use(f'{prefix}_change_rate_c{c}')}。" if f'{prefix}_change_rate_c{c}' in facts else '。'))
    return '\n\n'.join(parts), list(refs.values())


def source_items(contexts):
    sources = {}
    for key in ("selected_tables", "recent_result"):
        try:
            parsed = json.loads(contexts.get(key) or "[]")
        except (ValueError, TypeError):
            continue
        for item in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(item, dict) and item.get("task_id"):
                sources[str(item["task_id"])] = item
        if sources:
            break  # An explicit selection excludes unrelated recent results.
    return list(sources.values())


def formatted(value, field=""):
    value = Decimal(str(value))
    if field == "items_per_order":
        return f"{value:.4f}"
    if field in {"top5_share_of_total_delta","contribution_share"} or re.search(r"rate|ratio|percent|率|占比", field, re.I):
        return f"{value * 100:.2f}%"
    if re.search(r"sales|price|payment|amount|金额|销售额", field, re.I):
        return f"{value:,.2f}"
    return f"{value:,f}".rstrip("0").rstrip(".") if value != value.to_integral() else f"{value:,.0f}"


def build_facts(contexts):
    facts = {}
    items = source_items(contexts)
    source_limit = max(1, 600 // max(1, len(items)))
    for index, item in enumerate(items):
        start_count = len(facts)
        profile = item.get("result_profile") or {}
        columns = item.get("columns") or []
        rows = item.get("rows") or []
        complete = profile.get("coverage") == "full_result"
        prefix = f"s{index}"

        def add(key, label, value, field="", scope="完整 SQL 结果", display=None):
            if value is None or len(facts) - start_count >= source_limit:
                return
            fact_id = prefix + "_" + key
            facts[fact_id] = {"id": fact_id, "source_task_id": item["task_id"],
                "source_title": item.get("title") or "查询结果", "label": label,
                "value": str(value), "display": display or formatted(value, field), "scope": scope, "field": field, "filters": (item.get("analysis_scope") or {}).get("filters") or (item.get("review_context") or {}).get("filters") or {}}

        if complete:
            add("rows", "结果行数", profile.get("row_count"))
            for n, stat in enumerate(profile.get("numeric") or []):
                field = stat["field"]
                for name in ("sum", "min", "max"):
                    if stat.get(name) is not None:
                        label = f"{field} · { {'sum':'结果列总和','min':'最小值','max':'最大值'}[name]}"
                        if name in ("min", "max"):
                            label += " · " + json.dumps(stat.get(name + "_rows") or [], ensure_ascii=False)
                        add(f"n{n}_{name}", label, stat[name], field)
                signs = stat.get("sign_counts")
                if not signs and len(rows) == profile.get("row_count"):
                    values = [r.get(field) for r in rows if numeric(r.get(field))]
                    signs = {"positive": sum(v > 0 for v in values), "zero": sum(v == 0 for v in values),
                             "negative": sum(v < 0 for v in values)}
                if signs:
                    for sign, label in (("positive", "大于零的行数"), ("zero", "等于零的行数"), ("negative", "小于零的行数")):
                        add(f"n{n}_{sign}", field + " · " + label, signs[sign])
                    add(f"n{n}_nonnegative", field + " · 大于等于零的行数", signs["positive"] + signs["zero"])
            for field, value in (profile.get("metric_totals") or {}).get("values", {}).items():
                add("total_" + field, "标准口径整体值 · " + field, value, field)
        for r, row in enumerate(rows[:50]):
            identity = " / ".join(str(row.get(c)) for c in columns if isinstance(row.get(c), str))[:150]
            for c, field in enumerate(columns[:16]):
                if numeric(row.get(field)):
                    add(f"r{r}_c{c}", f"{identity or '结果行'} · {field}", row[field], field,
                        scope=f"结果第 {r+1} 行")
                elif isinstance(row.get(field), str) and (field in {'order_id','product_id','seller_id','delivery_group','period','month','customer_city','customer_state'} or re.search(r'_date$|_timestamp$|_at$',field)):
                    add(f"r{r}_c{c}", f"{identity or '结果行'} · {field}", row[field], field,
                        scope=f"结果第 {r+1} 行", display=row[field][:200])
        for t, comparison in enumerate((item.get('review_context') or {}).get('target_comparison') or []):
            field = comparison['metric']
            for key in ('actual','target','gap','achievement_rate'):
                value = comparison.get(key)
                if value is None:
                    continue
                display = (f"{Decimal(value)*100:+.2f} 个百分点" if key=='gap' and field=='late_rate' else None)
                add(f'target{t}_{key}', comparison['label']+' · '+{'actual':'本月实际','target':'用户填写目标','gap':'实际减目标','achievement_rate':'目标完成率'}[key],
                    value, 'rate' if key=='achievement_rate' else field,
                    scope='本次复盘用户目标；'+comparison['direction']+'；'+comparison['status'],display=display)
        # Two-period comparisons: actual result values, never fabricated missing months.
        if (complete and len(rows) == 2 and columns
                and len({r.get(columns[0]) for r in rows}) == 2
                and all(re.fullmatch(r"\d{4}-\d{2}", str(r.get(columns[0], ""))) for r in rows)
                and all(r.get(c) is None or numeric(r.get(c)) for r in rows for c in columns[1:])):
            before, after = sorted(rows, key=lambda r: str(r[columns[0]]))
            for c, field in enumerate(columns[1:], 1):
                if not (numeric(before.get(field)) and numeric(after.get(field))):
                    continue
                a, b = Decimal(str(before[field])), Decimal(str(after[field]))
                period_label=f"{after[columns[0]]} 对比 {before[columns[0]]}"
                add(f"change_c{c}", field + " · " + period_label + " · 差值", b-a, field,
                    display=f"{(b-a)*100:.2f} 个百分点" if re.search(r"rate|ratio|percent|率|占比",field,re.I) else None)
                if a != 0:
                    change = (b-a)/abs(a)
                    add(f"change_rate_c{c}", field + " · " + period_label + " · 相对变化率", change, "rate")
    return facts


def validate_and_render(payload, facts, schema_catalog):
    """Reject uncited business numbers before exposing a model-written answer."""
    output = dict(payload)
    refs = []
    for key in ("title", "answer", "markdown"):
        text = str(payload.get(key) or "")
        for fact_id in TOKEN.findall(text):
            if fact_id not in facts:
                raise ValueError("数值引用不存在：" + fact_id)
            if fact_id not in refs:
                refs.append(fact_id)
        without_refs = TOKEN.sub("", text)
        for fact in facts.values():
            without_refs = without_refs.replace(fact['source_task_id'],fact['source_title'])
        # Calendar dates and ordered-list markers are not business measurements.
        without_refs = re.sub(r"\d{4}\s*[-年/]\s*\d{1,2}(?:\s*[-月/]\s*\d{1,2}\s*日?)?\s*月?|\d{4}\s*年|\d{1,2}\s*[-—至到]\s*\d{1,2}\s*月|\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?", "", without_refs)
        without_refs = re.sub(r"(?m)^#{1,6}\s*", "", without_refs)
        without_refs = re.sub(r"(?m)^\s*\d+[.)、]\s*|(?<![A-Za-z])P[0-3](?!\d)|\bv\d+\.\d+\b", "", without_refs)
        # A top-N selection describes how to inspect rows, not the population size.
        # '共有十个州下降' remains a measured claim and must cite a fact.
        without_refs = re.sub(r"(?:排名前|降幅前|涨幅前|下降最多的|上升最多的|前)[零一二三四五六七八九十百两\d]+个(?:品类|州|地区|渠道|商品|商家|城市)", "", without_refs)
        without_refs = re.sub(r"(?i)(?<![A-Za-z0-9_])top\s*\d{1,2}(?!\d)", "", without_refs)
        enumeration=r"([A-Z]{2}(?:\s*[、与和及]\s*[A-Z]{2})+)\s*([零一二三四五六七八九十两\d]+)个州"
        for match in list(re.finditer(enumeration,without_refs)):
            states=set(re.findall(r'[A-Z]{2}',match[1]))
            if (small_integer(match[2])==len(states)
                    and all(any(re.match(re.escape(state)+r'(?: / | · )',f['label']) for f in facts.values()) for state in states)):
                without_refs=without_refs.replace(match[0],'')
        # A single explicitly selected dimension is a verifiable scope label,
        # not an inferred population count. Never exempt this for an unscoped source.
        for match in list(re.finditer(r'([A-Z]{2})(?:一|1)个州',without_refs)):
            if facts and all(f.get('filters',{}).get('states')==[match[1]] for f in facts.values()):
                without_refs=without_refs.replace(match[0],match[1])
        for match in list(re.finditer(r'([A-Za-z_]+)(?:一|1)个品类',without_refs)):
            if facts and all(f.get('filters',{}).get('categories')==[match[1]] for f in facts.values()):
                without_refs=without_refs.replace(match[0],match[1])
        # Verify deictic selection counts against the one complete selected product
        # table. Never exempt an arbitrary population or growth count.
        product_owners={f['source_task_id'] for f in facts.values() if f.get('field')=='product_id'}
        if len(product_owners)==1:
            owner=next(iter(product_owners))
            product_values={f['value'] for f in facts.values() if f.get('field')=='product_id' and f['source_task_id']==owner}
            count_fact=next((f for f in facts.values() if f['source_task_id']==owner and f['id'].endswith('_rows') and Decimal(f['value'])==len(product_values)),None)
            for match in list(re.finditer(r'(?:这|所选|选中的)([零一二三四五六七八九十两\d]+)个商品',without_refs)):
                if count_fact and small_integer(match[1])==len(product_values):
                    without_refs=without_refs.replace(match[0],'所选商品')
                    if count_fact['id'] not in refs:refs.append(count_fact['id'])
        owner_count=len({f['source_task_id'] for f in facts.values()})
        for match in list(re.finditer(r'(?:基于|综合|所选|选中的)([零一二三四五六七八九十两\d]+)份(?:结果|来源|查询)',without_refs)):
            if small_integer(match[1])==owner_count:
                without_refs=without_refs.replace(match[0],'所选查询来源')
        # Product identifiers such as home_appliances_2 are labels, not measurements.
        for label in re.findall(r"[A-Za-z][A-Za-z_]+\d+[A-Za-z_0-9]*", without_refs):
            if any(label in fact['label'] for fact in facts.values()):
                without_refs = without_refs.replace(label, '')
        unmatched = re.search(r"\d+(?:\.\d+)?|[零一二三四五六七八九十百千万两]+(?:个州|个品类|个商品|个商家|个城市|笔订单|单订单|元|个百分点)", without_refs)
        if unmatched:
            snippet = without_refs[max(0,unmatched.start()-20):unmatched.end()+20]
            raise ValueError(f"正文存在未引用的业务数值（{snippet}）；所有计数、金额、比率、差值必须使用 {{{{fact:id}}}}，不允许自行填写或计算。")
        if "{{fact:" in TOKEN.sub("", text):
            raise ValueError("数值引用格式错误，应为 {{fact:id}}")
        output[key] = TOKEN.sub(lambda match: facts[match[1]]["display"], text)
    body = output.get("answer", "") + "\n" + output.get("markdown", "")
    # The result's absent columns are not proof that the authorized database lacks them.
    available = " ".join(str(t) for t in schema_catalog)
    aliases = {"品类": "product_category_name", "价格": "price", "运费": "freight_value",
               "交给承运商时间": "order_delivered_carrier_date", "下单时间": "order_purchase_timestamp",
               "最迟发货时间": "shipping_limit_date", "预计送达": "order_estimated_delivery_date"}
    if all(field in available for field in ('order_purchase_timestamp','order_delivered_carrier_date')):
        aliases.update({'发货时效':'order_delivered_carrier_date','发货时长':'order_delivered_carrier_date'})
    if all(field in available for field in ('order_delivered_customer_date','order_delivered_carrier_date')):
        aliases['物流时效']='order_delivered_carrier_date'
    if all(field in available for field in ('order_id','price')):
        aliases['客单价']='price'
    for sentence in re.split(r"[。；\n]", body):
        if (re.search(r'(?:为零|为\s*0|归零|零值)',sentence)
                and re.search(r'属于|说明|表示|代表|意味着|等于',sentence)
                and re.search(r'数据缺失|数据缺少|缺失数据',sentence)
                and not re.search(r'不等于|不能|不可|不代表|并非|不是',sentence)):
            raise ValueError('统计计数为零不能被解释为数据缺失；缺失值与零值必须区分。')
        if not re.search(r"没有|缺少|缺失|不包含|未提供", sentence):
            continue
        if re.search(r"当前结果|本次结果|结果表|本轮结果|(?:所选|选中|已选)(?:的)?结果|现有结果|尚未查询|未查询", sentence):
            continue
        negative_spans = re.findall(r"(?:没有|缺少|缺失|不包含|未提供)([^。；，,\n]{0,100})", sentence)
        for alias, column in aliases.items():
            if column in available and any(alias in span for span in negative_spans):
                raise ValueError(f"数据目录存在{alias}；应说明当前结果未查询该维度，不能称数据源没有。")
    output["fact_references"] = [facts[f] for f in refs]
    return output
