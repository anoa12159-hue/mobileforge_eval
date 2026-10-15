#!/bin/bash
# DesignForge 环境变量配置
# 使用方式: source env.sh

# ============================================================
# Chat API 凭据 (当 experiment.yaml 中 llm.provider = "chat_api" 时使用)
# ============================================================
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:$LD_LIBRARY_PATH"
export HF_TOKEN="REDACTED_HF_TOKEN"
conda activate designforge
nvm use 20

# ============================================================
# API 中转服务 (当 experiment.yaml 中 llm.provider = "litellm" 时使用)
# 同一个 key 同时挂到 Anthropic 和 OpenAI 两条 LiteLLM 路由上，
# experiment.yaml 里的 model 前缀决定走哪条：
#   anthropic/* → https://api-relay.example.invalid  (Anthropic 协议，/v1/messages)
#   openai/*    → https://api-relay.example.invalid/v1 (OpenAI 协议，/chat/completions)
# ============================================================
export RELAY_API_KEY="REDACTED_RELAY_API_KEY"

# Anthropic 路由 (Claude 系列)
export ANTHROPIC_API_KEY="$RELAY_API_KEY"
export ANTHROPIC_API_BASE="https://api-relay.example.invalid"

# OpenAI 路由 (GPT 系列)
export OPENAI_API_KEY="$RELAY_API_KEY"
export OPENAI_API_BASE="https://api-relay.example.invalid/v1"
