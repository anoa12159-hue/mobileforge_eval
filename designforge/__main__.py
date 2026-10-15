"""Allow running as `python -m designforge`.

Subcommands:
    python -m designforge                  # default: agentic pipeline
    python -m designforge preflight        # config & connectivity pre-check
    python -m designforge preflight --skip-api
    python -m designforge preflight --model gemini-3-flash
    python -m designforge preflight --all-models
"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "preflight":
    sys.argv = sys.argv[:1] + sys.argv[2:]  # strip "preflight" so argparse sees the rest
    from designforge.preflight import main as preflight_main
    preflight_main()
else:
    from designforge.agentic.runner import main
    main()
