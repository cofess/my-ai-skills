# -*- coding: utf-8 -*-
"""
分环境方法集【固化版】—— 工作日 / 周末 两套互不相同的方法集

闭环：分析 → 预测 → 比对赛果 → 校正（参数迭代 + 时序留出验证）→ 定稿

⚠ 规则真源 = `env_rules.py`（与 `today_predict.py` 共用同一份 wd_rules/we_rules/predict）
   —— 本文件只负责「读数据 + 跑评估 + 出报表」，不再自行定义规则，避免回测与预测口径分裂。

数据口径：500 终盘赔率结算；让球盘口来自澳客/竞彩开奖。
环境变量：JZ_DATA_DIR（数据集目录）、JZ_OUT_DIR（输出目录）

产出：out/final_{weekday,weekend}.txt / .json、out/detail_{weekday,weekend}.csv
"""
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import env_rules as E  # noqa: E402

HIST = os.environ.get('JZ_DATA_DIR') or os.path.join(os.path.dirname(HERE), 'hist')
OUT = os.environ.get('JZ_OUT_DIR') or os.path.join(HERE, 'out')
os.makedirs(OUT, exist_ok=True)

ENV_META = [
    ('weekday', '工作日', 'master300_odds.json'),
    ('weekend', '周末', 'master200_wk_odds.json'),
]


def env_run(env, name, path):
    rows = json.load(open(path, encoding='utf-8'))
    rows = [r for r in rows if r.get('w_actual')]
    rows.sort(key=lambda r: (r['date'], r.get('time') or ''))
    n = len(rows)
    cut = int(n * .6)
    train, test = rows[:cut], rows[cut:]
    rules = E.rules_for(env)

    L = []
    P = L.append
    P('=' * 108)
    P('%s 环境 · 方法集【%s】  样本 n=%d  %s ~ %s' % (name, env, n, rows[0]['date'], rows[-1]['date']))
    P('时序留出：校准期 n=%d（%s~%s） | 验证期 n=%d（%s~%s）' % (
        len(train), train[0]['date'], train[-1]['date'],
        len(test), test[0]['date'], test[-1]['date']))
    P('=' * 108)
    P('%-30s %-46s %5s %5s %7s  %s' % (
        '玩法/档位', '规则', 'n', '命中', '命中率', 'ROI(注数·ROI·95%CI·t) / 验证期'))
    P('-' * 108)

    frozen = {}
    details = {}
    for rule in rules:
        ev, det = E.evaluate(rows, rule)
        tr, _ = E.evaluate(train, rule)
        te, _ = E.evaluate(test, rule)
        details[rule['key']] = det
        if ev['n'] == 0:
            P('%-30s %-46s %5s %5s %7s  【本轮不出手】' % (rule['tag'], rule['name'], '-', '-', '-'))
            frozen[rule['key']] = dict(rule=rule['name'], n=0, hits=0, acc=None, roi=None)
            continue
        rs = ev['roi']
        roi_s = ('n/a' if rs['roi'] is None else
                 '%+7.2f%% (注%3d, CI %+6.1f~%+6.1f, t=%5.2f)' % (
                     rs['roi'], rs['bets'], rs['lo'], rs['hi'], rs['t']))
        P('%-30s %-46s %5d %5d %6.1f%%  %s' % (
            rule['tag'], rule['name'], ev['n'], ev['hits'], ev['acc'], roi_s))
        P('%-30s %-46s %5s %5s %6s%%  留出：校准 %s%% → 验证 %s%%' % (
            '', '', '', '',
            ('%.1f' % te['acc']) if te['acc'] is not None else 'n/a',
            ('%.1f' % tr['acc']) if tr['acc'] is not None else 'n/a',
            ('%.1f' % te['acc']) if te['acc'] is not None else 'n/a'))
        frozen[rule['key']] = dict(rule=rule['name'], tag=rule['tag'], n=ev['n'], hits=ev['hits'],
                                  acc=ev['acc'], roi=rs, train_acc=tr['acc'], test_acc=te['acc'])

    # 环境结构
    c = collections.Counter(r['w_actual'] for r in rows)
    g = [r['goals'] for r in rows]
    P('')
    P('【环境结构】主胜%.1f%% 平%.1f%% 客胜%.1f%% | 场均进球%.2f | 大球(≥4)%.1f%% | 让球盘结果≠不让球结果 %.1f%%' % (
        100 * c['3'] / n, 100 * c['1'] / n, 100 * c['0'] / n, sum(g) / n,
        100 * sum(1 for x in g if x >= 4) / n,
        100 * sum(1 for r in rows if r.get('rq_res') and r['rq_res'] != r['w_actual']) / n))

    txt = '\n'.join(L)
    open(os.path.join(OUT, 'final_%s.txt' % env), 'w', encoding='utf-8').write(txt)
    print(txt)
    json.dump(frozen, open(os.path.join(OUT, 'final_%s.json' % env), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, 'detail_%s.csv' % env), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['玩法', '编号', '日期', '主队', '客队', '让球', '实际', '预测', '命中', '比分', '结算赔率'])
        for rule in rules:
            for d in details.get(rule['key'], []):
                w.writerow([E.PLAYNAME[rule['play']], d['no'], d['date'], d['home'], d['away'],
                            d['rq'], d['actual'], d['pred'], d['ok'], d['ft'],
                            '' if d['odds'] is None else d['odds']])
    return rows, frozen


