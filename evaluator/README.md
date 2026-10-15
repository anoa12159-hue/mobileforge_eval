# Evaluator — 实验评测与报告生成

实验评测模块，支持端到端的「生成 → 评测 → 报告」流程。可通过 CLI 运行完整实验，也可单独评测已有的 run 目录。

## 架构关系

Evaluator 与 Annotator、TestGen 三个模块各自独立运行，互不强依赖：

```
annotator (独立) → metadata.json + test_cases.json（可 --no-testgen 关闭）
testgen   (独立) → generate / run / eval，零依赖于 evaluator
evaluator (独立) → efficiency, build, project_quality, trajectory
                   + navigation_test, navigation_visual（可选，调用 testgen，可 --skip 跳过）
```

典型流水线：

```
1. annotator   标注设计图 → metadata.json + test_cases.json
2. designforge 生成代码   → output/{run_dir}/
3. evaluator   一站式评测  → eval/ 下所有指标 + summary.json
```

Evaluator 在运行 `navigation_test` 和 `navigation_visual` 时会调用 testgen 的函数，
但这是可选的——如果 `test_cases.json` 不存在或被 `--skip` 跳过，这两个模块会优雅地跳过，
其余评测模块不受影响。

## 目录结构

```
evaluator/
├── __main__.py          # CLI 入口（run / eval / report / status）
├── config.py            # 实验配置加载（exp_config.yaml → ExperimentConfig）
├── experiment.py         # 实验目录管理、矩阵展开、status.json 状态机
├── runner.py            # 批量编排：生成 + 评测 + 报告
├── utils.py             # 工具函数（JSON 读写、input_dir 推断等）
├── evaluate/            # 评测模块
│   ├── pipeline.py      # 评测流水线（按依赖顺序调度所有评测器）
│   ├── build.py         # 构建检查（tsc + vite build）
│   ├── efficiency.py    # Token / 调用效率分析
│   ├── project_quality.py  # 代码质量与规模
│   └── trajectory.py    # 轨迹分析 + token 组成图表
└── report/              # 报告生成
    ├── aggregator.py    # 跨 run 指标聚合
    ├── export_tables.py # 导出 CSV 表格
    └── table.py         # 生成 Markdown 对比表
```

## 快速开始

```bash
# 1. 运行完整实验（生成 + 评测 + 报告）
python -m evaluator run --config exp_config.yaml

# 2. 对已有 run 目录进行评测（含导航测试 + 视觉评分）
python -m evaluator eval \
  --dir output/wxreader-4__agentic__claude-opus-4-6 \
  --input input/wxreader-4

# 3. 批量评测 output/ 下所有 run
python -m evaluator eval --dir output --input input --batch

# 4. 只跑代码质量，跳过导航测试
python -m evaluator eval \
  --dir output/wxreader-4__agentic__claude-opus-4-6 \
  --skip navigation_test,navigation_visual

# 5. 只跑导航测试
python -m evaluator eval \
  --dir output/wxreader-4__agentic__claude-opus-4-6 \
  --input input/wxreader-4 \
  --only navigation_test,navigation_visual

# 6. 生成实验报告
python -m evaluator report --experiment-dir experiments/20250420_143000_my_experiment

# 7. 查看实验状态
python -m evaluator status experiments/20250420_143000_my_experiment
```

## CLI 子命令详解

### `run` — 运行完整实验

读取 `exp_config.yaml`，展开 apps × models × methods 矩阵，依次生成代码并评测。

```bash
python -m evaluator run --config exp_config.yaml [选项]

# 示例
python -m evaluator run --config exp_config.yaml -v
python -m evaluator run --config exp_config.yaml --resume --skip navigation_visual
```

| 参数 | 说明 |
|------|------|
| `--config`, `-c` | **必填**，实验配置文件路径 |
| `--resume` | 恢复最近一次同名实验（跳过已完成的 run） |
| `--skip <modules>` | 逗号分隔，跳过指定评测模块 |
| `--only <modules>` | 逗号分隔，只运行指定评测模块 |
| `--force` | 覆盖已有的评测结果 |
| `--visual-judge-model <model>` | VLM 裁判模型（默认 `gemini-2.5-pro`） |
| `--no-plot` | 不生成 token 组成图表 |
| `--x-axis {iteration,phase,both}` | 图表 x 轴模式（默认 `both`） |
| `--verbose`, `-v` | 开启 DEBUG 日志 |

### `eval` — 评测已有 run

对已经生成代码的 run 目录执行评测，不触发代码生成。

```bash
python -m evaluator eval --dir path/to/run [选项]

# 示例
python -m evaluator eval \
  --dir output/wxreader-4__agentic__claude-opus-4-6 \
  --input input/wxreader-4

# 批量评测 + 强制覆盖
python -m evaluator eval --dir output --input input --batch --force
```

