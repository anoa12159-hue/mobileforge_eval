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
  <a href="README_zh-TW.md" lang="zh-TW" dir="auto">繁體中文</a> |
  <strong>日本語</strong> |
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
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">コード</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">データセット・実験データ</a> •
  <a href="#overview">概要</a> •
  <a href="#set-up">セットアップ</a> •
  <a href="#usage">使い方</a> •
  <a href="#evaluation">評価</a> •
  <a href="#citation">引用</a>
</p>

---

本リポジトリは、**EMNLP 2026 本会議**に採択された **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)** の公式コードリポジトリです。

コードは論文に記載された [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval) で公開しています。データセットと実験データは [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) から入手できます。

**MobileForge** はベンチマークです。**DesignForge**（`designforge/`）は、複数画像を入力とする UI-to-code 生成のエージェント実行基盤です。ローカルの `dataset/` ワークスペースはデータ収集・整備用であり、生成基盤ではありません。

<a id="news"></a>

## ニュース

- **EMNLP 2026：** *Looks Right, Works Right* が本会議に採択されました。
- 公開コードは生成、アノテーション、ナビゲーションテスト、評価をカバーしています。

<a id="overview"></a>

## 概要

1 枚のスクリーンショットをページに変換するだけでは、モバイルアプリは構築できません。**MobileForge は、プロジェクト単位の複数画面 UI-to-code 生成を評価します**。同じアプリの複数のスクリーンショットから、一貫した画面、共有コンポーネント、動作する画面間ナビゲーションを備えた、ビルド可能な単一プロジェクトを生成することが求められます。

視覚的な再現、実行可能な動作、プロジェクト全体の品質を、次の観点で評価します。

| 評価軸 | 評価内容 |
| --- | --- |
| **ビルド** | コンパイルと本番用ビルドの検証に合格するか。 |
| **ナビゲーション** | 操作によって意図した画面に到達するか。 |
| **視覚的忠実度** | レンダリングされた画面が参照デザインにどれだけ近いか。 |
| **保守性** | プロジェクト全体のコード品質と構造に関する指標。 |
| **効率** | トークン数やモデル呼び出し回数などの生成リソース使用量。 |

論文では、失敗の連鎖を避ける**状態分離型ナビゲーションテスト**と、視覚評価の信頼性を高める**アンカー参照型リストワイズ視覚評価**を提案しています。

```text
データ収集・整備        複数画面のスクリーンショット
         dataset/                          |
                                  annotator/ → メタデータとテストケース
                                           |
                                  designforge/ → 生成プロジェクト
                                           |
                                  testgen/ → ナビゲーションと画像
                                           |
                                  evaluator/ → 指標とレポート
```

デフォルトの生成対象は、モバイルアプリの画面を再現する **React + TypeScript + Tailwind CSS** の Web プロジェクトです。Android/iOS のネイティブアプリをビルドするパイプラインではありません。

<a id="set-up"></a>

## セットアップ

**Python 3.10+**（リポジトリのセットアップ資料では 3.11）、**Node.js 20+**、**npm** を使用してください。以下のコマンドは Bash 互換シェルを想定しています。Windows では WSL を使うか、シェルに合わせて仮想環境の有効化コマンドを変更してください。

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

ルートの `requirements.txt` が Python 依存関係の統一インストール先です。ブラウザー評価には Chromium が必要です。生成時には、生成プロジェクトの npm 依存関係をインストールするためのネットワーク接続も必要です。

### モデルの設定

実行前に、既存の [`experiment.yaml`](../../experiment.yaml) と [`models.yaml`](../../models.yaml) を編集してください。

| 設定 | 用途 |
| --- | --- |
| `experiment.yaml` → `llm` | プロバイダーと生成モデル。 |
| `experiment.yaml` → `pipeline` | フレームワーク、反復回数の予算、任意のルートヒント。 |
| `experiment.yaml` → `annotator` | スクリーンショットのアノテーションモデルと並行実行数。 |
| `experiment.yaml` → `testgen` | ナビゲーションのフォールバックモデルと視覚評価モデル。 |
| `models.yaml` | モデルの能力とトークン制限。 |
| `exp_config.yaml` | アプリ × モデル × 手法の実験行列と評価方針。 |

