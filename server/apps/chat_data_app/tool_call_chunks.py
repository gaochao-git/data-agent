"""流式工具调用 chunk 的 id/name/index 索引。

主图与子图消费的是同一种 LangGraph chunk 形状：首个分片通常带
``id``/``name``，后续分片可能只带 ``index``（甚至什么都不带），参数正文
则分散在多个 ``args`` 分片里。索引职责只有三件：

1. 记住 ``id -> name``、``index -> id``、``index -> name``；
2. 按 ``id > index > fallback`` 的固定顺序裁决分片归属；
3. ``name`` 缺失时按 ``id`` / ``index`` 回填。

参数正文累积不在这里做：主图要保留权威参数快照，子图要按工具完成清理
缓冲，生命周期不同，继续由各自状态对象持有。
"""

from __future__ import annotations

from typing import Any


class ToolCallChunkIndex:
    """工具调用分片的身份索引，主图与子图共用同一套裁决规则。"""

    def __init__(self) -> None:
        self.names_by_id: dict[str, str] = {}
        self.ids_by_index: dict[int, str] = {}
        self.names_by_index: dict[int, str] = {}
        self.active_id: str | None = None

    def remember(self, chunk_id: str, chunk_name: str, chunk_index: Any) -> None:
        """登记一个分片携带的身份信息；缺 id/name 时按 index 暂存。"""
        if isinstance(chunk_index, int):
            if chunk_id:
                self.ids_by_index[chunk_index] = chunk_id
            if chunk_name:
                self.names_by_index[chunk_index] = chunk_name
        if chunk_id:
            self.active_id = chunk_id
            if chunk_name:
                self.names_by_id[chunk_id] = chunk_name

    def remember_name(self, tool_call_id: str, tool_name: str) -> None:
        """补登记「先按 index 拿到 name、后解析出 id」的迟到名称。"""
        if tool_call_id and tool_name:
            self.names_by_id[tool_call_id] = tool_name

    def resolve_id(
        self,
        chunk_id: str,
        chunk_index: Any,
        *,
        fallback: str | None = None,
    ) -> str | None:
        """按 ``id > index 映射 > fallback`` 解析分片归属。"""
        if chunk_id:
            return chunk_id
        if isinstance(chunk_index, int):
            mapped = self.ids_by_index.get(chunk_index)
            if mapped:
                return mapped
        return fallback

    def resolve_name(
        self,
        tool_call_id: str,
        *,
        chunk_name: str = "",
        chunk_index: Any = None,
    ) -> str:
        """按 ``本片 name > id 映射 > index 映射`` 回填工具名。"""
        if chunk_name:
            return chunk_name
        name = self.names_by_id.get(tool_call_id, "")
        if name:
            return name
        if isinstance(chunk_index, int):
            return self.names_by_index.get(chunk_index, "")
        return ""
