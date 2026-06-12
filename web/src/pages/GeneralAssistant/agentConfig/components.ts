/**
 * 动态加载的嵌入组件
 *
 * 注意：此文件独立于 agentRegistry.ts，避免循环依赖
 */

import { lazy } from "react";

// 执法检查嵌入组件
export const InspectionEmbedded = lazy(() =>
  import("../../Inspection/EmbeddedLayout").then(m => ({ default: m.EmbeddedLayout }))
);

// 投诉举报/专家系统统一嵌入组件
export const UnifiedEmbedded = lazy(() =>
  import("../UnifiedEmbeddedLayout").then(m => ({ default: m.UnifiedEmbeddedLayout }))
);

// 专家系统嵌入组件
export const ExpertEmbedded = lazy(() =>
  import("../../AISpecialistHome/EmbeddedLayout").then(m => ({ default: m.EmbeddedLayout }))
);

// 智能问数嵌入组件
export const ChatDataEmbedded = lazy(() =>
  import("../../ChatData/EmbeddedLayout").then(m => ({ default: m.EmbeddedLayout }))
);
