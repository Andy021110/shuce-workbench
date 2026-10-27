"""Server-owned time basis carried across an Olist analysis conversation."""
from __future__ import annotations

import re
from sqlglot import exp, parse_one

TIME_FIELDS = {
    "order_purchase_timestamp": "下单时间",
    "shipping_limit_date": "最迟发货时间",
    "order_delivered_carrier_date": "交给承运商时间",
    "order_delivered_customer_date": "实际送达时间",
    "order_estimated_delivery_date": "预计送达日期",
    "order_approved_at": "支付确认时间",
}


def period_fields(sql: str) -> set[str]:
    """Exclude date-to-date lateness tests; inspect date literals and grouping."""
    try:
        tree = parse_one(sql, read="duckdb")
    except Exception:
        return set()
    fields = set()
    for node in tree.walk():
        if isinstance(node, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)):
            has_date = any(v.is_string and re.match(r"^\d{4}-\d{2}(?:-\d{2})?", v.this)
                           for v in node.find_all(exp.Literal))
            if has_date:
                fields.update(c.name for c in node.find_all(exp.Column) if c.name in TIME_FIELDS)
        if isinstance(node, exp.Group):
            fields.update(c.name for c in node.find_all(exp.Column) if c.name in TIME_FIELDS)
        # GROUP BY an alias is common; STRFTIME(date, '%Y-%m') defines its basis.
        if isinstance(node, exp.TimeToStr):
            fields.update(c.name for c in node.find_all(exp.Column) if c.name in TIME_FIELDS)
    return fields


def basis_for(query: str, previous: dict | None = None) -> dict:
    explicit = None
    for field, label in TIME_FIELDS.items():
        # Mentioning delivery as a metric is not a request to change the cohort date.
        for name in (field, label):
            if re.search(r"(?:按|依据|基于|使用|改用|换成|以)\s*" + re.escape(name), query):
                explicit = field
    field = explicit or (previous or {}).get("time_field") or "order_purchase_timestamp"
    if field not in TIME_FIELDS:
        field = "order_purchase_timestamp"
    changed = bool(explicit and previous and explicit != previous.get("time_field"))
    return {"time_field": field, "time_label": TIME_FIELDS[field], "currency": "BRL",
            "source": "explicit_change" if changed else "explicit" if explicit else "inherited" if previous else "default",
            "previous_time_label": (previous or {}).get("time_label") if changed else None,
            "note": "月份与日期筛选均按此时间字段；更改日期依据时请明确说明。"}


def basis_error(sql: str, basis: dict) -> str:
    field = basis.get("time_field")
    if not field:
        return ""
    actual = period_fields(sql)
    different = actual - {field}
    if different:
        return (f"统计时间口径发生变化：必须按 {field}（{basis['time_label']}）筛选和分组，"
                f"不得改用 {', '.join(sorted(different))}。需要关联订单表时通过 order_id 关联。"
                "实际送达与预计日期比较仅用于判定超时，不改变下单月份范围。")
    return ""


def actual_basis(sql: str, requested: dict) -> dict:
    fields = period_fields(sql)
    return {**requested, "verified_time_fields": sorted(fields),
            "verified": bool(fields) and fields == {requested.get("time_field")}}
