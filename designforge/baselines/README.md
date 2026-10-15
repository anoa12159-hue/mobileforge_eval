# DesignForge Baselines

五个对比实验 baseline，代表从设计截图生成多页 React 前端项目的不同架构范式。所有 baseline 与 DesignForge 使用**相同底座模型、相同输入格式、相同输出格式**，隔离评估系统架构本身的贡献。

## 架构总览

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                   Design Screenshots × N                 │
                        └───┬──────────┬──────────┬───────────┬──────────┬─────────┘
                            │          │          │           │          │
                 ┌──────────▼──┐ ┌─────▼─────┐ ┌─▼────────┐ ┌▼────────────────┐ ┌─▼──────────┐
                 │ Monolithic  │ │  Page-     │ │Sequential│ │Plan-then-       │ │ Agentic    │
                 │             │ │Independent │ │Accumulate│ │Generate         │ │            │
                 │ 1 VLM call  │ │ N parallel │ │N serial  │ │1 plan + N gen   │ │ Tool loop  │
                 │ all images  │ │ per-image  │ │ + context│ │parallel per-page│ │ + build fix│
                 └──────┬──────┘ └─────┬──────┘ └────┬─────┘ └───────┬─────────┘ └─────┬──────┘
                        │              │             │               │                  │
                 ┌──────▼──────────────▼─────────────▼───────────────▼──┐               │
                 │           Deterministic Assembly & Build              │               │
                 │   scaffold → router/App/types → npm install → build  │               │
                 └──────────────────────────────────────────────────────┘               │
                                                                          ┌─────────────▼──────────┐
                                                                          │ Scaffold + Agent Loop   │
                                                                          │ scaffold → npm install  │
                                                                          │ → agent writes all code │
                                                                          │ → build verify → fix    │
                                                                          └────────────────────────┘
```

## Baseline 详解

### 1. Monolithic — 单次全量生成

```
所有截图 ──→ 1 次 VLM 调用 ──→ 解析多文件输出 ──→ 构建
```

- **策略**：将所有设计截图一次性送入 VLM，要求输出整个项目的所有源文件
- **输出格式**：`<file path="...">code</file>` 标签（自动回退解析 JSON / Markdown）
- **验证假设**：单次生成的上限在哪里？随页面数增长如何退化（上下文窗口压力、输出 token 上限）？
- **预期弱点**：页面数 > 5 时输出可能被截断；难以保证跨文件类型一致性

### 2. Page-Independent — 逐页独立 + 脚本组装

```
截图 1 ──→ VLM ──→ Page 1 ┐
截图 2 ──→ VLM ──→ Page 2 ├──→ 脚本生成 router/App/types ──→ 构建
截图 N ──→ VLM ──→ Page N ┘
          (并行)
```

- **策略**：每张截图独立调用 VLM 生成单个页面组件，零跨页上下文；路由和胶水代码由确定性脚本生成
- **并行**：使用 ThreadPoolExecutor，并行度受 `pipeline.max_parallel_pages` 控制
- **验证假设**：没有跨页信息共享时，样式一致性有多差？类型冲突有多严重？
- **预期弱点**：各页面颜色/字体/间距不统一；无共享状态管理；组件模式不一致

> 测试结果：缺少跨页上下文导致无法实现页面间导航，所以页面之间没有路由关系

### 3. Sequential-Accumulate — 顺序累积上下文

```
截图 1 ──→ VLM ──→ Page 1
截图 2 + Page 1 代码 ──→ VLM ──→ Page 2
截图 3 + Page 1,2 代码 ──→ VLM ──→ Page 3
...
最后：脚本生成 router/App/types ──→ 构建
```

- **策略**：按顺序逐页生成，每页的 prompt 包含所有已生成页面的代码（最近 2 页完整代码，更早页面截断至 3000 字符）
- **验证假设**：顺序累积上下文能否维持跨页一致性？代价是什么（token 爆炸、速度）？
- **预期弱点**：后面的页面 token 消耗指数增长；无法并行；页面顺序影响结果质量

### 4. Plan-then-Generate — 先规划后生成

```
Phase 1:  所有截图 ──→ VLM ──→ 全局 Plan (设计系统 + 导航 + 类型)
Phase 2:  截图 1 + Plan ──→ VLM ──→ Page 1 ┐
          截图 2 + Plan ──→ VLM ──→ Page 2 ├──→ 脚本生成 router/App ──→ 构建
          截图 N + Plan ──→ VLM ──→ Page N ┘
                           (并行)
