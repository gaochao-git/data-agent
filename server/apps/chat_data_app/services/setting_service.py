"""ChatData 后台配置变量服务：表覆盖行 > env > 代码默认。

设计要点（2026-09-26 定稿）：
- .env 变量分两类（用户拍板）：**可动态修改**与不可动态修改。SETTING_REGISTRY
  就是「可动态修改」类的白名单——key 与 env 变量同名，注册即出现在管理页，
  页面列表就是全部可覆盖项；未注册 key 一律拒入表（消费点未迁移的变量配了也
  不生效，不给「配了个寂寞」的入口）。待接入清单见文件尾，迁一个登记一个。
- 读取走进程内 TTL 缓存：多 worker 改后最迟一个 TTL 全节点生效，写入方清本进程
  缓存；不做 Redis 广播（30s 延迟可接受，不为秒级生效引入跨进程复杂度）。
- 读时兜底：表值/env 解析失败或 DB 不可用一律告警并回退 env/默认，运行期不因
  脏配置 fail-fast；写时校验才是拦截非法值的主要闸门。
- 变量说明单一来源 = 注册表 spec.description（表里不存说明，杜绝双写漂移）；
  「重置」= 软删覆盖行，变量回退 env（.env 的运行时形态）/ 代码默认。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from utils.logger import get_logger
from utils.models import ChatDataSetting

from ..daos.dao_session import transaction_scope
from .runtime_config import read_positive_int_env

logger = get_logger(__name__)

# 缓存 TTL：改后最迟该秒数全节点生效；可用 env 调整（1~300 秒）
SETTING_CACHE_TTL_SECONDS = max(1, min(300, read_positive_int_env("CHAT_DATA_SETTING_CACHE_TTL_SECONDS", 30)))
SETTING_VALUE_MAX_LENGTH = 2048

EFFECT_IMMEDIATE = "immediate"
EFFECT_NEXT_RUN = "next_run"
EFFECT_LABELS = {
    EFFECT_IMMEDIATE: "即时生效（≤30秒）",
    EFFECT_NEXT_RUN: "下次会话/运行生效",
}


# 分类统一管理（用户 2026-09-26 拍板「分隔统一」）：顺序即管理页分组展示顺序。
# 新 key 注册时必须归入既有分类；确需新分类在此登记，不允许页面出现随手造的分类。
SETTING_CATEGORIES: list[tuple[str, str]] = [
    ("upload_preview", "上传与预览"),
    ("query", "查询执行"),
    ("concurrency", "并发控制"),
    ("schedule", "定时调度"),
    ("agent", "智能体运行"),
    ("sync_meta", "同步与元数据"),
    ("alert", "监控告警"),
    ("feature", "功能开关"),
]
_CATEGORY_ORDER = {key: index for index, (key, _) in enumerate(SETTING_CATEGORIES)}
_CATEGORY_LABELS = dict(SETTING_CATEGORIES)


@dataclass(frozen=True)
class SettingSpec:
    """注册表条目（=「可动态修改」类 env 变量）：类型 + 代码默认值 + 校验范围 + 生效时机。"""

    key: str
    value_type: str  # int / bool / float / string
    default: Any
    category: str  # 必填：SETTING_CATEGORIES 中的分类 key
    minimum: float | None = None
    maximum: float | None = None
    effect: str = EFFECT_IMMEDIATE
    description: str = ""


# 「可动态修改」类 env 变量白名单（消费点均为调用时读取）。迁一个登记一个；
# 生效时机在接入时核实后如实标注，这是防「改了没生效」工单的关键。
SETTING_REGISTRY: dict[str, SettingSpec] = {
    "CHAT_DATA_MAX_UPLOAD_SIZE_MB": SettingSpec(
        key="CHAT_DATA_MAX_UPLOAD_SIZE_MB",
        value_type="int",
        default=50,
        category="upload_preview",
        minimum=1,
        maximum=2048,
        effect=EFFECT_IMMEDIATE,
        description="单文件上传上限（MB），主对话上传与技能文件上传共用口径",
    ),
    "CHAT_DATA_MAX_IMAGE_SIZE_MB": SettingSpec(
        key="CHAT_DATA_MAX_IMAGE_SIZE_MB",
        value_type="int",
        default=5,
        category="upload_preview",
        minimum=1,
        maximum=2048,
        effect=EFFECT_IMMEDIATE,
        description="图片读取/注入单张大小上限（MB），agent 视觉链路唯一口径",
    ),
    "CHAT_DATA_MAX_SCAN_PDF_PAGES": SettingSpec(
        key="CHAT_DATA_MAX_SCAN_PDF_PAGES",
        value_type="int",
        default=10,
        category="upload_preview",
        minimum=1,
        maximum=200,
        effect=EFFECT_IMMEDIATE,
        description="扫描版 PDF 视觉转写的最大页数上限（parse_document 兜底链路）",
    ),
    "CHAT_DATA_WORKSPACE_MAX_MB": SettingSpec(
        key="CHAT_DATA_WORKSPACE_MAX_MB",
        value_type="int",
        default=100,
        category="upload_preview",
        minimum=1,
        maximum=10240,
        effect=EFFECT_IMMEDIATE,
        description="单会话工作区总占用上限（MB），覆盖 uploads/workspace/outputs/memory，上传前校验",
    ),
    "CHAT_DATA_TEXT_PREVIEW_MAX_MB": SettingSpec(
        key="CHAT_DATA_TEXT_PREVIEW_MAX_MB",
        value_type="int",
        default=5,
        category="upload_preview",
        minimum=1,
        maximum=2048,
        effect=EFFECT_IMMEDIATE,
        description="会话工作区文本文件预览源上限（MB）",
    ),
    "CHAT_DATA_OFFICE_PREVIEW_SOURCE_MAX_MB": SettingSpec(
        key="CHAT_DATA_OFFICE_PREVIEW_SOURCE_MAX_MB",
        value_type="int",
        default=50,
        category="upload_preview",
        minimum=1,
        maximum=2048,
        effect=EFFECT_IMMEDIATE,
        description="会话工作区 Office 文件预览源上限（MB）",
    ),
    "CHAT_DATA_WORKSPACE_ARCHIVE_MAX_MB": SettingSpec(
        key="CHAT_DATA_WORKSPACE_ARCHIVE_MAX_MB",
        value_type="int",
        default=50,
        category="upload_preview",
        minimum=1,
        maximum=2048,
        effect=EFFECT_IMMEDIATE,
        description="会话工作区目录打包下载压缩包大小上限（MB），限 zip 产物而非源文件总大小",
    ),
    "CHAT_DATA_QUERY_CACHE_ENABLED": SettingSpec(
        key="CHAT_DATA_QUERY_CACHE_ENABLED",
        value_type="bool",
        default=False,
        category="query",
        effect=EFFECT_IMMEDIATE,
        description="SQL 查询结果缓存开关",
    ),
    "CHAT_DATA_QUERY_CACHE_TTL_SECONDS": SettingSpec(
        key="CHAT_DATA_QUERY_CACHE_TTL_SECONDS",
        value_type="int",
        default=3600,
        category="query",
        minimum=1,
        maximum=604800,
        effect=EFFECT_IMMEDIATE,
        description="SQL 查询结果缓存过期时间（秒），限新写入的缓存行",
    ),
    "CHAT_DATA_HTTP_POLL_INTERVAL_MS": SettingSpec(
        key="CHAT_DATA_HTTP_POLL_INTERVAL_MS",
        value_type="int",
        default=1500,
        category="query",
        minimum=200,
        maximum=60000,
        effect=EFFECT_IMMEDIATE,
        description="HTTP 执行器远端任务轮询间隔（毫秒）",
    ),
    "CHATDATA_ROW_LIMIT": SettingSpec(
        key="CHATDATA_ROW_LIMIT",
        value_type="int",
        default=100,
        category="query",
        minimum=1,
        maximum=10000,
        effect=EFFECT_IMMEDIATE,
        description="查询结果行数上限：未显式指定 limit 时的默认行数，也是显式 LIMIT / DSL limit 的校验上限",
    ),
    "CHAT_DATA_MYSQL_QUERY_TIMEOUT_SECONDS": SettingSpec(
        key="CHAT_DATA_MYSQL_QUERY_TIMEOUT_SECONDS",
        value_type="int",
        default=60,
        category="query",
        minimum=1,
        maximum=3600,
        effect=EFFECT_IMMEDIATE,
        description="MySQL 查询默认超时时间（秒），请求显式传参时按传参封顶 3600",
    ),
    "CHAT_DATA_HIVE_QUERY_TIMEOUT_SECONDS": SettingSpec(
        key="CHAT_DATA_HIVE_QUERY_TIMEOUT_SECONDS",
        value_type="int",
        default=600,
        category="query",
        minimum=1,
        maximum=3600,
        effect=EFFECT_IMMEDIATE,
        description="Hive 查询默认超时时间（秒），请求显式传参时按传参封顶 3600",
    ),
    "CHAT_DATA_AGENT_MAX_CONCURRENCY": SettingSpec(
        key="CHAT_DATA_AGENT_MAX_CONCURRENCY",
        value_type="int",
        default=4,
        category="concurrency",
        minimum=1,
        maximum=128,
        effect=EFFECT_IMMEDIATE,
        description="单进程数据智能体对话并发数（无在途运行时按新值重建）",
    ),
    "CHAT_DATA_RUN_LEASE_SECONDS": SettingSpec(
        key="CHAT_DATA_RUN_LEASE_SECONDS",
        value_type="int",
        default=30,
        category="concurrency",
        minimum=5,
        maximum=300,
        effect=EFFECT_IMMEDIATE,
        description="Agent Run 租约有效期（秒）：心跳每 5 秒续约，超时后才回收失联 Run",
    ),
    "CHAT_DATA_DASHBOARD_PANEL_MAX_CONCURRENCY": SettingSpec(
        key="CHAT_DATA_DASHBOARD_PANEL_MAX_CONCURRENCY",
        value_type="int",
        default=8,
        category="concurrency",
        minimum=1,
        maximum=128,
        effect=EFFECT_IMMEDIATE,
        description="看板面板批量执行最大并发数",
    ),
    "CHAT_DATA_SQL_USER_DATASOURCE_MAX_CONCURRENCY": SettingSpec(
        key="CHAT_DATA_SQL_USER_DATASOURCE_MAX_CONCURRENCY",
        value_type="int",
        default=2,
        category="concurrency",
        minimum=1,
        maximum=128,
        effect=EFFECT_IMMEDIATE,
        description="同一用户同一数据源 SQL 查询最大并发数",
    ),
    "CHAT_DATA_HIVE_SCHEMA_MAX_WORKERS": SettingSpec(
        key="CHAT_DATA_HIVE_SCHEMA_MAX_WORKERS",
        value_type="int",
        default=10,
        category="concurrency",
        minimum=1,
        maximum=128,
        effect=EFFECT_IMMEDIATE,
        description="Hive 表结构同步线程池最大并发",
    ),
    "CHAT_DATA_AGENT_RECURSION_LIMIT": SettingSpec(
        key="CHAT_DATA_AGENT_RECURSION_LIMIT",
        value_type="int",
        default=40,
        category="agent",
        minimum=1,
        maximum=10000,
        effect=EFFECT_NEXT_RUN,
        description="智能体超步（super-step）上限，主图与子图同源；新会话装配时读取",
    ),
    "CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED": SettingSpec(
        key="CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED",
        value_type="bool",
        default=False,
        category="agent",
        effect=EFFECT_NEXT_RUN,
        description="是否注册 explore 只读子智能体（新会话装配时生效）",
    ),
    "CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED": SettingSpec(
        key="CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED",
        value_type="bool",
        default=False,
        category="agent",
        effect=EFFECT_NEXT_RUN,
        description="是否注册 plan 规划子智能体（新会话装配时生效）",
    ),
    "CHAT_DATA_SCHEDULE_POLL_SECONDS": SettingSpec(
        key="CHAT_DATA_SCHEDULE_POLL_SECONDS",
        value_type="int",
        default=30,
        category="schedule",
        minimum=1,
        maximum=3600,
        effect=EFFECT_IMMEDIATE,
        description="定时调度轮询间隔（秒），下一轮轮询按新值睡眠",
    ),
    "CHAT_DATA_SCHEDULE_DISPATCH_BATCH_SIZE": SettingSpec(
        key="CHAT_DATA_SCHEDULE_DISPATCH_BATCH_SIZE",
        value_type="int",
        default=10,
        category="schedule",
        minimum=1,
        maximum=100,
        effect=EFFECT_IMMEDIATE,
        description="每进程每轮定时触发最大派发数",
    ),
    "CHAT_DATA_SCHEDULE_USER_MAX_CONCURRENCY": SettingSpec(
        key="CHAT_DATA_SCHEDULE_USER_MAX_CONCURRENCY",
        value_type="int",
        default=2,
        category="schedule",
        minimum=1,
        maximum=64,
        effect=EFFECT_IMMEDIATE,
        description="同一用户在途定时执行上限",
    ),
    "CHAT_DATA_SCHEDULE_MAX_JOBS_PER_USER": SettingSpec(
        key="CHAT_DATA_SCHEDULE_MAX_JOBS_PER_USER",
        value_type="int",
        default=20,
        category="schedule",
        minimum=1,
        maximum=1000,
        effect=EFFECT_IMMEDIATE,
        description="单用户可创建的定时任务数上限",
    ),
    "CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS": SettingSpec(
        key="CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS",
        value_type="int",
        default=30,
        category="sync_meta",
        minimum=1,
        maximum=3600,
        effect=EFFECT_IMMEDIATE,
        description="数据源同步任务总超时（秒）",
    ),
    "CHAT_DATA_META_TIMEOUT_SECONDS": SettingSpec(
        key="CHAT_DATA_META_TIMEOUT_SECONDS",
        value_type="int",
        default=30,
        category="sync_meta",
        minimum=1,
        maximum=3600,
        effect=EFFECT_IMMEDIATE,
        description="元数据 HTTP 模式单次请求超时（秒）",
    ),
    "CHAT_DATA_TOOL_CALL_RATE_ALERT_THRESHOLD": SettingSpec(
        key="CHAT_DATA_TOOL_CALL_RATE_ALERT_THRESHOLD",
        value_type="int",
        default=100,
        category="alert",
        minimum=1,
        maximum=100000,
        effect=EFFECT_IMMEDIATE,
        description="单会话工具调用频率告警阈值（次/统计窗口）",
    ),
    "CHAT_DATA_TOOL_CALL_RATE_ALERT_WINDOW_SECONDS": SettingSpec(
        key="CHAT_DATA_TOOL_CALL_RATE_ALERT_WINDOW_SECONDS",
        value_type="int",
        default=60,
        category="alert",
        minimum=1,
        maximum=3600,
        effect=EFFECT_IMMEDIATE,
        description="工具调用频率告警统计窗口（秒）",
    ),
    "CHAT_DATA_USER_SEMANTIC_MODEL_ENABLED": SettingSpec(
        key="CHAT_DATA_USER_SEMANTIC_MODEL_ENABLED",
        value_type="bool",
        default=False,
        category="feature",
        effect=EFFECT_IMMEDIATE,
        description=(
            "终端用户「自定义语义模型」入口是否开放（默认关闭，语义模型由管理员统一维护）；"
            "关闭时前台「语义模型配置」页签整页隐藏（管理员仍可从后台管理维护），"
            "打开后恢复该页签与自助建模，前端刷新页面后生效"
        ),
    ),
}

# 待接入清单（消费点仍是导入期常量/函数签名默认值，改完一处登记一处）：
# - 不进表：CHAT_DATA_DB_CONNECT_TIMEOUT_SECONDS / HIVE_SOCKET_TIMEOUT_SECONDS（连接建立参数，
#   连接池缓存引擎改了也不热）、CHAT_DATA_HTTP_VERIFY_TLS（安全开关，启动期 fail-fast）
# 注：CHATDATA_ROW_LIMIT 与 CHAT_DATA_MYSQL/HIVE_QUERY_TIMEOUT_SECONDS 已于 2026-09-26 二期迁入
# （消费点见 runtime_config.normalize_row_limit、sql_guard、executor、sql_run/runtime、dashboard 与语义编译链）。


class SettingValidationError(Exception):
    """写时校验失败：key 未注册 / 值类型或范围非法。"""


class SettingNotFoundError(Exception):
    """重置目标不存在或已删除。"""


_cache: dict[str, tuple[float, str | None]] = {}
# 单 key 加载锁：缓存过期瞬间避免多个协程同时打 DB（stampede）
_load_locks: dict[str, asyncio.Lock] = {}
# chat_data_setting 表未建（DDL 未执行）时每个读取都会抛异常：只告警一次，
# 之后降为 debug，避免表缺失期间日志被刷屏（功能不受影响，读取走 env/默认）。
_missing_table_warned = False


def _is_missing_table_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no such table" in text or "doesn't exist" in text


async def _load_raw_setting(setting_key: str) -> str | None:
    """读表覆盖行原始值（未配置返回 None）。DB 异常不外抛，按未配置处理并告警。"""
    global _missing_table_warned
    try:
        async with transaction_scope() as session:
            result = await session.execute(
                select(ChatDataSetting.setting_value).where(
                    ChatDataSetting.setting_key == setting_key,
                    ChatDataSetting.delete_flag == 0,
                )
            )
            return result.scalar_one_or_none()
    except Exception as exc:
        if _is_missing_table_error(exc):
            if not _missing_table_warned:
                _missing_table_warned = True
                logger.warning("chat_data_setting 表不存在（DDL 未执行？），配置变量回退 env/默认")
            else:
                logger.debug("读取配置变量 %s 时表不存在，回退 env/默认", setting_key)
        else:
            logger.error("读取配置变量 %s 失败，本次回退 env/默认: %s", setting_key, exc)
        return None


def _parse_registered_value(spec: SettingSpec, raw: str) -> Any:
    """按注册类型解析并做范围校验，非法抛 ValueError/TypeError。bool 仅收 true/false。"""
    text = str(raw).strip()
    if spec.value_type == "int":
        value: Any = int(text)
    elif spec.value_type == "float":
        value = float(text)
    elif spec.value_type == "bool":
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        raise ValueError("bool 类型仅接受 true/false（大小写不敏感）")
    else:
        return text
    if spec.minimum is not None and value < spec.minimum:
        raise ValueError(f"不能小于 {spec.minimum:g}")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"不能大于 {spec.maximum:g}")
    return value


async def _load_cached_raw_setting(key: str) -> str | None:
    """读取原始配置值：TTL 内走缓存，过期时按 key 单飞回源，避免并发打爆 DB。"""
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < SETTING_CACHE_TTL_SECONDS:
        return cached[1]

    lock = _load_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < SETTING_CACHE_TTL_SECONDS:
            return cached[1]
        raw = await _load_raw_setting(key)
        _cache[key] = (time.monotonic(), raw)
        return raw


async def get_runtime_setting(key: str, default: Any = None) -> Any:
    """运行期读取单一入口：表覆盖行 > env > default（注册 key 回退 spec.default）。

    注册 key 按类型解析，非法值告警后逐级回退；未注册 key 表行/env 均返回原始
    字符串（防御性读取，正常不会有未注册 key 入表），否则返回 default。
    """
    spec = SETTING_REGISTRY.get(key)
    raw = await _load_cached_raw_setting(key)
    if raw is not None:
        if spec is None:
            return str(raw)
        try:
            return _parse_registered_value(spec, raw)
        except (TypeError, ValueError) as exc:
            logger.error("配置变量 %s 表值 %r 非法（%s），回退 env/默认", key, raw, exc)
    if spec is None:
        # 未注册 key 同样遵循「表 > env > 默认」：env 有值时返回原始字符串。
        # 若在此忽略 env，管理页来源=env 的行运行时却不生效，徽标即失真。
        env_raw = os.getenv(key)
        if env_raw is not None and str(env_raw).strip():
            return str(env_raw)
        return default
    env_raw = os.getenv(key)
    if env_raw is not None and str(env_raw).strip():
        try:
            return _parse_registered_value(spec, env_raw)
        except (TypeError, ValueError) as exc:
            logger.error("配置变量 %s env 值 %r 非法（%s），回退默认", key, env_raw, exc)
    return spec.default


async def get_workspace_size_limit() -> tuple[int, int]:
    """单会话工作区总占用上限：返回 (MB, 字节)。"""
    size_mb = await get_runtime_setting("CHAT_DATA_WORKSPACE_MAX_MB")
    return size_mb, size_mb * 1024 * 1024


async def get_upload_size_limit() -> tuple[int, int]:
    """上传上限便捷读取：返回 (MB, 字节)，主对话与技能文件上传共用。"""
    size_mb = await get_runtime_setting("CHAT_DATA_MAX_UPLOAD_SIZE_MB")
    return size_mb, size_mb * 1024 * 1024


def clear_setting_cache() -> None:
    """写入/重置后清空本进程缓存；其他 worker 等 TTL 到期自然刷新。"""
    _cache.clear()


def _validate_setting_value(key: str, value: str) -> None:
    text = str(value)
    if not text.strip():
        raise SettingValidationError("变量值不能为空")
    if len(text) > SETTING_VALUE_MAX_LENGTH:
        raise SettingValidationError(f"变量值长度不能超过 {SETTING_VALUE_MAX_LENGTH}")
    spec = SETTING_REGISTRY.get(key)
    if spec is not None:
        try:
            _parse_registered_value(spec, text)
        except (TypeError, ValueError) as exc:
            raise SettingValidationError(f"变量值非法：{exc}") from exc


async def upsert_setting(
    setting_key: str,
    setting_value: str,
    user_id: str,
    username: str,
) -> dict[str, str]:
    """覆盖已注册变量；已软删的 key 复用原行恢复（不 insert，规避唯一键冲突）。

    key 必须在 SETTING_REGISTRY（「可动态修改」类白名单），未注册一律拒绝。
    """
    key = str(setting_key or "").strip()
    spec = SETTING_REGISTRY.get(key)
    if spec is None:
        raise SettingValidationError(f"变量 {key or '(空)'} 未注册，仅允许覆盖管理页列出的可动态修改变量")
    _validate_setting_value(key, setting_value)
    async with transaction_scope() as session:
        result = await session.execute(
            select(ChatDataSetting).where(ChatDataSetting.setting_key == key).with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            session.add(
                ChatDataSetting(
                    setting_key=key,
                    setting_value=str(setting_value),
                    delete_flag=0,
                    create_by=str(user_id),
                    create_by_name=str(username),
                    update_by=str(user_id),
                    update_by_name=str(username),
                )
            )
            action = "created"
        else:
            was_deleted = row.delete_flag == 1
            row.setting_value = str(setting_value)
            row.delete_flag = 0
            row.update_by = str(user_id)
            row.update_by_name = str(username)
            action = "restored" if was_deleted else "updated"
    clear_setting_cache()
    return {"setting_key": key, "action": action}


async def delete_setting(setting_key: str, user_id: str, username: str) -> dict[str, str]:
    """重置单个变量：软删覆盖行即回退 env / 代码默认。"""
    key = str(setting_key or "").strip()
    async with transaction_scope() as session:
        result = await session.execute(
            select(ChatDataSetting).where(ChatDataSetting.setting_key == key).with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None or row.delete_flag == 1:
            raise SettingNotFoundError(f"配置变量 {key} 不存在或已重置")
        row.delete_flag = 1
        row.update_by = str(user_id)
        row.update_by_name = str(username)
    clear_setting_cache()
    return {"setting_key": key}


async def reset_all_settings(user_id: str, username: str) -> dict[str, int]:
    """全部重置：软删所有覆盖行，所有变量回退 env / 代码默认。"""
    async with transaction_scope() as session:
        result = await session.execute(
            select(ChatDataSetting).where(ChatDataSetting.delete_flag == 0).with_for_update()
        )
        rows = result.scalars().all()
        for row in rows:
            row.delete_flag = 1
            row.update_by = str(user_id)
            row.update_by_name = str(username)
    clear_setting_cache()
    return {"reset_count": len(rows)}


def _setting_row_view(key: str, row: ChatDataSetting | None) -> dict[str, Any]:
    """单个 key 的来源视图：source、生效值原文与展示字段（覆盖行/env 二选一）。"""
    if row is not None:
        return {
            "source": "table",
            "effective_raw": row.setting_value,
            "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if row.update_time else "",
            "update_by_name": row.update_by_name or "",
        }
    env_raw = os.getenv(key)
    has_env = env_raw is not None and str(env_raw).strip()
    return {
        "source": "env" if has_env else "default",
        "effective_raw": env_raw if has_env else None,
        "update_time": "",
        "update_by_name": "",
    }


def _parse_effective_value(spec: SettingSpec, effective_raw: str | None) -> tuple[Any, bool]:
    """生效值解析：非法值告警口径与运行期读取一致——回退默认并标记 invalid。"""
    if effective_raw is not None and str(effective_raw).strip():
        try:
            return _parse_registered_value(spec, effective_raw), False
        except (TypeError, ValueError):
            return spec.default, True
    return spec.default, False


def _format_setting_item(key: str, spec: SettingSpec, row: ChatDataSetting | None) -> dict[str, Any]:
    """注册表条目 + 覆盖行 → 管理页单行视图。"""
    view = _setting_row_view(key, row)
    effective, invalid = _parse_effective_value(spec, view["effective_raw"])
    return {
        "setting_key": key,
        "setting_desc": spec.description,
        "category": spec.category,
        "category_label": _CATEGORY_LABELS.get(spec.category, spec.category),
        "value_type": spec.value_type,
        "effect_label": EFFECT_LABELS[spec.effect],
        "setting_value": row.setting_value if row is not None else None,
        "source": view["source"],
        "effective_value": effective,
        "value_invalid": invalid,
        "update_time": view["update_time"],
        "update_by_name": view["update_by_name"],
    }


def _matches_setting_keyword(item: dict[str, Any], needle: str) -> bool:
    return needle in item["setting_key"].lower() or needle in str(item["setting_desc"]).lower()


async def list_settings(keyword: str | None = None) -> dict[str, Any]:
    """管理页列表 = 注册表全量（可动态修改类白名单），标注生效值与来源（table/env/default）。"""
    async with transaction_scope() as session:
        result = await session.execute(select(ChatDataSetting).where(ChatDataSetting.delete_flag == 0))
        rows = result.scalars().all()
    row_map = {row.setting_key: row for row in rows}
    items = [
        _format_setting_item(key, spec, row_map.get(key))
        for key, spec in sorted(
            SETTING_REGISTRY.items(),
            key=lambda pair: (_CATEGORY_ORDER.get(pair[1].category, len(SETTING_CATEGORIES)), pair[0]),
        )
    ]
    needle = str(keyword or "").strip().lower()
    if needle:
        items = [item for item in items if _matches_setting_keyword(item, needle)]
    return {"items": items, "total": len(items)}
