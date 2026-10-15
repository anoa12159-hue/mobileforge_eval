"""
Global configuration — loaded from experiment.yaml + models.yaml.

Tunable parameters live in experiment.yaml at the project root.
Model registry lives separately in models.yaml (all available models);
experiment.yaml ``models`` section can override individual entries.
This module reads both once on import and exposes flat constants + ModelInfo registry.
Falls back to sensible defaults if either YAML file doesn't exist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


def _load_experiment() -> dict:
    config_path = PROJECT_ROOT / "experiment.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_cfg = _load_experiment()
_llm = _cfg.get("llm", {})
_pipeline = _cfg.get("pipeline", {})
_paths = _cfg.get("paths", {})
_tools = _cfg.get("tools", {})
_annotator = _cfg.get("annotator", {})
_testgen = _cfg.get("testgen", {})

# ------------------------------------------------------------------
# LLM settings
# ------------------------------------------------------------------

LLM_PROVIDER: str = _llm.get("provider", "litellm")
LLM_ENDPOINT: str = _llm.get("endpoint", "")

VISION_MODEL: str = _llm.get("vision_model", "anthropic/claude-sonnet-4-20250514")
TEXT_MODEL: str = _llm.get("text_model", "anthropic/claude-sonnet-4-20250514")
AGENT_MODEL: str = _llm.get("agent_model", "anthropic/claude-sonnet-4-20250514")

MAX_RETRIES: int = _llm.get("max_retries", 2)

GEMINI_READ_TIMEOUT: int = _llm.get("gemini_read_timeout", 420)
GEMINI_THINKING_BUDGET: int = _llm.get("gemini_thinking_budget", 1024)

_DEFAULT_MAX_TOKENS: int = 8192

# ------------------------------------------------------------------
# Pipeline settings
# ------------------------------------------------------------------

TARGET_FRAMEWORK: str = _pipeline.get("target_framework", "react")
CSS_STRATEGY: str = _pipeline.get("css_strategy", "tailwind")
MAX_PARALLEL_PAGES: int = _pipeline.get("max_parallel_pages", 5)
MAX_AGENT_ITERATIONS: int = _pipeline.get("max_agent_iterations", 25)

_raw_agent_max: dict = _pipeline.get("agent_max_iterations") or {}
AGENT_MAX_ITERATIONS_OVERRIDES: dict[str, int] = {
    str(k).strip().lower(): int(v)
    for k, v in _raw_agent_max.items()
    if v is not None
}

# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------

INPUT_DIR: Path = PROJECT_ROOT / _paths.get("input_dir", "input")
OUTPUT_DIR: Path = PROJECT_ROOT / _paths.get("output_dir", "output")

# ------------------------------------------------------------------
# Tool / agent I/O limits (Page, Merge, Build)
# ------------------------------------------------------------------

_DEFAULT_IGNORE_DIRS: list[str] = [
    ".designforge",
    "node_modules",
    "dist",
    ".git",
    ".vite",
]

LIST_FILES_IGNORE_DIR_NAMES: frozenset[str] = frozenset(
    _tools.get("list_files_ignore_dir_names", _DEFAULT_IGNORE_DIRS)
)

# Max characters returned to the LLM per read_file tool call (0 = no limit).
READ_FILE_MAX_CHARS: int = int(_tools.get("read_file_max_chars", 120_000))

# Max characters of run_command stdout/stderr combined returned to the LLM.
RUN_COMMAND_MAX_OUTPUT_CHARS: int = int(_tools.get("run_command_max_output_chars", 16_000))


# ------------------------------------------------------------------
# Annotator settings
# ------------------------------------------------------------------

ANNOTATOR_MODEL: str = _annotator.get("model", "gemini-2.5-flash")
ANNOTATOR_WORKERS: int = int(_annotator.get("workers", 8))

# ------------------------------------------------------------------
# TestGen settings
# ------------------------------------------------------------------

TESTGEN_FALLBACK_MODEL: str = _testgen.get("fallback_model", "gemini-2.5-flash")
TESTGEN_EVAL_MODEL: str = _testgen.get("eval_model", "gemini-2.5-pro")
TESTGEN_EVAL_WORKERS: int = int(_testgen.get("eval_workers", 8))

# ------------------------------------------------------------------
# Model registry
# ------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Capabilities and pricing for a single model."""

    name: str
    max_tokens: int = 8192
    context_window: int = 128000
    supports_images: bool = False
    supports_prompt_cache: bool = False
    input_price: float = 0.0       # USD per 1K tokens
    output_price: float = 0.0
    cache_writes_price: float = 0.0
    cache_reads_price: float = 0.0


