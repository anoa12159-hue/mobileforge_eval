"""
Unified LLM client — dispatches to LiteLLM or Chat API based on experiment config.

Provides a single `completion()` function that both BaseAgent and ToolAgent call.
Returns LiteLLM-compatible response objects regardless of underlying provider,
so downstream code doesn't need to know which backend is in use.

Chat API: Internal API with HMAC auth, OpenAI-compatible request format.
LiteLLM: Standard multi-provider proxy (100+ models).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMCallError(RuntimeError):
    """LLM API 调用重试耗尽后抛出，表示 API 系统性不可用。

    批量实验的外层循环可捕获此异常来区分"构建逻辑失败"和"API 不可用"，
    从而实现连续失败时自动跳过模型。
    """


_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
_HMAC_EXPIRED_KEYWORDS = ("时间窗口", "checkHMACAuth", "Auth fail")
_BASE_BACKOFF_SECS = 2

# Lazy-loaded to avoid circular imports
_config = None


def _get_config():
    global _config
    if _config is None:
        from designforge import config as cfg
        _config = cfg
    return _config


# ======================================================================
# Public API
# ======================================================================


def completion(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    tools: list[dict] | None = None,
    **kwargs: Any,
) -> Any:
    """Unified LLM completion call with automatic retry.

    Retries up to ``max_retries`` times (configured in experiment.yaml → llm.max_retries)
    on transient HTTP errors (429/5xx), connection failures, and timeouts.
    Uses exponential back-off: 2s, 4s, 8s, …
    """
    cfg = _get_config()
    if max_tokens is None:
        max_tokens = cfg.get_max_tokens(model)

    max_retries: int = cfg.MAX_RETRIES
    last_exc: Exception | None = None
    errors_log: list[str] = []

    for attempt in range(1, max_retries + 1):
        try:
            if cfg.LLM_PROVIDER == "chat_api":
                return _chat_api_completion(model, messages, max_tokens, tools, **kwargs)
            else:
                import litellm
                litellm.drop_params = True
                return litellm.completion(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    tools=tools if tools else None,
                    **kwargs,
                )
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
            detail = _describe_error(exc, model, attempt, max_retries)
            errors_log.append(detail)

            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in _RETRYABLE_HTTP_CODES:
                if exc.response.status_code == 401 and _is_hmac_expired(exc.response):
                    logger.warning("[llm] HMAC 签名过期 (401)，重新签名后重试 (第 %d/%d 次)\n%s",
                                   attempt, max_retries, detail)
                    if attempt < max_retries:
                        time.sleep(1)
                        continue
                logger.error("[llm] 不可重试的 HTTP 错误，立即终止:\n%s", detail)
                break

            if attempt < max_retries:
                backoff = _BASE_BACKOFF_SECS * (2 ** (attempt - 1))
                logger.warning("[llm] %s — %ds 后重试 (第 %d/%d 次)\n%s",
                               type(exc).__name__, backoff, attempt, max_retries, detail)
                time.sleep(backoff)
            else:
                logger.error("[llm] 已达最大重试次数 (%d)，放弃请求\n%s", max_retries, detail)
        except Exception as exc:
            last_exc = exc
            detail = _describe_error(exc, model, attempt, max_retries)
            errors_log.append(detail)
            logger.error("[llm] 不可重试的异常，立即终止:\n%s", detail)
            break

    summary = "\n---\n".join(errors_log)
    raise LLMCallError(
        f"LLM 调用失败 (model={model}, 共尝试 {len(errors_log)} 次):\n{summary}"
    ) from last_exc


def _describe_error(exc: Exception, model: str, attempt: int, max_retries: int) -> str:
    """Build a human-readable error description for logging and final raise."""
    parts = [
        f"[第 {attempt}/{max_retries} 次尝试] model={model}",
        f"异常类型: {type(exc).__name__}",
        f"异常信息: {exc}",
    ]
    if isinstance(exc, httpx.HTTPStatusError):
        resp = exc.response
        parts.append(f"HTTP 状态码: {resp.status_code}")
        parts.append(f"请求 URL: {resp.request.url}")
        try:
            body = resp.text[:2000]
        except Exception:
            body = "<无法读取响应体>"
        parts.append(f"响应体 (前 2000 字符): {body}")
    elif isinstance(exc, httpx.TimeoutException):
        parts.append("原因: 请求超时，服务端未在规定时间内返回")
    elif isinstance(exc, httpx.ConnectError):
        parts.append("原因: 无法建立连接，请检查网络或 endpoint 配置")
    parts.append(f"堆栈:\n{traceback.format_exc()}")
    return "\n".join(parts)


def _is_hmac_expired(response: httpx.Response) -> bool:
    """Check if a 401 response is caused by HMAC timestamp drift (retryable)."""
    try:
        body = response.text[:2000]
    except Exception:
        return False
    return any(kw in body for kw in _HMAC_EXPIRED_KEYWORDS)


# ======================================================================
# Chat API provider
# ======================================================================


_chat_api_initialized = False
_chat_api_lock = threading.Lock()
_app_name = ""
_app_id = ""
_app_key = ""


def _ensure_chat_api_init() -> None:
    global _chat_api_initialized, _app_name, _app_id, _app_key
    if _chat_api_initialized:
        return
    with _chat_api_lock:
        if _chat_api_initialized:
            return
        _app_name = os.getenv("APP_NAME", "")
        _app_id = os.getenv("APP_ID", "")
        _app_key = os.getenv("APP_KEY", "")
        if not all([_app_name, _app_id, _app_key]):
            raise RuntimeError(
                "Chat API 凭据不完整，请通过 source env.sh 设置 APP_NAME / APP_ID / APP_KEY"
            )
        _chat_api_initialized = True


def _chat_api_auth_headers() -> dict[str, str]:
    _ensure_chat_api_init()
    ts = int(time.time())
    sign = hmac.new(
        _app_key.encode(),
        f"x-timestamp: {ts}\nx-source: {_app_name}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "x-authorization": sign,
        "x-source": _app_name,
        "x-timestamp": str(ts),
        "x-appid": _app_id,
        "Content-Type": "application/json",
    }


_GEMINI_NATIVE_PREFIXES = ("gemini-3", "gemini-3.")


def _needs_native_genai(model: str) -> bool:
    """Gemini 3+ models require native GenAI API for thought_signature support."""
    return any(model.startswith(p) for p in _GEMINI_NATIVE_PREFIXES)


def _chat_api_completion(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    tools: list[dict] | None = None,
    **kwargs: Any,
) -> _ChatApiResponse:
    if _needs_native_genai(model):
        return _gemini_native_completion(model, messages, max_tokens, tools)

    cfg = _get_config()
    endpoint = cfg.LLM_ENDPOINT

    _NEW_API_PREFIXES = ("gpt-4o", "gpt-4-turbo", "gpt-4.1", "gpt-5", "o1", "o3", "o4")
    use_new_param = any(model.startswith(p) for p in _NEW_API_PREFIXES)

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens" if use_new_param else "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools

    logger.debug("[chat_api] POST %s model=%s tools=%s", endpoint, model, bool(tools))

    resp = httpx.post(
        endpoint,
        json=body,
        headers=_chat_api_auth_headers(),
        timeout=httpx.Timeout(connect=30, read=420, write=30, pool=30),
    )
    resp.raise_for_status()
    result = resp.json()

    if result.get("code") not in (0, None):
        raise RuntimeError(f"Chat API 错误: {result.get('msg', result)}")

    logger.debug("[chat_api] response keys=%s, has detail=%s",
                 list(result.keys()), "detail" in result)

    return _parse_chat_api_response(result)


def _parse_chat_api_response(result: dict[str, Any]) -> _ChatApiResponse:
    """Parse Chat API response into a LiteLLM-compatible object.

    Chat API wraps the OpenAI-compatible payload inside result["detail"].
    Fallback: simplified {"code": 0, "response": "..."} format (no detail).
    """
    detail = result.get("detail", result)

    if "choices" in detail:
        return _parse_openai_format(detail)

    text = result.get("response", "")
    usage = detail.get("usage", result.get("usage", {}))
    return _ChatApiResponse(
        choices=[_Choice(
            message=_Message(content=text, tool_calls=None),
            finish_reason="stop",
        )],
        usage=_Usage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        ),
    )


def _extract_thought_signature(obj: dict[str, Any]) -> str | None:
    """Extract thought_signature from various possible locations.

    Chat API/Gemini may place it at:
      - obj["thought_signature"]  (direct)
      - obj["extra_content"]["google"]["thought_signature"]  (Chat API wrapper)
    """
    sig = obj.get("thought_signature")
    if sig:
        return sig
    extra = obj.get("extra_content")
    if isinstance(extra, dict):
        google = extra.get("google")
        if isinstance(google, dict):
            return google.get("thought_signature")
    return None


def _parse_openai_format(result: dict[str, Any]) -> _ChatApiResponse:
    """Parse a full OpenAI-compatible response from Chat API."""
    raw_choices = result.get("choices", [])
    usage_raw = result.get("usage", {})

    choices = []
    for rc in raw_choices:
        raw_msg = rc.get("message", {})
        tool_calls = None
        if raw_msg.get("tool_calls"):
            tool_calls = [
                _ToolCall(
                    id=tc.get("id", ""),
                    function=_Function(
                        name=tc.get("function", {}).get("name", ""),
                        arguments=tc.get("function", {}).get("arguments", "{}"),
                    ),
                    thought_signature=_extract_thought_signature(tc),
                )
                for tc in raw_msg["tool_calls"]
            ]
        choices.append(_Choice(
            message=_Message(
                content=raw_msg.get("content"),
                tool_calls=tool_calls,
                thought_signature=_extract_thought_signature(raw_msg),
            ),
            finish_reason=rc.get("finish_reason", "stop"),
        ))

    return _ChatApiResponse(
        choices=choices,
        usage=_Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
        ),
    )


# ======================================================================
# LiteLLM-compatible response data classes for Chat API
# ======================================================================


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Function
    type: str = "function"
    thought_signature: str | None = None


@dataclass
class _Message:
    content: str | None
    tool_calls: list[_ToolCall] | None = None
    role: str = "assistant"
    thought_signature: str | None = None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self):
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens


@dataclass
class _ChatApiResponse:
    choices: list[_Choice]
    usage: _Usage


# ======================================================================
# Gemini Native GenAI API (for Gemini 3+ thought_signature support)
# ======================================================================

_CHAT_API_GENAI_BASE = "http://chat-api.example.invalid/api/google-genai/models"


def _gemini_native_completion(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    tools: list[dict] | None = None,
) -> _ChatApiResponse:
    """Call Gemini via Chat API's native GenAI endpoint to support thought_signature."""
    cfg = _get_config()
    endpoint = f"{_CHAT_API_GENAI_BASE}/{model}:generateContent"
    body = _openai_to_genai_request(messages, tools, max_tokens,
                                    thinking_budget=cfg.GEMINI_THINKING_BUDGET)

    logger.debug("[gemini-native] POST %s", endpoint)

    read_timeout = cfg.GEMINI_READ_TIMEOUT
    resp = httpx.post(
        endpoint,
        json=body,
        headers=_chat_api_auth_headers(),
        timeout=httpx.Timeout(connect=30, read=read_timeout, write=30, pool=30),
    )
    resp.raise_for_status()
    result = resp.json()

    if "error" in result:
        raise RuntimeError(f"Gemini GenAI API 错误: {result['error'].get('message', result)}")

    return _parse_genai_response(result)


