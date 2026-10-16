# scripts 用法速查

仅需 **Python 3.8+ 标准库**，无第三方依赖。

## 文件

| 文件 | 作用 |
|---|---|
| **`env_rules.py`** | ★ **规则与工具真源**：`wd_rules()` / `we_rules()` / `predict()` / `evaluate()` / `roi()` / `ztest()` / 别名表 `ALIAS` / `env_of()` |
| `today_predict.py` | 当日预测判定（套用工作日或周末固化方法集） |
| `sep_backtest.py` | 56 变体 × 5 玩法 × 2 环境**全排名引擎** |
| `sep_final.py` | **固化方法集** + 错因分解 + 显著性 + 逐场 CSV |
| `sep_special.py` | **四专项**（识别层 precision/recall/lift + 预测层 ROI） |
| `check_contam.py` | 回归校验（证明 D10 口径污染已修复） |
| `examples/today_2026-10-16.json` | 当日输入数据样例（11 场） |

> **铁则**：任何新脚本都必须 `from env_rules import ...`，**不得自行定义规则或另写一份 predict()**，
> 否则回测与预测口径会分裂（这正是 D10 / D12 两个坑的成因）。

## 环境变量

| 变量 | 含义 | 默认 |
|---|---|---|
| `JZ_DATA_DIR` | 数据集目录，需含 `master300_odds.json`（工作日）、`master200_wk_odds.json`（周末） | 脚本同级 `../hist/` |
| `JZ_OUT_DIR` | 输出目录 | 脚本同级 `out/` |

数据集字段（每场）：`no / date / time / home / away / rq / ft / goals`、
结果档位 `w_actual / rq_res / score_cat / goals_cat / htft_cat`、
赔率字典 `o500 = {nspf, spf, jqs, bqc, bf}`。

## 当日预测

```bash
PY=python3
SK=<skill_dir>/scripts

# 1) 按 references/data-sources.md 采集当日四玩法 SP，整理成 examples 那种 JSON
# 2) 判定（环境默认按期号前缀自动判断；也可 --env 强制）
$PY $SK/today_predict.py --input $SK/examples/today_2026-10-16.json
# -> out/today_<date>.txt
```

输出含：今日结构（深盘/主让/客让分布）→ 数据缺失披露 → 逐场指标（p / 概率差 / 让球盘最热）→ 各档位出手判定。

> **判环境看期号前缀**（周六/周日 → 周末方法集），**不看开球时刻** —— 凌晨开球场次属前一天期号（陷阱 T8）。

## 回测与校正

```bash
export JZ_DATA_DIR=/path/to/hist

$PY $SK/sep_backtest.py     # -> out/rank_{weekday,weekend}.txt / .json（56 变体全排名）
$PY $SK/sep_final.py        # -> out/final_*.txt/.json + detail_*.csv（固化集 + 错因 + 显著性）
$PY $SK/sep_special.py      # -> out/special_rank.txt / out/special_predict.txt（四专项）
$PY $SK/check_contam.py     # 回归校验（工作日 n=64 → 49，证明污染 15 场）
```

`sep_final.py` 末尾会打印**错因分解**与**显著性检验**：

```
工作日·深盘跟盘  88.6%(39/44) vs 基准 50.7%(152/300)  z=4.73  p=0.0000 ★
周末·A级三条件   87.5%(14/16) vs 基准 46.0%(92/200)   z=3.20  p=0.0014 ★
```

## 基线自检（改代码后必跑）

工作日数据集跑 `sep_final.py` 应复现：

```
深盘跟盘（|让球|≥2）      44 场 → 39 命中 = 88.6%（验证 92.9%）
半全场 Top3 ∩ 仅深盘      21 场 → 19 命中 = 90.5%（验证 92.3%）
比分 Top3 ∩ 概率差<20pp   49 场 → 17 命中 = 34.7%
进球数 固定1–3球 ∩ 仅主让 201 场 → 116 命中 = 57.7%
z(深盘 vs 基准)          = 4.73
```

周末数据集应复现：`A级三条件 14/16 = 87.5%`、`比分 Top3∩仅客让 21/54 = 38.9%`、`进球数 Top3 99/149 = 66.4%`。

任一项对不上 ⇒ **先查 `env_rules.py` 是否被改动**（尤其 `gp()` 量纲：本库统一为**百分点**）。
