<h1 align="center">MobileForge</h1>

<p align="center">
  <strong>Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation</strong>
</p>

<p align="center">
  Fan Wu, Cuiyun Gao, Yiming Huang, Yang Xiao, Yujia Chen, and Qing Liao
</p>

<p align="center" dir="ltr">
  <a href="../../README.md" lang="en" dir="auto">English</a> |
  <strong>简体中文</strong> |
  <a href="README_zh-TW.md" lang="zh-TW" dir="auto">繁體中文</a> |
  <a href="README_ja.md" lang="ja" dir="auto">日本語</a> |
  <a href="README_ko.md" lang="ko" dir="auto">한국어</a> |
  <a href="README_fr.md" lang="fr" dir="auto">Français</a> |
  <a href="README_de.md" lang="de" dir="auto">Deutsch</a> |
  <a href="README_ar.md" lang="ar" dir="auto">العربية</a> |
  <a href="README_th.md" lang="th" dir="auto">ไทย</a> |
  <a href="README_vi.md" lang="vi" dir="auto">Tiếng Việt</a>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.28645"><img src="https://img.shields.io/badge/arXiv-2607.28645-b31b1b.svg" alt="arXiv"></a>
  <img src="https://img.shields.io/badge/EMNLP-2026%20Main-blue.svg" alt="EMNLP 2026 Main">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Node.js-20%2B-339933.svg" alt="Node.js 20+">
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.28645">论文</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">代码</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">数据集与实验数据</a> •
  <a href="#overview">概览</a> •
  <a href="#set-up">环境配置</a> •
  <a href="#usage">使用方法</a> •
  <a href="#evaluation">评估</a> •
  <a href="#citation">引用</a>
</p>

---

这是 **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)** 的官方代码仓库，论文已被 **EMNLP 2026 主会**接收。

代码托管于 [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval)，与论文中的仓库地址一致。数据集和实验数据发布在 [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge)。

**MobileForge** 是评测基准；**DesignForge**（`designforge/`）是用于多图输入 UI-to-code 生成的智能体运行框架。本地 `dataset/` 工作区用于数据收集与整理，而非代码生成。

<a id="news"></a>

## 动态

- **EMNLP 2026：** *Looks Right, Works Right* 已被主会接收。
- 开源代码覆盖生成、标注、导航测试和评估流程。

<a id="overview"></a>

## 概览

将单张截图还原成页面并不足以构建移动应用。**MobileForge 评估项目级、多屏幕 UI-to-code 生成能力**：给定同一应用的多张截图，模型需要生成一个可构建的完整项目，保持界面一致、共享组件，并实现可用的跨屏幕导航。

该基准将视觉还原、可执行行为和项目整体质量联系起来：

| 评估维度 | 衡量内容 |
| --- | --- |
| **构建** | 生成项目是否通过编译和生产构建检查。 |
| **导航** | 交互是否到达预期界面。 |
| **视觉保真度** | 渲染界面与参考设计的相似程度。 |
| **可维护性** | 项目级代码质量与结构指标。 |
| **效率** | 生成过程的资源消耗，包括 token 和模型调用次数。 |

论文提出了**状态隔离的导航测试**，以避免导航失败的级联影响；同时提出**以锚点为参照的列表式视觉评估**，以提高视觉评审的可靠性。

```text
数据收集与整理        多屏幕截图
         dataset/                          |
                                  annotator/ → 元数据与测试用例
                                           |
                                  designforge/ → 生成项目
                                           |
                                  testgen/ → 导航与截图
                                           |
                                  evaluator/ → 指标与报告
```

默认生成目标是使用 **React + TypeScript + Tailwind CSS** 还原移动应用界面的 Web 项目，并非原生 Android/iOS 构建流程。

<a id="set-up"></a>

## 环境配置

使用 **Python 3.10+**（仓库环境配置说明使用 3.11）、**Node.js 20+** 和 **npm**。以下命令假定使用兼容 Bash 的 shell；Windows 用户可使用 WSL，或按所用 shell 调整环境激活命令。

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

根目录的 `requirements.txt` 是统一的 Python 依赖安装入口。浏览器评估需要 Chromium；生成过程中还需要联网安装生成项目的 npm 依赖。

### 配置模型

运行前编辑现有的 [`experiment.yaml`](../../experiment.yaml) 和 [`models.yaml`](../../models.yaml)：

