"""Structured Olist cohort filters. Applied to physical tables before execution.

This is not a general SQL equivalence proof. Other ad-hoc predicates remain visible
as SQL conditions and require review; category selection defines an order cohort
and restricts monetary/item measures to the selected category's item rows.
"""
from functools import lru_cache
import re
from sqlglot import parse_one, exp
from .database import SCHEMA
from .metric_registry import DATABASE

STATES = 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split()
STATUSES = {'delivered':'已送达','shipped':'已发货','canceled':'已取消','unavailable':'不可用',
            'invoiced':'已开票','processing':'处理中','approved':'已批准','created':'已创建'}
ORDER_TABLE='olist_orders_dataset'
ITEM_TABLE='olist_order_items_dataset'
CATEGORY_EXPR="COALESCE(t.product_category_name_english,p.product_category_name,'未分类')"

@lru_cache(maxsize=1)
def category_options():
    from .querying.duckdb_engine import DuckDbEngine
    with DuckDbEngine().connect(DATABASE) as connection:
        return [r[0] for r in connection.execute(f"SELECT DISTINCT {CATEGORY_EXPR} AS category FROM olist_order_items_dataset i LEFT JOIN olist_products_dataset p USING(product_id) LEFT JOIN product_category_name_translation t USING(product_category_name) ORDER BY category").fetchall()]

def normalize_filters(filters=None):
    from .models import ReviewFilters
    result=ReviewFilters.model_validate(filters or {}).model_dump()
    for field in result: result[field]=sorted(set(result[field]))
    if any(c not in category_options() for c in result['categories']):
        raise ValueError('品类不在当前数据源中，请从可用品类选择')
    return result

def active(filters):
    return any((filters or {}).get(k) for k in ('states','order_statuses','categories'))

def quoted(values):
    return ','.join("'"+v.replace("'","''")+"'" for v in values)

def mention_spans(query,value):
    """Keep positive selections separate from exclusions and focus examples."""
    pattern=re.escape(value) if re.search('[\u4e00-\u9fff]',value) else r'(?<![A-Za-z0-9_])'+re.escape(value)+r'(?![A-Za-z0-9_])'
    from .scope_language import polarity
    positive=[];negative=[]
    for match in re.finditer(pattern,query,re.I):
        kind=polarity(query,match.start(),match.end())
        if kind=='exclude':negative.append(match.span())
        elif kind=='include':positive.append(match.span())
    return positive,negative


def exclusions_for_query(query):
    from .category_labels import LABELS
    result={'states':[], 'order_statuses':[], 'categories':[]}
    result['states']=[v for v in STATES if mention_spans(query,v)[1]]
    result['order_statuses']=[v for v,label in STATUSES.items() if mention_spans(query,v)[1] or mention_spans(query,label)[1]]
    matches=[]
    for raw,code in category_translation():
        aliases={raw,code,LABELS.get(code,code)}
        if code=='watches_gifts':aliases.update(['手表礼品','手表与礼品'])
        for alias in aliases:
            matches.extend((code,a,b) for a,b in mention_spans(query,alias)[1])
    result['categories']=sorted({code for code,a,b in matches if not any(x<=a and b<=y and (a,b)!=(x,y) for _,x,y in matches)})
    return {k:sorted(set(v)) for k,v in result.items()}


def exclusion_guard(current,positive,negative):
    if current and negative and not positive:
        raise ValueError('本次使用了排除条件，但上一条已有该维度范围。请先清除该维度筛选，或明确选择要保留的范围，再发送问题。')

