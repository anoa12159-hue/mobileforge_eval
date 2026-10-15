# Annotator — VLM 数据集标注器

基于 VLM（视觉语言模型）的 App 截图结构化标注工具。为输入的设计截图生成 `metadata.json` 描述文件，并自动生成导航测试用例 `test_cases.json`，供下游 testgen 和 evaluator 使用。

## 架构关系

```
annotator (独立) → metadata.json + test_cases.json（可 --no-testgen 关闭）
testgen   (独立) → generate / run / eval，零依赖于 evaluator
evaluator (独立) → efficiency, build, project_quality, trajectory
                   + navigation_test, navigation_visual（可选，调用 testgen，可 --skip 跳过）
```

Annotator 在标注完成后会自动调用 testgen 的 `generate` 功能，从 `metadata.json` 生成 `test_cases.json`。这是可选的——使用 `--no-testgen` 可关闭自动生成。

## 目录结构

```
annotator/
├── __main__.py      # CLI 入口
├── __init__.py      # 包导出
├── describe.py      # Phase 1：单图结构化描述
├── analyze_app.py   # Phase 2：全局应用分析
├── assemble.py      # 组装 metadata.json
└── prompts.py       # VLM 提示词模板
```

## 快速开始

```bash
# 标注单个 app 目录（自动生成 metadata.json + test_cases.json）
python -m annotator --input input/wxreader-4

# 批量标注 input/ 下所有子目录
python -m annotator --input input --batch

# 指定模型
python -m annotator --input input/wxreader-4 --model gemini-2.5-pro

# 覆盖已有标注
python -m annotator --input input/wxreader-4 --force

# 只标注，不自动生成测试用例
python -m annotator --input input/wxreader-4 --no-testgen

# 只做单图描述，跳过全局分析
python -m annotator --input input/wxreader-4 --describe-only
```

## CLI 参数

| 参数 | 说明 |
|------|------|
| `--input`, `-i` | 单 app 目录路径（`--batch` 时为父目录；默认读取 `paths.input_dir`） |
| `--batch` | 批量模式：处理输入目录下所有非隐藏子目录 |
| `--model`, `-m` | VLM 模型名称（默认读取 `experiment.yaml` 的 `annotator.model`） |
| `--force` | 覆盖已存在的 `metadata.json` |
| `--describe-only` | 只运行 Phase 1 单图描述，跳过 Phase 2 全局分析 |
| `--no-testgen` | 标注完成后不自动生成 `test_cases.json` |
| `--verbose`, `-v` | 开启 DEBUG 日志 |

## 工作流程

标注分为两个阶段：

### Phase 1：单图描述（`describe.py`）

对输入目录中的每张截图，逐一调用 VLM 生成结构化描述，包括：

| 字段 | 说明 |
|------|------|
| `page_name` | 页面名称（英文标识） |
| `page_type` | 页面类型：`main_page` / `sub_page` / `overlay` / `state_variant` |
| `description` | 页面功能描述（中文） |
| `keywords` | 关键词列表 |
| `visual_anchors` | 视觉锚点（标题文字、图标等，用于页面匹配） |
| `structural_features` | 结构特征（布局类型、导航模式等） |

### Phase 2：全局分析（`analyze_app.py`）

将所有截图 + Phase 1 的描述结果一起发送给 VLM，提取全局信息：

| 字段 | 说明 |
|------|------|
| `app_name` | 应用名称 |
| `category` | 应用类别 |
| `tab_structure` | Tab 结构 |
| `page_relationships` | 页面间导航关系（parent / trigger） |

### 组装输出（`assemble.py`）

合并两阶段结果，写出 `metadata.json`。如果 Phase 2 未运行或失败，会使用占位默认值（`app_name: "unknown"` 等）。

## 输入要求

- 输入目录下需要有图片文件
- 支持格式：`.jpg`、`.jpeg`、`.png`、`.webp`、`.gif`
- 图片按文件名排序处理

## 输出格式（metadata.json）

```json
{
  "app_name": "微信读书",
  "category": "reading",
  "tab_structure": ["书架", "发现", "我"],
  "pages": [
    {
      "screenshot": "01_bookshelf.png",
      "page_name": "bookshelf",
      "page_type": "main_page",
      "description": "书架页面，展示用户的书籍收藏...",
      "keywords": ["书架", "书籍", "收藏"],
      "visual_anchors": ["书架", "搜索图标", "导航栏"],
      "structural_features": { ... },
      "parent": null,
      "trigger": null
    },
    ...
  ],
  "page_relationships": [
    {
      "from": "bookshelf",
      "to": "book_detail",
      "trigger": "点击书籍封面"
    }
  ],
  "annotation": {
    "model": "gemini-2.5-pro",
    "annotated_at": "2025-04-20T14:30:00",
    "annotator_version": "0.1"
  }
}
```

## 与 TestGen / Evaluator 的关系

Annotator 的输出驱动整条评测流水线：

```
annotator → metadata.json → testgen generate → test_cases.json
                                                    ↓
            run_dir/ ←──── testgen run ← test_cases.json
                                ↓
                           test_report.json ← testgen eval
                                ↓
                         evaluator（navigation_test + navigation_visual）
```

- **`metadata.json`** 是 testgen 生成测试用例的唯一输入
- **`test_cases.json`** 是 evaluator 中 `navigation_test` 模块的前置条件
- 如果设计图目录下缺少这两个文件，evaluator 的导航测试和视觉评分模块会自动跳过

## 编程接口

也可在 Python 代码中直接调用：

```python
from annotator import describe_screenshot, analyze_app, assemble_metadata, write_metadata

# Phase 1：描述单张截图
descriptor = describe_screenshot(
    "input/wxreader-4/Screenshot_2026-03-22-15-03-55-27.jpg",
    "gemini-2.5-pro",
)

# Phase 2：全局分析
image_paths = ["input/wxreader-4/Screenshot_01.jpg", "input/wxreader-4/Screenshot_02.jpg"]
app_analysis = analyze_app(image_paths, descriptors, "gemini-2.5-pro")

# 组装并写出
metadata = assemble_metadata(image_paths, descriptors, app_analysis, "gemini-2.5-pro")
write_metadata("input/wxreader-4", metadata)
```

## 配置

Annotator 的模型配置集中在项目根目录的 `experiment.yaml`，和主项目共用同一个配置文件：

```yaml
# experiment.yaml
annotator:
  # VLM 标注模型（须 supports_images: true）
  # 推荐 gemini-2.5-flash：视觉理解强、JSON 输出稳定、成本极低
  model: gemini-2.5-flash

  # Phase 1 并发数：单图描述的并行线程数（默认 8）
  workers: 8
```

**优先级**：CLI `--model` 参数 > `experiment.yaml` 配置。不传参数时自动使用配置值。

| 配置项 | 对应 CLI | 用途 |
|--------|----------|------|
| `annotator.model` | `--model` | Phase 1 单图描述 + Phase 2 全局分析使用的 VLM 模型 |
| `annotator.workers` | — | Phase 1 单图描述的并发线程数 |

## 容错机制

- **单图失败**：该图的 descriptor 会带 `error` 字段，但不影响其他图片的处理
- **Phase 2 失败**：仍会输出 `metadata.json`，但全局字段使用默认值
- **已有标注**：默认跳过已有 `metadata.json` 的目录（使用 `--force` 覆盖）

## 依赖

- Python 3.10+
- `designforge`（LLM 调用与图像编码）
- `rich`（终端输出）
