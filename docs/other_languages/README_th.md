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
  <strong>ไทย</strong> |
  <a href="README_vi.md" lang="vi" dir="auto">Tiếng Việt</a>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.28645"><img src="https://img.shields.io/badge/arXiv-2607.28645-b31b1b.svg" alt="arXiv"></a>
  <img src="https://img.shields.io/badge/EMNLP-2026%20Main-blue.svg" alt="EMNLP 2026 Main">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Node.js-20%2B-339933.svg" alt="Node.js 20+">
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2607.28645">บทความ</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">โค้ด</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">ชุดข้อมูลและข้อมูลการทดลอง</a> •
  <a href="#overview">ภาพรวม</a> •
  <a href="#set-up">การติดตั้ง</a> •
  <a href="#usage">การใช้งาน</a> •
  <a href="#evaluation">การประเมิน</a> •
  <a href="#citation">การอ้างอิง</a>
</p>

---

คลังโค้ดอย่างเป็นทางการของ **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)** ซึ่งได้รับการตอบรับในการประชุมหลัก **EMNLP 2026**

โค้ดอยู่ที่ [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval) ตามลิงก์ในบทความ ส่วนชุดข้อมูลและข้อมูลการทดลองอยู่บน [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge)

**MobileForge** คือเบนช์มาร์ก ส่วน **DesignForge** (`designforge/`) คือกรอบการทำงานสำหรับรันเอเจนต์เพื่อสร้างโค้ดจาก UI โดยรับภาพหลายภาพ พื้นที่ทำงาน `dataset/` ในเครื่องใช้รวบรวมและจัดเตรียมข้อมูล ไม่ใช่ส่วนสร้างโค้ด

<a id="news"></a>

## ข่าวสาร

- **EMNLP 2026:** *Looks Right, Works Right* ได้รับการตอบรับในการประชุมหลัก
- โค้ดที่เผยแพร่ครอบคลุมการสร้างโค้ด การทำคำอธิบายประกอบ การทดสอบการนำทาง และการประเมิน

<a id="overview"></a>

## ภาพรวม

การแปลงภาพหน้าจอเพียงภาพเดียวเป็นหน้าเว็บยังไม่เพียงพอสำหรับการสร้างแอปมือถือ **MobileForge ประเมินการสร้างโค้ดจาก UI แบบหลายหน้าจอในระดับโครงการ** เมื่อได้รับภาพหลายภาพจากแอปเดียวกัน โมเดลต้องสร้างโครงการเดียวที่บิลด์ได้ มีหน้าจอที่สอดคล้องกัน ใช้คอมโพเนนต์ร่วมกัน และนำทางข้ามหน้าจอได้จริง

เบนช์มาร์กเชื่อมโยงการสร้างภาพหน้าจอให้เหมือนต้นแบบ พฤติกรรมที่ทำงานได้ และคุณภาพโดยรวมของโครงการ:

| มิติการประเมิน | สิ่งที่วัด |
| --- | --- |
| **การบิลด์** | โครงการผ่านการคอมไพล์และการตรวจสอบบิลด์สำหรับใช้งานจริงหรือไม่ |
| **การนำทาง** | การโต้ตอบนำไปยังหน้าจอที่ต้องการหรือไม่ |
| **ความเหมือนทางภาพ** | หน้าจอที่เรนเดอร์ใกล้เคียงกับภาพอ้างอิงเพียงใด |
| **ความสามารถในการบำรุงรักษา** | ตัวชี้วัดคุณภาพโค้ดและโครงสร้างระดับโครงการ |
| **ประสิทธิภาพการใช้ทรัพยากร** | ทรัพยากรที่ใช้ในการสร้าง เช่น โทเคนและจำนวนการเรียกโมเดล |

บทความเสนอ **การทดสอบการนำทางแบบแยกสถานะ** เพื่อป้องกันความล้มเหลวต่อเนื่อง และ **การประเมินภาพแบบจัดอันดับรายการโดยมีตัวอย่างอ้างอิงเป็นจุดยึด** เพื่อเพิ่มความน่าเชื่อถือของผู้ประเมินภาพ

