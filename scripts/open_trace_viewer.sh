#!/usr/bin/env bash
# Open DesignForge trace viewer (viewer.html) for a task output or traces directory.
#
# Usage:
#   ./scripts/open_trace_viewer.sh [--serve] [--port N] <path>
#
# <path> may be:
#   - path/to/viewer.html
#   - path/to/.designforge/traces
#   - path/to/task_root (e.g. output/wxreader)
#
# Default: open file:// in the default browser (no HTTP server).
# --serve:   serve the traces directory with python -m http.server, then open http://127.0.0.1:<port>/viewer.html
#            Press Ctrl+C to stop the server.

set -euo pipefail

SERVE=false
PORT=8765
TARGET=""

usage() {
  cat <<'EOF'
Open DesignForge trace viewer (viewer.html).

Usage:
  open_trace_viewer.sh [--serve] [--port N] <path>

<path> may be:
  - path/to/viewer.html
  - path/to/.designforge/traces
  - path/to/task_root (e.g. output/wxreader)

  --serve, -s   Use python3 http.server instead of file://
  --port,  -p   Port for --serve (default: 8765)
EOF
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --serve|-s) SERVE=true; shift ;;
    --port|-p)
      PORT="${2:-}"
      if [ -z "$PORT" ]; then echo "error: --port needs a value" >&2; exit 1; fi
      shift 2
      ;;
    -h|--help) usage 0 ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage 1
      ;;
    *)
      if [ -n "$TARGET" ]; then
        echo "error: only one path allowed" >&2
        exit 1
      fi
      TARGET="$1"
      shift
      ;;
  esac
done

if [ -z "$TARGET" ]; then
  echo "error: missing path" >&2
  usage 1
fi

resolve_viewer() {
  local p="$1"
  [ -e "$p" ] || { echo "error: path does not exist: $p" >&2; return 1; }

  if [ -f "$p" ]; then
    case "$p" in
      */viewer.html) echo "$p"; return 0 ;;
      *)
        echo "error: not a viewer.html file: $p" >&2
        return 1
        ;;
    esac
  fi

  # directory: traces folder
  if [ -f "$p/viewer.html" ]; then
    echo "$p/viewer.html"
    return 0
  fi

  # task root: .designforge/traces/viewer.html
  if [ -f "$p/.designforge/traces/viewer.html" ]; then
    echo "$p/.designforge/traces/viewer.html"
    return 0
  fi

  echo "error: could not find viewer.html under: $p" >&2
  echo "  (expect task root, .designforge/traces, or viewer.html)" >&2
  return 1
}

VIEWER="$(resolve_viewer "$TARGET")" || exit 1
TRACES_DIR="$(cd "$(dirname "$VIEWER")" && pwd)"

open_url() {
  local url="$1"
  case "$(uname -s)" in
    Darwin) open "$url" ;;
    *)       xdg-open "$url" >/dev/null 2>&1 || true ;;
  esac
}

if [ "$SERVE" = false ]; then
  open_url "file://$VIEWER"
  echo "Opened: file://$VIEWER"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required for --serve" >&2
  exit 1
fi

cd "$TRACES_DIR"
python3 -m http.server "$PORT" --bind 127.0.0.1 &
HTTP_PID=$!

cleanup() {
  kill "$HTTP_PID" 2>/dev/null || true
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

sleep 0.2
if ! kill -0 "$HTTP_PID" 2>/dev/null; then
  echo "error: http.server failed to start (port $PORT in use?)" >&2
  exit 1
fi

URL="http://127.0.0.1:${PORT}/viewer.html"
open_url "$URL"
echo "Trace viewer: $URL"
echo "Serving directory: $TRACES_DIR"
echo "Press Ctrl+C to stop."

wait "$HTTP_PID" || true
cleanup
