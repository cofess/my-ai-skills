# 数据源契约与陷阱手册

> 本手册记录的都是**实测踩过的坑**。改脚本前先读，能省掉大量返工。

## 1. trade.500.com（竞彩投注页）

### 1.1 URL 与文件名

```
https://trade.500.com/jczq/?playid={pid}&g=2&date={YYYY-MM-DD}
# playid=354 时需额外加 &vtype=nspf
```

| playid | 玩法 | 页面内的 data-type |
|---|---|---|
| 354 | 胜平负（不让球 **+ 让球**） | `nspf`、`spf` |
| 271 | 比分（31 档） | `bf` |
| 270 | 进球数（0–7） | `jqs` |
| 272 | 半全场（9 组合） | `bqc` |

- `date` 参数是**开球日期**，不是销售日期。凌晨开球的场次其 `date` 会比期号日早一天。
- 必需 Header：`User-Agent`（桌面 Chrome）+ `Referer: https://trade.500.com/jczq/`。缺 Referer 会被拒。
- 页面编码 **gb18030**（不是 UTF-8）。用 `wbf_lib.decode()` 兜底。

### 1.2 行结构

```html
<tr class="bet-tb-tr" data-fixtureid="..." data-matchnum="周四012"
    data-matchdate="2026-10-16" data-matchtime="03:00" data-rangqiu="-2"
    data-simpleleague="欧冠" data-homesxname="本菲卡" data-awaysxname="凯尔特人"
    data-isend="1" data-buyendtime="2026-10-15 23:00" data-processdate="2026-10-15">
  ... <span class="score">2:1</span> ...
  <p data-type="nspf" data-value="3" data-sp="1.12">
```

关键点（全部踩过）：

1. **切行必须用 `re.split(r'<tr class="bet-tb-tr')`**，不要按 `</tr>` 截断——**比分行内含嵌套 `<table>`**（平局/负的其他选项），按 `</tr>` 切会把赔率截没。
   `re.split` 在每个行首处切开，每段天然被**下一行**界定，因此不会把后续行的赔率混进来。
2. **赔率元素有两种属性顺序**：`data-type → data-value → data-sp` 与 `data-value → data-sp → data-type`。
   两种正则都要跑（`RE_BET_A` / `RE_BET_B`），否则会漏一半玩法。
3. `data-value` 是选项、`data-sp` 是赔率，**不要弄反**（曾把 sp 当 value 导致整表错位）。
4. **比分档共 31 项**：26 个固定比分 + `胜其他 / 平其他 / 负其他`。后三者是 **SP ≥ 15 的高赔类**，高频出现（工作日 36% 的场次命中高赔比分）。
5. 让球数只在 `data-rangqiu`；**主让为负数**（`-2` = 主让两球），客让为正。`0` 为平手。

### 1.3 两个致命边界（务必记住）

**(a) 历史页只回溯约 30 天。**
早于窗口的日期返回 **302 降级到当日页**（拿回来的是当天数据，静默错位！）。判据：抓取后检查页面里的 `data-matchdate` 是否落在请求日期上；不一致即视为不可用。
⇒ 因此"回测 300 场"里只有约 150–165 场带终盘赔率，**其余只能用澳客的让球盘跑盘口法**。

**(b) 胜平负（不让球）页系统性缺失 |让球| ≥ 2 的场次。**

| 样本 | 全部深盘（\|让球\|≥2） | 能取到 nspf | 覆盖率 |
|---|---|---|---|
| 周末 200 | 20 | 1 | 5.0% |
| 工作日 300 | 44 | 8 | 18.2% |

窗口内缺口中，周末 8/8、工作日 19/25 都是深盘。
⇒ **深盘的赔率/ROI 无法用 500 验**（合并可测仅 n=9）；深盘只能靠**澳客的让球 SP**验证方向命中率。**禁止对深盘报 ROI。**

### 1.4 被验证码拦截的页面

