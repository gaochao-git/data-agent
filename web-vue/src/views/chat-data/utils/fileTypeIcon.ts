/**
 * 文件类型图标单一来源：按扩展名映射 FontAwesome 图标（含语义色）。
 * 新增文件类型先补 FILE_TYPE_ICONS，各消费方自动生效，不要再在组件里散写映射。
 *
 * 两个出口对应两种消费形态：
 * - fileIconClass(fileName)：图标 + 语义色，用于文件树等行内自着色场景
 *   （消费方：ConversationWorkspacePanel 工作区文件树）。
 * - fileIconNameByExtension(ext)：纯图标名，用于外部已指定颜色的场景，
 *   返回值不携带 text-* 色类，避免与消费方的颜色类冲突
 *   （消费方：ConversationMessageList 会话附件，外置品牌蓝）。
 *
 * 未覆盖的扩展名各自回落：行内场景回通用 file-lines 灰，附件场景回通用 file 图标。
 * PresentedFileArtifacts 的徽章式图标（白图标 + 彩底、按 artifactType 组织）是另一种
 * 视觉语言，不并入本映射。
 */

export interface FileTypeIcon {
  icon: string
  color: string
}

const FILE_TYPE_ICONS: Record<string, FileTypeIcon> = {
  json: { icon: 'fa-solid fa-file-code', color: 'text-sky-600' },
  yaml: { icon: 'fa-solid fa-file-code', color: 'text-emerald-600' },
  yml: { icon: 'fa-solid fa-file-code', color: 'text-emerald-600' },
  md: { icon: 'fa-brands fa-markdown', color: 'text-slate-600' },
  txt: { icon: 'fa-regular fa-file-lines', color: 'text-slate-400' },
  sql: { icon: 'fa-solid fa-database', color: 'text-amber-600' },
  pdf: { icon: 'fa-solid fa-file-pdf', color: 'text-red-500' },
  doc: { icon: 'fa-regular fa-file-word', color: 'text-blue-600' },
  docx: { icon: 'fa-regular fa-file-word', color: 'text-blue-600' },
  xls: { icon: 'fa-regular fa-file-excel', color: 'text-emerald-600' },
  xlsx: { icon: 'fa-regular fa-file-excel', color: 'text-emerald-600' },
  csv: { icon: 'fa-solid fa-file-csv', color: 'text-emerald-600' },
  ppt: { icon: 'fa-regular fa-file-powerpoint', color: 'text-orange-500' },
  pptx: { icon: 'fa-regular fa-file-powerpoint', color: 'text-orange-500' },
  png: { icon: 'fa-regular fa-file-image', color: 'text-purple-500' },
  jpg: { icon: 'fa-regular fa-file-image', color: 'text-purple-500' },
  jpeg: { icon: 'fa-regular fa-file-image', color: 'text-purple-500' },
  gif: { icon: 'fa-regular fa-file-image', color: 'text-purple-500' },
  webp: { icon: 'fa-regular fa-file-image', color: 'text-purple-500' },
  svg: { icon: 'fa-regular fa-file-image', color: 'text-purple-500' },
  html: { icon: 'fa-brands fa-html5', color: 'text-orange-600' }
}

/** 取扩展名（小写）；无扩展名返回空串。隐藏文件（.env 等）取「env」同样走映射回落，无碍。 */
export const extractFileExtension = (name: string): string => {
  if (!name) return ''
  const dot = name.lastIndexOf('.')
  if (dot <= -1 || dot === name.length - 1) return ''
  return name.slice(dot + 1).toLowerCase()
}

/** 行内自着色场景：文件名 → 图标 + 语义色类；未识别类型回通用 file-lines 灰。 */
export const fileIconClass = (fileName: string): string => {
  const hit = FILE_TYPE_ICONS[extractFileExtension(fileName)]
  return hit ? `${hit.icon} ${hit.color}` : 'fa-regular fa-file-lines text-slate-400'
}

/** 外部已指定颜色场景：扩展名 → 纯图标名（不带颜色类）；未识别类型回通用 file 图标。 */
export const fileIconNameByExtension = (extension?: string): string => {
  const hit = FILE_TYPE_ICONS[String(extension || '').toLowerCase()]
  return hit?.icon || 'fa-regular fa-file'
}
