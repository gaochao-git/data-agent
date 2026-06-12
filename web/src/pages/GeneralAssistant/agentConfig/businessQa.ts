/**
 * 业务知识问答 - 智能体配置
 */

import type { Agent } from "../types";
import { ExpertEmbedded } from "./components";

export const businessQa: Agent = {
  id: "business-qa",
  name: "业务知识问答",
  description: "ACS知识问答、清算知识问答、账户知识问答、支付知识问答、支付机构知识问答",
  category: "expert",
  icon: "fa-brain",
  color: "teal",
  displayMode: "inline",
  navigatePath: "/business-qa",
  component: ExpertEmbedded,
  componentProps: {
    agentId: "business-qa",
    toolType: "business-qa",
  },
};
