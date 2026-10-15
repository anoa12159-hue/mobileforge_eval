#!/usr/bin/env bash
# Run agentic baseline with multiple models sequentially.
#
# Usage:
#   source env.sh
#   bash run_multi_model.sh

set -uo pipefail
cd "$(dirname "$0")"

YAML="experiment.yaml"
INPUT="./input/wxreader-4"
METHOD="agentic"
DEFAULT_MODEL="claude-sonnet-4"

MODELS=(
  "claude-opus-4-6"
  # "claude-sonnet-4"
  # "gpt-5.1"
  # "gpt-5"
  # "gemini-2.5-pro"
)

# Robustly set agent_model in the YAML config.
# Uses '|' as sed delimiter to avoid conflicts with model names containing '/'.
# Matches agent_model with any leading whitespace (not hardcoded indent).
set_agent_model() {
  local target="$1"
  local before after
  before=$(grep -c '^\s*agent_model:' "$YAML")
  if [[ "$before" -eq 0 ]]; then
    echo "[ERROR] agent_model key not found in ${YAML}" >&2
    return 1
  fi
  sed -i "s|^\(\s*\)agent_model:.*|\1agent_model: ${target}|" "$YAML"
  after=$(grep -oP '(?<=agent_model:\s).*' "$YAML" | head -1 | xargs)
  if [[ "$after" != "$target" ]]; then
    echo "[ERROR] Failed to set agent_model to '${target}', got '${after}'" >&2
    return 1
  fi
}

restore_default() {
  echo ""
  echo "Restoring agent_model to ${DEFAULT_MODEL}..."
  set_agent_model "$DEFAULT_MODEL" || echo "[ERROR] Restore failed!" >&2
}
trap restore_default EXIT

for model in "${MODELS[@]}"; do
  echo ""
  echo "============================================"
  echo "  Running: ${METHOD} with ${model}"
  echo "============================================"
  echo ""

  if ! set_agent_model "$model"; then
    echo "[WARN] Skipping ${model}: could not set agent_model"
    continue
  fi
  echo "  agent_model set to: ${model}"

  python -m designforge.baselines --method "$METHOD" --input "$INPUT" -v || {
    echo "[WARN] ${model} run failed, continuing..."
  }

  echo ""
  echo "  Done: ${model}"
done

echo ""
echo "All runs complete."
