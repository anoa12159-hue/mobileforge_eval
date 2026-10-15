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
  <a href="README_ko.md" lang="ko" dir="auto">한국어</a> |
  <a href="README_fr.md" lang="fr" dir="auto">Français</a> |
  <a href="README_de.md" lang="de" dir="auto">Deutsch</a> |
  <a href="README_ar.md" lang="ar" dir="auto">العربية</a> |
  <a href="README_th.md" lang="th" dir="auto">ไทย</a> |
  <strong>Tiếng Việt</strong>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.28645"><img src="https://img.shields.io/badge/arXiv-2607.28645-b31b1b.svg" alt="arXiv"></a>
  <img src="https://img.shields.io/badge/EMNLP-2026%20Main-blue.svg" alt="EMNLP 2026 Main">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Node.js-20%2B-339933.svg" alt="Node.js 20+">
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.28645">Bài báo</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">Mã nguồn</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">Bộ dữ liệu và dữ liệu thí nghiệm</a> •
  <a href="#overview">Tổng quan</a> •
  <a href="#set-up">Cài đặt</a> •
  <a href="#usage">Sử dụng</a> •
  <a href="#evaluation">Đánh giá</a> •
  <a href="#citation">Trích dẫn</a>
</p>

---

Kho mã nguồn chính thức của **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)**, được chấp nhận tại **hội nghị chính EMNLP 2026**.

Mã nguồn được lưu tại [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval), đúng với liên kết trong bài báo. Bộ dữ liệu và dữ liệu thí nghiệm có trên [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge).

**MobileForge** là bộ chuẩn đánh giá; **DesignForge** (`designforge/`) là khung chạy tác tử để sinh mã từ giao diện với đầu vào nhiều ảnh. Không gian làm việc cục bộ `dataset/` dùng để thu thập và chuẩn bị dữ liệu, không phải khung sinh mã.

<a id="news"></a>

## Tin tức

- **EMNLP 2026:** *Looks Right, Works Right* đã được chấp nhận tại hội nghị chính.
- Mã nguồn công khai bao gồm sinh mã, chú thích, kiểm thử điều hướng và đánh giá.

<a id="overview"></a>

## Tổng quan

Chuyển một ảnh chụp màn hình thành một trang chưa đủ để xây dựng ứng dụng di động. **MobileForge đánh giá khả năng sinh mã từ giao diện nhiều màn hình ở cấp dự án**: từ nhiều ảnh của cùng một ứng dụng, mô hình phải tạo ra một dự án duy nhất có thể build, với các màn hình nhất quán, thành phần dùng chung và điều hướng hoạt động giữa các màn hình.

Bộ chuẩn kết hợp tái tạo hình ảnh, hành vi có thể thực thi và chất lượng toàn dự án:

| Trục đánh giá | Nội dung đo lường |
| --- | --- |
| **Build** | Dự án có vượt qua kiểm tra biên dịch và build cho môi trường production hay không. |
| **Điều hướng** | Tương tác có dẫn đến màn hình mong muốn hay không. |
| **Độ trung thực hình ảnh** | Mức độ tương đồng giữa màn hình kết xuất và thiết kế tham chiếu. |
| **Khả năng bảo trì** | Chỉ số chất lượng mã và cấu trúc ở cấp dự án. |
| **Hiệu quả** | Tài nguyên dùng khi sinh mã, gồm token và số lần gọi mô hình. |

Bài báo giới thiệu **kiểm thử điều hướng tách biệt trạng thái** để tránh lỗi lan truyền, cùng **đánh giá hình ảnh theo danh sách có tham chiếu neo** để tăng độ tin cậy của mô hình chấm điểm hình ảnh.

```text
Thu thập và chuẩn bị dữ liệu        Ảnh chụp nhiều màn hình
         dataset/                          |
                                  annotator/ → siêu dữ liệu và ca kiểm thử
                                           |
                                  designforge/ → dự án đã sinh
                                           |
                                  testgen/ → điều hướng và ảnh chụp
                                           |
                                  evaluator/ → chỉ số và báo cáo
```

