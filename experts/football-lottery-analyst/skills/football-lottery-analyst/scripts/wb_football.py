#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""足彩预测大师 · 一体化 CLI

子命令
------
fetch-odds     抓 trade.500.com 四玩法页面（当日或历史区间）
parse-odds     解析四玩法页面 -> odds.json
fetch-results  抓澳客竞彩开奖页 -> results.json（已归一化）
build          合并「赛果 + 500 终盘赔率」-> master_odds.json
backtest       回测：预测 vs 赛果 -> 命中率 / ROI / 重点子集 / 显著性
analyze        当日赔率结构 + 重点场次候选（爆冷/高赔/大球/半全场反转）
compare        预测存档 vs 赛果：闭环比对 + 逐玩法命中率

运行示例见 scripts/README.md。所有输出写入 --out 指定的目录（默认当前目录）。
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wbf_lib as L                                        # noqa: E402

# ------------------------------------------------------------------ 解析正则
# 注意：re.split 会在**每个**行首处切开，因此每段天然被下一行界定，
# 不会把后续行的赔率混进来（比分行内含嵌套 <table>，不能按 </tr> 截断）。
RE_TR = re.compile(r'<tr class="bet-tb-tr')
RE_ATTR = re.compile(r'data-([a-z0-9]+)="([^"]*)"')
RE_BET_A = re.compile(r'data-type="([a-z]+)"[^>]*?data-value="([^"]+)"[^>]*?data-sp="([\d.]+)"')
RE_BET_B = re.compile(r'data-value="([^"]+)"[^>]*?data-sp="([\d.]+)"[^>]*?data-type="([a-z]+)"')
RE_SCORE = re.compile(r'class="score"[^>]*>(\d+:\d+)<')
RE_HALF = re.compile(r'data-halfscore="([^"]+)"')
RE_PID = re.compile(r'^p?w?(\d{3})_')
RE_5DAY = re.compile(r'不得超过5天')


def _mkdir(path):
    """为输出文件/目录准备好父目录。"""
    d = path if os.path.isdir(path) or not os.path.splitext(path)[1] else os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _w(path, text):
    _mkdir(path)
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write(text)


def _dump(path, obj):
    _mkdir(path)
    with open(path, 'w', encoding='utf-8') as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=1)


# =========================================================== fetch-odds
def cmd_fetch_odds(a):
    os.makedirs(a.out, exist_ok=True)
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    pids = a.playid or list(L.PLAYS)
    n = 0
    d = start
    while d <= end:
        kind = 'weekend' if d.weekday() >= 5 else 'weekday'
        if a.days == 'all' or a.days == kind:
            for pid in pids:
                code, size = L.fetch_500_day(pid, d.isoformat(), a.out, a.force)
                print('%s playid=%s %s size=%s' % (d.isoformat(), pid, code, size), flush=True)
                n += 1
        d += dt.timedelta(days=1)
    print('DONE %d page(s) -> %s' % (n, a.out))


# ========================================================== parse-odds
def parse_500_file(path):
    html = L.read_text(path)
    name = os.path.basename(path)
    m = RE_PID.match(name)
    pid = m.group(1) if m else '?'
    play = L.PLAYS.get(pid, '?')
    out = []
    for seg in RE_TR.split(html)[1:]:
        attrs = dict(RE_ATTR.findall(seg))
        if not attrs.get('fixtureid') and not attrs.get('matchnum'):
            continue
        bets = {}
        for t, v, sp in RE_BET_A.findall(seg):
            bets.setdefault(t, {})[v] = sp
        for v, sp, t in RE_BET_B.findall(seg):
            bets.setdefault(t, {})[v] = sp
        sc = RE_SCORE.search(seg)
        ht = RE_HALF.search(seg)
        out.append({
            'src': name, 'playid': pid, 'play': play,
            'fixture': attrs.get('fixtureid', '') or attrs.get('matchnum', ''),
            'num': attrs.get('matchnum', ''),
            'sale_date': attrs.get('processdate', ''),
            'kick_date': attrs.get('matchdate', ''),
            'time': attrs.get('matchtime', ''),
            'rq': attrs.get('rangqiu', ''),
            'league': attrs.get('simpleleague', ''),
            'home': attrs.get('homesxname', ''),
            'away': attrs.get('awaysxname', ''),
            'isend': attrs.get('isend', ''),
            'buyend': attrs.get('buyendtime', ''),
            'score': sc.group(1) if sc else '',
            'ht': ht.group(1) if ht else '',
            'bets': bets,
        })
    return out


