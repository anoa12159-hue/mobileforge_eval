<h1 align="center">MobileForge</h1>

<p align="center">
  <strong>Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation</strong>
</p>

<p align="center">
  Fan Wu, Cuiyun Gao, Yiming Huang, Yang Xiao, Yujia Chen, and Qing Liao
</p>

<p align="center" dir="ltr">
  <strong>English</strong> |
  <a href="docs/other_languages/README_zh-CN.md" lang="zh-CN" dir="auto">简体中文</a> |
  <a href="docs/other_languages/README_zh-TW.md" lang="zh-TW" dir="auto">繁體中文</a> |
  <a href="docs/other_languages/README_ja.md" lang="ja" dir="auto">日本語</a> |
  <a href="docs/other_languages/README_ko.md" lang="ko" dir="auto">한국어</a> |
  <a href="docs/other_languages/README_fr.md" lang="fr" dir="auto">Français</a> |
  <a href="docs/other_languages/README_de.md" lang="de" dir="auto">Deutsch</a> |
  <a href="docs/other_languages/README_ar.md" lang="ar" dir="auto">العربية</a> |
  <a href="docs/other_languages/README_th.md" lang="th" dir="auto">ไทย</a> |
  <a href="docs/other_languages/README_vi.md" lang="vi" dir="auto">Tiếng Việt</a>
</p>
<p align="center">
  <a href="https://arxiv.org/abs/2607.28645"><img src="https://img.shields.io/badge/arXiv-2607.28645-b31b1b.svg" alt="arXiv"></a>
  <img src="https://img.shields.io/badge/EMNLP-2026%20Main-blue.svg" alt="EMNLP 2026 Main">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Node.js-20%2B-339933.svg" alt="Node.js 20+">
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.28645">Paper</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">Code</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">Dataset &amp; Experiment Data</a> •
  <a href="#overview">Overview</a> •
  <a href="#set-up">Set Up</a> •
  <a href="#usage">Usage</a> •
  <a href="#evaluation">Evaluation</a> •
  <a href="#citation">Citation</a>
</p>

---

Official code repository for **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)**, accepted to **EMNLP 2026 Main Conference**.

The code is hosted at [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval), matching the repository linked in the paper. The dataset and experiment data are available on [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge).

**MobileForge** is the benchmark; **DesignForge** (`designforge/`) is its agent harness for multi-image UI-to-code generation. The local `dataset/` workspace contains the data-collection and curation workflow, rather than the generation harness.

## News

- **EMNLP 2026:** *Looks Right, Works Right* has been accepted to the main conference.
- The open-source code release covers generation, annotation, navigation testing, and evaluation.

## Overview

Turning one screenshot into a page is not enough to build a mobile app. **MobileForge evaluates project-level, multi-screen UI-to-code generation**: given multiple screenshots from the same app, a model must produce a single buildable project with consistent screens, shared components, and working cross-screen navigation.

The benchmark connects visual reconstruction with executable behavior and project-wide quality:

| Evaluation axis | What it measures |
| --- | --- |
| **Build** | Whether the generated project passes compilation and production-build checks. |
| **Navigation** | Whether interactions reach the intended screens. |
| **Visual fidelity** | How closely rendered screens match the reference designs. |
| **Maintainability** | Project-level code-quality and structural indicators. |
| **Efficiency** | Generation resource usage, including tokens and model calls. |

The paper introduces **state-isolated navigation testing** to avoid cascading navigation failures and **anchor-referenced list-wise visual evaluation** to improve visual-judge reliability.

```text
Data collection & curation        Multi-screen screenshots
         dataset/                          |
                                  annotator/ → metadata + test cases
                                           |
                                  designforge/ → generated project
                                           |
                                  testgen/ → navigation + screenshots
                                           |
                                  evaluator/ → metrics + reports
```

The default generation target is a **React + TypeScript + Tailwind CSS** web project that reconstructs mobile-app screens. It is not a native Android/iOS build pipeline.

