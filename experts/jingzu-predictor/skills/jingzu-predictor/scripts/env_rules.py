# -*- coding: utf-8 -*-
"""
jingzu-predictor · 规则与工具真源（single source of truth）

设计原则：**回测与当日预测必须共用同一份规则代码**，不得手算、不得各写一份。

口径约定（改动会波及全部结论，勿轻改）：
  * 多选结算：**注数 = 选数**（铁律 3）。
  * `gp()` 返回**百分点**（0–100），不是小数。
  * 深盘 = |让球| ≥ 2；主让 rq ≤ -1；客让 rq ≥ +1；平手 rq = 0。
  * `None` / 空集 = 该场不出手。
"""
import math

# ---------------------------------------------------------------- 常量
ALIAS = {'7+': '7', '胜其他': '胜其它', '平其他': '平其它', '负其他': '负其它'}
# 数据源侧校正 D9：500 赔率键为 '7'/'胜其它'，竞彩结果档位为 '7+'/'胜其他'。
# 取值时须把「结果档位」映射回「赔率键」，否则含 7+ 档位的策略会静默 n=0。

ACTUAL = {'wdl': 'w_actual', 'rq': 'rq_res', 'score': 'score_cat',
          'goals': 'goals_cat', 'htft': 'htft_cat'}
PLAYNAME = {'wdl': '胜平负', 'rq': '让球胜平负', 'score': '比分',
            'goals': '进球数', 'htft': '半全场'}
MULTI_KEYS = {'score', 'goals', 'htft'}


