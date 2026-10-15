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
  <strong>Deutsch</strong> |
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
  <a href="https://arxiv.org/abs/2607.28645">Publikation</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">Code</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">Datensatz und Experimentdaten</a> •
  <a href="#overview">Überblick</a> •
  <a href="#set-up">Einrichtung</a> •
  <a href="#usage">Verwendung</a> •
  <a href="#evaluation">Evaluation</a> •
  <a href="#citation">Zitieren</a>
</p>

---

Offizielles Code-Repository zu **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)**, angenommen zur **EMNLP 2026 Main Conference**.

Der Code liegt unter [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval), entsprechend dem Link in der Publikation. Datensatz und Experimentdaten sind auf [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) verfügbar.

**MobileForge** ist der Benchmark; **DesignForge** (`designforge/`) ist seine Agenten-Laufzeitumgebung für die UI-to-code-Generierung aus mehreren Bildern. Der lokale Arbeitsbereich `dataset/` dient der Datensammlung und -aufbereitung, nicht der Codegenerierung.

<a id="news"></a>

## Neuigkeiten

- **EMNLP 2026:** *Looks Right, Works Right* wurde zur Hauptkonferenz angenommen.
- Der veröffentlichte Code umfasst Generierung, Annotation, Navigationstests und Evaluation.

<a id="overview"></a>

## Überblick

Ein einzelner Screenshot als Webseite reicht nicht aus, um eine mobile App zu erstellen. **MobileForge bewertet die UI-to-code-Generierung mehrerer Ansichten auf Projektebene**: Aus mehreren Screenshots derselben App soll ein Modell ein einziges baubares Projekt mit konsistenten Ansichten, gemeinsam genutzten Komponenten und funktionierender Navigation zwischen den Ansichten erzeugen.

Der Benchmark verbindet visuelle Rekonstruktion, ausführbares Verhalten und projektweite Qualität:

| Bewertungsdimension | Messgegenstand |
| --- | --- |
| **Build** | Bestehen der Kompilierungs- und Produktionsbuild-Prüfungen. |
| **Navigation** | Erreichen der vorgesehenen Ansichten durch Interaktionen. |
| **Visuelle Wiedergabetreue** | Ähnlichkeit gerenderter Ansichten mit den Referenzdesigns. |
| **Wartbarkeit** | Projektweite Codequalitäts- und Strukturindikatoren. |
| **Effizienz** | Ressourcenverbrauch der Generierung, einschließlich Tokens und Modellaufrufen. |

Die Publikation stellt **zustandsisolierte Navigationstests** zur Vermeidung von Fehlerkaskaden und eine **ankerreferenzierte listenweise visuelle Bewertung** für zuverlässigere visuelle Urteile vor.

```text
Datensammlung und -aufbereitung        Screenshots mehrerer Ansichten
         dataset/                          |
                                  annotator/ → Metadaten und Testfälle
                                           |
                                  designforge/ → generiertes Projekt
                                           |
                                  testgen/ → Navigation und Screenshots
                                           |
                                  evaluator/ → Metriken und Berichte
```

Standardmäßig entsteht ein **React + TypeScript + Tailwind CSS**-Webprojekt, das Ansichten mobiler Apps rekonstruiert. Dies ist keine Build-Pipeline für native Android-/iOS-Apps.

<a id="set-up"></a>

## Einrichtung

Verwenden Sie **Python 3.10+** (3.11 in den Einrichtungshinweisen), **Node.js 20+** und **npm**. Die Befehle setzen eine Bash-kompatible Shell voraus. Nutzen Sie unter Windows WSL oder passen Sie die Umgebungsaktivierung an Ihre Shell an.

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Die `requirements.txt` im Stammverzeichnis ist der zentrale Einstiegspunkt für die Installation der Python-Abhängigkeiten. Die Browser-Evaluation benötigt Chromium; für die npm-Abhängigkeiten generierter Projekte wird zudem Netzwerkzugriff benötigt.

### Modelle konfigurieren

Bearbeiten Sie vor der Ausführung die vorhandenen Dateien [`experiment.yaml`](../../experiment.yaml) und [`models.yaml`](../../models.yaml):

| Konfiguration | Zweck |
| --- | --- |
| `experiment.yaml` → `llm` | Anbieter und Generierungsmodelle. |
| `experiment.yaml` → `pipeline` | Framework, Iterationsbudget und optionale Routenhinweise. |
| `experiment.yaml` → `annotator` | Screenshot-Annotationsmodell und Parallelität. |
| `experiment.yaml` → `testgen` | Fallback-Modell für Navigation und visuelles Bewertungsmodell. |
| `models.yaml` | Modellfähigkeiten und Token-Limits. |
| `exp_config.yaml` | App × Modell × Methode als Experimentmatrix sowie Evaluationsstrategie. |

Bei `llm.provider: litellm` geben Sie Zugangsdaten für die tatsächlich verwendeten Anbieter an, beispielsweise:

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