def cmd_parse_odds(a):
    files = sorted(glob.glob(os.path.join(a.dir, 'p*_*.html')))
    rows = []
    for f in files:
        r = parse_500_file(f)
        kinds = sorted({x['play'] for x in r})
        print('%-34s rows=%3d plays=%s' % (os.path.basename(f), len(r), kinds), flush=True)
        rows += r
    out = os.path.join(a.out, 'odds.json')
    _dump(out, rows)
    print('DONE %d rows -> %s' % (len(rows), out))


# ======================================================= fetch-results
OKOOO_COLS = ['no', 'league', 'datetime', 'home', 'away', 'ht', 'ft', 'wdl_res',
              'wdl_odds', 'handicap', 'rq_res', 'rq_odds', 'score_res', 'score_odds',
              'goals_res', 'goals_odds', 'hf_res', 'hf_odds']
RE_ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
RE_TD = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
RE_TAG = re.compile(r'<[^>]+>')
RE_COMMENT = re.compile(r'<!--.*?-->', re.S)
RE_MID = re.compile(r'/soccer/match/(\d+)/')


def parse_okooo_file(path):
    html = L.read_text(path)
    if RE_5DAY.search(html):
        return []
    # 关键：先剥离 HTML 注释，否则被注释的 <td>指数</td> 会造成整行列错位
    html = RE_COMMENT.sub('', html)
    out = []
    for r in RE_ROW.findall(html):
        if '/soccer/match/' not in r:
            continue
        tds = RE_TD.findall(r)
        if len(tds) < 18:
            continue
        cells = [RE_TAG.sub('', t).strip() for t in tds[:18]]
        m = RE_MID.search(r)
        rec = dict(zip(OKOOO_COLS, cells))
        rec['match_id'] = m.group(1) if m else ''
        out.append(rec)
    return out


def norm_okooo(rec, src):
    """澳客开奖原始行 -> 统一结果行。"""
    m = re.match(r'(\d{2})-(\d{2})\s+(\d{2}):(\d{2})', rec.get('datetime', ''))
    date = '2026-%s-%s' % (m.group(1), m.group(2)) if m else ''
    time = '%s:%s' % (m.group(3), m.group(4)) if m else ''
    if not L.parse_ft(rec.get('ft')):
        return None
    return dict(mid=str(rec.get('match_id', '')), no=rec.get('no', ''),
                league=rec.get('league', ''), date=date, time=time,
                home=rec.get('home', ''), away=rec.get('away', ''),
                ht=rec.get('ht', ''), ft=rec.get('ft', ''),
                handicap=rec.get('handicap', ''),
                w_res=rec.get('wdl_res', ''), w_sp=rec.get('wdl_odds', ''),
                rq_res=rec.get('rq_res', ''), rq_sp=rec.get('rq_odds', ''),
                score_res=rec.get('score_res', ''), score_sp=rec.get('score_odds', ''),
                goals_res=rec.get('goals_res', ''), goals_sp=rec.get('goals_odds', ''),
                htft_res=rec.get('hf_res', ''), htft_sp=rec.get('hf_odds', ''),
                src=src)


def cmd_fetch_results(a):
    os.makedirs(a.out, exist_ok=True)
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    db, blocks = {}, 0
    d = start
    while d <= end and blocks < a.max_blocks:
        b_end = min(d + dt.timedelta(days=4), end)
        code, size = L.fetch_okooo_range(d.isoformat(), b_end.isoformat(), a.out, a.force)
        path = os.path.join(a.out, 'kj_%s_%s.html' % (d.isoformat(), b_end.isoformat()))
        recs = parse_okooo_file(path) if os.path.exists(path) else []
        for r in recs:
            nrm = norm_okooo(r, 'okooo')
            if nrm:
                db[nrm['mid']] = nrm
        wk = sum(1 for r in db.values() if r['no'][:2] in ('周六', '周日'))
        print('%s~%s %s parsed=%d uniq=%d (周末=%d)' % (d, b_end, code, len(recs), len(db), wk),
              flush=True)
        blocks += 1
        d = b_end + dt.timedelta(days=1)
    out = os.path.join(a.out, 'results.json')
    _dump(out, list(db.values()))
    print('DONE %d results -> %s' % (len(db), out))


