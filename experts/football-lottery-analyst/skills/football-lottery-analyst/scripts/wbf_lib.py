# -*- coding: utf-8 -*-
"""足彩预测大师 · 共用库

只放跨阶段复用的常量与纯函数：编解码、curl 抓取、隐含概率、命中/ROI/显著性、
比分与半全场归一化。所有逻辑均来自已在 300 场工作日 + 200 场周末样本上验证过的实现。
"""
import math
import os
import re
import subprocess

# ---------------------------------------------------------------- 常量
UA_500 = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
UA_OKOOO = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# trade.500.com 四个玩法（URL 参数 playid -> 玩法键）
PLAYS = {'354': 'nspf', '271': 'bf', '270': 'jqs', '272': 'bqc'}
PLAY_NAMES = {'nspf': '胜平负(不让球)', 'bf': '比分', 'jqs': '进球数',
              'bqc': '半全场', 'spf': '让球胜平负'}

URL_500 = 'https://trade.500.com/jczq/?playid={pid}&g=2&date={date}'
URL_OKOOO_KJ = ('https://www.okooo.com/jingcai/kaijiang/?LotteryType=SportteryNWDL'
                '&StartDate={start}&EndDate={end}')

# 竞彩比分 31 档里的 26 个固定档；其余归入「胜/平/负其他」（SP >= 15 的高赔类）
BF_OPTS = {'1:0', '2:0', '2:1', '3:0', '3:1', '3:2', '4:0', '4:1', '4:2',
           '5:0', '5:1', '5:2', '0:0', '1:1', '2:2', '3:3', '0:1', '0:2',
           '1:2', '0:3', '1:3', '2:3', '0:4', '1:4', '2:4', '0:5', '1:5', '2:5'}

HTFT_LAB = {'3-3': '胜胜', '3-1': '胜平', '3-0': '胜负', '1-3': '平胜', '1-1': '平平',
            '1-0': '平负', '0-3': '负胜', '0-1': '负平', '0-0': '负负'}
HTFT_REV = {'3-1', '0-1', '3-0', '0-3'}          # 胜平/负平/胜负/负胜 反转组
HTFT_HALF_DRAW = {'1-3', '1-1', '1-0'}           # 半场平（平胜/平平/平负）

HMAP = {'3': '主胜', '1': '平局', '0': '客胜'}
DIR_NAME = {'3': '主胜', '1': '平局', '0': '客胜'}
DIRS = ['主胜', '平局', '客胜']
WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

# ------------------------------------------------------------------ 编解码