def apply_filters(sql, filters, periods=None, time_field='order_purchase_timestamp', exclusions=None):
    if not active(filters) and not periods and not active(exclusions): return sql
    from .querying.duckdb_engine import DuckDbEngine
    # Preserve the original join/amplification checks before wrapping physical tables.
    DuckDbEngine()._validate_sql(DATABASE,sql,None)
    filters=normalize_filters(filters)
    exclusions=normalize_filters(exclusions)
    tree=parse_one(sql,read='duckdb')
    tables=list(tree.find_all(exp.Table))
    cte_names={c.alias_or_name for c in tree.find_all(exp.CTE)}
    if any(name.startswith('__ask_scope_') for name in cte_names):
        raise ValueError('请重新生成原始查询，不要复制系统施加范围后的SQL中间表')
    definitions={(t.get('name') or t['id']):t for t in SCHEMA if t['database']==DATABASE}
    if cte_names & definitions.keys():
        raise ValueError('查询中间表不能与数据源表同名')
    referenced={t.name for t in tables if t.name in definitions}
    if not referenced:raise ValueError('当前继承了订单范围，查询必须引用可核对的数据表')
    # Shared, server-owned CTEs avoid repeating the cohort at every table/branch.
    names={name:'__ask_scope_'+name for name in ('products','orders','items')}
    conditions=[]
    if exclusions['order_statuses']: conditions.append('scope_o.order_status NOT IN ('+quoted(exclusions['order_statuses'])+')')
    if exclusions['states']:
        conditions.append('scope_o.customer_id NOT IN (SELECT customer_id FROM olist_customers_dataset WHERE customer_state IN ('+quoted(exclusions['states'])+'))')
    if exclusions['categories']:
        conditions.append(f'scope_o.order_id NOT IN (SELECT ex_i.order_id FROM {ITEM_TABLE} ex_i LEFT JOIN olist_products_dataset p USING(product_id) LEFT JOIN product_category_name_translation t USING(product_category_name) WHERE {CATEGORY_EXPR} IN ({quoted(exclusions["categories"])}))')
    if filters['order_statuses']: conditions.append('scope_o.order_status IN ('+quoted(filters['order_statuses'])+')')
    if filters['states']:
        conditions.append('scope_o.customer_id IN (SELECT customer_id FROM olist_customers_dataset WHERE customer_state IN ('+quoted(filters['states'])+'))')
    if periods:
        from .analysis_basis import TIME_FIELDS
        if time_field not in TIME_FIELDS:raise ValueError('无法核对统计日期依据')
        if time_field=='shipping_limit_date':
            raise ValueError('按最迟发货时间的复杂范围请使用独立查询，不能按订单日期施加筛选')
        conditions.append(f"strftime(scope_o.{time_field},'%Y-%m') IN ({quoted(periods)})")
    selected_products='';ctes=[]
    if filters['categories']:
        selected_products=f"SELECT product_id FROM {names['products']}"
        ctes.append((names['products'],f"SELECT p.product_id FROM olist_products_dataset p LEFT JOIN product_category_name_translation t USING(product_category_name) WHERE {CATEGORY_EXPR} IN ({quoted(filters['categories'])})"))
        # Missing product records are only included for the explicit unclassified category.
        selected_item_condition=f"(scope_i.product_id IN ({selected_products})" + (" OR scope_i.product_id NOT IN (SELECT product_id FROM olist_products_dataset)" if '未分类' in filters['categories'] else '') + ')'
        conditions.append(f'scope_o.order_id IN (SELECT scope_i.order_id FROM {ITEM_TABLE} scope_i WHERE {selected_item_condition})')
    def columns(table, alias):
        definition=next(t for t in SCHEMA if (t.get('name') or t['id'])==table)
        return ','.join(alias+'."'+f['name']+'"' for f in definition['fields'])
    order_sql=f"SELECT {columns(ORDER_TABLE,'scope_o')} FROM {ORDER_TABLE} scope_o"+(' WHERE '+' AND '.join(conditions) if conditions else '')
    ctes.append((names['orders'],order_sql))
    order_ids=f"SELECT order_id FROM {names['orders']}"
    item_condition=(selected_item_condition+' AND ' if selected_products else '')+f'scope_i.order_id IN ({order_ids})'
    item_sql=f"SELECT {columns(ITEM_TABLE,'scope_i')} FROM {ITEM_TABLE} scope_i WHERE {item_condition}"
    needs_items=bool(referenced & {ITEM_TABLE,'olist_products_dataset','olist_sellers_dataset','product_category_name_translation'})
    if needs_items:ctes.append((names['items'],item_sql))
    products=f"SELECT product_id FROM {names['items']}"
    # Restrict every physical data source, including standalone customer/product
    # queries and UNION branches. Newly inserted raw eligibility subqueries are
    # intentionally not rewritten again.
    for name in sorted(referenced):
        if name==ORDER_TABLE:continue
        elif name==ITEM_TABLE:continue
        else:
            fields={f['name'] for f in definitions[name]['fields']}
            if 'order_id' in fields:condition=f'scope_row.order_id IN ({order_ids})'
            elif 'customer_id' in fields:condition=f"scope_row.customer_id IN (SELECT customer_id FROM {names['orders']})"
            elif 'product_id' in fields:condition=f'scope_row.product_id IN ({products})'
            elif 'seller_id' in fields:condition=f"scope_row.seller_id IN (SELECT seller_id FROM {names['items']})"
            elif 'product_category_name' in fields:condition=f'scope_row.product_category_name IN (SELECT product_category_name FROM olist_products_dataset WHERE product_id IN ({products}))'
            else:raise ValueError('当前范围无法约束该表，请明确清除筛选后重新查询')
            names[name]='__ask_scope_'+name
            ctes.append((names[name],f"SELECT {columns(name,'scope_row')} FROM {name} scope_row WHERE {condition}"))
    for table in tables:
        if table.name not in referenced:continue
        name=names['orders'] if table.name==ORDER_TABLE else names['items'] if table.name==ITEM_TABLE else names[table.name]
        table.replace(exp.Table(this=exp.to_identifier(name),alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name))))
    old=tree.args.get('with_')
    shared=[exp.CTE(this=parse_one(query,read='duckdb'),alias=exp.TableAlias(this=exp.to_identifier(name))) for name,query in ctes]
    tree.set('with_',exp.With(expressions=shared+list(old.expressions if old else []),recursive=bool(old and old.args.get('recursive'))))
    return tree.sql(dialect='duckdb')

