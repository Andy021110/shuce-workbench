"""Explicit date confirmation and a bounded, server-owned buyer cohort query."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import QueryResult
from .querying.duckdb_engine import DuckDbEngine
from .analysis_scope import active, scope_card
from .analysis_basis import actual_basis, basis_for
from .progress import stage


def business_result(task_id, query, intent, access_scope, *, today=None):
    absolute = re.search(r'\d{4}\s*(?:年|-(?:0[1-9]|1[0-2]))', query)
    relative = re.search(r'上个?月|本月|这个月|最近|上周|本周|今天|昨天|今年|去年', query)
    if relative and not absolute:
        current = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
        if relative.group() in ('上个月', '上月'):
            year, month = current.year - (current.month == 1), (current.month - 2) % 12 + 1
            meaning = f'按今天{current.isoformat()}，“{relative.group()}”指{year:04}-{month:02}。'
        else:
            meaning = f'今天是{current.isoformat()}，需要先确认“{relative.group()}”的绝对日期。'
        return QueryResult(task_id=task_id, status='completed', route='direct_response',
            message='请确认分析时间', workflow_mode='date_confirmation',
            analysis=meaning+'当前Olist是2016-09至2018-10历史样本，尚未按这个相对时间查询，也不会自动改用最近历史月份。请用明确年月重新提问，例如“比较2018年8月和7月的商品销售额”。'+('利润还需要成本、退款、税费等依据，不能由商品金额差额推算。' if re.search('利润|毛利',query) else ''))

    if not (re.search('老客', query) and re.search('之前|此前|历史', query) and re.search('买家|客户|用户', query)):
        return None
    periods = intent.get('periods') or []
    if (len(periods) != 1 or active(intent.get('filters')) or active(intent.get('exclusions'))
            or intent.get('time_field', 'order_purchase_timestamp') != 'order_purchase_timestamp'):
        return QueryResult(task_id=task_id, status='completed', route='direct_response',
            message='需要确认老客分析口径', workflow_mode='cohort_confirmation',
            analysis='当前老客分析支持一个明确下单月份、全部订单状态、全部地区与品类。请明确月份并输入“清除全部筛选”，再说明“当月买家中，在月初之前买过的人数和占比”。更复杂群体暂不自动继承，以免混淆本期与历史窗口。')
    month = periods[0]
    year, number = map(int, month.split('-'))
    start = month + '-01'
    end = f'{year + (number == 12):04}-{number % 12 + 1:02}-01'
    sql = f"""WITH current_buyers AS (
        SELECT DISTINCT c.customer_unique_id
        FROM olist_orders_dataset o JOIN olist_customers_dataset c USING(customer_id)
        WHERE o.order_purchase_timestamp >= DATE '{start}' AND o.order_purchase_timestamp < DATE '{end}'
          AND c.customer_unique_id IS NOT NULL
    ), prior_buyers AS (
        SELECT DISTINCT c.customer_unique_id
        FROM olist_orders_dataset o JOIN olist_customers_dataset c USING(customer_id)
        WHERE o.order_purchase_timestamp < DATE '{start}' AND c.customer_unique_id IS NOT NULL
    ) SELECT COUNT(*) AS buyers, COUNT(p.customer_unique_id) AS returning_buyers,
        COUNT(p.customer_unique_id) * 1.0 / NULLIF(COUNT(*),0) AS returning_rate
        FROM current_buyers b LEFT JOIN prior_buyers p USING(customer_unique_id)"""
    stage('metric_sql')
    execution = DuckDbEngine().execute('olist_ecommerce', sql, access_scope)
    result = QueryResult(task_id=task_id, status='completed' if execution.success else 'failed', route='database_query',
        message='老客群体核对完成' if execution.success else '老客群体核对未完成',
        sql=execution.sql, columns=execution.columns, rows=execution.rows, total_rows=execution.total_rows,
        snapshot_id=execution.snapshot_id, result_profile=execution.result_profile, metric_basis='canonical',
        workflow_mode='buyer_cohort', result_title=month+'买家中的历史老客占比',
        query_intent={**intent, 'validated':True, 'validation_version':2, 'can_continue':False},
        analysis_basis=actual_basis(sql,basis_for('按下单时间')),
        analysis=execution.error if not execution.success else '')
    result.analysis_scope=scope_card(sql,{},basis=result.analysis_basis)
    result.analysis_scope['period_label']=f'本期[{start}, {end})；历史早于{start}'
    result.analysis_scope['note']='全部状态、地区与品类。本期买家按customer_unique_id去重，历史仅判断本期买家在月初前是否买过；两窗口独立。历史仅限当前公开样本，未在样本中出现不代表从未购买。此指标不同于全样本复购率。'
    if execution.success:
        row=execution.rows[0]
        rate='不可计算（无本期买家）' if row['returning_rate'] is None else f"{row['returning_rate']:.2%}"
        result.analysis=f"{month}去重买家{row['buyers']}人，其中样本内月初前曾购买的老客{row['returning_buyers']}人，占比{rate}。"+result.analysis_scope['note']
    return result
