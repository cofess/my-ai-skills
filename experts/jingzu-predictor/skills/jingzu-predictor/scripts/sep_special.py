# -*- coding: utf-8 -*-
"""
四专项回测 —— 工作日 / 周末 分环境
  ① 胜平负：平局 + 爆冷
  ② 比分  ：高赔场次（实际比分赔率 >= 15）
  ③ 进球数：大球场次（总进球 >= 4）
  ④ 半全场：反转形态（胜平/负平/胜负/负胜）

方法：候选规则库 → 全样本 + 时序 60/40 留出 → precision/recall/lift 排名
口径：全部输入仅用赛前可得数据（nspf / spf / jqs / bqc / bf / 让球盘口）
"""
import json
import os
import math
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.environ.get('JZ_DATA_DIR') or os.path.join(os.path.dirname(HERE), 'hist')
OUT = os.environ.get('JZ_OUT_DIR') or os.path.join(HERE, 'out')
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- 工具
def fnum(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except Exception:
        return None


def o5(r, k):
    return (r.get('o500') or {}).get(k) or {}


def rqv(r):
    v = r.get('rq')
    return v if isinstance(v, int) else 0


def implied(odds):
    inv = {}
    for k, v in odds.items():
        f = fnum(v)
        if f:
            inv[k] = 1.0 / f
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s else {}


def fav(odds):
    p = implied(odds)
    return max(p, key=lambda x: p[x]) if p else None


def favp(odds):
    p = implied(odds)
    if not p:
        return None, 0.0
    f = max(p, key=lambda x: p[x])
    return f, p[f]


def gp(odds):
    """概率差（百分点）：最高 - 次高"""
    p = implied(odds)
    v = sorted(p.values(), reverse=True)
    return 100 * (v[0] - v[1]) if len(v) > 1 else 0.0


def drawp(r):
    """平局隐含概率（%）"""
    p = implied(o5(r, 'nspf'))
    return 100 * p.get('1', 0.0)


def high_sum(r, lo=4):
    """jqs 中 >= lo 球的隐含概率和（%）"""
    p = implied(o5(r, 'jqs'))
    tot = 0.0
    for k, v in p.items():
        if k == '7+':
            tot += v
        else:
            try:
                if int(k) >= lo:
                    tot += v
            except Exception:
                pass
    return 100 * tot


def jqs_top(r):
    t = fav(o5(r, 'jqs'))
    return 9 if t == '7+' else (int(t) if t and t.isdigit() else -1)


def bqc_rev_sum(r):
    """bqc 反转 4 档（3-1/0-1/3-0/0-3）隐含概率和（%）"""
    p = implied(o5(r, 'bqc'))
    return 100 * sum(p.get(k, 0.0) for k in ('3-1', '0-1', '3-0', '0-3'))


def score_odds(r):
    """实际比分对应的 500 比分赔率；无数据返回 None"""
    bf = o5(r, 'bf')
    s = r.get('score_cat') or ''
    if not bf or not s:
        return None
    for k in (s, s.replace('其他', '其它')):
        v = fnum(bf.get(k))
        if v:
            return v
    return None


def score_topk(r, k):
    bf = o5(r, 'bf')
    p = implied(bf)
    if not p:
        return []
    return sorted(p, key=lambda x: -p[x])[:k]


def score_dir(s):
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


def roi(units):
    """units: [(net, stake)]"""
    tot = sum(s for _, s in units)
    n = len(units)
    if n == 0 or tot == 0:
        return None, None
    per = [net / s for net, s in units]
    mean = sum(per) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in per) / (n - 1)
        se = math.sqrt(var / n)
        t = mean / se if se > 0 else 0.0
    else:
        t = 0.0
    return 100 * mean, t


# ---------------------------------------------------------------- 通用子集评估
def avail(r):
    """赛前赔率可得性：nspf/jqs/bqc 齐全才能算隐含概率
       —— 否则 gp() 会返回 0（伪均势）、fav() 返回 None（伪非爆冷），污染结论"""
    return bool(o5(r, 'nspf')) and bool(o5(r, 'jqs')) and bool(o5(r, 'bqc'))