```

- **策略**：两阶段——Phase 1 由 VLM 看所有截图生成全局规划（设计系统 token、导航图、共享类型定义）；Phase 2 每页独立生成，带 Plan 文档作为上下文
- **并行**：Phase 2 使用 ThreadPoolExecutor 并行
- **验证假设**：单步全局规划能否替代 DesignForge 的多阶段 Map → Reduce → Contract 流水线？缺少 Merge 阶段的后处理影响有多大？
- **预期弱点**：单次规划的质量不如多阶段分解；无 Merge 统一数据流和路由；Plan 中的信息可能不够细粒度

### 5. Agentic — 单 Agent 自主构建 + 构建修复循环

```
所有截图 ──→ 单 Agent (Tool Loop)
              │
              ├──→ 分析截图 → 规划结构
              ├──→ write_file 逐个创建源文件
              ├──→ run_command (npx tsc --noEmit) 自查
              ├──→ str_replace / batch_str_replace 修复问题
              └──→ task_done → 触发构建验证 (tsc + vite build)
                     │
                     ├── 通过 → 成功
                     └── 失败 → 错误反馈给 Agent → 继续修复 → 再次验证
```

- **策略**：单个自主 Agent 接收所有设计截图，通过工具循环（Tool Loop）自主完成整个项目的规划、编码、自检和修复。与其他 baseline 的"一次性生成"不同，Agent 可以迭代修改代码直到构建通过
- **工具集**：拥有全部 9 个工具——`write_file`、`read_file`、`str_replace`、`batch_str_replace`、`list_files`、`delete_file`、`run_command`、`grep`、`task_done`
- **脚手架**：项目基础设施（package.json、vite/ts/tailwind 配置等）由 scaffold 管理，Agent 专注于 `src/` 下的应用代码
- **构建验证**：当 Agent 调用 `task_done` 或停止调用工具时，自动运行 `tsc --noEmit` + `vite build`；如果失败，错误信息反馈给 Agent 继续修复
- **验证假设**：给予 Agent 完整工具集和迭代能力后，单 Agent 能否自主产出可构建的项目？与 DesignForge 多阶段分治策略相比，单 Agent 的自主决策能力如何？
- **预期弱点**：上下文窗口随迭代次数增长而膨胀；缺乏结构化的中间表示（IR/设计系统/数据契约）；全部决策压力集中在单个 Agent 上

## 与 DesignForge 的关键差异


| 能力             | Monolithic | Page-Independent | Sequential | Plan-then-Generate | Agentic              | **DesignForge**        |
| -------------- | ---------- | ---------------- | ---------- | ------------------ | -------------------- | ---------------------- |
| 视觉信息提取         | 隐式         | 隐式               | 隐式         | Phase 1            | 隐式 (Agent 自主分析)     | Map (IR/Style/Route)   |
| 全局设计系统         | ✗          | ✗                | 累积式        | Plan 中包含           | Agent 自主决策           | Reduce 阶段专门推导          |
| 导航图推理          | 隐式         | ✗                | ✗          | Plan 中包含           | Agent 自主决策           | Route Agent + NavGraph |
| 数据契约 & Mock    | ✗          | ✗                | ✗          | Plan 中的 types      | Agent 自主决策           | Contract 阶段专门推导        |
| 设计图嵌入生成        | ✓ (单次)     | ✓                | ✓          | ✓                  | ✓ (全程可见)             | ✓ (全程可见)               |
| 并行页面生成         | N/A        | ✓                | ✗          | ✓                  | ✗ (单线程)              | ✓                      |
| 跨页 Merge       | ✗          | ✗                | ✗          | ✗                  | Agent 自主协调           | **MergeAgent**         |
| 构建错误修复         | ✗          | ✗                | ✗          | ✗                  | **✓ (内置修复循环)**       | **BuildAgent**         |
| Stage Gate 质量门 | ✗          | ✗                | ✗          | ✗                  | ✗                    | **6 个 Gate**           |
| 页面生成工具循环       | ✗          | ✗                | ✗          | ✗                  | **✓ (全工具集)**         | **ToolAgent loop**     |
| 命令执行能力         | ✗          | ✗                | ✗          | ✗                  | **✓ (run_command)**   | ✗ (Build 确定性)          |


## 使用方法

### 前置条件

与 DesignForge 相同：

```bash
conda activate designforge
source env.sh       # 配置 LLM API 凭据
vim experiment.yaml  # 确认模型配置
```

### 运行单个 Baseline

```bash
# 指定方法 + 输入目录
python -m designforge.baselines -m monolithic -i ./input/wxreader

