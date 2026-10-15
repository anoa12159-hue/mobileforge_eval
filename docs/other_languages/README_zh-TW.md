<h1 align="center">MobileForge</h1>

<p align="center">
  <strong>Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation</strong>
</p>

<p align="center">
  Fan Wu, Cuiyun Gao, Yiming Huang, Yang Xiao, Yujia Chen, and Qing Liao
</p>

<p align="center" dir="ltr">
  <a href="../../README.md" lang="en" dir="auto">English</a> |
  <a href="README_zh-CN.md" lang="zh-CN" dir="auto">简体中文</a> |
  <strong>繁體中文</strong> |
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
  <a href="https://arxiv.org/abs/2607.28645">論文</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">程式碼</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">資料集與實驗資料</a> •
  <a href="#overview">概覽</a> •
  <a href="#set-up">環境設定</a> •
  <a href="#usage">使用方式</a> •
  <a href="#evaluation">評估</a> •
  <a href="#citation">引用</a>
</p>

---

這是 **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)** 的官方程式碼儲存庫，論文已獲 **EMNLP 2026 主會議**接受。

程式碼託管於 [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval)，與論文中的儲存庫位址一致。資料集與實驗資料發布於 [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge)。

**MobileForge** 是評測基準；**DesignForge**（`designforge/`）是用於多圖輸入 UI-to-code 生成的代理執行框架。本機 `dataset/` 工作區負責資料蒐集與整理，而非程式碼生成。

<a id="news"></a>

## 最新消息

- **EMNLP 2026：** *Looks Right, Works Right* 已獲主會議接受。
- 開源程式碼涵蓋生成、標註、導覽測試及評估流程。

<a id="overview"></a>

## 概覽

將單張螢幕截圖還原成頁面，並不足以建構行動應用程式。**MobileForge 評估專案層級、多畫面的 UI-to-code 生成能力**：給定同一應用程式的多張截圖，模型須產生可建置的完整專案，維持畫面一致性、共用元件，並實現可運作的跨畫面導覽。

此基準整合視覺還原、可執行行為與專案整體品質：

| 評估面向 | 衡量內容 |
| --- | --- |
| **建置** | 生成專案是否通過編譯及正式環境建置檢查。 |
| **導覽** | 互動是否抵達預期畫面。 |
| **視覺保真度** | 渲染畫面與參考設計的相似程度。 |
| **可維護性** | 專案層級的程式碼品質與結構指標。 |
| **效率** | 生成過程的資源用量，包括 token 與模型呼叫次數。 |

論文提出**狀態隔離的導覽測試**，避免導覽失敗產生連鎖影響；並提出**以錨點為參照的列表式視覺評估**，提高視覺評審的可靠性。

```text
資料蒐集與整理        多畫面截圖
         dataset/                          |
                                  annotator/ → 中繼資料與測試案例
                                           |
                                  designforge/ → 生成專案
                                           |
                                  testgen/ → 導覽與截圖
                                           |
                                  evaluator/ → 指標與報告
```

預設生成目標是以 **React + TypeScript + Tailwind CSS** 還原行動應用程式畫面的 Web 專案，並非原生 Android/iOS 建置流程。

<a id="set-up"></a>

## 環境設定

請使用 **Python 3.10+**（儲存庫設定說明採用 3.11）、**Node.js 20+** 與 **npm**。下列指令假設使用相容於 Bash 的 shell；Windows 使用者可採用 WSL，或依所用 shell 調整環境啟用指令。

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

根目錄的 `requirements.txt` 是統一的 Python 相依套件安裝入口。瀏覽器評估需要 Chromium；生成過程也需要網路連線，以安裝生成專案的 npm 相依套件。

### 設定模型

執行前請編輯現有的 [`experiment.yaml`](../../experiment.yaml) 與 [`models.yaml`](../../models.yaml)：

