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
  <strong>العربية</strong> |
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
  <a href="https://arxiv.org/abs/2607.28645">الورقة</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">الشيفرة</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">مجموعة البيانات وبيانات التجارب</a> •
  <a href="#overview">نظرة عامة</a> •
  <a href="#set-up">الإعداد</a> •
  <a href="#usage">الاستخدام</a> •
  <a href="#evaluation">التقييم</a> •
  <a href="#citation">الاستشهاد</a>
</p>

---

<div dir="rtl" lang="ar">

هذا هو المستودع الرسمي لشيفرة **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)**، المقبولة في **المؤتمر الرئيسي EMNLP 2026**.

تُستضاف الشيفرة في [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval)، وهو المستودع المشار إليه في الورقة. تتوفر مجموعة البيانات وبيانات التجارب على [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge).

**MobileForge** هو معيار التقييم؛ أما **DesignForge** (`designforge/`) فهو إطار تشغيل الوكيل لتوليد الشيفرة من واجهات المستخدم اعتمادًا على صور متعددة. تُستخدم مساحة العمل المحلية `dataset/` لجمع البيانات وتنظيمها، وليست إطارًا لتوليد الشيفرة.

<a id="news"></a>

## الأخبار

- **EMNLP 2026:** قُبلت ورقة *Looks Right, Works Right* في المؤتمر الرئيسي.
- تشمل الشيفرة المفتوحة التوليد والتوسيم واختبارات التنقل والتقييم.

<a id="overview"></a>

## نظرة عامة

لا يكفي تحويل لقطة شاشة واحدة إلى صفحة لإنشاء تطبيق جوّال. **يقيّم MobileForge توليد الشيفرة من واجهات متعددة الشاشات على مستوى المشروع**: انطلاقًا من عدة لقطات للتطبيق نفسه، يجب أن ينتج النموذج مشروعًا واحدًا قابلًا للبناء، بشاشات متسقة ومكونات مشتركة وتنقل فعّال بين الشاشات.

يربط المعيار بين إعادة البناء البصري والسلوك القابل للتنفيذ وجودة المشروع ككل:

| محور التقييم | ما يقيسه |
| --- | --- |
| **البناء** | اجتياز المشروع لفحوص الترجمة والبناء للإنتاج. |
| **التنقل** | وصول التفاعلات إلى الشاشات المقصودة. |
| **المطابقة البصرية** | مدى تشابه الشاشات المعروضة مع التصاميم المرجعية. |
| **قابلية الصيانة** | مؤشرات جودة الشيفرة وبنية المشروع. |
| **الكفاءة** | الموارد المستهلكة في التوليد، بما فيها الرموز واستدعاءات النماذج. |

تقدم الورقة **اختبارات تنقل مع عزل الحالات** لمنع تسلسل الإخفاقات، و**تقييمًا بصريًا قائمًا على القوائم مع مراجع ارتكاز** لتحسين موثوقية الحكم البصري.

<div dir="ltr">

```text
جمع البيانات وتنظيمها        لقطات لشاشات متعددة
         dataset/                          |
                                  annotator/ → البيانات الوصفية وحالات الاختبار
                                           |
                                  designforge/ → المشروع المولّد
                                           |
                                  testgen/ → التنقل ولقطات الشاشة
                                           |
                                  evaluator/ → المقاييس والتقارير
```

</div>

الهدف الافتراضي هو مشروع ويب يستخدم **React + TypeScript + Tailwind CSS** لإعادة إنشاء شاشات تطبيقات الجوّال، وليس مسار بناء لتطبيقات Android أو iOS الأصلية.

<a id="set-up"></a>

## الإعداد

استخدم **Python 3.10+** (تستخدم ملاحظات الإعداد الإصدار 3.11)، و**Node.js 20+**، و**npm**. تفترض الأوامر صدفة متوافقة مع Bash؛ على Windows، استخدم WSL أو عدّل أمر تفعيل البيئة ليناسب صدفتك.

<div dir="ltr">

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

</div>