# 带功能描述
python -m designforge.baselines -m plan_then_generate -i ./input/wxreader \
  -d "微信读书App，包含书架、阅读、书城等功能"

# 指定输出目录
python -m designforge.baselines -m sequential -i ./input/wxreader -o ./output/exp1
```

### 运行所有 Baselines

```bash
# 同一个输入，运行全部 4 个 baseline
python -m designforge.baselines -m all -i ./input/wxreader
```

### 批量模式

```bash
# input/ 下每个含图片的子目录 × 全部 4 种方法
python -m designforge.baselines -m all -i ./input --batch

# 单个方法批量
python -m designforge.baselines -m monolithic -i ./input --batch
```

### CLI 参数


| 参数                | 默认值              | 说明                                                                              |
| ----------------- | ---------------- | ------------------------------------------------------------------------------- |
| `--method`, `-m`  | (必填)             | `monolithic` / `page_independent` / `sequential` / `plan_then_generate` / `agentic` / `all` |
| `--input`, `-i`   | `./input`        | 设计截图目录（batch 模式下为父目录）                                                           |
| `--output`, `-o`  | `./output`       | 输出根目录                                                                           |
| `--task`, `-t`    | `{输入目录名}__{方法名}` | 自定义任务名                                                                          |
| `--desc`, `-d`    | `""`             | 功能描述（覆盖输入目录下的 desc.txt）                                                         |
| `--framework`     | `react`          | 目标框架                                                                            |
| `--css`           | `tailwind`       | CSS 方案                                                                          |
| `--batch`         | —                | 批量模式                                                                            |
| `--verbose`, `-v` | —                | 开启 debug 日志                                                                     |


## 输出结构

每个 baseline 运行产出与 DesignForge 相同的目录结构：

```
output/
├── wxreader/                        # DesignForge 输出
├── wxreader__monolithic/            # Monolithic baseline
│   ├── .designforge/
│   │   ├── logs/pipeline.log
│   │   ├── metrics.json             # Token 用量、耗时、成本
│   │   └── traces/
│   │       └── run_manifest.json
│   ├── src/
│   │   ├── pages/
│   │   │   ├── home.tsx
│   │   │   ├── detail.tsx
│   │   │   └── ...
│   │   ├── router.tsx
│   │   ├── App.tsx
│   │   └── types.ts
│   ├── package.json
│   └── dist/                        # 构建成功时存在
├── wxreader__page_independent/
├── wxreader__sequential/
└── wxreader__plan_then_generate/
```

## 模型配置

所有 baseline 默认使用 `experiment.yaml` 中的 `agent_model`（与 DesignForge 的 PageAgent 相同），确保公平对比。切换模型只需修改 YAML：

```yaml
llm:
  agent_model: claude-sonnet-4       # 所有 baseline 和 DesignForge PageAgent 使用此模型
```

## 设计原则

1. **输入输出对齐**：所有 baseline 接受相同输入（多张设计截图），产出相同输出（完整 React + TypeScript + Tailwind 项目），确保指标可比
2. **模型公平**：使用同一个底座模型，隔离评估的是系统架构而非模型能力
3. **无构建修复循环**（前四个 baseline）：构建步骤是纯确定性的（scaffold → npm install → tsc → vite build），不使用 LLM 修复编译错误，build success rate 反映代码生成质量本身。**Agentic baseline 例外**：内置构建修复循环，验证 Agent 自主修复能力
4. **基础设施复用**：复用 DesignForge 的 LLM 客户端、指标追踪、脚手架、Node 环境解析、任务注册等，减少混淆变量
5. **指标一致**：每次 VLM/Agent 调用自动记录 token/耗时到 `metrics.json`，与 DesignForge 的指标格式完全一致，便于统一评测脚本处理

