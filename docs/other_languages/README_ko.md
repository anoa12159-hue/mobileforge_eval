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
  <a href="README_ja.md" lang="ja" dir="auto">日本語</a> |
  <strong>한국어</strong> |
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
  <a href="https://arxiv.org/abs/2607.28645">논문</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">코드</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">데이터셋 및 실험 데이터</a> •
  <a href="#overview">개요</a> •
  <a href="#set-up">환경 설정</a> •
  <a href="#usage">사용법</a> •
  <a href="#evaluation">평가</a> •
  <a href="#citation">인용</a>
</p>

---

**EMNLP 2026 메인 컨퍼런스**에 채택된 **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)** 의 공식 코드 저장소입니다.

코드는 논문에 명시된 [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval)에 공개되어 있습니다. 데이터셋과 실험 데이터는 [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge)에서 제공합니다.

**MobileForge**는 벤치마크이며, **DesignForge**(`designforge/`)는 여러 이미지를 입력으로 사용하는 UI-to-code 생성용 에이전트 실행 프레임워크입니다. 로컬 `dataset/` 작업 공간은 데이터 수집 및 정제용이며 생성 프레임워크가 아닙니다.

<a id="news"></a>

## 소식

- **EMNLP 2026:** *Looks Right, Works Right*가 메인 컨퍼런스에 채택되었습니다.
- 공개 코드는 생성, 주석 작성, 탐색 테스트 및 평가 과정을 포함합니다.

<a id="overview"></a>

## 개요

스크린샷 하나를 페이지로 바꾸는 것만으로는 모바일 앱을 만들 수 없습니다. **MobileForge는 프로젝트 수준의 다중 화면 UI-to-code 생성을 평가합니다.** 동일한 앱의 여러 스크린샷을 입력받은 모델은 일관된 화면, 공유 컴포넌트, 정상적으로 작동하는 화면 간 탐색을 갖춘 하나의 빌드 가능한 프로젝트를 만들어야 합니다.

벤치마크는 시각적 재현, 실행 가능한 동작, 프로젝트 전체 품질을 연결합니다.

| 평가 축 | 측정 내용 |
| --- | --- |
| **빌드** | 생성된 프로젝트가 컴파일 및 프로덕션 빌드 검사를 통과하는지 여부. |
| **탐색** | 상호작용으로 의도한 화면에 도달하는지 여부. |
| **시각적 충실도** | 렌더링 화면과 참조 디자인의 유사도. |
| **유지보수성** | 프로젝트 수준의 코드 품질 및 구조 지표. |
| **효율성** | 토큰 수와 모델 호출 수 등 생성 자원 사용량. |

논문은 탐색 실패의 연쇄 전파를 막는 **상태 격리형 탐색 테스트**와 시각 평가의 신뢰도를 높이는 **앵커 참조형 리스트와이즈 시각 평가**를 제안합니다.

```text
데이터 수집 및 정제        다중 화면 스크린샷
         dataset/                          |
                                  annotator/ → 메타데이터 및 테스트 케이스
                                           |
                                  designforge/ → 생성 프로젝트
                                           |
                                  testgen/ → 탐색 및 스크린샷
                                           |
                                  evaluator/ → 지표 및 보고서
```

기본 생성 대상은 모바일 앱 화면을 재현하는 **React + TypeScript + Tailwind CSS** 웹 프로젝트입니다. 네이티브 Android/iOS 빌드 파이프라인은 아닙니다.

<a id="set-up"></a>

## 환경 설정

**Python 3.10+**(저장소 설정 문서는 3.11 사용), **Node.js 20+**, **npm**을 사용하세요. 아래 명령은 Bash 호환 셸을 가정합니다. Windows에서는 WSL을 사용하거나 사용하는 셸에 맞게 환경 활성화 명령을 바꾸세요.

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

루트의 `requirements.txt`는 Python 의존성을 통합 설치하는 진입점입니다. 브라우저 평가에는 Chromium이 필요하며, 생성 프로젝트의 npm 의존성을 설치하려면 네트워크 연결도 필요합니다.

### 모델 설정

실행 전에 기존 [`experiment.yaml`](../../experiment.yaml)과 [`models.yaml`](../../models.yaml)을 편집하세요.

| 설정 | 용도 |
| --- | --- |
| `experiment.yaml` → `llm` | 제공업체 및 생성 모델. |
| `experiment.yaml` → `pipeline` | 프레임워크, 반복 예산, 선택적 라우트 힌트. |
| `experiment.yaml` → `annotator` | 스크린샷 주석 모델과 동시 실행 수. |
| `experiment.yaml` → `testgen` | 탐색 폴백 모델과 시각 평가 모델. |
| `models.yaml` | 모델 기능과 토큰 제한. |
| `exp_config.yaml` | 앱 × 모델 × 방법 실험 매트릭스와 평가 정책. |

`llm.provider: litellm`을 사용할 경우 실제 사용하는 제공업체의 인증 정보를 설정하세요. 예:

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

