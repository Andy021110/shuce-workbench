"""Server-owned, checkable Olist constraints. Never trust model query_contract claims.

This supports explicit state/status/category cohorts and calendar-month questions.
Other business predicates still require SQL review and cannot silently qualify for
automatic continuation merely because the model claims they were preserved.
"""
from __future__ import annotations

import re
from sqlglot import exp, parse_one
from .analysis_scope import filters_for_query, category_translation, exclusions_for_query


def month_periods(query, previous=None):
    # Full day-level ranges stay in SQL; do not round them to complete months.
    if re.search(r'\d{4}-\d{2}-\d{2}', query):return []
    from .scope_language import polarity
    found=[];excluded=[];year=None
    inherited=(previous or {}).get('periods') or []
    fallback=int(inherited[-1][:4]) if inherited else None
    tokens=re.finditer(r'(?:(\d{4})\s*年\s*)?(1[0-2]|0?[1-9])\s*月|(\d{4})-(0[1-9]|1[0-2])(?![-\d])',query)
    for m in tokens:
        if m[3]:year=int(m[3]);month=int(m[4])
        else:
            prefix=query[:m.start()]
            years=re.findall(r'(\d{4})\s*年',prefix)
            year=int(m[1]) if m[1] else year or (int(years[-1]) if years else fallback)
            month=int(m[2])
        if year and 1900<=year<=9998:
            kind=polarity(query,m.start(),m.end(),samples=False)
            if kind=='include':found.append(f'{year:04}-{month:02}')
            elif kind=='exclude':excluded.append(f'{year:04}-{month:02}')
    found=sorted(set(found))
    if len(found)==2 and re.search(r'(?:月|\d{4}-\d{2})\s*(?:至|到|~|—)\s*(?:\d{4}年)?\d',query):
        a,b=found;value=a;found=[]
        while value<=b and len(found)<60:
            found.append(value);y,m=map(int,value.split('-'));value=f'{y+(m==12):04}-{m%12+1:02}'
    return [value for value in found if value not in excluded]


def intent_for(query, previous=None, *, inherit_periods=False):
    previous=previous or {}
    periods=month_periods(query,previous)
    if not periods and inherit_periods:periods=list(previous.get('periods') or [])
    return {'exclusions':exclusions_for_query(query),
            'all_statuses':bool(re.search('全部(?:订单)?状态|所有(?:订单)?状态|不限状态',query)),
            'filters':filters_for_query(query,previous.get('filters')),
            'periods':periods,
            'grain':'month' if re.search('每月|每个月|按月|月度趋势',query) else None}


def enum_error(sql):
    """Bind category values to the actual Portuguese/English fields, not mere text."""
    tree=parse_one(sql,read='duckdb')
    raw_to_english=dict(category_translation());english=set(raw_to_english.values())
    for node in tree.walk():
        if isinstance(node,exp.EQ):pairs=[(node.this,node.expression),(node.expression,node.this)]
        elif isinstance(node,exp.In):pairs=[(node.this,v) for v in node.expressions]
        else:continue
        for col,value in pairs:
            if not isinstance(col,exp.Column) or not isinstance(value,exp.Literal) or not value.is_string:continue
            if col.name=='product_category_name' and value.this in english and value.this not in raw_to_english:
                return f"品类字段和值不匹配：{value.this}是英文品类，必须使用翻译表product_category_name_english，原始product_category_name是葡语。"
            if col.name=='product_category_name_english' and value.this in raw_to_english and value.this not in english:
                return '品类字段和值不匹配：葡语原始值不能直接筛选英文品类字段。'
    return ''


