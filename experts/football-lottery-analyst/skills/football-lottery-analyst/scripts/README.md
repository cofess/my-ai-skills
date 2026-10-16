# scripts 用法速查

唯一入口：`wb_football.py`（依赖同目录 `wbf_lib.py`）。仅需系统 `curl` + Python 3.8+（标准库）。
所有子命令支持 `-h`；输出目录会自动创建。

```bash
PY=python3                     # 或本机 Python 绝对路径
SK=<skill_dir>/scripts/wb_football.py
```

## 子命令一览

| 子命令 | 作用 | 关键参数 |
|---|---|---|
| `fetch-odds` | 抓 trade.500.com 四玩法页 | `--start --end [--playid 354 271 270 272] [--days all\|weekday\|weekend] --out --force` |
| `parse-odds` | 解析页面 → `odds.json` | `--dir <html目录> --out` |
| `fetch-results` | 抓澳客开奖 → `results.json`（已归一化） | `--start --end --out [--max-blocks] [--force]` |
| `build` | 合并赛果 + 500 终盘 → `master_odds.json` | `--results <file> --data <odds.json目录> [--extra f1 f2] [--days] [--before] [--limit] --out` |
| `backtest` | 回测 → `*_report.txt` + `*.json` | `--dataset <master_odds.json> --out-prefix` |
| `analyze` | 当日结构 + 重点候选 | `--data <odds.json目录> --sale-date YYYY-MM-DD --out` |
| `compare` | 预测 vs 赛果闭环比对 | `--pred <json> --results <json> --out` |

## 典型流程

### 当日预测

```bash
$PY $SK fetch-odds --start 2026-10-15 --end 2026-10-15 --out ./data
$PY $SK parse-odds --dir ./data --out ./data
$PY $SK analyze    --data ./data --sale-date 2026-10-15 --out ./data
# -> data/today_merged.json, data/today_analysis.json, data/today_analysis.txt
```

### 历史回测（工作日 / 周末分开跑）

```bash
$PY $SK fetch-odds --playid 354 271 270 272 --start 2026-09-15 --end 2026-10-13 --days all --out ./data
$PY $SK parse-odds --dir ./data --out ./data
$PY $SK fetch-results --start 2026-07-27 --end 2026-10-13 --out ./data
$PY $SK build   --results ./data/results.json --data ./data --days weekday --before 2026-10-14 --limit 300 --out ./data
$PY $SK backtest --dataset ./data/master_odds.json --out-prefix ./data/bt300
# 周末：--days weekend --limit 200
```

> **500 赔率历史窗口约 30 天**。`--before` 超出窗口的场次会退化为"仅让球盘可跑"，
> 回测报告会如实显示 `500 终盘覆盖` 的实际条数。

### 比对与校正

`pred.json` 结构（`f1` 用 `3/1/0`，`score/goals/htft` 可为字符串或数组）：

```json
[{"no": "周四012", "home": "本菲卡", "away": "凯尔特人",
  "f1": "3", "score": ["2:0", "3:1"], "goals": ["2", "3"], "htft": ["3-3"]}]
```

```bash
$PY $SK compare --pred ./pred.json --results ./data/results.json --out ./data/cmp
```

## 输入 / 输出约定

| 文件 | 何时产出 | 内容 |
|---|---|---|
| `odds.json` | `parse-odds` | 所有解析到的行：`play / num / sale_date / kick_date / rq / bets{...}` |
| `results.json` | `fetch-results` | 归一化赛果：`no / date / ft / ht / handicap / w_res / w_sp / rq_sp / score_res / score_sp / goals_res / htft_res ...` |
| `master_odds.json` | `build` | 主数据集：赛果 + 派生字段（`w_actual / goals_cat / score_cat / htft_cat / rq`）+ `o500`（500 终盘赔率） |
| `today_merged.json` | `analyze` | 当日合并赔率矩阵（按销售日） |
| `today_analysis.json/.txt` | `analyze` | 归一化隐含概率 + A–E 五组重点候选 |
| `*_report.txt` / `*.json` | `backtest` | 命中率 / 重点子集 / 结构 / 分档 / ROI / 子组；逐场判定字段 |
| `cmp.json` | `compare` | 逐场预测 vs 实际 + 命中标记 |

## 兼容性

- `build --extra` 可并入旧格式赛果文件，自动识别三种 schema：
  - **B**：澳客开奖原始行（含 `datetime`）
  - **C**：已归一化行（含 `w_res` / `rq_res` / `score_res` / `htft_res`）
  - **A**：旧自建行（含 `wdl` / `wdl_sp` / `ah_wdl` / `total_goals` / `htft`）
- 文件名需形如 `p{playid}_{date}.html`（历史批量）或 `pw{playid}_{date}.html`（周末），
  `parse-odds` 从文件名推断 playid。

## 验证基线

用「工作日 300 场」数据集跑 `backtest`，应复现（与已发布报告**逐行一致**）：

```
F1 盘口倾斜法     188/300 = 62.7%
深盘(|rq|>=2)     39/44  = 88.6%
O5 半全场Top2     101/163 = 62.0%
O3 比分Top3       47/165 = 28.5%
overround         1.1292（抽水约 12.9%）
F1 全押 ROI       -3.43%   |  主让单押主胜 +0.72% (t=0.08)
```
