"""Deterministic statistics over a saved SQL result, never a model-generated fact."""
from __future__ import annotations
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, localcontext
from functools import lru_cache
from itertools import islice
from typing import Any
from .config import BASE_DIR

VERSION = "1.0"
MAX_COLUMNS = 16

def decimal_text(value):
    if value is None: return None
    with localcontext() as context:
        context.prec = 350
        if value == value.to_integral(): return str(value.to_integral())
        return format(round(value, 6), "f").rstrip("0").rstrip(".")

def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def month(value):
    if not isinstance(value, str): return None
    if not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2}(?:[T ].*)?)?", value): return None
    try:
        date.fromisoformat(value[:10] if len(value) >= 10 else value + "-01")
        return value[:7]
    except ValueError: return None

def nonadditive(field):
    return bool(re.search(r"rate|ratio|percent|avg|average|mean|(?:sales|items)_per_order|率|比例|占比|平均|客单价", field, re.I))

def build_profile(columns, rows, *, total_rows=None, complete=True, sql=""):
    count = len(rows)
    if total_rows is None and complete: total_rows=count
    if total_rows is not None and count!=total_rows: complete=False
    fields = columns[:MAX_COLUMNS]
    profile = {"version": VERSION, "coverage": "full_result" if complete else "preview_only",
        "row_count": total_rows, "analyzed_rows": count, "profiled_columns": fields,
        "omitted_columns": columns[MAX_COLUMNS:], "numeric": [], "categories": [], "temporal": None,
        "scope_note": "统计覆盖这条 SQL 的输出结果，不等于数据库原始全量；SQL 的筛选、去重、分组和 LIMIT 均保留。",
        "aggregation_note": "数值总和是结果列各行之和；平均值为行的非加权平均。比率和平均指标不提供总和。不得把分组均值当作整体比率，不得推断因果。"}
    temporal_field = next((f for f in fields if any(r.get(f) is not None for r in rows)
        and all(month(r[f]) is not None for r in rows if r.get(f) is not None)), None)
    identifier = next((f for f in fields if f != temporal_field and any(isinstance(r.get(f), str) for r in rows)), None)
    numeric_fields = []
    for field in fields:
        values = [r.get(field) for r in rows if r.get(field) is not None]
        if not values: continue
        if all(numeric(v) for v in values) and not re.search(r"(^id$|_id$|编号|编码|^year$|^month$)", field, re.I):
            numeric_fields.append(field)
            with localcontext() as ctx:
                ctx.prec = 350
                total = sum((Decimal(str(v)) for v in values), Decimal(0))
                low, high = min(values), max(values)
                context_fields = list(dict.fromkeys(f for f in (temporal_field, identifier, field) if f))
                pick = lambda value: list(islice(({f:r.get(f) for f in context_fields} for r in rows if r.get(field)==value),3))
                profile["numeric"].append({"field":field,"count":len(values),"missing":count-len(values),
                    "sign_counts":{"positive":sum(v>0 for v in values),"zero":sum(v==0 for v in values),"negative":sum(v<0 for v in values)},
                    "sum":None if nonadditive(field) else decimal_text(total),"mean":decimal_text(total/len(values)),
                    "min":low,"max":high,"min_rows":pick(low),"max_rows":pick(high),"additive":not nonadditive(field)})
        elif field != temporal_field and all(isinstance(v, (str,bool)) for v in values):
            frequencies = Counter()
            overflow = False
            for value in values:
                frequencies[str(value)] += 1
                if len(frequencies)>100:
                    overflow=True;break
            if not overflow:
                top = sorted(frequencies.items(), key=lambda item:(-item[1],item[0]))[:8]
                profile["categories"].append({"field":field,"count":len(values),"missing":count-len(values),
                    "distinct":len(frequencies),"top":[{"value":v,"rows":n,"row_share":round(n/count,6)} for v,n in top]})
    if temporal_field and numeric_fields:
        keys = {month(r.get(temporal_field)) for r in rows if month(r.get(temporal_field))}
        grain = "month" if len(keys)<=60 else "year"
        if grain=="year": keys={k[:4] for k in keys}
        if len(keys)<=60:
            measures = numeric_fields[:4]
            buckets = defaultdict(list)
            for row in rows:
                key=month(row.get(temporal_field))
                if key: buckets[key if grain=="month" else key[:4]].append(row)
            result=[]
            for key, group in sorted(buckets.items()):
                record={"period":key,"result_rows":len(group)}
                for field in measures:
                    values=[Decimal(str(r[field])) for r in group if numeric(r.get(field))]
                    if not values: continue
                    with localcontext() as ctx:
                        ctx.prec=350
                        value=sum(values,Decimal(0))/len(values) if nonadditive(field) else sum(values,Decimal(0))
                    column=field+("_mean" if nonadditive(field) else "_sum")
                    record[column]=decimal_text(value)
                    previous = result[-1].get(column) if result else None
                    record[column+"_change"]=decimal_text(value-Decimal(previous)) if previous is not None else None
                    record[column+"_change_rate"]=decimal_text((value-Decimal(previous))/abs(Decimal(previous))) if previous is not None and Decimal(previous)!=0 else None
                result.append(record)
            profile["temporal"]={"field":temporal_field,"grain":grain,"covered_rows":sum(len(g) for g in buckets.values()),
                "missing_dates":count-sum(len(g) for g in buckets.values()),"buckets":result,
                "measure_columns":[f+("_mean" if nonadditive(f) else "_sum") for f in measures],
                "note":"按实际存在的相邻期间比较；未补齐缺失日期或月份，缺失期间不能视为零。比率的期间值是非加权行均值。"}
    return profile

@lru_cache(maxsize=128)
def _read_profile(snapshot_id, mtime, size):
    path=BASE_DIR / "data/result_snapshots" / (snapshot_id+".json")
    payload=json.loads(path.read_text())
    return payload.get("result_profile") or build_profile(payload["columns"],payload["rows"],total_rows=len(payload["rows"]))

def ensure_profile(result):
    if result.result_profile is not None: return result.result_profile
    if result.snapshot_id and re.fullmatch(r"[a-f0-9]{32}",result.snapshot_id):
        path=BASE_DIR / "data/result_snapshots" / (result.snapshot_id+".json")
        try:
            stat=path.stat()
            result.result_profile=_read_profile(result.snapshot_id,stat.st_mtime_ns,stat.st_size)
        except (OSError, ValueError, KeyError):
            result.result_profile=build_profile(result.columns,result.rows,total_rows=result.total_rows,complete=False)
            result.result_profile["coverage"]="snapshot_unavailable"
    else:
        result.result_profile=build_profile(result.columns,result.rows,total_rows=result.total_rows,complete=False)
    return result.result_profile

def chart_source(task_id, title, profile):
    temporal=(profile or {}).get("temporal")
    if not temporal or not temporal["buckets"] or (profile or {}).get("coverage")!="full_result": return None
    return {"task_id":task_id+":summary:time","title":title+" · 完整结果期间汇总",
        "columns":["period",*temporal["measure_columns"]],"row_count":len(temporal["buckets"]),
        "coverage":"full_result_summary","covered_rows":temporal["covered_rows"],"excluded_rows":temporal["missing_dates"],"input_rows":profile["analyzed_rows"],"rows":[{c:r.get(c) for c in ["period",*temporal["measure_columns"]]} for r in temporal["buckets"]]}
