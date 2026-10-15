#!/usr/bin/env bash
# ============================================================================
# run_multi_version.sh — 多版本对比实验（文件替换法）
#
# 通过 git show <ref>:<path> 提取各版本的核心文件，替换当前工作区后运行实验，
# 实验完成后还原为原始状态。不需要切换分支或创建 worktree。
#
# 工作原理:
#   1. 自动分析各版本之间的差异文件
#   2. 备份当前工作区中这些文件的内容到 .experiment_backup/
#   3. 对每个版本：用 git show 拉出该版本的文件 → 覆盖 → 跑实验 → 还原
#   4. 全部结束后确保工作区恢复原样
#
# Usage:
#   source env.sh
#   bash run_multi_version.sh                     # 使用脚本内默认版本
#   bash run_multi_version.sh vanilla-v0 master   # 指定版本
#
# 特殊版本:
#   __working__  当前工作区状态（含未提交修改和新文件），不做任何替换
#
# 环境变量:
#   VERSIONS="vanilla-v0 __working__"   版本列表
#   INPUT="./input/wxreader-8"          输入目录
#   METHOD="agentic"                    实验方法
#   MODELS="gpt-5"                      模型列表
#   OUTPUT_ROOT="./output"              输出根目录
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Colors ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()   { echo -e "${RED}[ERR]${NC}   $*"; }
log_step()  { echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}\n"; }

# ─── Configuration ──────────────────────────────────────────────────────────

YAML="experiment.yaml"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${SCRIPT_DIR}/.experiment_backup/${TIMESTAMP}"

DEFAULT_VERSIONS=(
  "vanilla-v0"
  "__working__"
)

