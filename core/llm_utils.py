"""LLM 响应兼容工具。"""
from __future__ import annotations

from typing import Any, Iterable


def extract_text(response: Any) -> str:
    """尽量从不同兼容接口的响应里提取文本。"""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()

    parts: list[str] = []
    for block in _iter_blocks(content):
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
            continue
        if isinstance(block, dict):
            for key in ("text", "content", "output_text", "answer"):
                value = block.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
                    break

    if parts:
        return "\n".join(parts).strip()

    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            data = dump()
            return _find_text(data).strip()
        except Exception:
            return ""

    return ""


def _iter_blocks(content: Any) -> Iterable[Any]:
    if content is None:
        return []
    if isinstance(content, list):
        return content
    try:
        return list(content)
    except TypeError:
        return [content]


def _find_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "output_text", "answer"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        for candidate in value.values():
            found = _find_text(candidate)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_text(item)
            if found:
                return found
    return ""