# ================================================================ build
def norm_legacy(rec, src):
    """兼容三类赛果文件：
    schema B = 澳客开奖原始行（含 datetime）
    schema C = 已归一化行（含 w_res / rq_res / score_res / htft_res）
    schema A = 旧自建行（含 wdl / wdl_sp / ah_wdl / total_goals / htft）
    """
    if 'datetime' in rec:
        return norm_okooo(rec, src)
    if not L.parse_ft(rec.get('ft')):
        return None
    if any(k in rec for k in ('w_res', 'rq_res', 'score_res', 'htft_res')):
        d = dict(rec)
        d['src'] = rec.get('src') or src
        d['mid'] = str(rec.get('mid', '') or rec.get('match_id', ''))
        return d
    return dict(mid=str(rec.get('match_id', '')), no=rec.get('no', ''),
                league=rec.get('league', ''), date=rec.get('date', ''),
                time=rec.get('time', ''), home=rec.get('home', ''),
                away=rec.get('away', ''), ht=rec.get('ht', ''), ft=rec.get('ft', ''),
                handicap=rec.get('handicap', ''),
                w_res=rec.get('wdl', ''), w_sp=rec.get('wdl_sp', ''),
                rq_res=rec.get('ah_wdl', ''), rq_sp=rec.get('ah_sp', ''),
                score_res=rec.get('score', ''), score_sp=rec.get('score_sp', ''),
                goals_res=rec.get('total_goals', ''), goals_sp=rec.get('total_sp', ''),
                htft_res=rec.get('htft', ''), htft_sp=rec.get('htft_sp', ''),
                src=src)


def _ne(v):
    return v not in (None, '', '-', '0.000')


def enrich(r):
    """结果行 -> 派生字段（胜平负/总进球/比分档/半全场档/让球数）。"""
    sc = L.parse_ft(r['ft'])
    if not sc:
        return None
    h, a = sc
    r['gh'], r['ga'] = h, a
    r['w_actual'] = L.res(h, a)
    r['goals'] = h + a
    r['goals_cat'] = L.goals_cat(h + a)
    r['score_cat'] = L.bf_cat(h, a)
    hts = L.parse_ft(r.get('ht'))
    r['ht_w'] = L.res(*hts) if hts else ''
    r['htft_cat'] = L.htft_cat(hts, sc)
    r['rq'] = int(r['handicap']) if re.match(r'^[+-]?\d+$', str(r['handicap'])) else None
    return r


def cmd_build(a):
    db = {}
    for path, src in [(a.results, 'okooo')] + [(p, os.path.basename(p)) for p in (a.extra or [])]:
        if not path or not os.path.exists(path):
            continue
        for rec in json.load(open(path, encoding='utf-8')):
            nrm = norm_legacy(rec, src)
            if not nrm:
                continue
            old = db.get(nrm['mid'])
            if old:
                for k, v in nrm.items():
                    if _ne(v) or not _ne(old.get(k)):
                        old[k] = v
            else:
                db[nrm['mid']] = nrm

    rows = [x for x in (enrich(dict(r)) for r in db.values()) if x]
    if a.days != 'all':
        rows = [r for r in rows if L.num_kind(r['no']) == a.days]
    if a.before:
        rows = [r for r in rows if r['date'] < a.before]
    rows.sort(key=lambda r: (r['date'], r['time']), reverse=True)
    rows = rows[:a.limit]

    # 并入 500 终盘赔率（键 = (开球日, 期号)）
    odds_path = os.path.join(a.data, 'odds.json')
    cov = Counter()
    if os.path.exists(odds_path):
        idx = {}
        for r in json.load(open(odds_path, encoding='utf-8')):
            idx.setdefault(r['play'], {})[(r['kick_date'], r['num'])] = r
        for r in rows:
            o, key = {}, (r['date'], r['no'])
            for play, outk in [('nspf', 'nspf'), ('bf', 'bf'), ('jqs', 'jqs'), ('bqc', 'bqc')]:
                rec = idx.get(play, {}).get(key)
                if rec:
                    o[outk] = rec['bets'].get(outk, {})
                    if play == 'nspf':
                        o['spf'] = rec['bets'].get('spf', {})
                        o['score500'] = rec.get('score', '')
            r['o500'] = o
            for k in ('nspf', 'bf', 'jqs', 'bqc', 'spf'):
                if o.get(k):
                    cov[k] += 1
    suffix = '_odds' if cov else ''
    out = os.path.join(a.out, 'master%s.json' % suffix)
    _dump(out, rows)

    print('全量 =', len(rows), ' 日期区间:', rows[-1]['date'], '~', rows[0]['date'])
    print('期号前缀:', dict(Counter(r['no'][:2] for r in rows)))
    print('让球盘:', dict(Counter(r['handicap'] for r in rows)))
    print('500 终盘覆盖:', dict(cov) if cov else '(无 odds.json，仅让球盘可用)')
    print('DONE ->', out)


# ============================================================= backtest
BF_WIN = ['1:0', '2:0', '2:1', '3:0', '3:1', '3:2', '4:0', '4:1', '4:2',
          '5:0', '5:1', '5:2', '胜其他']
BF_DRAW = ['0:0', '1:1', '2:2', '3:3', '平其他']
BF_LOSE = ['0:1', '0:2', '1:2', '0:3', '1:3', '2:3', '0:4', '1:4', '2:4',
           '0:5', '1:5', '2:5', '负其他']
