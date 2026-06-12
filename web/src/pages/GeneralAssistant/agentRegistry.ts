/**
 * 智能体注册配置（主入口）
 *
 * 新增智能体卡片（每个卡片即一个智能体）：
 * 1. 在 agentConfig/ 目录下新建配置文件（参考现有文件格式）
 * 2. 在下方导入并添加到 AGENTS 数组
 */

import type { AgentCategory, Agent } from "./types";

// 投诉举报类
import { complaintAgent } from "./agentConfig/complaintAgent";

// 执法检查类
import { inspectionFactAnalysis } from "./agentConfig/inspectionFactAnalysis";
import { inspectionReportJcbg } from "./agentConfig/inspectionReportJcbg";
import { inspectionReportZgtzs } from "./agentConfig/inspectionReportZgtzs";
import { inspectionReportZjml } from "./agentConfig/inspectionReportZjml";
import { inspectionReportXxjb } from "./agentConfig/inspectionReportXxjb";

// 风险监测类
import { chatData } from "./agentConfig/chatData";

// 专家系统类
import { expertAgent } from "./agentConfig/expertAgent";

// ============================================================
// 智能体分类
// ============================================================

export const AGENT_CATEGORIES: AgentCategory[] = [
  { id: "all", name: "全部", icon: "fa-solid fa-grip" },
  { id: "complaint", name: "投诉举报", icon: "fa-solid fa-envelope-open-text", description: "智能分析投诉举报，快速生成回复建议" },
  { id: "inspection", name: "执法检查", icon: "fa-solid fa-gavel", description: "辅助事实认定分析，自动生成检查报告、整改通知书、证据目录、信息简表" },
  { id: "risk", name: "风险监测", icon: "fa-solid fa-chart-line", description: "自然语言生成SQL，智能查询数据、生成图表" },
  { id: "expert", name: "专家系统", icon: "fa-solid fa-user-tie", description: "提供专业法律咨询，检索相似案例" }
];

// ============================================================
// 智能体列表（顺序决定显示顺序）
// ============================================================

export const AGENTS: Agent[] = [
  // 投诉举报类
  complaintAgent,

  // 执法检查类
  inspectionFactAnalysis,
  inspectionReportJcbg,
  inspectionReportZgtzs,
  inspectionReportZjml,
  inspectionReportXxjb,

  // 风险监测类
  chatData,

  // 专家系统类
  expertAgent,
];

// ============================================================
// 辅助函数
// ============================================================

/**
 * 根据 ID 获取智能体配置
 */
export function getAgentById(agentId: string): Agent | undefined {
  return AGENTS.find(agent => agent.id === agentId);
}

/**
 * 根据分类获取智能体列表
 */
export function getAgentsByCategory(categoryId: string): Agent[] {
  if (categoryId === "all") {
    return AGENTS;
  }
  return AGENTS.filter(agent => agent.category === categoryId);
}
