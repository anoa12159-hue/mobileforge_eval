"""测试用例数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TestType(str, Enum):
    PAGE_EXISTS = "page_exists"
    TAB_NAVIGATION = "tab_navigation"
    PARENT_CHILD_NAVIGATION = "parent_child_navigation"


class ActionStrategy(str, Enum):
    TAB_CLICK = "tab_click"
    TEXT_MATCH = "text_match"
    DOM_SELECTOR = "dom_selector"
    VLM_COORDINATE = "vlm_coordinate"


@dataclass
class PageRef:
    page_name: str
    route_hint: str
    reference_screenshot: str  # filename relative to input dir

    def screenshot_path(self, input_dir: Path) -> Path:
        return input_dir / self.reference_screenshot


@dataclass
class Action:
    description: str
    strategy: ActionStrategy
    tab_position: int | None = None
    text_hints: list[str] = field(default_factory=list)
    dom_selector_hints: list[str] = field(default_factory=list)


@dataclass
class Assertions:
    navigation_success_weight: float = 0.4
    visual_before_weight: float = 0.3
    visual_after_weight: float = 0.3
    visual_threshold: float = 0.5


@dataclass
class TestCase:
    test_id: str
    test_type: TestType
    from_page: PageRef
    to_page: PageRef
    action: Action
    assertions: Assertions = field(default_factory=Assertions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "type": self.test_type.value,
            "from_page": {
                "page_name": self.from_page.page_name,
                "route_hint": self.from_page.route_hint,
                "reference_screenshot": self.from_page.reference_screenshot,
            },
            "action": {
                "description": self.action.description,
                "strategy": self.action.strategy.value,
                "tab_position": self.action.tab_position,
                "text_hints": self.action.text_hints,
                "dom_selector_hints": self.action.dom_selector_hints,
            },
            "to_page": {
                "page_name": self.to_page.page_name,
                "route_hint": self.to_page.route_hint,
                "reference_screenshot": self.to_page.reference_screenshot,
            },
            "assertions": {
                "navigation_success_weight": self.assertions.navigation_success_weight,
                "visual_before_weight": self.assertions.visual_before_weight,
                "visual_after_weight": self.assertions.visual_after_weight,
                "visual_threshold": self.assertions.visual_threshold,
            },
        }


@dataclass
class TestResult:
    test_id: str
    passed: bool
    navigation_success: bool
    visual_similarity_before: float | None = None
    visual_similarity_after: float | None = None
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    error: str | None = None