ملف `requirements.txt` في الجذر هو نقطة التثبيت الموحدة لاعتماديات Python. يتطلب التقييم داخل المتصفح Chromium، ويتطلب التوليد اتصالًا بالشبكة لتثبيت اعتماديات npm للمشروع الناتج.

### تهيئة النماذج

عدّل الملفين الموجودين [`experiment.yaml`](../../experiment.yaml) و[`models.yaml`](../../models.yaml) قبل التشغيل:

| الإعداد | الغرض |
| --- | --- |
| `experiment.yaml` → `llm` | المزوّد ونماذج التوليد. |
| `experiment.yaml` → `pipeline` | إطار العمل وميزانية التكرار وتلميحات المسارات الاختيارية. |
| `experiment.yaml` → `annotator` | نموذج توسيم لقطات الشاشة وعدد العمليات المتزامنة. |
| `experiment.yaml` → `testgen` | النموذج الاحتياطي للتنقل ونموذج الحكم البصري. |
| `models.yaml` | قدرات النماذج وحدود الرموز. |
| `exp_config.yaml` | مصفوفة التطبيق × النموذج × الطريقة وسياسة التقييم. |

عند استخدام `llm.provider: litellm`، وفّر بيانات اعتماد المزوّدين الذين تستخدمهم فعليًا، مثلًا:

<div dir="ltr">

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

</div>

اختر للتوليد نموذجًا يدعم الصور واستخدام الأدوات، وهيّئ بيانات اعتماد نماذج التوسيم والتقييم منفصلةً عند الحاجة. يوضح [`env.sh.example`](../../env.sh.example) متغيرات بيئة إضافية؛ إعدادات Conda وnvm وخدمات الترحيل فيه أمثلة قابلة للتعديل، وليست متطلبات يجب نسخها كما هي. لا تُدرج بيانات الاعتماد في التزامات Git.

افحص الإعداد المحلي أولًا، ثم الاتصال بالنماذج:

<div dir="ltr">

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

</div>

> **تنبيه التشغيل:** قد يستدعي التوليد والتقييم واجهات نماذج مدفوعة، ويثبت حزم npm، وينفذ الشيفرة المولدة، ويشغّل المتصفحات. استخدم بيئة تطوير معزولة لا تحتوي على أسرار لا تتعلق بالمهمة.

<a id="data-preparation"></a>

## تجهيز البيانات

تُستضاف **مجموعة البيانات وبيانات التجارب** على [unhappyNN/mobileforge في Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge). تستعيد أدوات [`server_migrate/`](../../server_migrate/) هذه الملفات إلى مساحة العمل المحلية: ينزّل [`hf_pull.py`](../../server_migrate/hf_pull.py) أرشيفات ZIP مع الحفاظ على مساراتها النسبية داخل مستودع Hugging Face. الوجهة الافتراضية هي جذر هذا المستودع؛ استخدم `--work-dir .` لتحديد المجلد الحالي صراحةً.

### تنزيل البيانات ومخرجات التجارب

يشير برنامج التنزيل إلى `unhappyNN/mobileforge`. نفّذ الأوامر من جذر المستودع:

<div dir="ltr">

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# فحص قائمة الأرشيفات البعيدة وخطة التنزيل.
python server_migrate/hf_pull.py --work-dir . --dry-run

# تنزيل أرشيفات البيانات والتجارب إلى مساحة العمل الحالية.
python server_migrate/hf_pull.py --work-dir .
```

</div>

يتطلب البرنامج `HF_TOKEN`، وينزّل ملفات `.zip` فقط، ويتجاوز الملفات المحلية ذات الحجم المطابق ما لم تحدد `--force`. وهو **لا يفك ضغط الأرشيفات**. افحص بنيتها الداخلية أولًا، ثم استخرج الملفات المطلوبة إلى مواضعها المناسبة، وتحقق من بنية المدخلات التالية قبل التوليد أو التقييم.

### بنية المهمة

تحتوي كل مهمة على **عدة لقطات شاشة للتطبيق نفسه**. ضع مجلدات التطبيقات داخل جذر إدخال مشترك:

<div dir="ltr">

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # توسيمات الصفحات والعلاقات المراجَعة بشريًا
    └── test_cases.reviewed.json # مواصفات تقييم التنقل المراجَعة بشريًا
```

