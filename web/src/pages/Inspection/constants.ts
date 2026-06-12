import { z } from "zod";
import type { ChatMessage } from "./types";

export const violationFormSchema = z.object({
  violationDescription: z
    .string()
    .min(10, "请详细描述违规问题，至少10个字符"),
});

export const panelHeightClass =
  "h-screen";

export const HISTORY_COLLAPSED_WIDTH = "3.5rem";

export const createInitialChatMessages = (): ChatMessage[] => [];

export const requiredLabelsMap = {
  legalBasis: "违反条款",
  penaltyClauseList: "处罚条款",
  adminPenaltyStandards: "裁量基准",
  penaltySuggestions: "处罚建议",
  rectificationMeasures: "整改措施",
} as const;
