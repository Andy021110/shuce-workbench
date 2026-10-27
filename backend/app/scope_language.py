"""Shared polarity rules for explicit scope values, not a general language parser."""
import re

OPERATORS = re.compile(
    r'排除|剔除|不包含|不含|不看|不是|而非|除了|除去|不要(?:统计|查看|查询|包含)?|不统计|不分析|非(?!常)|!=|<>|NOT\s+IN\s*\(?'
    r'|重点(?:关注|查看|看)?|优先(?:关注|查看|看)?|例如|比如'
    r'|只看|仅看|改看|改为|保留|选择|切换为', re.I)
NEGATIVE = re.compile(r'^(?:排除|剔除|不|非|而非|除了|除去|!=|<>|NOT)', re.I)
FOCUS = re.compile(r'^(?:重点|优先|例如|比如)')


def polarity(query: str, start: int, end: int, *, samples=True):
    """Return include/exclude/ignore for one value, sharing operators across lists.

    Explanation and measure-sample clauses cannot silently become global scope.
    Commas end a clause except an immediately continuing enumeration.
    """
    hard_start = max((m.end() for m in re.finditer(r'[。；;！？\n]', query[:start])), default=0)
    hard_end = next((end + m.start() for m in re.finditer(r'[。；;！？\n]', query[end:])), len(query))
    left = query[hard_start:start]
    comma = max(left.rfind('，'), left.rfind(','))
    if comma >= 0 and not re.fullmatch(r'\s*(?:和|与|及|或|还有)?\s*[\'\"(（]*', left[comma + 1:]):
        hard_start += comma + 1
        left = query[hard_start:start]
    tail = query[end:hard_end]
    comma_end = re.search('[，,]', tail)
    clause = query[hard_start:end + (comma_end.start() if comma_end else len(tail))]
    if re.search(r'不要把|不能把|请勿把|不要让|不能让|反向理解', clause):
        return 'ignore'
    if samples and (re.search(r'分母|样本条件|只有.{0,16}超时率|超时率.{0,16}(?:仅|只|使用|计算)', clause)
                    or (re.search(r'(?:没有|若|当|无).{0,18}$',left) and re.match(r'.{0,12}样本',tail))):
        return 'ignore'
    ops = list(OPERATORS.finditer(left))
    if ops:
        op = ops[-1].group()
        if NEGATIVE.match(op) and not re.search(r'按|统计|计算|返回|比较|查看|查询|分析',left[ops[-1].end():]): return 'exclude'
        if FOCUS.match(op): return 'ignore'
    suffix = re.search(r'除外|以外|之外', tail)
    if suffix and not re.search(r'统计|查询|比较|计算|查看', tail[:suffix.start()]):
        return 'exclude'
    return 'include'
