# football-lottery-analyst · 足彩预测大师

> 竞彩足球（足彩）**全流程、多维度、可校正**的预测分析与历史回测技能包。
> 目标不是「给一个结果」，而是：**分析 → 预测 → 比对赛果 → 校正方法 → 找到准确率最高且经得起 ROI 检验的预测方法**。

**定位**：基于公开数据（赔率 + 开奖结果）的**统计分析与历史回测**，属信息分析用途，**不构成投注建议**。

---

## 这个包能做什么

| 能力 | 说明 |
|---|---|
| **当日预测** | 抓取当日竞彩四玩法赔率 → 归一化隐含概率 → 输出胜平负/比分/进球数/半全场预测 + A–E 五组重点候选 |
| **历史回测** | 批量抓取历史赛程赔率与开奖赛果，按玩法统计命中率、ROI、显著性 |
| **闭环比对** | 预测 vs 实际赛果逐场比对，按「重点分析规则」单独统计命中率，输出错因与校正建议 |
| **分环境建模** | 工作日 / 周末分开标定（两环境的平局率、进球数分布显著不同） |

覆盖玩法：**胜平负、比分（31 档）、进球数（0–7+）、半全场（9 组合）**。

---

## 目录结构

```
football-lottery-analyst/
├── SKILL.md                     技能主文档：角色 / 四条铁律 / 数据源降级协议
│                                / 五维分析框架 / 四条重点规则 / 工作流 / 输出格式
├── references/
│   ├── methodology.md           方法库：全部标定数字（工作日 300 场 vs 周末 200 场 对照）
│   ├── data-sources.md          7 类实测数据源陷阱（30 天窗口 / 深盘缺赔率 / 注释错位 / 验证码…）
│   └── calibration-log.md       校正台账：C1–C8 / W1–W9 / E1–E6 / G1–G6 / D1–D8 + 待办
└── scripts/
    ├── wb_football.py           唯一 CLI 入口（7 个子命令）
    ├── wbf_lib.py               共用常量与纯函数
    └── README.md                脚本用法速查 + 验证基线
```

---

## 快速开始

依赖：**Python 3.8+（仅标准库）+ 系统 `curl`**，无需 pip 安装。

```bash
PY=python3
SK=scripts/wb_football.py

# 1) 当日预测
$PY $SK fetch-odds --start 2026-10-15 --end 2026-10-15 --out ./data
$PY $SK parse-odds --dir ./data --out ./data
$PY $SK analyze    --data ./data --sale-date 2026-10-15 --out ./data

# 2) 历史回测（工作日 / 周末分开跑）
$PY $SK fetch-odds    --playid 354 271 270 272 --start 2026-09-15 --end 2026-10-13 --days all --out ./data
$PY $SK parse-odds    --dir ./data --out ./data
$PY $SK fetch-results --start 2026-07-27 --end 2026-10-13 --out ./data
$PY $SK build         --results ./data/results.json --data ./data --days weekday --before 2026-10-14 --limit 300 --out ./data
$PY $SK backtest      --dataset ./data/master_odds.json --out-prefix ./data/bt300

# 3) 闭环比对
$PY $SK compare --pred ./pred.json --results ./data/results.json --out ./data/cmp
```

| 子命令 | 作用 |
|---|---|
| `fetch-odds` | 抓 trade.500.com 四玩法页 |
| `parse-odds` | 解析页面 → `odds.json` |
| `fetch-results` | 抓澳客开奖 → `results.json`（已归一化） |
| `build` | 合并赛果 + 500 终盘 → `master_odds.json` |
| `backtest` | 回测 → `*_report.txt` + `*.json` |
| `analyze` | 当日结构 + 重点候选 |
| `compare` | 预测 vs 赛果闭环比对 |

各子命令均支持 `-h`；输出目录自动创建。详见 [`scripts/README.md`](scripts/README.md)。

---

## 数据源

| 源 | 能给 | 已知边界 |
|---|---|---|
| `trade.500.com/jczq/`（playid 354/271/270/272） | 四玩法**完整终盘赔率**；历史日页还带已完赛比分 | 只回溯约 30 天；胜平负（不让球）页系统性缺失 \|让球\|≥2 场次 |
| 澳客开奖页 | 让球盘口 + 赛果 + 中奖 SP | 行内含 HTML 注释，解析前必须先剥离，否则整列错位 |

⚠️ **「初盘 / 盘中 / 尾盘轨迹」「欧赔亚盘」「基本面（伤病/轮换/交锋/赛程密度）」均不可得**，
`odds.500.com` 的欧亚盘页被验证码拦截。本包全部结论基于**终盘赔率 + 让球盘 + 赛果**，
产出时必须显式标注该 scope 边界。

---

## 验证基线

用「工作日 300 场」数据集跑 `backtest`，应逐行复现：

```
F1 盘口倾斜法     188/300 = 62.7%
深盘(|rq|>=2)      39/44  = 88.6%
O5 半全场Top2     101/163 = 62.0%
O3 比分Top3        47/165 = 28.5%
overround          1.1292（抽水约 12.9%）
F1 全押 ROI        -3.43%  |  主让单押主胜 +0.72% (t=0.08)
```

---

## 安装

把本目录整个复制到工作区的 skills 目录下即可（两个位置任选其一）：

```bash
# 用户级（所有项目可用）
cp -r football-lottery-analyst ~/.workbuddy/skills/

# 项目级（仅当前项目）
cp -r football-lottery-analyst <project>/.workbuddy/skills/
```

重载后即可通过「足彩预测大师 / 竞彩 / 回测某日赛程 / 爆冷 / 高赔比分 / 半全场反转」等触发词唤起。

---

## License / 免责

预测仅供参考，**不构成任何投注建议**。请遵守所在地区相关法律法规。