```text
รวบรวมและจัดเตรียมข้อมูล        ภาพหน้าจอหลายภาพ
         dataset/                          |
                                  annotator/ → เมทาดาทาและกรณีทดสอบ
                                           |
                                  designforge/ → โครงการที่สร้าง
                                           |
                                  testgen/ → การนำทางและภาพหน้าจอ
                                           |
                                  evaluator/ → ตัวชี้วัดและรายงาน
```

เป้าหมายเริ่มต้นคือโครงการเว็บ **React + TypeScript + Tailwind CSS** ที่จำลองหน้าจอแอปมือถือ ไม่ใช่กระบวนการบิลด์แอป Android/iOS แบบเนทีฟ

<a id="set-up"></a>

## การติดตั้ง

ใช้ **Python 3.10+** (บันทึกการติดตั้งใช้ 3.11), **Node.js 20+** และ **npm** คำสั่งต่อไปนี้ใช้เชลล์ที่เข้ากันได้กับ Bash บน Windows ให้ใช้ WSL หรือปรับคำสั่งเปิดใช้งานสภาพแวดล้อมให้ตรงกับเชลล์ของคุณ

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

ไฟล์ `requirements.txt` ที่รากโครงการเป็นจุดติดตั้งแพ็กเกจ Python แบบรวม การประเมินผ่านเบราว์เซอร์ต้องใช้ Chromium และการสร้างโครงการต้องเชื่อมต่อเครือข่ายเพื่อติดตั้งแพ็กเกจ npm ของโครงการที่สร้างขึ้น

### การตั้งค่าโมเดล

แก้ไข [`experiment.yaml`](../../experiment.yaml) และ [`models.yaml`](../../models.yaml) ที่มีอยู่ก่อนรัน:

| การตั้งค่า | หน้าที่ |
| --- | --- |
| `experiment.yaml` → `llm` | ผู้ให้บริการและโมเดลสร้างโค้ด |
| `experiment.yaml` → `pipeline` | เฟรมเวิร์ก งบประมาณการวนซ้ำ และคำแนะนำเส้นทางที่เลือกเปิดได้ |
| `experiment.yaml` → `annotator` | โมเดลทำคำอธิบายประกอบภาพและจำนวนงานพร้อมกัน |
| `experiment.yaml` → `testgen` | โมเดลสำรองสำหรับการนำทางและโมเดลประเมินภาพ |
| `models.yaml` | ความสามารถของโมเดลและขีดจำกัดโทเคน |
| `exp_config.yaml` | เมทริกซ์แอป × โมเดล × วิธีการ และนโยบายการประเมิน |

เมื่อใช้ `llm.provider: litellm` ให้กำหนดข้อมูลรับรองของผู้ให้บริการที่ใช้งานจริง เช่น:

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

เลือกโมเดลที่รับภาพและใช้เครื่องมือได้สำหรับการสร้างโค้ด และกำหนดข้อมูลรับรองของโมเดลทำคำอธิบายประกอบและประเมินแยกกันหากจำเป็น ตัวแปรเพิ่มเติมอยู่ใน [`env.sh.example`](../../env.sh.example) การตั้งค่า Conda, nvm และบริการตัวกลางเป็นเพียงตัวอย่างที่ควรปรับ ไม่ใช่ข้อกำหนดที่ต้องคัดลอกทั้งหมด อย่าคอมมิตข้อมูลรับรอง

ตรวจสอบการตั้งค่าในเครื่องก่อน แล้วจึงตรวจสอบการเชื่อมต่อโมเดล:

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **ข้อควรระวังในการรัน:** การสร้างและประเมินอาจเรียก API ที่มีค่าใช้จ่าย ติดตั้งแพ็กเกจ npm รันโค้ดที่สร้างขึ้น และเปิดเบราว์เซอร์ ควรใช้สภาพแวดล้อมพัฒนาที่แยกออกมาและไม่มีข้อมูลลับที่ไม่เกี่ยวข้อง

