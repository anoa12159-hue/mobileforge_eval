"""
AgenticForge — CLI entry point.

Usage:
  python -m designforge --input ./input/wxreader
  python -m designforge --input ./input --batch
"""

from designforge.agentic.runner import main

if __name__ == "__main__":
    main()
