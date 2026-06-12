/**
 * 检查报告撰写 - 智能体配置
 */

import type { Agent } from "../types";
import { InspectionEmbedded } from "./components";

export const inspectionReportJcbg: Agent = {
  id: "inspection-report-jcbg",
  name: "检查报告撰写",
  description: "自动撰写执法检查报告",
  category: "inspection",
  icon: "fa-file-lines",
  color: "purple",
  displayMode: "inline",
  navigatePath: "/report_jcbg",
  guide: {
    title: "检查报告撰写",
    description: "上传事实认定书，自动生成规范的执法检查报告",
    icon: "fa-file-lines",
    iconColor: "purple",
  },
  component: InspectionEmbedded,
  componentProps: {
    agentId: "report_jcbg",
  },
};