_MODEL_REGISTRY: dict[str, ModelInfo] = {}

MODELS_YAML_PATH: Path = PROJECT_ROOT / "models.yaml"


def _load_models_yaml() -> dict:
    """Load the standalone models.yaml registry file."""
    if MODELS_YAML_PATH.exists():
        with open(MODELS_YAML_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _parse_model_entry(name: str, info: dict) -> ModelInfo:
    return ModelInfo(
        name=name,
        max_tokens=info.get("max_tokens", 8192),
        context_window=info.get("context_window", 128000),
        supports_images=info.get("supports_images", False),
        supports_prompt_cache=info.get("supports_prompt_cache", False),
        input_price=info.get("input_price", 0.0),
        output_price=info.get("output_price", 0.0),
        cache_writes_price=info.get("cache_writes_price", 0.0),
        cache_reads_price=info.get("cache_reads_price", 0.0),
    )


def _build_model_registry() -> None:
    # Layer 1: models.yaml (base registry with all available models)
    base_models = _load_models_yaml()
    for name, info in base_models.items():
        if not isinstance(info, dict):
            continue
        _MODEL_REGISTRY[name] = _parse_model_entry(name, info)

    # Layer 2: experiment.yaml models section (overrides / additions)
    override_models = _cfg.get("models", {})
    if override_models:
        for name, info in override_models.items():
            if not isinstance(info, dict):
                continue
            _MODEL_REGISTRY[name] = _parse_model_entry(name, info)

    if _MODEL_REGISTRY:
        logger.debug(
            "Model registry: %d models (%d from models.yaml, %d overrides from experiment.yaml)",
            len(_MODEL_REGISTRY), len(base_models), len(override_models) if override_models else 0,
        )


_build_model_registry()


def get_model_info(model_name: str) -> ModelInfo | None:
    """Look up model capabilities by name. Returns None if not registered."""
    if model_name in _MODEL_REGISTRY:
        return _MODEL_REGISTRY[model_name]
    for key, info in _MODEL_REGISTRY.items():
        if key in model_name or model_name in key:
            return info
    return None


def get_max_tokens(model_name: str) -> int:
    """Return max_tokens for a model from the registry. Falls back to default."""
    info = get_model_info(model_name)
    if info is not None:
        return info.max_tokens
    return _DEFAULT_MAX_TOKENS



def get_all_models() -> dict[str, ModelInfo]:
    """Return the full model registry."""
    return dict(_MODEL_REGISTRY)


def validate_model_config() -> list[str]:
    """Check that configured models are in the registry and vision_model supports images.
    Returns a list of warning messages (empty = all good).
    """
    warnings: list[str] = []

    for role, name in [("vision_model", VISION_MODEL), ("text_model", TEXT_MODEL), ("agent_model", AGENT_MODEL)]:
        info = get_model_info(name)
        if info is None:
            warnings.append(f"{role}='{name}' 未在 models 注册表中找到，无法校验能力")

    vision_info = get_model_info(VISION_MODEL)
    if vision_info and not vision_info.supports_images:
        warnings.append(
            f"vision_model='{VISION_MODEL}' 的 supports_images=false，"
            "Map 阶段 VLM 调用可能失败，请换用支持图片的模型"
        )

    return warnings
