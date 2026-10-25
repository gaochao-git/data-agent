"""Model tools for the experimental persistent Agent Team."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from utils.logger import get_logger

from .service import TeamService

logger = get_logger(__name__)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class SpawnTeammateArgs(BaseModel):
    name: str = Field(description="lower-kebab-case 唯一名称，例如 reviewer")
    description: str = Field(description="这个 teammate 的职责")
    prompt: str = Field(description="第一轮完整任务书")
    subagent_type: str = Field(default="general-purpose", description="子智能体类型")


class SendMessageArgs(BaseModel):
    target: str = Field(description="teammate 名称")
    message: str = Field(description="要继续发送给该 teammate 的消息")


class CreateTaskArgs(BaseModel):
    subject: str = Field(description="任务标题")
    description: str = Field(default="", description="任务详情")
    blocked_by: list[str] = Field(default_factory=list, description="依赖的 task id")
    write_scopes: list[str] = Field(default_factory=list, description="预期的写路径前缀，仅提示")


class UpdateTaskArgs(BaseModel):
    task_id: str = Field(description="任务 id，例如 task-1")
    action: Literal["claim", "complete", "release", "reopen", "delete", "edit", "assign", "set_dependencies"] = Field(
        description="任务操作"
    )
    expected_revision: int = Field(description="当前 revision，用于 CAS")
    owner: str | None = Field(default=None, description="assign 的成员名")
    subject: str | None = Field(default=None, description="edit 时的新标题")
    description: str | None = Field(default=None, description="edit 时的新详情")
    blocked_by: list[str] | None = Field(default=None, description="set_dependencies 的依赖")
    write_scopes: list[str] | None = Field(default=None, description="edit 时的新写范围")


def build_team_tools(service: TeamService) -> list[StructuredTool]:
    async def spawn_teammate(
        name: str,
        description: str,
        prompt: str,
        subagent_type: str = "general-purpose",
    ) -> str:
        result = await service.spawn(
            name=name,
            description=description,
            prompt=prompt,
            subagent_type=subagent_type,
        )
        return _dump(result)

    async def send_message(
        target: str,
        message: str,
    ) -> str:
        result = await service.send(target=target, message=message)
        return _dump(result)

    async def list_agents() -> str:
        return _dump(service.list_members())

    async def team_task_create(
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
        write_scopes: list[str] | None = None,
    ) -> str:
        return _dump(
            await service.create_task(
                subject=subject,
                description=description,
                blocked_by=blocked_by or [],
                write_scopes=write_scopes or [],
            )
        )

    async def team_task_list() -> str:
        return _dump(service.list_tasks())

    async def team_task_get(task_id: str) -> str:
        return _dump(service.get_task(task_id))

    async def team_task_update(
        task_id: str,
        action: str,
        expected_revision: int,
        owner: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        blocked_by: list[str] | None = None,
        write_scopes: list[str] | None = None,
    ) -> str:
        return _dump(
            await service.update_task(
                task_id=task_id,
                action=action,
                expected_revision=expected_revision,
                owner=owner,
                subject=subject,
                description=description,
                blocked_by=blocked_by,
                write_scopes=write_scopes,
            )
        )

    return [
        StructuredTool.from_function(
            name="spawn_teammate",
            coroutine=spawn_teammate,
            description="创建一个具名持久 teammate，并把第一轮任务发给它。它会保留自己的多轮上下文。",
            args_schema=SpawnTeammateArgs,
        ),
        StructuredTool.from_function(
            name="send_message",
            coroutine=send_message,
            description="继续给已有 teammate 发消息；它会在原上下文里继续执行并返回本轮结论。",
            args_schema=SendMessageArgs,
        ),
        StructuredTool.from_function(
            name="list_agents",
            coroutine=list_agents,
            description="列出当前 Team 的 teammate、状态和最近一条回复。",
        ),
        StructuredTool.from_function(
            name="team_task_create",
            coroutine=team_task_create,
            description="创建共享任务；blocked_by 填写前序 task id。",
            args_schema=CreateTaskArgs,
        ),
        StructuredTool.from_function(
            name="team_task_list",
            coroutine=team_task_list,
            description="列出共享任务板。",
        ),
        StructuredTool.from_function(
            name="team_task_get",
            coroutine=team_task_get,
            description="读取一个共享任务。",
        ),
        StructuredTool.from_function(
            name="team_task_update",
            coroutine=team_task_update,
            description="按 expected_revision CAS 更新共享任务。",
            args_schema=UpdateTaskArgs,
        ),
    ]