Wählen Sie für die Generierung ein Modell mit Bild- und Werkzeugunterstützung. Konfigurieren Sie Zugangsdaten für Annotation und Evaluation bei Bedarf separat. Weitere Umgebungsvariablen beschreibt [`env.sh.example`](../../env.sh.example). Die Einstellungen für Conda, nvm und Relay-Dienste sind anzupassende Beispiele, keine unverändert zu übernehmenden Voraussetzungen. Committen Sie keine Zugangsdaten.

Prüfen Sie zuerst die lokale Konfiguration und danach die Modellverbindung:

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **Ausführungshinweis:** Generierung und Evaluation können kostenpflichtige Modell-APIs aufrufen, npm-Pakete installieren, generierten Code ausführen und Browser starten. Verwenden Sie eine isolierte Entwicklungsumgebung ohne aufgabenfremde Geheimnisse.

<a id="data-preparation"></a>

## Datenvorbereitung

**Datensatz und Experimentdaten** liegen auf [unhappyNN/mobileforge bei Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge). Die Werkzeuge in [`server_migrate/`](../../server_migrate/) stellen sie lokal wieder her: [`hf_pull.py`](../../server_migrate/hf_pull.py) lädt ZIP-Archive herunter und erhält deren relative Pfade im Hugging-Face-Repository. Standardziel ist das Stammverzeichnis dieses Repositorys; mit `--work-dir .` wählen Sie ausdrücklich das aktuelle Verzeichnis.

### Daten und Experimentartefakte herunterladen

Das Downloadskript verweist auf `unhappyNN/mobileforge`. Führen Sie Folgendes im Stammverzeichnis aus:

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# Remote-Archivliste und geplante Downloads prüfen.
python server_migrate/hf_pull.py --work-dir . --dry-run

# Daten- und Experimentarchive in den aktuellen Arbeitsbereich laden.
python server_migrate/hf_pull.py --work-dir .
```

Das Skript benötigt `HF_TOKEN`, lädt nur `.zip`-Dateien und überspringt lokale Dateien gleicher Größe, sofern `--force` nicht gesetzt ist. Es **entpackt keine Archive**. Prüfen Sie zuerst die interne Verzeichnisstruktur, entpacken Sie die benötigten Inhalte an die vorgesehenen Stellen und kontrollieren Sie vor Generierung oder Evaluation die folgende Eingabestruktur.

### Aufgabenstruktur

Jede Aufgabe enthält **mehrere Screenshots derselben App**. Legen Sie die App-Verzeichnisse unter einem gemeinsamen Eingabeverzeichnis ab:

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # Menschlich geprüfte Seiten- und Beziehungsannotation
    └── test_cases.reviewed.json # Menschlich geprüfte Navigationsspezifikationen
```

Die Laufzeitumgebung liest Bilder direkt aus jedem App-Verzeichnis (`.png`, `.jpg`, `.jpeg`, `.gif` oder `.webp`). Standardbeispiele verwenden `metadata.reviewed.json` und `test_cases.reviewed.json` als maßgebliche Annotationen und Navigationsspezifikationen. Mit `pipeline.route_hints` werden geprüfte Metadaten für Routenhinweise verwendet. Für eigene Eingaben unterstützen Laufzeitumgebung und Evaluator einen Rückgriff auf ungeprüfte Dateien; Benchmark-Läufe sollten jedoch die genannten geprüften Dateien nutzen.

**Sammlung und Aufbereitung.** Der lokale Workflow `dataset/mobbin-scraper/` unterstützt Screenshot-Sammlung und manuelle Auswahl. `annotator/` erstellt strukturierte Annotationen und Testfälle; das [Review-Werkzeug](../../annotator/check-tool/README.md) ermöglicht die menschliche Prüfung.

> **Datenverfügbarkeit:** Datensatz und Experimentdaten werden über [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge) verteilt, nicht mit dem Git-Repository. `dataset/`, `input/` und `experiments/` werden von Git ignoriert und nicht versioniert. Ein neuer Klon enthält daher weder lokale Sammelwerkzeuge noch Screenshots oder Experimentartefakte. Der lokale Sammelbereich `dataset/` ist von den veröffentlichten Benchmark-Daten zu unterscheiden.

Erzeugen Sie für eigene Screenshots zunächst Annotationen und Testfälle:

```bash
python -m annotator --input ./input/mobileforge/example-app
```

Prüfen Sie die Annotationen mit dem [Review-Werkzeug](../../annotator/check-tool/README.md) und speichern Sie geprüfte Metadaten und Testfälle vor dem Benchmark-Einsatz. Wenn Testfälle aus geprüften Metadaten neu erzeugt werden sollen, schreiben Sie zunächst einen Entwurf:

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

Prüfen Sie diesen Entwurf und speichern Sie freigegebene Fälle vor der Evaluation als `test_cases.reviewed.json`. Überschreiben Sie geprüfte Fälle nicht mit einem ungeprüften Entwurf.

Den vollständigen Ablauf beschreibt die [Annotationsanleitung](../../annotator/README.md). Verwenden Sie nur Daten, auf die Sie zugreifen und die Sie weiterverbreiten dürfen.