def decode(raw):
    """字节流 -> str。500 站为 gb18030，澳客为 gbk/gb18030。"""
    if isinstance(raw, str):
        return raw
    for enc in ('gb18030', 'gbk', 'utf-8'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('gb18030', 'replace')


def read_text(path):
    return decode(open(path, 'rb').read())


# ------------------------------------------------------------------ 抓取


def curl(url, out_path=None, referer=None, ua=UA_500, timeout=40):
    """用系统 curl 抓取。返回 (http_code, size)。out_path 为空则丢弃内容。"""
    cmd = ['curl', '-s', '-L', '-w', '%{http_code} %{size_download}',
           '-A', ua, '--max-time', str(timeout)]
    if referer:
        cmd += ['-e', referer]
    cmd += ['-o', out_path if out_path else os.devnull, url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
        return (r.stdout or '').strip()
    except Exception as e:                                    # noqa: BLE001
        return 'ERR %s' % e


def fetch_500_day(pid, date, out_dir, force=False):
    """抓某玩法某日的历史/当日页 -> out_dir/p{pid}_{date}.html（命中缓存则跳过）"""
    url = URL_500.format(pid=pid, date=date)
    if pid == '354':
        url += '&vtype=nspf'
    path = os.path.join(out_dir, 'p%s_%s.html' % (pid, date))
    if not force and os.path.exists(path) and os.path.getsize(path) > 5000:
        return 'cached', os.path.getsize(path)
    code = curl(url, path, referer='https://trade.500.com/jczq/')
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return code, size


def fetch_okooo_range(start, end, out_dir, force=False):
    """抓澳客竞彩开奖页（单块 <=5 天）-> out_dir/kj_{start}_{end}.html"""
    url = URL_OKOOO_KJ.format(start=start, end=end)
    path = os.path.join(out_dir, 'kj_%s_%s.html' % (start, end))
    if not force and os.path.exists(path) and os.path.getsize(path) > 20000:
        return 'cached', os.path.getsize(path)
    code = curl(url, path, referer='https://www.okooo.com/jingcai/kaijiang/',
                ua=UA_OKOOO)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return code, size


# -------------------------------------------------------------- 数值工具


def fnum(v):
    """安全转 float，失败返回 None。"""
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def implied(odds):
    """赔率 dict -> (归一化隐含概率 dict, overround)。

    竞彩终盘 overround 实测约 1.125~1.130（抽水 ~12.9%）。
    """
    inv = {}
    for k, v in odds.items():
        f = fnum(v)
        if f:
            inv[k] = 1.0 / f
    s = sum(inv.values())
    if not s:
        return {}, 0.0
    return {k: v / s for k, v in inv.items()}, s


def pick_min(odds):
    """最低赔率对应的键（= 市场热门）。"""
    items = [(k, fnum(v)) for k, v in odds.items()]
    items = [(k, v) for k, v in items if v]
    return min(items, key=lambda kv: kv[1])[0] if items else None


def pick_top(odds, n=1):
    """按赔率升序取前 n 个键（越低越热）。"""
    items = [(k, fnum(v)) for k, v in odds.items()]
    items = [(k, v) for k, v in items if v]
    items.sort(key=lambda kv: kv[1])
    return [k for k, _ in items[:n]]


def roi_stats(bets):
    """bets = [(odds, hit_bool), ...] -> dict(n, roi%, t, ci95)。1 单位均注。"""
    n = len(bets)
    if n == 0:
        return None
    rets = [((float(o) - 1.0) if h else -1.0) for o, h in bets]
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1) if n > 1 else 0.0
    se = math.sqrt(var / n)
    return dict(n=n, roi=round(100 * mean, 2),
                t=round(mean / se, 2) if se else 0.0,
                ci=(round(100 * (mean - 1.96 * se), 2),
                    round(100 * (mean + 1.96 * se), 2)) if se else (0.0, 0.0))


def ztest(h1, n1, h2, n2):
    """两比例 z 检验，返回 (z, p_two_sided)。"""
    if not n1 or not n2:
        return 0.0, 1.0
    p1, p2 = h1 / n1, h2 / n2
    p = (h1 + h2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if not se:
        return 0.0, 1.0
    z = (p1 - p2) / se
    pv = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, pv


def binom_ge(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def binom_le(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))


# ---------------------------------------------------------- 赛果归一化


def parse_ft(s):
    """"2-1" / "2:1" -> (2, 1)；失败返回 None。"""
    m = re.match(r'^\s*(\d+)\s*[-:]\s*(\d+)\s*$', s or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def res(h, a):
    """胜平负：'3' 主胜 / '1' 平 / '0' 客胜。"""
    return '3' if h > a else ('1' if h == a else '0')


def bf_cat(h, a):
    """比分归一化到竞彩 31 档；超出固定档归入 胜其他/平其他/负其他。"""
    s = '%d:%d' % (h, a)
    if s in BF_OPTS:
        return s
    return '胜其他' if h > a else ('平其他' if h == a else '负其他')


def goals_cat(total):
    return str(total) if total <= 6 else '7+'


def htft_cat(ht, ft):
    """(半场, 全场) 比分 -> '3-1' 形式；缺半场返回 ''。"""
    if not ht or not ft:
        return ''
    return '%s-%s' % (res(*ht), res(*ft))


def num_kind(num):
    """竞彩期号 -> 'weekend' / 'weekday'。周六/周日 为 weekend。"""
    if not num:
        return 'weekday'
    return 'weekend' if num[:2] in ('周六', '周日') else 'weekday'


def day_index(num):
    """竞彩期号 -> 0..6（周一=0）；无法识别返回 None。"""
    pre = (num or '')[:2]
    return WEEKDAY_CN.index(pre) if pre in WEEKDAY_CN else None
