"""CLI entry point: ``python -m human_eval <experiment_dir>``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .server import start_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m human_eval",
        description="启动人工-VLM 一致性评估的网页平台。",
    )
    parser.add_argument(
        "experiment_dir",
        type=Path,
        help="实验目录，例如 experiments/20260428_181902_mobileforge-compare",
    )
    parser.add_argument("--host", default="0.0.0.0", help="dashboard 绑定的 host (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8090, help="dashboard 端口 (默认 8090)")
    parser.add_argument(
        "--preview-host",
        default="0.0.0.0",
        help="子 app 预览服务器绑定的 host (默认 0.0.0.0)",
    )
    parser.add_argument(
        "--preview-port",
        type=int,
        default=None,
        help="子 app 预览端口 (默认 = --port + 1)。设为 0 走临时端口（不利于远程访问）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="打印更多日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        start_server(
            args.experiment_dir,
            host=args.host,
            port=args.port,
            preview_host=args.preview_host,
            preview_port=args.preview_port,
        )
    except FileNotFoundError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
