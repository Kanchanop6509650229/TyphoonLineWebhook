import os
import logging
from typing import Iterable, AsyncIterable, List, Dict, Any, Optional

from openai import OpenAI, AsyncOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    APIStatusError,
)


# Defaults per xAI docs: use OpenAI client with base_url to xAI
_DEFAULT_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_MODEL = os.getenv("XAI_MODEL", "grok-4")


def _get_sync_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> OpenAI:
    return OpenAI(
        api_key=api_key or os.getenv("XAI_API_KEY"),
        base_url=base_url or os.getenv("XAI_BASE_URL", _DEFAULT_BASE_URL),
    )


def _get_async_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key or os.getenv("XAI_API_KEY"),
        base_url=base_url or os.getenv("XAI_BASE_URL", _DEFAULT_BASE_URL),
    )


def send_chat(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Send a synchronous chat completion and return the text content.

    Parameters mirror OpenAI Chat Completions per xAI docs.
    """
    client = _get_sync_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    try:
        resp = client.chat.completions.create(**params)
        return resp.choices[0].message.content
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as e:
        logging.error(f"xAI Grok chat error: {e}")
        raise


def stream_chat(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Iterable[str]:
    """Synchronous streaming chat completion yielding text chunks.

    Uses Chat Completions streaming compatible with OpenAI client.
    """
    client = _get_sync_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
        "stream": True,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    try:
        for chunk in client.chat.completions.create(**params):
            if not chunk.choices:
                continue
            delta = getattr(chunk.choices[0], "delta", None)
            if delta and getattr(delta, "content", None):
                yield delta.content
            elif getattr(chunk.choices[0], "message", None) and chunk.choices[0].message.content:
                # Some servers may send message blocks
                yield chunk.choices[0].message.content
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as e:
        logging.error(f"xAI Grok streaming error: {e}")
        raise


async def astream_chat(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Async chat completion returning the text content."""
    client = _get_async_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    try:
        resp = await client.chat.completions.create(**params)
        # Do not close client explicitly; connection pool is reused by SDK.
        return resp.choices[0].message.content
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as e:
        logging.error(f"xAI Grok async chat error: {e}")
        raise


async def astream_chat_iter(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> AsyncIterable[str]:
    """Async streaming generator yielding text chunks."""
    client = _get_async_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
        "stream": True,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    try:
        stream = await client.chat.completions.create(**params)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = getattr(chunk.choices[0], "delta", None)
            if delta and getattr(delta, "content", None):
                yield delta.content
            elif getattr(chunk.choices[0], "message", None) and chunk.choices[0].message.content:
                yield chunk.choices[0].message.content
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as e:
        logging.error(f"xAI Grok async streaming error: {e}")
        raise

