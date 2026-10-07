# skill-router · Agent 技能冲突路由裁决器

**让 AI Agent 在"好几个技能都能干这活"的时候选对技能。**

现代 Agent 的技能库动辄上百项,工具自带的技能族(修图工具、视频工具、设计工具…)描述宽泛、互相抢路由:同一个"做视频"请求,生成式视频技能和 HTML 合成渲染技能都会命中。常见做法是把技能裁掉一部分("瘦身"),但那牺牲能力、且每次会话省下的 token 有限。

**skill-router 的取舍:能力全量保留,把"选谁"变成可审计的机制。**

```
任务进来
   │
   ▼
冲突矩阵命中? ──是──▶ 按矩阵裁决(decided_by: matrix)
   │ 否
   ▼
优势度打分(CJK 二元组 + 英文词 + 中文信号表)
   │
   ├─ 一方优势明显(top ≥ 阈值 且 ≥ ratio × 次名)──▶ 自动裁决(auto-dominant),不弹窗
   │
   ├─ 势均力敌,且用户在场(--ask)──▶ 弹窗让人选(只给前 6 候选)──▶ 选择回流 feedback.jsonl
   │
   └─ 零信号 ──▶ 不弹窗、不硬猜,按通用能力处理
```

人只教一次:人工选择写入 `feedback.jsonl`,重生成矩阵时吸收进 `conflicts.json`(特征词/信号表),下次同样的问题机器自己就能断。

## 快速开始

```bash
# 扫描技能清单(默认 ~/.agents/skills,可用 SKILL_ROUTER_ROOT 或 --root 覆盖)
python router.py scan

# 细比对:输出"用谁/避谁/为什么"的 JSON
python router.py route "做一个产品发布视频"

# 优势不明显且用户在场:弹窗让人选(无预选,未选不生效)
python router.py route "把这张图修一下" --ask --timeout 90

# 聊天里确认的选择补录(不弹窗)
python router.py route "把这张图修一下" --record photon-edit

# 矩阵漂移检查:技能增删/描述变更后跑
python router.py verify
```

`decided_by` 四种取值:`matrix`(矩阵命中)/ `auto-dominant`(优势明显自动裁决)/ `human`(人工弹窗)/ `null`(未决,附候选与原因)。

## 两阶段工作流

**初比对(离线,AI 提示词)** — 按本仓库 `PROMPT.md` 的模板,把 `router.py scan` 的全量清单交给模型,产出 `conflicts.json`:冲突分组、按触发特征(中英文正则)的路由、每组兜底默认项、每技能的中文信号表、优势度阈值。拿不准是否冲突的**不要建组**——矩阵误判比缺组更有害。

**细比对(在线,确定性工具)** — `route` 命令每次实时重扫技能清单,与矩阵指纹比对:清单变了自动标 `matrix_stale: true`,提示重做初比对。裁决永远基于当前真实库存,不是过期快照。

**回流(人教机器)** — 弹窗/补录的人工选择落 `feedback.jsonl`;同一任务模式被人工多次裁向同一技能时,把特征词补进 `skill_signals`,下次自动裁决。

## 安装为 Agent 技能

本仓库自带标准 SKILL.md,可进任意支持 [Agent Skills](https://github.com/vercel-labs/skills) 的 harness:

```bash
npx skills add ghgjkbf/skill-router -g
```

或把仓库克隆到本地后直接引用 `router.py`。

## 规范模板

多 Agent / 团队环境可直接采用 [`NORM_TEMPLATE.md`](NORM_TEMPLATE.md) 的五条规则(全量入库+矩阵裁决 / 最小人工干预 / 矩阵即真值 / 漂移必检 / 运行前提自检),作为行为规范写进你们的知识库或指令层。

## 文件说明

| 文件 | 作用 |
|---|---|
| `router.py` | 细比对 + 弹窗 + 漂移检查,纯 Python 标准库,零依赖 |
| `conflicts.example.json` | 冲突矩阵样例(结构说明:conflicts / auto_policy / skill_signals) |
| `PROMPT.md` | 初比对的 AI 提示词模板 + 校验清单 + 回流吸收流程 |
| `NORM_TEMPLATE.md` | 可直接采用的行为规范模板 |
| `SKILL.md` | Agent Skills 包装,支持 `npx skills add` |

> 本仓库是通用化发布版;生产实例跑在作者的多 Agent 环境里(路径经环境变量注入),你按自己的技能根目录配置即可。

## English (TL;DR)

**skill-router** adjudicates which agent skill should handle a task when several skills compete. Two-phase workflow: an offline **AI initial comparison** (prompt template in `PROMPT.md`) generates a conflict matrix (`conflicts.json`); an online **deterministic re-scan** (`router.py route "task"`, stdlib-only) adjudicates at runtime — matrix hit → dominant-score auto-decision → human picker (`--ask`, top-6 candidates, no preselection) only on genuine ties → zero-signal tasks fall back to generic handling. Human choices log to `feedback.jsonl` and are absorbed into the matrix on regeneration, so the human teaches once and the router stops asking. Skills stay installed in full — no pruning. Adopts as a norm via `NORM_TEMPLATE.md`, or install as an agent skill: `npx skills add ghgjkbf/skill-router -g`.

## License

Apache-2.0
