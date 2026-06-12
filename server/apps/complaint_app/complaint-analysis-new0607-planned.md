Here is Claude's plan:
╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
 Context

 需要在不影响现有 /api/complaint/complaint-analysis 的前提下，新增一条举报事项分析 v2 流程。v2
 的核心变化不是简单改出参，而是改成新的三阶段链路：先生成举报摘要，再基于摘要生成举报事项分析，最后生成调阅材料清单与核实思路；并且整个过程要通过 SSE 以 Start / Reasoning
 / Acting / Answer / Reference / Done 事件对外流式输出。这些 SSE 事件面向接口消费者，返回内容应使用后端预设的业务文案与阶段结果摘要，不直接透出模型原始流式输出。

 现有代码已经具备较成熟的能力：请求解析、上传/下载文件、OCR/文本抽取、版本化存储、摘要/分析缓存、以及 complaint_app 内部的流式事件生成。因此推荐方案是：保留 v1
 完全不动，新增一条独立的 v2 编排链路，并最大限度复用底层能力。

 Recommended approach

 1. 新增独立 v2 路由，但复用现有入参与解析逻辑

 在 server/apps/complaint_app/fastapi_routes.py 新增 v2 路由入口，复用现有：

 - ComplaintAnalysisRequest
 - _build_analysis_payload_from_form()
 - _build_analysis_payload_from_json()
 - _parse_uploaded_pdf()
 - _validate_analysis_payload()
 - _resolve_analysis_file_ids()
 - _resolve_complaint_scope_context()
 - build_base_id_for_file_ids() / build_base_id_for_uploaded_pdf()

 这样可以保证：
 - v2 入参与当前接口保持一致
 - JSON / multipart 两种调用方式保持一致
 - scope、reported_entities、file_id/file_ids、use_uploaded_pdf 等既有行为不变
 - 新路由按需求文档实现为 /api/complants/complaint-analysis-v2，同时保持现有 /api/complaint/complaint-analysis 不变

 2. 复用现有文件解析、文本抽取与 scope/base_id 规则

 在 v2 编排里直接复用 server/apps/complaint_app/services/complaint_api_service.py 中现有能力：

 - resolve_pdf_file()
 - save_uploaded_pdf()
 - extract_texts_and_save()
 - load_latest_text() / _load_ready_text()
 - ensure_user_id()
 - scope 相关读取链路

 保持现有行为：
 - 原始文件与 text 继续沿用当前 stage 目录结构
 - 多文件合案继续使用稳定 base_id
 - force=true 只控制 v2 LLM 阶段是否重跑，不重写基础文件解析规则

 3. 为 v2 新建独立服务目录，避免污染 v1 编排

 新增目录：

 - server/apps/complaint_app/services/complaint_analysis_v2/orchestrator.py
 - server/apps/complaint_app/services/complaint_analysis_v2/prompt_loader.py
 - server/apps/complaint_app/services/complaint_analysis_v2/sse.py
 - server/apps/complaint_app/prompts/complaint_analysis_v2/

 职责划分：

 - orchestrator.py
   - 串联文本准备、三阶段 LLM 调用、缓存判断、结果落盘、SSE 输出
 - prompt_loader.py
   - 加载并缓存 v2 prompt 目录中的 system.md、阶段模板和业务技能文档
   - 完成占位符替换与 prompt hash 计算
 - sse.py
   - 单独实现命名 SSE 输出，不改动现有 ComplaintSSEEvent.to_sse() 的 data-only 行为
 - prompts/complaint_analysis_v2/
   - 存放全部 markdown 模板和业务技能文档，避免与 Python 代码揉在一起

 4. v2 三阶段 LLM 流程按“文件模板 + 占位符渲染”实现

 新增 prompt 目录与文件：

 - server/apps/complaint_app/prompts/complaint_analysis_v2/system.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/summary_prompt.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/complaint_summary_guidance.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/issue_analysis_prompt.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/complaint_issue_analysis_guidance.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/final_prompt.md

 渲染方式参考现有本地 prompt 加载模式：
 - server/apps/complaint_app/services/summary/llm_summary.py:_load_sys_prompt
 - server/apps/complaint_app/services/analysis_agent/two_stage_matcher.py:_render_template

 建议采用轻量模板替换（如 {{complaint_text}}），不要把 v2 强耦合到当前 skill runtime。

 阶段 1：举报摘要

 消息结构：
 - system: system.md
 - user: summary_prompt.md 渲染结果

 summary_prompt.md 至少注入：
 - 举报件原文
 - 举报人
 - 被举报机构（优先使用请求中的 reported_entities 作为占位值来源；为空时再退化为空字符串）
 - complaint_summary_guidance.md 内容

 输出：
 - 一段 markdown 摘要
 - 保存为 summary_v2 阶段产物

 阶段 2：举报事项分析

 消息结构：
 - system: system.md
 - user: issue_analysis_prompt.md 渲染结果

 issue_analysis_prompt.md 至少注入：
 - 举报件原文
 - 阶段 1 摘要输出
 - complaint_issue_analysis_guidance.md 内容

 输出：
 - 一段 markdown 的举报事项分析
 - 保存为 analysis_v2 阶段产物

 阶段 3：调阅材料清单及核实思路

 消息结构：
 - system: system.md
 - user: final_prompt.md 渲染结果

 final_prompt.md 至少注入：
 - 阶段 2 的举报事项分析输出
 - 最终阶段的指导内容直接写在 final_prompt.md 内，不再拆分单独的 guidance 文件

 输出：
 - 一段 markdown，包含调阅材料清单与核实思路
 - 保存到聚合 complaint_analysis_v2 产物

 5. 直接复用 LLM 客户端与指标埋点，不复用 v1 分析主流程

 v2 不应复用 server/apps/complaint_app/services/analysis_agent/agent.py:run_complaint_analysis()
 的业务语义，因为该流程围绕“事项抽取/合理性分析/经验匹配/法律条文分析”，与新需求的三段式提示词链路不一致。

 但应复用其底层模型调用与完整结果生成模式：

 - server/apps/complaint_app/core/llm/llm_client.py
 - server/apps/complaint_app/core/async_openai_client.py
 - utils/llm_prompt_metrics.py
 - utils/llm_thinking.py:build_thinking_extra_body

 建议每个阶段统一做法：
 - 记录 prompt metrics
 - 完整拿到阶段 markdown 结果后再保存
 - SSE 不直接转发模型原始 reasoning delta 或 content delta
 - SSE 仅输出后端定义的业务文案、阶段状态与阶段完成后的结果摘要

 6. v2 使用独立 stage 名称与 input hash，避免污染 v1 缓存

 继续使用现有 versioning/stage 机制（stage_dir()、manifest、版本递增逻辑），但使用新的 stage 名：

 - summary_v2
 - analysis_v2
 - complaint_analysis_v2

 不要复用：
 - summary
 - issue_analysis

 每个阶段保存：
 - final.md：完整 markdown 输出
 - final.json：结构化元数据（版本、上游版本、input_hash、prompt 文件版本、cache_hit 等）

 缓存命中逻辑参考 build_reply_draft_input_hash() 的做法，为每个阶段单独计算 input_hash：

 - summary_v2 hash：text_version + reporter_name + reported_institution + system.md + summary.md + skill1.md
 - analysis_v2 hash：text_version + summary_v2_version(or summary output) + system.md + analysis.md + skill2.md
 - complaint_analysis_v2 hash：analysis_v2_version(or analysis output) + system.md + final.md

 这样可以确保：
 - prompt 文件调整后自动失效缓存
 - 同一举报件在不同 reporter / entity 上下文下不串缓存
 - v1/v2 数据完全隔离

 7. 用独立 named-SSE formatter 输出目标事件名

 不要改 server/apps/complaint_app/services/pipeline_base.py:ComplaintSSEEvent.to_sse() 的现有语义；在 v2 新增 named SSE helper，输出标准帧：

 event: Start
 data: {...}

 event: Reasoning
 data: {...}

 事件映射建议

 - Start
   - 请求校验、base_id/scope 解析完成后发送一次
   - 携带 base_id、user_id、scope_context、file_ids
 - Acting
   - 非 LLM 动作与阶段切换
   - 例如：准备文件、抽取文本、命中文本缓存、开始 summary、开始 analysis、开始 final、保存结果
   - 携带 phase、message、progress、cache_hit
 - Reasoning
   - 使用面向用户的固定业务文案，不直接透出模型 reasoning 内容
   - 例如：“正在梳理举报摘要重点”“正在根据摘要拆解举报事项”“正在生成调阅材料与核实思路”
   - 携带 phase、message
 - Answer
   - 用于返回阶段性业务结果摘要或最终结构化结果摘要，不直接流式透出模型正文
   - 例如：摘要阶段可返回“已完成举报摘要生成”；分析阶段返回“已完成举报事项分析”；最终阶段返回“已生成调阅材料清单与核实思路”
   - 携带 phase、message、必要时附带精简 result_preview
 - Reference
   - 每个阶段完成时发送一次完整快照
   - 建议携带：phase、version、content、prompt_files、cache_hit
 - Done
   - 结束时只发一次
   - 携带聚合结果：
       - summary_markdown
     - analysis_markdown
     - final_markdown
     - versions
     - scope_context
     - cache_hit

 8. 尽量沿用现有 route/service 的日志与耗时记录风格

 复用并对齐现有日志/耗时模式：
 - log_phase_timing()
 - log_route_timing()
 - timing_now() / timing_elapsed_ms()
 - complaint trace context 组装方式

 这样 v2 在观测性上与现有 complaint_app 保持一致，便于排障与对比。

 Critical files to modify

 Existing files

 - server/apps/complaint_app/fastapi_routes.py
 - server/apps/complaint_app/services/complaint_api_service.py

 New files

 - server/apps/complaint_app/services/complaint_analysis_v2/orchestrator.py
 - server/apps/complaint_app/services/complaint_analysis_v2/prompt_loader.py
 - server/apps/complaint_app/services/complaint_analysis_v2/sse.py
 - server/apps/complaint_app/prompts/complaint_analysis_v2/system.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/summary_prompt.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/complaint_summary_guidance.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/issue_analysis_prompt.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/complaint_issue_analysis_guidance.md
 - server/apps/complaint_app/prompts/complaint_analysis_v2/final_prompt.md

 Existing code to reuse

 - 请求解析与校验：
   - server/apps/complaint_app/fastapi_routes.py
 - 文件解析与文本抽取：
   - server/apps/complaint_app/services/complaint_api_service.py:resolve_pdf_file
   - server/apps/complaint_app/services/complaint_api_service.py:save_uploaded_pdf
   - server/apps/complaint_app/services/complaint_api_service.py:extract_texts_and_save
 - 版本与目录管理：
   - server/apps/complaint_app/core/versioning.py
 - prompt 读取模式：
   - server/apps/complaint_app/services/summary/llm_summary.py:_load_sys_prompt
   - server/apps/complaint_app/services/analysis_agent/two_stage_matcher.py:_render_template
 - LLM 调用与结果生成：
   - server/apps/complaint_app/core/llm/llm_client.py
   - server/apps/complaint_app/core/async_openai_client.py
 - 现有 SSE 与阶段流设计参考：
   - server/apps/complaint_app/services/pipeline_base.py
   - server/routes/chat_routes.py

 Verification

 1. 接口兼容性验证

     Verification

     1. 接口兼容性验证
       - 分别用 JSON 和 multipart/form-data 调用 v2
       - 验证入参与现有 ComplaintAnalysisRequest 完全兼容
       - 验证 file_id / file_ids / use_uploaded_pdf / scope / reported_entities 均可正常工作
     2. SSE 协议验证
       - 验证返回头仍为 text/event-stream
       - 验证事件名严格为：Start、Reasoning、Acting、Answer、Reference、Done
       - 验证最后一定有 Done
       - 验证每帧 data 都是合法 JSON
     3. 三阶段链路验证
       - 无缓存时：能依次完成 summary / analysis / final 三阶段
       - 有缓存时：能正确命中 summary_v2 / analysis_v2 / complaint_analysis_v2
       - force=true 时：跳过 v2 缓存并重跑三阶段
     4. 持久化验证
       - 检查 summary_v2、analysis_v2、complaint_analysis_v2 目录和 manifest 是否正确写入
       - 检查 input_hash、上游版本、prompt 文件版本是否落盘
       - 确认不会覆盖或污染现有 summary / issue_analysis
     5. 回归验证
       - 重新调用现有 /api/complaint/complaint-analysis
       - 确认 v1 SSE 行为与结果不变
     6. 建议执行的验证命令
       - 在 server/ 目录运行针对 complaint_app 的测试（若已有）
       - 补充最小化的 route/service 单元测试或集成测试，覆盖：
           - 路由解析
         - 缓存命中
         - SSE 事件顺序
         - force 重跑
         - v1 不受影响

=====以下是完成时的一个阐述========


 现在的 SSE 语义变成：

  - Start：开始
  - Reasoning：固定业务文案
  - Acting：阶段动作状态
  - Answer：每个阶段的实际产出内容
    - summary 阶段返回摘要全文
    - analysis 阶段返回举报事项分析全文
    - final 阶段返回调阅材料清单与核实思路全文
  - Reference：仅保留空壳/占位
  - Done：最终聚合结果