# ---------------------------------------------------------------- 基础工具
def fnum(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except Exception:
        return None


def o5(r, k):
    """取某玩法赔率字典"""
    return (r.get('o500') or {}).get(k) or {}


def geth(odds, key):
    """按别名表取赔率（D9 修复）"""
    if not odds:
        return None
    v = fnum(odds.get(ALIAS.get(key, key)))
    return v if v else fnum(odds.get(key))


def rqv(r):
    """让球数（主让为负）"""
    v = r.get('rq')
    return v if isinstance(v, int) else 0


def implied(odds):
    """1/赔率 归一化 -> 隐含概率分布"""
    inv = {}
    for k, v in odds.items():
        f = fnum(v)
        if f:
            inv[k] = 1.0 / f
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s else {}


def topk(odds, k):
    p = implied(odds)
    return [kk for kk, _ in sorted(p.items(), key=lambda x: -x[1])[:k]] if p else []


def fav(odds):
    t = topk(odds, 1)
    return t[0] if t else None


def favp(odds):
    """返回 (最热项, 其隐含概率)"""
    p = implied(odds)
    if not p:
        return None, 0.0
    f = max(p, key=lambda x: p[x])
    return f, p[f]


def gp(odds):
    """概率差（**百分点**）：最高 - 次高。缺赔率返回 0.0 —— 调用方必须先过 avail()
    （D10：若不门控，缺赔率场次会被误判为「均势局」而污染结论）"""
    p = implied(odds)
    v = sorted(p.values(), reverse=True)
    return 100 * (v[0] - v[1]) if len(v) > 1 else 0.0


def score_dir(s):
    """比分档位 -> 胜平负方向"""
    if s == '胜其他':
        return '3'
    if s == '平其他':
        return '1'
    if s == '负其他':
        return '0'
    try:
        h, a = s.split(':')
        h, a = int(h), int(a)
        return '3' if h > a else ('1' if h == a else '0')
    except Exception:
        return None


def avail(r):
    """赛前赔率可得性门控（D10）：nspf/jqs/bqc 齐全才算可得"""
    return bool(o5(r, 'nspf')) and bool(o5(r, 'jqs')) and bool(o5(r, 'bqc'))


def roi(units):
    """units: [(net, stake)] -> dict(roi%, t, lo, hi)
    多选打法 stake = 选数（铁律 3）"""
    tot = sum(s for _, s in units)
    n = len(units)
    if n == 0 or tot == 0:
        return dict(n=0, bets=0, roi=None, t=None, lo=None, hi=None)
    per = [net / s for net, s in units]
    mean = sum(net for net, _ in units) / tot
    if n > 1:
        mu = sum(per) / n
        var = sum((x - mu) ** 2 for x in per) / (n - 1)
        se = math.sqrt(var / n)
        t = mu / se if se > 0 else 0.0
        lo, hi = mu - 1.96 * se, mu + 1.96 * se
    else:
        mu, t, lo, hi = per[0], 0.0, per[0], per[0]
    return dict(n=n, bets=int(tot), roi=100 * mean, t=t, lo=100 * lo, hi=100 * hi)


def ztest(h1, n1, h2, n2):
    """两比例 z 检验 -> (z, p)"""
    if not n1 or not n2:
        return 0.0, 1.0
    p1, p2 = h1 / n1, h2 / n2
    p = (h1 + h2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


# ---------------------------------------------------------------- 两套方法集（固化）
def wd_rules():
    """工作日方法集（C9）—— 6 条，与周末零重叠"""
    return [
        dict(key='wdl_primary', play='wdl', odds='nspf', tag='工作日·胜平负主推',
             name='深盘跟盘（|让球|≥2，跟盘口方向）',
             fn=lambda r: (('3' if rqv(r) < 0 else '0') if abs(rqv(r)) >= 2 else None)),
        dict(key='wdl_settle', play='wdl', odds='nspf', tag='工作日·胜平负可结算',
             name='主胜热门且 p≥60%（fav=主 且 p≥0.60）',
             fn=lambda r: ('3' if fav(o5(r, 'nspf')) == '3' and favp(o5(r, 'nspf'))[1] >= .60 else None)),
        dict(key='rq', play='rq', odds='spf', tag='工作日·让球胜平负',
             name='让球玩法热门且 p≥60%',
             fn=lambda r: (fav(o5(r, 'spf')) if favp(o5(r, 'spf'))[1] >= .60 else None)),
        dict(key='score', play='score', odds='bf', tag='工作日·比分',
             name='比分Top3 且 概率差<20pp（均势局）',
             fn=lambda r: None),   # 多选，见 predict()
        dict(key='goals', play='goals', odds='jqs', tag='工作日·进球数',
             name='固定1-3球（仅主让场 rq≤-1）',
             fn=lambda r: None),
        dict(key='htft', play='htft', odds='bqc', tag='工作日·半全场',
             name='半全场Top3（仅深盘场 |让球|≥2）',
             fn=lambda r: None),
    ]


def we_rules():
    """周末方法集（W10）—— 与工作日零重叠"""
    return [
        dict(key='wdl_primary', play='wdl', odds='nspf', tag='周末·胜平负主推',
             name='A级三条件（p∈[60,70%) ∩ 热门赔∈[1.30,1.60) ∩ 主胜热门）',
             fn=lambda r: ('3' if (fav(o5(r, 'nspf')) == '3'
                                   and .60 <= favp(o5(r, 'nspf'))[1] < .70
                                   and 1.30 <= (fnum(o5(r, 'nspf').get('3')) or 0) < 1.60) else None)),
        dict(key='wdl_settle', play='wdl', odds='nspf', tag='周末·胜平负可结算',
             name='市场热门 p≥70%',
             fn=lambda r: (fav(o5(r, 'nspf')) if favp(o5(r, 'nspf'))[1] >= .70 else None)),
        dict(key='wdl_main', play='wdl', odds='nspf', tag='周末·主让场补充',
             name='盘口倾斜法（仅主让 rq≤-1）',
             fn=lambda r: ('3' if rqv(r) <= -1 else None)),
        dict(key='rq', play='rq', odds='spf', tag='周末·让球胜平负',
             name='【回避】周末不参与让球胜平负（无稳健方法）',
             fn=lambda r: None),
        dict(key='score', play='score', odds='bf', tag='周末·比分',
             name='比分Top3（仅客让场 rq≥1）',
             fn=lambda r: None),
        dict(key='goals', play='goals', odds='jqs', tag='周末·进球数',
             name='进球数Top3（全样本）',
             fn=lambda r: None),
        dict(key='htft', play='htft', odds='bqc', tag='周末·半全场',
             name='半全场Top3 且 市场热门 p≥60%',
             fn=lambda r: None),
    ]


def rules_for(env):
    """env: 'weekday' | 'weekend'"""
    return wd_rules() if env == 'weekday' else we_rules()


def predict(r, rule):
    """返回 (预测集合 set, 注数 k)；空集 = 不出手。多选打法注数 = 选数。"""
    k = rule['key']
    if k == 'score':
        if rule['tag'].startswith('工作日'):
            # D10 门控：gp() 在 nspf 缺失时返回 0.0，会把无赔率场次误判为「均势局」
            if not (o5(r, 'nspf') and o5(r, 'bf')):
                return set(), 3
            return (set(topk(o5(r, 'bf'), 3)) if gp(o5(r, 'nspf')) < 20 else set()), 3
        return (set(topk(o5(r, 'bf'), 3)) if rqv(r) >= 1 else set()), 3
    if k == 'goals':
        if rule['tag'].startswith('工作日'):
            return ({'1', '2', '3'} if rqv(r) <= -1 else set()), 3
        return set(topk(o5(r, 'jqs'), 3)), 3
    if k == 'htft':
        if rule['tag'].startswith('工作日'):
            return (set(topk(o5(r, 'bqc'), 3)) if abs(rqv(r)) >= 2 else set()), 3
        return (set(topk(o5(r, 'bqc'), 3)) if favp(o5(r, 'nspf'))[1] >= .60 else set()), 3
    single = rule['fn'](r)
    return ({single} if single else set()), 1


# ---------------------------------------------------------------- 评估
def evaluate(rows, rule):
    """返回 (metrics dict, 逐场明细 list)"""
    n = h = 0
    units = []
    detail = []
    for r in rows:
        actual = r.get(ACTUAL[rule['play']]) or ''
        if not actual:
            continue
        pset, k = predict(r, rule)
        if not pset:
            continue
        odds = o5(r, rule['odds'])
        ok = actual in pset
        n += 1
        h += 1 if ok else 0
        o = geth(odds, actual) if odds else None
        if o:
            units.append(((o - k) if ok else -k, k))
        detail.append(dict(no=r['no'], date=r['date'], home=r['home'], away=r['away'],
                           rq=rqv(r), actual=actual, pred='|'.join(sorted(pset)),
                           ok=int(ok), ft=r.get('ft'), odds=o))
    return dict(n=n, hits=h, acc=(100.0 * h / n if n else None), roi=roi(units)), detail


def baseline(rows, play='wdl'):
    """基准：始终主胜"""
    n = h = 0
    for r in rows:
        a = r.get(ACTUAL[play]) or ''
        if not a:
            continue
        n += 1
        h += 1 if a == '3' else 0
    return n, h, (100.0 * h / n if n else None)


def env_of(num):
    """按竞彩期号前缀判环境（T8：以**期号前缀**为准，不看开球时刻）"""
    s = str(num or '')
    for wd in ('周六', '周日'):
        if s.startswith(wd):
            return 'weekend'
    return 'weekday'
