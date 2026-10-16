# -*- coding: utf-8 -*-
"""
分环境（工作日 / 周末）足彩回测 + 校准引擎
闭环：分析 → 预测 → 比对赛果 → 校正（时序切分调参 → 留出验证）→ 排序出最优方法

数据口径（SKILL football-lottery-analyst 边界）：
  特征 = 500 终盘赔率(nspf/spf/bf/jqs/bqc) + 让球盘口(handicap/rq) + league
  结算 = 500 终盘赔率（竞彩口径）
  样本 = master300_odds.json（工作日 300）/ master200_wk_odds.json（周末 200）
约束：工作日与周末分别建模，方法集不得混用（SKILL 铁律 3）。
"""
import json
import os
import math
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.environ.get('JZ_DATA_DIR') or os.path.join(os.path.dirname(HERE), 'hist')
OUT = os.environ.get('JZ_OUT_DIR') or os.path.join(HERE, 'out')
os.makedirs(OUT, exist_ok=True)

PLAYS = {
    'wdl':   ('胜平负',      'nspf', lambda r: r['w_actual']),
    'rq':    ('让球胜平负',  'spf',  lambda r: r.get('rq_res') or ''),
    'score': ('比分',        'bf',   lambda r: r.get('score_cat') or ''),
    'goals': ('进球数',      'jqs',  lambda r: r.get('goals_cat') or ''),
    'htft':  ('半全场',      'bqc',  lambda r: r.get('htft_cat') or ''),
}


