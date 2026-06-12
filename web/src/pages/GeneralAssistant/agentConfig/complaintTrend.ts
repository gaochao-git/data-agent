/**
 * 举报趋势分析 - 智能体配置
 */

import type { Agent } from "../types";
import { UnifiedEmbedded } from "./components";

export const complaintTrend: Agent = {
  id: "complaint-trend",
  name: "举报趋势分析",
  description: "分析举报数据趋势和规律",
  category: "complaint",
  icon: "fa-chart-line",
  color: "orange",
  displayMode: "inline",
  navigatePath: "/complaint",
  component: UnifiedEmbedded,
  componentProps: {
    agentId: "complaint_trend",
    toolType: "trend",
  },
};
