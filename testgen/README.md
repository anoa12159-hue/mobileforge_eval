# TestGen — 基于设计图的自动化导航测试

从 `metadata.json` 自动生成测试用例，通过 Playwright 在浏览器中逐一执行，
验证 AI 生成项目的路由跳转是否正确，并可选地调用 VLM 评估页面与设计图的视觉相似度。

## 架构关系

```
annotator (独立) → metadata.json + test_cases.json（可 --no-testgen 关闭）
testgen   (独立) → generate / run / eval，零依赖于 evaluator
evaluator (独立) → efficiency, build, project_quality, trajectory
                   + navigation_test, navigation_visual（可选，调用 testgen，可 --skip 跳过）
```

TestGen 是完全独立的模块，对 evaluator **零依赖**。可以单独使用，也可以被 evaluator 集成调用。

## 核心理念

每个测试用例是一个 **独立的黑盒测试**：

1. 打开页面 A
2. 截图（对比设计图 A）
3. 触发指定元素（点击 Tab / 按钮 / 图标）
4. 检查是否成功跳转到页面 B
5. 截图（对比设计图 B）

用例之间互不依赖，某一处跳转失败不影响其他用例。**通过率即质量分**。

## 两阶段架构

```
Phase 1: Navigation Testing — testgen run
  ├── 启动 vite preview
  ├── Playwright 逐个执行测试用例
  ├── 截图 + 判定导航是否成功
  └── 输出：test_report.json + test_screenshots/

Phase 2: Visual Evaluation — testgen eval
  ├── 读取 Phase 1 的报告和截图
  ├── 收集所有 (设计图, 实现截图) 对
  ├── 调用 VLM-as-Judge 逐对评分 (layout/style/content/consistency)
  └── 输出：视觉分数写回 test_report.json
```

两个阶段通过独立的 CLI 子命令执行，可以单独运行、重复运行。

## 快速开始

### 前置依赖

```bash
pip install playwright
python -m playwright install chromium
```

### 端到端示例

```bash
# 假设项目结构如下：
#   input/wxreader-4/metadata.json          ← 设计规格 + 参考截图
#   output/wxreader-4__agentic__claude-opus-4-6/  ← AI 生成的前端项目

# ① 生成测试用例（输出到 input 目录，和 metadata.json 放一起）
python -m testgen generate \
  input/wxreader-4/metadata.json \
  -o input/wxreader-4/test_cases.json

# ② 执行导航测试（Phase 1）
python -m testgen run \
  --cases input/wxreader-4/test_cases.json \
  --run-dir output/wxreader-4__agentic__claude-opus-4-6 \
  --input-dir input/wxreader-4

# ③ 执行视觉评估（Phase 2，可选）
python -m testgen eval \
  --report output/wxreader-4__agentic__claude-opus-4-6/eval/test_report.json \
  --cases input/wxreader-4/test_cases.json \
  --input-dir input/wxreader-4
```

## CLI 参考

### `testgen generate` — 生成测试用例

```
python -m testgen generate <metadata.json> [-o OUTPUT]
```


| 参数             | 必填  | 说明                                      |
| -------------- | --- | --------------------------------------- |
| `metadata`     | 是   | `metadata.json` 文件路径（位置参数）              |
| `-o, --output` | 否   | 输出 `test_cases.json` 的路径，不指定则输出到 stdout |


从 `metadata.json` 中提取页面信息和跳转关系，自动生成三类测试用例：


| 类型                        | 来源                     | 说明                 |
| ------------------------- | ---------------------- | ------------------ |
| `page_exists`             | `pages[]` 中的 main_page | 直接访问路由，验证页面能正常加载   |
| `tab_navigation`          | `tab_structure` 的全排列   | 从 Tab A 点击跳到 Tab B |
| `parent_child_navigation` | `page_relationships[]` | 点击触发元素，跳到子页面/弹窗    |


### `testgen run` — 导航测试（Phase 1）

```
python -m testgen run --cases CASES --run-dir RUN_DIR --input-dir INPUT_DIR
                      [--report REPORT] [--vlm-model VLM_MODEL] [--no-vlm-fallback]
```


| 参数                  | 必填  | 说明                                          |
| ------------------- | --- | ------------------------------------------- |
| `--cases`           | 是   | `test_cases.json` 路径（`generate` 的输出）        |
| `--run-dir`         | 是   | AI 生成项目的目录（包含 `src/`、`package.json` 等）      |
| `--input-dir`       | 是   | 输入目录，包含设计参考截图                               |
| `--report`          | 否   | 报告输出路径，默认 `<run-dir>/eval/test_report.json` |
| `--vlm-model`       | 否   | 覆盖 VLM 兜底模型（默认使用 `testgen.fallback_model`）  |
| `--no-vlm-fallback` | 否   | 禁用 VLM 点击兜底，仅使用 DOM/文本定位                    |