def _user_content_to_parts(content: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI user content (string or multimodal list) to Gemini parts."""
    if isinstance(content, str):
        return [{"text": content}] if content else [{"text": ""}]

    parts: list[dict[str, Any]] = []
    for block in content:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append({"text": block.get("text", "")})
        elif block_type == "image_url":
            url: str = block.get("image_url", {}).get("url", "")
            if url.startswith("data:"):
                header, _, b64data = url.partition(",")
                mime = header.split(";")[0].removeprefix("data:")
                parts.append({"inlineData": {"mimeType": mime, "data": b64data}})
            else:
                parts.append({"text": f"[image: {url}]"})
    return parts or [{"text": ""}]


def _openai_to_genai_request(
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
    max_tokens: int,
    *,
    thinking_budget: int = 1024,
) -> dict[str, Any]:
    """Convert OpenAI-format messages+tools to Gemini native generateContent body."""
    contents: list[dict[str, Any]] = []
    system_text: str | None = None
    # Map tool_call_id → function name for resolving tool response names
    tc_id_to_name: dict[str, str] = {}

    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            system_text = msg.get("content", "")
        elif role == "user":
            contents.append({"role": "user", "parts": _user_content_to_parts(msg.get("content", ""))})
        elif role == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                tc_id_to_name[tc.get("id", "")] = fn.get("name", "")
            parts = _assistant_msg_to_parts(msg)
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            _append_tool_response(contents, msg, tc_id_to_name)

    body: dict[str, Any] = {"contents": contents}

    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    if tools:
        body["tools"] = [_openai_tools_to_genai(tools)]

    gen_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
    if thinking_budget >= 0:
        gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    body["generationConfig"] = gen_config

    return body


def _assistant_msg_to_parts(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert an assistant message (with possible tool_calls) to Gemini parts."""
    parts: list[dict[str, Any]] = []

    content = msg.get("content")
    if content:
        parts.append({"text": content})

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            args = {}

        part: dict[str, Any] = {
            "functionCall": {"name": fn.get("name", ""), "args": args}
        }
        sig = tc.get("thought_signature")
        if not sig:
            ec = tc.get("extra_content")
            if isinstance(ec, dict):
                g = ec.get("google")
                if isinstance(g, dict):
                    sig = g.get("thought_signature")
        if sig:
            part["thoughtSignature"] = sig
        parts.append(part)

    return parts or [{"text": ""}]


def _append_tool_response(
    contents: list[dict[str, Any]],
    msg: dict[str, Any],
    tc_id_to_name: dict[str, str],
) -> None:
    """Append a tool response as a functionResponse part.

    Groups consecutive tool messages into a single user turn (Gemini requires
    functionResponse parts in a user role content block).
    """
    fn_name = msg.get("name", "")
    tool_call_id = msg.get("tool_call_id", "")

    if not fn_name and tool_call_id:
        fn_name = tc_id_to_name.get(tool_call_id, "")

    content_str = msg.get("content", "")
    try:
        response_val = json.loads(content_str)
    except (json.JSONDecodeError, TypeError):
        response_val = {"result": content_str}

    part: dict[str, Any] = {
        "functionResponse": {"name": fn_name, "response": response_val}
    }
    sig = msg.get("thought_signature")
    if sig:
        part["thoughtSignature"] = sig

    # If the last content block is already a user turn with functionResponse parts, merge
    if contents and contents[-1].get("role") == "user":
        last_parts = contents[-1].get("parts", [])
        if last_parts and "functionResponse" in last_parts[-1]:
            contents[-1]["parts"].append(part)
            return

    contents.append({"role": "user", "parts": [part]})


def _openai_tools_to_genai(tools: list[dict]) -> dict[str, Any]:
    """Convert OpenAI tools format to Gemini functionDeclarations."""
    declarations = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        fn = tool.get("function", {})
        params = fn.get("parameters", {})
        decl: dict[str, Any] = {
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
        }
        if params:
            decl["parameters"] = _convert_json_schema_to_genai(params)
        declarations.append(decl)
    return {"functionDeclarations": declarations}


def _convert_json_schema_to_genai(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert JSON Schema to Gemini's parameter schema format."""
    result: dict[str, Any] = {}
    schema_type = schema.get("type", "")
    if schema_type:
        result["type"] = schema_type.upper()

    if "description" in schema:
        result["description"] = schema["description"]
    if "enum" in schema:
        result["enum"] = schema["enum"]
    if "required" in schema:
        result["required"] = schema["required"]

    if "properties" in schema:
        result["properties"] = {
            k: _convert_json_schema_to_genai(v)
            for k, v in schema["properties"].items()
        }

    if "items" in schema:
        result["items"] = _convert_json_schema_to_genai(schema["items"])

    return result


def _parse_genai_response(result: dict[str, Any]) -> _ChatApiResponse:
    """Parse Gemini native generateContent response into _ChatApiResponse."""
    candidates = result.get("candidates", [])
    usage_meta = result.get("usageMetadata", {})

    choices = []
    for cand in candidates:
        content = cand.get("content", {})
        parts = content.get("parts", [])
        finish = cand.get("finishReason", "STOP")

        text_parts = []
        tool_calls = []

        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tc_id = f"call_{fc.get('name', '')}_{len(tool_calls)}"
                tool_calls.append(_ToolCall(
                    id=tc_id,
                    function=_Function(
                        name=fc.get("name", ""),
                        arguments=json.dumps(fc.get("args", {})),
                    ),
                    thought_signature=part.get("thoughtSignature"),
                ))

        finish_reason = "tool_calls" if tool_calls else "stop"
        if finish == "MAX_TOKENS":
            finish_reason = "length"

        choices.append(_Choice(
            message=_Message(
                content="\n".join(text_parts) if text_parts else None,
                tool_calls=tool_calls if tool_calls else None,
            ),
            finish_reason=finish_reason,
        ))

    return _ChatApiResponse(
        choices=choices,
        usage=_Usage(
            prompt_tokens=usage_meta.get("promptTokenCount", 0),
            completion_tokens=usage_meta.get("candidatesTokenCount", 0),
        ),
    )