| 参数 | 说明 |
|------|------|
| `--dir`, `-d` | **必填**，run 目录或（`--batch` 时）父目录 |
| `--input`, `-i` | 输入目录（设计图所在目录），用于导航测试和视觉评分 |
| `--batch` | 批量评测 `--dir` 下所有子目录 |
| `--skip`, `--only`, `--force` | 同 `run` 子命令 |
| `--visual-judge-model` | VLM 裁判模型（默认 `gemini-2.5-pro`） |
| `--no-plot` | 不生成 token 组成图表 |
| `--x-axis` | 图表 x 轴模式 |

### `report` — 生成报告

聚合实验目录下所有 run 的评测结果，输出 CSV 表格、Markdown 对比表和 token 对比图。

```bash
python -m evaluator report --experiment-dir experiments/20250420_143000_my_experiment
```

输出文件（在 `experiment_dir/report/` 下）：
- `summary.json` — 聚合后的完整结果
- `tables/*.csv` — 分维度 CSV 表格（`all_runs`、`by_model`、`by_policy` 等）
- `comparison_table.md` — Markdown 对比大表
- `token_composition_comparison.png` — 跨 run token 组成对比图

### `status` — 查看状态

```bash
python -m evaluator status path/to/experiment
```

## 评测模块与依赖链

评测按照以下依赖顺序执行（箭头表示依赖关系）：

```
efficiency           （独立）
build                （独立）
project_quality      （独立）
trajectory           （独立）
navigation_test      → 依赖 build 通过 + input_dir/test_cases.json
navigation_visual    → 依赖 navigation_test 完成
```

### 各模块说明

| 模块 | 输出文件 | 说明 |
|------|---------|------|
| `efficiency` | `eval/efficiency.json` | Token 用量、LLM 调用次数、耗时、成本估算 |
| `build` | `eval/build_result.json` | tsc + vite build 是否通过、路由数、页面数、路由覆盖率 |
| `project_quality` | `eval/project_quality.json` | 代码行数、文件数、组件复用率、Tailwind 一致性 |
| `trajectory` | `eval/trajectory.json` | 阶段分析、行为模式（自修复次数等）、token 归因 |
| `navigation_test` | `eval/test_report.json` | 导航测试通过率（调用 testgen Phase 1） |
| `navigation_visual` | `eval/navigation_visual.json` | 视觉还原评分，同时更新 test_report.json（调用 testgen Phase 2） |

### navigation_test / navigation_visual 的前置条件

这两个模块通过调用 testgen 来执行导航测试和视觉评分。运行需要满足：

1. **build 通过** — 代码能成功构建（`dist/` 存在）
2. **提供 `--input`** — 指向原始设计图目录
3. **`test_cases.json` 存在** — 设计图目录下需要有测试用例文件（由 annotator 自动生成，或手动 `python -m testgen generate`）
4. **testgen 包可导入** — testgen 模块需要在 Python path 中

任何一项不满足，对应模块会被跳过，不影响其余评测。使用 `--verbose` 可以查看具体跳过原因。

## 评测指标说明

### 效率指标（efficiency）

| 指标 | 含义 |
|------|------|
| `total_tokens` | 总 token 消耗（input + output） |
| `llm_calls` | LLM 调用次数 |
| `llm_time_sec` | LLM 调用累计耗时 |
| `estimated_cost_usd` | 估算费用（基于 models.yaml 中的定价） |

### 构建指标（build）

| 指标 | 含义 |
|------|------|
| `build_passed` | tsc + vite build 是否全部通过 |
| `route_count` | router.tsx 中定义的路由数 |
| `page_count` | src/pages/ 下的页面文件数 |
| `route_coverage` | 路由与页面文件的覆盖率（0-1） |

### 代码质量指标（project_quality）

| 指标 | 含义 |
|------|------|
| `total_loc` | 代码总行数 |
| `file_count` | 源文件总数 |
| `reuse_rate` | 组件复用率——共享组件被引用的比例 |
| `color_consistency` | Tailwind 颜色 token 一致性 |

### 导航测试指标（navigation_test）

| 指标 | 含义 | 性质 |
|------|------|------|
| `nav_pass_rate` | 导航测试通过率 | **纯交互指标**——衡量点击后是否正确跳转 |
| `nav_passed` / `nav_total` | 通过数 / 总数 | 按用例类型可细分 |

通过率基于 URL 变化或页面内容 hash 变化来判定。不依赖 VLM，是纯 Playwright 自动化结果。

### 视觉评分指标（navigation_visual）

| 指标 | 含义 | 性质 |
|------|------|------|
| `visual_avg_before` | 导航前页面的平均视觉还原度 | **纯视觉指标**——页面静态截图 vs 设计图 |
| `visual_avg_after` | 导航后页面的平均视觉还原度 | **混合指标**——同时受交互正确性和视觉还原影响 |

