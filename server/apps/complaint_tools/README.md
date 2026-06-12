# complaint_tools 目录说明

本目录下的模块围绕“投诉/举报事项抽取与分析”场景，实现了两套主要能力：

- 工具函数：基于 LangChain 的 Tool，用于 Agent 链路中调用；
- 流式接口与现有 SSE 实现方式对齐；
- 本地测试脚本：方便本地调试；
- 大模型与配置封装：统一读取 `case_analysis.env`，初始化大模型 Client。

## 配置相关（可舍弃，回到统一继承，但新增`CASE_ANALYSIS_LAW_BASE_URL`字段）

- `case_analysis.env`
  - 案例分析 Agent 专用配置文件（本地开发使用），典型字段包括：
    - `OPENAI_API_KEY`：访问大模型的 API Key；
    - `OPENAI_BASE_URL`：兼容 OpenAI 协议的 Base URL；
    - `OPENAI_ANALYSIS_MODEL`：用于案例分析的模型名称；
    - `LLM_PROVIDER`：模型提供方标识（如 `openai`）；
    - `CASE_ANALYSIS_LAW_BASE_URL`：法规条文检索接口基础地址（可选，未配置时使用代码内默认地址）。
  - 仅在本目录下被读取，不会覆盖系统已有环境变量。

- `case_analysis_config.py`
  - 封装 `case_analysis.env` 的加载逻辑，避免在业务代码中重复解析配置文件。
  - 主要函数：
    - `ensure_case_env_loaded()`：按需加载 `case_analysis.env`，只加载一次，不覆盖已有环境变量；
    - `get_case_env(name, default=None)`：对外统一的配置读取入口，先保证已加载 env，再从 `os.environ` 读取配置。
  - 其他模块通过 `get_case_env` 读取模型配置、法规接口地址等。

## 大模型封装（可舍弃，回到统一继承）

- `case_analysis_llm.py`
  - 负责统一初始化用于“案例分析”相关工具的 LLM。
  - 关键函数：
    - `get_default_model()`：读取 `OPENAI_ANALYSIS_MODEL`，默认回退为 `gpt-4o-mini`；
    - `get_llm_for_case_analysis(streaming: bool = True)`：基于 `LLM_PROVIDER` / `OPENAI_API_KEY` / `OPENAI_BASE_URL` 创建 LangChain ChatModel，用于后续工具调用；
    - `safe_parse_json_from_llm_output(raw_result, default)`：从 LLM 返回内容中安全解析 JSON，自动剥离 Markdown 代码块，失败时返回默认值。
  - 配置读取全部通过 `case_analysis_config.get_case_env` 完成，业务代码不直接解析 env 文件。

## 举报事项抽取（工具 1）

- `case_analysis_tool_identify_v2.py`
  - 职责：从长文本中抽取“举报事项列表”，并给出每条事项的简要描述与当事人引用的法律依据名称。
  - 主要能力：
    - 构造 LLM 提示词，将输入文本拆分为若干独立的“举报事项/争议点”，输出结构化 JSON 列表；
    - 提供 `identify_case_items_core` 这一非流式核心实现；
    - 暴露 LangChain Tool：`identify_case_items_tool`，供 Agent / Dify 调用；
    - 提供流式 SSE 接口：`stream_identify_case_items(...)`，通过 `ComplaintSSEEvent` 向前端推送 task=2（或配置中约定任务）的进度与结果；
    - 内部还会调用外部“政策库/法规库”接口（如 `http://39.97.229.91:8082/api/policies`），构建政策标题与发布机构映射，用于增强法律依据识别。
  - 典型输出字段示例：
    - `complaint_desc`：单条举报事项的详细描述；
    - `complaint_law_list`：文本中由客户引用或提及的法律/规章/规范性文件名称列表。

## 举报事项分析（工具 2）

