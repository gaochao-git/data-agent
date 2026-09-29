"""Service boundaries for system-owned workspace operations."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SemanticModelWorkspaceBackend(Protocol):
    async def write_internal_text_files(
        self,
        runtime: Any,
        *,
        entries: list[tuple[str, str]],
    ) -> list[dict[str, Any]]: ...

    async def delete_internal_text_files(
        self,
        runtime: Any,
        *,
        path: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ToolResultWorkspaceBackend(Protocol):
    async def write_tool_result(
        self,
        runtime: Any,
        *,
        file_path: str,
        content: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ArtifactWorkspaceBackend(Protocol):
    async def artifact_payload(
        self,
        runtime: Any,
        *,
        file_path: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OfficePreviewWorkspaceBackend(Protocol):
    async def render_office_html(
        self,
        runtime: Any,
        *,
        file_path: str,
        max_chars: int,
    ) -> dict[str, Any]: ...


def _require_capability(runtime: Any, protocol: type, operation: str) -> Any:
    backend = runtime.backend
    if not isinstance(backend, protocol):
        raise ValueError(f"当前工作区后端不支持{operation}")
    return backend


class SemanticModelWorkspaceService:
    async def write_files(
        self,
        runtime: Any,
        entries: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        normalized = [
            (str(path or "").strip(), str(content or "")) for path, content in entries if str(path or "").strip()
        ]
        if not normalized:
            return []
        backend = _require_capability(runtime, SemanticModelWorkspaceBackend, "语义模型物料化")
        return await backend.write_internal_text_files(runtime, entries=normalized)

    async def delete_files(
        self,
        runtime: Any,
        *,
        path: str,
    ) -> dict[str, Any]:
        """删除一个系统托管的语义模型文件（停用/删除模型的残留清理）。"""
        normalized_path = str(path or "").strip()
        if not normalized_path:
            raise ValueError("语义模型删除路径不能为空")
        backend = _require_capability(runtime, SemanticModelWorkspaceBackend, "语义模型残留清理")
        return await backend.delete_internal_text_files(runtime, path=normalized_path)


class WorkspaceToolResultService:
    async def write(self, runtime: Any, *, file_path: str, content: str) -> dict[str, Any]:
        backend = _require_capability(runtime, ToolResultWorkspaceBackend, "隐藏工具结果存储")
        return await backend.write_tool_result(runtime, file_path=file_path, content=content)


class WorkspaceArtifactService:
    async def load(self, runtime: Any, *, file_path: str) -> dict[str, Any]:
        backend = _require_capability(runtime, ArtifactWorkspaceBackend, "产物读取")
        return await backend.artifact_payload(runtime, file_path=file_path)


def stabilize_office_preview_html(html: str) -> str:
    """Hide Word's unscaled first paint until its pagination script is ready."""
    if not html or 'class="page"' not in html or "data-page=" not in html:
        return html

    bootstrap = """
<style id="chatdata-office-preview-bootstrap">
html.chatdata-office-preview-pending { visibility: hidden; }
html.chatdata-office-preview-ready { visibility: visible; }
</style>
<script>
(function () {
  var root = document.documentElement;
  root.classList.add('chatdata-office-preview-pending');
  var revealed = false;
  var timer;
  var fallbackTimer;

  function reveal() {
    if (revealed) return;
    var page = document.querySelector('.page[data-page]');
    if (!page || !/^scale\\(/.test(page.style.transform || '')) return;
    revealed = true;
    root.classList.remove('chatdata-office-preview-pending');
    root.classList.add('chatdata-office-preview-ready');
    if (timer) clearInterval(timer);
    if (fallbackTimer) clearTimeout(fallbackTimer);
  }

  timer = setInterval(reveal, 50);
  fallbackTimer = setTimeout(function () {
    if (revealed) return;
    revealed = true;
    root.classList.remove('chatdata-office-preview-pending');
    root.classList.add('chatdata-office-preview-ready');
    clearInterval(timer);
  }, 5000);
  reveal();
}());
</script>
"""
    marker = "</head>"
    if marker not in html:
        return html
    return html.replace(marker, bootstrap + marker, 1)


class WorkspaceOfficePreviewService:
    async def render_html(
        self,
        runtime: Any,
        *,
        file_path: str,
        max_chars: int,
    ) -> dict[str, Any]:
        backend = _require_capability(runtime, OfficePreviewWorkspaceBackend, "Office 预览")
        preview = await backend.render_office_html(
            runtime,
            file_path=file_path,
            max_chars=max_chars,
        )
        if preview.get("truncated"):
            return preview
        html = stabilize_office_preview_html(str(preview.get("html") or ""))
        if len(html) > max_chars:
            return {**preview, "html": "", "truncated": True}
        return {**preview, "html": html}


semantic_model_workspace_service = SemanticModelWorkspaceService()
workspace_tool_result_service = WorkspaceToolResultService()
workspace_artifact_service = WorkspaceArtifactService()
workspace_office_preview_service = WorkspaceOfficePreviewService()


__all__ = [
    "ArtifactWorkspaceBackend",
    "OfficePreviewWorkspaceBackend",
    "SemanticModelWorkspaceBackend",
    "ToolResultWorkspaceBackend",
    "semantic_model_workspace_service",
    "workspace_artifact_service",
    "workspace_office_preview_service",
    "workspace_tool_result_service",
    "stabilize_office_preview_html",
]