</div>

يقرأ الإطار الصور الموجودة مباشرة داخل مجلد كل تطبيق (`.png` أو `.jpg` أو `.jpeg` أو `.gif` أو `.webp`). تستخدم العينة القياسية `metadata.reviewed.json` و`test_cases.reviewed.json` باعتبارهما المرجع المعتمد للتوسيم ومواصفات التنقل. عند تفعيل `pipeline.route_hints`، تُستخدم البيانات الوصفية المراجَعة لتلميحات المسارات. يدعم الإطار والمقيّم الرجوع إلى ملفات غير مراجَعة للمدخلات المخصصة، لكن تشغيل المعيار ينبغي أن يعتمد على الملفات المراجَعة أعلاه.

**الجمع والتنظيم.** يوفر المسار المحلي `dataset/mobbin-scraper/` جمع لقطات الشاشة واختيارها يدويًا. ينتج `annotator/` توسيمات منظمة وحالات اختبار، وتتيح [أداة المراجعة](../../annotator/check-tool/README.md) فحصها بشريًا.

> **توفر البيانات:** تُوزّع البيانات عبر [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge)، ولا تُضمّن في مستودع Git. يتجاهل Git المجلدات `dataset/` و`input/` و`experiments/` ولا يتتبعها، لذلك لا تتضمن النسخة المستنسخة حديثًا أدوات الجمع المحلية أو مجموعة اللقطات أو مخرجات التجارب. مساحة الجمع المحلية `dataset/` منفصلة عن بيانات المعيار المنشورة.

للقطاتك الخاصة، أنشئ التوسيمات وحالات الاختبار الأولية باستخدام:

<div dir="ltr">

```bash
python -m annotator --input ./input/mobileforge/example-app
```

</div>

استخدم [أداة المراجعة](../../annotator/check-tool/README.md) لفحص التوسيمات، واحفظ البيانات الوصفية وحالات الاختبار المراجَعة قبل استخدامها في المعيار. إذا لزم توليد الحالات مجددًا من البيانات الوصفية المراجَعة، فاكتب مسودة أولًا:

<div dir="ltr">

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

</div>

راجع المسودة واحفظ الحالات المعتمدة باسم `test_cases.reviewed.json` قبل التقييم. لا تستبدل الحالات المراجَعة بمسودة لم تُفحص.

راجع [دليل التوسيم](../../annotator/README.md) للاطلاع على المسار الكامل. استخدم فقط البيانات المصرح لك بالوصول إليها وإعادة توزيعها.

<a id="usage"></a>

## الاستخدام

### توليد مشروع

شغّل DesignForge على جميع اللقطات داخل مجلد تطبيق واحد:

<div dir="ltr">

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

</div>

يُكتب المشروع في `output/example-app/`. ينشئ الإطار الهيكل الأولي، ويزوّد الوكيل بالصور المتعددة وأدوات تحرير الشيفرة، ويفحص البناء ضمن حلقة إصلاح. تُحفظ السجلات والتتبعات ومقاييس التوليد في مجلد `.designforge/` الخاص بالتشغيل.

لتوليد مشاريع لجميع التطبيقات تحت جذر إدخال واحد:

<div dir="ltr">

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

</div>

عند عدم تحديد `--task`، تتبع أسماء التشغيل النمط `<app>__agentic__<model>`. استخدم `--suffix` للتمييز بين التشغيلات المتكررة.

### تشغيل مصفوفة تجارب

عدّل [`exp_config.yaml`](../../exp_config.yaml) لاختيار التطبيقات والنماذج والطرق وحدود الموارد، ثم شغّل:

<div dir="ltr">

```bash
python -m evaluator run --config exp_config.yaml
```

</div>