<a id="data-preparation"></a>

## การเตรียมข้อมูล

**ชุดข้อมูลและข้อมูลการทดลอง** อยู่บน [unhappyNN/mobileforge ใน Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) เครื่องมือใน [`server_migrate/`](../../server_migrate/) ใช้กู้คืนข้อมูลมายังพื้นที่ทำงานในเครื่อง โดย [`hf_pull.py`](../../server_migrate/hf_pull.py) ดาวน์โหลดไฟล์ ZIP และคงพาธสัมพัทธ์ตามคลัง Hugging Face ค่าเริ่มต้นของปลายทางคือรากคลังนี้ ใช้ `--work-dir .` เพื่อระบุโฟลเดอร์ปัจจุบันอย่างชัดเจน

### ดาวน์โหลดข้อมูลและผลการทดลอง

สคริปต์ดาวน์โหลดชี้ไปที่ `unhappyNN/mobileforge` ให้รันจากรากคลัง:

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# ตรวจสอบรายการไฟล์บนเซิร์ฟเวอร์และแผนดาวน์โหลด
python server_migrate/hf_pull.py --work-dir . --dry-run

# ดาวน์โหลดไฟล์ชุดข้อมูลและการทดลองมายังพื้นที่ทำงานปัจจุบัน
python server_migrate/hf_pull.py --work-dir .
```

สคริปต์ต้องใช้ `HF_TOKEN` ดาวน์โหลดเฉพาะ `.zip` และข้ามไฟล์ในเครื่องที่มีขนาดตรงกัน เว้นแต่ระบุ `--force` สคริปต์ **ไม่แตกไฟล์อัตโนมัติ** ให้ตรวจสอบโครงสร้างภายในก่อนแตกไฟล์ไปยังตำแหน่งที่ต้องการ และตรวจสอบรูปแบบอินพุตต่อไปนี้ก่อนสร้างหรือประเมิน

### โครงสร้างงาน

แต่ละงานประกอบด้วย **ภาพหน้าจอหลายภาพจากแอปเดียวกัน** วางโฟลเดอร์ของแต่ละแอปไว้ใต้รากอินพุตร่วมกัน:

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # คำอธิบายหน้าและความสัมพันธ์ที่ตรวจทานโดยคน
    └── test_cases.reviewed.json # ข้อกำหนดประเมินการนำทางที่ตรวจทานโดยคน
```

กรอบการทำงานอ่านภาพที่อยู่โดยตรงในโฟลเดอร์แอป (`.png`, `.jpg`, `.jpeg`, `.gif` หรือ `.webp`) ตัวอย่างมาตรฐานใช้ `metadata.reviewed.json` และ `test_cases.reviewed.json` เป็นคำอธิบายประกอบและข้อกำหนดการนำทางที่ยึดถือเป็นหลัก เมื่อเปิด `pipeline.route_hints` ระบบใช้เมทาดาทาที่ตรวจทานแล้วเป็นคำแนะนำเส้นทาง สำหรับอินพุตที่กำหนดเอง ระบบรองรับการย้อนกลับไปใช้ไฟล์ที่ยังไม่ตรวจทาน แต่การรันเบนช์มาร์กควรใช้ไฟล์ที่ตรวจทานแล้วข้างต้น

**การรวบรวมและจัดเตรียมข้อมูล** เวิร์กโฟลว์ในเครื่อง `dataset/mobbin-scraper/` ใช้รวบรวมภาพหน้าจอและคัดเลือกด้วยคน `annotator/` สร้างคำอธิบายประกอบแบบมีโครงสร้างและกรณีทดสอบ โดยมี [เครื่องมือตรวจทาน](../../annotator/check-tool/README.md) สำหรับการตรวจสอบโดยคน

