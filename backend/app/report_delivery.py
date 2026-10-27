"""Presentation constraints for reports; no model arithmetic or source substitution."""
import re
from .report_facts import small_integer


def preferences(query):
    compact=bool(re.search(r'一页|简短|摘要|短版|精简',query))
    result={'compact':compact,'observations':5 if compact else 8,'actions':3 if compact else 8,
            'report_requested':bool(re.search(r'报告|一页|图表|柱状图|对比图|饼图',query))}
    for key,words in [('observations','结论|观察|发现'),('actions','动作|建议|行动')]:
        match=re.search(r'(?:最多|不超过|至多)\s*([一二三四五六七八九十两\d]+)\s*(?:条|项|个)?\s*(?:'+words+')',query)
        if not match:
            match=re.search(r'(?:'+words+r').{0,6}(?:不超过|最多|至多)\s*([一二三四五六七八九十两\d]+)',query)
        if match:
            number=small_integer(match[1])
            if number is not None:result[key]=max(1,min(8,number))
    return result


def validation_code(issue):
    for words,code in [('引用不存在|引用格式','invalid_fact_reference'),('未引用','uncited_business_value'),('数据目录','catalog_scope_mismatch'),('零不能','zero_missing_confusion'),('原因缺乏证据','unsupported_causal_claim'),('观察|建议格式','invalid_report_structure')]:
        if re.search(words,issue):return code
    return 'report_contract_mismatch'


def requested_charts(query,catalog):
    if not re.search(r'图|柱状',query) or re.search(r'不(?:需要|要|附|画).{0,4}图',query):return []
    metrics=[]
    if re.search(r'金额|销售额',query):metrics.append(('商品金额',['item_sales','sales','current_sales']))
    if re.search(r'订单数|订单对比',query):metrics.append(('订单数',['orders','order_count']))
    if not metrics:return []
    result=[]
    for label,fields in metrics:
        for source in catalog.values():
            columns=source['columns']
            value=next((f for f in fields if f in columns),None)
            category=next((f for f in ['period','month','order_month','purchase_month','customer_city','customer_state','category','product_id'] if f in columns),None)
            if value and category:
                result.append({'name':'build_bar_chart','arguments':{'title':label+'对比','source_task_id':source['task_id'],'category_field':category,'value_field':value,'max_items':12}})
                break
    return result


def merge_charts(query, raw_calls, catalog):
    """Keep equivalent full-period and raw-month charts from being duplicated."""
    calls=list(raw_calls) if isinstance(raw_calls,list) else raw_calls
    if not isinstance(calls,list):return calls
    if re.search(r'不(?:需要|要|附|画).{0,4}图',query):return []
    planned=requested_charts(query,catalog)
    if not planned:return calls
    def signature(call):
        args=call.get('arguments') or {} if isinstance(call,dict) else {}
        source=str(args.get('source_task_id') or '')
        field=str(args.get('value_field') or '')
        if source.endswith(':summary:time'):
            source=source[:-len(':summary:time')]
            if field.endswith('_sum'):field=field[:-4]
        return source,field
    selected=[]
    for desired in planned:
        selected.append(next((call for call in calls if signature(call)==signature(desired)),desired))
    if re.search(r'(?:一个|1个).{0,24}图',query):return selected[:1]
    return selected+[call for call in calls if signature(call) not in {signature(c) for c in selected}]