Đầu ra mặc định là dự án web **React + TypeScript + Tailwind CSS** tái tạo màn hình ứng dụng di động, không phải quy trình build ứng dụng Android/iOS native.

<a id="set-up"></a>

## Cài đặt

Sử dụng **Python 3.10+** (ghi chú cài đặt dùng 3.11), **Node.js 20+** và **npm**. Các lệnh dưới đây giả định shell tương thích Bash. Trên Windows, dùng WSL hoặc điều chỉnh lệnh kích hoạt môi trường cho shell của bạn.

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Tệp `requirements.txt` ở thư mục gốc là điểm cài đặt tập trung cho các phụ thuộc Python. Đánh giá qua trình duyệt cần Chromium; quá trình sinh mã cũng cần mạng để cài các phụ thuộc npm của dự án được tạo.

### Cấu hình mô hình

Chỉnh sửa các tệp hiện có [`experiment.yaml`](../../experiment.yaml) và [`models.yaml`](../../models.yaml) trước khi chạy:

| Cấu hình | Mục đích |
| --- | --- |
| `experiment.yaml` → `llm` | Nhà cung cấp và mô hình sinh mã. |
| `experiment.yaml` → `pipeline` | Framework, ngân sách số vòng lặp và gợi ý tuyến tùy chọn. |
| `experiment.yaml` → `annotator` | Mô hình chú thích ảnh và mức đồng thời. |
| `experiment.yaml` → `testgen` | Mô hình dự phòng cho điều hướng và mô hình chấm điểm hình ảnh. |
| `models.yaml` | Khả năng của mô hình và giới hạn token. |
| `exp_config.yaml` | Ma trận ứng dụng × mô hình × phương pháp và chính sách đánh giá. |

Với `llm.provider: litellm`, cung cấp thông tin xác thực cho các nhà cung cấp thực sự sử dụng, ví dụ:

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

Chọn mô hình hỗ trợ ảnh đầu vào và sử dụng công cụ để sinh mã. Nếu cần, cấu hình riêng thông tin xác thực cho mô hình chú thích và đánh giá. [`env.sh.example`](../../env.sh.example) mô tả các biến môi trường bổ sung; cấu hình Conda, nvm và dịch vụ chuyển tiếp chỉ là ví dụ để điều chỉnh, không phải điều kiện bắt buộc cần sao chép nguyên trạng. Không commit thông tin xác thực.

Kiểm tra cấu hình cục bộ trước, rồi kiểm tra kết nối mô hình:

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **Lưu ý khi chạy:** Quá trình sinh mã và đánh giá có thể gọi API tính phí, cài gói npm, thực thi mã được tạo và mở trình duyệt. Hãy dùng môi trường phát triển cô lập, không chứa bí mật không liên quan.

<a id="data-preparation"></a>

## Chuẩn bị dữ liệu

**Bộ dữ liệu và dữ liệu thí nghiệm** được lưu trên [unhappyNN/mobileforge tại Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge). Các công cụ [`server_migrate/`](../../server_migrate/) khôi phục chúng vào không gian làm việc cục bộ: [`hf_pull.py`](../../server_migrate/hf_pull.py) tải các tệp ZIP và giữ nguyên đường dẫn tương đối trong kho Hugging Face. Đích mặc định là thư mục gốc của kho này; dùng `--work-dir .` để chỉ rõ thư mục hiện tại.

### Tải dữ liệu và sản phẩm thí nghiệm

Script tải xuống trỏ tới `unhappyNN/mobileforge`. Chạy từ thư mục gốc của kho:

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# Kiểm tra danh sách tệp nén từ xa và kế hoạch tải xuống.
python server_migrate/hf_pull.py --work-dir . --dry-run

