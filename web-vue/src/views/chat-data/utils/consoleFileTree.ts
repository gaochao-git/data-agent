/**
 * 控制台「工作区文件」目录树的折叠集合运算。
 *
 * 折叠状态用「已折叠路径的 Set」表示：集合里有的路径收起，没有的展开。
 * 顶层目录的路径就是目录名（uploads / workspace / outputs），更深层节点是 'a/b' 形式的完整相对路径。
 * 其余树逻辑（建树、展平可见行）仍留在 AnalysisConversation.vue，这里只放与折叠集合有关的纯函数，
 * 便于单测覆盖「一键全折叠 / 全展开」的边界（文件节点、空目录、已有默认折叠项）。
 */

/**
 * 面板首次打开时默认收起的顶层目录。
 *
 * 折叠策略对齐「先看结果」的阅读习惯，三个目录各按角色区分：
 * - `outputs`（智能体产出）**默认展开**——它是用户真正要看的东西，面板一打开就该看见成效；
 * - `workspace`（智能体中间文件，动辄几十个）默认收起——过程产物只在下钻排查时才需要；
 * - `uploads`（用户自己刚上传的文件）默认收起——内容用户本就知道，展开反而挤占结果的空间。
 * 只作为折叠集合的**初始值**：用户手动点开的目录不会被后续刷新重置；
 * 更深的子目录（如 `outputs/月报`）不在集合里，父级展开后即默认可见。
 */
export const DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES: readonly string[] = ['uploads', 'workspace']

export interface FileTreeCollapseNode {
  /** 顶层目录为目录名，更深层节点为 'a/b' 形式的完整相对路径 */
  path: string
  children: FileTreeCollapseNode[]
  /** 带 file 的节点是文件而不是目录 */
  file?: unknown
}

/**
 * 收集树里所有「可折叠目录」的路径。
 *
 * 文件节点与没有子节点的空目录不算——把它们塞进折叠集合没有可见效果，
 * 还会让「是否已全部折叠」的判断失真（空目录永远无法体现出折叠状态）。
 */
export function collectCollapsibleDirectoryPaths(nodes: FileTreeCollapseNode[]): string[] {
  const paths: string[] = []
  for (const node of nodes) {
    if (node.file || node.children.length === 0) continue
    paths.push(node.path)
    paths.push(...collectCollapsibleDirectoryPaths(node.children))
  }
  return paths
}

/**
 * 全部折叠：并入所有可折叠目录路径。
 *
 * 只做并集、不清空集合，因此集合里原有的条目（例如首次进入时默认折叠的 uploads）保持折叠，
 * 不会因为「点了全折叠」而被意外展开。
 */
export function collapseAllDirectories(
  paths: readonly string[],
  collapsed: ReadonlySet<string>,
): Set<string> {
  const next = new Set(collapsed)
  paths.forEach((path) => next.add(path))
  return next
}

/**
 * 全部展开：从集合里移除所有可折叠目录路径。
 *
 * 只移除本棵树上真实存在的路径，集合里其它条目原样保留——它们不对应当前渲染出的目录，
 * 清掉只会让后续重新出现同名目录时丢失折叠意图。
 */
export function expandAllDirectories(
  paths: readonly string[],
  collapsed: ReadonlySet<string>,
): Set<string> {
  const next = new Set(collapsed)
  paths.forEach((path) => next.delete(path))
  return next
}

/**
 * 是否已全部折叠——用于决定按钮落在「折叠全部」还是「展开全部」。
 *
 * 没有可折叠目录时返回 false（按钮不渲染展开态），避免空文件列表下显示成「展开全部」。
 */
export function isTreeFullyCollapsed(
  paths: readonly string[],
  collapsed: ReadonlySet<string>,
): boolean {
  return paths.length > 0 && paths.every((path) => collapsed.has(path))
}

/**
 * 系统结果目录：`outputs/query_result`（SQL 查询结果）与 `outputs/.tool-offload`（工具结果卸载缓存）。
 *
 * 两者都是「原始数据 + 审计留存」：放开下载等于绕过问数的权限边界，会造成数据泄漏；
 * 删除则会让审计链路失去凭证。后端 `is_system_result_workspace_path` 已在「文件列表」
 * 「raw 读取」与删除接口三侧拦截，这些路径本就不会出现在文件树里；
 * 这里是前端侧的纵深防御，防止将来任一侧放开后静默失效。
 */
