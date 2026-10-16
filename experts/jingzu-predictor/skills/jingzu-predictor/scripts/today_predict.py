# -*- coding: utf-8 -*-
"""
当日预测判定 —— 套用【工作日 / 周末】固化方法集

**与回测共用同一份规则**（env_rules.rules_for / predict），严禁手算、严禁另写一份。

用法：
    python today_predict.py --input examples/today_2026-10-16.json
    python today_predict.py --input today.json --env weekend        # 强制环境
    python today_predict.py --input today.json --out out/

输入 JSON（每场一个对象）：
[
  {"no": "周五001", "date": "2026-10-16", "league": "澳超",
   "home": "悉尼FC", "away": "西悉尼流浪者", "rq": -1,
   "odds": {
     "nspf": {"3": 2.06, "1": 3.53, "0": 2.77},        # 不让球胜平负（titan007 无 → 用即时平均欧指替代，必须披露）
     "spf":  {"3": 4.03, "1": 3.90, "0": 1.60},        # 让球胜平负 SP
     "jqs":  {"0": 15.5, "1": 5.8, ..., "7+": 17.0},   # 进球数 SP
     "bqc":  {"3-3": 3.6, "3-1": 13.0, ...},           # 半全场 SP
     "bf":   {"1:0": 9.65, ...}                        # 比分 SP（可缺）
   }}
]

环境判定：默认按竞彩期号前缀（周X）——周六/周日 → weekend，其余 → weekday。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from env_rules import (implied, fav, favp, gp, o5, rqv, rules_for, predict,  # noqa: E402
                       env_of)

DIRNAME = {'3': '主胜', '1': '平局', '0': '客胜'}
ENVNAME = {'weekday': '工作日', 'weekend': '周末'}


def build_rows(items):
    rows = []
    for it in items:
        rows.append(dict(
            no=it.get('no', ''), date=it.get('date', ''), league=it.get('league', ''),
            home=it.get('home', ''), away=it.get('away', ''), rq=it.get('rq', 0),
            o500=it.get('odds') or {},
        ))
    return rows


def coverage(rows):
    """数据可得性披露（铁律 5）"""
    n = len(rows)
    miss = {}
    for key in ('nspf', 'spf', 'jqs', 'bqc', 'bf'):
        miss[key] = [r['no'] for r in rows if not o5(r, key)]
    return n, miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='当日数据 JSON')
    ap.add_argument('--env', choices=['weekday', 'weekend'], default=None,
                    help='强制环境；默认按期号前缀自动判定')
    ap.add_argument('--out', default=os.path.join(HERE, 'out'))
    ap.add_argument('--title', default=None)
    args = ap.parse_args()

    items = json.load(open(args.input, encoding='utf-8'))
    rows = build_rows(items)
    if not rows:
        print('输入为空'); return

    date = rows[0]['date'] or 'unknown'
    env = args.env or env_of(rows[0]['no'])
    rules = rules_for(env)
    os.makedirs(args.out, exist_ok=True)

    L = []
    P = L.append
    P('=' * 104)
    P('%s · %s环境 · 套用【%s】固化方法集' % (
        args.title or date, ENVNAME[env], env))
    P('=' * 104)
    P('')

    # 0) 结构预案 —— 决定哪些高精度规则今日可用
    deep = [r['no'] for r in rows if abs(rqv(r)) >= 2]
    hom = [r['no'] for r in rows if rqv(r) <= -1]
    away = [r['no'] for r in rows if rqv(r) >= 1]
    P('【今日结构】共 %d 场 | 主让(rq=-1) %d 场: %s | 客让(rq=+1) %d 场: %s'
      % (len(rows), len(hom), ','.join(hom) or '-', len(away), ','.join(away) or '-'))
    if env == 'weekday':
        P('深盘(|让球|>=2): %s%s' % (
            ','.join(deep) if deep else '无',
            ' → 依赖深盘的两条规则（深盘跟盘 88.6% / 半全场深盘Top3 90.5%）今日全部空仓'
            if not deep else ''))
    P('')

    # 0.5) 可得性披露
    n, miss = coverage(rows)
    gaps = ['%s 缺 %d/%d 场' % (k, len(v), n) for k, v in miss.items() if v]
    if gaps:
        P('【数据缺失披露】' + ' | '.join(gaps))
        for k, v in miss.items():
            if k == 'nspf' and v:
                P('  ⚠ nspf（不让球胜平负）缺失 → 若已用「即时平均欧指」替代，临界判定会漂移，须在报告中标注')
            if k == 'bf' and v:
                P('  ⚠ bf（比分）缺失 → 比分玩法仅在有赔率的场次出手')
        P('')

    # 1) 逐场指标
    P('-' * 104)
    P('【逐场指标】p=归一化隐含概率  gp=概率差(百分点)  fav=最高概率项')
    P('-' * 104)
    P('%-6s %-9s %-16s %-16s %4s | %-20s | %6s %6s  %-14s | %s' % (
        '编号', '联赛', '主队', '客队', '让球', 'nspf(主/平/客)', 'p主', 'gp', '让球盘最热', 'fav'))
    for r in rows:
        o = o5(r, 'nspf')
        spf = o5(r, 'spf')
        if o:
            p = implied(o)
            f, fp = favp(o)
            sf, sp = favp(spf) if spf else (None, 0.0)
            P('%-6s %-9s %-16s %-16s %+4d | %.2f/%.2f/%.2f | %5.1f%% %6.1f%%  %-14s | %s %.1f%%' % (
                r['no'], r['league'], r['home'][:16], r['away'][:16], rqv(r),
                o.get('3', 0), o.get('1', 0), o.get('0', 0),
                p.get('3', 0) * 100, gp(o),
                ('%s %.1f%%' % (DIRNAME.get(sf, '-'), sp * 100)) if sf else 'n/a',
                DIRNAME.get(f, '-'), fp * 100))
        else:
            sf, sp = favp(spf) if spf else (None, 0.0)
            P('%-6s %-9s %-16s %-16s %+4d | %-20s | %5s %5s  %-14s | %s' % (
                r['no'], r['league'], r['home'][:16], r['away'][:16], rqv(r),
                'nspf 缺失', '-', '-',
                ('%s %.1f%%' % (DIRNAME.get(sf, '-'), sp * 100)) if sf else 'n/a', '不可判'))
    P('')

    # 2) 各档位出手判定（复用回测同款 predict）
    P('-' * 104)
    P('【%s方法集 · 今日出手判定】' % ENVNAME[env])
    P('-' * 104)
    for rule in rules:
        P('· %s —— %s' % (rule['tag'], rule['name']))
        hits = []
        for r in rows:
            pset, k = predict(r, rule)
            if pset:
                hits.append((r['no'], r['home'] + ' vs ' + r['away'], sorted(pset), k))
        if not hits:
            P('    今日不出手（无条件满足）')
        else:
            for no, vs, pset, k in hits:
                if rule['key'] in ('wdl', 'rq'):
                    show = [DIRNAME.get(x, x) for x in pset]
                elif rule['key'] == 'goals':
                    show = [x + '球' for x in pset]
                else:
                    show = pset
                P('    %-6s %-34s → %s  (%d注)' % (no, vs[:34], '|'.join(show), k))
        P('')

    P('=' * 104)
    P('⚠ 本输出为基于公开数据的统计分析，不构成投注建议。命中率高 ≠ 盈利（见 SKILL.md 铁律 4）。')

    txt = '\n'.join(L)
    path = os.path.join(args.out, 'today_%s.txt' % date)
    open(path, 'w', encoding='utf-8').write(txt)
    print(txt)
    print('\n[written] %s' % path)


if __name__ == '__main__':
    main()