def _monthly_projection(select, tree, seen=None):
    """Trace exposed monthly columns through CTE/subquery projections, not dead CTEs."""
    seen=set(seen or ())
    if id(select) in seen:return set()
    seen.add(id(select));ctes={c.alias_or_name:c.this for c in tree.find_all(exp.CTE)}
    monthly=set()
    for projection in select.expressions:
        value=projection.this if isinstance(projection,exp.Alias) else projection
        direct=any((isinstance(n,exp.TimeToStr) and '%Y-%m' in n.sql(dialect='duckdb') and '%d' not in n.sql(dialect='duckdb')) or (isinstance(n,(exp.DateTrunc,exp.TimestampTrunc)) and str(n.args.get('unit','')).strip("'").upper()=='MONTH') for n in value.walk())
        if value.find(exp.AggFunc):continue
        if direct:monthly.add(projection.alias_or_name);continue
        if not isinstance(value,exp.Column):continue
        sources=[]
        for table in select.find_all(exp.Table):
            if table.find_ancestor(exp.Select) is select and (not value.table or table.alias_or_name==value.table):
                if table.name in ctes:sources.append(ctes[table.name])
        for sub in select.find_all(exp.Subquery):
            if sub.find_ancestor(exp.Select) is select and (not value.table or sub.alias_or_name==value.table):sources.append(sub.this)
        if any(isinstance(source,exp.Select) and value.name in _monthly_projection(source,tree,seen) for source in sources):monthly.add(projection.alias_or_name)
    # An aggregate must actually group the exposed month, not MIN/MAX it.
    if any(p.find(exp.AggFunc) for p in select.expressions):
        group=select.args.get('group')
        if not group:return set()
        grouped=set()
        for g in group.expressions:
            if isinstance(g,exp.Literal) and not g.is_string and g.this.isdigit():
                index=int(g.this)-1
                if 0<=index<len(select.expressions):grouped.add(select.expressions[index].alias_or_name)
            elif isinstance(g,exp.Column):grouped.add(g.name)
            else:
                for p in select.expressions:
                    if (p.this if isinstance(p,exp.Alias) else p)==g:grouped.add(p.alias_or_name)
        monthly &= grouped
    return monthly


def business_sql_error(sql, intent):
    error=enum_error(sql)
    if error:return error
    tree=parse_one(sql,read='duckdb')
    periods=intent.get('periods') or []
    # FILTER samples stay local to a measure. All-status totals may not be
    # narrowed by a WHERE, including one hidden in a model-generated CTE.
    if intent.get('all_statuses'):
        for where in tree.find_all(exp.Where):
            if not where.find_ancestor(exp.Filter) and any(c.name=='order_status' for c in where.find_all(exp.Column)):
                return '订单总量要求全部状态；请删除全局WHERE中的订单状态条件，配送样本只用CASE或聚合FILTER定义。'
    for field,key in [('customer_state','states'),('order_status','order_statuses'),('product_category_name_english','categories'),('product_category_name','categories')]:
        excluded=set((intent.get('exclusions') or {}).get(key) or [])
        for where in tree.find_all(exp.Where):
            for node in where.walk():
                if isinstance(node,exp.EQ):pairs=[(node.this,node.expression),(node.expression,node.this)]
                elif isinstance(node,exp.In) and not isinstance(node.parent,exp.Not):pairs=[(node.this,v) for v in node.expressions]
                else:continue
                for col,value in pairs:
                    if not isinstance(col,exp.Column) or col.name!=field or not isinstance(value,exp.Literal) or not value.is_string:continue
                    actual=dict(category_translation()).get(value.this,value.this) if field=='product_category_name' else value.this
                    if actual in excluded:return 'SQL把用户排除的范围当作正向条件，请省略该条件，由后端施加排除范围。'
    if periods:
        first=periods[0]+'-01';y,m=map(int,periods[-1].split('-'));end=f'{y+(m==12):04}-{m%12+1:02}-01'
        field=intent.get('time_field','order_purchase_timestamp')
        for where in tree.find_all(exp.Where):
            for node in where.walk():
                if not isinstance(node,(exp.EQ,exp.GTE,exp.GT,exp.LT,exp.LTE,exp.Between,exp.In)):continue
                if not any(c.name==field for c in node.this.find_all(exp.Column)):continue
                literals=[v.this for v in node.find_all(exp.Literal) if v.is_string and re.match(r'^\d{4}-\d{2}(?:-\d{2})?',v.this)]
                literals=[v[:10] if re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]00:00:00(?:\.0+)?',v) else v for v in literals]
                if not literals:continue
                valid=(isinstance(node,exp.GTE) and literals==[first]) or (isinstance(node,exp.LT) and literals==[end])
                if isinstance(node,(exp.In,exp.EQ)) and all(re.fullmatch(r'\d{4}-\d{2}',v) for v in literals):valid=set(literals)==set(periods)
                if not valid:
                    return f'日期条件与已确认月份{periods}不一致或无法核对。请省略日期WHERE，由后端统一施加月份范围；保留月份输出和分组。'
    if intent.get('grain')=='month' and (not isinstance(tree,exp.Select) or not _monthly_projection(tree,tree)):
        return '问题要求每月分别返回结果；SQL必须输出真实月份列并按月份分组，不能把两个月合成一行。'
    # Detect explicit contradictory literals before applying a mandatory cohort.
    # Otherwise a wrong category/state could intersect the right cohort into zero.
    for field,key in [('customer_state','states'),('order_status','order_statuses'),('product_category_name_english','categories'),('product_category_name','categories')]:
        expected=set((intent.get('filters') or {}).get(key) or [])
        if not expected:continue
        for where in tree.find_all(exp.Where):
            for node in where.walk():
                if isinstance(node,exp.EQ):pairs=[(node.this,node.expression),(node.expression,node.this)]
                elif isinstance(node,exp.In):pairs=[(node.this,v) for v in node.expressions]
                else:continue
                for col,value in pairs:
                    if not isinstance(col,exp.Column) or col.name!=field or not isinstance(value,exp.Literal) or not value.is_string:continue
                    actual=dict(category_translation()).get(value.this,value.this) if field=='product_category_name' else value.this
                    if actual not in expected:return f'查询中的{field}={value.this}与确认范围冲突；请使用{sorted(expected)}或省略该条件，由后端统一施加范围。'
    return ''


