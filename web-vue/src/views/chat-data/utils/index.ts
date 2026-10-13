/**
 * ChatData 工具函数导出
 */

export { readLocalStorage, writeLocalStorage } from './localStorage'
export {
  SIDEBAR_TAB_STORAGE_KEY,
  defaultSidebarTab,
  readSidebarTabMemory,
  rememberSidebarTab,
  resolveSidebarTab,
} from './sidebarTab'
export type { SidebarTabMemory } from './sidebarTab'
export { cleanDisplayError, dedupeRepeatedErrorText, normalizeErrorKey, stripAnsiErrorText } from './errorText'
export { normalizeMarkdownFences, renderMarkdown } from './markdown'
export { renderMermaidBlocks } from './mermaid'
export { parseStructuredContent } from './parseContent'
export { formatSemanticConfigOptionLabel, formatSemanticConfigSummary, getSemanticConfigTableCount, getSemanticConfigsTableCount } from './semanticConfig'
export { getSkillCreatorLabel, getSkillCreatorShortLabel } from './skill'
export {
  BINARY_PREVIEW_EXTENSIONS,
  IMAGE_PREVIEW_EXTENSIONS,
  IMAGE_PREVIEW_MIME,
  OFFICE_HTML_PREVIEW_EXTENSIONS,
  PDF_PREVIEW_EXTENSIONS,
  getPreviewFileKind,
  isBinaryPreviewPath,
} from './filePreview'
export type { PreviewFileKind } from './filePreview'
export { getToolDisplayName } from './toolDisplay'
export { getSafeNumericColumns, isNumericValue, isSafeNumericValue, toSafeNumber } from './numeric'
export {
  QUERY_MEMBER_ID_COLUMN,
  QUERY_MEMBER_NAME_COLUMN,
  flattenQueryResultForDisplay,
  isQueryFailed,
  isQuerySucceeded,
  normalizeQueryStatus,
} from './queryResult'
export { buildRecentDaysDateTimeRange, formatDateTimeValue, formatLocalDateTime, formatEpochDateTime, formatRelativeConversationTime, parseDateTimeValue } from './dateTime'
export {
  SQL_EXPRESSION_FUNCTION_KEYWORDS,
  SQL_FUNCTION_GROUPS,
  buildMetricExpression,
  buildSqlFunctionExpression,
  getSqlFunctionCatalog,
  getSqlFunctionOptions,
  quoteIdentifier,
} from './sqlFunctionCatalog'
export {
  getRelationshipConditions,
  normalizeSemanticRelationship,
  syncRelationshipColumnsFromConditions,
} from './semanticRelationship'
export {
  buildSemanticParamsFromDsl,
  extractGeneratedSqlFromToolCall,
  extractQueryResultPayload,
  extractSemanticDslFromArgs,
  extractSemanticParamsFromArgs,
  parseJsonObject,
} from './semanticTool'
export type { ContentSegment, ParsedContent } from './parseContent'
export type { SqlFunctionCatalogItem, SqlFunctionGroupKey, SqlFunctionOption } from './sqlFunctionCatalog'
export { computeLiveGenerationSpeed, estimateTokensFromText, formatGenerationSpeed, formatGenerationStatsDetail, toGenerationStats } from './generationSpeed'
