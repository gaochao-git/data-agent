"""
执法检查 API 数据模型定义

包含：
- Pydantic 模型：API 请求和响应模型
- TypedDict：LangChain 消息内容块类型定义
"""

from typing import Optional, Literal, TypedDict, NotRequired, Any
from pydantic import BaseModel, Field


# ============ LangChain 消息内容块类型定义 ============
# 符合官方规范：https://reference.langchain.com/python/langchain/messages/

class TextContentBlock(TypedDict):
    """文本内容块（标准类型）"""
    type: Literal["text"]
    text: str
    id: NotRequired[str]
    annotations: NotRequired[list[Any]]
    index: NotRequired[int | str]
    extras: NotRequired[dict[str, Any]]


class FileContentBlock(TypedDict):
    """文件内容块（标准类型）

    官方规范: https://reference.langchain.com/python/langchain/messages/#langchain.messages.FileContentBlock
    """
    type: Literal["file"]
    id: NotRequired[str]                 # 内容块标识符
    file_id: NotRequired[str]            # 文件存储系统中的文件 ID
    mime_type: NotRequired[str]          # 文件 MIME 类型
    index: NotRequired[int | str]        # 流式响应中的块索引
    url: NotRequired[str]                # 文件 URL
    base64: NotRequired[str]             # Base64 编码的文件数据
    extras: NotRequired[dict[str, Any]]  # 提供商特定的元数据


class NonStandardContentBlock(TypedDict):
    """非标准内容块（用于自定义类型）

    官方规范: https://reference.langchain.com/python/langchain/messages/#langchain.messages.NonStandardContentBlock
    """
    type: Literal["non_standard"]
    id: NotRequired[str]
    value: Any
    index: NotRequired[int]


# 内容块联合类型
ContentBlock = TextContentBlock | FileContentBlock | NonStandardContentBlock


# ============ 通用响应模型 ============

class BusinessResponse(BaseModel):
    """统一业务响应格式"""
    status: Literal["success", "error"] = "success"
    msg: str = ""
    code: int = 2000
    data: Optional[dict] = None


# ============ 对话管理 ============

class ConversationCreateResponse(BaseModel):
    """创建对话响应"""
    id: str = Field(..., description="对话 ID")
    created_at: str = Field(..., description="创建时间")