**VLM 点击兜底默认开启。** 当 DOM/文本定位失败时，系统自动截图发给 VLM 获取点击坐标。
模型默认读取 `experiment.yaml` 中的 `testgen.fallback_model`，也可以用 `--vlm-model` 覆盖。
如果不想调 API，传 `--no-vlm-fallback` 关闭。

`**run` 只做导航测试，不做视觉评估。** 产出 `test_report.json`（导航通过/失败）和 `test_screenshots/`（截图）。

示例：

```bash
# 默认行为：DOM/文本 + VLM 兜底（使用 config 中的模型）
python -m testgen run \
  --cases input/wxreader-4/test_cases.json \
  --run-dir output/wxreader-4__agentic__claude-opus-4-6 \
  --input-dir input/wxreader-4

# 指定其他 VLM 模型
python -m testgen run \
  --cases input/wxreader-4/test_cases.json \
  --run-dir output/wxreader-4__agentic__claude-opus-4-6 \
  --input-dir input/wxreader-4 \
  --vlm-model gpt-4o

# 禁用 VLM 兜底，纯 DOM/文本定位（不调 API）
python -m testgen run \
  --cases input/wxreader-4/test_cases.json \
  --run-dir output/wxreader-4__agentic__claude-opus-4-6 \
  --input-dir input/wxreader-4 \
  --no-vlm-fallback
```

### `testgen eval` — 视觉评估（Phase 2）

```
python -m testgen eval --report REPORT --cases CASES --input-dir INPUT_DIR
                       [--vlm-model VLM_MODEL] [--workers N]
```


| 参数            | 必填  | 说明                                              |
| ------------- | --- | ----------------------------------------------- |
| `--report`    | 是   | `run` 阶段生成的 `test_report.json` 路径               |
| `--cases`     | 是   | `test_cases.json` 路径（同 `run` 使用的文件）             |
| `--input-dir` | 是   | 输入目录，包含设计参考截图                                   |
| `--vlm-model` | 否   | 覆盖 VLM 评分模型（默认使用 `testgen.eval_model`）          |
| `--workers`   | 否   | VLM 评分并发线程数（默认使用 `testgen.eval_workers`，兜底 8） |


`**eval` 读取 `run` 已经生成的报告和截图**，批量调 VLM 对每对 (设计图, 实现截图) 评分，
把视觉分数写回 `test_report.json`。

核心优势：

- **不需要重跑导航测试**，直接复用已有截图
- **可以随时换模型重评**，例如先用 `gemini-2.5-pro` 评一遍，再用 `gpt-4o` 评一遍对比
- **可重复执行**，新的评分会覆盖旧的

示例：

```bash
# 用 gemini 评分
python -m testgen eval \
  --report output/wxreader-4__agentic__claude-opus-4-6/eval/test_report.json \
  --cases input/wxreader-4/test_cases.json \
  --input-dir input/wxreader-4 \
  --vlm-model gemini-2.5-pro

# 换 gpt-4o 重新评分（覆盖之前的结果）
python -m testgen eval \
  --report output/wxreader-4__agentic__claude-opus-4-6/eval/test_report.json \
  --cases input/wxreader-4/test_cases.json \
  --input-dir input/wxreader-4 \
  --vlm-model gpt-4o
```

### 通用选项


| 参数              | 说明              |
| --------------- | --------------- |
| `-v, --verbose` | 输出详细日志（放在子命令前面） |


```bash
python -m testgen -v run --cases ... --run-dir ... --input-dir ...
```

## 产出文件

```
<run-dir>/eval/
├── test_report.json          ← 测试报告（导航结果 + 视觉评分）
└── test_screenshots/         ← 截图目录
    ├── page_exists__reading_discover__before.png
    ├── tab_nav__bookshelf__to__user_profile__before.png
    ├── tab_nav__bookshelf__to__user_profile__after.png
    └── ...
```

**截图数量 ≠ 测试用例数 × 2**。不同类型的用例产生的截图数量不同：

| 类型                        | 截图规则                                          | 每个用例产生文件数 |
| ------------------------- | --------------------------------------------- | --------- |
| `page_exists`             | 只截 before（from 和 to 是同一个页面，无导航动作）             | 1         |
| `tab_navigation`          | 截 before + after                               | 2         |
| `parent_child_navigation` | 截 before + after                               | 2         |

例如 18 个用例（4 page_exists + 12 tab_nav + 2 parent_child）产生的截图文件数为
4×1 + 12×2 + 2×2 = **32** 个，而非 18×2 = 36 个。

