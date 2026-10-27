from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from app.database import SCHEMA
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController

DATA = Path(__file__).resolve().parents[1] / "data/databases/olist_ecommerce"


@unittest.skipUnless((DATA / "_schema.json").exists(), "Olist data is not installed")
class OlistDataTest(unittest.TestCase):
    def test_raw_order_and_money_baselines(self):
        engine = DuckDbEngine()
        with engine.connect("olist_ecommerce") as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*),COUNT(DISTINCT order_id) FROM olist_orders_dataset").fetchone(), (99441, 99441))
            self.assertEqual(conn.execute("SELECT SUM(CAST(price AS DECIMAL(18,2))) FROM olist_order_items_dataset").fetchone()[0], Decimal("13591643.70"))
            self.assertEqual(conn.execute("SELECT SUM(CAST(payment_value AS DECIMAL(18,2))) FROM olist_order_payments_dataset").fetchone()[0], Decimal("16008872.12"))

    def test_category_join_does_not_drop_unclassified_products_or_inflate_money(self):
        with DuckDbEngine().connect("olist_ecommerce") as conn:
            count, total = conn.execute("""
                SELECT COUNT(*),SUM(CAST(i.price AS DECIMAL(18,2)))
                FROM olist_order_items_dataset i
                LEFT JOIN olist_products_dataset p USING(product_id)
                LEFT JOIN product_category_name_translation t USING(product_category_name)
            """).fetchone()
        self.assertEqual(count, 112650)
        self.assertEqual(total, Decimal("13591643.70"))

    def test_selecting_database_enforces_table_and_tool_boundary(self):
        controller = AccessController()
        scope = controller.select_database(controller.resolve("demo_admin"), "olist_ecommerce")
        self.assertEqual(len(controller.filter_schema(scope)), 9)
        self.assertEqual(scope.allowed_databases, frozenset({"olist_ecommerce"}))
        engine = DuckDbEngine()
        allowed = engine.execute("olist_ecommerce", "SELECT COUNT(DISTINCT order_id) AS orders FROM olist_orders_dataset", scope)
        denied = engine.execute("short_video_ops", "SELECT COUNT(user_id) AS users FROM users", scope)
        self.assertTrue(allowed.success, allowed.error)
        self.assertEqual(allowed.rows, [{"orders": 99441}])
        self.assertFalse(denied.success)
        with self.assertRaises(PermissionError):
            controller.select_database(controller.resolve("demo_growth_ops"), "olist_ecommerce")

    def test_raw_multiple_fact_money_join_is_rejected_and_preaggregation_is_allowed(self):
        engine = DuckDbEngine()
        scope = AccessController().resolve("demo_admin")
        bad = engine.execute("olist_ecommerce", "SELECT SUM(i.price) AS sales, SUM(p.payment_value) AS payments FROM olist_order_items_dataset i JOIN olist_order_payments_dataset p USING(order_id)", scope)
        self.assertFalse(bad.success)
        self.assertIn("预聚合", bad.error)
        good = engine.execute("olist_ecommerce", """WITH item_totals AS (
            SELECT order_id, SUM(price) AS sales FROM olist_order_items_dataset GROUP BY order_id
        ), payment_totals AS (
            SELECT order_id, SUM(payment_value) AS paid FROM olist_order_payments_dataset GROUP BY order_id
        ) SELECT ROUND(SUM(sales),2) AS sales, ROUND(SUM(paid),2) AS paid
          FROM item_totals FULL OUTER JOIN payment_totals USING(order_id)""", scope)
        self.assertTrue(good.success, good.error)
        self.assertEqual(good.rows[0], {"sales":13591643.7,"paid":16008872.12})

    def test_original_scenario_and_schema_metadata_are_preserved(self):
        self.assertEqual(len([table for table in SCHEMA if table["database"] == "short_video_ops"]), 41)
        self.assertEqual(len(AccessController().resolve("demo_growth_ops").allowed_tables), 18)
        catalog = json.loads((DATA / "_schema.json").read_text())
        self.assertEqual(len(catalog["relations"]), 7)
        self.assertEqual(sum(len(table["fields"]) for table in catalog["tables"]), 52)
        money = [field for table in catalog["tables"] for field in table["fields"] if field["name"] in {"price", "payment_value", "freight_value"}]
        self.assertTrue(all("BRL" in field["data_profile"]["unit"] for field in money))


if __name__ == "__main__":
    unittest.main()