class ConversationInfo(BaseModel):
    """对话信息"""
    id: str = Field(..., description="对话 ID")
    title: str = Field(default="未命名会话", description="对话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    message_count: int = Field(default=0, description="消息数量")
    is_favorited: bool = Field(default=False, description="是否收藏")


class ConversationListResponse(BaseModel):
    """对话列表响应"""
    conversations: list[ConversationInfo] = Field(default_factory=list)


class MessageItem(BaseModel):
    """消息项"""
    role: Literal["user", "assistant"] = Field(..., description="消息角色")
    content: str | list = Field(..., description="消息内容（字符串或内容块列表）")
    timestamp: str = Field(default="", description="时间戳")
    additional_kwargs: Optional[dict] = Field(default=None, description="附加参数")


class ConversationDetailResponse(BaseModel):
    """对话详情响应"""
    messages: list[MessageItem] = Field(default_factory=list, description="消息列表")
    title: Optional[str] = Field(default=None, description="对话标题")


class ConversationDeleteResponse(BaseModel):
    """删除对话响应"""
    id: str = Field(..., description="已删除的对话 ID")


# ============ 标题管理 ============

class UpdateTitleRequest(BaseModel):
    """更新标题请求"""
    title: Optional[str] = Field(default=None, max_length=20, description="用户指定的标题（用于重命名）")


class UpdateConversationRequest(BaseModel):
    """更新会话请求"""
    title: Optional[str] = Field(default=None, max_length=100, description="会话标题")


class TitleResponse(BaseModel):
    """标题响应"""
    title: str = Field(..., description="对话标题")


# ============ 对话补全 ============

class UploadedFileInfo(BaseModel):
    """上传文件信息"""
    file_id: str = Field(..., description="文件ID")
    file_name: str = Field(..., description="文件名")
    file_path: Optional[str] = Field(default=None, description="文件路径")
    file_type: Optional[str] = Field(default=None, description="文件类型")

    class Config:
        extra = "allow"  # 允许额外字段


class TaskInfo(BaseModel):
    """任务信息"""
    task_type: Literal["qa", "violation_analysis", "report"] = Field(
        default="qa",
        description="任务类型：qa=问答, violation_analysis=事实认定分析, report=报告生成"
    )
    report_type: Optional[str] = Field(default=None, description="报告类型")
    uploaded_files: Optional[list[UploadedFileInfo]] = Field(default=None, description="已上传的文件列表")


class CompletionRequest(BaseModel):
    """发送消息请求"""
    query: str = Field(..., min_length=1, description="用户查询内容")
    task: Optional[TaskInfo] = Field(default=None, description="任务对象")


# ============ 文件管理 ============

class FileUploadResponse(BaseModel):
    """文件上传响应"""
    file_id: str = Field(..., description="文件 ID")
    file_name: str = Field(..., description="原始文件名")
    file_type: str = Field(..., description="检测到的文件类型")


class AvailableFiles(BaseModel):
    """可用文件状态"""
    事实认定书: bool = Field(default=False, alias="事实认定书")
    取证记录表: bool = Field(default=False, alias="取证记录表")

    class Config:
        populate_by_name = True


class ConversationStateResponse(BaseModel):
    """对话状态响应"""
    available_files: dict = Field(
        default_factory=lambda: {"事实认定书": False, "取证记录表": False},
        description="可用文件状态"
    )
    has_analysis_result: bool = Field(default=False, description="是否有分析结果")


# ============ 报告生成 ============

class ResumeRequest(BaseModel):
    """恢复中断的对话请求（Human-in-the-Loop）"""
    action: str = Field(..., description="操作类型: approve | edit | reject")
    data: Optional[dict] = Field(default=None, description="修改后的数据（仅 action=edit 时需要）")


# ============ SSE 事件模型 ============

class SSEStartEvent(BaseModel):
    """SSE 开始事件"""
    timestamp: str = Field(..., description="时间戳")


class SSEMessageEvent(BaseModel):
    """SSE 消息事件（流式文本）"""
    type: Literal["AIMessageChunk"] = "AIMessageChunk"
    id: Optional[str] = Field(default=None, description="消息 ID")
    content: str = Field(..., description="消息内容片段")
    additional_kwargs: dict = Field(default_factory=dict)
    response_metadata: dict = Field(default_factory=dict)
    timestamp: str = Field(..., description="时间戳")


class TaskProgressValue(BaseModel):
    """任务进度值"""
    type: Literal["task_progress"] = "task_progress"
    data: list = Field(..., description="任务列表")


class SSETaskProgressEvent(BaseModel):
    """SSE 任务进度事件（NonStandardContentBlock）"""
    type: Literal["non_standard"] = "non_standard"
    value: TaskProgressValue = Field(..., description="任务进度数据")
    timestamp: str = Field(..., description="时间戳")


class SSECompletedMessage(BaseModel):
    """SSE 完成事件中的消息"""
    id: Optional[str] = Field(default=None, description="消息 ID")
    role: Literal["assistant"] = "assistant"
    content: str | list = Field(..., description="完整消息内容")
    additional_kwargs: dict = Field(default_factory=dict)


class SSECompletedEvent(BaseModel):
    """SSE 完成事件"""
    messages: list[SSECompletedMessage] = Field(..., description="消息列表")
    timestamp: str = Field(..., description="时间戳")


class SSEErrorEvent(BaseModel):
    """SSE 错误事件"""
    message: str = Field(..., description="错误信息")
    timestamp: str = Field(..., description="时间戳")