## Action 定位策略

测试用例中的 `action` 描述如何在页面上定位并点击目标元素。按优先级：


| 策略               | 适用场景        | 方法                                                             |
| ---------------- | ----------- | -------------------------------------------------------------- |
| `tab_click`      | 底部 Tab 导航   | `dom_selector_hints` → `text_hints` → `tab_position` nth-child |
| `text_match`     | 有明确文字的按钮/链接 | DOM `:has-text()` 选择器 → `get_by_role()` → `get_by_text()`      |
| `dom_selector`   | 已知 CSS 选择器  | 逐个尝试 `dom_selector_hints` 列表                                   |
| `vlm_coordinate` | 无文字的图标按钮    | 截图 + 自然语言描述 → VLM 返回 (x, y) 坐标                                 |


**自动兜底机制**（默认开启）：如果主策略（tab_click / text_match / dom_selector）全部失败，
系统会自动 fallback 到 `vlm_coordinate`。传 `--no-vlm-fallback` 可关闭：

```
tab_click 失败
  → text_hints 失败
    → nth-child 失败
      → [自动 fallback] vlm_coordinate
        → VLM 看截图，返回坐标 (x=35, y=28)
          → page.mouse.click(35, 28)
```

## 路由映射

生成器根据 `page_name`（如 `book_friends_feed`）猜测路由（`/book-friends-feed`），
但实际路由可能不同（`/friends`）。

Runner 在执行前会解析 `src/router.tsx`，提取组件名到路由的映射：

```
ReadingDiscover  → /
Bookshelf        → /bookshelf
BookFriendsFeed  → /friends       (不是 /book-friends-feed)
UserProfile      → /profile       (不是 /user-profile)
Notifications    → /notifications
```

通过 **组件名归一化匹配**（`book_friends_feed` ↔ `BookFriendsFeed`）自动覆盖
test_case 中的 `route_hint`。

## 视觉评分详情

Phase 2 使用 VLM-as-Judge（`testgen/visual_judge.py`），从四个维度评分：


| 维度          | 权重  | 评分标准             |
| ----------- | --- | ---------------- |
| Layout      | 30% | 空间布局：位置、大小、对齐、间距 |
| Style       | 25% | 视觉风格：颜色、字体、圆角、阴影 |
| Content     | 25% | 内容完整性：文字、图片、数据项  |
| Consistency | 20% | 跨页面一致性：导航、共享组件   |


每个维度 1-5 分，加权后得到综合分（满分 5.0）。

## 推荐模型

两个阶段对 VLM 的能力要求不同：

- **点击兜底（`run`）**：需要 **GUI grounding** 能力 —— 看截图定位元素坐标，对空间理解要求高，对语义理解要求低
- **视觉评估（`eval`）**：需要 **设计评审** 能力 —— 对比设计图和实现截图，从布局/风格/内容/一致性多维度打分，对细节感知和审美判断要求高

以下模型均已注册在 `models.yaml` 中，价格以该文件为准（$/M tokens）。

### 点击兜底（`testgen run --vlm-model`）


| 厂商        | 模型                 | 输入 / 输出       | GUI 定位能力 | 推荐度    |
| --------- | ------------------ | ------------- | -------- | ------ |
| Google    | `gemini-2.5-flash` | $0.30 / $2.50 | 很好       | **首选** |
| Google    | `gemini-2.0-flash` | $0.10 / $0.40 | 好        | 极低成本备选 |
| Google    | `gemini-2.5-pro`   | $1.25 / $10   | 极好       | 精度优先   |
| OpenAI    | `gpt-4.1-mini`     | $0.40 / $1.60 | 好        | 备选     |
| OpenAI    | `gpt-4o`           | $2.50 / $10   | 好        | 备选     |
| OpenAI    | `gpt-4o-mini`      | $0.15 / $0.60 | 中等偏弱     | 不推荐    |
| Anthropic | `claude-haiku-4-5` | $1.00 / $5.00 | 中等       | 不推荐    |
| Anthropic | `claude-sonnet-4`  | $3.00 / $15   | 好        | 性价比低   |


**推荐 `gemini-2.5-flash`**：Gemini 系列原生支持 bounding box 输出，GUI grounding
是训练层面的能力而非 prompt hack。flash 版本成本低，定位精度接近 pro。
每次兜底仅一次调用（约 1000-2000 input tokens），单次成本极低。

### 视觉评估（`testgen eval --vlm-model`）


