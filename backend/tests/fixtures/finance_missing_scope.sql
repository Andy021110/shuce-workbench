WITH cat_orders AS (
  SELECT DISTINCT oi.order_id
  FROM olist_order_items_dataset oi
  JOIN olist_products_dataset p ON oi.product_id = p.product_id
  JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name
  WHERE t.product_category_name_english = 'watches_gifts'
),
scoped AS (
  SELECT o.order_id
  FROM olist_orders_dataset o
  JOIN cat_orders c ON o.order_id = c.order_id
  WHERE o.order_status = 'delivered'
    AND o.order_purchase_timestamp >= TIMESTAMP '2018-07-01 00:00:00'
    AND o.order_purchase_timestamp < TIMESTAMP '2018-09-01 00:00:00'
),
item_agg AS (
  SELECT oi.order_id, SUM(oi.price) AS item_sales
  FROM olist_order_items_dataset oi
  JOIN olist_products_dataset p ON oi.product_id = p.product_id
  JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name
  JOIN scoped s ON oi.order_id = s.order_id
  WHERE t.product_category_name_english = 'watches_gifts'
  GROUP BY oi.order_id
),
pay_agg AS (
  SELECT pay.order_id, SUM(pay.payment_value) AS payments
  FROM olist_order_payments_dataset pay
  JOIN scoped s ON pay.order_id = s.order_id
  GROUP BY pay.order_id
)
SELECT COUNT(DISTINCT s.order_id) AS order_count,
       ROUND(SUM(i.item_sales), 2) AS item_sales,
       ROUND(SUM(p.payments), 2) AS payments,
       ROUND(SUM(p.payments) - SUM(i.item_sales), 2) AS diff
FROM scoped s
LEFT JOIN item_agg i ON s.order_id = i.order_id
LEFT JOIN pay_agg p ON s.order_id = p.order_id