`llm.provider: litellm` を使う場合は、実際に使用するプロバイダーの認証情報を設定します。例：

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

生成には画像入力とツール使用に対応したモデルを選び、必要に応じてアノテーション用・評価用の認証情報を別途設定してください。[`env.sh.example`](../../env.sh.example) は追加の環境変数を説明しています。Conda、nvm、中継サービスの設定は調整用の例であり、そのままコピーする必須条件ではありません。認証情報をコミットしないでください。

まずローカル設定を確認し、次にモデルへの接続を確認します。

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **実行上の注意：** 生成と評価は、有料モデル API の呼び出し、npm パッケージのインストール、生成コードの実行、ブラウザーの起動を伴う場合があります。無関係な機密情報を含まない、隔離された開発環境を使用してください。

<a id="data-preparation"></a>

## データの準備

**データセットと実験データ**は [Hugging Face の unhappyNN/mobileforge](https://huggingface.co/datasets/unhappyNN/mobileforge) にあります。[`server_migrate/`](../../server_migrate/) のツールでローカルに復元できます。[`hf_pull.py`](../../server_migrate/hf_pull.py) は、Hugging Face リポジトリ内の相対パスを保持して ZIP をダウンロードします。デフォルトの保存先は本リポジトリのルートです。`--work-dir .` で現在のディレクトリを明示できます。

### データと実験成果物のダウンロード

ダウンロードスクリプトは `unhappyNN/mobileforge` を参照します。リポジトリのルートで実行してください。

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# リモートのアーカイブ一覧とダウンロード計画を確認。
python server_migrate/hf_pull.py --work-dir . --dry-run

# データと実験アーカイブを現在のワークスペースにダウンロード。
python server_migrate/hf_pull.py --work-dir .
```

`HF_TOKEN` が必要です。対象は `.zip` のみで、同じサイズのローカルファイルは `--force` を指定しない限りスキップします。スクリプトは**アーカイブを展開しません**。内部のディレクトリ構造を確認してから適切な場所に展開し、生成・評価前に次の入力構造と一致することを確認してください。

### タスクの構成

各タスクは**同じアプリの複数のスクリーンショット**を含みます。アプリごとのディレクトリを共通の入力ルート配下に配置します。

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # 人がレビューした画面・関係のアノテーション
    └── test_cases.reviewed.json # 人がレビューしたナビゲーション評価仕様
```

実行基盤は各アプリディレクトリ直下の画像（`.png`、`.jpg`、`.jpeg`、`.gif`、`.webp`）を読み込みます。標準サンプルでは、`metadata.reviewed.json` と `test_cases.reviewed.json` を正式なアノテーションとナビゲーション仕様として使用します。`pipeline.route_hints` を有効にすると、レビュー済みメタデータがルートヒントに使われます。独自入力では未レビューのファイルへのフォールバックも可能ですが、ベンチマーク実行では上記のレビュー済みファイルを使用してください。

**収集と整備。** ローカルの `dataset/mobbin-scraper/` はスクリーンショット収集と手動選別のワークフローです。`annotator/` は構造化アノテーションとテストケースを生成し、[レビューツール](../../annotator/check-tool/README.md)で人による確認ができます。

> **データの提供形態：** データセットと実験データは [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) で配布し、Git リポジトリには含めていません。`dataset/`、`input/`、`experiments/` は Git の対象外で追跡されていません。そのため、新規クローンにはローカル収集ツール、スクリーンショット群、実験成果物は含まれません。ローカルの収集用 `dataset/` と公開ベンチマークデータは別物です。

独自のスクリーンショットから初期アノテーションとテストケースを生成するには：

```bash
python -m annotator --input ./input/mobileforge/example-app
```

[レビューツール](../../annotator/check-tool/README.md)でアノテーションを確認し、ベンチマークに使う前にレビュー済みメタデータとテストケースを保存してください。レビュー済みメタデータからテストケースを再生成する場合は、まず下書きを作成します。

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

評価前に下書きをレビューし、承認したケースを `test_cases.reviewed.json` として保存します。未確認の下書きでレビュー済みケースを上書きしないでください。

全体の手順は[アノテーションガイド](../../annotator/README.md)を参照してください。アクセスと再配布が許可されたデータのみを使用してください。

<a id="usage"></a>

## 使い方

### プロジェクトの生成

1 つのアプリディレクトリ内の全スクリーンショットに対して DesignForge を実行します。

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

プロジェクトは `output/example-app/` に書き込まれます。実行基盤はひな形を作成し、複数画像とコード編集ツールをエージェントに渡して、修復ループ付きでビルドを検証します。ログ、トレース、生成指標は各実行の `.designforge/` に保存されます。

入力ルート配下の全アプリに対して生成するには：

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

`--task` を省略すると実行名は `<app>__agentic__<model>` になります。繰り返し実行を区別するには `--suffix` を使います。

### 実験行列の実行

[`exp_config.yaml`](../../exp_config.yaml) で入力アプリ、モデル、手法、リソース制限を設定し、実行します。

```bash
python -m evaluator run --config exp_config.yaml
```

同梱設定は `evaluation.eval_strategy: manual` のため、このコマンドは**プロジェクトを生成するだけで自動評価しません**。自動評価には `per_run` または `batch` を選択してください。全行列の実行前にモデルの利用可否と API ルーティングを確認してください。

<a id="evaluation"></a>

## 評価

### 生成プロジェクトの評価

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

評価器はビルド、プロジェクト品質、効率、軌跡分析を統合し、テストケースがあればナビゲーションテストと視覚評価も実行します。結果は各実行の `eval/` に保存されます。テストケースがない場合はナビゲーション関連の評価が省略されるため、コマンドの成功だけでは 5 つの評価軸すべてを評価したことにはなりません。

### ナビゲーションと視覚評価の個別実行

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

ナビゲーション実行時に実装画面を撮影し、視覚評価は操作を繰り返さずにその画像を再利用します。視覚レポートとナビゲーションレポートは別々です。`testgen run` に `--no-vlm-fallback` を追加すると、モデルを用いたクリック対象の特定を無効にできます。

**論文のプロトコルとデフォルト CLI の違い。** `testgen eval` は画面単位の視覚評価を実行し、論文の候補間リストワイズ評価を自動実行するものではありません。アンカー参照型ランキングの実装は [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py) にあります。手法の比較では、評価モデル、候補集合、順序・シャッフル設定、アンカー方針を明示してください。

再現可能な比較のため、コードのコミット、入力・アノテーションのバージョン、生成・評価モデル、反復予算、ルートヒント設定、評価設定を記録してください。コマンド例は手順の例示であり、デフォルト設定による論文の全表の再現を保証するものではありません。

追加オプションは[評価器ガイド](../../evaluator/README.md)と [TestGen ガイド](../../testgen/README.md)を参照してください。CLI は `python -m evaluator --help` と `python -m testgen --help` で確認できます。

<a id="repository-structure"></a>

## リポジトリ構成

| パス | 役割 |
| --- | --- |
| [`designforge/`](../../designforge/) | 複数画像 UI-to-code の**エージェント実行基盤**。ツール、ひな形、モデルアクセスを含みます。 |
| `dataset/` | **データ収集・整備**用ワークスペース。現在の Git スナップショットには含まれず、ローカルのみです。 |
| [`server_migrate/`](../../server_migrate/) | Hugging Face からデータセットと実験アーカイブをローカルにダウンロード。 |
| [`annotator/`](../../annotator/) | スクリーンショットのアノテーション、画面間関係、人によるレビュー。 |
| [`testgen/`](../../testgen/) | ナビゲーションテストの生成・実行と視覚評価。 |
| [`evaluator/`](../../evaluator/) | 実験の制御、指標、集計、レポート。 |
| [`human_eval/`](../../human_eval/) | 人手評価のツールと資料。 |
| `input/`、`output/`、`experiments/` | ローカルのタスク入力、生成プロジェクト、実験成果物。 |

<a id="citation"></a>

## 引用

MobileForge または本コードを使用する場合は、論文を引用してください。以下は arXiv 版の引用情報です。本研究は EMNLP 2026 本会議に採択されています。

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
