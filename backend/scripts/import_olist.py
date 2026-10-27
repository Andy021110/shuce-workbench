"""Import the unmodified Kaggle Olist CSVs and build a Chinese schema/catalog.

Usage: .venv/bin/python scripts/import_olist.py /absolute/path/to/olist.zip
No model calls. Original text, dates, currency and missing values are preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

BACKEND = Path(__file__).resolve().parents[1]
DATABASE = "olist_ecommerce"
SOURCE = "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"
EXPECTED_SHA256 = "967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784"

TABLES = {
    "olist_orders_dataset": ("订单", "每行一笔订单，订单数用 COUNT(DISTINCT order_id)。一笔订单可有多个商品、支付记录或评价，跨明细表连接前应分别按订单聚合，避免金额重复。", ["order_id"]),
    "olist_order_items_dataset": ("订单商品明细", "每行一个订单商品项。商品销售额为 SUM(price)，不含运费、不等于利润；运费为 freight_value。品类销售额需关联商品表。", ["order_id", "order_item_id"]),
    "olist_order_payments_dataset": ("订单支付记录", "每行一次支付记录，同一订单可能多次或多种方式支付。总支付金额 SUM(payment_value) 包含运费，不等于商品销售额。连接商品明细前先按订单汇总。", ["order_id", "payment_sequential"]),
    "olist_order_reviews_dataset": ("订单评价", "每行一条评价记录；同一订单可能多条评价。文本为葡萄牙语，评分 1 至 5。与订单分析关联前按订单聚合评分，不能将评论当作企业政策。", []),
    "olist_customers_dataset": ("订单客户", "customer_id 对应一次订单中的客户身份；跨订单识别同一客户和复购须用 customer_unique_id，不能按 customer_id 计算复购。", ["customer_id"]),
    "olist_products_dataset": ("商品", "商品品类、重量及尺寸。品类原文为葡萄牙语，可关联品类英文对照表；没有商品成本，不能计算利润。", ["product_id"]),
    "olist_sellers_dataset": ("卖家", "卖家所在城市和州；同一订单可以由多个卖家履约。", ["seller_id"]),
    "olist_geolocation_dataset": ("邮编地理坐标", "邮编前缀可对应多条地理坐标，不是唯一键。用于地图展示前须按邮编聚合，禁止将原始多行坐标直接连接订单汇总金额。", []),
    "product_category_name_translation": ("品类英文对照", "商品品类葡萄牙语原文及英文对照，并非中文翻译。", ["product_category_name"]),
}

# Labels are metadata only: original column names and original data remain unchanged.
LABELS = {
    "order_id": "订单编号", "customer_id": "订单客户编号", "customer_unique_id": "跨订单客户唯一编号",
    "order_status": "订单状态", "order_purchase_timestamp": "下单时间", "order_approved_at": "支付确认时间",
    "order_delivered_carrier_date": "交给承运商时间", "order_delivered_customer_date": "实际送达时间",
    "order_estimated_delivery_date": "预计送达日期", "order_item_id": "订单内商品序号",
    "product_id": "商品编号", "seller_id": "卖家编号", "shipping_limit_date": "最迟发货时间",
    "price": "商品金额", "freight_value": "运费", "payment_sequential": "订单内支付序号",
    "payment_type": "支付方式", "payment_installments": "支付分期数", "payment_value": "支付金额",
    "review_id": "评价编号", "review_score": "评价评分", "review_comment_title": "评价标题",
    "review_comment_message": "评价正文", "review_creation_date": "评价问卷创建时间", "review_answer_timestamp": "评价提交时间",
    "customer_zip_code_prefix": "客户邮编前缀", "customer_city": "客户城市", "customer_state": "客户州",
    "seller_zip_code_prefix": "卖家邮编前缀", "seller_city": "卖家城市", "seller_state": "卖家州",
    "geolocation_zip_code_prefix": "地理邮编前缀", "geolocation_lat": "纬度", "geolocation_lng": "经度",
    "geolocation_city": "地理城市", "geolocation_state": "地理州", "product_category_name": "商品品类葡萄牙语名",
    "product_category_name_english": "商品品类英文名", "product_name_lenght": "商品名称长度",
    "product_description_lenght": "商品描述长度", "product_photos_qty": "商品照片数量", "product_weight_g": "商品重量克",
    "product_length_cm": "商品长度厘米", "product_height_cm": "商品高度厘米", "product_width_cm": "商品宽度厘米",
}

RELATIONS = [
    ("olist_orders_dataset", "customer_id", "olist_customers_dataset", "customer_id"),
    ("olist_order_items_dataset", "order_id", "olist_orders_dataset", "order_id"),
    ("olist_order_payments_dataset", "order_id", "olist_orders_dataset", "order_id"),
    ("olist_order_reviews_dataset", "order_id", "olist_orders_dataset", "order_id"),
    ("olist_order_items_dataset", "product_id", "olist_products_dataset", "product_id"),
    ("olist_order_items_dataset", "seller_id", "olist_sellers_dataset", "seller_id"),
    ("olist_products_dataset", "product_category_name", "product_category_name_translation", "product_category_name"),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    if digest(archive) != EXPECTED_SHA256:
        raise ValueError("压缩包版本与已核对版本不符；请先复核来源和校验值，禁止静默替换数据。")
    destination = BACKEND / "data" / "databases" / DATABASE
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("压缩包校验失败")
        for name in TABLES:
            # Extract only explicit CSV names, never arbitrary archive paths.
            with bundle.open(name + ".csv") as src, (destination / (name + ".csv")).open("wb") as out:
                shutil.copyfileobj(src, out)

    conn = duckdb.connect()
    tables, files = [], []
    for name, (label, description, primary_key) in TABLES.items():
        csv_path = destination / (name + ".csv")
        escaped = str(csv_path).replace("'", "''")
        conn.execute(f'CREATE TABLE "{name}" AS SELECT * FROM read_csv_auto(\'{escaped}\',sample_size=-1)')
        count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        fields = []
        for column, kind, *_ in conn.execute(f'DESCRIBE "{name}"').fetchall():
            label_col = LABELS[column]
            temporal = kind in {"TIMESTAMP", "DATE"}
            identifier = column.endswith("_id") or column.endswith("_prefix") or column in {"order_item_id", "payment_sequential"}
            numeric = kind in {"DOUBLE", "BIGINT", "INTEGER"} and not identifier
            role = "time" if temporal else "identifier" if identifier else "measure" if numeric else "dimension"
            stats = conn.execute(f'SELECT COUNT("{column}"), COUNT(DISTINCT "{column}") FROM "{name}"').fetchone()
            samples = conn.execute(f'SELECT DISTINCT "{column}" FROM "{name}" WHERE "{column}" IS NOT NULL ORDER BY "{column}" LIMIT 5').fetchall()
            profile = {"row_count": count, "null_ratio": round(1-stats[0]/count, 6), "distinct_count": stats[1], "sample_values": [str(row[0]) if temporal else row[0] for row in samples]}
            if temporal or numeric:
                low, high = conn.execute(f'SELECT MIN("{column}"), MAX("{column}") FROM "{name}"').fetchone()
                profile.update(min=str(low) if temporal else low, max=str(high) if temporal else high)
            if column in {"price", "freight_value", "payment_value"}:
                profile["unit"] = "BRL（巴西雷亚尔），不可标注为人民币"
            details = label_col
            if column == "order_estimated_delivery_date":
                details += "；为日期粒度，判定超时应比较 CAST(实际送达时间 AS DATE) > CAST(预计送达日期 AS DATE)，同一天送达不算超时。"
            if column == "order_status":
                details += "；delivered=已送达，canceled=已取消，unavailable=缺货，shipped=已发货。全量订单须明确是否包含取消订单。"
            if column == "review_score":
                details += "；1到5分，一般以1到2分作为低评分，使用时应注明口径。"
            fields.append({"name": column, "label": label_col, "description": details, "type": "日期" if temporal else "数值" if numeric else "文本", "source_type": kind, "role": role, "aggregation": "none", "aliases": [label_col], "data_profile": profile})
        tables.append({"id": f"{DATABASE}.{name}", "name": name, "label": "Olist · " + label, "description": "Olist 真实匿名历史电商数据，2016—2018年，金额单位BRL。" + description, "database": DATABASE, "domain": "Olist电商经营", "business_terms": ["Olist", label, "电商", "真实订单"], "primary_key": primary_key, "row_count": count, "fields": fields})
        files.append({"file": csv_path.name, "rows": count, "columns": len(fields), "bytes": csv_path.stat().st_size, "sha256": digest(csv_path)})
    relations = [{"id": f"olist_relation_{i}", "left_table": f"{DATABASE}.{a}", "left_field": b, "right_table": f"{DATABASE}.{c}", "right_field": d, "relation_type": "foreign_key", "description": "关联前检查两侧数据粒度，避免一对多明细连接放大计数或金额"} for i, (a,b,c,d) in enumerate(RELATIONS)]
    catalog = {"schema_version": "1.0", "database": DATABASE, "scenario": "Olist 巴西电商历史经营分析", "source": SOURCE, "license": "CC BY-NC-SA 4.0", "tables": tables, "relations": relations}
    (destination / "_schema.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    dates = conn.execute("SELECT MIN(order_purchase_timestamp),MAX(order_purchase_timestamp),COUNT(*),COUNT(DISTINCT order_id) FROM olist_orders_dataset").fetchone()
    checks = {
        "order_dates": [str(dates[0]), str(dates[1])], "orders": dates[2], "unique_orders": dates[3],
        "item_value_brl": str(conn.execute("SELECT SUM(CAST(price AS DECIMAL(18,2))) FROM olist_order_items_dataset").fetchone()[0]),
        "payment_value_brl": str(conn.execute("SELECT SUM(CAST(payment_value AS DECIMAL(18,2))) FROM olist_order_payments_dataset").fetchone()[0]),
        "items_without_order": conn.execute("SELECT COUNT(*) FROM olist_order_items_dataset i LEFT JOIN olist_orders_dataset o USING(order_id) WHERE o.order_id IS NULL").fetchone()[0],
        "orders_without_customer": conn.execute("SELECT COUNT(*) FROM olist_orders_dataset o LEFT JOIN olist_customers_dataset c USING(customer_id) WHERE c.customer_id IS NULL").fetchone()[0],
    }
    assert checks["orders"] == checks["unique_orders"] == 99441
    assert checks["items_without_order"] == checks["orders_without_customer"] == 0
    manifest = {"database": DATABASE, "source": SOURCE, "download_endpoint": "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce", "imported_at": datetime.now(timezone.utc).isoformat(), "license": "CC BY-NC-SA 4.0", "attribution": "Brazilian E-Commerce Public Dataset by Olist — Olist and collaborators, Kaggle", "archive_sha256": EXPECTED_SHA256, "table_count": len(tables), "field_count": sum(len(t["fields"]) for t in tables), "files": files, "checks": checks}
    (destination / "_database_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
