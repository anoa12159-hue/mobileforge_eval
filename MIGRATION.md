# AgenticForge 迁移指引

本文档用于将 AgenticForge 项目迁移到一台全新的服务器，确保**环境可复现**且**独立打包的数据可正确还原**。

---

## 目录

1. [迁移概览](#1-迁移概览)
2. [环境复现](#2-环境复现)
3. [独立数据的还原](#3-独立数据的还原)
4. [配置文件准备](#4-配置文件准备)
5. [迁移完成后的自检](#5-迁移完成后的自检)

---

## 1. 迁移概览

迁移分为两条独立路径：


| 路径                                      | 内容                                                                                                       | 来源                       |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------ |
| **A. 代码 + 配置**                          | 整个 git 仓库（含所有 `.py`、`.yaml`、`.sh`、`.md`）                                                                 | `git clone` 或 `rsync` 仓库 |
| **B. 数据资产**（被 `.gitignore` 排除，需独立压缩包传输） | `experiments/` `input/` `output/` `dataset/mobbin-scraper/downloads/` `dataset/mobbin-scraper/selected/` | 单独的 `.tar.gz` 包          |


代码以外的**敏感凭据文件**（不入库、需手工带过去）：

- `env.sh`（API key）
- `dataset/mobbin-scraper/.env.local`（Mobbin Cookie）

---

## 2. 环境复现

### 2.1 系统级前置

新服务器需具备：

- Linux x86_64（已在 linux 5.4 内核验证）
- `git`、`curl`、`tar`、`unzip`
- 可访问外网（`pip` / `npm` 安装依赖时需要）

### 2.2 Python 环境

> **从代码推断**（不读取当前服务器环境）：
>
> - 所有 78 个核心源文件均使用 `from __future__ import annotations`，允许类型注解使用 PEP 604（`X | Y`）语法。
> - 依赖中 `matplotlib >= 3.8`、`Pillow >= 11`、`pydantic >= 2.10`、`fastapi >= 0.115` 共同要求 **Python ≥ 3.9**。
> - 未使用 `match`/`case`、`tomllib`、`ExceptionGroup`、`asyncio.timeout` 等 Python 3.10+/3.11+ 独占特性。
>
> **推荐版本：Python 3.11**（与既有 conda 环境名 `designforge` 一致；3.10 也可工作）。

#### 2.2.1 用 conda 创建独立环境（推荐）

```bash
# 安装 miniconda（如已装可跳过）
curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /tmp/miniconda.sh -b -p $HOME/miniconda3
source $HOME/miniconda3/bin/activate

# 创建项目专用环境
conda create -n designforge python=3.11 -y
conda activate designforge
```

#### 2.2.2 安装 Python 依赖

项目根目录 `requirements.txt` 是**唯一安装入口**，已合并所有子模块（designforge / evaluator / annotator / 各 web 工具 / dataset/mobbin-scraper）的依赖，子目录下的历史 `requirements*.txt` 不必再单独安装。

```bash
cd <项目根目录>
pip install -r requirements.txt
```

如果只需要"代码生成 + 评测"主流水线，到此为止；如果还要跑数据集预览脚本 `scripts/preview_mobileviews.py`，再额外装：

```bash
pip install 'huggingface_hub>=0.20.0' 'pyarrow>=15.0.0'
```

#### 2.2.3 各包的用途（便于排错）


| 包                     | 用途                                                                  | 必要性          |
| --------------------- | ------------------------------------------------------------------- | ------------ |
| `httpx`               | `designforge.llm_client` Chat API HTTP 调用                              | 必需           |
| `litellm`             | `provider: litellm` 模式下的统一 LLM 接口                                   | LiteLLM 模式必需 |
| `PyYAML`              | `experiment.yaml` / `models.yaml` 加载                                | 必需           |
| `rich`                | 所有 CLI 的彩色输出、表格、日志                                                  | 必需           |
| `Pillow`              | 图片处理（标注/分析脚本、stub 检测）                                               | 必需           |
| `pydantic`            | `select-tool` / `review-tool` / `annotator/check-tool` 的 FastAPI 模型 | Web 工具必需     |
| `matplotlib`          | 评测产出 token 分布图、cost-quality 前沿图                                     | 评测必需         |
| `playwright`          | 评测视觉渲染、`testgen` 浏览器执行测试用例、`vlm_click_benchmark`                    | 评测必需         |
| `numpy` + `scipy`     | `testgen/route_resolver.py` 路由名最优匹配（缺失会降级为 greedy）                   | 评测强烈建议       |
| `fastapi` + `uvicorn` | `select-tool` / `review-tool` / `annotator/check-tool` Web 服务       | 标注 / 选页必需    |
| `requests`            | `dataset/mobbin-scraper/download_mobbin.py` 下载 Mobbin               | 抓取数据时必需      |
| `huggingface_hub` + `pyarrow` | `scripts/preview_mobileviews.py` 预览 MobileViews 数据集            | 可选（不在主流水线）   |


#### 2.2.4 Playwright 浏览器二进制

`playwright` 包本身只是 SDK，浏览器内核要单独装：

```bash
playwright install chromium
# 如果系统库缺失，root 下补依赖：
sudo playwright install-deps chromium   # 或忽略，缺什么再补什么
```

### 2.3 Node.js 环境

`designforge.scaffold` 中硬编码 `REQUIRED_NODE_MAJOR = 20`（Vite 6 要求 Node ≥ 18，项目锁 LTS 20）。

```bash
# 安装 nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# 安装并切换 Node 20
nvm install 20
nvm use 20
node --version    # 应显示 v20.x
```

`env.sh` 中已包含 `nvm use 20`，每次开新 shell 时 `source env.sh` 即可。

### 2.4 验证环境

```bash
source env.sh                                # 激活 conda + Node + 写入 API 凭据
python -m designforge preflight --skip-api   # 仅检查本地配置（约 1 秒）
python -m designforge preflight              # 含 API 连通性测试
```

`preflight` 会逐项检查：`experiment.yaml` 完整性、模型注册表、API 凭据、Node 版本、输入/输出目录、Python 关键依赖。任何一项失败都会指出具体原因。

---

## 3. 独立数据的还原

### 3.1 需要打包的目录清单

下表列出**必须独立通过压缩包迁移**的目录（已被 `.gitignore` 排除，git 不会带过去）：


| 序号  | 源目录（旧机器）                            | 还原后位置（新机器）                                | 估计大小    | 说明                                                                                                     |
| --- | ----------------------------------- | ----------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------ |
| 1   | `experiments/`                      | `<项目根>/experiments/`                      | ~22 GB  | 评测实验产物：每次 `evaluator run` 的 `runs/`、`report/`、`status.json`、`config_snapshot.yaml` 等                   |
| 2   | `input/`                            | `<项目根>/input/`                            | ~216 MB | 设计稿输入（含子目录 `mobileforge/`、`wxreader-4/` 等，每个子目录含 png/jpg、`desc.txt`、`metadata.json`、`test_cases.json`） |
| 3   | `output/`                           | `<项目根>/output/`                           | ~3.1 GB | DesignForge / Baselines 生成的 React 项目代码与构建产物                                                            |
| 4   | `dataset/mobbin-scraper/downloads/` | `<项目根>/dataset/mobbin-scraper/downloads/` | ~1.5 GB | 从 Mobbin 下载的全量截图（按 `app/flow` 层级组织），每个 app 一个子目录，含 `metadata.json` 与原图                                 |
| 5   | `dataset/mobbin-scraper/selected/`  | `<项目根>/dataset/mobbin-scraper/selected/`  | ~54 MB  | 通过 `select-tool` 手动勾选后的精简数据集，每个 app 一个子目录，含 `selection.json`                                           |


**总计约 27 GB**，建议分包，避免单包过大（见 3.2）。

### 3.2 旧机器：制作压缩包

在**旧机器**的项目根目录执行：

```bash
cd <旧机器项目根>

# 大目录单独打包，便于断点续传 / 校验
tar --use-compress-program="pigz -p 8" -cf experiments.tar.gz experiments/      # ~22G，最大
tar --use-compress-program="pigz -p 8" -cf output.tar.gz output/                # ~3.1G
tar --use-compress-program="pigz -p 8" -cf input.tar.gz input/                  # ~216M
tar --use-compress-program="pigz -p 8" -cf mobbin_downloads.tar.gz dataset/mobbin-scraper/downloads/   # ~1.5G
tar --use-compress-program="pigz -p 8" -cf mobbin_selected.tar.gz dataset/mobbin-scraper/selected/    # ~54M

# 凭据文件（小，单独 scp 即可）
cp env.sh env.sh.backup
cp dataset/mobbin-scraper/.env.local mobbin.env.local.backup

# 校验（可选但强烈推荐）
sha256sum *.tar.gz > checksums.txt
```

> 没有 `pigz` 时把 `--use-compress-program="pigz -p 8"` 换成 `-z`（用 gzip 单线程）即可。

### 3.3 传输到新机器

```bash
# 推荐用 rsync（支持断点续传）
rsync -avzP \
  experiments.tar.gz \
  output.tar.gz \
  input.tar.gz \
  mobbin_downloads.tar.gz \
  mobbin_selected.tar.gz \
  checksums.txt \
  env.sh.backup \
  mobbin.env.local.backup \
  user@new-host:/path/to/AgenticForge/
```

### 3.4 新机器：还原数据

**前提**：新机器上已 `git clone` 仓库到 `<新项目根>`。

```bash
cd <新项目根>

# 校验包完整性
sha256sum -c checksums.txt

# 1. experiments/ → <根>/experiments/
tar -xzf experiments.tar.gz -C .
ls experiments/                                  # 应看到 20260428_181902_mobileforge-compare/ 等

# 2. input/ → <根>/input/
tar -xzf input.tar.gz -C .
ls input/                                        # 应看到 mobileforge/、wxreader-4/ 等

# 3. output/ → <根>/output/
tar -xzf output.tar.gz -C .
ls output/                                       # 应看到 mobileforge/、stash/ 等

# 4. dataset/mobbin-scraper/downloads/ → <根>/dataset/mobbin-scraper/downloads/
mkdir -p dataset/mobbin-scraper                  # 仓库里 dataset/ 已被 gitignore，需先确认存在
tar -xzf mobbin_downloads.tar.gz -C .            # 包内已带 dataset/mobbin-scraper/ 前缀
ls dataset/mobbin-scraper/downloads/             # 应看到 airbnb-ios-...、apple-news-ios-... 等

# 5. dataset/mobbin-scraper/selected/ → <根>/dataset/mobbin-scraper/selected/
tar -xzf mobbin_selected.tar.gz -C .
ls dataset/mobbin-scraper/selected/              # 应看到 airbnb/、apple-news/ 等

# 6. 凭据文件归位
cp env.sh.backup env.sh
chmod 600 env.sh
cp mobbin.env.local.backup dataset/mobbin-scraper/.env.local
chmod 600 dataset/mobbin-scraper/.env.local

# 7. （可选）恢复样本项目的 node_modules
#    自 testgen/runner.py 引入 SPA fallback 静态服务器后，此步默认 **不再必须**：
#    navigation_test 在检测不到本地 vite 时会自动用 Python 内置静态服务器
#    serve dist/，行为与 vite preview 字节级一致（已通过 parity 测试）。
#
# 三条经验规则（与 evaluator 模块对应）：
#   - 只看历史 eval 结果 / 出图 / 跑 analysis 脚本        → 0 个 node_modules
#   - 重跑 navigation_test / navigation_visual           → 0 个（dist/ 已带，Python fallback 自动接管）
#   - 重跑 evaluator 的 build 模块（npx tsc + vite build）   → 必须装该 run 的 node_modules
#
# 7a. 大多数情况：什么都不用装，直接进入下一节自检。
#
# 7b. 仅当你要重跑某些 run 的 build 时，按需恢复：
source env.sh
bash server_migrate/restore_node_modules.sh experiments/20260428_181902_mobileforge-compare
bash server_migrate/restore_node_modules.sh experiments/main_results
bash server_migrate/restore_node_modules.sh output/mobileforge

#  - 主源失败自动回退 https://registry.npmmirror.com/
#  - 并发可调：JOBS=8 bash server_migrate/restore_node_modules.sh ...
#  - 只看会装什么：DRY_RUN=1 bash server_migrate/restore_node_modules.sh ...
#  - 失败重试：FORCE=1 bash server_migrate/restore_node_modules.sh $(cat server_migrate/.restore_logs/failed.txt)
#  - 详细日志：server_migrate/.restore_logs/
#  - 单项目装满约 89 MB，单机 4 并发 npm install 约 8~15 秒/个
#
# 7c. 想强制选某个预览后端（调试用）：
#   TESTGEN_SERVER=vite    强制走本地 vite preview（要求 node_modules）
#   TESTGEN_SERVER=python  强制走 Python SPA 静态服务器（要求 dist/）
#   TESTGEN_SERVER=auto    默认：有 vite 用 vite，没有就 python fallback
#
# 7d. 备选方案 B（全局 vite + 空 config）：见 server_migrate/preview_server_alt_plan_B.md
#    只在你严格要求 navigation_test 进程类型与论文实验 100% 一致时才需要。
```

### 3.5 还原后目录结构应为

```
<新项目根>/
├── designforge/               (来自 git)
├── evaluator/                 (来自 git)
├── annotator/                 (来自 git)
├── testgen/                   (来自 git)
├── scripts/                   (来自 git)
├── env.sh                     ← 凭据，手工带
├── experiment.yaml            (来自 git)
├── models.yaml                (来自 git)
├── requirements.txt           (来自 git)
│
├── experiments/               ← 解压自 experiments.tar.gz
│   ├── 20260428_181902_mobileforge-compare/
│   ├── annotator_review/
│   ├── cost_quality/
│   ├── failures/
│   ├── judge_method_study/
│   └── main_results/
│
├── input/                     ← 解压自 input.tar.gz
│   ├── mobileforge/
│   ├── wxreader-4/
│   └── ...（含若干 *.tar.gz 旧备份，可保留可删）
│
├── output/                    ← 解压自 output.tar.gz
│   ├── mobileforge/
│   └── stash/
│
└── dataset/
    └── mobbin-scraper/        (代码部分来自 git)
        ├── download_mobbin.py
        ├── select-tool/
        ├── review-tool/
        ├── .env.local         ← 凭据，手工带
        ├── downloads/         ← 解压自 mobbin_downloads.tar.gz
        └── selected/          ← 解压自 mobbin_selected.tar.gz
```

### 3.6 等价性验证：navigation_test 在「无 node_modules」下与论文实验一致

`testgen/runner.py` 的 `_start_preview_server` 会按下表自动选预览后端（也可用
`TESTGEN_SERVER=vite|python|auto` 强制）：

| 项目状态 | 自动选择的后端 | 实际跑的进程 |
| --- | --- | --- |
| 有 `node_modules/.bin/vite` | `vite`（论文复现路径，零变化） | `npx vite preview --port N --strictPort` 子进程 |
| 无 `node_modules` 但 `dist/` 在 | `python` | 同进程内 stdlib `ThreadingHTTPServer` + SPA fallback handler |
| 两者皆无 | （拒绝服务，打印明确错误指向 `restore_node_modules.sh`） | — |

#### 3.6.1 字节级 HTTP 等价

两后端对 6 类典型路径返回的 `status / content-type / body 大小` 完全一致：

| 路径 | vite | python | 一致 |
| --- | --- | --- | --- |
| `/` | `200 text/html 403B` | `200 text/html 403B` | ✓ |
| `/assets/index-XXX.js`（存在）| `200 text/javascript 332797B` | 同 | ✓ |
| `/explore`（SPA 路由，文件不存在）| `200 text/html 403B`（fallback）| 同 | ✓ |
| `/listing-details/123`（嵌套 SPA 路由）| `200 text/html 403B` | 同 | ✓ |
| `/assets/nonexistent.js`（缺失资源）| `200 text/html 403B`（vite 默认 historyApiFallback 行为）| 同 | ✓ |
| `/totally/random/path` | `200 text/html 403B` | 同 | ✓ |

#### 3.6.2 端到端 Playwright + 截图字节级一致

把同一个 airbnb 样本拷成两份（一份保留 node_modules 走 vite，一份去除 node_modules 走 python fallback），用 `testgen.runner.run_tests` 跑 3 个覆盖三种类型的真实用例（`page_exists` / `tab_navigation` / `parent_child_navigation`），结果：

```
报告字段               vite                 python              一致
total_cases / passed / failed / pass_rate   3 / 2 / 1 / 0.6667   3 / 2 / 1 / 0.6667   ✓
by_type 分组明细                            完全相同              完全相同              ✓
4 张截图 MD5（before/after × 用例）          a/b/c/d              a/b/c/d              ✓ 全部 IDENTICAL
evaluated_at 时间戳                         12:23:32              12:23:40             ✗（预期）
```

**截图字节级相同 = 浏览器渲染像素级一致 = 后续 navigation_visual 的 VLM 评分必然一致**。所以："不装 node_modules、只带 `dist/` 过新机器" 是评测路径上的安全等价。

#### 3.6.3 自检：在你的新机器上重跑这套验证

可直接执行下面的脚本，三分钟跑完一次完整的双后端对照：

```bash
cd <新项目根>
source env.sh

SAMPLE=output/mobileforge/airbnb__agentic__claude-sonnet-4    # 任挑一个有 dist/ 的样本
INPUT=input/mobileforge/airbnb
WORK=/tmp/nav-parity && rm -rf "$WORK" && mkdir -p "$WORK/vite-run" "$WORK/py-run"

# 准备两份镜像项目：vite-run 用符号链接复用 node_modules；py-run 不放 node_modules
for D in "$WORK/vite-run" "$WORK/py-run"; do
  cp -r "$SAMPLE"/{src,dist,package.json,package-lock.json,index.html,vite.config.ts,tsconfig.json,tailwind.config.cjs,postcss.config.cjs} "$D/"
  [ -d "$SAMPLE/.designforge" ] && cp -r "$SAMPLE/.designforge" "$D/" || mkdir -p "$D/.designforge"
done
ln -s "$(pwd)/$SAMPLE/node_modules" "$WORK/vite-run/node_modules"

# 取三种类型各一条作为精简 test_cases.json
python3 - <<EOF
import json, shutil
from pathlib import Path
src = json.load(open("$INPUT/test_cases.json"))
picked, seen = [], set()
for c in src["cases"]:
    if c["type"] not in seen:
        picked.append(c); seen.add(c["type"])
    if len(seen) >= 3 and len(picked) >= 3: break
inp = Path("$WORK/input"); inp.mkdir(exist_ok=True)
for p in Path("$INPUT").iterdir():
    if p.name == "test_cases.json": continue
    (shutil.copy2 if p.is_file() else lambda a,b: shutil.copytree(a,b,dirs_exist_ok=True))(p, inp / p.name)
out = dict(src); out["cases"] = picked; out["total_cases"] = len(picked)
json.dump(out, open(inp / "test_cases.json", "w"), indent=2)
EOF

# 两个后端各跑一次
for MODE in vite python; do
  TESTGEN_SERVER=$MODE python3 -c "
from testgen.runner import run_tests
r = run_tests('$WORK/input/test_cases.json', '$WORK/${MODE/python/py}-run', '$WORK/input',
              report_path='$WORK/${MODE/python/py}-run/report.json',
              screenshots_dir='$WORK/${MODE/python/py}-run/shots')
print(f'$MODE pass_rate={r[\"pass_rate\"]} passed={r[\"passed\"]}/{r[\"total_cases\"]}')"
done

# 对照
python3 - <<EOF
import json, hashlib
from pathlib import Path
v = json.load(open("$WORK/vite-run/report.json"))
p = json.load(open("$WORK/py-run/report.json"))
print(f"\nReport fields equal (modulo timestamp):",
      all(v[k] == p[k] for k in ("total_cases","passed","failed","pass_rate","by_type")))
v_shots = sorted(Path("$WORK/vite-run/shots").rglob("*.png"))
mismatch = 0
for vs in v_shots:
    ps = Path("$WORK/py-run/shots") / vs.relative_to("$WORK/vite-run/shots")
    if not ps.is_file() or hashlib.md5(vs.read_bytes()).hexdigest() != hashlib.md5(ps.read_bytes()).hexdigest():
        mismatch += 1
print(f"Screenshots: {len(v_shots)} total, {mismatch} byte-mismatches  (expected 0)")
EOF

rm -rf "$WORK"
```

预期输出：

```
vite   pass_rate=0.6667 passed=2/3
python pass_rate=0.6667 passed=2/3
Report fields equal (modulo timestamp): True
Screenshots: 4 total, 0 byte-mismatches  (expected 0)
```

若 `byte-mismatches` 非 0，说明两个后端响应不一致（理论上不应发生），请联系维护者。

---

### 3.7 路径与配置的一致性检查

代码中默认引用的路径（来自 `experiment.yaml`）：

```yaml
paths:
  input_dir: ./input/mobileforge
  output_dir: ./output/mobileforge
```

只要在新机器上**项目根目录的工作路径相同（即解压时 `-C .` 指向项目根）**，所有相对路径都不需要改。如果迁到不同目录名，确认下面三处都是相对路径或同步更新：

1. `experiment.yaml` 的 `paths` 段
2. `exp_config*.yaml` 中的实验目录字段
3. `experiments/<expname>/runs/<run>/...` 内若已有绝对路径写入（一般 evaluator 使用相对路径，无需改）

---

## 4. 配置文件准备

### 4.1 `env.sh`（不入库）

如果旧的 `env.sh` 没带过来，按以下模板创建：

```bash
#!/bin/bash
# Chat API 模式凭据
export APP_NAME=mmpayxuatcopilotsvr_gpt4
export APP_ID=<你的 APP_ID>
export APP_KEY=<你的 APP_KEY>

# Conda 库路径修正（避免 LD_LIBRARY_PATH 冲突）
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:$LD_LIBRARY_PATH"

conda activate designforge
nvm use 20

# 可选：LiteLLM 模式
# export ANTHROPIC_API_KEY="sk-ant-..."
# export OPENAI_API_KEY="sk-..."
# export GEMINI_API_KEY="..."
```

### 4.2 `dataset/mobbin-scraper/.env.local`（不入库）

```
MOBBIN_COOKIES="<完整的 mobbin.com 登录态 Cookie 字符串>"
```

获取方法见 `dataset/mobbin-scraper/README.md` 第 41-55 行。

---

## 5. 迁移完成后的自检

按顺序跑下面这套自检流程：

```bash
cd <新项目根>
source env.sh

# 1. 检查 conda + Node 是否激活成功
python --version           # 期望: Python 3.11.x
node --version             # 期望: v20.x

# 2. Python 依赖完整性 + API 连通性
python -m designforge preflight

# 3. 数据目录可见性（关键）
ls experiments/ | head -5
ls input/ | head -5
ls output/ | head -5
ls dataset/mobbin-scraper/downloads/ | head -5
ls dataset/mobbin-scraper/selected/ | head -5

# 4. 跑一个最小实验（dry-run，不调用 LLM）
python -m designforge --input ./input/wxreader-4 --dry-run

# 5. 检查评测器对历史实验的可读性
python -m evaluator status experiments/20260428_181902_mobileforge-compare

# 6. （可选）启动 Web 监控面板
python -m evaluator monitor experiments/20260428_181902_mobileforge-compare --host 0.0.0.0 --port 8080
```

**全部通过 = 迁移成功。**

如果某一步失败：


| 现象                                                               | 检查点                                               |
| ---------------------------------------------------------------- | ------------------------------------------------- |
| `ModuleNotFoundError: xxx`                                       | 回到 [2.2.2](#222-安装-python-依赖) 重装                  |
| `playwright._impl._errors.Error: Executable doesn't exist`       | 跑 `playwright install chromium`                   |
| `nvm: command not found` 或 `node: command not found`             | 重新 `source ~/.nvm/nvm.sh` 后 `nvm use 20`          |
| `preflight` 报 401/403                                            | `env.sh` 里 `APP_KEY` 错或环境变量未导出，重新 `source env.sh` |
| `experiments/` 下子目录看得到但 `evaluator status` 报 `FileNotFoundError` | 解压时 `-C` 指错了根目录，重新解压（包内是相对路径，必须在项目根执行 `tar -xzf`） |
| Mobbin 下载 401                                                    | `.env.local` Cookie 过期，登录浏览器后重新复制 Cookie 写入文件     |
| navigation_test 报「dist/ missing AND no node_modules/.bin/vite」      | dist 没带且 node_modules 也没装；二选一：`bash server_migrate/restore_node_modules.sh <run>` 让 vite 自动 build 出 dist；或者补 dist 进迁移包 |
| 想确认 navigation_test 用的是 vite 还是 python 后端                 | 查 logs：`preview (vite) serving at ...` 或 `preview (python) serving at ...`；或在调用前 `export TESTGEN_SERVER=vite\|python` 强制 |
| 某些样本项目缺 `node_modules/` 导致 `vite build` / `npm run dev` 报错       | 跑 `bash server_migrate/restore_node_modules.sh <子目录>`；只补失败项: `FORCE=1 bash server_migrate/restore_node_modules.sh $(cat server_migrate/.restore_logs/failed.txt)` |


