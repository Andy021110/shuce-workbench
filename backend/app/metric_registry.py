"""Versioned Olist definitions shared by dashboard, canonical queries and agent guidance."""
from __future__ import annotations
from datetime import date, timedelta
import re

VERSION="1.0"
DATABASE="olist_ecommerce"
DEFINITIONS=[
    {"id":"order_count","label":"订单数","definition":"按 order_id 去重，包含全部订单状态。", "grain":"订单", "currency":None,"grains":["total","month","day"],"outputs":["orders"]},
    {"id":"item_sales","label":"商品金额","definition":"商品明细 price 之和，不含运费；包含全部订单状态，不代表净收入或利润。", "grain":"订单商品明细","currency":"BRL","grains":["total","month","day"],"outputs":["item_sales"]},
    {"id":"payments","label":"支付金额","definition":"支付记录 payment_value 之和；包含全部订单状态，不扣除退款，不代表净收入。", "grain":"支付记录","currency":"BRL","grains":["total","month","day"],"outputs":["payments"]},
    {"id":"delivery_late","label":"配送超时率","definition":"仅 delivered 且实际、预计送达时间均不为空；实际送达 DATE 大于预计送达 DATE 为超时。超时率为 late / delivered。按下单时间划分订单范围。", "grain":"可比较的已送达订单","currency":None,"grains":["total","month","day"],"outputs":["delivered","late","late_rate"]},
    {"id":"repeat_buyers","label":"复购买家","definition":"用 customer_unique_id 识别买家，所选下单时间范围内去重订单数大于 1 为复购买家；包含全部状态。repeat_rate 为 repeat_buyers / buyers。", "grain":"唯一买家","currency":None,"grains":["total"],"outputs":["buyers","repeat_buyers","repeat_rate"]},
]

def definitions():
    return [{**d,"version":VERSION,"database_id":DATABASE,"time_field":"order_purchase_timestamp", "status_scope":"全部订单状态" if d["id"]!="delivery_late" else "delivered，且实际和预计送达日期均存在"} for d in DEFINITIONS]

def get_definition(metric_id):
    definition=next((d for d in definitions() if d["id"]==metric_id),None)
    if not definition: raise ValueError("指标不存在")
    return definition

def metric_query(metric_id, start_date=None, end_date=None, grain="total"):
    spec=get_definition(metric_id)
    if grain not in spec["grains"]: raise ValueError("该指标不支持此时间粒度")
    start=date.fromisoformat(str(start_date)) if start_date else None
    end=date.fromisoformat(str(end_date)) if end_date else None
    if start and end and start>end: raise ValueError("开始日期不能晚于结束日期")
    if end and end==date.max: raise ValueError("结束日期超出支持范围")
    conditions=[]
    if start: conditions.append(f"o.order_purchase_timestamp >= DATE '{start}'")
    if end: conditions.append(f"o.order_purchase_timestamp < DATE '{end+timedelta(days=1)}'")
    where=" WHERE "+" AND ".join(conditions) if conditions else ""
    period="strftime(o.order_purchase_timestamp, '%Y-%m')" if grain=="month" else "CAST(o.order_purchase_timestamp AS DATE)"
    prefix=period+" AS period, " if grain!="total" else ""
    suffix=" GROUP BY period ORDER BY period" if grain!="total" else ""
    source="olist_orders_dataset o"
    if metric_id=="order_count": measure="COUNT(DISTINCT o.order_id) AS orders"
    elif metric_id=="item_sales":
        source="olist_order_items_dataset i JOIN olist_orders_dataset o USING(order_id)"
        measure="COALESCE(SUM(CAST(i.price AS DECIMAL(18,2))),0) AS item_sales"
    elif metric_id=="payments":
        source="olist_order_payments_dataset p JOIN olist_orders_dataset o USING(order_id)"
        measure="COALESCE(SUM(CAST(p.payment_value AS DECIMAL(18,2))),0) AS payments"
    elif metric_id=="delivery_late":
        conditions.extend(["o.order_status='delivered'","o.order_delivered_customer_date IS NOT NULL","o.order_estimated_delivery_date IS NOT NULL"])
        where=" WHERE "+" AND ".join(conditions)
        late="COUNT(DISTINCT CASE WHEN CAST(o.order_delivered_customer_date AS DATE)>CAST(o.order_estimated_delivery_date AS DATE) THEN o.order_id END)"
        delivered="COUNT(DISTINCT o.order_id)"
        measure=f"{delivered} AS delivered, {late} AS late, CAST({late} AS DOUBLE)/NULLIF({delivered},0) AS late_rate"
    else:
        sql=f"""WITH buyer AS (SELECT c.customer_unique_id, COUNT(DISTINCT o.order_id) AS n
            FROM olist_customers_dataset c JOIN olist_orders_dataset o USING(customer_id){where} GROUP BY c.customer_unique_id)
            SELECT COUNT(*) AS buyers, COUNT(CASE WHEN n>1 THEN 1 END) AS repeat_buyers,
            CAST(COUNT(CASE WHEN n>1 THEN 1 END) AS DOUBLE)/NULLIF(COUNT(*),0) AS repeat_rate FROM buyer"""
        return sql, {**spec,"parameters":{"start_date":str(start) if start else None,"end_date":str(end) if end else None,"grain":grain}}
    sql=f"SELECT {prefix}{measure} FROM {source}{where}{suffix}"
    return sql,{**spec,"parameters":{"start_date":str(start) if start else None,"end_date":str(end) if end else None,"grain":grain}}

def guidance(database):
    if database!=DATABASE: return ""
    return "统一 Olist 指标口径 v"+VERSION+"：\n"+"\n".join(
        f"{d['label']}（{d['id']}）：{d['definition']} 时间字段 {d['time_field']}；金额单位 {d['currency'] or '不适用'}。" for d in definitions())+"\n用户明确指定不同状态、日期字段、币种或业务范围时应遵循用户，并明确标为自定义口径；不可声称与标准一致。不同事实表必须先按订单预聚合再关联。"

def references(sql):
    text=(sql or "").lower()
    ids=set()
    if re.search(r"count\s*\(\s*distinct\s+(?:[a-z_]+\.)?order_id",text): ids.add("order_count")
    if "price" in text: ids.add("item_sales")
    if "payment_value" in text: ids.add("payments")
    if "order_delivered_customer_date" in text: ids.add("delivery_late")
    if "customer_unique_id" in text: ids.add("repeat_buyers")
    return [d for d in definitions() if d["id"] in ids]


def add_metric_totals(profile, contract):
    """Only canonical queries have proven additive grains across the selected periods."""
    from decimal import Decimal
    from .result_profile import decimal_text
    if not profile or profile.get("coverage")!="full_result": return
    totals={item["field"]:item["sum"] for item in profile["numeric"] if item["sum"] is not None}
    if contract["id"]=="delivery_late" and totals.get("delivered") is not None and totals.get("late") is not None:
        denominator=Decimal(totals["delivered"])
        totals["late_rate"]=decimal_text(Decimal(totals["late"])/denominator) if denominator else None
    if contract["id"]=="repeat_buyers" and totals.get("buyers") is not None and totals.get("repeat_buyers") is not None:
        denominator=Decimal(totals["buyers"])
        totals["repeat_rate"]=decimal_text(Decimal(totals["repeat_buyers"])/denominator) if denominator else None
    profile["metric_totals"]={"metric_id":contract["id"],"version":contract["version"],"values":totals,
        "note":"标准查询在所选期间内的整体指标；比率按整体分子除以整体分母计算，不使用分组比率均值。"}