# ---------------------------------------------------------------- 基础工具
def fnum(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except Exception:
        return None


def implied(odds):
    """赔率字典 -> 归一化隐含概率"""
    inv = {}
    for k, v in odds.items():
        f = fnum(v)
        if f:
            inv[k] = 1.0 / f
    s = sum(inv.values())
    if not s:
        return {}, 0.0
    return {k: v / s for k, v in inv.items()}, s


def topk(odds, k):
    p, _ = implied(odds)
    if not p:
        return []
    return [kk for kk, _ in sorted(p.items(), key=lambda x: -x[1])[:k]]


def fav(odds):
    t = topk(odds, 1)
    return t[0] if t else None


def fav_p(odds):
    p, _ = implied(odds)
    if not p:
        return None, 0.0
    f = max(p, key=lambda x: p[x])
    return f, p[f]


def gap(odds):
    """热门与次热门的概率差"""
    p, _ = implied(odds)
    if len(p) < 2:
        return 0.0
    v = sorted(p.values(), reverse=True)
    return v[0] - v[1]


def o5(r, key):
    return (r.get('o500') or {}).get(key) or {}


def rq(r):
    v = r.get('rq')
    return v if isinstance(v, int) else 0


def rq_dir(r):
    """盘口倾斜法方向：主让 -> 主胜(3)，客让 -> 客胜(0)"""
    v = rq(r)
    if v <= -1:
        return '3'
    if v >= 1:
        return '0'
    return None


def deep_dir(r):
    return rq_dir(r) if abs(rq(r)) >= 2 else None


def roi_stats(units):
    """units: [(net, stake)] -> n(注数), roi, t, ci"""
    tot_stake = sum(s for _, s in units)
    n = len(units)
    if n == 0 or tot_stake == 0:
        return dict(n=0, bets=0, roi=None, t=None, lo=None, hi=None)
    per = [net / s for net, s in units]          # 每注单位收益
    mean = sum(net for net, _ in units) / tot_stake
    if n > 1:
        mu = sum(per) / n
        var = sum((x - mu) ** 2 for x in per) / (n - 1)
        se = math.sqrt(var / n)
        t = mu / se if se > 0 else 0.0
        lo, hi = mu - 1.96 * se, mu + 1.96 * se
    else:
        mu, t, lo, hi = per[0], 0.0, per[0], per[0]
    return dict(n=n, bets=int(tot_stake), roi=100 * mean, t=t, lo=100 * lo, hi=100 * hi)


def ztest(h1, n1, h2, n2):
    if not n1 or not n2:
        return 0.0, 1.0
    p1, p2 = h1 / n1, h2 / n2
    p = (h1 + h2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # 双侧 p 值（正态近似）
    pv = math.erfc(abs(z) / math.sqrt(2))
    return z, pv


# ---------------------------------------------------------------- 方法库
# 每个方法：fn(r) -> 预测值 或 None（不出手）
# 每个方法声明 settles: 结算赔率key（与玩法一致）

def _pick_fav(r):
    return fav(o5(r, 'nspf'))


def mk_methods():
    M = []

    # ---------------- 胜平负 ----------------
    M.append(dict(id='W_fav', play='wdl', name='市场热门(最低赔)',
                  fn=lambda r: fav(o5(r, 'nspf'))))
    M.append(dict(id='W_fav_p70', play='wdl', name='市场热门 p>=70%',
                  fn=lambda r: (fav(o5(r, 'nspf')) if fav_p(o5(r, 'nspf'))[1] >= .70 else None)))
    M.append(dict(id='W_fav_p60_70', play='wdl', name='市场热门 p∈[60,70)%',
                  fn=lambda r: (fav(o5(r, 'nspf')) if .60 <= fav_p(o5(r, 'nspf'))[1] < .70 else None)))
    M.append(dict(id='W_fav_p_ge60', play='wdl', name='市场热门 p>=60%',
                  fn=lambda r: (fav(o5(r, 'nspf')) if fav_p(o5(r, 'nspf'))[1] >= .60 else None)))
    M.append(dict(id='W_fav_p_lt50', play='wdl', name='市场热门 p<50%(三路分散)',
                  fn=lambda r: (fav(o5(r, 'nspf')) if fav_p(o5(r, 'nspf'))[1] < .50 else None)))
    M.append(dict(id='W_fav_gap20', play='wdl', name='市场热门 概率差>=20pp',
                  fn=lambda r: (fav(o5(r, 'nspf')) if gap(o5(r, 'nspf')) >= .20 else None)))
    M.append(dict(id='W_fav_gap30', play='wdl', name='市场热门 概率差>=30pp',
                  fn=lambda r: (fav(o5(r, 'nspf')) if gap(o5(r, 'nspf')) >= .30 else None)))
    M.append(dict(id='W_hcap', play='wdl', name='盘口倾斜法(全)',
                  fn=rq_dir))
    M.append(dict(id='W_hcap_main', play='wdl', name='盘口倾斜法(仅主让)',
                  fn=lambda r: ('3' if rq(r) <= -1 else None)))
    M.append(dict(id='W_hcap_away', play='wdl', name='盘口倾斜法(仅客让)',
                  fn=lambda r: ('0' if rq(r) >= 1 else None)))
    M.append(dict(id='W_deep', play='wdl', name='深盘跟盘 |让球|>=2',
                  fn=deep_dir))
    M.append(dict(id='W_deep3', play='wdl', name='超深盘跟盘 |让球|>=3',
                  fn=lambda r: (rq_dir(r) if abs(rq(r)) >= 3 else None)))
    M.append(dict(id='W_deep_main', play='wdl', name='深盘跟盘(仅主让<-1)',
                  fn=lambda r: ('3' if rq(r) <= -2 else None)))
    M.append(dict(id='W_shallow', play='wdl', name='浅盘盘口法 |让球|=1',
                  fn=lambda r: (rq_dir(r) if abs(rq(r)) == 1 else None)))
    M.append(dict(id='W_flat', play='wdl', name='平手盘(|让球|=0)跟热门',
                  fn=lambda r: (fav(o5(r, 'nspf')) if rq(r) == 0 else None)))
    M.append(dict(id='W_agree', play='wdl', name='盘口×市场 同向',
                  fn=lambda r: (fav(o5(r, 'nspf'))
                                if rq_dir(r) and fav(o5(r, 'nspf')) == rq_dir(r) else None)))
    M.append(dict(id='W_conflict', play='wdl', name='盘口×市场 冲突',
                  fn=lambda r: (fav(o5(r, 'nspf'))
                                if rq_dir(r) and fav(o5(r, 'nspf')) != rq_dir(r) else None)))
    M.append(dict(id='W_deep_agree', play='wdl', name='深盘 + 市场同向',
                  fn=lambda r: (deep_dir(r)
                                if deep_dir(r) and fav(o5(r, 'nspf')) == deep_dir(r) else None)))
    M.append(dict(id='W_fav_odds_130_160', play='wdl', name='热门赔率∈[1.30,1.60)',
                  fn=lambda r: (fav(o5(r, 'nspf'))
                                if o5(r, 'nspf') and 1.30 <= (fnum(o5(r, 'nspf').get(fav(o5(r, 'nspf')))) or 0) < 1.60 else None)))
    M.append(dict(id='W_Aclass', play='wdl', name='A级三条件(p∈[60,70)∩赔∈[1.3,1.6)∩主胜热门)',
                  fn=lambda r: ('3' if (o5(r, 'nspf') and fav(o5(r, 'nspf')) == '3'
                                        and .60 <= fav_p(o5(r, 'nspf'))[1] < .70
                                        and 1.30 <= (fnum(o5(r, 'nspf').get('3')) or 0) < 1.60) else None)))
    M.append(dict(id='W_draw_shield', play='wdl', name='平局保护(概率差<12pp 取平)',
                  fn=lambda r: ('1' if o5(r, 'nspf') and gap(o5(r, 'nspf')) < .12 else None)))
    M.append(dict(id='W_draw_mid', play='wdl', name='平局带(概率差∈[12,22pp) 取平)',
                  fn=lambda r: ('1' if o5(r, 'nspf') and .12 <= gap(o5(r, 'nspf')) < .22 else None)))
    M.append(dict(id='W_fav_main_only', play='wdl', name='市场大热且主胜(p>=60%∩fav=主)',
                  fn=lambda r: ('3' if o5(r, 'nspf') and fav(o5(r, 'nspf')) == '3'
                                and fav_p(o5(r, 'nspf'))[1] >= .60 else None)))
    M.append(dict(id='W_fav_away_only', play='wdl', name='市场大热且客胜(p>=60%∩fav=客)',
                  fn=lambda r: ('0' if o5(r, 'nspf') and fav(o5(r, 'nspf')) == '0'
                                and fav_p(o5(r, 'nspf'))[1] >= .60 else None)))

    # ---------------- 让球胜平负 ----------------
    M.append(dict(id='R_fav', play='rq', name='让球玩法:热门(最低赔)',
                  fn=lambda r: fav(o5(r, 'spf'))))
    M.append(dict(id='R_fav_p60', play='rq', name='让球玩法:热门 p>=60%',
                  fn=lambda r: (fav(o5(r, 'spf')) if fav_p(o5(r, 'spf'))[1] >= .60 else None)))
    M.append(dict(id='R_hcap_main', play='rq', name='让球玩法:主让场取主胜',
                  fn=lambda r: ('3' if rq(r) <= -1 else None)))
    M.append(dict(id='R_fav_not_home', play='rq', name='让球玩法:热门且非主胜',
                  fn=lambda r: (fav(o5(r, 'spf')) if fav(o5(r, 'spf')) in ('0', '1') else None)))

    # ---------------- 比分 ----------------
    M.append(dict(id='S_top1', play='score', name='比分 Top1(最高隐含)',
                  fn=lambda r: (topk(o5(r, 'bf'), 1) or [None])[0]))
    M.append(dict(id='S_top2', play='score', name='比分 Top2',
                  fn=lambda r: None))  # 多选型，单独处理
    M.append(dict(id='S_top3', play='score', name='比分 Top3',
                  fn=lambda r: None))
    M.append(dict(id='S_dir_top1', play='score', name='比分 方向内Top1',
                  fn=lambda r: None))
    M.append(dict(id='S_dir_top2', play='score', name='比分 方向内Top2',
                  fn=lambda r: None))
    M.append(dict(id='S_fixed21', play='score', name='比分 固定2:1(基准)',
                  fn=lambda r: '2:1'))
    M.append(dict(id='S_top1_plus_fixed', play='score', name='比分 Top1 或固定2:1(双选)',
                  fn=lambda r: None))

    # ---------------- 进球数 ----------------
    M.append(dict(id='G_top1', play='goals', name='进球数 Top1(最高隐含)',
                  fn=lambda r: (topk(o5(r, 'jqs'), 1) or [None])[0]))
    M.append(dict(id='G_top2', play='goals', name='进球数 Top2',
                  fn=lambda r: None))
    M.append(dict(id='G_top3', play='goals', name='进球数 Top3',
                  fn=lambda r: None))
    M.append(dict(id='G_fix_1_3', play='goals', name='进球数 固定1-3球',
                  fn=lambda r: None))
    M.append(dict(id='G_fix_2_4', play='goals', name='进球数 固定2-4球',
                  fn=lambda r: None))
    M.append(dict(id='G_fix_2_3', play='goals', name='进球数 固定2-3球',
                  fn=lambda r: None))
    M.append(dict(id='G_fix_3', play='goals', name='进球数 固定3球(单值基准)',
                  fn=lambda r: '3'))
    M.append(dict(id='G_top2_wide', play='goals', name='进球数 隐含Top2 ∪ {2,3}',
                  fn=lambda r: None))
    M.append(dict(id='G_hi_conf', play='goals', name='进球数 Top2 且 P(top1)>=35%',
                  fn=lambda r: None))

    # ---------------- 半全场 ----------------
    M.append(dict(id='H_top1', play='htft', name='半全场 Top1(最高隐含)',
                  fn=lambda r: (topk(o5(r, 'bqc'), 1) or [None])[0]))
    M.append(dict(id='H_top2', play='htft', name='半全场 Top2',
                  fn=lambda r: None))
    M.append(dict(id='H_top3', play='htft', name='半全场 Top3',
                  fn=lambda r: None))
    M.append(dict(id='H_cond', play='htft', name='半全场 条件版(主让->胜胜/客让->负负)',
                  fn=lambda r: ('3-3' if rq(r) <= -1 else ('0-0' if rq(r) >= 1 else None))))
    M.append(dict(id='H_cond_main', play='htft', name='半全场 仅主让->胜胜',
                  fn=lambda r: ('3-3' if rq(r) <= -1 else None)))
    M.append(dict(id='H_fix_33', play='htft', name='半全场 固定胜胜(基准)',
                  fn=lambda r: '3-3'))
    M.append(dict(id='H_top2_drawbr', play='htft', name='半全场 Top2 ∪ {1-1,0-0}(挂平分支)',
                  fn=lambda r: None))
    M.append(dict(id='H_deep_cond', play='htft', name='半全场 深盘条件版(|让球|>=2->对应胜胜/负负)',
                  fn=lambda r: (('3-3' if rq(r) < 0 else '0-0') if abs(rq(r)) >= 2 else None)))

    # ---------------- 分环境门控变体（校正迭代用） ----------------
    M.append(dict(id='W_deep_p60', play='wdl', name='[门控]深盘+同向p>=60%',
                  fn=lambda r: (deep_dir(r) if deep_dir(r) and fav_p(o5(r, 'nspf'))[1] >= .60
                                and fav(o5(r, 'nspf')) == deep_dir(r) else None)))
    M.append(dict(id='W_fav_main_gap20', play='wdl', name='[门控]主胜热门 p>=60% 且概率差>=20pp',
                  fn=lambda r: ('3' if o5(r, 'nspf') and fav(o5(r, 'nspf')) == '3'
                                and fav_p(o5(r, 'nspf'))[1] >= .60
                                and gap(o5(r, 'nspf')) >= .20 else None)))
    M.append(dict(id='W_fav_main_odds13', play='wdl', name='[门控]主胜热门 且赔率>=1.30',
                  fn=lambda r: ('3' if o5(r, 'nspf') and fav(o5(r, 'nspf')) == '3'
                                and (fnum(o5(r, 'nspf').get('3')) or 0) >= 1.30 else None)))
    M.append(dict(id='W_fav_p65', play='wdl', name='[门控]市场热门 p>=65%',
                  fn=lambda r: (fav(o5(r, 'nspf')) if fav_p(o5(r, 'nspf'))[1] >= .65 else None)))
    M.append(dict(id='W_hcap_main_p55', play='wdl', name='[门控]盘口倾斜(仅主让) 且 p>=55%',
                  fn=lambda r: ('3' if rq(r) <= -1 and fav_p(o5(r, 'nspf'))[1] >= .55 else None)))
    M.append(dict(id='W_hcap_main_p60', play='wdl', name='[门控]盘口倾斜(仅主让) 且 p>=60%',
                  fn=lambda r: ('3' if rq(r) <= -1 and fav_p(o5(r, 'nspf'))[1] >= .60 else None)))

    M.append(dict(id='S_top3_gap20', play='score', name='[门控]比分Top3 且 概率差>=20pp',
                  fn=lambda r: None))
    M.append(dict(id='S_top3_gap_lt20', play='score', name='[门控]比分Top3 且 概率差<20pp',
                  fn=lambda r: None))
    M.append(dict(id='S_top3_main', play='score', name='[门控]比分Top3 仅主让场',
                  fn=lambda r: None))
    M.append(dict(id='S_top3_away', play='score', name='[门控]比分Top3 仅客让场',
                  fn=lambda r: None))
    M.append(dict(id='S_top3_p60', play='score', name='[门控]比分Top3 且热门p>=60%',
                  fn=lambda r: None))

    M.append(dict(id='G_fix_2_4_main', play='goals', name='[门控]固定2-4球 仅主让场',
                  fn=lambda r: None))
    M.append(dict(id='G_fix_1_3_away', play='goals', name='[门控]固定1-3球 仅客让场',
                  fn=lambda r: None))
    M.append(dict(id='G_fix_1_3_main', play='goals', name='[门控]固定1-3球 仅主让场',
                  fn=lambda r: None))
    M.append(dict(id='G_top3_gap20', play='goals', name='[门控]进球Top3 且 概率差>=20pp',
                  fn=lambda r: None))
    M.append(dict(id='G_top3_p60', play='goals', name='[门控]进球Top3 且热门p>=60%',
                  fn=lambda r: None))

    M.append(dict(id='H_top3_main', play='htft', name='[门控]半全场Top3 仅主让场',
                  fn=lambda r: None))
    M.append(dict(id='H_top3_away', play='htft', name='[门控]半全场Top3 仅客让场',
                  fn=lambda r: None))
    M.append(dict(id='H_top3_deep', play='htft', name='[门控]半全场Top3 仅深盘场',
                  fn=lambda r: None))
    M.append(dict(id='H_top3_shallow', play='htft', name='[门控]半全场Top3 仅浅盘场',
                  fn=lambda r: None))
    M.append(dict(id='H_top3_p60', play='htft', name='[门控]半全场Top3 且热门p>=60%',
                  fn=lambda r: None))

    return M


# ---------------------------------------------------------------- 多选型方法
def multi_pred(r, mid):
    """多选型方法：返回预测集合(set)；单选型返回单元素集合"""
    bf = o5(r, 'bf')
    jq = o5(r, 'jqs')
    bq = o5(r, 'bqc')
    nspf = o5(r, 'nspf')
    if mid == 'S_top2':
        return set(topk(bf, 2))
    if mid == 'S_top3':
        return set(topk(bf, 3))
    if mid == 'S_dir_top1':
        d = rq_dir(r)
        t = topk(bf, 6)
        return {t[0]} if d and t else set()
    if mid == 'S_dir_top2':
        # 方向（主胜/平/客胜）内按隐含概率取前2
        d = rq_dir(r)
        if not d:
            return set()
        bucket = {'3': [], '1': [], '0': []}
        for s in topk(bf, 31):
            h, a = s.split(':') if ':' in s else (None, None)
            if s == '胜其他':
                bucket['3'].append(s)
            elif s == '平其他':
                bucket['1'].append(s)
            elif s == '负其他':
                bucket['0'].append(s)
            elif h is not None:
                hh, aa = int(h), int(a)
                bucket['3' if hh > aa else ('1' if hh == aa else '0')].append(s)
        return set(bucket[d][:2])
    if mid == 'S_top1_plus_fixed':
        return set(topk(bf, 1)) | {'2:1'}
    if mid == 'G_top2':
        return set(topk(jq, 2))
    if mid == 'G_top3':
        return set(topk(jq, 3))
    if mid == 'G_fix_1_3':
        return {'1', '2', '3'}
    if mid == 'G_fix_2_4':
        return {'2', '3', '4'}
    if mid == 'G_fix_2_3':
        return {'2', '3'}
    if mid == 'G_top2_wide':
        return set(topk(jq, 2)) | {'2', '3'}
    if mid == 'G_hi_conf':
        t = topk(jq, 2)
        p, _ = implied(jq)
        return set(t) if p and t and p[t[0]] >= .35 else set()
    if mid == 'H_top2':
        return set(topk(bq, 2))
    if mid == 'H_top3':
        return set(topk(bq, 3))
    if mid == 'H_top2_drawbr':
        return set(topk(bq, 2)) | {'1-1', '0-0'}
    # ---- 门控变体 ----
    g = gap(nspf)
    fp = fav_p(nspf)[1]
    if mid == 'S_top3_gap20':
        return set(topk(bf, 3)) if g >= .20 else set()
    if mid == 'S_top3_gap_lt20':
        return set(topk(bf, 3)) if g < .20 else set()
    if mid == 'S_top3_main':
        return set(topk(bf, 3)) if rq(r) <= -1 else set()
    if mid == 'S_top3_away':
        return set(topk(bf, 3)) if rq(r) >= 1 else set()
    if mid == 'S_top3_p60':
        return set(topk(bf, 3)) if fp >= .60 else set()
    if mid == 'G_fix_2_4_main':
        return {'2', '3', '4'} if rq(r) <= -1 else set()
    if mid == 'G_fix_1_3_away':
        return {'1', '2', '3'} if rq(r) >= 1 else set()
    if mid == 'G_fix_1_3_main':
        return {'1', '2', '3'} if rq(r) <= -1 else set()
    if mid == 'G_top3_gap20':
        return set(topk(jq, 3)) if g >= .20 else set()
    if mid == 'G_top3_p60':
        return set(topk(jq, 3)) if fp >= .60 else set()
    if mid == 'H_top3_main':
        return set(topk(bq, 3)) if rq(r) <= -1 else set()
    if mid == 'H_top3_away':
        return set(topk(bq, 3)) if rq(r) >= 1 else set()
    if mid == 'H_top3_deep':
        return set(topk(bq, 3)) if abs(rq(r)) >= 2 else set()
    if mid == 'H_top3_shallow':
        return set(topk(bq, 3)) if abs(rq(r)) == 1 else set()
    if mid == 'H_top3_p60':
        return set(topk(bq, 3)) if fp >= .60 else set()
    return None


MULTI = {'S_top2', 'S_top3', 'S_dir_top1', 'S_dir_top2', 'S_top1_plus_fixed',
         'G_top2', 'G_top3', 'G_fix_1_3', 'G_fix_2_4', 'G_fix_2_3',
         'G_top2_wide', 'G_hi_conf', 'H_top2', 'H_top3', 'H_top2_drawbr',
         'S_top3_gap20', 'S_top3_gap_lt20', 'S_top3_main', 'S_top3_away', 'S_top3_p60',
         'G_fix_2_4_main', 'G_fix_1_3_away', 'G_fix_1_3_main', 'G_top3_gap20', 'G_top3_p60',
         'H_top3_main', 'H_top3_away', 'H_top3_deep', 'H_top3_shallow', 'H_top3_p60'}


# ---------------------------------------------------------------- 评估
def eval_method(rows, m):
    """返回 dict(n, hits, acc, roi)"""
    play = m['play']
    pk, odds_key, actual_fn = PLAYS[play]
    n = h = 0
    units = []
    for r in rows:
        odds = o5(r, odds_key)
        actual = actual_fn(r)
        if not actual:
            continue
        if m['id'] in MULTI:
            pset = multi_pred(r, m['id'])
            if not pset:
                continue
            k = len(pset)
            n += 1
            ok = actual in pset
            if ok:
                h += 1
            o = fnum(odds.get(actual)) if odds else None
            if o:
                units.append(((o - k) if ok else -k, k))
        else:
            pred = m['fn'](r)
            if pred is None:
                continue
            n += 1
            ok = pred == actual
            if ok:
                h += 1
            o = fnum(odds.get(pred)) if odds else None
            if o:
                units.append(((o - 1.0) if ok else -1.0, 1))
    return dict(n=n, hits=h, acc=(100.0 * h / n if n else None), roi=roi_stats(units))


def baseline(rows, play):
    """最频繁单一结果基准"""
    pk, odds_key, actual_fn = PLAYS[play]
    c = collections.Counter(actual_fn(r) for r in rows if actual_fn(r))
    if not c:
        return 0, 0, None
    top, _ = c.most_common(1)[0]
    n = sum(c.values())
    return n, c[top], (100.0 * c[top] / n), top


def fmt_roi(rs):
    if rs['n'] == 0 or rs['roi'] is None:
        return 'n/a'
    return '%+7.2f%% [%+6.1f,%+6.1f] t=%5.2f (注=%d)' % (
        rs['roi'], rs['lo'], rs['hi'], rs['t'], rs['bets'])


# ---------------------------------------------------------------- 主流程
def run(env, path):
    rows = json.load(open(path, encoding='utf-8'))
    rows = [r for r in rows if r.get('w_actual')]
    rows.sort(key=lambda r: (r['date'], r['time']))
    n = len(rows)
    cut = int(n * 0.6)
    train, test = rows[:cut], rows[cut:]

    METHODS = mk_methods()
    lines = []
    P = lines.append
    P('=' * 92)
    P('%s 环境回测  样本 n=%d   区间 %s ~ %s' % (
        '工作日' if env == 'weekday' else '周末', n, rows[0]['date'], rows[-1]['date']))
    P('时序切分：校准期(前60%%) n=%d [%s~%s]  |  验证期(后40%%) n=%d [%s~%s]' % (
        len(train), train[0]['date'], train[-1]['date'],
        len(test), test[0]['date'], test[-1]['date']))
    P('=' * 92)

    results = {}
    for play, (pk, _, _) in PLAYS.items():
        bn, bh, bacc, btop = baseline(rows, play)
        P('')
        P('【%s】基准(始终取最频结果 %s)=%s  (n=%d)' % (
            pk, btop, ('%.1f%%' % bacc) if bacc else 'n/a', bn))
        P('%-44s %5s %5s %7s   %-34s %s' % ('方法', 'n', '命中', '命中率', 'ROI(注数/ROI/95%CI/t)', '校准期→验证期'))
        P('-' * 120)
        rows_out = []
        for m in METHODS:
            if m['play'] != play:
                continue
            ev = eval_method(rows, m)
            if ev['n'] == 0:
                continue
            tr = eval_method(train, m)
            te = eval_method(test, m)
            rows_out.append(dict(mid=m['id'], name=m['name'], **ev, train=tr, test=te))
        rows_out.sort(key=lambda x: (-(x['acc'] or 0), -(x['roi']['bets'] or 0)))
        for x in rows_out:
            P('%-44s %5d %5d %6s%%   %-34s %5s%% -> %5s%%' % (
                x['name'], x['n'], x['hits'],
                ('%.1f' % x['acc']) if x['acc'] is not None else 'n/a',
                fmt_roi(x['roi']),
                ('%.1f' % x['train']['acc']) if x['train']['acc'] is not None else 'n/a',
                ('%.1f' % x['test']['acc']) if x['test']['acc'] is not None else 'n/a'))
        results[play] = rows_out

    # 环境结构
    P('')
    P('【环境结构】')
    c = collections.Counter(r['w_actual'] for r in rows)
    P('  胜平负: 主胜%d(%.1f%%) 平%d(%.1f%%) 客胜%d(%.1f%%)' % (
        c['3'], 100 * c['3'] / n, c['1'], 100 * c['1'] / n, c['0'], 100 * c['0'] / n))
    g = [r['goals'] for r in rows]
    P('  场均进球=%.2f  大球(>=4)=%d(%.1f%%)' % (
        sum(g) / n, sum(1 for x in g if x >= 4), 100 * sum(1 for x in g if x >= 4) / n))
    P('  让球盘: %s' % dict(collections.Counter(r.get('handicap') for r in rows)))
    P('  平局率=%.1f%%' % (100 * c['1'] / n))

    txt = '\n'.join(lines)
    open(os.path.join(OUT, 'rank_%s.txt' % env), 'w', encoding='utf-8').write(txt)
    json.dump(results, open(os.path.join(OUT, 'rank_%s.json' % env), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(txt)
    return results


if __name__ == '__main__':
    run('weekday', os.path.join(HIST, 'master300_odds.json'))
    run('weekend', os.path.join(HIST, 'master200_wk_odds.json'))
