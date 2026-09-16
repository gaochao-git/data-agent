/**
 * 文件预览类型常量（唯一来源）。
 *
 * 主对话工作区预览（AnalysisConversation.vue）与技能管理预览（SkillManager.vue）
 * 共用这份定义：新增可预览类型只改这里，两处行为自动保持一致。
 * 后端对应的预览能力：工作区 raw / office-html 端点，技能版本 files/raw / files/office-preview 端点。
 */

/** 可用 <img> 直接预览的图片扩展名。 */
export const IMAGE_PREVIEW_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'])

/** 交给后端 OfficeCLI 渲染为 HTML 预览的 Office 扩展名（与后端 _render_local_office_html 支持集一致）。 */
export const OFFICE_HTML_PREVIEW_EXTENSIONS = new Set(['docx', 'xlsx', 'pptx'])

/** 图片扩展名 → blob MIME。raw 接口返回的 blob.type 缺失时兜底。 */
export const IMAGE_PREVIEW_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  svg: 'image/svg+xml',
}

/** 可用 <iframe src=blobUrl> 原生预览的扩展名。 */
export const PDF_PREVIEW_EXTENSIONS = new Set(['pdf'])

/** 全部走二进制（blob / 服务端渲染）通道、不进文本预览的扩展名。 */
export const BINARY_PREVIEW_EXTENSIONS = new Set(['pdf', ...OFFICE_HTML_PREVIEW_EXTENSIONS, ...IMAGE_PREVIEW_EXTENSIONS])

export type PreviewFileKind = 'image' | 'pdf' | 'office' | 'text'

/** 按文件名扩展名判定预览方式。 */
export function getPreviewFileKind(path: string): PreviewFileKind {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  if (PDF_PREVIEW_EXTENSIONS.has(ext)) return 'pdf'
  if (IMAGE_PREVIEW_EXTENSIONS.has(ext)) return 'image'
  if (OFFICE_HTML_PREVIEW_EXTENSIONS.has(ext)) return 'office'
  return 'text'
}

export const isBinaryPreviewPath = (path: string) => getPreviewFileKind(path) !== 'text'
