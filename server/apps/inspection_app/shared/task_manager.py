"""
任务状态管理器
统一处理任务列表的状态更新和进度推送

支持两种模式：
1. 流式推送（stream_writer）- 实时显示给前端
2. 状态持久化（返回 dict 供节点写入 state）- 存入 checkpoint
"""
from typing import Optional, Callable
from langgraph.config import get_stream_writer
from utils.logger import get_logger

logger = get_logger(__name__)


class TaskManager:
    """任务管理器 - 自动管理任务状态和进度推送"""

    def __init__(self, tasks: list[dict]):
        """
        初始化任务管理器

        Args:
            tasks: 任务列表，格式: [{"id": "check", "title": "检查所需文件"}, ...]
        """
        self.task_definitions = tasks
        self._writer: Optional[Callable] = None
        self._current_tasks: list[dict] = []  # 缓存当前任务状态

    @property
    def writer(self):
        """延迟获取 stream writer（仅在节点执行时有效）"""
        if self._writer is None:
            self._writer = get_stream_writer()
        return self._writer

    def _get_task_index(self, task_id: str) -> int:
        """获取任务索引"""
        for idx, task in enumerate(self.task_definitions):
            if task['id'] == task_id:
                return idx
        raise ValueError(f"Task ID not found: {task_id}")

    def _build_task_list(self, current_task_id: str, status: str) -> list[dict]:
        """
        构建任务列表（设置当前任务状态，之前的为completed，之后的为pending）

        Args:
            current_task_id: 当前任务ID
            status: 当前任务状态 ('in_progress' 或 'completed')
        """
        current_index = self._get_task_index(current_task_id)

        result = []
        for idx, task in enumerate(self.task_definitions):
            if idx < current_index:
                task_status = 'completed'
            elif idx == current_index:
                task_status = status
            else:
                task_status = 'pending'

            result.append({
                "id": task['id'],
                "title": task['title'],
                "status": task_status
            })

        return result

    def set_in_progress(self, task_id: str) -> dict:
        """
        设置任务为进行中

        Returns:
            dict: 用于节点 return 的状态更新 {"task_progress": [...]}
        """
        logger.debug(f"Task [{task_id}] -> in_progress")
        tasks = self._build_task_list(task_id, 'in_progress')
        self._current_tasks = tasks
        self._send_update(tasks)
        return {"task_progress": tasks}

    def set_completed(self, task_id: str) -> dict:
        """
        设置任务为已完成

        Returns:
            dict: 用于节点 return 的状态更新 {"task_progress": [...]}
        """
        logger.debug(f"Task [{task_id}] -> completed")
        tasks = self._build_task_list(task_id, 'completed')
        self._current_tasks = tasks
        self._send_update(tasks)
        return {"task_progress": tasks}

    def get_current_tasks(self) -> list[dict]:
        """获取当前任务状态列表"""
        return self._current_tasks

    def get_state_update(self) -> dict:
        """获取用于节点 return 的状态更新"""
        return {"task_progress": self._current_tasks}

    def _send_update(self, tasks: list[dict]):
        """发送任务状态更新（使用 NonStandardContentBlock 格式）"""
        self.writer({
            "type": "non_standard",
            "value": {
                "type": "task_progress",
                "data": tasks
            }
        })