BANDS = [(['2', '3'], '2-3球'), (['1', '2', '3'], '1-3球'), (['2', '3', '4'], '2-4球'),
         (['1', '2'], '1-2球'), (['0', '1'], '0-1球')]


def cmd_backtest(a):
    rows = json.load(open(a.dataset, encoding='utf-8'))
    n = len(rows)
    preds = []
    for r in rows:
        p = dict(r)
        rq = r['rq']
        p['f1'] = '3' if (rq is None or rq <= -1) else '0'      # 盘口倾斜法
        p['f2_band'] = ['2', '3']
        p['f5'] = '3-3' if (rq is None or rq <= -1) else '0-0'
        o = r.get('o500') or {}
        fav = None
        if o.get('nspf'):
            pr, ov = L.implied(o['nspf'])
            p['o1'] = L.pick_min(o['nspf'])
            p['o1_p'] = round(pr.get(p['o1'], 0), 4)
            p['o1_overround'] = round(ov, 4)
            fav = p['o1']
        if o.get('jqs'):
            p['o2_top1'] = L.pick_min(o['jqs'])
            p['o2_top2'] = L.pick_top(o['jqs'], 2)
        if o.get('bf'):
            bf, hot = o['bf'], L.pick_min(o['bf'])
            side = BF_WIN if fav == '3' else (BF_LOSE if fav == '0' else BF_DRAW)
            sub = {k: bf[k] for k in side if k in bf}
            p['o3_dir_top1'] = L.pick_min(sub) if sub else hot
            p['o3_hot'] = hot
            p['o3_picks'] = list(dict.fromkeys([p['o3_dir_top1'], hot]))
            p['o3_top3'] = L.pick_top(bf, 3)
        if o.get('bqc'):
            p['o5_top1'] = L.pick_min(o['bqc'])
            p['o5_top2'] = L.pick_top(o['bqc'], 2)
            p['o5_top3'] = L.pick_top(o['bqc'], 3)
        aw = r['w_actual']
        p['hit_f1'] = (p['f1'] == aw)
        p['hit_base_home'] = (aw == '3')
        p['hit_f2_band'] = (r['goals_cat'] in p['f2_band'])
        p['hit_f5'] = (p['f5'] == r['htft_cat']) if r['htft_cat'] else None
        if o.get('nspf'):
            p['hit_o1'] = (p['o1'] == aw)
        if o.get('jqs'):
            p['hit_o2_1'] = (r['goals_cat'] == p['o2_top1'])
            p['hit_o2_2'] = (r['goals_cat'] in p['o2_top2'])
            # 深盘更偏大球：|让球|>=2 用 3-5 球，浅盘 2-3 球
            p['hit_o2_deep'] = (r['goals_cat'] in (['3', '4', '5'] if (rq is not None and abs(rq) >= 2)
                                                   else ['2', '3']))
        if o.get('bf'):
            p['hit_o3_1'] = (r['score_cat'] == p['o3_dir_top1'])
            p['hit_o3_2'] = (r['score_cat'] in p['o3_picks'])
            p['hit_o3_3'] = (r['score_cat'] in p['o3_top3'])
        if o.get('bqc'):
            p['hit_o5_1'] = (r['htft_cat'] == p['o5_top1'])
            p['hit_o5_2'] = (r['htft_cat'] in p['o5_top2'])
            p['hit_o5_3'] = (r['htft_cat'] in p['o5_top3'])
        for band, lab in BANDS:
            p['hit_band_' + lab] = (r['goals_cat'] in band)
        preds.append(p)

    def rate(key, sub=None):
        v = [p[key] for p in (sub or preds) if p.get(key) is not None]
        return sum(1 for x in v if x), len(v)

    O = []
    W = O.append
    W('=' * 78)
    W('足彩回测  样本 %d 场  %s ~ %s' % (n, min(r['date'] for r in rows), max(r['date'] for r in rows)))
    W('500 终盘覆盖: ' + ' '.join('%s=%d' % (k, sum(1 for r in rows if (r.get('o500') or {}).get(k)))
                                  for k in ('nspf', 'bf', 'jqs', 'bqc')))
    W('=' * 78)

    W('\n【一、玩法命中率】')
    items = [('F1 胜平负 盘口倾斜法', 'hit_f1'), ('  └基准 始终主胜', 'hit_base_home'),
             ('F2 进球 区间 2-3球', 'hit_f2_band'),
             ('F5 半全场 条件版(主让→胜胜)', 'hit_f5'),
             ('O1 胜平负 最低赔率法', 'hit_o1'),
             ('O2 进球 隐含Top1', 'hit_o2_1'), ('O2 进球 隐含Top2', 'hit_o2_2'),
             ('O2 进球 深盘3-5/浅盘2-3', 'hit_o2_deep'),
             ('O3 比分 方向内隐含Top1', 'hit_o3_1'), ('O3 比分 双选(dir+最热)', 'hit_o3_2'),
             ('O3 比分 市场最热Top3', 'hit_o3_3'),
             ('O5 半全场 隐含Top1', 'hit_o5_1'), ('O5 半全场 隐含Top2', 'hit_o5_2'),
             ('O5 半全场 隐含Top3', 'hit_o5_3')]
    for lab, k in items:
        h, t = rate(k)
        W('  %-34s %3d/%-3d = %s' % (lab, h, t, ('%.1f%%' % (100 * h / t)) if t else '-'))
    for band, lab in BANDS:
        h, t = rate('hit_band_' + lab)
        W('  %-34s %3d/%-3d = %.1f%%' % ('  区间 ' + lab, h, t, 100 * h / t if t else 0))

    sub = [p for p in preds if (p.get('o500') or {}).get('nspf')]
    if sub:
        a1, b1 = sum(1 for p in sub if p['hit_f1']), sum(1 for p in sub if p['hit_o1'])
        W('\n  同子集(n=%d)对照: 盘口倾斜法 %.1f%%  vs  最低赔率法 %.1f%%'
          % (len(sub), 100 * a1 / len(sub), 100 * b1 / len(sub)))

    W('\n【二、重点规则子集】')
    cold = [r for r in rows if _ne(r['w_sp']) and float(r['w_sp']) >= 3.5]
    W('  爆冷(胜方SP>=3.5): %d/%d = %.1f%%' % (len(cold), n, 100 * len(cold) / n))
    if cold:
        W('    方向: ' + ', '.join('%s%d' % (L.HMAP[k], v)
                                   for k, v in Counter(r['w_actual'] for r in cold).most_common()))
        W('    F1 在爆冷场次命中: %d/%d' % (sum(1 for p in preds if p in cold and p['hit_f1']), len(cold)))
    hisp = [r for r in rows if _ne(r['score_sp']) and float(r['score_sp']) >= 15]
    W('  高赔比分(SP>=15): %d/%d = %.1f%%' % (len(hisp), n, 100 * len(hisp) / n))
    if hisp:
        big = sum(1 for r in hisp if r['goals'] >= 4)
        small = [r for r in rows if r['goals'] <= 3]
        sbig = sum(1 for r in small if _ne(r['score_sp']) and float(r['score_sp']) >= 15)
        W('    其中大球(>=4): %d/%d = %.1f%%' % (big, len(hisp), 100 * big / len(hisp)))
        W('    小球场次出高赔比分: %d/%d = %.1f%%'
          % (sbig, len(small), 100 * sbig / len(small) if small else 0))
    big4 = [r for r in rows if r['goals'] >= 4]
    W('  大球(>=4球): %d/%d = %.1f%%' % (len(big4), n, 100 * len(big4) / n))
    rev = [r for r in rows if r['htft_cat'] in L.HTFT_REV]
    W('  半全场反转(胜平/负平/胜负/负胜): %d/%d = %.1f%%' % (len(rev), n, 100 * len(rev) / n))
    if rev:
        W('    分布: ' + ', '.join('%s%s×%d' % (k, L.HTFT_LAB.get(k, ''), v)
                                   for k, v in Counter(r['htft_cat'] for r in rev).most_common()))
    htp = [r for r in rows if r['ht_w'] == '1']
    if htp:
        W('  半场平: %d/%d = %.1f%%  (其中全场分胜负 %d/%d = %.1f%%)'
          % (len(htp), n, 100 * len(htp) / n,
             sum(1 for r in htp if r['w_actual'] != '1'), len(htp),
             100 * sum(1 for r in htp if r['w_actual'] != '1') / len(htp)))

    W('\n【三、环境结构】')
    W('  胜平负: ' + ', '.join('%s%d(%.1f%%)' % (L.HMAP[k], v, 100 * v / n)
                               for k, v in Counter(r['w_actual'] for r in rows).most_common()))
    W('  让球盘: ' + str(dict(Counter(r['handicap'] for r in rows).most_common())))
    W('  总进球: ' + str(dict(sorted(Counter(r['goals_cat'] for r in rows).items(),
                                     key=lambda x: (len(x[0]), x[0])))))
    W('  比分Top5: ' + ', '.join('%s×%d' % kv for kv in Counter(r['score_cat'] for r in rows).most_common(5)))
    ov = [r['o1_overround'] for p in preds for r in [p] if p.get('o1_overround')]
    if ov:
        W('  终盘 overround 均值 %.4f（抽水约 %.1f%%）' % (sum(ov) / len(ov), 100 * (sum(ov) / len(ov) - 1)))

    W('\n【四、市场热门分档】')
    b = defaultdict(lambda: [0, 0])
    for p in preds:
        if not (p.get('o500') or {}).get('nspf'):
            continue
        pp = p['o1_p']
        k = '<50%' if pp < .5 else ('50-60%' if pp < .6 else ('60-70%' if pp < .7 else '>=70%'))
        b[k][1] += 1
        b[k][0] += 1 if p['hit_o1'] else 0
    for k in ['<50%', '50-60%', '60-70%', '>=70%']:
        h, t = b[k]
        if t:
            W('  %-7s %d/%d = %.1f%%' % (k, h, t, 100 * h / t))

    W('\n【五、ROI（1 单位均注，终盘子集）】')
    def roi_of(cond, pick, ok, hk):
        bets = []
        for p in preds:
            if not cond(p):
                continue
            o = (p.get('o500') or {}).get(ok)
            if not o or not p.get(pick):
                continue
            v = o.get(p[pick])
            if L.fnum(v):
                bets.append((v, bool(p[hk])))
        return L.roi_stats(bets)
    specs = [('F1 盘口倾斜法(全)', lambda p: True, 'f1', 'nspf', 'hit_f1'),
             ('主让场次 单押主胜', lambda p: p['rq'] is not None and p['rq'] <= -1, 'f1', 'nspf', 'hit_f1'),
             ('客让场次 单押客胜', lambda p: p['rq'] is not None and p['rq'] >= 1, 'f1', 'nspf', 'hit_f1'),
             ('深盘(|rq|>=2) 跟盘', lambda p: p['rq'] is not None and abs(p['rq']) >= 2, 'f1', 'nspf', 'hit_f1'),
             ('O1 最低赔率法', lambda p: True, 'o1', 'nspf', 'hit_o1'),
             ('O2 进球 隐含Top1', lambda p: True, 'o2_top1', 'jqs', 'hit_o2_1')]
    for lab, cond, pk, ok, hk in specs:
        r = roi_of(cond, pk, ok, hk)
        if r:
            W('  %-20s n=%-4d ROI=%7.2f%%  t=%6.2f  95%%CI=%s' % (lab, r['n'], r['roi'], r['t'], r['ci']))

    W('\n【六、分让球子组】')
    for lab, cond in [('主让(<=-1)', lambda r: r['rq'] is not None and r['rq'] <= -1),
                      ('客让(>=+1)', lambda r: r['rq'] is not None and r['rq'] >= 1),
                      ('深盘(|rq|>=2)', lambda r: r['rq'] is not None and abs(r['rq']) >= 2)]:
        g = [p for p in preds if cond(p)]
        if not g:
            continue
        W('  %-14s n=%-4d 主胜率=%.1f%%  F1=%.1f%%  平均进球=%.2f'
          % (lab, len(g), 100 * sum(1 for p in g if p['w_actual'] == '3') / len(g),
             100 * sum(1 for p in g if p['hit_f1']) / len(g),
             sum(p['goals'] for p in g) / len(g)))

    txt = '\n'.join(O)
    _w(a.out_prefix + '_report.txt', txt)
    _dump(a.out_prefix + '.json', preds)
    print(txt)


