文件目录在：server/apps/complaint_app/services/analysis_agent/

# 举报事项分析agent
- 输入：用户问题 +最新摘要 / 举报事项列表 （2选1）
- 输出：结构化结果（每个举报事项的分类匹配、证据/材料、法律依据、是否涉及支付结算、结论）
# 文件规划
- 包含：agent.py（主流程）、prompts.py（提示词）、schemas.py（输出结构）
# 任务流拆解（你描述的四步）
- Step1：识别举报事项
    - 若用户明确指定单项 → 用指定项
    - 否则从摘要里抽取全量事项
- Step2：经验库匹配 （find_expert_experience_for_second_category method）
    - 输出一级/二级分类 + 匹配依据 + 补齐清单/核查材料/是否涉支付结算
- Step3：法律条文分析
    - 根据匹配的法律依据检索条文，形成条款摘  要与适用判断（get_law_item 调 CASE_ANALYSIS_LAW_BASE_URL 拉条文原文。）
- Step4：综合结论 （_build_law_refine_prompt）
    - 输出结构化结论（每项一个结论块）
# 运行方式（当前阶段）
- 作为可直接运行的主程序：`python xxx.py`
- 运行时输出 SSE 数据到控制台（stdout）
- 当前阶段不定义 JSON schema
# 调用方式（后续接入）
- 单独暴露为工具/服务函数：run_complaint_analysis(...)
- 先在内部可直接调用，后续再接入 stream_chat 的意图判定分支
- SSE 输出：每步用 node_start/node_delta/node_end 推进
- 相关必要实现参考：server/apps/complaint_tools/case_analysis_tool_analyze_v2.py
# SSE 事件分块（当前约定）
- 思考块：node_start / node_delta / node_end
- 内容块：content_delta
- 建议块：suggestions
- 引用块：暂不设计（complaint 前端暂不支持）
# 失败回退策略
- 法律检索失败：保留分类与经验库结果，`法律法规及其原文` 置空
- 经验库匹配失败：仅保留分类结果，调阅清单/核查材料为空，是否支付结算默认“否”
- 分类失败：返回“其他/其他”，匹配度低，分类理由说明“无法判定”
