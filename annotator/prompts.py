"""
Prompt templates for the annotator pipeline.

Two-phase annotation:
  1. DESCRIBE_SINGLE_PROMPT — per-screenshot structured descriptor
  2. ANALYZE_APP_PROMPT    — cross-screenshot global app metadata
"""

DESCRIBE_SINGLE_PROMPT = """\
You are an expert mobile app UI analyst. You will receive a single screenshot \
from a mobile application.

Analyze the screenshot and output a structured JSON descriptor with the \
following fields:

- **page_name**: A short, lowercase English identifier for this page \
(e.g. "home", "bookshelf", "settings", "search_results"). Use snake_case.
- **page_type**: One of:
  - "main_page" — an independent top-level page with its own route
  - "sub_page" — a page navigated to from a main page
  - "overlay" — a popup, dialog, bottom sheet, or modal
  - "state_variant" — a different state of an existing page (e.g. empty state, loading)
- **description**: A concise Chinese description of what this page shows and its purpose.
- **keywords**: 5-8 Chinese keywords that characterize this page's content and function.
- **visual_anchors**: 3-6 specific, observable visual elements that uniquely identify \
this page. Be concrete — mention exact text labels, icon positions, layout patterns. \
Use Chinese.
- **structural_features**: An object with:
  - "has_tab_bar" (bool): whether a bottom tab/navigation bar is visible
  - "tab_position" (int|null): if has_tab_bar is true, which tab is highlighted \
(1-indexed from left); null if no tab bar
  - "has_search_bar" (bool): whether a search input is visible
  - "has_header" (bool): whether a top header/title bar is visible
  - "primary_layout" (string): the dominant content layout — one of \
"vertical_scroll", "grid", "list", "card", "form", "bottom_sheet", "modal", "tabs", "other"
  - "has_floating_action" (bool): whether a floating action button is visible

Output ONLY valid JSON, no additional text. Example format:

```json
{
  "page_name": "bookshelf",
  "page_type": "main_page",
  "description": "书架页面，展示用户收藏的书籍，网格布局排列书籍封面",
  "keywords": ["书架", "收藏", "书籍", "网格", "封面"],
  "visual_anchors": [
    "顶部标题文字'书架'",
    "书籍封面网格布局（3列）",
    "底部TabBar第二个tab高亮",
    "右上角'...'更多按钮"
  ],
  "structural_features": {
    "has_tab_bar": true,
    "tab_position": 2,
    "has_search_bar": false,
    "has_header": true,
    "primary_layout": "grid",
    "has_floating_action": false
  }
}
```"""

ANALYZE_APP_PROMPT = """\
You are an expert mobile app analyst. You will receive ALL screenshots from a \
single mobile application, along with their individual descriptors (produced \
by a previous analysis pass).

Your task is to produce a **global app-level metadata** object by analyzing \
the screenshots holistically. Consider:

1. **App identity**: Identify the app name (in its original language) and category.
2. **Tab structure**: If the app has a bottom tab bar, list all tabs in order \
(left to right) using the page_name from the descriptors.
3. **Page relationships**: For overlay/sub_page types, identify which main_page \
is their parent and what user action triggers the navigation.
4. **Consistency check**: Verify and correct page_type assignments based on \
the global view — e.g. if two screenshots share the same tab bar state, they \
might be the same page in different states.

Output ONLY valid JSON with the following structure:

```json
{
  "app_name": "微信读书",
  "category": "reading",
  "tab_structure": ["home", "bookshelf", "community", "profile"],
  "total_main_pages": 5,
  "total_overlays": 2,
  "page_relationships": [
    {"page": "import_popup", "parent": "bookshelf", "trigger": "点击导入按钮"},
    {"page": "book_detail", "parent": "home", "trigger": "点击书籍卡片"}
  ]
}
```

Categories: "reading", "social", "shopping", "finance", "media", \
"productivity", "health", "education", "travel", "food", "utility", "other".

Here are the individual page descriptors from Phase 1:

{descriptors_json}

Now analyze all screenshots together and produce the global metadata."""
