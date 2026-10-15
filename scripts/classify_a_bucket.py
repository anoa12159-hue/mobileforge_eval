"""
A-bucket fine classification: A1 (route not registered) vs A2/A3 (uncertain)
============================================================================

For every record currently in `A_WRONG_ROUTE_TO_ROOT` (final_url == "/", click
triggered navigation but landed on the React-Router catch-all), we read the
agent's `src/router.tsx` and check whether the **target route** (from
`test_cases.reviewed.json -> to_page.route_hint`) is registered.

- **A1_ROUTE_NOT_REGISTERED**: the agent's router does not contain the target
  route (exact path or pattern-matchable, e.g. `/foo/:id`). The link is
  guaranteed to fall through to the catch-all. This is unambiguously an
  agent bug — they declared a `<Link to=\"/foo\">` somewhere but never wrote
  a `<Route path=\"/foo\">`.

- **A23_NEEDS_MANUAL**: the target route IS registered in the router. So the
  link should have worked. Two possibilities:
    A2 — the click model hit the wrong element (a different tab / button).
    A3 — the agent uses a route alias (e.g. implements `explore` at path `/`
         instead of `/explore`), and the click did go to the right *page*
         even though the URL path test marked it as wrong.
  We cannot disambiguate A2 from A3 from the router alone; this needs the
  click trace or the after-screenshot. We surface this set for the
  click-model human-eval extension (C2-B2).

Outputs:
  experiments/failures/a_bucket_breakdown.json
  experiments/failures/a_bucket_breakdown.md
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PATH_REGEXES = [
    re.compile(r"path:\s*['\"]([^'\"]+)['\"]"),     # path: '/foo'
    re.compile(r"path=['\"]([^'\"]+)['\"]"),        # path="/foo"
]


def extract_routes(router_path: Path) -> set[str]:
    """Return the set of registered paths in a router file."""
    if not router_path.exists():
        return set()
    text = router_path.read_text(encoding="utf-8", errors="ignore")
    paths: set[str] = set()
    for rx in PATH_REGEXES:
        for m in rx.finditer(text):
            p = m.group(1).strip()
            if not p.startswith("/"):
                p = "/" + p
            paths.add(p.rstrip("/") or "/")
    return paths


def normalize(p: str) -> str:
    return (p.rstrip("/") or "/").lower()


def route_matches(target: str, registered: set[str]) -> tuple[bool, str | None]:
    """True if `target` matches any registered path. Returns (matched, matched_pattern).

    Matching rules:
      - Exact (case-insensitive) match.
      - Pattern match: a registered `/foo/:id` matches a target `/foo/123` or
        `/foo/anything`. We treat `:slug`, `:id`, `:param` as wildcards.
    """
    t = normalize(target)
    t_segs = t.split("/")
    for r in registered:
        if normalize(r) == t:
            return True, r
        r_segs = normalize(r).split("/")
        if len(r_segs) != len(t_segs):
            continue
        ok = True
        for rs, ts in zip(r_segs, t_segs):
            if rs.startswith(":"):
                continue
            if rs != ts:
                ok = False
                break
        if ok:
            return True, r
    return False, None


def find_router(run_dir: Path) -> Path:
    candidates = [
        run_dir / "src" / "router.tsx",
        run_dir / "src" / "router.ts",
        run_dir / "src" / "Router.tsx",
        run_dir / "src" / "App.tsx",
    ]
    for c in candidates:
        if c.exists():
            return c
    return run_dir / "src" / "router.tsx"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=Path("experiments/failures/taxonomy.json"),
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("experiments/20260428_181902_mobileforge-compare/runs"),
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("input/mobileforge"),
    )
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/failures"))
    args = parser.parse_args()

    tax = json.load(args.taxonomy.open("r", encoding="utf-8"))
    a_records = [r for r in tax["records"] if r["bucket"] == "A_WRONG_ROUTE_TO_ROOT"]

    test_cases_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def get_case(app: str, test_id: str) -> dict[str, Any] | None:
        if app not in test_cases_cache:
            tc = json.load((args.input_dir / app / "test_cases.reviewed.json").open("r", encoding="utf-8"))
            test_cases_cache[app] = {c["test_id"]: c for c in tc["cases"]}
        return test_cases_cache[app].get(test_id)

    routes_cache: dict[tuple[str, str], set[str]] = {}

    def get_routes(app: str, model: str) -> set[str]:
        key = (app, model)
        if key in routes_cache:
            return routes_cache[key]
        rp = find_router(args.runs_dir / f"{app}__{model}__agentic")
        routes_cache[key] = extract_routes(rp)
        return routes_cache[key]

    classified: list[dict[str, Any]] = []
    for r in a_records:
        case = get_case(r["app"], r["test_id"])
        if not case:
            classified.append({**r, "sub_bucket": "UNKNOWN_NO_CASE", "target_route": None, "matched_pattern": None})
            continue
        target = (case.get("to_page") or {}).get("route_hint")
        registered = get_routes(r["app"], r["model"])
        matched, pattern = route_matches(target or "", registered) if target else (False, None)
        sub = "A23_NEEDS_MANUAL" if matched else "A1_ROUTE_NOT_REGISTERED"
        classified.append({
            **r,
            "sub_bucket": sub,
            "target_route": target,
            "matched_pattern": pattern,
            "registered_routes": sorted(registered),
        })

    counts = Counter(c["sub_bucket"] for c in classified)
    by_model = defaultdict(Counter)
    by_type = defaultdict(Counter)
    by_app = defaultdict(Counter)
    for c in classified:
        by_model[c["sub_bucket"]][c["model"]] += 1
        by_type[c["sub_bucket"]][c.get("type", "?")] += 1
        by_app[c["sub_bucket"]][c["app"]] += 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "a_bucket_breakdown.json").write_text(
        json.dumps({"records": classified, "counts": dict(counts)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append("# A-bucket sub-classification: A1 vs A2/A3")
    lines.append("")
    lines.append(f"> {len(a_records)} records originally in `A_WRONG_ROUTE_TO_ROOT` "
                 "(final URL == `/`). Sub-bucket assigned by parsing the agent's `src/router.tsx`.")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append("| Sub-bucket | Definition | Count | Share |")
    lines.append("|---|---|---:|---:|")
    total = max(1, sum(counts.values()))
    for sb in ("A1_ROUTE_NOT_REGISTERED", "A23_NEEDS_MANUAL", "UNKNOWN_NO_CASE"):
        c = counts.get(sb, 0)
        defn = {
            "A1_ROUTE_NOT_REGISTERED": "Target route is NOT registered in the agent's router; agent bug.",
            "A23_NEEDS_MANUAL": "Target route IS registered; either click-model misclick (A2) or routing-alias (A3). Needs visual inspection.",
            "UNKNOWN_NO_CASE": "Test case not found in test_cases.reviewed.json (annotation issue).",
        }[sb]
        lines.append(f"| `{sb}` | {defn} | {c} | {c/total*100:.1f}% |")
    lines.append("")

    lines.append("## Per-model breakdown")
    lines.append("")
    models = sorted({c["model"] for c in classified})
    lines.append("| Sub-bucket | " + " | ".join(f"`{m}`" for m in models) + " | Total |")
    lines.append("|---|" + "---|" * (len(models) + 1))
    for sb in ("A1_ROUTE_NOT_REGISTERED", "A23_NEEDS_MANUAL", "UNKNOWN_NO_CASE"):
        cells = [str(by_model[sb].get(m, 0)) for m in models]
        lines.append(f"| `{sb}` | " + " | ".join(cells) + f" | **{sum(by_model[sb].values())}** |")
    lines.append("")

    lines.append("## Per-scenario-type breakdown")
    lines.append("")
    types = ["page_exists", "tab_navigation", "parent_child_navigation"]
    lines.append("| Sub-bucket | " + " | ".join(f"`{t}`" for t in types) + " | Total |")
    lines.append("|---|" + "---|" * (len(types) + 1))
    for sb in ("A1_ROUTE_NOT_REGISTERED", "A23_NEEDS_MANUAL", "UNKNOWN_NO_CASE"):
        cells = [str(by_type[sb].get(t, 0)) for t in types]
        lines.append(f"| `{sb}` | " + " | ".join(cells) + f" | **{sum(by_type[sb].values())}** |")
    lines.append("")

    a1 = counts.get("A1_ROUTE_NOT_REGISTERED", 0)
    a23 = counts.get("A23_NEEDS_MANUAL", 0)
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- **A1 share**: {a1}/{total} ({a1/total*100:.1f}%) — confirmed agent routing bugs.")
    lines.append(f"- **A23 share**: {a23}/{total} ({a23/total*100:.1f}%) — these ARE registered in the agent's router. "
                 "Either the click model misclicked, or the agent used a route alias that the URL-path test could not "
                 "recognise. Needs the screenshot or click trace to disambiguate.")
    lines.append("")

    lines.append("## Examples")
    lines.append("")
    lines.append("### Top 5 A1 examples (route truly missing)")
    lines.append("")
    a1_records = [c for c in classified if c["sub_bucket"] == "A1_ROUTE_NOT_REGISTERED"]
    for r in a1_records[:5]:
        lines.append(
            f"- `{r['app']}` / `{r['model']}` / `{r['test_id']}` — target `{r['target_route']}` not in registered routes "
            f"`{r['registered_routes'][:6]}{'...' if len(r['registered_routes'])>6 else ''}`"
        )
    lines.append("")
    lines.append("### Top 5 A23 examples (route registered but click landed on `/`)")
    lines.append("")
    a23_records = [c for c in classified if c["sub_bucket"] == "A23_NEEDS_MANUAL"]
    for r in a23_records[:5]:
        lines.append(
            f"- `{r['app']}` / `{r['model']}` / `{r['test_id']}` — target `{r['target_route']}` matches `{r['matched_pattern']}` "
            "but click landed on `/`"
        )
    lines.append("")

    (args.out_dir / "a_bucket_breakdown.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out_dir / 'a_bucket_breakdown.md'}: A1={a1}, A23={a23}, "
          f"unknown={counts.get('UNKNOWN_NO_CASE', 0)}, total={total}.")


if __name__ == "__main__":
    main()