> **การเข้าถึงข้อมูล:** ชุดข้อมูลและข้อมูลการทดลองเผยแพร่ผ่าน [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) ไม่ได้รวมอยู่ใน Git โดย `dataset/`, `input/` และ `experiments/` ถูกละเว้นและไม่ได้ติดตามใน Git ดังนั้นการโคลนใหม่จะไม่มีเครื่องมือรวบรวมข้อมูลในเครื่อง ชุดภาพ หรือผลการทดลอง พื้นที่รวบรวมข้อมูล `dataset/` ในเครื่องเป็นคนละส่วนกับข้อมูลเบนช์มาร์กที่เผยแพร่

สำหรับภาพของคุณเอง ให้สร้างคำอธิบายประกอบและกรณีทดสอบเบื้องต้นด้วย:

```bash
python -m annotator --input ./input/mobileforge/example-app
```

ใช้ [เครื่องมือตรวจทาน](../../annotator/check-tool/README.md) ตรวจสอบคำอธิบายประกอบและบันทึกเมทาดาทากับกรณีทดสอบที่ตรวจทานแล้วก่อนใช้กับเบนช์มาร์ก หากต้องสร้างกรณีทดสอบใหม่จากเมทาดาทาที่ตรวจทานแล้ว ให้สร้างฉบับร่างก่อน:

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

ตรวจทานฉบับร่างและบันทึกกรณีที่อนุมัติเป็น `test_cases.reviewed.json` ก่อนประเมิน อย่าเขียนทับกรณีที่ตรวจทานแล้วด้วยฉบับร่างที่ยังไม่ตรวจสอบ

ดูขั้นตอนทั้งหมดใน [คู่มือการทำคำอธิบายประกอบ](../../annotator/README.md) ใช้เฉพาะข้อมูลที่คุณมีสิทธิ์เข้าถึงและเผยแพร่ต่อ

<a id="usage"></a>

## การใช้งาน

### สร้างโครงการ

รัน DesignForge กับภาพทั้งหมดในโฟลเดอร์แอปเดียว:

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

โครงการถูกบันทึกใน `output/example-app/` ระบบสร้างโครงเริ่มต้น ส่งภาพหลายภาพและเครื่องมือแก้ไขโค้ดให้เอเจนต์ แล้วตรวจสอบบิลด์ผ่านวงรอบแก้ไข บันทึกการทำงาน ร่องรอย และตัวชี้วัดการสร้างอยู่ใน `.designforge/` ของการรันนั้น

สร้างโครงการสำหรับทุกแอปใต้รากอินพุต:

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

หากไม่ระบุ `--task` ชื่อการรันใช้รูปแบบ `<app>__agentic__<model>` ใช้ `--suffix` เพื่อแยกการรันซ้ำ

### รันเมทริกซ์การทดลอง

แก้ไข [`exp_config.yaml`](../../exp_config.yaml) เพื่อเลือกแอป โมเดล วิธีการ และขีดจำกัดทรัพยากร แล้วรัน:

```bash
python -m evaluator run --config exp_config.yaml
```

การตั้งค่าที่ให้มาใช้ `evaluation.eval_strategy: manual` ดังนั้นคำสั่งนี้ **สร้างโครงการโดยไม่ประเมินอัตโนมัติ** เลือก `per_run` หรือ `batch` เพื่อเปิดการประเมินอัตโนมัติ ตรวจสอบความพร้อมของโมเดลและการกำหนดเส้นทาง API ก่อนรันทั้งเมทริกซ์

<a id="evaluation"></a>

## การประเมิน

### ประเมินโครงการที่สร้างขึ้น

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

ตัวประเมินประสานการบิลด์ คุณภาพโครงการ ประสิทธิภาพการใช้ทรัพยากร และการวิเคราะห์ลำดับการทำงาน รวมถึงการทดสอบการนำทางและให้คะแนนภาพเมื่อมีกรณีทดสอบ ผลลัพธ์อยู่ใน `eval/` ของการรัน หากไม่มีกรณีทดสอบ ระบบจะข้ามการประเมินที่เกี่ยวข้องกับการนำทาง ดังนั้นคำสั่งสำเร็จไม่ได้แปลว่าครบทั้งห้ามิติ