def load_env(env):
    for e, name, fn in ENV_META:
        if e == env:
            return json.load(open(os.path.join(HIST, fn), encoding='utf-8'))
    raise KeyError(env)


if __name__ == '__main__':
    cache = {}
    for env, name, fn in ENV_META:
        rows, frozen = env_run(env, name, os.path.join(HIST, fn))
        cache[env] = rows
        print()

    # ---------------- 错因分解（主推规则） ----------------
    print()
    print('=' * 108)
    print('【错因分解 · 主推规则未命中场次】')
    print('=' * 108)
    for env, name in [('weekday', '工作日·深盘跟盘'), ('weekend', '周末·A级三条件')]:
        rows = cache[env]
        rule = E.rules_for(env)[0]
        ev, det = E.evaluate(rows, rule)
        miss = [d for d in det if not d['ok']]
        cause = collections.Counter()
        for d in miss:
            a = d['actual']
            if rule['play'] == 'wdl':
                direction = '3' if d['rq'] < 0 else '0'
                if a == '1':
                    cause['平局爆冷'] += 1
                else:
                    cause['方向押反(客胜)' if a == '0' else '方向押反(主胜)'] += 1
            else:
                cause['未覆盖'] += 1
        print('%-16s n=%3d 命中=%3d (%s)  未命中=%d  → %s' % (
            name, ev['n'], ev['hits'],
            ('%.1f%%' % ev['acc']) if ev['acc'] is not None else 'n/a',
            len(miss), dict(cause)))
        for d in miss:
            print('      ✗ %s %s %s-%s 让球%s 实际=%s 预测=%s 比分%s' % (
                d['no'], d['date'], d['home'], d['away'], d['rq'], d['actual'], d['pred'], d['ft']))

    # ---------------- 显著性 ----------------
    print()
    print('【显著性检验（主推 vs 基准全主胜）】')
    for env, name in [('weekday', '工作日·深盘跟盘'), ('weekend', '周末·A级三条件')]:
        rows = cache[env]
        rule = E.rules_for(env)[0]
        ev, _ = E.evaluate(rows, rule)
        bn, bh, bacc = E.baseline(rows, 'wdl')
        z, p = E.ztest(ev['hits'], ev['n'], bh, bn)
        print('  %-16s %.1f%%(%d/%d) vs 基准 %.1f%%(%d/%d)  z=%.2f  p=%.4f %s' % (
            name, ev['acc'], ev['hits'], ev['n'], bacc, bh, bn, z, p,
            '★' if p < .01 else ('*' if p < .05 else '')))

    # ---------------- 基线自检 ----------------
    print()
    print('【基线自检】工作日应复现：深盘跟盘 39/44=88.6% | 半全场深盘Top3 19/21=90.5% | 比分 17/49=34.7%')