| 配置 | 用途 |
| --- | --- |
| `experiment.yaml` → `llm` | 服务提供商与生成模型。 |
| `experiment.yaml` → `pipeline` | 框架、迭代预算和可选路由提示。 |
| `experiment.yaml` → `annotator` | 截图标注模型与并发数。 |
| `experiment.yaml` → `testgen` | 导航回退模型与视觉评审模型。 |
| `models.yaml` | 模型能力与 token 限制。 |
| `exp_config.yaml` | 应用 × 模型 × 方法的实验矩阵及评估策略。 |

使用 `llm.provider: litellm` 时，为实际使用的服务提供商设置凭据，例如：

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

生成模型应支持图像输入和工具调用；必要时，为标注和评估模型分别配置凭据。[`env.sh.example`](../../env.sh.example) 说明了其他环境变量，其中 Conda、nvm 和中转服务设置仅为可调整的示例，不应原样照搬。请勿提交凭据。

先检查本地配置，再测试模型连通性：

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **执行提示：** 生成和评估可能调用付费模型 API、安装 npm 包、执行生成的代码并启动浏览器。请在不含无关敏感凭据的隔离开发环境中运行。

<a id="data-preparation"></a>

## 数据准备

**数据集与实验数据**托管在 [Hugging Face 的 unhappyNN/mobileforge](https://huggingface.co/datasets/unhappyNN/mobileforge)。[`server_migrate/`](../../server_migrate/) 工具用于将这些数据恢复到本地工作区：[`hf_pull.py`](../../server_migrate/hf_pull.py) 下载 ZIP 压缩包，并保留其相对于 Hugging Face 仓库的路径。默认目标是本仓库根目录；使用 `--work-dir .` 可明确指定当前目录。

### 下载数据与实验产物

下载脚本已指向 `unhappyNN/mobileforge`。在仓库根目录执行：

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# 查看远程压缩包列表与下载计划。
python server_migrate/hf_pull.py --work-dir . --dry-run

# 将数据集和实验压缩包下载到当前工作区。
python server_migrate/hf_pull.py --work-dir .
```

该脚本需要 `HF_TOKEN`，仅下载 `.zip` 文件；除非指定 `--force`，否则会跳过大小一致的本地文件。它**不会解压压缩包**。请先检查包内目录结构，再将所需内容解压至对应工作区位置，并在生成或评估前确认任务输入符合下述结构。

### 任务结构

每个任务包含**同一应用的多张截图**。将各应用目录放在统一的输入根目录下：

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # 人工审核后的页面与关系标注
    └── test_cases.reviewed.json # 人工审核后的导航评估规范
```

运行框架直接读取应用目录中的图片（`.png`、`.jpg`、`.jpeg`、`.gif` 或 `.webp`）。标准评测样本以 `metadata.reviewed.json` 和 `test_cases.reviewed.json` 作为权威标注与导航规范。启用 `pipeline.route_hints` 后，框架使用审核后的元数据提供路由提示。框架和评估器为自定义输入保留了回退到未审核文件的能力，但基准实验应使用上述审核文件。

**收集与整理。** 本地 `dataset/mobbin-scraper/` 工作流负责截图采集和人工筛选。`annotator/` 生成结构化标注与测试用例，其[审核工具](../../annotator/check-tool/README.md)支持人工检查。

> **数据获取：** 数据集和实验数据通过 [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) 分发，不随 Git 仓库提供。`dataset/`、`input/` 和 `experiments/` 被 Git 忽略且未被跟踪，因此新克隆的仓库不包含本地采集工具、截图语料或实验产物。本地 `dataset/` 采集工作区与已发布的基准数据不同。

对于自己的截图，可使用以下命令生成初始标注和测试用例：

```bash
python -m annotator --input ./input/mobileforge/example-app
```

使用[审核工具](../../annotator/check-tool/README.md)检查生成的标注，并在用于基准实验前保存审核后的元数据和测试用例。如果需要根据审核后的元数据重新生成测试用例，请先输出草稿：

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

评估前审核重新生成的草稿，并将通过审核的用例保存为 `test_cases.reviewed.json`；不要用未经检查的草稿覆盖已审核的用例。

完整流程见[标注指南](../../annotator/README.md)。请仅使用有权访问和再分发的数据。

<a id="usage"></a>

## 使用方法

### 生成项目

对一个应用目录中的全部截图运行 DesignForge 智能体框架：

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

项目写入 `output/example-app/`。框架创建项目骨架，将多图输入和代码编辑工具提供给智能体，并通过修复循环检查构建。日志、轨迹和生成指标保存在该次运行的 `.designforge/` 目录中。

为输入根目录下的每个应用生成项目：

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

未指定 `--task` 时，运行名称采用 `<app>__agentic__<model>`；可用 `--suffix` 区分重复运行。

### 运行实验矩阵

编辑 [`exp_config.yaml`](../../exp_config.yaml)，选择输入应用、模型、方法和资源限制，然后运行：

```bash
python -m evaluator run --config exp_config.yaml
```

仓库中的配置使用 `evaluation.eval_strategy: manual`，因此该命令**只生成项目，不会自动评估**。选择 `per_run` 或 `batch` 可启用自动评估。在运行完整矩阵前，请检查模型可用性和 API 路由配置。

<a id="evaluation"></a>

## 评估

### 评估生成的项目

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

评估器协调构建、项目质量、效率和轨迹分析；存在测试用例时，还会进行导航测试和视觉评分。结果保存在该次运行的 `eval/` 目录中。缺少测试用例会跳过导航相关评估；命令执行成功并不意味着五个维度均已完成评估。

### 分别运行导航测试与视觉评分

```bash
python -m testgen run \
  --cases ./input/mobileforge/example-app/test_cases.reviewed.json \
  --run-dir ./output/example-app \
  --input-dir ./input/mobileforge/example-app \
  --report ./output/example-app/test_report.json

python -m testgen eval \
  --nav-report ./output/example-app/test_report.json \
  --cases ./input/mobileforge/example-app/test_cases.reviewed.json \
  --input-dir ./input/mobileforge/example-app \
  --visual-report ./output/example-app/visual_report.json
```

导航执行会截取实现界面的截图；视觉评分直接复用这些截图，无须重复交互。视觉报告与导航报告分别保存。为 `testgen run` 添加 `--no-vlm-fallback` 可禁用模型辅助的点击定位。

**论文协议与默认 CLI 的区别。** `testgen eval` 执行逐屏视觉评分，不会自动执行论文中跨候选的列表式评估协议。以锚点为参照的排序实现在 [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py) 中。比较不同方法时，应明确记录评审模型、候选集合、排序或打乱设置以及锚点策略。

为保证比较可复现，请记录代码提交、输入与标注版本、生成与评审模型、迭代预算、路由提示设置和评估配置。示例命令用于演示工作流，不代表默认设置能复现论文中的所有表格。

更多选项见[评估器指南](../../evaluator/README.md)和 [TestGen 指南](../../testgen/README.md)。使用 `python -m evaluator --help` 和 `python -m testgen --help` 查看 CLI。

<a id="repository-structure"></a>

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| [`designforge/`](../../designforge/) | 多图输入 UI-to-code 的**智能体运行框架**，包括工具、项目骨架和模型访问。 |
| `dataset/` | **数据收集与整理**工作区，仅存在于本地，不包含在当前 Git 快照中。 |
| [`server_migrate/`](../../server_migrate/) | 从 Hugging Face 下载数据集与实验压缩包到本地工作区。 |
| [`annotator/`](../../annotator/) | 截图标注、页面关系和人工审核工具。 |
| [`testgen/`](../../testgen/) | 导航测试生成与执行、视觉评审。 |
| [`evaluator/`](../../evaluator/) | 实验编排、指标、结果聚合和报告。 |
| [`human_eval/`](../../human_eval/) | 人工评估工具与材料。 |
| `input/`、`output/`、`experiments/` | 本地任务输入、生成项目与实验产物。 |

<a id="citation"></a>

## 引用

如果使用 MobileForge 或本代码库，请引用论文。以下条目引用 arXiv 版本；该工作已被 EMNLP 2026 主会接收。

```bibtex
@misc{wu2026looksrightworksright,
  title         = {Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation},
  author        = {Fan Wu and Cuiyun Gao and Yiming Huang and Yang Xiao and Yujia Chen and Qing Liao},
  year          = {2026},
  eprint        = {2607.28645},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2607.28645}
}
```
