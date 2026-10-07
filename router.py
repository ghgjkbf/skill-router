#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill-router: 技能冲突比对与路由裁决工具

两层机制(配套 PROMPT.md 的初比对流程):
  初比对(离线, AI 提示词): LLM 读取 scan 输出, 产出 conflicts.json 冲突裁决矩阵
  细比对(在线, 本工具):    route 命令结合当前实时技能清单 + 矩阵, 输出裁决
  人工兜底(--ask):        仅势均力敌时弹窗让人选; 优势明显自动裁决, 零信号不弹窗;
                           人工选择写入 feedback.jsonl 回流

子命令:
  scan           扫描技能清单(frontmatter name/description + 指纹)
  route "任务"    细比对: 任务文本 -> 用谁/避谁/为什么(JSON)
                 --ask          无命中时弹窗让人选(tkinter, 需本机桌面)
                 --timeout N    弹窗等待秒数(默认 120, 超时/取消视为未决)
                 --record 技能名 记录人工选择(聊天确认场景, 不弹窗)
  verify         矩阵覆盖率与漂移检查(新技能/描述变更/矩阵过期)
标准库实现, 无第三方依赖。用法示例:
  python router.py scan
  python router.py route "做个产品发布视频"
  python router.py route "把这张图修一下" --ask --timeout 90
  python router.py route "修这张图" --record photon-edit
  python router.py verify