| 厂商        | 模型                 | 输入 / 输出       | 评审能力 | 推荐度       |
| --------- | ------------------ | ------------- | ---- | --------- |
| Google    | `gemini-2.5-pro`   | $1.25 / $10   | 极好   | **首选**    |
| Google    | `gemini-2.5-flash` | $0.30 / $2.50 | 好    | **低成本首选** |
| OpenAI    | `gpt-4o`           | $2.50 / $10   | 极好   | 备选        |
| OpenAI    | `gpt-4.1`          | $2.00 / $8.00 | 极好   | 备选        |
| OpenAI    | `gpt-4.1-mini`     | $0.40 / $1.60 | 中等偏好 | 低成本备选     |
| OpenAI    | `gpt-4o-mini`      | $0.15 / $0.60 | 中等   | 不推荐       |
| Anthropic | `claude-sonnet-4`  | $3.00 / $15   | 极好   | 备选        |
| Anthropic | `claude-haiku-4-5` | $1.00 / $5.00 | 中等   | 不推荐       |


**推荐 `gemini-2.5-pro`**：视觉评估每对截图需要详细的多维度分析，对细节感知
和结构化输出要求高，pro 级别模型明显优于 mini/flash。如果测试用例多（>50 对）
且对成本敏感，可用 `gemini-2.5-flash` 替代，准确度略降但成本低一个数量级。

### 成本估算

假设 18 个测试用例，每个用例产生 before + after 两张截图：


| 场景            | 模型                 | 调用次数   | 估算成本    |
| ------------- | ------------------ | ------ | ------- |
| 点击兜底（仅失败用例触发） | `gemini-2.5-flash` | ~2-5 次 | < $0.01 |
| 视觉评估（全部截图对）   | `gemini-2.5-pro`   | ~35 次  | ~$0.10  |
| 视觉评估（全部截图对）   | `gemini-2.5-flash` | ~35 次  | ~$0.02  |


## 报告格式

```json
{
  "evaluated_at": "2026-04-21T10:05:38",
  "visual_judge_model": "gemini-2.5-pro",
  "total_cases": 18,
  "passed": 17,
  "failed": 1,
  "pass_rate": 0.9444,
  "by_type": {
    "page_exists": {"total": 4, "passed": 4, "rate": 1.0},
    "tab_navigation": {"total": 12, "passed": 12, "rate": 1.0},
    "parent_child_navigation": {"total": 2, "passed": 1, "rate": 0.5}
  },
  "visual_summary": {
    "model": "gemini-2.5-pro",
    "scored_pairs_before": 18,
    "scored_pairs_after": 17,
    "avg_before": 3.85,
    "avg_after": 3.72
  },
  "details": [...]
}
```

`visual_summary` 和 `visual_judge_model` 仅在执行 `eval` 后出现。

## 文件结构

```
testgen/
├── __init__.py
├── __main__.py          # python -m testgen 入口
├── cli.py               # 命令行：generate / run / eval 子命令
├── models.py            # 数据模型 (TestCase, TestResult, ...)
├── generator.py         # 从 metadata.json 生成测试用例
├── route_resolver.py    # 解析 router.tsx，page_name → 实际路由
├── actions.py           # Action 执行策略 + VLM 坐标兜底
├── runner.py            # 核心执行器（Phase 1 导航 + Phase 2 视觉评估）
├── visual_judge.py      # VLM-as-Judge 视觉打分（独立于 evaluator）
└── README.md            # 本文件
```

## 配置

TestGen 的模型配置集中在项目根目录的 `experiment.yaml`，和主项目共用同一个配置文件：

```yaml
# experiment.yaml
testgen:
  # Phase 1 (run): VLM 点击兜底模型
  fallback_model: gemini-2.5-flash

  # Phase 2 (eval): VLM 视觉评估模型
  eval_model: gemini-2.5-pro

  # Phase 2 (eval): VLM 评分并发线程数（默认 8）
  # eval_workers: 8
```

**优先级**：CLI 参数 > `experiment.yaml` 配置。不传参数时自动使用配置值。


| 配置项                      | 对应 CLI             | 用途                        |
| ------------------------ | ------------------ | ------------------------- |
| `testgen.fallback_model` | `run --vlm-model`  | 点击兜底：DOM/文本定位失败时 VLM 返回坐标 |
| `testgen.eval_model`     | `eval --vlm-model` | 视觉评估：对比设计图和实现截图打分         |
| `testgen.eval_workers`   | `eval --workers`   | VLM 评分并发线程数，默认 8          |


## 依赖

- `playwright` — 浏览器自动化（需 `python -m playwright install chromium`）
- `designforge` — 配置读取 + LLM 调用（VLM 点击兜底和视觉评估需要）

**注意**：TestGen 对 evaluator **零依赖**，可以完全独立使用。