export const SYSTEM_RESULT_WORKSPACE_PREFIXES = [
  'outputs/query_result',
  'outputs/.tool-offload',
] as const

/** 下载侧沿用的历史导出名（语义等价于系统结果目录）。 */
export const NON_DOWNLOADABLE_WORKSPACE_PREFIXES = SYSTEM_RESULT_WORKSPACE_PREFIXES

/** 把相对路径、带沙箱前缀的可见路径或 Windows 分隔符写法统一成 `outputs/...` 形式。 */
function normalizeWorkspaceRelativePath(relativePath: string | null | undefined): string {
  return String(relativePath || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\/mnt\/user-data\/?/, '')
    .replace(/^\/+/, '')
}

function isSystemResultWorkspacePath(relativePath: string | null | undefined): boolean {
  const path = normalizeWorkspaceRelativePath(relativePath)
  if (!path) return false
  return SYSTEM_RESULT_WORKSPACE_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  )
}

/** 判断工作区文件是否提供下载入口。空路径视为不可下载（保守取值）。 */
export function isWorkspaceFileDownloadable(relativePath: string | null | undefined): boolean {
  return Boolean(normalizeWorkspaceRelativePath(relativePath)) && !isSystemResultWorkspacePath(relativePath)
}

/**
 * 判断工作区文件是否提供删除入口。
 *
 * 目前与下载共用同一组排除目录，但语义独立：下载被拦是防泄漏，删除被拦是保审计留存。
 * 两者将来分化（例如允许下载但不允许删）时改各自的函数即可，不必动常量。
 */
export function isWorkspaceFileDeletable(relativePath: string | null | undefined): boolean {
  return Boolean(normalizeWorkspaceRelativePath(relativePath)) && !isSystemResultWorkspacePath(relativePath)
}

/**
 * 悬浮时给「被截断的行名」挂上原生 title 提示，让用户能看到完整文件名。
 *
 * 只在真的被截断时才挂：常显 title 会让「测试.sql」这种本来就看得全的名字也弹一次提示，属于噪音；
 * 而按字符数猜长度在中英文混排 + 等宽字体下必然误判，所以这里在悬浮（此刻布局已完成）时用
 * scrollWidth/clientWidth 实测——截断来自 `truncate` 的 overflow:hidden，被截断时 scrollWidth 就是整段文字的宽度。
 * 反向（未截断）时移除 title，避免面板拉宽、换会话后留下过期的长提示。
 */
export function syncTruncatedTitle(event: Event): void {
  const el = event.currentTarget as HTMLElement | null
  if (!el) return
  const text = (el.textContent || '').trim()
  if (text && el.scrollWidth > el.clientWidth) {
    el.setAttribute('title', text)
  } else {
    el.removeAttribute('title')
  }
}

/** 文件大小的单位阶梯，按 1024 进位（与后端 `st_size` 的字节口径一致）。 */
const FILE_SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const

/**
 * 把后端返回的字节数格式化成行尾展示用的短文本（如 `12.3 KB`）。
 *
 * - 1024 进位；不足 1 KB 直接给整数字节，避免出现「0.5 KB」这种比字节还难读的写法。
 * - 只有 1 位小数：面板行宽有限，且大小是扫读信息，不需要更细的精度；整数倍（1.0 MB）省掉 `.0`。
 * - ≥100 时取整：1023.9 KB 这类值的小数位没有信息量，保留反而让数字列宽度抖动。
 * - 负数、NaN、空值返回空串，调用方据此不渲染（拿不到 size 的老接口不该显示成 0 B）。
 */
export function formatFileSize(bytes: number | null | undefined): string {
  // 空值必须先挡掉：Number(null) 是 0，直接透传给 Number 会把「没有大小」显示成「0 B」
  if (bytes === null || bytes === undefined) return ''
  const value = Number(bytes)
  if (!Number.isFinite(value) || value < 0) return ''
  if (value < 1024) return `${Math.round(value)} B`

  let scaled = value
  let unitIndex = 0
  while (scaled >= 1024 && unitIndex < FILE_SIZE_UNITS.length - 1) {
    scaled /= 1024
    unitIndex += 1
  }
  if (scaled >= 100) return `${Math.round(scaled)} ${FILE_SIZE_UNITS[unitIndex]}`
  const text = scaled.toFixed(1).replace(/\.0$/, '')
  return `${text} ${FILE_SIZE_UNITS[unitIndex]}`
}
