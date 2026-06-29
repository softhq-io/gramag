"""Azure OpenAI client helpers for Gramag runtime code."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from openai import BadRequestError, OpenAI

from config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_CHAT_DEPLOYMENT,
    AZURE_OPENAI_EMBED_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_VISION_DEPLOYMENT,
)


def _base_url() -> str:
    endpoint = AZURE_OPENAI_ENDPOINT.rstrip("/")
    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
    if endpoint.endswith("/openai/v1"):
        return f"{endpoint}/"
    return f"{endpoint}/openai/v1/"


def _client() -> OpenAI:
    if not AZURE_OPENAI_API_KEY:
        raise RuntimeError("AZURE_OPENAI_API_KEY is not set")
    return OpenAI(api_key=AZURE_OPENAI_API_KEY, base_url=_base_url())


def _chat_completion(
    *,
    messages: list[dict],
    model: str,
    temperature: float | None,
    max_tokens: int,
    response_format: dict | None = None,
) -> str:
    kwargs = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if response_format:
        kwargs["response_format"] = response_format

    for _ in range(3):
        try:
            response = _client().chat.completions.create(**kwargs)
            break
        except BadRequestError as exc:
            # Newer reasoning/chat models can reject sampling knobs. Retry with
            # the minimum stable request shape before surfacing provider errors.
            msg = str(exc).lower()
            if "temperature" in msg and "temperature" in kwargs:
                kwargs.pop("temperature", None)
                continue
            if "max_completion_tokens" in msg and "max_completion_tokens" in kwargs:
                kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                continue
            if "response_format" in msg and "response_format" in kwargs:
                kwargs.pop("response_format", None)
                continue
            raise
    else:
        raise RuntimeError("Azure OpenAI chat completion failed")

    content = response.choices[0].message.content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return content or ""


def chat(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float | None = 0.2,
    max_tokens: int = 2000,
    deployment: str | None = None,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _chat_completion(
        messages=messages,
        model=deployment or AZURE_OPENAI_CHAT_DEPLOYMENT,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def json_chat(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float | None = 0.1,
    max_tokens: int = 2000,
    deployment: str | None = None,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _chat_completion(
        messages=messages,
        model=deployment or AZURE_OPENAI_CHAT_DEPLOYMENT,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )


def image_content(path: str | Path, *, mime_type: str | None = None, detail: str = "high") -> dict:
    p = Path(path)
    mime = mime_type or mimetypes.guess_type(str(p))[0] or "image/png"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime};base64,{data}",
            "detail": detail,
        },
    }


def vision_chat(
    content: list[dict],
    *,
    system: str | None = None,
    temperature: float | None = 0.1,
    max_tokens: int = 2000,
    deployment: str | None = None,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    return _chat_completion(
        messages=messages,
        model=deployment or AZURE_OPENAI_VISION_DEPLOYMENT,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def vision_chat_messages(
    messages: list[dict],
    *,
    temperature: float | None = 0.1,
    max_tokens: int = 2000,
    deployment: str | None = None,
) -> str:
    return _chat_completion(
        messages=messages,
        model=deployment or AZURE_OPENAI_VISION_DEPLOYMENT,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def embed_one(text: str, *, input_type: str | None = None) -> list[float]:
    return embed_batch([text], input_type=input_type)[0]


def embed_batch(texts: list[str], *, input_type: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    clean_texts = [text if text and text.strip() else " " for text in texts]
    response = _client().embeddings.create(
        model=AZURE_OPENAI_EMBED_DEPLOYMENT,
        input=clean_texts,
        encoding_format="float",
    )
    return [list(item.embedding) for item in response.data]