`zx.500.com/jczq/kaijiang.php`（开奖）、`odds.500.com/fenxi/{shuju,ouzhi,yazhi}-{fid}.shtml`（基本面/欧赔/亚盘）会被腾讯 **EO_Bot** 拦截（返回 <3KB 的挑战页）。

挑战页里有两个变量与一个常量，拼出两枚 cookie：
```
__tst_status = WTKkN + bOYDu + wyeCN   （三个数相加，末尾带 #）
EO_Bot_Ssid  = 挑战脚本里 (t,<9~12位常数>) 的那个常数
```
带这两枚 cookie 再请求一次即可拿到真实页。**但该方案不稳定**（常量位置随混淆版本变化）。默认策略：**改用澳客**，或请用户提供页面文本。若确实要试，参考 `reference` 思路自行实现，**不要把它写成硬依赖**。

## 2. www.okooo.com（澳客竞彩开奖）

### 2.1 URL 与限制

```
https://www.okooo.com/jingcai/kaijiang/?LotteryType=SportteryNWDL&StartDate={s}&EndDate={e}
```
- **单次最多 5 天**，超限页面会含「不得超过5天」提示并返回空表。
- 需 `User-Agent`（桌面 Chrome）+ `Referer: https://www.okooo.com/jingcai/kaijiang/`。
- 编码 gbk / gb18030。

### 2.2 行结构（**必须先剥注释**）

```python
html = re.sub(r'<!--.*?-->', '', html, flags=re.S)   # ← 不剥会整行列错位！
```

页面上存在**被注释掉的 `<td>指数</td>`**。若不先剥注释，`re.findall(r'<td>(.*?)</td>')` 会数错列数，导致 `handicap / rq_res / score_res ...` 整体错位一格（表现为"让球数"字段里出现赔率数字）。

列顺序（剥离注释后，取前 18 个 `<td>`）：
```
no, league, datetime, home, away, ht, ft, wdl_res, wdl_odds,
handicap, rq_res, rq_odds, score_res, score_odds,
goals_res, goals_odds, hf_res, hf_odds
```
只保留含 `/soccer/match/` 且 `<td>` 数 ≥ 18 的行；`match_id` 从 `/soccer/match/(\d+)/` 取。

- `datetime` 形如 `MM-DD HH:MM`（无年份），脚本按当年 2026 补全。
- 澳客**偶有缺行**（如 10-10 周六007 热那亚 vs 佛罗伦萨 缺失）→ 样本应以"两源并集"为准。

## 3. 两源对齐

- **匹配键 = (开球日, 竞彩期号)**。两站共用竞彩编号（如 500 的「周日021 贝蒂斯 vs 奥萨苏纳」与澳客同日同号同队）。
- **期号前缀 = 开奖日（周X）**，与实际开球日期可能差一天（凌晨开球）。**两站一致**，所以按 `num` 前缀过滤"工作日/周末"是可靠的。
- 比分交叉校验：工作日 149 可比 / 2 处不一致；周末 139 可比 / 6 处不一致——**全部是 `6:0 → 胜其他` 这类归一化差异，非数据冲突**。

## 4. 已知不可得（默认缺失，勿伪造）

| 维度 | 状态 |
|---|---|
| 初盘 / 盘中 / 尾盘赔率轨迹 | ❌ 500 历史页只回吐**最终盘**，无升降轨迹 |
| 成交量 / 热度 / 异常波动 | ❌ 无公开接口 |
| 欧赔（多公司初盘即时对比） | ❌ odds.500.com 被验证码拦截 |
| 亚盘 / 大小球盘口 | ❌ 同上 |
| 基本面（伤病 / 轮换 / 交锋 / 赛程密度） | ❌ 无稳定源；如需须由用户提供 |

⇒ 产出时必须显式声明："本轮基于终盘赔率 + 让球盘 + 赛果"，并把上表列为缺口。