# ============================================================== analyze
def cmd_analyze(a):
    rows = json.load(open(os.path.join(a.data, 'odds.json'), encoding='utf-8'))
    day = [r for r in rows if r['sale_date'] == a.sale_date]
    by_fix = {}
    for r in day:
        f = by_fix.setdefault(r['fixture'] or (r['kick_date'] + r['num']), dict(r))
        f['bets'] = f.get('bets', {})
        for t, d in r['bets'].items():
            f.setdefault('plays', {})[t] = d
    merged = []
    for f in by_fix.values():
        plays = f.get('plays', {})
        merged.append({'num': f['num'], 'sale_date': f['sale_date'],
                       'kick': '%s %s' % (f['kick_date'], f['time']),
                       'league': f['league'], 'home': f['home'], 'away': f['away'],
                       'rq': f['rq'], 'buyend': f['buyend'],
                       'nspf': plays.get('nspf', {}), 'spf': plays.get('spf', {}),
                       'bf': plays.get('bf', {}), 'jqs': plays.get('jqs', {}),
                       'bqc': plays.get('bqc', {})})
    merged.sort(key=lambda x: x['num'])
    _dump(os.path.join(a.out, 'today_merged.json'), merged)

    out = []
    for r in merged:
        p, ov = L.implied(r['nspf'])
        psp, _ = L.implied(r['spf'])
        jq, _ = L.implied(r['jqs'])
        bf, _ = L.implied(r['bf'])
        bq, _ = L.implied(r['bqc'])
        rev = {L.HTFT_LAB[k]: v for k, v in bq.items() if k in L.HTFT_REV}
        hd = sum(v for k, v in bq.items() if k in L.HTFT_HALF_DRAW)
        out.append({
            'num': r['num'], 'kick': r['kick'], 'league': r['league'],
            'home': r['home'], 'away': r['away'], 'rq': r['rq'],
            'nspf': r['nspf'], 'p': {k: round(v, 4) for k, v in p.items()},
            'overround': round(ov, 4),
            'p_fav': round(max(p.values()), 4) if p else None,
            'fav': max(p.items(), key=lambda x: x[1])[0] if p else None,
            'min_odds': round(min((L.fnum(v) or 9) for v in r['nspf'].values()), 3) if r['nspf'] else None,
            'rq_p': {k: round(v, 4) for k, v in psp.items()},
            'jqs_p': {k: round(v, 4) for k, v in sorted(jq.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 99)},
            'jq_top': L.pick_top(r['jqs'], 3) if r['jqs'] else [],
            'p_ge4': round(sum(v for k, v in jq.items() if k.isdigit() and int(k) >= 4), 4),
            'p_1_3': round(sum(v for k, v in jq.items() if k.isdigit() and 1 <= int(k) <= 3), 4),
            'eg': round(sum(int(k) * v for k, v in jq.items() if k.isdigit()), 2),
            'bf_top5': [(k, round(v, 4), r['bf'].get(k)) for k, v in
                        sorted(bf.items(), key=lambda x: -x[1])[:5]],
            'bf_hi': sorted([(k, round(v, 4), r['bf'].get(k)) for k, v in bf.items()
                             if L.fnum(r['bf'].get(k)) and float(r['bf'][k]) >= 15],
                            key=lambda x: -x[1])[:5],
            'bqc_top3': [(L.HTFT_LAB.get(k, k), round(v, 4), r['bqc'].get(k)) for k, v in
                         sorted(bq.items(), key=lambda x: -x[1])[:3]],
            'bqc_rev': {k: round(v, 4) for k, v in rev.items()},
            'p_halfdraw': round(hd, 4),
        })
    _dump(os.path.join(a.out, 'today_analysis.json'), out)

    T = []
    W = T.append
    W('=== 当日 %s 共 %d 场 ===' % (a.sale_date, len(out)))
    W('\n[A. 大球候选（P(>=4) 降序）]')
    for x in sorted(out, key=lambda y: -y['p_ge4']):
        W('  %-8s %-6s vs %-6s 让%-3s P(>=4)=%.3f 期望=%.2f P(1-3)=%.3f' %
          (x['num'], x['home'], x['away'], x['rq'], x['p_ge4'], x['eg'], x['p_1_3']))
    W('\n[B. 胜平负/赔率结构（按热门概率降序）]')
    for x in sorted(out, key=lambda y: -(y['p_fav'] or 0)):
        W('  %-8s %-6s vs %-6s 让%-3s 不让%s  p=%s 最低赔=%s 热门=%s(%.3f) over=%.4f' %
          (x['num'], x['home'], x['away'], x['rq'], x['nspf'], x['p'],
           x['min_odds'], L.HMAP.get(x['fav'], x['fav']), x['p_fav'] or 0, x['overround']))
    W('\n[C. 高赔比分候选（SP>=15）]')
    for x in out:
        if x['bf_hi']:
            W('  %-8s %-6s vs %-6s  P(>=4)=%.3f  ' % (x['num'], x['home'], x['away'], x['p_ge4']) +
              ' | '.join('%s@%s(%.2f%%)' % (k, sp, v * 100) for k, v, sp in x['bf_hi'][:3]))
    W('\n[D. 半全场候选（隐含排序 + 反转/半场平）]')
    for x in out:
        t = x['bqc_top3']
        W('  %-8s %-6s vs %-6s  首选=%s@%s(%.1f%%) 次选=%s  半场平合计=%.1f%%  反转=%s' %
          (x['num'], x['home'], x['away'], t[0][0], t[0][2], t[0][1] * 100,
           t[1][0] if len(t) > 1 else '-', x['p_halfdraw'] * 100,
           {k: '%.1f%%' % (v * 100) for k, v in x['bqc_rev'].items()}))
    W('\n[E. 爆冷候选（热门概率降序 = 越前越"稳"，越后越可能爆冷）]')
    for x in sorted(out, key=lambda y: -(y['p_fav'] or 0)):
        W('  %-8s %-6s vs %-6s  热门=%s p=%.3f  弱方不败=%.3f  让球=%s' %
          (x['num'], x['home'], x['away'], L.HMAP.get(x['fav'], x['fav']),
           x['p_fav'] or 0, 1 - (x['p_fav'] or 0), x['rq']))
    txt = '\n'.join(T)
    _w(os.path.join(a.out, 'today_analysis.txt'), txt)
    print(txt)


