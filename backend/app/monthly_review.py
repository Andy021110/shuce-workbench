"""A repeatable monthly business task with fixed, versioned SQL definitions."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import re

from .metric_registry import DATABASE, metric_query, definitions

STEPS = {"overview": "月度经营对比", "delivery_states": "按州排查配送", "sales_categories": "按品类排查销售", "sales_drivers": "拆解单均金额"}


def period(month):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(month)):
        raise ValueError("请选择有效月份")
    if not "2016-10" <= month <= "2018-10":
        raise ValueError("当前历史样本支持复盘2016年10月至2018年10月；需要前一个月的数据进行比较")
    current = date.fromisoformat(month + "-01")
    previous = (current - timedelta(days=1)).replace(day=1)
    end = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    if previous.year < 1 or end.year > 9999:
        raise ValueError("月份超出支持范围")
    return previous, current, end


def review_query(month, step="overview", filters=None):
    previous, current, end = period(month)
    if step not in STEPS:
        raise ValueError("未知复盘步骤")
    context = {"month": month, "previous_month": str(previous)[:7], "step": step,
               "time_label": "下单时间", "version": "1.0", "status_scope": "全部订单状态；超时率仅比较已送达且日期齐全的订单",
               "currency": "BRL"}
    contracts = [d for d in definitions() if d["id"] in {"order_count", "item_sales", "delivery_late"}]
    if step not in {"overview", "sales_drivers"}:
        contracts = [d for d in contracts if d['id'] == ('item_sales' if step=='sales_categories' else 'delivery_late')]
    contracts = [{**d,"parameters":{"start_date":str(previous),"end_date":str(end-timedelta(days=1)),"grain":"month"}} for d in contracts]
    if step in {"overview", "sales_drivers"}:
        queries = {key: metric_query(key, str(previous), str(end-timedelta(days=1)), "month")[0]
                   for key in ("order_count", "item_sales", "delivery_late")}
        sql = f"""WITH months AS (SELECT '{str(previous)[:7]}' AS period UNION ALL SELECT '{month}'),
            orders AS ({queries['order_count']}), sales AS ({queries['item_sales']}),
            delivery AS ({queries['delivery_late']})
            SELECT m.period, COALESCE(o.orders,0) AS orders, COALESCE(s.item_sales,0) AS item_sales,
              COALESCE(d.delivered,0) AS delivered, COALESCE(d.late,0) AS late, d.late_rate
            FROM months m LEFT JOIN orders o USING(period) LEFT JOIN sales s USING(period)
            LEFT JOIN delivery d USING(period) ORDER BY m.period"""
        if step == "sales_drivers":
            sql = f"""WITH months AS (SELECT '{str(previous)[:7]}' AS period UNION ALL SELECT '{month}'),
                per_order AS (SELECT i.order_id, COUNT(i.order_item_id) AS item_rows,
                    SUM(CAST(i.price AS DECIMAL(18,2))) AS item_sales
                    FROM olist_order_items_dataset i GROUP BY i.order_id),
                grouped AS (SELECT strftime(o.order_purchase_timestamp,'%Y-%m') AS period,
                    COUNT(DISTINCT o.order_id) AS orders,
                    COUNT(DISTINCT CASE WHEN i.item_rows>0 THEN o.order_id END) AS orders_with_items,
                    SUM(COALESCE(i.item_rows,0)) AS item_rows,
                    SUM(COALESCE(i.item_sales,0)) AS item_sales
                    FROM olist_orders_dataset o LEFT JOIN per_order i USING(order_id)
                    WHERE o.order_purchase_timestamp >= DATE '{previous}' AND o.order_purchase_timestamp < DATE '{end}'
                    GROUP BY 1)
                SELECT m.period, COALESCE(g.orders,0) AS orders,
                    COALESCE(g.orders_with_items,0) AS orders_with_items,
                    COALESCE(g.item_rows,0) AS item_rows, COALESCE(g.item_sales,0) AS item_sales,
                    CAST(g.item_sales AS DOUBLE)/NULLIF(g.orders,0) AS sales_per_order,
                    CAST(g.item_rows AS DOUBLE)/NULLIF(g.orders,0) AS items_per_order,
                    CAST(g.item_sales AS DOUBLE)/NULLIF(g.item_rows,0) AS avg_item_price
                FROM months m LEFT JOIN grouped g USING(period) ORDER BY m.period"""
            contracts = [d for d in contracts if d['id'] in {'order_count','item_sales'}]
            context['decomposition_note'] = '单均商品金额 = 每单商品明细条数 × 平均明细价格。订单分母含全部状态及无商品明细订单；明细条数为订单商品表行数，不把它称为独立商品种类数。价格不含运费，不代表实收客单价或利润。'
    elif step == "delivery_states":
        valid = "o.order_status='delivered' AND o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL"
        late = valid + " AND CAST(o.order_delivered_customer_date AS DATE)>CAST(o.order_estimated_delivery_date AS DATE)"
        sql = f"""WITH grouped AS (
            SELECT c.customer_state,
              COUNT(DISTINCT CASE WHEN o.order_purchase_timestamp < DATE '{current}' AND {valid} THEN o.order_id END) AS previous_delivered,
              COUNT(DISTINCT CASE WHEN o.order_purchase_timestamp >= DATE '{current}' AND {valid} THEN o.order_id END) AS current_delivered,
              COUNT(DISTINCT CASE WHEN o.order_purchase_timestamp < DATE '{current}' AND {late} THEN o.order_id END) AS previous_late,
              COUNT(DISTINCT CASE WHEN o.order_purchase_timestamp >= DATE '{current}' AND {late} THEN o.order_id END) AS current_late
            FROM olist_orders_dataset o JOIN olist_customers_dataset c USING(customer_id)
            WHERE o.order_purchase_timestamp >= DATE '{previous}' AND o.order_purchase_timestamp < DATE '{end}'
            GROUP BY c.customer_state)
            SELECT customer_state, previous_delivered, current_delivered, previous_late, current_late, current_late-previous_late AS new_late_orders,
              CAST(previous_late AS DOUBLE)/NULLIF(previous_delivered,0) AS previous_late_rate,
              CAST(current_late AS DOUBLE)/NULLIF(current_delivered,0) AS current_late_rate
            FROM grouped ORDER BY new_late_orders DESC, customer_state"""
    else:
        sql = f"""WITH grouped AS (
            SELECT COALESCE(t.product_category_name_english,p.product_category_name,'未分类') AS category,
              SUM(CASE WHEN o.order_purchase_timestamp < DATE '{current}' THEN CAST(i.price AS DECIMAL(18,2)) ELSE 0 END) AS previous_sales,
              SUM(CASE WHEN o.order_purchase_timestamp >= DATE '{current}' THEN CAST(i.price AS DECIMAL(18,2)) ELSE 0 END) AS current_sales
            FROM olist_order_items_dataset i JOIN olist_orders_dataset o USING(order_id)
            LEFT JOIN olist_products_dataset p USING(product_id)
            LEFT JOIN product_category_name_translation t USING(product_category_name)
            WHERE o.order_purchase_timestamp >= DATE '{previous}' AND o.order_purchase_timestamp < DATE '{end}'
            GROUP BY 1)
            SELECT category, previous_sales, current_sales, current_sales-previous_sales AS sales_change FROM grouped ORDER BY sales_change, category"""
    from .analysis_scope import normalize_filters, apply_filters, active
    filters=normalize_filters(filters)
    context['filters']=filters
    if active(filters):
        sql=apply_filters(sql,filters)
        context['status_scope']='用户选择的订单范围；配送超时率仅比较其中已送达且日期齐全的订单'
        if context.get('decomposition_note'):
            context['decomposition_note']=context['decomposition_note'].replace('订单分母含全部状态及无商品明细订单','订单分母为当前范围内的订单；符合筛选条件的无商品明细订单保留')
        for contract in contracts:
            contract['applied_filters']=filters
            contract['definition']+=' 本次另应用结构化筛选；选中品类时，订单包含该品类，商品金额仅含所选品类。'
            contract['status_scope']=context['status_scope']
    return sql, context, contracts


def review_analysis(rows, context):
    if context["step"] == "overview" and len(rows) == 2:
        before, after = rows
        def relative(field):
            a, b = Decimal(str(before[field])), Decimal(str(after[field]))
            return f"{(b-a)/abs(a)*100:+.2f}%" if a else "上月为零，不能计算变化率"
        a, b = before.get("late_rate"), after.get("late_rate")
        rate = (f"超时率由 {Decimal(str(a))*100:.2f}% 变为 {Decimal(str(b))*100:.2f}%，变化 {(Decimal(str(b))-Decimal(str(a)))*100:+.2f} 个百分点。"
                if a is not None and b is not None else "有月份没有可比较的已送达订单，超时率不填零。")
        return (f"{context['month']} 对比 {context['previous_month']}：订单数 {after['orders']:,}，变化 {relative('orders')}；"
                f"商品金额 BRL {after['item_sales']:,.2f}，变化 {relative('item_sales')}。{rate} "
                "以下可按州或品类继续排查，时间依据保持为下单时间。")
    if context["step"] == "delivery_states":
        return "按新增超时订单数排序，并同时提供两个月可比较的已送达订单数及超时率。大州的绝对增量不等于履约最差；应结合基数和比率判断，不推断承运商或活动原因。"
    if context["step"] == "sales_drivers":
        summary = ''
        if len(rows)==2:
            before,after=rows
            descriptions=[]
            for field,label,digits in [('sales_per_order','单均商品金额（BRL）',2),('items_per_order','每单明细条数',4),('avg_item_price','平均明细价格（BRL）',2)]:
                a,b=before.get(field),after.get(field)
                if a is not None and b is not None:
                    a,b=Decimal(str(a)),Decimal(str(b))
                    relative=f'，变化 {(b-a)/abs(a)*100:+.2f}%' if a else '，上月为零，不计算变化率'
                    descriptions.append(f'{label}由 {a:,.{digits}f} 变为 {b:,.{digits}f}{relative}')
            summary='；'.join(descriptions)+'。' if descriptions else ''
        return (summary + context['decomposition_note'] + ' 先比较订单量、每单明细条数和平均明细价格，再按品类排查结构；这是恒等式分解，不能据此认定降价或购买意愿变化。有月份没有订单或商品明细时，对应均值留空。')
    return "按商品金额变化从低到高排列，包含未分类商品，便于与经营对比总额核对。商品金额不含运费，也不代表利润；当前结果只能定位下降品类，不能证明下降原因。"


def target_comparison(rows, targets):
    """Compute against the current month's exact result; no model arithmetic."""
    if not rows or not targets:
        return []
    actual = rows[-1]
    output = []
    for field, label, unit, direction in (
        ('orders','订单数','笔','at_least'),
        ('item_sales','商品金额','BRL','at_least'),
        ('late_rate','配送超时率','%','at_most'),
    ):
        if targets.get(field) is None:
            continue
        target = Decimal(str(targets[field]))
        value = actual.get(field)
        value = Decimal(str(value)) if value is not None else None
        # Input rate is in percent; query rate is a fraction.
        if field == 'late_rate':
            target /= 100
        met = value is not None and (value >= target if direction=='at_least' else value <= target)
        gap = value-target if value is not None else None
        achievement = value/target if value is not None and target>0 and direction=='at_least' else None
        output.append({'metric':field,'label':label,'unit':unit,'direction':direction,
            'actual':str(value) if value is not None else None,'target':str(target),
            'gap':str(gap) if gap is not None else None,
            'achievement_rate':str(achievement) if achievement is not None else None,
            'status':('已达标' if met else '未达标') if value is not None else '无法判断'})
    return output
