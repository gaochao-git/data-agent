/**
 * 格式化工具函数
 */

import type { LawItem } from "../types";

export const mergeClassNames = (
  ...classes: Array<string | undefined | null | false>
): string => classes.filter(Boolean).join(" ");

export const formatPromptPreview = (prompt: string, maxLength = 25) =>
  prompt.length <= maxLength ? prompt : `${prompt.slice(0, maxLength)}...`;

export const formatLawItem = (item: LawItem) => {
  const parts = [item.law_name, item.law_item_name, item.law_item_content]
    .map((value) => (value || "").trim())
    .filter(Boolean);
  return parts.join(" - ");
};

export const formatDetailItem = (item: string | Record<string, unknown>) => {
  if (typeof item === "string") {
    return item;
  }
  if (!item) {
    return "";
  }

  const pickString = (...values: unknown[]) => {
    for (const value of values) {
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
    }
    return "";
  };

  const record = item as Record<string, unknown>;
  const label = pickString(
    record["title"],
    record["name"],
    record["clause_name"],
    record["clause"],
    record["law_name"],
    record["standard_name"],
    record["label"],
  );
    const body = pickString(
    record["content"],
    record["clause_content"],
    record["description"],
    record["detail"],
    record["text"],
    record["value"],
  );

  if (label && body) {
    return `${label}：${body}`;
  }

  const collected = Object.values(record).filter(
    (value) => typeof value === "string" && value.trim().length > 0,
  ) as string[];
  if (collected.length > 0) {
    return collected.join("；");
  }

  try {
    return JSON.stringify(record);
  } catch {
    return "";
  }
};

export const toStringSafe = (value: unknown): string => {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  try {
    return JSON.stringify(value).trim();
  } catch {
    return String(value).trim();
  }
};

export const joinChineseSemicolon = (items?: Array<unknown>): string => {
  if (!Array.isArray(items) || items.length === 0) return "";
  const parts = items
    .map(toStringSafe)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return parts.join("；");
};

export const stringifyLawItems = (list?: LawItem[]): string => {
  if (!Array.isArray(list) || list.length === 0) return "";
  const parts = list
    .map((item) => {
      const lawName = String(item.law_name || "").trim();
      const lawItemName = String(item.law_item_name || "").trim();
      const lawItemContent = String(item.law_item_content || "").trim();
      const joined = [lawName, lawItemName, lawItemContent]
        .filter((s) => s.length > 0)
        .join(" ");
      return joined.trim();
    })
    .filter((s) => s.length > 0);
  return parts.join("；");
};

export const createMessageId = () =>
  `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;

export const formatTimestamp = (timestamp: Date): string =>
  timestamp.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).replace(/\//g, '-');