def filters_for_query(query, previous=None):
    """Only explicit, narrow scope edits; ambiguous edits ask for structured controls."""
    filters={k:list((previous or {}).get(k) or []) for k in ('states','order_statuses','categories')}
    if re.search('清除(?:全部|所有)?筛选|取消(?:全部|所有)?筛选|不限制范围',query):
        filters={k:[] for k in filters}
    if re.search('所有州|全部州|全部地区|不限地区',query): filters['states']=[]
    else:
        mentions={state:mention_spans(query,state) for state in STATES}
        states=[state for state,(positive,_) in mentions.items() if positive]
        exclusion_guard(filters['states'],states,any(negative for _,negative in mentions.values()))
        if states: filters['states']=sorted(set(states))
    if re.search('全部(?:订单)?状态|所有(?:订单)?状态|不限状态',query): filters['order_statuses']=[]
    else:
        mentions={s:[mention_spans(query,label),mention_spans(query,s)] for s,label in STATUSES.items()}
        matched=[s for s,values in mentions.items() if any(positive for positive,_ in values)]
        exclusion_guard(filters['order_statuses'],matched,any(negative for values in mentions.values() for _,negative in values))
        if matched: filters['order_statuses']=sorted(matched)
    if re.search('所有品类|全部品类|不限品类',query): filters['categories']=[]
    else:
        from .category_labels import LABELS
        # UI labels, English codes and Portuguese source values share one ID.
        aliases={c:[c,LABELS.get(c,c)] for c in category_options()}
        aliases.setdefault('watches_gifts',[]).extend(['手表礼品','手表与礼品'])
        for raw,code in category_translation():aliases.setdefault(code,[code]).append(raw)
        matches=[];excluded=False;excluded_spans=[]
        for code,values in aliases.items():
            for value in values:
                positive,negative=mention_spans(query,value);excluded=excluded or bool(negative)
                matches.extend((code,start,end) for start,end in positive)
                excluded_spans.extend(negative)
        # A mention of 电脑配件 must not also select the shorter label 电脑.
        covering=[(a,b) for _,a,b in matches]+excluded_spans
        categories=sorted({code for code,start,end in matches if not any(a<=start and end<=b and (a,b)!=(start,end) for a,b in covering)})
        exclusion_guard(filters['categories'],categories,excluded)
        if categories: filters['categories']=categories
    return filters

@lru_cache(maxsize=1)
def category_translation():
    from .querying.duckdb_engine import DuckDbEngine
    with DuckDbEngine().connect(DATABASE) as connection:
        return tuple(connection.execute('SELECT product_category_name,product_category_name_english FROM product_category_name_translation').fetchall())