两个指标的区别：

- **VisBefore** 比较的是「还没做任何交互的页面截图」vs「对应的设计图」，完全不受导航是否成功的影响，是对视觉还原度的纯净度量
- **VisAfter** 比较的是「交互完成后的页面截图」vs「目标页面的设计图」。如果导航失败（停在原页面），拿错误的页面去和目标设计图比较，分数自然会低——但无法区分是视觉差还是交互没成功

因此：

- 如果需要单独评价**视觉还原质量**，看 `VisBefore`
- 如果需要单独评价**交互实现质量**，看 `NavPassRate`
- `VisAfter` 是两者的综合体现，适合作为总体质量的参考

每个截图对的评分包含四个维度（满分 5.0，加权求和）：

| 维度 | 权重 | 评分标准 |
|------|------|---------|
| Layout | 30% | 空间布局：位置、大小、对齐、间距 |
| Style | 25% | 视觉风格：颜色、字体、圆角、阴影 |
| Content | 25% | 内容完整性：文字、图片、数据项 |
| Consistency | 20% | 跨页面一致性：导航、共享组件 |

### 汇总报告表头说明

Markdown 对比表中的列：

| 列名 | 含义 | 方向 |
|------|------|------|
| InTok↓ | 平均输入 token 数 | 越小越好（效率） |
| OutTok | 平均输出 token 数 | 参考 |
| Time↓ | 平均 LLM 耗时 | 越小越好 |
| Build% | 构建通过率 | 越高越好 |
| Rt.Cov | 路由覆盖率 | 越高越好 |
| NavPass↑ | 导航测试通过率 | 越高越好（交互质量） |
| VisBefore↑ | 导航前视觉还原度 | 越高越好（纯视觉质量） |
| VisAfter↑ | 导航后视觉还原度 | 越高越好（综合质量） |
| LoC | 代码行数 | 参考 |
| Reuse↑ | 组件复用率 | 越高越好 |

## 实验配置文件（exp_config.yaml）

```yaml
experiment:
  name: my_experiment
  description: "实验描述"

matrix:
  apps:
    - input/app1
    - input/app2
  models:
    - gpt-4o
    - claude-sonnet-4-20250514
  methods:
    - name: agentic
      context_policy:
        strategy: full
      suffix: agentic_full

runs_per_cell: 1

limits:
  max_parallel_runs: 1
  timeout_per_run_sec: 3600

evaluation:
  eval_strategy: per_run    # per_run | batch | manual
  build_check: true
  structure_check: true
  visual_judge: true
  visual_judge_model: gemini-2.5-pro
```

### eval_strategy 说明

| 策略 | 行为 |
|------|------|
| `per_run` | 每个 run 生成完毕后立即评测 |
| `batch` | 所有 run 生成完毕后统一批量评测 |
| `manual` | 只生成代码，不自动评测（后续手动 `eval`） |

## 实验目录结构

运行实验后会在 `experiments/` 下创建带时间戳的目录：

```
experiments/20250420_143000_my_experiment/
├── config_snapshot.yaml
├── status.json
├── runs/
│   ├── app1__gpt-4o__agentic_full/
│   │   ├── src/               # 生成的代码
│   │   ├── .designforge/      # 生成过程日志与 traces
│   │   └── eval/              # 评测结果
│   │       ├── build_result.json
│   │       ├── efficiency.json
│   │       ├── trajectory.json
│   │       ├── project_quality.json
│   │       ├── test_report.json        # 导航测试报告（含视觉评分）
│   │       ├── navigation_visual.json  # 视觉评分缓存
│   │       ├── summary.json
│   │       ├── test_screenshots/       # 导航测试截图
│   │       └── token_composition.png
│   └── app1__claude-sonnet__agentic_full/
│       └── ...
└── report/
    ├── summary.json
    ├── comparison_table.md
    ├── token_composition_comparison.png
    └── tables/
        ├── all_runs.csv
        ├── by_model.csv
        └── ...
```

## 编程接口

除 CLI 外，也可在 Python 代码中直接调用：

```python
from evaluator.evaluate.pipeline import evaluate_run

results = evaluate_run(
    run_dir="output/wxreader-4__agentic__claude-opus-4-6",
    input_dir="input/wxreader-4",
    visual_judge_model="gemini-2.5-pro",
    plot=True,
    x_axis="both",
)

# results 包含每个评测模块的结果
print(results.get("build"))            # 构建结果
print(results.get("navigation_test"))  # 导航测试报告
```

## 依赖

- Python 3.10+
- `designforge`（代码生成引擎）
- `rich`（终端输出）
- `pyyaml`（配置加载）
- `matplotlib`（图表生成，可选）
- `testgen`（导航测试，可选——不安装则 navigation_test/navigation_visual 自动跳过）
- Playwright（导航测试需要，需已安装浏览器）