# Tải tệp nén dữ liệu và thí nghiệm vào không gian làm việc hiện tại.
python server_migrate/hf_pull.py --work-dir .
```

Script yêu cầu `HF_TOKEN`, chỉ tải tệp `.zip` và bỏ qua tệp cục bộ có cùng kích thước, trừ khi dùng `--force`. Script **không giải nén**. Hãy kiểm tra cấu trúc bên trong trước khi giải nén vào vị trí thích hợp, rồi xác nhận đầu vào có cấu trúc như dưới đây trước khi sinh mã hoặc đánh giá.

### Cấu trúc tác vụ

Mỗi tác vụ chứa **nhiều ảnh chụp màn hình của cùng một ứng dụng**. Đặt các thư mục ứng dụng dưới một thư mục đầu vào chung:

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # Chú thích trang và quan hệ đã được con người duyệt
    └── test_cases.reviewed.json # Đặc tả đánh giá điều hướng đã được con người duyệt
```

Khung chạy đọc ảnh trực tiếp trong thư mục ứng dụng (`.png`, `.jpg`, `.jpeg`, `.gif` hoặc `.webp`). Mẫu chuẩn sử dụng `metadata.reviewed.json` và `test_cases.reviewed.json` làm chú thích và đặc tả điều hướng chính thức. Khi bật `pipeline.route_hints`, khung dùng siêu dữ liệu đã duyệt để cung cấp gợi ý tuyến. Với đầu vào tùy chỉnh, khung và bộ đánh giá hỗ trợ chuyển sang tệp chưa duyệt khi cần, nhưng các lần chạy benchmark nên dùng tệp đã duyệt nêu trên.

**Thu thập và chuẩn bị.** Quy trình cục bộ `dataset/mobbin-scraper/` hỗ trợ thu thập ảnh và chọn lọc thủ công. `annotator/` tạo chú thích có cấu trúc và ca kiểm thử; [công cụ duyệt](../../annotator/check-tool/README.md) hỗ trợ con người kiểm tra.

> **Phân phối dữ liệu:** Bộ dữ liệu và dữ liệu thí nghiệm được phân phối qua [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge), không đi kèm kho Git. `dataset/`, `input/` và `experiments/` bị Git bỏ qua và không được theo dõi. Vì vậy, bản clone mới không có công cụ thu thập cục bộ, tập ảnh hoặc sản phẩm thí nghiệm. Không gian thu thập `dataset/` cục bộ khác với dữ liệu benchmark đã phát hành.

Với ảnh của riêng bạn, tạo chú thích và ca kiểm thử ban đầu bằng:

```bash
python -m annotator --input ./input/mobileforge/example-app
```

Dùng [công cụ duyệt](../../annotator/check-tool/README.md) để kiểm tra chú thích, rồi lưu siêu dữ liệu và ca kiểm thử đã duyệt trước khi dùng cho benchmark. Nếu cần tạo lại ca kiểm thử từ siêu dữ liệu đã duyệt, hãy ghi ra bản nháp trước:

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

Kiểm tra bản nháp và lưu các ca được chấp thuận thành `test_cases.reviewed.json` trước khi đánh giá. Không ghi đè ca đã duyệt bằng bản nháp chưa kiểm tra.

Xem toàn bộ quy trình trong [hướng dẫn chú thích](../../annotator/README.md). Chỉ sử dụng dữ liệu mà bạn có quyền truy cập và phân phối lại.

<a id="usage"></a>

## Sử dụng

### Sinh một dự án

Chạy DesignForge trên toàn bộ ảnh trong một thư mục ứng dụng:

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

Dự án được ghi vào `output/example-app/`. Khung tạo bộ khung dự án, cung cấp nhiều ảnh và công cụ sửa mã cho tác tử, rồi kiểm tra build qua vòng lặp sửa lỗi. Nhật ký, dấu vết thực thi và chỉ số sinh mã nằm trong thư mục `.designforge/` của lần chạy.

Để sinh dự án cho mọi ứng dụng dưới thư mục đầu vào:

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

Nếu không có `--task`, tên lần chạy theo mẫu `<app>__agentic__<model>`. Dùng `--suffix` để phân biệt các lần chạy lặp lại.

### Chạy ma trận thí nghiệm

Chỉnh [`exp_config.yaml`](../../exp_config.yaml) để chọn ứng dụng, mô hình, phương pháp và giới hạn tài nguyên, rồi chạy:

```bash
python -m evaluator run --config exp_config.yaml
```

Cấu hình có sẵn dùng `evaluation.eval_strategy: manual`, nên lệnh này **sinh dự án nhưng không tự động đánh giá**. Chọn `per_run` hoặc `batch` để bật đánh giá tự động. Kiểm tra khả năng truy cập mô hình và định tuyến API trước khi chạy toàn bộ ma trận.

<a id="evaluation"></a>

## Đánh giá

### Đánh giá dự án đã sinh

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

Bộ đánh giá phối hợp kiểm tra build, chất lượng dự án, hiệu quả và phân tích quỹ đạo thực thi; khi có ca kiểm thử, nó thực hiện thêm kiểm thử điều hướng và chấm điểm hình ảnh. Kết quả được ghi vào `eval/` của lần chạy. Thiếu ca kiểm thử sẽ bỏ qua đánh giá liên quan đến điều hướng; lệnh thành công không đồng nghĩa đã đánh giá đủ năm trục.

### Chạy điều hướng và chấm điểm hình ảnh riêng

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

Quá trình điều hướng chụp màn hình của bản triển khai; chấm điểm hình ảnh dùng lại các ảnh đó mà không lặp tương tác. Báo cáo hình ảnh tách biệt với báo cáo điều hướng. Thêm `--no-vlm-fallback` vào `testgen run` để tắt việc dùng mô hình hỗ trợ xác định vị trí nhấp.

**Giao thức trong bài báo và CLI mặc định.** `testgen eval` chấm điểm từng màn hình, không tự chạy giao thức so sánh theo danh sách giữa các ứng viên trong bài báo. Mã xếp hạng có tham chiếu neo nằm tại [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py). Khi so sánh phương pháp, hãy nêu rõ mô hình chấm điểm, tập ứng viên, thứ tự hoặc cách xáo trộn và chính sách neo.

Để so sánh có thể tái lập, ghi lại commit mã nguồn, phiên bản đầu vào và chú thích, mô hình sinh mã và chấm điểm, ngân sách vòng lặp, gợi ý tuyến và cấu hình đánh giá. Các lệnh minh họa quy trình, không khẳng định cấu hình mặc định tái tạo được mọi bảng trong bài báo.

Xem thêm tùy chọn trong [hướng dẫn bộ đánh giá](../../evaluator/README.md) và [hướng dẫn TestGen](../../testgen/README.md). Dùng `python -m evaluator --help` và `python -m testgen --help` để xem CLI.

<a id="repository-structure"></a>

## Cấu trúc kho

| Đường dẫn | Vai trò |
| --- | --- |
| [`designforge/`](../../designforge/) | **Khung chạy tác tử** UI-to-code nhiều ảnh, công cụ, tạo bộ khung dự án và truy cập mô hình. |
| `dataset/` | Không gian **thu thập và chuẩn bị dữ liệu**, chỉ có cục bộ, không nằm trong ảnh chụp Git hiện tại. |
| [`server_migrate/`](../../server_migrate/) | Tải tệp nén dữ liệu và thí nghiệm từ Hugging Face về không gian làm việc cục bộ. |
| [`annotator/`](../../annotator/) | Chú thích ảnh, quan hệ giữa các trang và công cụ duyệt thủ công. |
| [`testgen/`](../../testgen/) | Sinh và chạy kiểm thử điều hướng, mô hình chấm điểm hình ảnh. |
| [`evaluator/`](../../evaluator/) | Điều phối thí nghiệm, chỉ số, tổng hợp và báo cáo. |
| [`human_eval/`](../../human_eval/) | Công cụ và tài liệu phục vụ đánh giá bởi con người. |
| `input/`, `output/`, `experiments/` | Đầu vào tác vụ, dự án đã sinh và sản phẩm thí nghiệm cục bộ. |

<a id="citation"></a>

## Trích dẫn

Nếu sử dụng MobileForge hoặc mã nguồn này, vui lòng trích dẫn bài báo. Mục dưới đây trích dẫn bản arXiv; công trình đã được chấp nhận tại hội nghị chính EMNLP 2026.

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
