"""Browsable business cases, computed from the same authorized monthly queries.

Browsing does not create sessions, snapshot archives, approvals or model claims.
The business brief and follow-ups are scenario examples; measurements are real.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json

from .analysis_basis import actual_basis, basis_for
from .analysis_scope import scope_card
from .config import BASE_DIR
from .metric_registry import DATABASE
from .monthly_review import review_query, review_analysis, STEPS
from .querying.duckdb_engine import DuckDbEngine
from .report_facts import build_facts, factual_brief

ORDER = ('overview', 'sales_drivers', 'sales_categories', 'delivery_states')
EXAMPLES = (
    dict(id='monthly-review', title='订单增加，商品金额为什么下降？',
         tag='完整月度复盘', persona='经营运营负责人', month='2018-08', filters={},
         description='从全局变化出发，拆解单均金额，再定位品类与配送变化。',
         objective='准备一份能核对数字、区分事实与假设的月度经营复盘。',
         focus='overview',
         followups=[
             dict(title='核查金额下降的构成', step='sales_drivers', reason='先区分每单明细条数与平均明细价格的变化，再判断需要查什么。', question='请按下单时间比较2018年8月与7月各商品的明细条数、商品金额和平均明细价格，保持当前分析范围；不要推断降价或促销原因。'),
             dict(title='定位需要优先核查的品类', step='sales_categories', reason='品类结果按商品金额变化排序；下降不等于已找到业务原因。', question='请按下单时间比较2018年8月与7月商品金额下降最多的品类，包含未分类，并说明商品金额不含运费。'),
         ]),
    dict(id='sp-watches', title='SP 州手表礼品，销售与配送一起变差了',
         tag='带范围的异常排查', persona='区域经营分析人员', month='2018-08',
         filters={'states':['SP'], 'order_statuses':['delivered'], 'categories':['watches_gifts']},
         description='只看已送达且包含手表礼品的 SP 订单，沿同一范围完成四步分析。',
         objective='找出区域与品类范围内的变化，形成下一步核查清单。', focus='delivery_states',
         followups=[
             dict(title='拆开交运前耗时与运输耗时', step='delivery_states', reason='超时率上升已经发生，但当前汇总不能确定发生在哪个环节。', question='请按下单时间比较2018年8月与7月SP州已送达且包含watches_gifts的订单，分别计算下单到交给承运商、交给承运商到实际送达的耗时，说明缺失日期如何处理。'),
             dict(title='核查品类内部的商品结构', step='sales_categories', reason='本次只选择了一个品类，需要继续查询商品维度，不能外推其他品类。', question='请按下单时间比较2018年8月与7月SP州已送达订单中watches_gifts各商品的明细条数、金额与平均明细价格，仅统计该品类商品。'),
         ]),
    dict(id='delivery-investigation', title='配送超时增加，先核查哪些州？',
         tag='配送专题复盘', persona='履约运营分析人员', month='2018-08', filters={},
         description='同时看新增超时订单、可比较订单基数与超时率，避免只按绝对增量下结论。',
         objective='准备有依据的配送核查顺序，保留原因尚未确认的边界。', focus='delivery_states',
         followups=[
             dict(title='结合订单基数确定核查顺序', step='delivery_states', reason='大州新增超时订单多，不等于履约比例最差；需同时核对分子和分母。', question='请按下单时间比较2018年8月与7月各州可比较已送达订单数、超时订单数、超时率及变化，保留零与空值的区别。'),
             dict(title='继续核查配送过程', step='overview', reason='预计与实际送达的比较可以定位现象，业务原因仍需要进一步验证。', question='请按下单时间比较2018年8月与7月已送达订单的交运前耗时与运输耗时，按客户州分组，并说明日期缺失的排除数量。'),
         ]),
)


def _authorize(scope):
    if not scope.allows_database(DATABASE):
        raise PermissionError('当前账号无权浏览 Olist 业务场景，请选择有权限的数据源')


def example_catalog(scope):
    _authorize(scope)
    cases=[deepcopy({key:value for key,value in case.items() if key != 'followups'}) for case in EXAMPLES]
    for case in cases: case['filters']={key:case['filters'].get(key,[]) for key in ('states','order_statuses','categories')}
    return cases


def example_detail(scope, example_id):
    _authorize(scope)
    case = next((case for case in EXAMPLES if case['id']==example_id), None)
    if not case: raise KeyError('场景不存在')
    output=deepcopy(case)
    engine=DuckDbEngine()
    results=[]
    for step in ORDER:
        sql,context,contracts=review_query(case['month'],step,case['filters'])
        execution=engine.execute(DATABASE,sql,scope)
        try:
            if not execution.success:
                raise ValueError('场景数据计算失败：'+str(execution.error or '请稍后重试'))
            basis=actual_basis(execution.sql,basis_for('按下单时间'))
            results.append(dict(task_id=f'example_{example_id}_{step}',step=step,
                title=f"{case['month']} · {STEPS[step]}", query=f"{case['title']} · {STEPS[step]}",
                columns=execution.columns,rows=execution.rows,row_count=execution.total_rows,
                result_profile=execution.result_profile,analysis_basis=basis,
                review_context=context,metric_contracts=contracts,
                analysis_scope=scope_card(execution.sql,context['filters'],context,basis),
                sql=execution.sql,analysis=review_analysis(execution.rows,context)))
        finally:
            # Preview values remain in this response. Browsing creates no saved analysis.
            if execution.snapshot_id:
                (BASE_DIR/'data/result_snapshots'/f'{execution.snapshot_id}.json').unlink(missing_ok=True)
    overview=results[0]['rows']
    categories=results[2]['rows']
    states=results[3]['rows']
    reconciliations=[]
    for index,prefix in [(0,'previous'),(1,'current')]:
        amounts=sum((Decimal(str(row[prefix+'_sales'])) for row in categories),Decimal(0))
        amounts_match=amounts==Decimal(str(overview[index]['item_sales']))
        delivery_match=all(sum(row[prefix+'_'+field] for row in states)==overview[index][field] for field in ('delivered','late'))
        reconciliations.append(dict(title=overview[index]['period']+' 品类金额与总览一致',passed=amounts_match))
        reconciliations.append(dict(title=overview[index]['period']+' 地区配送分子、分母与总览一致',passed=delivery_match))
    if not all(check['passed'] for check in reconciliations):
        raise ValueError('示例结果的汇总核对未通过，请重新检查数据后浏览')
    contexts={'selected_tables':json.dumps(results,ensure_ascii=False)}
    facts=build_facts(contexts)
    markdown,refs=factual_brief(contexts,facts)
    output.update(results=results,filters=results[0]['review_context']['filters'],
        previous_month=results[0]['review_context']['previous_month'],
        summary=review_analysis(overview,results[0]['review_context']),
        reconciliations=reconciliations,
        report=dict(title=case['title']+' · 示例事实报告',markdown=markdown,fact_references=refs),
        source=dict(database_id=DATABASE,name='Olist 巴西电商公开历史样本',currency='BRL',
            url='https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce',license='CC BY-NC-SA 4.0',
            computed_at=datetime.now(timezone.utc).isoformat(),
            note='数值按当前历史样本与标准口径计算；无自动更新。角色、任务与待核查事项为场景编排，未记录真实业务审批。'))
    return output