"""
import argparse
import hashlib
import os
import json
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("SKILL_ROUTER_ROOT") or (Path.home() / ".agents" / "skills"))
TOOL_DIR = Path(__file__).resolve().parent
CONFLICTS_FILE = TOOL_DIR / "conflicts.json"


# ---------- 技能清单扫描 ----------

def parse_skill_md(path: Path):
    """宽容解析 SKILL.md frontmatter, 取 name 与 description(支持折叠块)。"""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    if not txt.startswith("---"):
        return "", ""
    end = txt.find("\n---", 3)
    fm = txt[3:end] if end != -1 else txt[3:2000]
    name, desc = "", ""
    lines = fm.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("name:") and not name:
            name = ln[5:].strip().strip("'\"")
        elif ln.startswith("description:") and not desc:
            rest = ln[12:].strip()
            if rest in (">", ">-", "|", "|-"):
                buf = []
                i += 1
                while i < len(lines) and (lines[i][:1] in (" ", "\t") or lines[i].strip() == ""):
                    buf.append(lines[i].strip())
                    i += 1
                desc = " ".join(b for b in buf if b)
                continue
            desc = rest.strip("'\"")
        i += 1
    return name, desc


def scan(root: Path):
    """扫描技能目录, 返回清单与整体指纹。"""
    skills = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            sm = d / "SKILL.md"
            if d.is_dir() and sm.exists():
                name, desc = parse_skill_md(sm)
                if not name:
                    name = d.name
                dh = hashlib.sha256(desc.encode("utf-8")).hexdigest()[:12]
                skills.append({"name": name, "dir": d.name, "description": desc,
                               "desc_hash": dh, "path": str(sm)})
    inventory_hash = hashlib.sha256(
        "\n".join(f"{s['name']}:{s['desc_hash']}" for s in sorted(skills, key=lambda x: x["name"])).encode("utf-8")
    ).hexdigest()[:16]
    return skills, inventory_hash


# ---------- 细比对(route) ----------

def load_conflicts():
    if not CONFLICTS_FILE.exists():
        return None
    return json.loads(CONFLICTS_FILE.read_text(encoding="utf-8"))


def keyword_fallback(task: str, skills, top=5, signals=None):
    """无裁决命中时按优势度排序的候选(带得分)。
    中文无分词,用 CJK 二元组命中数 + 英文词命中(×2)合成信号;
    signals 为 conflicts.json 里的中文特征词表(技能描述常为英文,需要中文信号补充)。"""
    t_low = task.lower()
    signals = signals or {}
    score_parts = []
    for s in skills:
        d = (s.get("name", "") + " " + (s.get("description") or "") + " " + " ".join(signals.get(s["name"], []))).lower()
        score = 0
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", t_low):
            grams = {run[i:i + 2] for i in range(len(run) - 1)}
            score += sum(1 for g in grams if g in d)
        for w in re.findall(r"[a-z0-9]{3,}", t_low):
            if w in d:
                score += 2
        score_parts.append((score, s))
    score_parts.sort(key=lambda x: (-x[0], x[1]["name"]))
    return score_parts[:top]


def auto_dominant(task: str, skills, policy, signals=None):
    """优势明显判定:top 得分达阈值且 ≥ dominance_ratio × 次名。
    返回 (top, second, 全量排序);不明显返回 (None, None, 全量排序)。"""
    ranked = keyword_fallback(task, skills, top=len(skills), signals=signals)
    if not ranked or ranked[0][0] <= 0:
        return None, None, ranked
    top_score, top_skill = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0
    min_top = policy.get("min_top_score", 3)
    ratio = policy.get("dominance_ratio", 2)
    if top_score >= min_top and top_score >= ratio * max(second_score, 1):
        return (top_score, top_skill), (second_score, ranked[1][1] if len(ranked) > 1 else None), ranked
    return None, None, ranked


FEEDBACK_FILE = TOOL_DIR / "feedback.jsonl"


def record_feedback(task: str, chosen: str, source: str, candidates=None):
    """人工选择回流: 追加到 feedback.jsonl, 供下一轮初比对(PROMPT.md 第 2.5 步)吸收。"""
    import datetime
    entry = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
             "task": task, "chosen": chosen, "source": source,
             "candidates": [c["name"] for c in (candidates or [])]}
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ask_human(task: str, candidates, timeout: int):
    """tkinter 弹窗让人选; 返回所选技能名, 取消/超时返回 None。
    候选 <=6 用单选钮, 更多用滚动列表(保证人工兜底永远可达)。"""
    import tkinter as tk
    result = {"chosen": None}
    root = tk.Tk()
    root.title("skill-router 技能路由裁决")
    root.attributes("-topmost", True)
    root.geometry("+80+80")
    tk.Label(root, text="任务:", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
    tk.Label(root, text=task, wraplength=620, justify="left").pack(anchor="w", padx=14, pady=(0, 8))
    tk.Label(root, text="多个相似技能匹配, 请按你的需求选择(未选择时确定无效):").pack(anchor="w", padx=14, pady=(0, 4))
    var = tk.StringVar(value="")

    if len(candidates) <= 6:
        for s in candidates:
            desc = (s.get("description") or "").replace("\n", " ")[:110]
            tk.Radiobutton(root, text=f"{s['name']}   —   {desc}", value=s["name"], variable=var,
                           wraplength=620, justify="left", anchor="w").pack(fill="x", padx=18)
    else:
        listbox = tk.Listbox(root, height=14, width=95, activestyle="dotbox")
        for s in candidates:
            desc = (s.get("description") or "").replace("\n", " ")[:90]
            listbox.insert(tk.END, f"{s['name']}   —   {desc}")
        listbox.pack(padx=14, fill="both", expand=True)

        def on_select(event):
            sel = listbox.curselection()
            if sel:
                var.set(candidates[sel[0]]["name"])
        listbox.bind("<<ListboxSelect>>", on_select)

    btns = tk.Frame(root)
    btns.pack(pady=12)

    def ok(event=None):
        if not var.get():
            return  # 未选择则确定无效, 强制显式选择或取消
        result["chosen"] = var.get()
        root.destroy()

    def cancel(event=None):
        root.destroy()
    tk.Button(btns, text="确定", width=10, command=ok).pack(side="left", padx=8)
    tk.Button(btns, text="取消", width=10, command=cancel).pack(side="left", padx=8)
    root.bind("<Return>", ok)
    root.bind("<Escape>", cancel)
    if timeout > 0:
        root.after(timeout * 1000, cancel)
    root.mainloop()
    return result["chosen"]


def route(task: str, root: Path, ask=False, timeout=120, record=None):
    conflicts = load_conflicts()
    if conflicts is None:
        print(json.dumps({"ok": False, "error": "conflicts.json 不存在, 先跑初比对(见 PROMPT.md)"}, ensure_ascii=False))
        return 2
    skills, inv_hash = scan(root)
    drift = inv_hash != conflicts.get("inventory_hash")

    # 聊天确认场景: 只记录人工选择, 不做匹配
    if record:
        record_feedback(task, record, "chat")
        print(json.dumps({"ok": True, "recorded": record, "feedback_file": str(FEEDBACK_FILE)}, ensure_ascii=False))
        return 0

    hits = []
    for c in conflicts.get("conflicts", []):
        for r in c.get("routes", []):
            matched = []
            for p in r.get("when", []):
                try:
                    if re.search(p, task, re.I):
                        matched.append(p)
                except re.error:
                    if p.lower() in task.lower():
                        matched.append(p)
            if matched:
                hits.append({"conflict": c["id"], "domain": c.get("domain", ""),
                             "use": r["use"], "avoid": [m for m in c["members"] if m != r["use"]],
                             "matched_by": matched, "adjudication": c.get("adjudication", "")})
                break

    if hits:
        print(json.dumps({"ok": True, "task": task, "matrix_drift": drift,
                          "decided_by": "matrix", "verdicts": hits}, ensure_ascii=False, indent=1))
        return 0

    # 矩阵无命中 -> 优势度打分; 一方优势明显则自动裁决(最小人工干预)
    policy = conflicts.get("auto_policy", {})
    top, second, ranked = auto_dominant(task, skills, policy, conflicts.get("skill_signals", {}))

    if top:
        detail = [{"name": s["name"], "score": sc} for sc, s in ranked[:5]]
        print(json.dumps({"ok": True, "task": task, "matrix_drift": drift,
                          "decided_by": "auto-dominant", "use": top[1]["name"],
                          "advantage": {"top_score": top[0], "runner_up": second[0] if second else 0,
                                        "policy": policy or {"min_top_score": 3, "dominance_ratio": 2}},
                          "candidates": detail}, ensure_ascii=False, indent=1))
        return 0

    # 优势不明显: 势均力敌才弹窗(仅前 6 候选); 零信号不弹窗不硬猜
    weak = bool(ranked and ranked[0][0] > 0)
    if ask and weak:
        cands = [s for _, s in ranked[:6]]
        try:
            chosen = ask_human(task, cands, timeout)
        except Exception as e:  # 无桌面/无显示环境, 优雅降级
            chosen = None
            ask_error = str(e)
        else:
            ask_error = None
        if chosen:
            record_feedback(task, chosen, "popup", cands)
            print(json.dumps({"ok": True, "task": task, "matrix_drift": drift,
                              "decided_by": "human", "use": chosen,
                              "candidates": [c["name"] for c in cands],
                              "feedback_file": str(FEEDBACK_FILE)}, ensure_ascii=False, indent=1))
            return 0
        print(json.dumps({"ok": True, "task": task, "matrix_drift": drift,
                          "decided_by": None,
                          "note": "人工未决(超时/取消/无桌面)" + (f": {ask_error}" if ask_error else ""),
                          "candidates": [c["name"] for c in cands],
                          "candidates_detail": [{"name": c["name"], "score": sc} for sc, c in ranked[:6]]},
                         ensure_ascii=False, indent=1))
        return 0

    note = ("零技能信号: 无技能具备优势, 按通用能力处理, 不启用专门技能" if not weak
            else "候选势均力敌(无明显优势方)" + ("; 用户在场可加 --ask 弹窗人工裁决" if not ask else "; 弹窗已跳过"))
    print(json.dumps({"ok": True, "task": task, "matrix_drift": drift,
                      "decided_by": None, "verdicts": [],
                      "candidates_no_conflict": [{"name": s["name"], "score": sc} for sc, s in ranked[:5]],
                      "note": note}, ensure_ascii=False, indent=1))
    return 0


# ---------- 矩阵体检(verify) ----------

def verify(root: Path):
    conflicts = load_conflicts()
    if conflicts is None:
        print(json.dumps({"ok": False, "error": "conflicts.json 不存在"}, ensure_ascii=False))
        return 2
    skills, inv_hash = scan(root)
    names = {s["name"] for s in skills}
    covered = set()
    for c in conflicts.get("conflicts", []):
        covered.update(c.get("members", []))
        for r in c.get("routes", []):
            covered.add(r["use"])
    uncovered = sorted(names - covered)
    missing = sorted(covered - names)
    out = {
        "ok": True,
        "inventory_hash_now": inv_hash,
        "inventory_hash_matrix": conflicts.get("inventory_hash"),
        "matrix_stale": inv_hash != conflicts.get("inventory_hash"),
        "skills_total": len(names),
        "matrix_covered": sorted(covered & names),
        "uncovered_new_skills": uncovered,
        "matrix_references_missing_skills": missing,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="技能根目录(默认 ~/.agents/skills, 可用环境变量 SKILL_ROUTER_ROOT 覆盖)")
    ap = argparse.ArgumentParser(description="skill-router: 技能冲突初比对(AI)+ 细比对(本工具)", parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", parents=[common], help="输出技能清单 JSON(供初比对提示词使用)")
    route_p = sub.add_parser("route", parents=[common], help='细比对: route "任务描述"')
    route_p.add_argument("task", help="任务描述文本")
    route_p.add_argument("--ask", action="store_true", help="仅势均力敌时弹窗让人选(需本机桌面)")
    route_p.add_argument("--timeout", type=int, default=120, help="弹窗等待秒数(默认 120)")
    route_p.add_argument("--record", metavar="SKILL", help="记录人工选择(聊天确认场景, 不匹配不弹窗)")
    sub.add_parser("verify", parents=[common], help="矩阵漂移与覆盖检查")
    args = ap.parse_args()
    if args.cmd == "scan":
        skills, inv_hash = scan(args.root)
        print(json.dumps({"inventory_hash": inv_hash, "count": len(skills), "skills": skills},
                         ensure_ascii=False, indent=1))
    elif args.cmd == "route":
        sys.exit(route(args.task, args.root, ask=args.ask, timeout=args.timeout, record=args.record))
    elif args.cmd == "verify":
        sys.exit(verify(args.root))


if __name__ == "__main__":
    main()
