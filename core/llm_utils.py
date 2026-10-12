"""LLM 响应兼容工具。"""  # 模块说明：统一从各兼容接口的响应里提取文本
from __future__ import annotations  # 延迟求值类型注解（兼容旧版 Python）

from typing import Any, Iterable  # 类型注解


def extract_text(response: Any) -> str:
    """
    尽量从不同兼容接口的响应里提取文本。

    兼容多种响应形态：
    1. response.content 直接是字符串；
    2. content 是 block 列表（对象或字典，支持 text/content/output_text/answer 字段）；
    3. 兜底走 model_dump() 递归查找文本。

    参数：
        response: 模型响应对象（结构随 SDK 版本/兼容层不同）

    返回：
        提取出的纯文本（找不到时返回空串）
    """
    # 取响应的 content 字段（没有则返回 None）
    content = getattr(response, "content", None)
    # content 本身就是字符串时直接返回
    if isinstance(content, str):
        return content.strip()  # 去除首尾空白

    # 收集文本片段
    parts: list[str] = []
    # 遍历 content 中的每个 block
    for block in _iter_blocks(content):
        # 优先按对象属性取 text（支持 block 是对象的情况）
        text = getattr(block, "text", None)
        # 对象 text 属性是有效的非空字符串时收录
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())  # 追加文本片段
            continue  # 本 block 已处理，跳到下一个
        # block 是字典时按常见键名逐个取文本
        if isinstance(block, dict):
            # 遍历各候选键（不同 SDK 用不同字段名）
            for key in ("text", "content", "output_text", "answer"):
                value = block.get(key)  # 取该键对应的值
                # 值是有效的非空字符串时收录
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())  # 追加文本片段
                    break  # 命中一个键即可，跳出内层循环

    # 收集到片段时拼接返回
    if parts:
        return "\n".join(parts).strip()  # 用换行拼接所有片段

    # 以上都没取到：尝试调用 model_dump() 兜底
    dump = getattr(response, "model_dump", None)
    # 存在可调用的 model_dump 方法时执行
    if callable(dump):
        try:
            # 调 model_dump 得到字典
            data = dump()
            # 递归查找文本并去除首尾空白
            return _find_text(data).strip()
        except Exception:
            # 兜底过程异常时返回空串
            return ""

    # 完全找不到文本返回空串
    return ""


def _iter_blocks(content: Any) -> Iterable[Any]:
    """
    把响应 content 安全转成可迭代的 block 序列。

    参数：
        content: 响应 content 字段（可能是 None、列表或其它可迭代对象）

    返回：
        block 的迭代对象
    """
    # content 为空时返回空列表
    if content is None:
        return []
    # content 已是列表时原样返回
    if isinstance(content, list):
        return content  # 直接返回原列表
    # 尝试把其它可迭代对象转为列表
    try:
        return list(content)  # 转为列表
    except TypeError:
        # 不可迭代时包装成单元素列表
        return [content]


def _find_text(value: Any) -> str:
    """
    递归地在嵌套结构里查找第一条文本。

    优先查 text/content/output_text/answer 四个键，其次递归字典值与列表元素。

    参数：
        value: 任意嵌套结构（字符串 / 字典 / 列表）

    返回：
        找到的文本，找不到返回空串
    """
    # 本身就是字符串时直接返回
    if isinstance(value, str):
        return value
    # 是字典时先按候选键查找
    if isinstance(value, dict):
        # 遍历各候选键（不同 SDK 用不同字段名）
        for key in ("text", "content", "output_text", "answer"):
            candidate = value.get(key)  # 取该键对应的值
            # 值是有效的非空字符串时直接返回
            if isinstance(candidate, str) and candidate.strip():
                return candidate  # 命中候选键
        # 候选键都没命中：递归查找所有字典值
        for candidate in value.values():
            found = _find_text(candidate)  # 深度优先递归
            # 找到文本即返回
            if found:
                return found
    # 是列表时逐个递归查找
    if isinstance(value, list):
        # 遍历列表元素
        for item in value:
            found = _find_text(item)  # 递归查找元素
            # 找到文本即返回
            if found:
                return found
    # 全部未命中返回空串
    return ""