def eval_subset(rows, selector, target, name, pool_filter=None):
    """
    识别类评估：precision / recall / lift
    基准与目标统计均限定在可得子集上，避免缺失场次被误算
    """
    pf = pool_filter or avail
    pool = [r for r in rows if pf(r)]
    tgt_all = [r for r in pool if target(r)]
    sel = [r for r in pool if selector(r)]
    n_sel = len(sel)
    n_hit = sum(1 for r in sel if target(r))
    n_all = len(tgt_all)
    base = (n_all / len(pool)) if pool else 0.0
    if n_sel == 0:
        return dict(name=name, n_sel=0, n_hit=0, prec=None, rec=None, lift=None,
                    base=100 * base, pool=len(pool), n_all=n_all)
    prec = n_hit / n_sel
    rec = (n_hit / n_all) if n_all else None
    lift = (prec / base) if base else None
    return dict(name=name, n_sel=n_sel, n_hit=n_hit, prec=100 * prec,
                rec=(100 * rec) if rec is not None else None,
                lift=lift, base=100 * base, pool=len(pool), n_all=n_all)


def split_rows(rows):
    rows = sorted(rows, key=lambda r: (r['date'], r.get('time') or ''))
    cut = int(len(rows) * 0.6)
    return rows[:cut], rows[cut:]


def load(env):
    fn = 'master300_odds.json' if env == 'weekday' else 'master200_wk_odds.json'
    rows = json.load(open(os.path.join(HIST, fn), encoding='utf-8'))
    rows = [r for r in rows if r.get('w_actual')]
    return sorted(rows, key=lambda r: (r['date'], r.get('time') or ''))


# ---------------------------------------------------------------- 专项定义
def sp1_draw(r):
    return r.get('w_actual') == '1'


def sp1_upset(r):
    f = fav(o5(r, 'nspf'))
    a = r.get('w_actual')
    return bool(f and a and a != f)


def sp2_highscore(r):
    v = score_odds(r)
    return v is not None and v >= 15


def sp3_big(r):
    g = r.get('goals')
    return isinstance(g, int) and g >= 4


REV = {'3-1', '0-1', '3-0', '0-3'}


def sp4_rev(r):
    return r.get('htft_cat') in REV