if [[ $# -gt 0 ]]; then
  VERSIONS=("$@")
elif [[ -n "${VERSIONS:-}" ]]; then
  read -ra VERSIONS <<< "$VERSIONS"
else
  VERSIONS=("${DEFAULT_VERSIONS[@]}")
fi

INPUT="${INPUT:-./input/wxreader-8}"
METHOD="${METHOD:-agentic}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$(pwd)/output}"

if [[ -n "${MODELS:-}" ]]; then
  read -ra MODEL_LIST <<< "$MODELS"
else
  MODEL_LIST=("gpt-5")
fi

# ─── Version ref resolution ────────────────────────────────────────────────

resolve_ref() {
  local ref="$1"
  if git rev-parse --verify "refs/tags/$ref" &>/dev/null; then
    echo "refs/tags/$ref"
  elif git rev-parse --verify "refs/heads/$ref" &>/dev/null; then
    echo "refs/heads/$ref"
  elif git rev-parse --verify "$ref" &>/dev/null; then
    echo "$ref"
  else
    echo ""
  fi
}

sanitize_name() {
  echo "$1" | tr '/' '_' | tr ':' '_'
}

version_display() {
  local v="$1"
  if [[ "$v" == "__working__" ]]; then
    echo "working（当前工作区）"
  else
    local sha
    sha="$(git rev-parse --short "$v" 2>/dev/null || echo '?')"
    echo "$v ($sha)"
  fi
}

# ─── Method files vs infrastructure ─────────────────────────────────────────
# 只替换「方法实现」文件，runner/baselines 等基础设施始终用最新版本。
# 在此列表中添加新的方法核心文件路径模式。

METHOD_FILE_PATTERNS=(
  "designforge/agentic/core.py"
  "designforge/agentic/prompts.py"
  "designforge/agents/tool_agent.py"
  "designforge/agents/base_agent.py"
  "designforge/config.py"
  "designforge/context_store.py"
  "designforge/llm_client.py"
  "designforge/scaffold.py"
)

is_method_file() {
  local file="$1"
  for pattern in "${METHOD_FILE_PATTERNS[@]}"; do
    [[ "$file" == "$pattern" ]] && return 0
  done
  return 1
}

# ─── Diff analysis: find METHOD files that differ between versions ──────────

collect_diff_files() {
  local -n _result=$1
  shift
  local versions=("$@")

  local -A file_set=()

  for v in "${versions[@]}"; do
    [[ "$v" == "__working__" ]] && continue

    local resolved
    resolved="$(resolve_ref "$v")"
    [[ -z "$resolved" ]] && continue

    while IFS= read -r f; do
      is_method_file "$f" && file_set["$f"]=1
    done < <(git diff --name-only "$resolved" HEAD 2>/dev/null)
  done

  while IFS= read -r f; do
    is_method_file "$f" && file_set["$f"]=1
  done < <(git diff --name-only HEAD 2>/dev/null)

  while IFS= read -r f; do
    is_method_file "$f" && file_set["$f"]=1
  done < <(git ls-files --others --exclude-standard -- designforge/ 2>/dev/null)

  for f in "${!file_set[@]}"; do
    _result+=("$f")
  done
}

# ─── Backup current working state of affected files ────────────────────────

backup_files() {
  local backup_root="$1"
  shift
  local files=("$@")

  mkdir -p "$backup_root"

  log_info "备份 ${#files[@]} 个文件到 ${backup_root}"

  local manifest="${backup_root}/manifest.txt"
  : > "$manifest"

  for f in "${files[@]}"; do
    local src="${SCRIPT_DIR}/${f}"
    local dst="${backup_root}/${f}"
    mkdir -p "$(dirname "$dst")"

    if [[ -f "$src" ]]; then
      cp "$src" "$dst"
      echo "EXISTS ${f}" >> "$manifest"
      log_info "  备份: ${f}"
    else
      echo "ABSENT ${f}" >> "$manifest"
      log_info "  记录不存在: ${f}"
    fi
  done

  log_ok "备份完成: ${backup_root}"
}

# ─── Restore files from backup ─────────────────────────────────────────────

restore_files() {
  local backup_root="$1"
  local manifest="${backup_root}/manifest.txt"

  if [[ ! -f "$manifest" ]]; then
    log_err "备份清单不存在: ${manifest}"
    return 1
  fi

  log_info "从 ${backup_root} 还原文件..."

  while IFS=' ' read -r status filepath; do
    local target="${SCRIPT_DIR}/${filepath}"
    if [[ "$status" == "EXISTS" ]]; then
      local bak="${backup_root}/${filepath}"
      mkdir -p "$(dirname "$target")"
      cp "$bak" "$target"
      log_info "  还原: ${filepath}"
    elif [[ "$status" == "ABSENT" ]]; then
      if [[ -f "$target" ]]; then
        rm -f "$target"
        log_info "  删除（原本不存在）: ${filepath}"
      fi
    fi
  done < "$manifest"

  log_ok "还原完成"
}

# ─── Apply a version's files to the working directory ──────────────────────

apply_version() {
  local version="$1"
  shift
  local files=("$@")

  if [[ "$version" == "__working__" ]]; then
    log_info "版本 __working__：使用备份中的工作区原始文件"
    restore_files "$BACKUP_DIR"
    return 0
  fi

  local resolved
  resolved="$(resolve_ref "$version")"
  if [[ -z "$resolved" ]]; then
    log_err "无法解析版本: $version"
    return 1
  fi

  log_info "应用版本 ${version} 的文件..."

  for f in "${files[@]}"; do
    local target="${SCRIPT_DIR}/${f}"

    if git cat-file -e "${resolved}:${f}" 2>/dev/null; then
      mkdir -p "$(dirname "$target")"
      git show "${resolved}:${f}" > "$target"
      log_info "  替换: ${f}"
    else
      if [[ -f "$target" ]]; then
        rm -f "$target"
        log_info "  删除（该版本中不存在）: ${f}"
      fi
    fi
  done

  log_ok "版本 ${version} 文件已就位"
}

# ─── Run experiment ────────────────────────────────────────────────────────

run_experiment() {
  local version_tag="$1"
  local model="$2"
  local safe_version
  safe_version="$(sanitize_name "$version_tag")"

  if [[ -f "$YAML" ]]; then
    sed -i "s/^  agent_model: .*/  agent_model: ${model}/" "$YAML"
  fi

  log_info "运行实验: version=${version_tag}  model=${model}  method=${METHOD}"

  python -m designforge.baselines \
    --method "$METHOD" \
    --input "$INPUT" \
    --suffix "$safe_version" \
    -v

  local rc=$?

  if [[ $rc -eq 0 ]]; then
    log_ok "实验完成: ${version_tag}/${model}"
  else
    log_err "实验失败 (exit=${rc}): ${version_tag}/${model}"
  fi
  return $rc
}

# ─── Main ──────────────────────────────────────────────────────────────────

main() {
  log_step "多版本对比实验"

  echo -e "${BOLD}配置:${NC}"
  echo "  版本:  ${VERSIONS[*]}"
  echo "  模型:  ${MODEL_LIST[*]}"
  echo "  方法:  ${METHOD}"
  echo "  输入:  ${INPUT}"
  echo "  输出:  ${OUTPUT_ROOT}"
  echo ""

  # ── 验证版本 ──
  for v in "${VERSIONS[@]}"; do
    if [[ "$v" == "__working__" ]]; then
      log_ok "版本: $(version_display "$v")"
      continue
    fi
    local resolved
    resolved="$(resolve_ref "$v")"
    if [[ -z "$resolved" ]]; then
      log_err "版本 '${v}' 不存在。"
      echo "  可用标签: $(git tag -l | tr '\n' ' ')"
      echo "  可用分支: $(git branch --format='%(refname:short)' | tr '\n' ' ')"
      exit 1
    fi
    log_ok "版本: $(version_display "$v")"
  done

  # ── 分析差异文件 ──
  log_step "Phase 1: 分析版本差异"

  local diff_files=()
  collect_diff_files diff_files "${VERSIONS[@]}"

  if [[ ${#diff_files[@]} -eq 0 ]]; then
    log_warn "各版本之间没有文件差异，将使用当前代码运行所有版本"
  else
    log_info "版本间差异文件（${#diff_files[@]} 个）:"
    for f in "${diff_files[@]}"; do
      echo -e "  ${DIM}•${NC} ${f}"
    done
    echo ""

    for v in "${VERSIONS[@]}"; do
      [[ "$v" == "__working__" ]] && continue
      local resolved
      resolved="$(resolve_ref "$v")"
      [[ -z "$resolved" ]] && continue
      echo -e "  ${BOLD}${v}${NC} 相对于工作区的差异:"
      for f in "${diff_files[@]}"; do
        local in_version
        in_version=$(git cat-file -e "${resolved}:${f}" 2>/dev/null && echo "Y" || echo "N")
        local in_working
        in_working=$([[ -f "${SCRIPT_DIR}/${f}" ]] && echo "Y" || echo "N")
        local marker=""
        if [[ "$in_version" == "Y" && "$in_working" == "Y" ]]; then
          if ! git diff --quiet "${resolved}" -- "$f" 2>/dev/null; then
            marker="${YELLOW}修改${NC}"
          else
            marker="${DIM}相同${NC}"
          fi
        elif [[ "$in_version" == "N" && "$in_working" == "Y" ]]; then
          marker="${RED}该版本无此文件${NC}"
        elif [[ "$in_version" == "Y" && "$in_working" == "N" ]]; then
          marker="${GREEN}该版本独有${NC}"
        fi
        echo -e "    ${f}  ${marker}"
      done
      echo ""
    done
  fi

  # ── 备份 ──
  log_step "Phase 2: 备份工作区"
  if [[ ${#diff_files[@]} -gt 0 ]]; then
    backup_files "$BACKUP_DIR" "${diff_files[@]}"
  else
    mkdir -p "$BACKUP_DIR"
    : > "$BACKUP_DIR/manifest.txt"
    log_info "无需备份（无差异文件）"
  fi

  # ── 依次运行每个版本 ──
  log_step "Phase 3: 运行实验"

  local -A all_results=()
  local run_order=()

  local has_working=false
  local other_versions=()
  for v in "${VERSIONS[@]}"; do
    if [[ "$v" == "__working__" ]]; then
      has_working=true
    else
      other_versions+=("$v")
    fi
  done

  # __working__ 先跑：此时文件就是原样，不需要任何替换
  if $has_working; then
    log_step "运行: __working__（当前工作区状态）"
    for model in "${MODEL_LIST[@]}"; do
      local key="__working__/${model}"
      run_order+=("$key")
      run_experiment "__working__" "$model" && \
        all_results["$key"]="OK" || all_results["$key"]="FAIL"
    done
  fi

  # 其余版本：替换 → 跑 → 还原
  for v in "${other_versions[@]}"; do
    log_step "运行: $(version_display "$v")"

    if [[ ${#diff_files[@]} -gt 0 ]]; then
      apply_version "$v" "${diff_files[@]}"
    fi

    for model in "${MODEL_LIST[@]}"; do
      local key="${v}/${model}"
      run_order+=("$key")
      run_experiment "$v" "$model" && \
        all_results["$key"]="OK" || all_results["$key"]="FAIL"
    done

    # 每个版本跑完立即还原，确保下一个版本从干净状态开始
    if [[ ${#diff_files[@]} -gt 0 ]]; then
      log_info "还原工作区（准备下一版本）..."
      restore_files "$BACKUP_DIR"
    fi
  done

  # ── 最终还原确认 ──
  log_step "Phase 4: 最终还原确认"
  if [[ ${#diff_files[@]} -gt 0 ]]; then
    restore_files "$BACKUP_DIR"
    log_ok "工作区已恢复到实验前状态"
  fi

  # ── 验证还原 ──
  local restore_ok=true
  for f in "${diff_files[@]}"; do
    local src="${SCRIPT_DIR}/${f}"
    local bak="${BACKUP_DIR}/${f}"
    local manifest_line
    manifest_line="$(grep " ${f}$" "$BACKUP_DIR/manifest.txt" 2>/dev/null || true)"

    if [[ "$manifest_line" == "EXISTS "* ]]; then
      if [[ -f "$src" ]] && diff -q "$src" "$bak" &>/dev/null; then
        : # match
      else
        log_err "还原校验失败: ${f}"
        restore_ok=false
      fi
    elif [[ "$manifest_line" == "ABSENT "* ]]; then
      if [[ -f "$src" ]]; then
        log_err "还原校验失败（应删除）: ${f}"
        restore_ok=false
      fi
    fi
  done

  if $restore_ok; then
    log_ok "还原校验通过：所有文件与实验前一致"
  else
    log_err "还原校验有异常，请检查 ${BACKUP_DIR}"
  fi

  # ── 汇总 ──
  log_step "实验结果汇总"

  local total=0 ok_count=0 fail_count=0

  printf "\n  ${BOLD}%-35s %-12s %-25s${NC}\n" "版本 / 模型" "状态" "版本信息"
  printf "  %-35s %-12s %-25s\n" "───────────────────────────────" "─────────" "─────────────────────"

  for key in "${run_order[@]}"; do
    local status="${all_results[$key]:-UNKNOWN}"
    local v="${key%%/*}"
    total=$((total + 1))
    case "$status" in
      OK)
        ok_count=$((ok_count + 1))
        printf "  %-35s ${GREEN}%-12s${NC} %s\n" "$key" "✓ 成功" "$(version_display "$v")"
        ;;
      FAIL)
        fail_count=$((fail_count + 1))
        printf "  %-35s ${RED}%-12s${NC} %s\n" "$key" "✗ 失败" "$(version_display "$v")"
        ;;
      *)
        printf "  %-35s ${YELLOW}%-12s${NC} %s\n" "$key" "? 未知" "$(version_display "$v")"
        ;;
    esac
  done

  echo ""
  echo -e "  总计: ${total}   ${GREEN}成功: ${ok_count}${NC}   ${RED}失败: ${fail_count}${NC}"
  echo -e "  备份: ${BACKUP_DIR}"
  echo ""

  if [[ $fail_count -gt 0 ]]; then
    log_err "部分实验失败"
    exit 1
  else
    log_ok "所有实验完成"
  fi
}

main
