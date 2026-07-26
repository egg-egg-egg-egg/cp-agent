"""
LLM 协议适配层：Anthropic / OpenAI-compatible 双协议、指数退避重试、纯文本调用。
供 agent 循环与查重裁判共用，不依赖 agent 模块。
"""
import json
import logging
from typing import Optional

_logger = logging.getLogger("cp_agent.llm")


# Convert to OpenAI tools format (used by OpenAI-compatible providers)
def to_openai_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-style tool defs to OpenAI tools format."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            }
        })
    return result


def classify_llm_error(e: Exception) -> tuple[bool, float | None]:
    """
    Decide whether an SDK exception is retryable and extract a server-suggested
    wait. Retry: rate limits, connection/timeout errors, 5xx. Don't retry:
    auth (401/403) and bad requests (4xx other than 429).
    """
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    retryable = (
        name in {"RateLimitError", "APIConnectionError", "APITimeoutError",
                 "InternalServerError"}
        or (isinstance(status, int) and (status >= 500 or status == 429))
    )
    if not retryable:
        return False, None
    wait = None
    headers = getattr(getattr(e, "response", None), "headers", None)
    if headers:
        try:
            wait = float(headers.get("retry-after"))
        except (TypeError, ValueError):
            pass
    return True, wait


def retry_llm_call(fn, max_attempts: int = 5):
    """Call fn() with exponential backoff on retryable LLM API errors."""
    import logging
    import random
    import time as _time

    logger = logging.getLogger("cp_agent.llm")
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            retryable, server_wait = classify_llm_error(e)
            if not retryable or attempt == max_attempts:
                raise
            wait = server_wait if server_wait is not None else \
                min(2 * 2 ** (attempt - 1), 60) + random.uniform(0, 1)
            print(f"  ⏳ LLM 调用失败（{type(e).__name__}），{wait:.1f}s 后重试 ({attempt}/{max_attempts - 1})")
            logger.warning("LLM call failed (%s), retrying in %.1fs (attempt %d)",
                           type(e).__name__, wait, attempt)
            _time.sleep(wait)


def _call_anthropic_with_tools(messages: list[dict], system: str, model: str,
                                api_key: str, max_tokens: int, tools: list[dict]) -> dict:
    """
    Call Anthropic API with tools.
    Returns {"stop_reason": "tool_use"|"end_turn", "content": [...], "usage": {...}}
    """
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = retry_llm_call(lambda: client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        tools=tools,
    ))
    return {
        "stop_reason": resp.stop_reason,
        "content": [{"type": b.type, **({"id": b.id, "name": b.name, "input": b.input} if b.type == "tool_use" else {"text": b.text})} for b in resp.content],
        "usage": {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
    }


def _call_openai_with_tools(messages: list[dict], system: str, model: str,
                             base_url: str, api_key: str, max_tokens: int,
                             tools: list[dict]) -> dict:
    """
    Call OpenAI-compatible API with tools.
    Returns {"stop_reason": "tool_calls"|"stop", "content": [...], "usage": {...}}
    """
    from openai import OpenAI
    client = OpenAI(base_url=base_url, api_key=api_key)

    # Build messages with system
    api_messages = [{"role": "system", "content": system}] + messages

    resp = retry_llm_call(lambda: client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=api_messages,
        tools=to_openai_tools(tools),
    ))

    choice = resp.choices[0]
    msg = choice.message

    # Convert to unified format
    content = []
    if msg.content:
        content.append({"type": "text", "text": msg.content})
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": args,
            })

    stop_reason = "tool_calls" if msg.tool_calls else "stop"
    usage = {}
    if resp.usage:
        usage = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens}

    return {
        "stop_reason": stop_reason,
        "content": content,
        "usage": usage,
    }


def call_llm_with_tools(messages: list[dict], system: str, provider: str,
                        tools: list[dict],
                        model: Optional[str] = None, base_url: Optional[str] = None,
                        api_key: Optional[str] = None, max_tokens: int = 16000) -> dict:
    """
    Call LLM API with tool support. Routes to Anthropic or OpenAI based on protocol.
    Returns unified format: {stop_reason, content, usage}
    """
    from config import get_provider, resolve_api_key

    protocol, cfg = get_provider(provider)
    resolved_model = model or cfg["default_model"]
    resolved_base_url = base_url or cfg["base_url"]
    resolved_api_key = resolve_api_key(cfg, cli_key=api_key, provider_name=provider or "")

    if protocol == "anthropic":
        return _call_anthropic_with_tools(messages, system, resolved_model,
                                          resolved_api_key, max_tokens, tools)
    else:
        return _call_openai_with_tools(messages, system, resolved_model,
                                       resolved_base_url, resolved_api_key, max_tokens, tools)



def call_llm_text(system: str, user: str, provider: Optional[str] = None,
                   model: Optional[str] = None, base_url: Optional[str] = None,
                   api_key: Optional[str] = None, max_tokens: int = 2000) -> str:
    """Plain text LLM call (no tools) using the same provider routing/retry."""
    from config import get_provider, resolve_api_key

    protocol, cfg = get_provider(provider)
    resolved_model = model or cfg["default_model"]
    resolved_base_url = base_url or cfg["base_url"]
    resolved_api_key = resolve_api_key(cfg, cli_key=api_key, provider_name=provider or "")

    if protocol == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=resolved_api_key)
        resp = retry_llm_call(lambda: client.messages.create(
            model=resolved_model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}]))
        return "".join(b.text for b in resp.content if b.type == "text")
    else:
        from openai import OpenAI
        client = OpenAI(base_url=resolved_base_url, api_key=resolved_api_key)
        resp = retry_llm_call(lambda: client.chat.completions.create(
            model=resolved_model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}]))
        return resp.choices[0].message.content or ""