def build_rules():
    R = {}
    # ---- ①A 平局识别
    R['1A'] = ('胜平负 · 平局识别', sp1_draw, [
        ('D01 平赔最低(fav=平)', lambda r: fav(o5(r, 'nspf')) == '1'),
        ('D02 平赔<=3.30', lambda r: (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.30),
        ('D03 平赔<=3.40', lambda r: (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40),
        ('D04 平赔<=3.50', lambda r: (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.50),
        ('D05 概率差<=15pp', lambda r: gp(o5(r, 'nspf')) <= 15),
        ('D06 概率差<=10pp', lambda r: gp(o5(r, 'nspf')) <= 10),
        ('D07 平手盘(rq=0)', lambda r: rqv(r) == 0),
        ('D08 平赔<=3.5 且 概率差<=20pp', lambda r: (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.50 and gp(o5(r, 'nspf')) <= 20),
        ('D09 |rq|=1 且 概率差<=15pp', lambda r: abs(rqv(r)) == 1 and gp(o5(r, 'nspf')) <= 15),
        ('D10 进球最热<=2球', lambda r: 0 <= jqs_top(r) <= 2),
        ('D11 平赔<=3.4 且 进球最热<=2', lambda r: (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40 and 0 <= jqs_top(r) <= 2),
    ])
    # ---- ①B 爆冷识别
    R['1B'] = ('胜平负 · 爆冷识别(实际≠最热)', sp1_upset, [
        ('U01 |rq|=1 浅盘', lambda r: abs(rqv(r)) == 1),
        ('U02 |rq|>=2 深盘', lambda r: abs(rqv(r)) >= 2),
        ('U03 平手盘 rq=0', lambda r: rqv(r) == 0),
        ('U04 概率差<=10pp', lambda r: gp(o5(r, 'nspf')) <= 10),
        ('U05 概率差<=20pp', lambda r: gp(o5(r, 'nspf')) <= 20),
        ('U06 概率差>=30pp', lambda r: gp(o5(r, 'nspf')) >= 30),
        ('U07 平赔<=3.40', lambda r: (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40),
        ('U08 客让 rq>=1', lambda r: rqv(r) >= 1),
        ('U09 主让浅盘 rq=-1', lambda r: rqv(r) == -1),
        ('U10 热门概率 50-60%', lambda r: 0.50 <= favp(o5(r, 'nspf'))[1] < 0.60),
        ('U11 热门概率>=65%', lambda r: favp(o5(r, 'nspf'))[1] >= 0.65),
    ])
    # ---- ② 比分高赔（识别）
    R['2'] = ('比分 · 高赔场次识别(实际赔率>=15)', sp2_highscore, [
        ('H01 概率差>=25pp', lambda r: gp(o5(r, 'nspf')) >= 25),
        ('H02 |rq|>=2 深盘', lambda r: abs(rqv(r)) >= 2),
        ('H03 客让 rq>=1', lambda r: rqv(r) >= 1),
        ('H04 进球最热>=3球', lambda r: jqs_top(r) >= 3),
        ('H05 4+球概率和>=22%', lambda r: high_sum(r, 4) >= 22),
        ('H06 4+球概率和>=28%', lambda r: high_sum(r, 4) >= 28),
        ('H07 热门概率>=60%', lambda r: favp(o5(r, 'nspf'))[1] >= 0.60),
        ('H08 主热p>=60% 且 rq<=-1', lambda r: favp(o5(r, 'nspf'))[1] >= 0.60 and fav(o5(r, 'nspf')) == '3' and rqv(r) <= -1),
        ('H09 客热p>=60% 且 rq>=1', lambda r: favp(o5(r, 'nspf'))[1] >= 0.60 and fav(o5(r, 'nspf')) == '0' and rqv(r) >= 1),
        ('H10 概率差>=25pp 且 进球最热>=3', lambda r: gp(o5(r, 'nspf')) >= 25 and jqs_top(r) >= 3),
    ])
    # ---- ③ 进球数大球（识别）
    R['3'] = ('进球数 · 大球识别(总进球>=4)', sp3_big, [
        ('B01 |rq|>=2 深盘', lambda r: abs(rqv(r)) >= 2),
        ('B02 客让 rq>=1', lambda r: rqv(r) >= 1),
        ('B03 主让 rq=-1', lambda r: rqv(r) == -1),
        ('B04 概率差>=25pp', lambda r: gp(o5(r, 'nspf')) >= 25),
        ('B05 进球最热>=3球', lambda r: jqs_top(r) >= 3),
        ('B06 4+球概率和>=25%', lambda r: high_sum(r, 4) >= 25),
        ('B07 4+球概率和>=30%', lambda r: high_sum(r, 4) >= 30),
        ('B08 5+球概率和>=15%', lambda r: high_sum(r, 5) >= 15),
        ('B09 概率差>=20pp 且 进球最热>=3', lambda r: gp(o5(r, 'nspf')) >= 20 and jqs_top(r) >= 3),
        ('B10 进球最热>=4球', lambda r: jqs_top(r) >= 4),
    ])
    # ---- ④ 半全场反转（识别）
    R['4'] = ('半全场 · 反转形态识别(胜平/负平/胜负/负胜)', sp4_rev, [
        ('R01 概率差<=15pp', lambda r: gp(o5(r, 'nspf')) <= 15),
        ('R02 概率差<=10pp', lambda r: gp(o5(r, 'nspf')) <= 10),
        ('R03 平赔<=3.40', lambda r: (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40),
        ('R04 平手盘 rq=0', lambda r: rqv(r) == 0),
        ('R05 |rq|>=2 深盘', lambda r: abs(rqv(r)) >= 2),
        ('R06 进球最热>=3球', lambda r: jqs_top(r) >= 3),
        ('R07 反转4档概率和>=12%', lambda r: bqc_rev_sum(r) >= 12),
        ('R08 反转4档概率和>=15%', lambda r: bqc_rev_sum(r) >= 15),
        ('R09 概率差<=20pp 且 进球最热>=3', lambda r: gp(o5(r, 'nspf')) <= 20 and jqs_top(r) >= 3),
        ('R10 概率差<=15pp 且 平赔<=3.4', lambda r: gp(o5(r, 'nspf')) <= 15 and (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40),
    ])
    return R


def fmt_pct(v, w=6):
    return ('%*.1f%%' % (w, v)) if v is not None else ('%*s' % (w, 'n/a'))


def fmt_lift(v):
    return ('%.2fx' % v) if v is not None else 'n/a'


def topk(odds, k):
    p = implied(odds)
    return sorted(p, key=lambda x: -p[x])[:k] if p else []


ACTUAL = {'nspf': 'w_actual', 'bf': 'score_cat', 'jqs': 'goals_cat', 'bqc': 'htft_cat'}

# 竞彩结果档位 → 500 赔率键 的别名映射（两站命名不一致，必须显式转换）
ALIAS = {'7+': '7', '胜其他': '胜其它', '平其他': '平其它', '负其他': '负其它'}


def get_odds(odds, key):
    if not odds:
        return None
    if key in odds:
        return odds[key]
    a = ALIAS.get(key)
    return odds.get(a) if a else None


def settle_multi(rows, pred_fn, odds_key):
    """多选结算，注数 = 选数（红线）；任一所选档位缺赔率 → 该场不结算"""
    units = []
    for r in rows:
        pset = pred_fn(r)
        if not pset:
            continue
        a = r.get(ACTUAL[odds_key])
        odds = o5(r, odds_key)
        if not a or not odds:
            continue
        vals = [fnum(get_odds(odds, s)) for s in pset]
        if any(v is None for v in vals):
            continue
        k = len(pset)
        if a in pset:
            units.append((fnum(get_odds(odds, a)) - k, k))
        else:
            units.append((-k, k))
    return roi(units), len(units)


def build_preds():
    P = {}
    # ---- ① 平局/爆冷应对
    P['1'] = ('胜平负 · 平局与爆冷应对', 'nspf', [
        ('S1 押最热（基线）', lambda r: ({fav(o5(r, 'nspf'))} if fav(o5(r, 'nspf')) else set())),
        ('S2 平赔<=3.5 → 押平局', lambda r: ({'1'} if (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.50 else set())),
        ('S3 平赔<=3.4 → 押平局', lambda r: ({'1'} if (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40 else set())),
        ('S4 平赔<=3.5 → 押{主,平}', lambda r: ({'3', '1'} if (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.50 else set())),
        ('S5 平赔<=3.5 → 押{客,平}', lambda r: ({'0', '1'} if (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.50 else set())),
        ('S6 概率差<=15pp → 押平局', lambda r: ({'1'} if gp(o5(r, 'nspf')) <= 15 else set())),
        ('S7 概率差<=15pp → 押{主,平}', lambda r: ({'3', '1'} if gp(o5(r, 'nspf')) <= 15 else set())),
        ('S8 全样本押平局', lambda r: {'1'}),
        ('S9 深盘跟盘｜浅盘平赔<=3.5押平', lambda r: (
            {'3' if rqv(r) < 0 else '0'} if abs(rqv(r)) >= 2
            else ({'1'} if (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.50 else set()))),
    ])
    # ---- ② 高赔比分覆盖
    P['2'] = ('比分 · 高赔场次覆盖', 'bf', [
        ('C1 押 Top3（基线）', lambda r: set(topk(o5(r, 'bf'), 3))),
        ('C2 押 Top5', lambda r: set(topk(o5(r, 'bf'), 5))),
        ('C3 押{3:1,3:2}', lambda r: {'3:1', '3:2'}),
        ('C4 押{3:0,3:1,3:2}', lambda r: {'3:0', '3:1', '3:2'}),
        ('C5 押{4:0,4:1,4:2}', lambda r: {'4:0', '4:1', '4:2'}),
        ('C6 押{0:3,1:3,2:3}', lambda r: {'0:3', '1:3', '2:3'}),
        ('C7 押{3:1,3:2,0:3,1:3}', lambda r: {'3:1', '3:2', '0:3', '1:3'}),
        ('C8 进球最热>=3 → 押{3:1,3:2,2:2}', lambda r: ({'3:1', '3:2', '2:2'} if jqs_top(r) >= 3 else set())),
        ('C9 全押赔率∈[15,30)档', lambda r: {k for k, v in o5(r, 'bf').items() if 15 <= (fnum(v) or 0) < 30}),
        ('C10 Top3 ∪ {3:1,3:2}', lambda r: set(topk(o5(r, 'bf'), 3)) | {'3:1', '3:2'}),
    ])
    # ---- ③ 大球覆盖
    P['3'] = ('进球数 · 大球覆盖', 'jqs', [
        ('G1 押 Top3（基线）', lambda r: set(topk(o5(r, 'jqs'), 3))),
        ('G2 押{4,5,6,7+}', lambda r: {'4', '5', '6', '7+'}),
        ('G3 押{5,6,7+}', lambda r: {'5', '6', '7+'}),
        ('G4 进球最热>=3 → 押{3,4,5,6}', lambda r: ({'3', '4', '5', '6'} if jqs_top(r) >= 3 else set())),
        ('G5 概率差>=20pp且最热>=3 → 押{4,5,6,7+}', lambda r: (
            {'4', '5', '6', '7+'} if gp(o5(r, 'nspf')) >= 20 and jqs_top(r) >= 3 else set())),
        ('G6 深盘 → 押{3,4,5}', lambda r: ({'3', '4', '5'} if abs(rqv(r)) >= 2 else set())),
        ('G7 Top3 ∪ {4,5}', lambda r: set(topk(o5(r, 'jqs'), 3)) | {'4', '5'}),
        ('G8 押{2,3,4,5}', lambda r: {'2', '3', '4', '5'}),
    ])
    # ---- ④ 半全场反转覆盖
    P['4'] = ('半全场 · 反转形态覆盖', 'bqc', [
        ('R1 押 Top3（基线）', lambda r: set(topk(o5(r, 'bqc'), 3))),
        ('R2 押反转4档{3-1,0-1,3-0,0-3}', lambda r: {'3-1', '0-1', '3-0', '0-3'}),
        ('R3 概率差<=15pp → 押反转4档', lambda r: ({'3-1', '0-1', '3-0', '0-3'} if gp(o5(r, 'nspf')) <= 15 else set())),
        ('R4 概率差<=10pp → 押反转4档', lambda r: ({'3-1', '0-1', '3-0', '0-3'} if gp(o5(r, 'nspf')) <= 10 else set())),
        ('R5 平赔<=3.4 → 押{3-1,0-1}', lambda r: ({'3-1', '0-1'} if (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40 else set())),
        ('R6 平赔<=3.4 → 押{3-0,0-3}', lambda r: ({'3-0', '0-3'} if (fnum(o5(r, 'nspf').get('1')) or 99) <= 3.40 else set())),
        ('R7 押 Top5', lambda r: set(topk(o5(r, 'bqc'), 5))),
        ('R8 押{3-3,3-1,3-0,0-1}', lambda r: {'3-3', '3-1', '3-0', '0-1'}),
    ])
    return P


def main_predict():
    preds = build_preds()
    L = []
    P = L.append
    for env, label in [('weekday', '工作日'), ('weekend', '周末')]:
        rows = load(env)
        tr, te = split_rows(rows)
        P('=' * 112)
        P('%s 环境 · 四专项【预测/结算】对比（多选按「选数=注数」结算）' % label)
        P('=' * 112)
        for key in ('1', '2', '3', '4'):
            title, okey, cands = preds[key]
            P('')
            P('【%s】' % title)
            P('  %-38s %6s %8s %11s %10s | %s' % (
                '策略', '结算n', '命中率', 'ROI', 't值', '留出验证命中率'))
            P('  ' + '-' * 100)
            res = []
            for name, fn in cands:
                (r_, n_) = settle_multi(rows, fn, okey)
                (r2, n2) = settle_multi(te, fn, okey)
                acc, acc2 = hitrate(rows, fn, okey), hitrate(te, fn, okey)
                res.append((name, n_, acc, r_, acc2, n2))
            res.sort(key=lambda x: -(x[2] or -1))
            for name, n_, acc, r_, acc2, n2 in res:
                P('  %-38s %6d %8s %11s %10s | %s (n=%d)' % (
                    name, n_, fmt_pct(acc, 7),
                    ('%+.2f%%' % r_[0]) if (r_ and r_[0] is not None) else 'n/a',
                    ('%.2f' % r_[1]) if (r_ and r_[1] is not None) else 'n/a',
                    fmt_pct(acc2, 7), n2))
        P('')
    txt = '\n'.join(L)
    open(os.path.join(OUT, 'special_predict.txt'), 'w', encoding='utf-8').write(txt)
    print(txt)


def hitrate(rows, fn, okey):
    n = h = 0
    for r in rows:
        pset = fn(r)
        if not pset:
            continue
        a = r.get(ACTUAL[okey])
        if not a:
            continue
        odds = o5(r, okey)
        if not odds:
            continue
        vals = [fnum(get_odds(odds, s)) for s in pset]
        if any(v is None for v in vals):
            continue
        n += 1
        h += 1 if a in pset else 0
    return (100.0 * h / n) if n else None


def main():
    rules = build_rules()
    L = []
    P = L.append

    for env, label in [('weekday', '工作日'), ('weekend', '周末')]:
        rows = load(env)
        tr, te = split_rows(rows)
        n = len(rows)
        P('=' * 112)
        P('%s 环境 · 四专项分析   样本 n=%d（%s ~ %s）' % (
            label, n, rows[0]['date'], rows[-1]['date']))
        P('时序留出：校准 n=%d（%s~%s） | 验证 n=%d（%s~%s）' % (
            len(tr), tr[0]['date'], tr[-1]['date'], len(te), te[0]['date'], te[-1]['date']))
        P('赔率可得性：nspf %d | spf %d | jqs %d | bqc %d | bf %d  —— 全齐(avail) %d' % (
            sum(1 for r in rows if o5(r, 'nspf')), sum(1 for r in rows if o5(r, 'spf')),
            sum(1 for r in rows if o5(r, 'jqs')), sum(1 for r in rows if o5(r, 'bqc')),
            sum(1 for r in rows if o5(r, 'bf')), sum(1 for r in rows if avail(r))))
        P('=' * 112)

        for key in ('1A', '1B', '2', '3', '4'):
            title, target, cands = rules[key]
            pf = (lambda r: avail(r) and bool(o5(r, 'bf'))) if key == '2' else avail
            pool = [r for r in rows if pf(r)]
            n_all = sum(1 for r in pool if target(r))
            base = 100.0 * n_all / len(pool) if pool else 0
            P('')
            P('【%s】可得评估池 %d/%d 场，目标子集 %d 场 = %.1f%%' % (
                title, len(pool), n, n_all, base))
            P('  %-34s %6s %6s %9s %9s %8s | %s' % (
                '候选规则', '选出n', '命中', '精准率', '召回率', '提升', '留出验证精准率'))
            P('  ' + '-' * 106)
            res = []
            for name, sel in cands:
                e = eval_subset(rows, sel, target, name, pool_filter=pf)
                e2 = eval_subset(te, sel, target, name, pool_filter=pf)
                res.append((e, e2))
            res.sort(key=lambda x: (-(x[0]['prec'] if x[0]['prec'] is not None else -1)))
            for e, e2 in res:
                P('  %-34s %6d %6d %s %s %8s | %s (n=%d)' % (
                    e['name'], e['n_sel'], e['n_hit'],
                    fmt_pct(e['prec']), fmt_pct(e['rec']), fmt_lift(e['lift']),
                    fmt_pct(e2['prec']), e2['n_sel']))
            P('  %-34s %6d %6d %s %8s %8s' % (
                '（基准：池内随机）', len(pool), n_all, fmt_pct(base), '100.0%', '1.00x'))
        P('')
    txt = '\n'.join(L)
    open(os.path.join(OUT, 'special_rank.txt'), 'w', encoding='utf-8').write(txt)
    print(txt)


if __name__ == '__main__':
    main()
    print()
    main_predict()