## Set Up

Use **Python 3.10+** (3.11 is used in the repository's setup notes), **Node.js 20+**, and **npm**. The commands below assume a Bash-compatible shell; on Windows, use WSL or adapt activation commands to your shell.

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The root `requirements.txt` is the consolidated Python installation entry point. Browser evaluation requires Chromium; generation also needs network access to install the generated project's npm dependencies.

### Configure models

Edit the existing [`experiment.yaml`](experiment.yaml) and [`models.yaml`](models.yaml) before running:

| Configuration | Purpose |
| --- | --- |
| `experiment.yaml` → `llm` | Provider and generation models. |
| `experiment.yaml` → `pipeline` | Framework, iteration budget, and optional route hints. |
| `experiment.yaml` → `annotator` | Screenshot annotation model and concurrency. |
| `experiment.yaml` → `testgen` | Navigation fallback model and visual judge. |
| `models.yaml` | Model capabilities and token limits. |
| `exp_config.yaml` | App × model × method experiment matrix and evaluation policy. |

For `llm.provider: litellm`, supply credentials for the providers you actually use, for example:

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

Choose a model that supports image input and tool use for generation, and configure credentials for annotation and evaluation models separately if needed. [`env.sh.example`](env.sh.example) documents additional environment variables; its Conda, nvm, and relay settings are examples to adapt, not prerequisites to copy unchanged. Do not commit credentials.

Check configuration locally, then check model connectivity:

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **Execution note:** Generation and evaluation can invoke paid model APIs, install npm packages, execute generated code, and launch browsers. Use an isolated development environment without unrelated secrets.

## Data Preparation

The **dataset and experiment data** are hosted on [unhappyNN/mobileforge on Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge). The [`server_migrate/`](server_migrate/) utilities restore these artifacts into the local workspace: [`hf_pull.py`](server_migrate/hf_pull.py) downloads ZIP archives while preserving their paths relative to the Hugging Face repository. By default, the destination is this repository's root; use `--work-dir .` to explicitly target the current directory.

### Download data and experiment artifacts

The download script points to `unhappyNN/mobileforge`. Run the following from the repository root:

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# Inspect the remote archive list and planned downloads.
python server_migrate/hf_pull.py --work-dir . --dry-run

# Download dataset and experiment archives into the current workspace.
python server_migrate/hf_pull.py --work-dir .
```

The script requires `HF_TOKEN`, downloads only `.zip` files, and skips local files with matching sizes unless `--force` is supplied. It **does not extract archives**. Extract the required archives into their intended workspace locations, checking their internal directory layout first, and confirm that task inputs match the structure below before running generation or evaluation.

### Task layout

Each task contains **multiple screenshots from one app**. Place app directories under a common input root:

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # Human-reviewed page and relationship annotations
    └── test_cases.reviewed.json # Human-reviewed navigation evaluation specifications
```

The harness reads images directly inside each app directory (`.png`, `.jpg`, `.jpeg`, `.gif`, or `.webp`). The standard benchmark sample uses `metadata.reviewed.json` and `test_cases.reviewed.json` as the authoritative annotations and navigation specifications. When `pipeline.route_hints` is enabled, the harness uses the reviewed metadata for route hints. The harness and evaluator support fallback to unreviewed files for custom inputs, but benchmark runs should use the reviewed files shown above.

**Collection and curation.** The local `dataset/mobbin-scraper/` workflow provides screenshot collection and manual screen selection. `annotator/` produces structured annotations and test cases; its [review tool](annotator/check-tool/README.md) supports human checking.

> **Data availability:** The dataset and experiment data are distributed through [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge), not bundled with the Git repository. `dataset/`, `input/`, and `experiments/` are ignored by Git and are not tracked. A fresh clone therefore does not include the local collection tools, screenshot corpus, or experiment artifacts. The local `dataset/` collection workspace is distinct from the released benchmark data.

For your own screenshots, generate initial annotations and test cases with:

```bash
python -m annotator --input ./input/mobileforge/example-app
```

Use the [review tool](annotator/check-tool/README.md) to check the generated annotations and save the reviewed metadata and test cases before benchmark use. If test cases need to be regenerated from reviewed metadata, write a draft first:

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

Review the regenerated draft and save the approved cases as `test_cases.reviewed.json` before evaluation; do not overwrite reviewed cases with an unchecked draft.

See the [annotation guide](annotator/README.md) for the full workflow. Use data you are authorized to access and redistribute.

## Usage

### Generate a project

Run the DesignForge agent harness on all screenshots in one app directory:

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

This writes the project to `output/example-app/`. The harness scaffolds the project, gives the agent the multi-image input and code-editing tools, and checks the build with a repair loop. Logs, traces, and generation metrics are stored under the run's `.designforge/` directory.

To generate projects for every app under an input root:

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

Without `--task`, run names follow `<app>__agentic__<model>`; use `--suffix` to distinguish repeated runs.

### Run an experiment matrix

Edit [`exp_config.yaml`](exp_config.yaml) to select the input apps, models, methods, and resource limits, then run:

```bash
python -m evaluator run --config exp_config.yaml
```

The checked-in configuration uses `evaluation.eval_strategy: manual`, so this command **generates projects without automatically evaluating them**. Choose `per_run` or `batch` to enable automatic evaluation. Review model availability and API routing before launching a full matrix.

## Evaluation

### Evaluate a generated project

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

The evaluator coordinates build, project quality, efficiency, trajectory analysis, and, when test cases are available, navigation tests and visual scoring. Results are written under the run's `eval/` directory. Missing test cases cause navigation-related evaluation to be skipped; a successful command alone does not imply that all five axes were evaluated.

### Run navigation and visual scoring separately

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

Navigation execution captures implementation screenshots; visual scoring reuses them without rerunning interactions. The visual report is separate from the navigation report. Add `--no-vlm-fallback` to `testgen run` to disable model-assisted click grounding.

**Paper protocol vs. default CLI.** `testgen eval` runs per-screen visual scoring; it does not automatically execute the paper's cross-candidate list-wise protocol. The anchor-referenced ranking implementation is provided in [`testgen/visual_judge_listwise.py`](testgen/visual_judge_listwise.py). Keep the judge, candidate set, ordering/shuffle settings, and anchor policy explicit when comparing methods.

For reproducible comparisons, record the code commit, input/annotation version, generation and judge models, iteration budgets, route-hint setting, and evaluation configuration. Example commands are workflow demonstrations, not a claim that the default settings reproduce every paper table.

See the [evaluator guide](evaluator/README.md) and [TestGen guide](testgen/README.md) for additional options. Use `python -m evaluator --help` and `python -m testgen --help` to inspect the CLI.

## Repository Structure

| Path | Role |
| --- | --- |
| [`designforge/`](designforge/) | **Agent harness** for multi-image UI-to-code generation, tools, scaffolding, and model access. |
| `dataset/` | **Data collection and curation** workspace; local-only in the current Git snapshot. |
| [`server_migrate/`](server_migrate/) | Download dataset and experiment archives from Hugging Face into the local workspace. |
| [`annotator/`](annotator/) | Screenshot annotation, page relationships, and human-review tooling. |
| [`testgen/`](testgen/) | Navigation test generation/execution and visual judges. |
| [`evaluator/`](evaluator/) | Experiment orchestration, metrics, aggregation, and reports. |
| [`human_eval/`](human_eval/) | Human-evaluation tooling and materials. |
| `input/`, `output/`, `experiments/` | Local task inputs, generated projects, and experiment artifacts. |

## Citation

If you use MobileForge or this codebase, please cite the paper. The entry below cites the arXiv version; the work is accepted to EMNLP 2026 Main Conference.

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