### รันการนำทางและให้คะแนนภาพแยกกัน

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

การรันนำทางจะบันทึกภาพของหน้าจอที่สร้างขึ้น ส่วนการให้คะแนนภาพนำภาพเหล่านั้นกลับมาใช้โดยไม่ทำปฏิสัมพันธ์ซ้ำ รายงานภาพแยกจากรายงานการนำทาง เพิ่ม `--no-vlm-fallback` ให้ `testgen run` เพื่อปิดการใช้โมเดลช่วยระบุตำแหน่งคลิก

**ระเบียบวิธีในบทความกับ CLI เริ่มต้น** `testgen eval` ประเมินภาพทีละหน้าจอ ไม่ได้รันวิธีจัดอันดับรายการข้ามตัวเลือกในบทความโดยอัตโนมัติ โค้ดการจัดอันดับโดยอ้างอิงจุดยึดอยู่ใน [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py) เมื่อเปรียบเทียบวิธีการ ให้ระบุโมเดลผู้ประเมิน ชุดตัวเลือก ลำดับหรือการสับลำดับ และนโยบายจุดยึดอย่างชัดเจน

เพื่อให้ทำซ้ำได้ ให้บันทึกคอมมิตโค้ด รุ่นอินพุตและคำอธิบายประกอบ โมเดลสร้างและประเมิน งบประมาณการวนซ้ำ การตั้งค่าคำแนะนำเส้นทาง และการประเมิน คำสั่งตัวอย่างแสดงเวิร์กโฟลว์ ไม่ได้รับรองว่าค่าเริ่มต้นจะทำซ้ำทุกตารางในบทความได้

ดูตัวเลือกเพิ่มเติมใน [คู่มือตัวประเมิน](../../evaluator/README.md) และ [คู่มือ TestGen](../../testgen/README.md) ตรวจสอบ CLI ด้วย `python -m evaluator --help` และ `python -m testgen --help`

<a id="repository-structure"></a>

## โครงสร้างคลัง

| พาธ | หน้าที่ |
| --- | --- |
| [`designforge/`](../../designforge/) | **กรอบการทำงานเอเจนต์** สำหรับ UI-to-code แบบหลายภาพ พร้อมเครื่องมือ โครงโครงการ และการเข้าถึงโมเดล |
| `dataset/` | พื้นที่ **รวบรวมและจัดเตรียมข้อมูล** ในเครื่องเท่านั้น ไม่รวมในสแนปช็อต Git ปัจจุบัน |
| [`server_migrate/`](../../server_migrate/) | ดาวน์โหลดชุดข้อมูลและไฟล์การทดลองจาก Hugging Face มายังเครื่อง |
| [`annotator/`](../../annotator/) | คำอธิบายประกอบภาพ ความสัมพันธ์ระหว่างหน้า และเครื่องมือตรวจทานโดยคน |
| [`testgen/`](../../testgen/) | สร้างและรันทดสอบการนำทาง รวมถึงประเมินภาพ |
| [`evaluator/`](../../evaluator/) | จัดการการทดลอง ตัวชี้วัด การรวมผล และรายงาน |
| [`human_eval/`](../../human_eval/) | เครื่องมือและเอกสารสำหรับการประเมินโดยคน |
| `input/`, `output/`, `experiments/` | อินพุตงาน โครงการที่สร้าง และผลการทดลองในเครื่อง |

<a id="citation"></a>

## การอ้างอิง

หากใช้ MobileForge หรือโค้ดนี้ โปรดอ้างอิงบทความ รายการต่อไปนี้อ้างอิงฉบับ arXiv โดยงานได้รับการตอบรับในการประชุมหลัก EMNLP 2026

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