def simple_sql_filters(sql):
    result={k:[] for k in ('states','order_statuses','categories')}
    try:
        tree=parse_one(sql,read='duckdb')
        if not isinstance(tree,exp.Select) or len(list(tree.find_all(exp.Select)))!=1 or tree.args.get('with_'):
            return result
        where=tree.args.get('where')
        if not where: return result
        terms=list(where.this.flatten()) if isinstance(where.this,exp.And) else [where.this]
        mapping={'customer_state':'states','order_status':'order_statuses','product_category_name_english':'categories'}
        for term in terms:
            field=None;values=[]
            if isinstance(term,exp.EQ) and isinstance(term.this,exp.Column) and isinstance(term.expression,exp.Literal) and term.expression.is_string:
                field=term.this.name;values=[term.expression.this]
            elif isinstance(term,exp.In) and isinstance(term.this,exp.Column) and term.expressions and all(isinstance(e,exp.Literal) and e.is_string for e in term.expressions):
                field=term.this.name;values=[e.this for e in term.expressions]
            if field in mapping:result[mapping[field]]=values
        return normalize_filters(result)
    except Exception:
        return {k:[] for k in result}

def scope_card(sql, filters=None, review=None, basis=None, inherited=False, exclusions=None):
    detected=simple_sql_filters(sql) if not review else {}
    filters={k:list((filters or {}).get(k) or detected.get(k) or []) for k in ('states','order_statuses','categories')}
    predicates=[]
    try:
        tree=parse_one(sql,read='duckdb')
        for where in tree.find_all(exp.Where):
            for term in where.this.flatten() if isinstance(where.this, exp.And) else [where.this]:
                text=term.sql(dialect='duckdb')
                if any(c.name in {'order_status','customer_state','product_category_name','product_category_name_english',*(basis or {}).get('verified_time_fields',[])} for c in term.find_all(exp.Column)):
                    if text not in predicates:predicates.append(text)
    except Exception: pass
    fixed=bool(review)
    exclusions=normalize_filters(exclusions)
    exclusion_note='；'.join(label+'：'+'、'.join(values) for label,values in [('排除地区',exclusions['states']),('排除状态',[STATUSES[v] for v in exclusions['order_statuses']]),('排除品类订单',exclusions['categories'])] if values)
    return {'database_id':DATABASE,'mode':'monthly_review' if fixed else 'generated_sql',
        'filters':filters,'exclusions':exclusions,'inherited':inherited,'time_label':(basis or {}).get('time_label','下单时间'),
        'period_label':f"{review['month']} 对比 {review['previous_month']}" if review else '日期条件见下方 SQL 范围',
        'statuses':[STATUSES[s] for s in filters['order_statuses']],
        'states':filters['states'],'categories':filters['categories'],'currency':'BRL',
        'predicates':predicates[:20],
        'note':(exclusion_note+'。' if exclusion_note else '')+('排除品类时剔除含该品类的整笔订单。' if exclusions['categories'] else '')+('范围由固定查询执行并保存。' if fixed else '已由后端施加本次确认的结构化范围；自由查询的其他条件仍需核对 SQL。')+
            ('选中品类时，订单数为包含该品类的订单；商品金额和明细条数仅统计选中品类；支付等订单级指标未按品类分摊。' if filters['categories'] else '')+
            '配送超时率仍只比较范围内已送达且实际、预计日期齐全的订单；目标仅适用于填写时的范围。'}


def continuation_guard(query, previous):
    """Fail closed for dependent questions on ad-hoc predicates we cannot retain.

    Fixed review cohorts are server-owned. Arbitrary SQL equivalence is deliberately
    not guessed: a user may explicitly reset filters or restate a new full question.
    """
    if not previous or not previous.sql or previous.review_context: return ''
    if re.search('清除(?:全部|所有)?筛选|取消(?:全部|所有)?筛选|不限制范围',query): return ''
    if not re.search('继续|这些|该范围|同一?范围|下钻|进一步|再看|按.+拆',query): return ''
    try:
        tree=parse_one(previous.sql,read='duckdb')
        # A WHERE in a generated query can define a cohort beyond our three typed filters.
        # Do not reuse only the recognized subset, including OR/CTE/HAVING cases.
        if any(tree.find_all(exp.Where)) or any(tree.find_all(exp.Having)):
            return '上次自由查询包含额外条件，无法自动确认本次下钻仍保留完整范围。请在新问题中重述时间与全部条件，或使用月度复盘的范围选择；如需重置，请明确输入“清除全部筛选”。上次结果仍可查看。'
    except Exception:
        return '上次查询范围无法核对，请重述时间与全部条件后查询。'
    return ''