def reusable_cohort(sql, intent):
    """Conservative allowlist: no arbitrary amount, review, HAVING, OR predicates."""
    if any((intent.get('exclusions') or {}).values()):return False
    if not intent.get('periods') or not any((intent.get('filters') or {}).values()):return False
    if any(len(values)>1 for values in (intent.get('filters') or {}).values()):return False
    try:
        tree=parse_one(sql,read='duckdb')
        if business_sql_error(sql,intent) or tree.find(exp.Having) or tree.find(exp.Union):return False
        allowed={'order_id','customer_id','product_id','product_category_name','product_category_name_english','customer_state','order_status','order_purchase_timestamp'}
        for where in tree.find_all(exp.Where):
            # COUNT/AVG FILTER(WHERE ...) defines a measure's samples, not the
            # row cohort. Missing-date counts must not make the cohort opaque.
            if where.find_ancestor(exp.Filter):continue
            if any(where.find(kind) for kind in (exp.Or,exp.Not,exp.NEQ,exp.Like,exp.ILike,exp.RegexpLike)):return False
            if any(c.name not in allowed for c in where.find_all(exp.Column)):return False
            for node in where.find_all(exp.EQ):
                if any(c.name in {'order_id','customer_id','product_id'} for c in node.find_all(exp.Column)) and any(node.find_all(exp.Literal)):return False
                for field,key in [('customer_state','states'),('order_status','order_statuses'),('product_category_name_english','categories')]:
                    if any(c.name==field for c in node.find_all(exp.Column)) and any(node.find_all(exp.Literal)) and not (intent.get('filters') or {}).get(key):return False
        # Only current validated results can be confirmed. Legacy results may
        # have omitted filters despite mentioning them; do not trust those.
        return bool(intent.get('validated'))
    except Exception:return False


def public_intent(intent, sql):
    value={**intent,'validated':True,'validation_version':2}
    value['can_continue']=reusable_cohort(sql,value)
    return value


def legacy_result_error(sql, query):
    """Recheck old free queries without rewriting the archived SQL or prose."""
    try:
        if re.search('上个?月|本月|这个月|最近|上周|本周|今天|昨天',query):return '历史相对时间没有确认绝对日期，请明确月份后重新查询。'
        intent=intent_for(query)
        error=business_sql_error(sql,intent)
        if error:return error
        tree=parse_one(sql,read='duckdb')
        for field,key in [('customer_state','states'),('order_status','order_statuses'),('product_category_name_english','categories')]:
            expected=set(intent['filters'].get(key) or [])
            if not expected:continue
            actual=set()
            for where in tree.find_all(exp.Where):
                for node in where.walk():
                    if isinstance(node,exp.EQ):pairs=[(node.this,node.expression),(node.expression,node.this)]
                    elif isinstance(node,exp.In):pairs=[(node.this,v) for v in node.expressions]
                    else:continue
                    for col,value in pairs:
                        if not isinstance(col,exp.Column) or not isinstance(value,exp.Literal) or not value.is_string:continue
                        if col.name==field:actual.add(value.this)
                        elif key=='categories' and col.name=='product_category_name':actual.add(dict(category_translation()).get(value.this,value.this))
            if not expected<=actual:return f'历史SQL无法核对明确条件{field}={sorted(expected)}，需要按原问题重新运行。'
        return ''
    except Exception:return '历史SQL的业务条件暂时无法核对，请重新运行原问题。'
