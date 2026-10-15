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
  <strong>Français</strong> |
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
  <a href="https://arxiv.org/abs/2607.28645">Article</a> •
  <a href="https://github.com/anoa12159-hue/mobileforge_eval">Code</a> •
  <a href="https://huggingface.co/datasets/unhappyNN/mobileforge">Jeu de données et données expérimentales</a> •
  <a href="#overview">Présentation</a> •
  <a href="#set-up">Installation</a> •
  <a href="#usage">Utilisation</a> •
  <a href="#evaluation">Évaluation</a> •
  <a href="#citation">Citation</a>
</p>

---

Dépôt officiel du code de **[Looks Right, Works Right: A Project-Level Benchmark for Multi-Screen Mobile App Generation](https://arxiv.org/abs/2607.28645)**, accepté à la **conférence principale EMNLP 2026**.

Le code est hébergé sur [anoa12159-hue/mobileforge_eval](https://github.com/anoa12159-hue/mobileforge_eval), comme indiqué dans l’article. Le jeu de données et les données expérimentales sont disponibles sur [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge).

**MobileForge** est le benchmark ; **DesignForge** (`designforge/`) est son environnement d’exécution d’agents pour la génération UI-to-code à partir de plusieurs images. L’espace local `dataset/` sert à la collecte et à la préparation des données, et non à la génération de code.

<a id="news"></a>

## Actualités

- **EMNLP 2026 :** *Looks Right, Works Right* a été accepté à la conférence principale.
- Le code publié couvre la génération, l’annotation, les tests de navigation et l’évaluation.

<a id="overview"></a>

## Présentation

Transformer une capture d’écran en page ne suffit pas à créer une application mobile. **MobileForge évalue la génération UI-to-code multi-écran à l’échelle d’un projet** : à partir de plusieurs captures d’une même application, le modèle doit produire un projet unique, compilable, avec des écrans cohérents, des composants partagés et une navigation fonctionnelle entre les écrans.

Le benchmark associe reconstruction visuelle, comportement exécutable et qualité globale du projet :

| Axe d’évaluation | Ce qu’il mesure |
| --- | --- |
| **Construction** | Réussite des contrôles de compilation et de construction pour la production. |
| **Navigation** | Capacité des interactions à atteindre les écrans attendus. |
| **Fidélité visuelle** | Proximité entre les écrans rendus et les conceptions de référence. |
| **Maintenabilité** | Indicateurs de qualité du code et de structure du projet. |
| **Efficacité** | Ressources consommées pendant la génération, notamment tokens et appels aux modèles. |

L’article introduit des **tests de navigation à états isolés** pour éviter les échecs en cascade et une **évaluation visuelle par listes avec références d’ancrage** pour améliorer la fiabilité du jugement visuel.

```text
Collecte et préparation        Captures multi-écrans
         dataset/                          |
                                  annotator/ → métadonnées et cas de test
                                           |
                                  designforge/ → projet généré
                                           |
                                  testgen/ → navigation et captures
                                           |
                                  evaluator/ → métriques et rapports
```

La cible de génération par défaut est un projet Web **React + TypeScript + Tailwind CSS** reproduisant les écrans d’applications mobiles. Il ne s’agit pas d’une chaîne de construction Android/iOS native.

<a id="set-up"></a>

## Installation

Utilisez **Python 3.10+** (3.11 dans les notes d’installation), **Node.js 20+** et **npm**. Les commandes supposent un shell compatible Bash ; sous Windows, utilisez WSL ou adaptez l’activation de l’environnement à votre shell.

```bash
git clone https://github.com/anoa12159-hue/mobileforge_eval.git
cd mobileforge_eval

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Le fichier racine `requirements.txt` centralise l’installation des dépendances Python. L’évaluation dans le navigateur nécessite Chromium ; la génération nécessite aussi un accès réseau pour installer les dépendances npm du projet produit.

### Configuration des modèles

Avant l’exécution, modifiez les fichiers existants [`experiment.yaml`](../../experiment.yaml) et [`models.yaml`](../../models.yaml) :

| Configuration | Rôle |
| --- | --- |
| `experiment.yaml` → `llm` | Fournisseur et modèles de génération. |
| `experiment.yaml` → `pipeline` | Framework, budget d’itérations et indications de routage facultatives. |
| `experiment.yaml` → `annotator` | Modèle d’annotation des captures et parallélisme. |
| `experiment.yaml` → `testgen` | Modèle de secours pour la navigation et juge visuel. |
| `models.yaml` | Capacités des modèles et limites de tokens. |
| `exp_config.yaml` | Matrice application × modèle × méthode et politique d’évaluation. |

Avec `llm.provider: litellm`, fournissez les identifiants des fournisseurs effectivement utilisés, par exemple :

```bash
export ANTHROPIC_API_KEY="<your-api-key>"
```

Choisissez un modèle acceptant les images et l’utilisation d’outils pour la génération. Configurez séparément les identifiants des modèles d’annotation et d’évaluation si nécessaire. [`env.sh.example`](../../env.sh.example) décrit d’autres variables d’environnement ; ses réglages Conda, nvm et relais sont des exemples à adapter, pas des prérequis à recopier tels quels. Ne commitez pas d’identifiants secrets.

Vérifiez d’abord la configuration locale, puis la connexion aux modèles :

```bash
python -m designforge preflight --skip-api
python -m designforge preflight
```

> **Précaution d’exécution :** la génération et l’évaluation peuvent appeler des API payantes, installer des paquets npm, exécuter du code généré et lancer des navigateurs. Utilisez un environnement de développement isolé, sans secrets sans rapport avec ces tâches.

<a id="data-preparation"></a>

## Préparation des données

Le **jeu de données et les données expérimentales** sont hébergés sur [unhappyNN/mobileforge sur Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge). Les outils de [`server_migrate/`](../../server_migrate/) les restaurent dans l’espace local : [`hf_pull.py`](../../server_migrate/hf_pull.py) télécharge les archives ZIP en conservant leurs chemins relatifs au dépôt Hugging Face. La destination par défaut est la racine de ce dépôt ; `--work-dir .` désigne explicitement le répertoire courant.

### Téléchargement des données et des résultats expérimentaux

Le script cible `unhappyNN/mobileforge`. Exécutez les commandes depuis la racine du dépôt :

```bash
python -m pip install huggingface_hub
export HF_TOKEN="<your-hugging-face-token>"

# Inspecter la liste des archives distantes et les téléchargements prévus.
python server_migrate/hf_pull.py --work-dir . --dry-run

# Télécharger les archives de données et d’expériences dans l’espace courant.
python server_migrate/hf_pull.py --work-dir .
```

Le script exige `HF_TOKEN`, ne télécharge que des fichiers `.zip` et ignore les fichiers locaux de même taille, sauf avec `--force`. Il **n’extrait pas les archives**. Inspectez leur arborescence avant de les extraire aux emplacements voulus, puis vérifiez la structure des entrées avant toute génération ou évaluation.

### Structure d’une tâche

Chaque tâche contient **plusieurs captures d’une même application**. Placez les répertoires des applications sous une racine d’entrée commune :

```text
input/mobileforge/
└── example-app/
    ├── home.png
    ├── details.png
    ├── settings.png
    ├── metadata.reviewed.json   # Annotations des pages et relations vérifiées par une personne
    └── test_cases.reviewed.json # Spécifications de navigation vérifiées par une personne
```

L’environnement lit les images directement dans chaque répertoire d’application (`.png`, `.jpg`, `.jpeg`, `.gif` ou `.webp`). Les échantillons standard utilisent `metadata.reviewed.json` et `test_cases.reviewed.json` comme annotations et spécifications de navigation de référence. Lorsque `pipeline.route_hints` est activé, les métadonnées vérifiées fournissent les indications de routage. Pour des entrées personnalisées, un repli vers les fichiers non vérifiés est possible, mais les expériences du benchmark doivent utiliser les fichiers vérifiés ci-dessus.

**Collecte et préparation.** Le workflow local `dataset/mobbin-scraper/` assure la collecte des captures et leur sélection manuelle. `annotator/` produit les annotations structurées et les cas de test ; son [outil de révision](../../annotator/check-tool/README.md) permet leur vérification humaine.

> **Disponibilité des données :** les données sont distribuées via [Hugging Face](https://huggingface.co/datasets/unhappyNN/mobileforge), et non avec le dépôt Git. `dataset/`, `input/` et `experiments/` sont ignorés et non suivis par Git. Un nouveau clone ne contient donc ni les outils locaux de collecte, ni les captures, ni les résultats expérimentaux. L’espace local de collecte `dataset/` est distinct des données publiées du benchmark.

Pour vos propres captures, générez les annotations et cas de test initiaux ainsi :

```bash
python -m annotator --input ./input/mobileforge/example-app
```

Vérifiez les annotations avec l’[outil de révision](../../annotator/check-tool/README.md), puis enregistrez les métadonnées et cas de test vérifiés avant de les utiliser dans le benchmark. Pour régénérer des cas à partir des métadonnées vérifiées, créez d’abord un brouillon :

```bash
python -m testgen generate \
  ./input/mobileforge/example-app/metadata.reviewed.json \
  --output ./input/mobileforge/example-app/test_cases.json
```

Révisez ce brouillon et enregistrez les cas approuvés dans `test_cases.reviewed.json` avant l’évaluation ; ne remplacez pas des cas vérifiés par un brouillon non contrôlé.

Consultez le [guide d’annotation](../../annotator/README.md) pour le workflow complet. N’utilisez que des données auxquelles vous êtes autorisé à accéder et que vous pouvez redistribuer.

<a id="usage"></a>

## Utilisation

### Génération d’un projet

Exécutez DesignForge sur toutes les captures d’un répertoire d’application :

```bash
python -m designforge \
  --input ./input/mobileforge/example-app \
  --output ./output \
  --task example-app
```

Le projet est écrit dans `output/example-app/`. L’environnement crée son ossature, fournit les images et les outils d’édition à l’agent, puis vérifie la construction avec une boucle de correction. Journaux, traces et métriques de génération sont enregistrés dans le répertoire `.designforge/` de l’exécution.

Pour générer un projet pour chaque application sous une racine d’entrée :

```bash
python -m designforge --input ./input/mobileforge --output ./output --batch
```

Sans `--task`, les noms d’exécution suivent `<app>__agentic__<model>` ; utilisez `--suffix` pour distinguer les répétitions.

### Exécution d’une matrice d’expériences

Modifiez [`exp_config.yaml`](../../exp_config.yaml) pour choisir les applications, modèles, méthodes et limites de ressources, puis lancez :

```bash
python -m evaluator run --config exp_config.yaml
```

La configuration fournie utilise `evaluation.eval_strategy: manual` : cette commande **génère les projets sans les évaluer automatiquement**. Choisissez `per_run` ou `batch` pour activer l’évaluation automatique. Vérifiez la disponibilité des modèles et le routage des API avant de lancer toute la matrice.

<a id="evaluation"></a>

## Évaluation

### Évaluation d’un projet généré

```bash
python -m evaluator eval \
  --dir ./output/example-app \
  --input ./input/mobileforge/example-app
```

L’évaluateur coordonne la construction, la qualité, l’efficacité et l’analyse des trajectoires, ainsi que les tests de navigation et la notation visuelle lorsque des cas de test sont disponibles. Les résultats sont écrits dans `eval/` pour chaque exécution. Sans cas de test, les évaluations liées à la navigation sont ignorées ; la réussite de la commande ne signifie pas que les cinq axes ont été évalués.

### Exécution séparée de la navigation et de la notation visuelle

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

La navigation capture les écrans de l’implémentation ; la notation visuelle les réutilise sans rejouer les interactions. Les rapports visuel et de navigation sont distincts. Ajoutez `--no-vlm-fallback` à `testgen run` pour désactiver la localisation des cibles de clic assistée par modèle.

**Protocole de l’article et CLI par défaut.** `testgen eval` note chaque écran ; il n’exécute pas automatiquement le protocole comparatif par listes de l’article. Le classement avec références d’ancrage est implémenté dans [`testgen/visual_judge_listwise.py`](../../testgen/visual_judge_listwise.py). Lors de comparaisons, précisez le juge, les candidats, l’ordre ou le mélange des candidats et la politique d’ancrage.

Pour des comparaisons reproductibles, consignez le commit du code, la version des entrées et annotations, les modèles de génération et d’évaluation, les budgets d’itérations, les indications de routage et la configuration d’évaluation. Les commandes illustrent le workflow ; elles ne garantissent pas que les paramètres par défaut reproduisent tous les tableaux de l’article.

Consultez le [guide de l’évaluateur](../../evaluator/README.md) et le [guide TestGen](../../testgen/README.md) pour d’autres options. Utilisez `python -m evaluator --help` et `python -m testgen --help` pour consulter la CLI.

<a id="repository-structure"></a>

## Structure du dépôt

| Chemin | Rôle |
| --- | --- |
| [`designforge/`](../../designforge/) | **Environnement d’exécution d’agents** UI-to-code multi-image, outils, ossature des projets et accès aux modèles. |
| `dataset/` | Espace de **collecte et préparation des données**, local uniquement et absent de l’instantané Git actuel. |
| [`server_migrate/`](../../server_migrate/) | Téléchargement local des archives de données et d’expériences depuis Hugging Face. |
| [`annotator/`](../../annotator/) | Annotation des captures, relations entre pages et outils de révision humaine. |
| [`testgen/`](../../testgen/) | Génération et exécution des tests de navigation, juges visuels. |
| [`evaluator/`](../../evaluator/) | Orchestration des expériences, métriques, agrégation et rapports. |
| [`human_eval/`](../../human_eval/) | Outils et supports d’évaluation humaine. |
| `input/`, `output/`, `experiments/` | Entrées locales, projets générés et résultats expérimentaux. |

<a id="citation"></a>

## Citation

Si vous utilisez MobileForge ou ce code, veuillez citer l’article. L’entrée suivante correspond à la version arXiv ; les travaux ont été acceptés à la conférence principale EMNLP 2026.

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
