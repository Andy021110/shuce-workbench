"""Read-only checks against the authorized CSV snapshot, never a live-data claim."""
import json
import calendar
from datetime import datetime, timezone
from .workbench import ROOT
from .database import SCHEMA
from .querying.duckdb_engine import DuckDbEngine

IDENTIFIERS = {
    'olist_orders_dataset':['order_id'], 'olist_customers_dataset':['customer_id'],
    'olist_products_dataset':['product_id'], 'olist_sellers_dataset':['seller_id'],
    'olist_order_items_dataset':['order_id','order_item_id'],
    'olist_order_payments_dataset':['order_id','payment_sequential'],
    'olist_order_reviews_dataset':['review_id'],
    'product_category_name_translation':['product_category_name'],
}

def inspect_readiness(scope, database):
    if not scope.allows_database(database): raise PermissionError('无权访问该数据源')
    tables=[t for t in SCHEMA if t['database']==database and t['id'] in scope.allowed_tables]
    if not tables: raise PermissionError('没有可检查的数据表')
    metadata=json.loads((ROOT/database/'_schema.json').read_text())
    result={'database_id':database,'checked_at':datetime.now(timezone.utc).isoformat(),
            'imported_at':metadata.get('imported_at'),'tables':[], 'coverage':None,
            'note':'检查当前本地文件；检查时间不是数据更新时间。缺失、重复仅提示核查，不自动删除或补值。'}
    with DuckDbEngine().connect(database) as c:
        for table in tables:
            name=table['name']; fields=[f['name'] for f in table['fields']]
            q=lambda field:'"'+field.replace('"','""')+'"'
            measures=['COUNT(*)']+[f'SUM(CASE WHEN {q(f)} IS NULL OR TRIM(CAST({q(f)} AS VARCHAR))=\'\' THEN 1 ELSE 0 END)' for f in fields]
            values=c.execute('SELECT '+','.join(measures)+' FROM '+q(name)).fetchone()
            identifiers=IDENTIFIERS.get(name,[]) if database=='olist_ecommerce' else []
            duplicate=None; missing_key=None
            if identifiers:
                grouped=','.join(map(q,identifiers))
                duplicate=c.execute(f'SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM {q(name)} GROUP BY {grouped} HAVING COUNT(*)>1)').fetchone()[0]
                missing_key=c.execute(f'SELECT COUNT(*) FROM {q(name)} WHERE '+ ' OR '.join(f'{q(k)} IS NULL OR TRIM(CAST({q(k)} AS VARCHAR))=\'\'' for k in identifiers)).fetchone()[0]
            result['tables'].append({'name':name,'label':table.get('label',name),'rows':values[0],
              'missing':[{'field':f,'label':table['fields'][i].get('label',f),'count':values[i+1] or 0} for i,f in enumerate(fields) if values[i+1]],
              'identifier_fields':identifiers,'duplicate_identifier_rows':duplicate,'missing_identifier_rows':missing_key,
              'identifier_note':'评论标识在源数据中可重复，需核对评论更新记录；不是订单重复。' if name=='olist_order_reviews_dataset' else ''})
        if database=='olist_ecommerce' and any(t['name']=='olist_orders_dataset' for t in tables):
            first,last=c.execute('SELECT MIN(CAST(order_purchase_timestamp AS DATE)),MAX(CAST(order_purchase_timestamp AS DATE)) FROM olist_orders_dataset').fetchone()
            incomplete=c.execute("SELECT COUNT(*) FROM olist_orders_dataset WHERE order_status='delivered' AND (order_delivered_customer_date IS NULL OR order_estimated_delivery_date IS NULL)").fetchone()[0]
            monthly={r[0]:{'orders':r[1],'active_days':r[2]} for r in c.execute("SELECT strftime(order_purchase_timestamp,'%Y-%m'),COUNT(*),COUNT(DISTINCT CAST(order_purchase_timestamp AS DATE)) FROM olist_orders_dataset WHERE order_purchase_timestamp IS NOT NULL GROUP BY 1").fetchall()}
            months=[]
            y,m=first.year,first.month
            while (y,m)<=(last.year,last.month):
                key=f'{y:04}-{m:02}'
                months.append({'month':key,**monthly.get(key,{'orders':0,'active_days':0}),'calendar_days':calendar.monthrange(y,m)[1]})
                y,m=(y+1,1) if m==12 else (y,m+1)
            result['coverage']={'start':str(first),'end':str(last),'boundary_months':sorted(set([str(first)[:7],str(last)[:7]])),
                'delivered_missing_comparison_dates':incomplete,'months':months,
                'note':'首尾月份可能不完整；有记录的天数用于核查覆盖，无订单日不等于数据缺失；有记录也不能证明采集完整。'}
    result['warning_tables']=sum(bool(t['missing'] or t['duplicate_identifier_rows']) for t in result['tables'])
    return result
