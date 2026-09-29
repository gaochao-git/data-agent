"""MCP 工具检索目录：两级加载的第二级（目录 + 按需检索）。

插件/MCP server 数量增长后，把几十上百个工具 schema 全量挂进每次模型请求
会造成 token 风暴与工具选择噪声。参照业界做法（Anthropic Tool Search Tool、
LangChain tool-retrieval agent），把"枚举目录"与"请求挂载"分离：

- ``MCPCatalog``：对枚举结果建手写 BM25 索引（纯 Python、零新增依赖），
  检索域 = 工具名 + 描述 + 参数文档；指纹（内容 hash）未变化时不重建。
- ``MCPToolSearchService``：目录的会话内持有者，负责从 manager 同步枚举
  结果（TTL 缓存复用，不新增失效机制）并产出模型可读的检索结果 JSON。
- ``mcp_tool_search`` 内置工具：模型在检索模式下先检索、后按命中
  ``qualified_name`` 直接调用；发现结果由
  ``agent/middleware/mcp_tool_discovery.py`` 并入后续请求的工具列表。

当前规模（工具数少 / token 占用低于窗口阈值）时装配走全量挂载，本模块
不参与运行——行为与两级加载引入前完全一致。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from utils.logger import get_logger

from .manager import MCPToolInfo

logger = get_logger(__name__)

MCP_TOOL_SEARCH_TOOL_NAME = "mcp_tool_search"
MCP_TOOL_DESCRIPTION_WARN_CHARS = 500

# BM25 (Okapi) 参数：业界常规取值。
_BM25_K1 = 1.5
_BM25_B = 0.75
# 工具名是检索最强信号，token 重复两次等效加权。
_NAME_TOKEN_REPEAT = 2

_SEARCH_RESULT_LIMIT_DEFAULT = 5
_SEARCH_RESULT_LIMIT_MAX = 10
_RESULT_DESCRIPTION_MAX_CHARS = 200

# ASCII 词（含下划线/数字）整词收录；CJK 按单字收录——中文短查询下
# 字级 BM25 的区分度对工具名/描述这类短文本已经足够，避免引入分词依赖。
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_PATTERN.findall(str(text or ""))]


def _param_docs(input_schema: dict[str, Any]) -> str:
    """把工具入参 schema 压成检索用文档：参数名 + 参数描述。"""
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else None
    if not isinstance(properties, dict):
        return ""
    parts: list[str] = []
    for prop_name, prop in properties.items():
        parts.append(str(prop_name))
        if isinstance(prop, dict):
            description = str(prop.get("description") or "").strip()
            if description:
                parts.append(description)
    return " ".join(parts)


@dataclass(frozen=True)
class ToolCatalogEntry:
    """目录里的一个可检索工具条目。"""

    qualified_name: str
    server_id: str
    name: str
    description: str
    param_docs: str
    label: str = ""  # 用户可见友好名（tool_labels 配置），参与检索域并随摘要下发

    def summary(self) -> dict[str, str]:
        """检索结果里给模型看的单条摘要。"""
        summary = {
            "name": self.qualified_name,
            "server_id": self.server_id,
            "description": self.description[:_RESULT_DESCRIPTION_MAX_CHARS],
        }
        if self.label:
            summary["display_name"] = self.label
        return summary


def _catalog_fingerprint(tools_info: list[MCPToolInfo]) -> str:
    """工具清单内容指纹：qualified_name + 描述 + 入参 schema + 展示名任一变化即失效。"""
    digest = hashlib.sha1()
    for info in tools_info:
        digest.update(info.qualified_name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(info.description.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(json.dumps(info.input_schema, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(info.label.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class _BM25Index:
    """手写 Okapi BM25 倒排索引（小语料场景，纯 Python 实现足够）。"""

    def __init__(self, docs: list[list[str]]):
        self._doc_count = len(docs)
        self._doc_len = [len(doc) for doc in docs]
        self._avg_len = (sum(self._doc_len) / self._doc_count) if docs else 0.0
        self._doc_freq: dict[str, int] = {}
        self._tf: list[dict[str, int]] = []
        for doc in docs:
            counts: dict[str, int] = {}
            for token in doc:
                counts[token] = counts.get(token, 0) + 1
            self._tf.append(counts)
            for token in counts:
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1

    def search(self, query: str) -> list[float]:
        """返回每篇文档的 BM25 得分（与构造时文档顺序一一对应）。"""
        query_tokens = _tokenize(query)
        scores = [0.0] * self._doc_count
        if not query_tokens or not self._doc_count:
            return scores
        for token in set(query_tokens):
            df = self._doc_freq.get(token)
            if not df:
                continue
            idf = math.log(1.0 + (self._doc_count - df + 0.5) / (df + 0.5))
            for doc_index, counts in enumerate(self._tf):
                tf = counts.get(token)
                if not tf:
                    continue
                doc_len = self._doc_len[doc_index]
                norm = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * (doc_len / self._avg_len if self._avg_len else 0.0))
                scores[doc_index] += idf * tf * (_BM25_K1 + 1.0) / (tf + norm)
        return scores


class MCPCatalog:
    """MCP 工具检索目录：BM25 索引 + 指纹失效 + server 清单描述。"""

    def __init__(self) -> None:
        self._entries: list[ToolCatalogEntry] = []
        self._entries_by_name: dict[str, ToolCatalogEntry] = {}
        self._index: _BM25Index | None = None
        self._fingerprint = ""

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def entries(self) -> list[ToolCatalogEntry]:
        return list(self._entries)

    def entry(self, qualified_name: str) -> ToolCatalogEntry | None:
        return self._entries_by_name.get(qualified_name)

    def sync(self, tools_info: list[MCPToolInfo]) -> bool:
        """按枚举结果同步目录；指纹变化才重建索引。返回是否发生重建。"""
        fingerprint = _catalog_fingerprint(tools_info)
        if fingerprint == self._fingerprint and self._index is not None:
            return False
        entries: list[ToolCatalogEntry] = []
        docs: list[list[str]] = []
        for info in tools_info:
            entry = ToolCatalogEntry(
                qualified_name=info.qualified_name,
                server_id=info.server_id,
                name=info.name,
                description=info.description or info.qualified_name,
                param_docs=_param_docs(info.input_schema),
                label=info.label,
            )
            entries.append(entry)
            name_tokens = _tokenize(entry.qualified_name) * _NAME_TOKEN_REPEAT
            docs.append(
                name_tokens + _tokenize(entry.label) + _tokenize(entry.description) + _tokenize(entry.param_docs)
            )
        self._entries = entries
        self._entries_by_name = {entry.qualified_name: entry for entry in entries}
        self._index = _BM25Index(docs)
        self._fingerprint = fingerprint
        return True

    def search(
        self,
        query: str,
        *,
        limit: int = _SEARCH_RESULT_LIMIT_DEFAULT,
        excluded_names: frozenset[str] | set[str] = frozenset(),
    ) -> list[ToolCatalogEntry]:
        """按 BM25 得分返回 top-N 条目；query 为空或无命中返回空列表。"""
        if self._index is None or not str(query or "").strip():
            return []
        scores = self._index.search(query)
        ranked = sorted(
            (
                (score, entry)
                for score, entry in zip(scores, self._entries, strict=True)
                if score > 0.0 and entry.qualified_name not in excluded_names
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [entry for _, entry in ranked[: max(1, min(int(limit), _SEARCH_RESULT_LIMIT_MAX))]]

    def describe_servers(self) -> list[dict[str, Any]]:
        """按 server 汇总工具数量，供检索无命中时给模型提示可用范围。"""
        counts: dict[str, int] = {}
        for entry in self._entries:
            counts[entry.server_id] = counts.get(entry.server_id, 0) + 1
        return [{"server_id": server_id, "tool_count": count} for server_id, count in sorted(counts.items())]


class MCPToolSearchService:
    """检索目录的会话内持有者：从 manager 同步枚举结果并产出检索结果文本。

    manager 的 TTL 缓存与插件变更 reset 机制直接复用，本服务不新增缓存。
    """

    def __init__(self, manager):
        self._manager = manager
        self._catalog = MCPCatalog()

    @property
    def catalog(self) -> MCPCatalog:
        return self._catalog

    async def ensure_catalog(self) -> MCPCatalog:
        """取最新枚举（TTL 缓存内零开销）并同步目录。"""
        tools_info = await self._manager.list_tools_async()
        self._catalog.sync(tools_info)
        return self._catalog

    async def search_text(
        self,
        query: str,
        *,
        server_id: str | None = None,
        limit: int = _SEARCH_RESULT_LIMIT_DEFAULT,
        excluded_names: frozenset[str] | set[str] = frozenset(),
    ) -> str:
        """执行检索并返回模型可读的 JSON 结果（同时是发现中间件的解析契约）。"""
        catalog = await self.ensure_catalog()
        candidates = catalog.search(query, limit=limit, excluded_names=excluded_names)
        if server_id:
            candidates = [entry for entry in candidates if entry.server_id == server_id]
        if candidates:
            return json.dumps(
                {
                    "count": len(candidates),
                    "query": query,
                    "results": [entry.summary() for entry in candidates],
                    "guidance": "按 results[].name 直接调用对应工具；都不合适时换关键词再次检索。",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "count": 0,
                "query": query,
                "servers": catalog.describe_servers(),
                "guidance": "没有命中工具：更换更具体的关键词（功能名/操作对象）再次检索，或直接向用户说明当前没有可用工具。",
            },
            ensure_ascii=False,
        )


def create_mcp_tool_search_tool(service: MCPToolSearchService) -> StructuredTool:
    """构建 ``mcp_tool_search`` 内置工具（仅检索模式装配）。"""

    async def _search(
        query: str,
        server_id: str | None = None,
        limit: int = _SEARCH_RESULT_LIMIT_DEFAULT,
        intent: str = "",
    ) -> str:
        return await service.search_text(query, server_id=server_id, limit=limit)

    args_model = create_model(
        f"{MCP_TOOL_SEARCH_TOOL_NAME}_args",
        query=(
            str,
            Field(description="检索关键词：用功能名/操作对象描述想做的事，如 'nginx 日志'、'数据库慢查询'。"),
        ),
        server_id=(
            str | None,
            Field(default=None, description="可选。只在该 MCP server 内检索（server_id 见服务器清单）。"),
        ),
        limit=(
            int,
            Field(
                default=_SEARCH_RESULT_LIMIT_DEFAULT, description=f"最多返回条数，默认 {_SEARCH_RESULT_LIMIT_DEFAULT}。"
            ),
        ),
        intent=(
            str,
            Field(default="", description="一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。"),
        ),
    )
    return StructuredTool(
        name=MCP_TOOL_SEARCH_TOOL_NAME,
        description=(
            "按需检索 MCP 插件工具目录。当前会话可用工具较多、未全部挂载时，"
            "先用本工具按关键词检索（返回工具名与说明），再按 name 直接调用命中的工具。"
        ),
        args_schema=args_model,
        coroutine=_search,
    )


def warn_long_mcp_descriptions(tools: list) -> None:
    """装配期告警：MCP 工具描述超长会放大 schema token 成本，提示插件作者精简。"""
    for tool in tools:
        description = str(getattr(tool, "description", "") or "")
        if len(description) > MCP_TOOL_DESCRIPTION_WARN_CHARS:
            logger.warning(
                "MCP 工具描述超过 %d 字符，建议插件作者精简以降低 token 成本: tool=%s length=%d",
                MCP_TOOL_DESCRIPTION_WARN_CHARS,
                getattr(tool, "name", "") or "",
                len(description),
            )


__all__ = [
    "MCP_TOOL_DESCRIPTION_WARN_CHARS",
    "MCP_TOOL_SEARCH_TOOL_NAME",
    "MCPCatalog",
    "MCPToolSearchService",
    "ToolCatalogEntry",
    "create_mcp_tool_search_tool",
    "warn_long_mcp_descriptions",
]