يستخدم الإعداد المرفق `evaluation.eval_strategy: manual`، ولذلك **يولّد الأمر المشاريع دون تقييمها تلقائيًا**. اختر `per_run` أو `batch` لتمكين التقييم التلقائي. تحقق من توفر النماذج وتوجيه واجهات API قبل تشغيل المصفوفة كاملةً.

<a id="evaluation"></a>

## التقييم

### تقييم مشروع مولّد

<div dir="ltr">

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

</div>

ينسق المقيّم فحوص البناء وجودة المشروع والكفاءة وتحليل مسارات التنفيذ، ويضيف اختبارات التنقل والتقييم البصري عند توفر حالات اختبار. تُحفظ النتائج في مجلد `eval/` الخاص بالتشغيل. عند غياب الحالات، تُتجاوز التقييمات المتعلقة بالتنقل؛ لذا لا يعني نجاح الأمر وحده تقييم المحاور الخمسة كلها.

### تشغيل التنقل والتقييم البصري كلٍّ على حدة

<div dir="ltr">

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

</div>

يلتقط تنفيذ التنقل صورًا لواجهات المشروع، ويعيد التقييم البصري استخدامها دون تكرار التفاعلات. التقرير البصري منفصل عن تقرير التنقل. أضف `--no-vlm-fallback` إلى `testgen run` لتعطيل الاستعانة بالنموذج في تحديد مواضع النقر.

**بروتوكول الورقة وواجهة CLI الافتراضية.** يقيّم `testgen eval` كل شاشة على حدة، ولا ينفذ تلقائيًا بروتوكول المقارنة القائم على القوائم بين المرشحين في الورقة. يتوفر تنفيذ الترتيب باستخدام مراجع الارتكاز في [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py). عند مقارنة الطرق، حدّد نموذج الحكم ومجموعة المرشحين وإعدادات الترتيب أو الخلط وسياسة الارتكاز بوضوح.

لإجراء مقارنات قابلة لإعادة الإنتاج، سجّل معرّف التزام الشيفرة وإصدارات المدخلات والتوسيمات ونماذج التوليد والحكم وميزانيات التكرار وتلميحات المسارات وإعدادات التقييم. الأوامر أمثلة لمسار العمل، وليست ضمانًا بأن الإعدادات الافتراضية تعيد إنتاج جميع جداول الورقة.

راجع [دليل المقيّم](../../evaluator/README.md) و[دليل TestGen](../../testgen/README.md) للخيارات الإضافية. استخدم `python -m evaluator --help` و`python -m testgen --help` لعرض واجهة الأوامر.

<a id="repository-structure"></a>

## بنية المستودع

| المسار | الدور |
| --- | --- |
| [`designforge/`](../../designforge/) | **إطار تشغيل الوكيل** لتوليد الشيفرة من صور متعددة، مع الأدوات والهيكل الأولي والوصول إلى النماذج. |
| `dataset/` | مساحة **جمع البيانات وتنظيمها**؛ محلية فقط وغير مضمنة في لقطة Git الحالية. |
| [`server_migrate/`](../../server_migrate/) | تنزيل أرشيفات البيانات والتجارب من Hugging Face إلى مساحة العمل المحلية. |
| [`annotator/`](../../annotator/) | توسيم اللقطات وعلاقات الصفحات وأدوات المراجعة البشرية. |
| [`testgen/`](../../testgen/) | توليد اختبارات التنقل وتنفيذها ونماذج الحكم البصري. |
| [`evaluator/`](../../evaluator/) | تنسيق التجارب والمقاييس والتجميع والتقارير. |
| [`human_eval/`](../../human_eval/) | أدوات التقييم البشري ومواده. |
| `input/`, `output/`, `experiments/` | مدخلات المهام المحلية والمشاريع المولّدة ومخرجات التجارب. |

<a id="citation"></a>

## الاستشهاد

إذا استخدمت MobileForge أو هذه الشيفرة، يُرجى الاستشهاد بالورقة. يشير السجل التالي إلى نسخة arXiv؛ وقد قُبل العمل في المؤتمر الرئيسي EMNLP 2026.

<div dir="ltr">

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

</div>

</div>
