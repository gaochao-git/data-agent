"""
任务数据结构定义
用于在消息的 additional_kwargs 中传递任务元数据
"""
import json
import os
from typing import Optional, Literal, Type, TypeVar
from docx.shared import Pt
from docx.oxml.ns import qn
from dataclasses import dataclass, asdict
from pydantic import BaseModel
from langchain_core.messages import SystemMessage
from dotenv import load_dotenv
from utils.exceptions import BusinessException
from utils.storage_paths import VIOLATION_BASE_DIR
from utils.logger import get_logger
from utils.lang_util.llm import get_llm
# from ..agent.states import SplitResult  # ⚠️ 已删除，旧的事实认定分析功能废弃

# 加载环境变量
load_dotenv()

# 初始化日志
logger = get_logger(__name__)

# 任务类型定义（与 schema.py 保持一致）
TaskType = Literal["qa", "violation_analysis", "report"]


@dataclass
class Task:
    """
    任务元数据

    用于标识和管理不同类型的用户请求
    存储在 HumanMessage.additional_kwargs["task"] 中
    """
    task_type: TaskType

    def to_dict(self) -> dict:
        """
        转换为字典（用于存入 additional_kwargs）

        Returns:
            dict: 任务的字典表示
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """
        从字典创建任务对象（用于从 additional_kwargs 解析）

        Args:
            data: 任务字典数据

        Returns:
            Task: 任务对象
        """
        return cls(
            task_type=data.get("task_type", "qa")
        )

    def __repr__(self) -> str:
        """字符串表示"""
        return f"Task(type={self.task_type})"


# 泛型类型变量
T = TypeVar('T', bound=BaseModel)


def get_chat_model(
    model: Optional[str] = None,
    temperature: float = 0,
    streaming: bool = False,
    max_tokens: Optional[int] = None,
    timeout: int = 120
):
    """
    获取统一配置的聊天模型（向后兼容的包装函数）

    Args:
        model: 模型名称（默认从环境变量 OPENAI_ANALYSIS_MODEL 读取）
        temperature: 温度参数（默认 0）
        streaming: 是否启用流式输出（默认 False）
        max_tokens: 最大输出 token 数（可选）
        timeout: 超时时间（秒，默认 120）

    Returns:
        初始化好的聊天模型实例
    """
    return get_llm(
        model=model,
        streaming=streaming,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout
    )


def extract_json_with_llm(
    prompt: str,
    response_model: Type[T],
    llm_model: Optional[str] = None,
    temperature: float = 0,
    max_tokens: int = 8192,
    timeout: int = 120
) -> T:
    """
    使用大模型提取结构化 JSON 数据的通用工具

    替代 with_structured_output()，避免 LangGraph 流式模式下的重试问题

    Args:
        prompt: 发送给 LLM 的提示词
        response_model: Pydantic 模型类，用于验证和解析 JSON
        llm_model: LLM 模型名称（默认从环境变量读取）
        temperature: 温度参数（默认 0）
        max_tokens: 最大输出 token 数（默认 8192）
        timeout: 超时时间（秒，默认 120）

    Returns:
        response_model 类型的实例

    Raises:
        ValueError: JSON 解析失败或验证失败
        Exception: LLM 调用失败

    Example:
        >>> class ViolationItem(BaseModel):
        ...     violation_id: str
        ...     original_fact: str

        >>> result = extract_json_with_llm(
        ...     prompt="提取违法行为...",
        ...     response_model=ViolationItem
        ... )
    """
    # 使用统一的 get_chat_model 函数
    llm = get_chat_model(
        model=llm_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout
    )

    logger.debug(f"调用 LLM 提取 JSON，模型: {llm_model}, max_tokens: {max_tokens}")

    try:
        # 调用 LLM
        raw_result = llm.invoke([SystemMessage(content=prompt)])

        # 提取 JSON（处理可能的 markdown 包装）
        content = raw_result.content.strip()

        # 移除 markdown 代码块标记
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]

        if content.endswith("```"):
            content = content[:-3]

        content = content.strip()

        logger.debug(f"LLM 返回内容长度: {len(content)} 字符")

        # 解析 JSON
        data = json.loads(content)

        # 使用 Pydantic 模型验证和解析
        result = response_model.model_validate(data)

        logger.info(f"成功提取并验证 JSON，类型: {response_model.__name__}")

        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败: {str(e)}, 内容: {content[:200]}...")
        raise ValueError(f"LLM 返回的内容不是有效的 JSON: {str(e)}")

    except Exception as e:
        logger.error(f"LLM 调用或数据验证失败: {str(e)}", exc_info=True)
        raise


# ⚠️ 已废弃：旧的事实认定分析功能不再使用
# def parse_file_groups_to_violations(file_groups: list[dict]) -> SplitResult:
#     """从文件分组列表构造违法行为列表（已废弃）"""
#     pass


def set_chinese_font(run, font_name='仿宋_GB2312', font_size=16):
    """
    设置中文字体（同时设置西文和中文字体）

    Args:
        run: docx.text.run.Run 对象
        font_name: 字体名称，默认为仿宋_GB2312
        font_size: 字体大小（磅），默认为16
    """

    run.font.name = font_name
    run.font.size = Pt(font_size)
    # 设置中文字体
    run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)


# ============ 文件路径管理 ============

def get_violation_thread_dir(thread_id: str) -> str:
    """获取执法检查会话的根目录（thread_id 为 UUID 格式）"""
    return os.path.join(VIOLATION_BASE_DIR, thread_id)


def get_violation_uploads_dir(thread_id: str) -> str:
    """获取执法检查会话的上传文件目录"""
    return os.path.join(get_violation_thread_dir(thread_id), "uploads")


def get_violation_generated_dir(thread_id: str) -> str:
    """获取执法检查会话的生成文件目录"""
    return os.path.join(get_violation_thread_dir(thread_id), "generated")


def get_violation_upload_path(thread_id: str, filename: str) -> str:
    """获取上传文件的完整路径，自动创建目录"""
    uploads_dir = get_violation_uploads_dir(thread_id)
    os.makedirs(uploads_dir, exist_ok=True)
    return os.path.join(uploads_dir, filename)


def get_violation_generated_path(thread_id: str, filename: str) -> str:
    """获取生成文件的完整路径，自动创建目录"""
    generated_dir = get_violation_generated_dir(thread_id)
    os.makedirs(generated_dir, exist_ok=True)
    return os.path.join(generated_dir, filename)


# ============ 消息内容块工厂函数 ============

def create_text_block(text: str) -> dict:
    """创建文本块"""
    return {"type": "text", "text": text}


def create_file_block(file_id: str, mime_type: str, url: str) -> dict:
    """创建文件块（符合官方 FileContentBlock 规范）"""
    return {
        "type": "file",
        "file_id": file_id,
        "mime_type": mime_type,
        "url": url
    }


def create_non_standard_block(
    custom_type: str,
    data,
    block_id: Optional[str] = None,
    index: Optional[int] = None
) -> dict:
    """创建非标准内容块（用于自定义类型）"""
    block = {
        "type": "non_standard",
        "value": {
            "type": custom_type,
            "data": data
        }
    }
    if block_id is not None:
        block["id"] = block_id
    if index is not None:
        block["index"] = index
    return block


def create_task_progress_block(tasks: list) -> dict:
    """创建任务进度块"""
    return create_non_standard_block("task_progress", tasks)


def create_analysis_data_block(data: dict) -> dict:
    """创建分析数据块"""
    return create_non_standard_block("analysis_data", data)


def create_excel_data_block(data) -> dict:
    """创建 Excel 预览数据块"""
    return create_non_standard_block("excel_data", data)


def create_file_title_block(title: str) -> dict:
    """创建文件标题块"""
    return create_non_standard_block("file_title", title)


def create_report_source_data_block(data: dict) -> dict:
    """创建报告源数据块（用于编辑后重新生成）"""
    return create_non_standard_block("report_source_data", data)


def create_progress_message(tasks: list, text: str = "") -> "AIMessage":
    """
    创建包含任务进度的 AIMessage

    用于在节点执行完成后保存进度到 checkpoint，
    这样刷新页面后可以恢复任务进度显示。

    Args:
        tasks: 任务列表，每个任务包含 id, title, status
        text: 可选的文本内容

    Returns:
        AIMessage: 包含任务进度的消息
    """
    from langchain_core.messages import AIMessage

    content_blocks = []
    if text:
        content_blocks.append(create_text_block(text))
    content_blocks.append(create_task_progress_block(tasks))

    return AIMessage(content=content_blocks)