- `case_analysis_tool_analyze_v2.py`
  - 职责：在“已抽取的举报事项”基础上，结合专家经验与法规条文，对每条事项进行更细颗粒度的结构化分析。
  - 主要能力：
    - `get_experience(...)`：调用 `apps.complaint_app.experience_loader.find_expert_experience_for_second_category`，或在缺省时返回内置 mock 数据，生成：
      - 是否属于支付结算范围；
      - 调阅清单（需要调阅的资料列表）；
      - 核查材料（需核查的关键材料）；
      - 法律依据列表（法规条文名称字符串列表）。
    - `get_law_item(key: str)`：根据单条法规关键词/名称，调用法规条文查询接口，返回包含法名、条款号、条文原文的列表：
      - 接口基础地址由 `CASE_ANALYSIS_LAW_BASE_URL` 环境变量控制，未配置时回退 `http://39.97.229.91:8082/api/clause`；
      - 调用失败时返回空列表，不中断主流程。
    - `analyze_case_items_core(...)`：核心分析逻辑，将举报事项、分类结果、专家经验、条文原文等信息汇总后交给 LLM 精细化分析；
    - `analyze_case_items(...)`：非流式包装，直接返回 JSON 字符串与 artifact 字符串；
    - `analyze_case_items_tool`：LangChain Tool 定义，提供“content_and_artifact”格式输出；
    - `stream_analyze_case_items(...)`：流式 SSE 接口，适配前端 task=3 阶段，推送开始/进度/完成/错误事件。
  - 输出示例：
    - “分类结果列表”：包括每条举报事项的分类、匹配度、适用条款、建议调阅清单与核查材料等；
    - artifact：用于前端或上层系统展示的详尽分析结构。

## 本地流式测试

- `stream_testmain.py`
  - 职责：快速验证 `stream_identify_case_items` 与 `stream_analyze_case_items` 两个流式接口在命令行中的行为，与现有 SSE 实现保持兼容。
  - 主要内容：
    - `tool1_test()`：调用 `stream_identify_case_items`，使用内置示例文本，逐个打印 SSE chunk 结果；
    - `tool2_test()`：调用 `stream_analyze_case_items`，同样使用示例文本，逐个打印 SSE 输出；
    - `__main__` 中默认运行 `tool2_test()`，可按需修改为 `tool1_test()`。
  - 使用方式（示例）：
    - 在项目根目录激活虚拟环境后执行：`python -m apps.complaint_tools.stream_testmain`。

## LangChain Agent 测试

- `tool_testmain.py`
  - 职责：演示如何使用 LangChain `create_agent` 将“举报事项抽取”和“举报事项分析”两个 Tool 组合在一个 Agent 中统一调用，便于后续接入 Dify 或其他编排平台。
  - 主要能力：
    - `get_case_analysis_agent()`：构造一个包含系统提示词的 Agent，绑定 `identify_case_items_tool` 和 `analyze_case_items_tool`；
    - `astream_case_analysis(query, stream_mode)`：对外提供一个异步流式接口，支持同时输出：
      - `messages`：模型回复消息流；
      - `custom`：用于前端 SSE 的自定义事件流；
    - `test_case_analysis_sync()`：同步测试入口，一次性拿到完整分析结果；
    - `test_case_analysis_stream()` / `_test_case_analysis_stream_async()`：流式测试入口，打印 Agent 的流式输出。
  - 典型调用方式（命令行）：
    - `python -m apps.complaint_tools.tool_testmain`，默认先运行非流式测试，再运行流式测试（可在文件末尾按需注释/启用）。

## 目录总体关系小结

- 配置层：`case_analysis.env` + `case_analysis_config.py`
  - 统一提供模型与外部服务地址等配置，业务代码通过 `get_case_env` 读取。
- 模型层：`case_analysis_llm.py`
  - 基于配置初始化大模型，并提供统一的 JSON 解析工具函数。
- 工具层（Tool）：
  - `case_analysis_tool_identify_v2.py`：负责“举报事项抽取”；
  - `case_analysis_tool_analyze_v2.py`：负责“举报事项分析与结构化输出”。
- 集成与测试层：
  - `stream_testmain.py`：对接现有 SSE 方案的流式测试脚本；
  - `tool_testmain.py`：基于 LangChain Agent 的组合调用与流式/非流式测试脚本。