| 設定 | 用途 |
| --- | --- |
| `experiment.yaml` → `llm` | 服務供應商與生成模型。 |
| `experiment.yaml` → `pipeline` | 框架、迭代預算與選用的路由提示。 |
| `experiment.yaml` → `annotator` | 截圖標註模型與並行數。 |
| `experiment.yaml` → `testgen` | 導覽備援模型與視覺評審模型。 |
| `models.yaml` | 模型能力與 token 限制。 |
| `exp_config.yaml` | 應用程式 × 模型 × 方法的實驗矩陣與評估策略。 |

使用 `llm.provider: litellm` 時，請為實際使用的供應商設定憑證，例如：

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

生成模型應支援圖像輸入與工具呼叫；必要時，另為標註及評估模型設定憑證。[`env.sh.example`](../../env.sh.example) 說明其他環境變數，其中 Conda、nvm 與中繼服務設定僅為可調整的範例，並非必須原樣複製的前置條件。請勿提交憑證。

先檢查本機設定，再檢查模型連線：

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **執行提醒：** 生成與評估可能呼叫付費模型 API、安裝 npm 套件、執行生成的程式碼及啟動瀏覽器。請使用不含無關敏感憑證的隔離開發環境。

<a id="data-preparation"></a>

## 資料準備

**資料集與實驗資料**位於 [Hugging Face 的 unhappyNN/mobileforge](https://huggingface.co/datasets/unhappyNN/mobileforge)。[`server_migrate/`](../../server_migrate/) 工具可將其還原至本機工作區：[`hf_pull.py`](../../server_migrate/hf_pull.py) 下載 ZIP 壓縮檔，並保留其相對於 Hugging Face 儲存庫的路徑。預設目的地為本儲存庫根目錄；使用 `--work-dir .` 可明確指定目前目錄。

### 下載資料與實驗產物

下載腳本已指向 `unhappyNN/mobileforge`。請在儲存庫根目錄執行：

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# 查看遠端壓縮檔清單與下載計畫。
python server_migrate/hf_pull.py --work-dir . --dry-run

# 將資料集與實驗壓縮檔下載至目前工作區。
python server_migrate/hf_pull.py --work-dir .
```

腳本需要 `HF_TOKEN`，僅下載 `.zip` 檔案；除非指定 `--force`，否則會略過大小相同的本機檔案。它**不會解壓縮檔案**。請先檢查壓縮檔內的目錄結構，再將所需檔案解壓縮至對應位置，並於生成或評估前確認任務輸入符合以下結構。

### 任務結構

每個任務包含**同一應用程式的多張截圖**。將應用程式目錄放在共同的輸入根目錄下：

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # 經人工審核的頁面與關係標註
    └── test_cases.reviewed.json # 經人工審核的導覽評估規格
```

框架直接讀取各應用程式目錄中的圖片（`.png`、`.jpg`、`.jpeg`、`.gif` 或 `.webp`）。標準評測樣本以 `metadata.reviewed.json` 與 `test_cases.reviewed.json` 作為正式的標註及導覽規格。啟用 `pipeline.route_hints` 時，框架會使用審核後的中繼資料提供路由提示。框架與評估器對自訂輸入支援回退至未審核檔案，但基準實驗應使用上述已審核檔案。

**蒐集與整理。** 本機 `dataset/mobbin-scraper/` 工作流程提供截圖蒐集與人工篩選功能。`annotator/` 產生結構化標註及測試案例，其[審核工具](../../annotator/check-tool/README.md)支援人工檢查。

> **資料取得：** 資料集與實驗資料透過 [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) 分發，不隨 Git 儲存庫附上。`dataset/`、`input/` 與 `experiments/` 由 Git 忽略且未被追蹤。因此，全新複製的儲存庫不含本機蒐集工具、截圖資料或實驗產物。本機 `dataset/` 蒐集工作區與已發布的基準資料不同。

若使用自己的截圖，可先產生初始標註及測試案例：

```bash
python -m annotator --input ./input/mobileforge/example-app
```

使用[審核工具](../../annotator/check-tool/README.md)檢查生成的標註，並在基準實驗前儲存審核後的中繼資料與測試案例。如需根據已審核的中繼資料重新產生測試案例，請先輸出草稿：

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

評估前請審核重新產生的草稿，將核准的案例儲存為 `test_cases.reviewed.json`；勿以未檢查的草稿覆寫已審核的案例。

完整流程請參閱[標註指南](../../annotator/README.md)。請僅使用有權存取及再分發的資料。

<a id="usage"></a>

## 使用方式

### 生成專案

對一個應用程式目錄中的所有截圖執行 DesignForge 代理框架：

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

專案寫入 `output/example-app/`。框架建立專案骨架，將多圖輸入與程式碼編輯工具提供給代理，並透過修復迴圈檢查建置。日誌、軌跡及生成指標儲存於該次執行的 `.designforge/` 目錄。

為輸入根目錄下的每個應用程式生成專案：

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

未指定 `--task` 時，執行名稱採用 `<app>__agentic__<model>`；可用 `--suffix` 區分重複執行。

### 執行實驗矩陣

編輯 [`exp_config.yaml`](../../exp_config.yaml)，選擇輸入應用程式、模型、方法及資源限制，再執行：

```bash
python -m evaluator run --config exp_config.yaml
```

儲存庫中的設定採用 `evaluation.eval_strategy: manual`，因此此指令**僅生成專案，不會自動評估**。選擇 `per_run` 或 `batch` 可啟用自動評估。執行完整矩陣前，請先確認模型可用性與 API 路由。

<a id="evaluation"></a>

## 評估

### 評估生成的專案

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

評估器協調建置、專案品質、效率與軌跡分析；若有測試案例，也會執行導覽測試及視覺評分。結果儲存於該次執行的 `eval/` 目錄。缺少測試案例時會略過導覽相關評估；指令成功不代表已評估全部五個面向。

### 分別執行導覽測試與視覺評分

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

導覽執行時會擷取實作畫面；視覺評分會重用這些截圖，不必重複互動。視覺報告與導覽報告分開儲存。為 `testgen run` 加上 `--no-vlm-fallback` 可停用模型輔助的點擊定位。

**論文協定與預設 CLI 的差異。** `testgen eval` 執行逐畫面視覺評分，不會自動執行論文中跨候選項目的列表式評估協定。以錨點為參照的排序實作位於 [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py)。比較方法時，請明確記錄評審模型、候選集合、排序或隨機排列設定及錨點策略。

為確保可重現性，請記錄程式碼提交、輸入與標註版本、生成與評審模型、迭代預算、路由提示設定及評估設定。範例指令僅示範工作流程，不表示預設設定可重現論文的所有表格。

更多選項請參閱[評估器指南](../../evaluator/README.md)與 [TestGen 指南](../../testgen/README.md)。使用 `python -m evaluator --help` 和 `python -m testgen --help` 查看 CLI。

<a id="repository-structure"></a>

## 儲存庫結構

| 路徑 | 職責 |
| --- | --- |
| [`designforge/`](../../designforge/) | 多圖輸入 UI-to-code 的**代理執行框架**，包含工具、專案骨架及模型存取。 |
| `dataset/` | **資料蒐集與整理**工作區，僅存於本機，不包含於目前的 Git 快照。 |
| [`server_migrate/`](../../server_migrate/) | 從 Hugging Face 下載資料集與實驗壓縮檔至本機工作區。 |
| [`annotator/`](../../annotator/) | 截圖標註、頁面關係及人工審核工具。 |
| [`testgen/`](../../testgen/) | 導覽測試生成與執行，以及視覺評審。 |
| [`evaluator/`](../../evaluator/) | 實驗協調、指標、結果彙整及報告。 |
| [`human_eval/`](../../human_eval/) | 人工評估工具與材料。 |
| `input/`、`output/`、`experiments/` | 本機任務輸入、生成專案及實驗產物。 |

<a id="citation"></a>

## 引用

若使用 MobileForge 或本程式碼庫，請引用論文。以下條目引用 arXiv 版本；本研究已獲 EMNLP 2026 主會議接受。

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
