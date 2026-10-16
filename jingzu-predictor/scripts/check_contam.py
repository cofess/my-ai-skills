# -*- coding: utf-8 -*-
"""
回归校验：确认「工作日·比分」档位不再被赔率缺失污染（校正项 D10 / C11-a）

问题（D10）：
    `gp()` 在赔率缺失时返回 0.0 → 被门控「概率差 < 20pp」误判为「均势局」纳入。
    污染范围 = 有 bf 但无 nspf 的场次（工作日 15 场）。
    bf 缺失时 topk({}) = [] → 自动出局，不受影响。

本脚本对照两种口径，证明门控修复有效：
    第6轮原逻辑（无门控）  vs  修正版（加 avail 门控）
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env_rules as E  # noqa: E402

HIST = os.environ.get('JZ_DATA_DIR') or os.path.join(os.path.dirname(HERE), 'hist')


def score_pick(r, strict, env):
    """工作日: 比分Top3 ∩ gp(nspf)<20pp ｜ 周末: 比分Top3 ∩ 仅客让 rq≥1"""
    bf = E.o5(r, 'bf')
    if not bf:
        return set()
    if env == 'weekday':
        if strict and not E.o5(r, 'nspf'):
            return set()
        if E.gp(E.o5(r, 'nspf')) >= 20:
            return set()
    else:
        if not isinstance(r.get('rq'), int) or r['rq'] < 1:
            return set()
    return set(E.topk(bf, 3))


def run(rows, strict, env):
    n = h = 0
    units = []
    for r in rows:
        pset = score_pick(r, strict, env)
        if not pset:
            continue
        a = r.get('score_cat') or ''
        if not a:
            continue
        n += 1
        ok = a in pset or E.ALIAS.get(a) in pset
        h += 1 if ok else 0
        odds = E.o5(r, 'bf')
        vals = [E.geth(odds, s) for s in pset]
        if any(v is None for v in vals):
            continue
        k = 3
        if ok:
            o = E.geth(odds, a)
            if o is None:
                continue
            units.append((o - k, k))
        else:
            units.append((-k, k))
    tot = sum(s for _, s in units)
    roi = (100.0 * sum(net for net, _ in units) / tot) if tot else None
    return n, h, (100.0 * h / n if n else None), roi, len(units)


if __name__ == '__main__':
    for label, fn, env in [('工作日', 'master300_odds.json', 'weekday'),
                           ('周末', 'master200_wk_odds.json', 'weekend')]:
        rows = json.load(open(os.path.join(HIST, fn), encoding='utf-8'))
        rows = [r for r in rows if r.get('w_actual')]
        n_bf = sum(1 for r in rows if E.o5(r, 'bf'))
        n_nspf = sum(1 for r in rows if E.o5(r, 'nspf'))
        n_bf_only = sum(1 for r in rows if E.o5(r, 'bf') and not E.o5(r, 'nspf'))
        print('=' * 96)
        print('[%s] n=%d | 有bf %d | 有nspf %d | 有bf无nspf(污染源) %d' % (
            label, len(rows), n_bf, n_nspf, n_bf_only))
        for strict in (False, True):
            n, h, acc, roi, nb = run(rows, strict, env)
            tag = '修正版(加可得性门控)' if strict else '第6轮原逻辑        '
            print('  %s  n=%3d 命中=%3d 命中率=%s  ROI=%s (结算%d场/注%d)' % (
                tag, n, h,
                ('%.1f%%' % acc) if acc is not None else 'n/a',
                ('%+.2f%%' % roi) if roi is not None else 'n/a', nb, nb * 3))