생성에는 이미지 입력과 도구 사용을 지원하는 모델을 선택하고, 필요하면 주석 및 평가 모델의 인증 정보를 별도로 설정하세요. 추가 환경 변수는 [`env.sh.example`](../../env.sh.example)에 설명되어 있습니다. Conda, nvm, 중계 서비스 설정은 수정해서 사용하는 예시이지 그대로 복사해야 하는 필수 조건이 아닙니다. 인증 정보를 커밋하지 마세요.

먼저 로컬 설정을 확인한 다음 모델 연결을 확인합니다.

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **실행 주의사항:** 생성과 평가는 유료 모델 API 호출, npm 패키지 설치, 생성 코드 실행, 브라우저 시작을 수행할 수 있습니다. 관련 없는 비밀 정보가 없는 격리된 개발 환경을 사용하세요.

<a id="data-preparation"></a>

## 데이터 준비

**데이터셋 및 실험 데이터**는 [Hugging Face의 unhappyNN/mobileforge](https://huggingface.co/datasets/unhappyNN/mobileforge)에 있습니다. [`server_migrate/`](../../server_migrate/) 도구는 이를 로컬 작업 공간으로 복원합니다. [`hf_pull.py`](../../server_migrate/hf_pull.py)는 Hugging Face 저장소의 상대 경로를 유지하며 ZIP 파일을 다운로드합니다. 기본 저장 위치는 이 저장소의 루트이며, `--work-dir .`로 현재 디렉터리를 명시할 수 있습니다.

### 데이터 및 실험 산출물 다운로드

다운로드 스크립트는 `unhappyNN/mobileforge`를 가리킵니다. 저장소 루트에서 실행하세요.

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# 원격 압축 파일 목록과 다운로드 계획을 확인합니다.
python server_migrate/hf_pull.py --work-dir . --dry-run

# 데이터셋과 실험 압축 파일을 현재 작업 공간에 다운로드합니다.
python server_migrate/hf_pull.py --work-dir .
```

스크립트는 `HF_TOKEN`이 필요하고 `.zip` 파일만 다운로드합니다. `--force`를 지정하지 않으면 크기가 같은 로컬 파일은 건너뜁니다. **압축을 해제하지는 않습니다.** 먼저 압축 파일 내부 구조를 확인한 뒤 필요한 파일을 적절한 위치에 풀고, 생성이나 평가 전에 입력 구조가 아래와 일치하는지 확인하세요.

### 태스크 구조

각 태스크는 **동일한 앱의 여러 스크린샷**으로 구성됩니다. 공통 입력 루트 아래에 앱별 디렉터리를 배치하세요.

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # 사람이 검토한 페이지 및 관계 주석
    └── test_cases.reviewed.json # 사람이 검토한 탐색 평가 명세
```

프레임워크는 앱 디렉터리 바로 아래의 이미지(`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`)를 읽습니다. 표준 벤치마크 샘플은 `metadata.reviewed.json`과 `test_cases.reviewed.json`을 기준 주석 및 탐색 명세로 사용합니다. `pipeline.route_hints`가 활성화되면 검토된 메타데이터로 라우트 힌트를 제공합니다. 사용자 정의 입력에는 검토 전 파일로의 폴백도 지원하지만, 벤치마크 실행에는 위의 검토된 파일을 사용해야 합니다.

**수집 및 정제.** 로컬 `dataset/mobbin-scraper/` 워크플로는 스크린샷 수집 및 수동 선별을 지원합니다. `annotator/`는 구조화된 주석과 테스트 케이스를 생성하며, [검토 도구](../../annotator/check-tool/README.md)를 통해 사람이 확인할 수 있습니다.

> **데이터 제공:** 데이터셋 및 실험 데이터는 [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge)로 배포하며 Git 저장소에 포함되지 않습니다. `dataset/`, `input/`, `experiments/`는 Git에서 무시되고 추적되지 않으므로 새로 복제한 저장소에는 로컬 수집 도구, 스크린샷 모음, 실험 산출물이 없습니다. 로컬 `dataset/` 수집 작업 공간과 공개 벤치마크 데이터는 구분됩니다.

직접 준비한 스크린샷의 초기 주석과 테스트 케이스를 생성하려면:

```bash
python -m annotator --input ./input/mobileforge/example-app
```

[검토 도구](../../annotator/check-tool/README.md)로 생성된 주석을 확인하고, 벤치마크에 사용하기 전에 검토된 메타데이터와 테스트 케이스를 저장하세요. 검토된 메타데이터에서 테스트 케이스를 다시 생성할 때는 먼저 초안을 만드세요.

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

평가 전에 초안을 검토하고 승인된 케이스를 `test_cases.reviewed.json`으로 저장하세요. 검토하지 않은 초안으로 기존 검토된 케이스를 덮어쓰지 마세요.

전체 과정은 [주석 가이드](../../annotator/README.md)를 참고하세요. 접근 및 재배포 권한이 있는 데이터만 사용하세요.

<a id="usage"></a>

## 사용법

### 프로젝트 생성

앱 디렉터리의 모든 스크린샷에 DesignForge를 실행합니다.

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

프로젝트는 `output/example-app/`에 저장됩니다. 프레임워크는 프로젝트 기본 구조를 만들고, 다중 이미지와 코드 편집 도구를 에이전트에 제공하며, 수정 루프를 통해 빌드를 검사합니다. 로그, 트레이스, 생성 지표는 해당 실행의 `.designforge/` 디렉터리에 저장됩니다.

입력 루트의 모든 앱에 대해 프로젝트를 생성하려면:

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

`--task`를 생략하면 실행 이름은 `<app>__agentic__<model>` 형식을 따릅니다. 반복 실행을 구분하려면 `--suffix`를 사용하세요.

### 실험 매트릭스 실행

[`exp_config.yaml`](../../exp_config.yaml)에서 입력 앱, 모델, 방법, 자원 제한을 선택한 다음 실행합니다.

```bash
python -m evaluator run --config exp_config.yaml
```

저장소의 설정은 `evaluation.eval_strategy: manual`을 사용하므로 이 명령은 **프로젝트만 생성하고 자동 평가하지 않습니다**. 자동 평가에는 `per_run` 또는 `batch`를 선택하세요. 전체 매트릭스를 실행하기 전에 모델 가용성과 API 라우팅을 확인하세요.

<a id="evaluation"></a>

## 평가

### 생성 프로젝트 평가

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

평가기는 빌드, 프로젝트 품질, 효율성, 실행 궤적 분석을 조율하며, 테스트 케이스가 있으면 탐색 테스트와 시각 평가도 수행합니다. 결과는 해당 실행의 `eval/`에 저장됩니다. 테스트 케이스가 없으면 탐색 관련 평가를 건너뛰므로, 명령 성공만으로 다섯 평가 축을 모두 평가했다고 볼 수 없습니다.

### 탐색과 시각 평가를 별도로 실행

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

탐색 실행에서 구현 화면을 캡처하고, 시각 평가는 상호작용을 반복하지 않고 해당 이미지를 재사용합니다. 시각 보고서와 탐색 보고서는 별개입니다. `testgen run`에 `--no-vlm-fallback`을 추가하면 모델 기반 클릭 대상 식별을 비활성화합니다.

**논문 프로토콜과 기본 CLI의 차이.** `testgen eval`은 화면별 시각 평가를 수행하며, 논문의 후보 간 리스트와이즈 프로토콜을 자동 실행하지 않습니다. 앵커 참조 순위 평가 구현은 [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py)에 있습니다. 방법을 비교할 때 평가 모델, 후보 집합, 순서 및 셔플 설정, 앵커 정책을 명확히 기록하세요.

재현 가능한 비교를 위해 코드 커밋, 입력 및 주석 버전, 생성 및 평가 모델, 반복 예산, 라우트 힌트와 평가 설정을 기록하세요. 예시 명령은 워크플로를 설명하기 위한 것이며, 기본 설정으로 논문의 모든 표를 재현한다는 의미는 아닙니다.

추가 옵션은 [평가기 가이드](../../evaluator/README.md) 및 [TestGen 가이드](../../testgen/README.md)를 참고하세요. CLI는 `python -m evaluator --help`와 `python -m testgen --help`로 확인합니다.

<a id="repository-structure"></a>

## 저장소 구조

| 경로 | 역할 |
| --- | --- |
| [`designforge/`](../../designforge/) | 다중 이미지 UI-to-code **에이전트 실행 프레임워크**, 도구, 기본 구조 생성 및 모델 접근. |
| `dataset/` | **데이터 수집 및 정제** 작업 공간. 현재 Git 스냅샷에 포함되지 않는 로컬 전용 디렉터리. |
| [`server_migrate/`](../../server_migrate/) | Hugging Face의 데이터셋 및 실험 압축 파일을 로컬로 다운로드. |
| [`annotator/`](../../annotator/) | 스크린샷 주석, 페이지 관계, 사람의 검토를 위한 도구. |
| [`testgen/`](../../testgen/) | 탐색 테스트 생성·실행 및 시각 평가. |
| [`evaluator/`](../../evaluator/) | 실험 조율, 지표, 집계 및 보고서. |
| [`human_eval/`](../../human_eval/) | 사람에 의한 평가를 위한 도구와 자료. |
| `input/`, `output/`, `experiments/` | 로컬 태스크 입력, 생성 프로젝트, 실험 산출물. |

<a id="citation"></a>

## 인용

MobileForge 또는 이 코드베이스를 사용한다면 논문을 인용해 주세요. 아래 항목은 arXiv 버전이며, 본 연구는 EMNLP 2026 메인 컨퍼런스에 채택되었습니다.

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
