import categoryLabels from './categoryLabels.json' with {type:'json'}
export function categoryLabel(value: string) { return (categoryLabels as Record<string,string>)[value] || value }
const labels: Record<string, string> = {
  returning_buyers:'本期历史老客数',returning_rate:'本期老客占比',reviewed_order_count:'有评价订单数',review_record_count:'评价记录数',avg_order_review_score:'订单级平均评分',bed_bath_table_orders:'床品卫浴订单数',furniture_decor_orders:'家具装饰订单数',union_orders:'至少购买一个品类的订单数',intersection_orders:'同时购买两个品类的订单数',seller_id:'商家编号',valid_orders:'有效订单数',valid_samples:'有效样本数',avg_days:'平均时长（天）',p90_days:'P90时长（天）',order_month:'下单月份',purchase_month:'下单月份',diff:'差值',
  carrier_date_missing:'承运商交接日期缺失数',delivered_date_missing:'实际送达日期缺失数',payment_minus_item:'支付减商品金额',full_order_payments:'同批订单整单支付',
  p2c_valid_samples:'下单至交接 · 有效样本数',p2c_avg_days:'下单至交接 · 平均天数',p2c_p90_days:'下单至交接 · P90天数',
  c2d_valid_samples:'交接至送达 · 有效样本数',c2d_avg_days:'交接至送达 · 平均天数',c2d_p90_days:'交接至送达 · P90天数',
  delivered_status:'已送达',canceled:'已取消',shipped:'已发货',unavailable:'不可用',invoiced:'已开票',processing:'处理中',approved:'已批准',created:'已创建',
  orders_with_items:'有商品明细的订单数', item_rows:'商品明细条数', sales_per_order:'单均商品金额', items_per_order:'每单明细条数', avg_item_price:'平均明细价格', period:'月份', month:'月份', order_count:'订单数', orders:'订单数', item_sales:'商品金额', payments:'支付金额',
  delivered:'可比较已送达订单数', delivered_orders:'可比较已送达订单数', late:'超时订单数',
  late_rate:'配送超时率', delivery_late_rate:'配送超时率', customer_state:'客户州', category:'商品品类',
  previous_sales:'上月商品金额', current_sales:'本月商品金额', sales_change:'商品金额变化',
  jul_sales:'7月商品金额', aug_sales:'8月商品金额', late_jul:'7月超时订单数', late_aug:'8月超时订单数',
  previous_delivered:'上月可比较已送达数', current_delivered:'本月可比较已送达数',
  previous_late:'上月超时订单数', current_late:'本月超时订单数', new_late_orders:'新增超时订单数',
  previous_late_rate:'上月超时率', current_late_rate:'本月超时率', buyers:'买家数', repeat_buyers:'复购买家数',repeat_rate:'复购率',
}
export function businessLabel(field: string) { return labels[field] || field }
export function statusLabel(status:string){return status==='delivered'?'已送达':businessLabel(status)}
export function businessDefinition(field:string){
  if(/(?:p2c|c2d)_valid_samples|valid_orders|valid_samples/.test(field))return '用于本项计算的有效样本；具体去重对象和剔除规则以本次问题与SQL为准。'
  if(/p90.*days/.test(field))return '时长的第90百分位，单位为天，可含小数。表示约90%的有效样本不超过此时长，不代表超时率。'
  if(/avg_days/.test(field))return '有效样本时长的算术平均值，单位为天，可含小数；缺失值不能按0天解释。'
  if(/date_missing/.test(field))return '该日期为空的订单数；与负时长剔除数是不同概念。'
  if(/returning_buyers|returning_rate/.test(field))return '本期去重买家中，样本内在期初之前曾购买的人及其占比；与全样本复购率不同。无本期买家时占比不可计算。'
  if(/reviewed_order_count|review_record_count|avg_order_review_score/.test(field))return '有评价订单按order_id去重；评价记录可一单多条。平均评分先按订单求均值，再对有评价订单平均，不能据多条记录认定刷评。'
  if(/union_orders|intersection_orders|bed_bath_table_orders|furniture_decor_orders/.test(field))return '订单按order_id去重；并集为至少买一个品类，交集为同时购买两个品类。品类订单数不能直接相加。'
  if(field==='seller_id')return '源数据中的匿名商家编号。跨商家订单可以各计一次，商家订单数不能直接加总当作平台去重订单数。'
  if(/payment_minus_item|full_order_payments/.test(field))return '整单支付未按品类分摊。支付与商品金额差额不能直接当作利润。'
  if(field==='diff')return '两项指标的差值，计算方向和单位以本次问题与SQL为准；金额差值不能直接解释为利润。'
  return ''
}
export function businessCell(value: unknown, field: string, currency='') {
  if(value == null || value === '') return '—'
  if(typeof value !== 'number') return /category|品类/.test(field) ? categoryLabel(String(value)) : String(value)
  if(/rate|ratio|percent|率|占比/i.test(field)) return new Intl.NumberFormat('zh-CN',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(value)
  if(currency && /金额|销售额|客单价|price|freight|payment|revenue|sales|amount/i.test(field)) return new Intl.NumberFormat('zh-CN',{style:'currency',currency}).format(value)
  return new Intl.NumberFormat('zh-CN',{maximumFractionDigits:4}).format(value)
}
