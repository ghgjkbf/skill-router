# skill-router 初比对提示词(重生成矩阵时使用)

> 用途:技能清单变化(新增/删除/描述变更)后,由任意 Agent 用本提示词重做初比对,更新 `conflicts.json`。
> 细比对(`router.py route`)始终读取矩阵 + 实时扫描,矩阵指纹过期会自动报警(`matrix_stale: true`)。

## 流程

1. 跑 `python router.py scan` 得到全量清单(name + description + inventory_hash)。
2. **吸收人工回流**:读 `feedback.jsonl`(source=popup/chat 的人工选择)。同一任务模式若被人工多次裁向同一技能,应把对应中文特征词补进 `skill_signals`——这是人教机器的核心通道。
3. 把 scan 的 JSON 喂给本提示词,让模型产出新的 `conflicts.json`。
4. 用 `python router.py verify` 校验;用 3~5 个真实任务跑 `route` 抽查。
5. 矩阵更新后,同步更新你们内部的路由边界文档(如知识库条目),保持"矩阵即真值、文档是镜像"。

## 初比对提示词(复制以下内容给执行模型)

```
你是技能路由裁决器。以下是本机公用技能库的全量技能清单(JSON),
每个条目含 name 和 description。请做跨工具冲突比对:

任务:
1. 找出所有"同一类用户任务会同时命中多个技能"的冲突组(跨工具优先,如生成式视频 vs
   HTML合成视频;工具族内部的分流单独成组)。
2. 每个冲突组写出 routes:按触发特征(when,中英文关键词/正则都要覆盖,来自真实用户说法
   而非技能名)给出"用谁",组内标 default 的路由是兜底。
3. adjudication 一句话写清裁决逻辑,供 Agent 在提示词里快速理解。
4. 为每个易被中文任务命中的技能写 skill_signals 中文特征词表(工具技能的 description 常
   为英文,中文任务打分需要这些信号;写真实用户会说的词,不写技能名)。
5. 设定 auto_policy(min_top_score / dominance_ratio):阈值宁紧勿松,矩阵误判比缺组更有害。
6. 不要发明不存在的技能名;不在扫描清单里但确定存在的技能(如 hub 层技能)可作为路由目标。
7. 拿不准是否冲突的,不建组(宁缺毋滥)。
8. 输出严格遵循 conflicts.json 的现有 schema(version/generated/generated_by/
   inventory_hash/auto_policy/skill_signals/conflicts[]),inventory_hash 原样带回。

技能清单:
<在此粘贴 scan 输出>
```

## 校验清单(初比对完成后必做)

- [ ] `router.py verify`:`matrix_stale` 为 false、`matrix_references_missing_skills` 仅含确知的库外技能
- [ ] 每个冲突组至少有一条 default 路由
- [ ] route 抽查:每个冲突域各一个任务 + 一个"不该触发任何技能"的负例,裁决与预期一致
- [ ] 优势度抽查:一个优势明显任务应得到 `auto-dominant` 且 use 正确
