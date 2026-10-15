"""
Preflight checks — validate configuration and connectivity before heavy runs.

Usage:
    python -m designforge preflight
    python -m designforge preflight --model gemini-3-flash
    python -m designforge preflight --skip-api
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _ok(msg: str) -> None:
    console.print(f"  [green]✓[/green] {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [yellow]⚠[/yellow] {msg}")


def _fail(msg: str) -> None:
    console.print(f"  [red]✗[/red] {msg}")


# ------------------------------------------------------------------
# Individual checks
# ------------------------------------------------------------------


def check_yaml_loadable() -> bool:
    """experiment.yaml 能否正常加载"""
    console.rule("[bold]1. experiment.yaml 文件检查")
    from designforge.config import PROJECT_ROOT
    path = PROJECT_ROOT / "experiment.yaml"
    if not path.exists():
        _fail(f"文件不存在: {path}")
        return False
    _ok(f"文件存在: {path}")

    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        _fail(f"YAML 解析失败: {exc}")
        return False
    _ok("YAML 解析成功")

    required_sections = ["llm"]
    for sec in required_sections:
        if sec not in data:
            _fail(f"缺少必要段: '{sec}'")
            return False
    _ok(f"必要段均存在: {required_sections}")

    from designforge.config import MODELS_YAML_PATH
    if MODELS_YAML_PATH.exists():
        _ok(f"模型注册表: {MODELS_YAML_PATH}")
    else:
        _warn(f"models.yaml 不存在: {MODELS_YAML_PATH}")

    if "models" in data:
        _ok(f"experiment.yaml 包含 models 覆盖段 ({len(data['models'])} 条)")

    return True


def check_model_registry() -> bool:
    """模型注册表是否完整、vision_model 是否支持图片"""
    console.rule("[bold]2. 模型注册表检查")
    from designforge import config

    all_models = config.get_all_models()
    _ok(f"注册表共 {len(all_models)} 个模型")

    table = Table(show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("角色", width=14)
    table.add_column("配置值", min_width=20)
    table.add_column("注册表", justify="center", width=8)
    table.add_column("max_tokens", justify="right", width=11)
    table.add_column("images", justify="center", width=7)

    all_good = True

    for role, name in [
        ("vision_model", config.VISION_MODEL),
        ("text_model", config.TEXT_MODEL),
        ("agent_model", config.AGENT_MODEL),
    ]:
        info = config.get_model_info(name)
        if info is None:
            table.add_row(role, name, "[red]未找到[/red]", "-", "-")
            all_good = False
        else:
            img = "[green]✓[/green]" if info.supports_images else "[red]✗[/red]"
            table.add_row(role, name, "[green]已注册[/green]", str(info.max_tokens), img)

    console.print(table)

    warnings = config.validate_model_config()
    for w in warnings:
        _warn(w)
        all_good = False

    if all_good:
        _ok("模型配置校验通过")
    return all_good


def check_env_credentials() -> bool:
    """Chat API 模式下检查环境变量，LiteLLM 模式下检查常见 API key"""
    console.rule("[bold]3. 凭据 / 环境变量检查")
    from designforge import config

    if config.LLM_PROVIDER == "chat_api":
        _ok(f"Provider: chat_api, Endpoint: {config.LLM_ENDPOINT}")
        required = ["APP_NAME", "APP_ID", "APP_KEY"]
        ok = True
        for var in required:
            val = os.getenv(var, "")
            if val:
                _ok(f"${var} 已设置 (长度 {len(val)})")
            else:
                _fail(f"${var} 未设置 — 请 source env.sh")
                ok = False
        return ok
    else:
        _ok(f"Provider: litellm")
        common_keys = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"]
        found = [k for k in common_keys if os.getenv(k)]
        if found:
            for k in found:
                _ok(f"${k} 已设置")
        else:
            _warn(f"未检测到常见 API key ({', '.join(common_keys)})")
            _warn("如果使用代理或其他方式鉴权，可忽略此警告")
        return True


def check_node_env() -> bool:
    """Node.js 版本检查"""
    console.rule("[bold]4. Node.js 环境检查")
    try:
        from designforge.node_env import (
            get_node_version_display,
            resolve_node_env,
        )
        from designforge.scaffold import REQUIRED_NODE_MAJOR

        env = resolve_node_env()
        ver = get_node_version_display(env)
        _ok(f"Node.js {ver} (要求 >= {REQUIRED_NODE_MAJOR})")
        return True
    except Exception as exc:
        _fail(f"Node.js 环境异常: {exc}")
        return False


def check_paths() -> bool:
    """输入/输出路径检查"""
    console.rule("[bold]5. 路径检查")
    from designforge import config

    ok = True
    if config.INPUT_DIR.is_dir():
        count = sum(1 for _ in config.INPUT_DIR.iterdir() if _.is_dir())
        _ok(f"输入目录存在: {config.INPUT_DIR}  ({count} 个子目录)")
    else:
        _warn(f"输入目录不存在: {config.INPUT_DIR} — 运行时需指定 --input")

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _ok(f"输出目录: {config.OUTPUT_DIR}")
    return ok


def check_api_connectivity(model_override: str | None = None) -> bool:
    """向 LLM API 发送一个最小请求验证连通性"""
    console.rule("[bold]6. API 连通性测试 (轻量请求)")
    from designforge import config

    model = model_override or config.AGENT_MODEL
    console.print(f"  测试模型: [cyan]{model}[/cyan]")

    messages = [{"role": "user", "content": "Say OK"}]

    try:
        start = time.time()
        from designforge.llm_client import completion
        resp = completion(model=model, messages=messages, max_tokens=16)
        elapsed = time.time() - start

        content = ""
        if hasattr(resp, "choices") and resp.choices:
            msg = resp.choices[0].message
            content = getattr(msg, "content", "") or ""

        usage = getattr(resp, "usage", None)
        prompt_tok = getattr(usage, "prompt_tokens", "?") if usage else "?"
        comp_tok = getattr(usage, "completion_tokens", "?") if usage else "?"

        _ok(f"请求成功 ({elapsed:.1f}s)")
        _ok(f"返回内容: {content[:80]!r}")
        _ok(f"Token 用量: prompt={prompt_tok}, completion={comp_tok}")
        return True

    except Exception as exc:
        _fail(f"API 调用失败: {exc}")
        return False


def check_all_models_connectivity() -> dict[str, bool]:
    """遍历注册表中的所有模型，逐个测试 API 连通性"""
    from designforge import config

    all_models = config.get_all_models()
    results = {}

    console.rule("[bold]6+. 全模型 API 连通性测试")
    for name in all_models:
        console.print(f"\n  [cyan]→ {name}[/cyan]")
        try:
            start = time.time()
            from designforge.llm_client import completion
            resp = completion(
                model=name,
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=16,
            )
            elapsed = time.time() - start
            _ok(f"{name}: 成功 ({elapsed:.1f}s)")
            results[name] = True
        except Exception as exc:
            _fail(f"{name}: 失败 — {exc}")
            results[name] = False

    return results


def check_dependencies() -> bool:
    """检查关键 Python 依赖"""
    console.rule("[bold]7. Python 依赖检查")
    deps = {
        "yaml": "PyYAML",
        "httpx": "httpx",
        "rich": "rich",
    }
    ok = True
    for module, pkg in deps.items():
        try:
            __import__(module)
            _ok(f"{pkg}")
        except ImportError:
            _fail(f"{pkg} 未安装 — pip install {pkg}")
            ok = False

    from designforge import config
    if config.LLM_PROVIDER == "litellm":
        try:
            import litellm  # noqa: F401
            _ok("litellm")
        except ImportError:
            _fail("litellm 未安装 — pip install litellm")
            ok = False

    return ok


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def run_preflight(
    skip_api: bool = False,
    model_override: str | None = None,
    all_models: bool = False,
) -> bool:
    console.print(Panel(
        "[bold]DesignForge Preflight Check[/bold]\n"
        "快速检测配置、凭据、环境是否就绪",
        style="blue",
    ))

    results: dict[str, bool] = {}

    results["experiment.yaml"] = check_yaml_loadable()
    results["模型注册表"] = check_model_registry()
    results["凭据/环境变量"] = check_env_credentials()
    results["Node.js"] = check_node_env()
    results["路径"] = check_paths()
    results["Python 依赖"] = check_dependencies()

    if not skip_api:
        if all_models:
            model_results = check_all_models_connectivity()
            for m, ok in model_results.items():
                results[f"API:{m}"] = ok
        else:
            results["API 连通性"] = check_api_connectivity(model_override)

    console.print()
    console.rule("[bold]检查结果汇总")

    summary = Table(show_header=True, header_style="bold", padding=(0, 1))
    summary.add_column("检查项", min_width=20)
    summary.add_column("结果", justify="center", width=8)

    all_passed = True
    for name, passed in results.items():
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        summary.add_row(name, status)
        if not passed:
            all_passed = False

    console.print(summary)

    if all_passed:
        console.print("\n[bold green]✓ 所有检查通过，可以启动系统[/bold green]\n")
    else:
        console.print("\n[bold red]✗ 存在失败项，请修复后再启动[/bold red]\n")

    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DesignForge preflight: 快速检测配置与连通性"
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="跳过 API 连通性测试（仅检查本地配置）",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="指定要测试的模型名称（默认使用 agent_model）",
    )
    parser.add_argument(
        "--all-models",
        action="store_true",
        help="测试注册表中所有模型的 API 连通性",
    )
    args = parser.parse_args()

    ok = run_preflight(
        skip_api=args.skip_api,
        model_override=args.model,
        all_models=args.all_models,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