# ============================================================== compare
def cmd_compare(a):
    res = {}
    for rec in json.load(open(a.results, encoding='utf-8')):
        nrm = norm_legacy(rec, 'results')
        if nrm:
            res[nrm['no']] = nrm
    preds = json.load(open(a.pred, encoding='utf-8'))
    rows = []
    for pr in preds:
        r = res.get(pr['no'])
        if not r:
            print('MISSING', pr['no'])
            continue
        x = dict(pr)
        x['act'] = dict(ft=r['ft'], ht=r['ht'], w=r['w_res'] or L.res(*L.parse_ft(r['ft'])),
                       goals=r['goals_res'], score=r['score_res'], htft=r['htft_res'],
                       w_sp=r['w_sp'], score_sp=r['score_sp'], handicap=r['handicap'])
        f1 = pr.get('f1')
        x['hit1'] = (f1 == x['act']['w']) if f1 in ('3', '1', '0') else None
        sel = pr.get('score')
        x['hit3'] = (x['act']['score'] in sel) if isinstance(sel, list) else \
            ((x['act']['score'] == sel) if sel else None)
        g = pr.get('goals')
        x['hit2'] = (x['act']['goals'] in g) if isinstance(g, list) else \
            ((x['act']['goals'] == g) if g else None)
        h = pr.get('htft')
        x['hit5'] = (x['act']['htft'] in h) if isinstance(h, list) else \
            ((x['act']['htft'] == h) if h else None)
        rows.append(x)

    def rate(k):
        v = [r[k] for r in rows if r.get(k) is not None]
        return sum(1 for x in v if x), len(v)

    print('=== 逐场 ===')
    for r in rows:
        print('  %-8s %-6s %s-%s %-6s 实际WDL=%s 比分=%s(SP%s) 进球=%s 半全场=%s | F1:%s F2:%s F3:%s F5:%s' %
              (r['no'], r.get('home', ''), r['act']['ft'].split('-')[0], r['act']['ft'].split('-')[1],
               r.get('away', ''), r['act']['w'], r['act']['score'], r['act']['score_sp'],
               r['act']['goals'], r['act']['htft'], r['hit1'], r['hit2'], r['hit3'], r['hit5']))
    print('\n=== 命中率 ===')
    for lab, k in [('F1 胜平负', 'hit1'), ('F2 进球数', 'hit2'), ('F3 比分', 'hit3'), ('F5 半全场', 'hit5')]:
        h, n = rate(k)
        print('  %-10s %d/%d = %.1f%%' % (lab, h, n, 100 * h / n if n else 0))
    _dump(a.out + '.json', rows)


