"""从 metadata.json 自动生成测试用例"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import (
    Action,
    ActionStrategy,
    PageRef,
    TestCase,
    TestType,
)


def load_metadata(metadata_path: str | Path) -> dict:
    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _page_name_to_route(page_name: str) -> str:
    """将 page_name 转为路由路径猜测值"""
    route = page_name.replace("_", "-")
    return f"/{route}"


def _extract_text_hints(trigger: str) -> list[str]:
    """从 trigger 自然语言描述中提取引号内的关键词"""
    patterns = [
        r"['\u2018\u2019]([^'\u2018\u2019]+)['\u2018\u2019]",
        r'["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]',
    ]
    hints: list[str] = []
    for p in patterns:
        hints.extend(re.findall(p, trigger))
    return hints


def _build_page_index(metadata: dict) -> dict[str, dict]:
    """构建 page_name → page info 的索引（取每个 page_name 的第一条记录）"""
    index: dict[str, dict] = {}
    for page in metadata["pages"]:
        name = page["page_name"]
        if name not in index:
            index[name] = page
    return index


def _make_page_ref(page_info: dict) -> PageRef:
    return PageRef(
        page_name=page_info["page_name"],
        route_hint=_page_name_to_route(page_info["page_name"]),
        reference_screenshot=page_info["screenshot"],
    )


# ---------------------------------------------------------------------------
# Type A: 页面存在性测试
# ---------------------------------------------------------------------------

def generate_page_exists_cases(metadata: dict) -> list[TestCase]:
    page_index = _build_page_index(metadata)
    cases: list[TestCase] = []

    for name, page_info in page_index.items():
        if page_info["page_type"] != "main_page":
            continue

        ref = _make_page_ref(page_info)
        cases.append(
            TestCase(
                test_id=f"page_exists__{name}",
                test_type=TestType.PAGE_EXISTS,
                from_page=ref,
                to_page=ref,  # 存在性测试 from == to
                action=Action(
                    description=f"直接访问 {name} 页面",
                    strategy=ActionStrategy.DOM_SELECTOR,
                ),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Type B: Tab 导航测试
# ---------------------------------------------------------------------------

def generate_tab_navigation_cases(metadata: dict) -> list[TestCase]:
    tabs: list[str] = metadata.get("tab_structure", [])
    if not tabs:
        return []

    page_index = _build_page_index(metadata)
    cases: list[TestCase] = []

    tab_labels = {
        "reading_discover": "阅读",
        "bookshelf": "书架",
        "book_friends_feed": "书友",
        "user_profile": "我",
    }

    for from_tab in tabs:
        from_info = page_index.get(from_tab)
        if not from_info:
            continue

        for to_tab in tabs:
            if to_tab == from_tab:
                continue
            to_info = page_index.get(to_tab)
            if not to_info:
                continue

            to_features = to_info.get("structural_features", {})
            tab_pos = to_features.get("tab_position")
            label = tab_labels.get(to_tab, to_tab)

            cases.append(
                TestCase(
                    test_id=f"tab_nav__{from_tab}__to__{to_tab}",
                    test_type=TestType.TAB_NAVIGATION,
                    from_page=_make_page_ref(from_info),
                    to_page=_make_page_ref(to_info),
                    action=Action(
                        description=f"点击底部导航栏第{tab_pos}个Tab'{label}'",
                        strategy=ActionStrategy.TAB_CLICK,
                        tab_position=tab_pos,
                        text_hints=[label],
                        dom_selector_hints=[
                            f"nav a:nth-child({tab_pos})",
                            f"[class*='tab']:nth-child({tab_pos})",
                            f"[class*='tabbar'] > *:nth-child({tab_pos})",
                        ],
                    ),
                )
            )
    return cases


# ---------------------------------------------------------------------------
# Type C: 父子页面跳转测试
# ---------------------------------------------------------------------------

def generate_parent_child_cases(metadata: dict) -> list[TestCase]:
    relationships: list[dict] = metadata.get("page_relationships", [])
    if not relationships:
        return []

    page_index = _build_page_index(metadata)
    cases: list[TestCase] = []

    for rel in relationships:
        parent_name = rel["parent"]
        child_name = rel["page"]
        trigger = rel["trigger"]

        parent_info = page_index.get(parent_name)
        child_info = page_index.get(child_name)
        if not parent_info or not child_info:
            continue

        text_hints = _extract_text_hints(trigger)

        dom_hints: list[str] = []
        for hint in text_hints:
            dom_hints.extend([
                f"button:has-text('{hint}')",
                f"a:has-text('{hint}')",
                f"[role='button']:has-text('{hint}')",
            ])

        cases.append(
            TestCase(
                test_id=f"parent_child__{parent_name}__to__{child_name}",
                test_type=TestType.PARENT_CHILD_NAVIGATION,
                from_page=_make_page_ref(parent_info),
                to_page=_make_page_ref(child_info),
                action=Action(
                    description=trigger,
                    strategy=ActionStrategy.TEXT_MATCH if text_hints else ActionStrategy.VLM_COORDINATE,
                    text_hints=text_hints,
                    dom_selector_hints=dom_hints,
                ),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# 汇总生成
# ---------------------------------------------------------------------------

def generate_all_cases(metadata: dict) -> list[TestCase]:
    cases: list[TestCase] = []
    cases.extend(generate_page_exists_cases(metadata))
    cases.extend(generate_tab_navigation_cases(metadata))
    cases.extend(generate_parent_child_cases(metadata))
    return cases


def generate_from_file(metadata_path: str | Path) -> list[TestCase]:
    metadata = load_metadata(metadata_path)
    return generate_all_cases(metadata)


def export_cases(cases: list[TestCase], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "total_cases": len(cases),
        "by_type": {},
        "cases": [c.to_dict() for c in cases],
    }
    for t in TestType:
        count = sum(1 for c in cases if c.test_type == t)
        if count > 0:
            data["by_type"][t.value] = count

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
