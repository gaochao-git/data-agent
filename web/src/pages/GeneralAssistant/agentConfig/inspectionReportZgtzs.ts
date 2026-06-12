/**
 * 整改通知书撰写 - 智能体配置
 */

import type { Agent } from "../types";
import { InspectionEmbedded } from "./components";

export const inspectionReportZgtzs: Agent = {
  id: "inspection-report-zgtzs",
  name: "整改通知书撰写",
  description: "自动撰写整改通知书",
  category: "inspection",
  icon: "fa-file-contract",
  color: "indigo",
  displayMode: "inline",
  navigatePath: "/report_zgtzs",
  guide: {
    title: "整改通知书撰写",
    description: "上传检查报告，自动生成整改通知书",
    icon: "fa-file-contract",
    iconColor: "indigo",
  },
  component: InspectionEmbedded,
  componentProps: {
    agentId: "report_zgtzs",
  },
};