# ==================================================================== main
def main():
    ap = argparse.ArgumentParser(prog='wb_football', description='足彩预测大师 · 数据与回测流水线')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('fetch-odds', help='抓 trade.500.com 四玩法页面')
    p.add_argument('--playid', nargs='*', default=None, help='354/271/270/272，默认全部')
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--days', choices=['all', 'weekday', 'weekend'], default='all')
    p.add_argument('--out', default='.')
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_fetch_odds)

    p = sub.add_parser('parse-odds', help='解析四玩法页面 -> odds.json')
    p.add_argument('--dir', default='.')
    p.add_argument('--out', default='.')
    p.set_defaults(func=cmd_parse_odds)

    p = sub.add_parser('fetch-results', help='抓澳客竞彩开奖 -> results.json')
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--out', default='.')
    p.add_argument('--max-blocks', type=int, default=40)
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_fetch_results)

    p = sub.add_parser('build', help='合并赛果 + 500 终盘 -> master_odds.json')
    p.add_argument('--results', required=True)
    p.add_argument('--extra', nargs='*', default=None, help='额外旧格式赛果文件')
    p.add_argument('--data', default='.', help='odds.json 所在目录')
    p.add_argument('--days', choices=['all', 'weekday', 'weekend'], default='all')
    p.add_argument('--before', default=None, help='仅保留该日期之前（不含）')
    p.add_argument('--limit', type=int, default=300)
    p.add_argument('--out', default='.')
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('backtest', help='回测')
    p.add_argument('--dataset', required=True)
    p.add_argument('--out-prefix', default='backtest')
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser('analyze', help='当日赔率结构 + 重点场次候选')
    p.add_argument('--data', default='.')
    p.add_argument('--sale-date', required=True)
    p.add_argument('--out', default='.')
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser('compare', help='预测 vs 赛果闭环比对')
    p.add_argument('--pred', required=True)
    p.add_argument('--results', required=True)
    p.add_argument('--out', default='compare')
    p.set_defaults(func=cmd_compare)

    a = ap.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
