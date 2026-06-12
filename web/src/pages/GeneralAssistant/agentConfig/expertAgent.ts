/**
 * 法律检索 - 智能体配置
 */

import type { Agent } from "../types";
import { UnifiedEmbedded } from "./components";

export const expertAgent: Agent = {
  id: "expert_agent",
  name: "法律检索",
  description: "专业法律条文检索和查询",
  category: "expert",
  icon: "fa-search",
  color: "green",
  displayMode: "inline",
  navigatePath: "/expert",
  opening_message: {
    title: "法律检索智能体",
    icon: "fa-search",
    content: "本智能体集成600余部国家法律、行政法规、部门规章及规范性文件，构建智能法律检索与分析系统，为用户提供便捷的法律法规信息服务。\n<b>- 全文检索：</b>支持对全部法律法规检索，用户输入关键词后可快速定位相关条文及出处，提升法律法规检索效率。\n<b>- 案例分析：</b>针对支付结算等典型领域的违法违规案例，智能体能够结合法律法规进行解析，辅助用户理解法律适用逻辑，提升案件研判质量。\n<b>- 条款检索：</b>提供法律条款查询服务，用户通过输入条款内容片段，可即时获取条款全文、生效时间等关键信息，确保法律适用的准确性与时效性。",
    prompts: [
      "帮我找与“银行卡违规发卡”相关的法律条款",
      "梳理法人履职风险涉及的法律责任",
      "列出涉及反洗钱义务的主要法规",
    ],
  },
  input_actions: [
    { id: "deepseek-r1", label: "DeepSeek-R1", kind: "model", defaultSelected: true },
    { id: "deep-think", label: "深度思考", kind: "toggle", icon: "deep-think", defaultSelected: true },
    // { id: "legal-search", label: "法律检索", kind: "custom", defaultSelected: true },
  ],
  input_controls: {
    upload: false,
  },
  input_placeholder: "描述您的业务情景，我将从专业角度为您解答。",
  footer_tag: "法律检索",
  assistant_role_label: "AI",
  mock_messages: [
    {
      message_id: "e-1",
      role: "user",
      text: "法人履职风险包含哪些关键点？",
      time: "17:54:20",
    },
  ],
  mock_sse_events: [
    {
      event: "chat_message",
      data: { event: "message_start", response_id: "r1", message_id: "e-2", created: 1 },
    },
    {
      event: "chat_message",
      data: { event: "node_start", response_id: "r1", message_id: "e-2", node_id: "n1", type: "thinking", title: "规划完成", created: 2 },
    },
    {
      event: "chat_message",
      data: { event: "node_end", response_id: "r1", message_id: "e-2", node_id: "n1", status: "completed", title: "规划完成", created: 3 },
    },
    {
      event: "chat_message",
      data: { event: "node_start", response_id: "r1", message_id: "e-2", node_id: "n2", type: "tool", title: "法律检索", created: 4 },
    },
    {
      event: "chat_message",
      data: { event: "node_end", response_id: "r1", message_id: "e-2", node_id: "n2", status: "completed", title: "法律检索", created: 5 },
    },
    {
      event: "chat_message",
      data: { event: "node_start", response_id: "r1", message_id: "e-2", node_id: "n3", type: "thinking", title: "综合分析思考", created: 6 },
    },
    {
      event: "chat_message",
      data: {
        event: "content_delta",
        response_id: "r1",
        message_id: "e-2",
        node_id: null,
        index: 0,
        delta:
          "结合法规检索结果，法人履职风险主要集中在公司独立责任边界、重大经营决策的勤勉义务与对外侵权责任三类情形。建议优先核验公司治理文件与相关授权流程，避免因程序瑕疵导致责任扩张。",
        role: "assistant",
        created: 7,
        metadata: { channel: "final" },
      },
    },
    {
      event: "chat_message",
      data: { event: "message_end", response_id: "r1", message_id: "e-2", created: 8 },
    },
  ],
  mock_assistant_meta: {
    references: [
      {
        id: "ref-1",
        title: "《中华人民共和国网络安全法》 · 第二十一条",
        summary:
          `条款要义是国家实行网络安全等级保护制度，网络运营者应按等级保护要求履行安全保护义务（制度层面的核心条款之一）。原文摘录（短句）："国家实行网络安全等级保护制度"。`,
      },
      {
        id: "ref-2",
        title: "《中华人民共和国民法典》 · 第一百一十一条",
        summary:
          `条款要义是明确"自然人的个人信息受法律保护"，并要求获取他人个人信息应依法取得、确保信息安全，不得非法收集、使用、加工、传输等。原文摘录（短句）："自然人的个人信息受法律保护"。`,
      },
    ],
    suggestions: [
      { tool_id: "view_regulation", name: "查看条款原文" },
      { tool_id: "regulation_summary", name: "生成法规摘要" },
      { tool_id: "compliance_advice", name: "生成合规建议" },
    ],
  },
  component: UnifiedEmbedded,
  componentProps: {
    agentId: "expert_agent",
    toolType: "legal-search",
  },
  workbench: {
    enabled: true,
    variant: "expert",
  },
};
