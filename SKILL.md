---
name: skill-router
description: Use when a task plausibly matches multiple agent skill families at once (e.g. video generation vs HTML-rendered video, image generation vs image editing, design guidance vs design artifacts) and the agent must pick exactly one skill. Adjudicates via an AI-generated conflict matrix, auto-dominant score fallback, and a human picker only on genuine ties; also checks matrix drift (verify) and manages matrix regeneration (PROMPT.md). Implements the tool-bundled-skills routing norm.
---

# skill-router · 技能冲突路由裁决

多个技能同时命中一个任务时,按以下链路裁决,**不要靠描述自由发挥**:

## 使用步骤

1. **先跑细比对**(每次任务实时执行,几秒钟):

   ```bash
   python <repo>/router.py route "<用户任务原话>"
   ```

2. **按 `decided_by` 分支处理**:
   - `matrix` / `auto-dominant`:按 `use` 字段选用技能,`avoid` 列表里的不要碰;
   - `human`:用户已在弹窗里选过,按 `use` 执行;
   - `null`:读 `note`——零信号就按通用能力处理(不硬套专门技能);势均力敌且用户在场,加 `--ask` 重跑让用户选;用户不在场则在答复中注明"多个技能均可能适用,未过人工确认"。

3. **矩阵漂移**:输出含 `matrix_stale: true` 时,跑 `python <repo>/router.py verify`,然后按 `PROMPT.md` 流程重做初比对(吸收 `feedback.jsonl` 的人工回流),再继续服务。

4. **人工选择回流**:用户在聊天里明确说"用 XX 技能"的,补录:`python <repo>/router.py route "<任务>" --record <技能名>`。

## 维护

- 技能清单变化(装/卸/升级工具)后:`verify` → `matrix_stale` 为 true 时按 `PROMPT.md` 重做初比对;
- 矩阵即真值:知识库/文档里的路由描述与 `conflicts.json` 冲突时,以 `conflicts.json` 为准并回改文档;
- 工具技能常依赖其宿主应用运行(如修图工具需应用在跑),触发前先做前提自检,给用户明确指引而非报错堆栈。