<a id="usage"></a>

## Verwendung

### Ein Projekt generieren

Führen Sie DesignForge für alle Screenshots in einem App-Verzeichnis aus:

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

Das Projekt wird nach `output/example-app/` geschrieben. Die Laufzeitumgebung erstellt das Projektgerüst, stellt dem Agenten mehrere Bilder und Codebearbeitungswerkzeuge bereit und prüft den Build in einer Reparaturschleife. Logs, Traces und Generierungsmetriken liegen im jeweiligen `.designforge/`-Verzeichnis.

So generieren Sie Projekte für alle Apps eines Eingabeverzeichnisses:

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

Ohne `--task` folgen die Laufnamen dem Muster `<app>__agentic__<model>`. Mit `--suffix` unterscheiden Sie wiederholte Läufe.

### Eine Experimentmatrix ausführen

Wählen Sie in [`exp_config.yaml`](../../exp_config.yaml) Apps, Modelle, Methoden und Ressourcenlimits und starten Sie:

```bash
python -m evaluator run --config exp_config.yaml
```

Die enthaltene Konfiguration verwendet `evaluation.eval_strategy: manual`. Der Befehl **generiert Projekte, ohne sie automatisch zu bewerten**. Wählen Sie `per_run` oder `batch` für automatische Evaluation. Prüfen Sie vor einer vollständigen Matrix die Modellverfügbarkeit und das API-Routing.

<a id="evaluation"></a>

## Evaluation

### Ein generiertes Projekt bewerten

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

Der Evaluator koordiniert Build, Projektqualität, Effizienz und Trajektorienanalyse sowie Navigationstests und visuelle Bewertung, sofern Testfälle vorliegen. Ergebnisse stehen im `eval/`-Verzeichnis des Laufs. Ohne Testfälle werden navigationsbezogene Bewertungen übersprungen; ein erfolgreicher Befehl allein belegt daher nicht die Bewertung aller fünf Dimensionen.

### Navigation und visuelle Bewertung separat ausführen

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

Die Navigation nimmt Screenshots der Implementierung auf; die visuelle Bewertung verwendet sie erneut, ohne Interaktionen zu wiederholen. Visueller Bericht und Navigationsbericht sind getrennt. Mit `--no-vlm-fallback` bei `testgen run` deaktivieren Sie die modellgestützte Lokalisierung von Klickzielen.

**Publikationsprotokoll gegenüber Standard-CLI.** `testgen eval` bewertet einzelne Ansichten und führt das listenweise, kandidatenübergreifende Protokoll der Publikation nicht automatisch aus. Die ankerreferenzierte Rangbildung ist in [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py) implementiert. Halten Sie bei Vergleichen Bewertungsmodell, Kandidatenmenge, Reihenfolge beziehungsweise Durchmischung und Ankerstrategie ausdrücklich fest.

Dokumentieren Sie für reproduzierbare Vergleiche Code-Commit, Eingabe- und Annotationsversion, Generierungs- und Bewertungsmodelle, Iterationsbudgets, Routenhinweise und Evaluationskonfiguration. Die Befehle zeigen den Ablauf, garantieren aber nicht, dass die Standardeinstellungen sämtliche Tabellen der Publikation reproduzieren.

Weitere Optionen bieten die [Evaluator-Anleitung](../../evaluator/README.md) und die [TestGen-Anleitung](../../testgen/README.md). Die CLI erläutern `python -m evaluator --help` und `python -m testgen --help`.

<a id="repository-structure"></a>

## Repository-Struktur

| Pfad | Aufgabe |
| --- | --- |
| [`designforge/`](../../designforge/) | **Agenten-Laufzeitumgebung** für UI-to-code aus mehreren Bildern, Werkzeuge, Projektgerüste und Modellzugriff. |
| `dataset/` | Lokaler Bereich zur **Datensammlung und -aufbereitung**, nicht im aktuellen Git-Snapshot enthalten. |
| [`server_migrate/`](../../server_migrate/) | Download von Daten- und Experimentarchiven aus Hugging Face in den lokalen Arbeitsbereich. |
| [`annotator/`](../../annotator/) | Screenshot-Annotation, Seitenbeziehungen und menschliche Prüfung. |
| [`testgen/`](../../testgen/) | Erstellung und Ausführung von Navigationstests sowie visuelle Bewertungsmodelle. |
| [`evaluator/`](../../evaluator/) | Experimentsteuerung, Metriken, Aggregation und Berichte. |
| [`human_eval/`](../../human_eval/) | Werkzeuge und Materialien zur menschlichen Evaluation. |
| `input/`, `output/`, `experiments/` | Lokale Eingaben, generierte Projekte und Experimentartefakte. |

<a id="citation"></a>

## Zitieren

Wenn Sie MobileForge oder diesen Code verwenden, zitieren Sie bitte die Publikation. Der folgende Eintrag verweist auf die arXiv-Version; die Arbeit wurde zur EMNLP 2026 Main Conference angenommen.

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
