#!/usr/bin/env python3
"""Extract CONTEXT declarations from v2 hook experiment traces."""
import json, re, os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TRACES = {
    "Opus 4-6": str(PROJECT_ROOT / "output/wxreader-11-hook__agentic__claude-opus-4-6/.designforge/traces/00_/agentic/af5ee83f4bf7.trace.json"),
    "Sonnet 4": str(PROJECT_ROOT / "output/wxreader-11-hook__agentic__claude-sonnet-4/.designforge/traces/00_/agentic/837a7d2b2b10.trace.json"),
    "GPT-5":    str(PROJECT_ROOT / "output/wxreader-11-hook__agentic__gpt-5/.designforge/traces/00_/agentic/b41698fe32ca.trace.json"),
}

CONTEXT_RE = re.compile(r'CONTEXT:\s*phase=(\S+)\s+screenshots=(\S+)\s+files=(\S+)')

def shorten_screenshot(s):
    m = re.search(r'15-(\d{2})-(\d{2})-(\d{2})', s)
    return f"ss-{m.group(1)}:{m.group(2)}:{m.group(3)}" if m else s[:20]

def parse_declaration(text):
    if not text:
        return None
    m = CONTEXT_RE.search(text)
    if not m:
        return None
    phase = m.group(1)
    raw_ss = m.group(2)
    raw_files = m.group(3)

    if raw_ss.lower() in ("none", "\"none\""):
        screenshots = []
    elif "all" in raw_ss.lower():
        screenshots = ["ALL"]
    else:
        screenshots = [shorten_screenshot(s) for s in raw_ss.split(",") if s.strip()]

    if raw_files.lower() in ("none", "\"none\""):
        files = []
    else:
        files = [os.path.basename(f) for f in raw_files.split(",") if f.strip()]

    return {"phase": phase, "screenshots": screenshots, "files": files}

def classify_tools(it):
    tcs = it.get("tool_calls") or []
    names = [tc.get("name","") for tc in tcs]
    written = []
    for tc in tcs:
        if tc.get("name") == "write_file":
            p = tc.get("arguments",{}).get("path","") if isinstance(tc.get("arguments"),dict) else ""
            written.append(os.path.basename(p))
    if "task_done" in names: return "done", written
    if "write_file" in names: return "write", written
    if any(n in ("str_replace","batch_str_replace") for n in names): return "fix", written
    if "run_command" in names: return "verify", written
    return "explore", written

def analyze(label, path):
    with open(path) as f:
        data = json.load(f)
    iters = data["iterations"]
    tok = data["tokens"]
    n_decl = 0

    print(f"\n{'='*110}")
    print(f"  {label} | {len(iters)} iters | input={tok['input']:,} | output={tok['output']:,}")
    print(f"{'='*110}")
    print(f"{'It':>3} {'Action':<8} {'InTok':>8} {'OutTok':>7} {'Decl':>5} {'Phase':<18} {'Screenshots':<30} {'Files'}")
    print("-"*110)

    declarations = []
    for it in iters:
        idx = it["iteration"]
        intok = it["tokens"]["input"]
        outtok = it["tokens"]["output"]
        action, written = classify_tools(it)
        decl = parse_declaration(it.get("assistant_text",""))

        if decl:
            n_decl += 1
            ss_str = ",".join(decl["screenshots"]) if decl["screenshots"] else "none"
            f_str = ",".join(decl["files"]) if decl["files"] else "none"
            declarations.append({"iter": idx, **decl, "action": action})
            print(f"{idx:>3} {action:<8} {intok:>8,} {outtok:>7,} {'✅':>5} {decl['phase']:<18} {ss_str:<30} {f_str}")
        else:
            w = ",".join(written[:2])
            if len(written) > 2: w += f"+{len(written)-2}"
            print(f"{idx:>3} {action:<8} {intok:>8,} {outtok:>7,} {'—':>5} {'':18} {'':30} {w}")

    pct = n_decl / len(iters) * 100 if iters else 0
    print(f"\n  声明覆盖率: {n_decl}/{len(iters)} ({pct:.0f}%)")

    if declarations:
        print(f"\n  --- 声明摘要 ---")
        for d in declarations:
            ss = d["screenshots"]
            if ss == ["ALL"]:
                ss_desc = "全部 7 张"
            elif not ss:
                ss_desc = "不需要"
            else:
                ss_desc = f"{len(ss)} 张: {','.join(ss)}"
            f_desc = ",".join(d["files"]) if d["files"] else "不需要"
            print(f"  iter {d['iter']:>2}: phase={d['phase']:<18} 截图={ss_desc:<35} 文件={f_desc}")

    return {"label": label, "n_iters": len(iters), "n_decl": n_decl,
            "declarations": declarations, "input": tok["input"], "output": tok["output"]}

def main():
    results = []
    for label, path in TRACES.items():
        results.append(analyze(label, path))

    print(f"\n{'='*110}")
    print("  跨模型对比")
    print(f"{'='*110}")

    print("\n  --- 声明覆盖率 ---")
    for r in results:
        pct = r["n_decl"] / r["n_iters"] * 100
        print(f"  {r['label']:>12}: {r['n_decl']:>2}/{r['n_iters']:<2} ({pct:>4.0f}%) | input={r['input']:>10,} | output={r['output']:>7,}")

    print("\n  --- 截图需求按阶段 ---")
    for r in results:
        for d in r["declarations"]:
            ss = d["screenshots"]
            if ss == ["ALL"]: tag = "ALL_7"
            elif not ss: tag = "NONE"
            else: tag = f"{len(ss)}_img"
            print(f"  {r['label']:>12} iter{d['iter']:>2} phase={d['phase']:<18} screenshots={tag}")

if __name__ == "__main__":
    main()
