/**
 * 控制台「工作区文件」目录树的折叠集合运算与建树逻辑。
 *
 * 折叠状态用「已折叠路径的 Set」表示：集合里有的路径收起，没有的展开。
 * 顶层目录的路径就是目录名（uploads / workspace / outputs），更深层节点是 'a/b' 形式的完整相对路径。
 * 折叠集合的纯函数与建树/展平均在这里，便于单测覆盖「一键全折叠 / 全展开」的边界
 * （文件节点、空目录、已有默认折叠项）；组件只负责响应式装配与交互。
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
 * 支持打包下载的顶层目录。
 *
 * 用户拍板（2026-09-19）先只放开 outputs：它是「智能体产出」的汇聚地，是打包下载
 * 的真实诉求；workspace 是过程中间文件、uploads 内容用户本就有原件，暂不放开。
 * 2026-09-24 追加放开 outputs 下的**任意层级子目录**（顶层范围不变）。
 * 后端 archive 接口按 WORKSPACE_DIR_NAMES 校验路径首段并拒绝 `..` 逃逸，打包内容仍走
 * list_workspace_files 的可见清单——系统结果目录（query_result / .tool-offload）
 * 即便在 outputs 下也不会进包，与单文件下载同一防泄漏口径。
 */
export const ARCHIVE_DOWNLOADABLE_DIRECTORIES: readonly string[] = ['outputs']

/**
 * 判断目录是否提供打包下载入口：顶层目录名或其下子目录路径（如 `outputs/月报`）都算。
 *
 * 系统结果目录即便在 outputs 下也拒绝：包里不会有任何文件，给了入口只会得到一个空包报错。
 */
export function isWorkspaceDirectoryArchiveDownloadable(directoryPath: string | null | undefined): boolean {
  const path = normalizeWorkspaceRelativePath(directoryPath)
  if (!path || isSystemResultWorkspacePath(path)) return false
  return ARCHIVE_DOWNLOADABLE_DIRECTORIES.includes(path.split('/')[0] ?? '')
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

// ---------------------------------------------------------------------------
// 工作区文件树构建（从 conversation/Conversation.vue 平移，纯函数 + 类型）
// ---------------------------------------------------------------------------

/** 工作区文件面板里的一个文件条目（由会话工作区文件列表接口聚合而来）。 */
export interface ProducedFile {
  path: string
  fullPath: string
  relativePath: string
  displayPath: string
  source: string
  status: string
  /** 字节数，来自工作区列表接口的 st_size；行尾展示用，拿不到时为 0 且不渲染 */
  size: number
  /** 修改时间（秒级 Unix 时间戳，后端 st_mtime）；行尾展示用，拿不到时为 0 且不渲染 */
  mtime: number
}

export interface ProducedFileTreeNode {
  name: string
  path: string
  label?: string
  depth: number
  file?: ProducedFile
  children: ProducedFileTreeNode[]
}

export interface VisibleProducedFileTreeNode {
  node: ProducedFileTreeNode
  depth: number
  isLast: boolean
}

export interface ProducedFileDirectory extends ProducedFileTreeNode {
  label: string
}

/** 面板固定展示的四个顶层目录（顺序即渲染顺序）。 */
export const PRODUCED_FILE_DIRECTORIES: Array<{ name: string; label: string }> = [
  { name: 'uploads', label: '用户上传文件' },
  { name: 'workspace', label: '智能体中间文件' },
  { name: 'outputs', label: '智能体输出文件' },
  { name: 'memory', label: '会话记忆' },
]

/**
 * 把任意路径写法归一化成 `uploads/...` 形式的可见相对路径：
 * 剥掉 `/mnt/user-data` 前缀、统一分隔符、去多余斜杠。
 */
export function normalizeVisiblePath(path: unknown): string {
  return normalizeWorkspaceRelativePath(String(path ?? ''))
}

/** 可见相对路径 → 沙箱内展示路径（`/mnt/user-data/<relative>`），空路径回退根目录。 */
export function toSandboxDisplayPath(path: unknown): string {
  const relativePath = normalizeVisiblePath(path)
  if (!relativePath) return '/mnt/user-data'
  return `/mnt/user-data/${relativePath}`
}

export interface ProducedFileInput {
  /** 后端返回的相对路径（优先）或任意原始路径 */
  relative_path?: string
  path?: string
  source?: string
  size?: number | string
  mtime?: number | string
}

/** 单个文件条目聚合：归一化相对路径与沙箱展示路径，非法路径丢弃。 */
export function toProducedFile(rawPath: string, source: string, size: number, mtime: number): ProducedFile | null {
  const relativePath = normalizeVisiblePath(rawPath)
  if (!rawPath || !relativePath) return null
  return {
    path: rawPath,
    fullPath: toSandboxDisplayPath(relativePath),
    relativePath,
    displayPath: toSandboxDisplayPath(relativePath),
    source,
    status: 'completed',
    size,
    mtime,
  }
}

/** 把工作区文件列表聚合成「文件条目列表」：去重，且只保留三个固定目录的路径（与平移前口径一致）。 */
export function buildProducedFiles(files: ProducedFileInput[]): ProducedFile[] {
  const produced: ProducedFile[] = []
  const seenPaths = new Set<string>()
  for (const file of files) {
    const rawPath = file.relative_path || file.path || ''
    const relativePath = normalizeVisiblePath(rawPath)
    if (!relativePath || seenPaths.has(relativePath)) continue
    const rootDirectory = relativePath.split('/')[0] || ''
    if (!PRODUCED_FILE_DIRECTORIES.some(directory => directory.name === rootDirectory)) continue
    const item = toProducedFile(rawPath, file.source || 'workspace', Number(file.size) || 0, Number(file.mtime) || 0)
    if (!item) continue
    seenPaths.add(relativePath)
    produced.push(item)
  }
  return produced
}

/** 同级节点排序：目录在前、文件在后，各自按名称升序（码点序，与后端 Path 排序口径一致）。 */
function sortTreeNodes(nodes: ProducedFileTreeNode[]): ProducedFileTreeNode[] {
  const byName = (a: ProducedFileTreeNode, b: ProducedFileTreeNode) =>
    a.name < b.name ? -1 : a.name > b.name ? 1 : 0
  const directories = nodes.filter(node => !node.file).sort(byName)
  const files = nodes.filter(node => node.file).sort(byName)
  directories.forEach((node) => { node.children = sortTreeNodes(node.children) })
  return [...directories, ...files]
}

/**
 * 把文件条目按相对路径挂到三个固定顶层目录下，逐级建立中间目录节点。
 * 固定目录常驻（空也有上传入口等行级交互），额外顶层归入「其他文件」；
 * 每级子节点按「目录在上、文件在下」排序（顶层固定目录的常驻顺序不受影响）。
 */
export function buildProducedFileTree(files: ProducedFile[]): {
  root: string
  directories: ProducedFileDirectory[]
} {
  const directoryMap = new Map<string, ProducedFileDirectory>()
  PRODUCED_FILE_DIRECTORIES.forEach((directory) => {
    directoryMap.set(directory.name, {
      ...directory,
      path: directory.name,
      depth: 0,
      children: [],
    })
  })

  for (const file of files) {
    const relativePath = normalizeVisiblePath(file.path)
    if (!relativePath) continue
    const [directoryName, ...restParts] = relativePath.split('/').filter(Boolean)
    if (!directoryName) continue
    if (!directoryMap.has(directoryName)) {
      directoryMap.set(directoryName, {
        name: directoryName,
        label: '其他文件',
        path: directoryName,
        depth: 0,
        children: [],
      })
    }
    const rootDirectory = directoryMap.get(directoryName)!
    let current: ProducedFileTreeNode = rootDirectory
    restParts.forEach((part, index) => {
      const nodePath = [directoryName, ...restParts.slice(0, index + 1)].join('/')
      const isFile = index === restParts.length - 1
      let child = current.children.find(item => item.name === part && item.path === nodePath)
      if (!child) {
        child = {
          name: part,
          path: nodePath,
          depth: index + 1,
          children: [],
          ...(isFile ? { file } : {}),
        }
        current.children.push(child)
      } else if (isFile) {
        child.file = file
      }
      current = child
    })
  }

  const directories = Array.from(directoryMap.values())
  directories.forEach((directory) => { directory.children = sortTreeNodes(directory.children) })
  return {
    root: '/mnt/user-data',
    directories,
  }
}

/** 统计节点（目录或文件）子树内的文件数量。 */
export function getFileCount(node: ProducedFileTreeNode): number {
  return (node.file ? 1 : 0) + node.children.reduce((count, child) => count + getFileCount(child), 0)
}

/**
 * 展平出「可见行」序列：文件行直接输出，目录行在未折叠时递归展开子级。
 * `depth` 从 1 起（顶层目录行本身是 depth 0 的独立渲染分支）。
 */
export function flattenFileTreeNodes(
  nodes: ProducedFileTreeNode[],
  depth = 1,
  isCollapsed: (path: string) => boolean = () => false,
): VisibleProducedFileTreeNode[] {
  const visible: VisibleProducedFileTreeNode[] = []
  nodes.forEach((node, index) => {
    visible.push({ node, depth, isLast: index === nodes.length - 1 })
    if (!node.file && node.children.length && !isCollapsed(node.path)) {
      visible.push(...flattenFileTreeNodes(node.children, depth + 1, isCollapsed))
    }
  })
  return visible
}

// 文件树每级的左缩进。基准 6px 必须等于顶层目录行按钮的 px-1.5，之后每级固定 +18px——
// 两端用同一个基准，「顶层目录 → 它的子节点」这一步的跨度才和更深层一致。
// （此前基准写的是 10px：第一级跨度 22px、后面 18px，居中那一级看着就比上下两级偏右。）
export const CONSOLE_TREE_INDENT_BASE_PX = 6
export const CONSOLE_TREE_INDENT_STEP_PX = 18

export const consoleTreeIndentStyle = (depth: number) => ({
  paddingLeft: `${CONSOLE_TREE_INDENT_BASE_PX + depth * CONSOLE_TREE_INDENT_STEP_PX}px`,
})

// 行首「展开箭头 / 占位」槽位宽度。文件行用 w-3 的空 span（正好 12px），目录行的箭头是 FA 图标——
// FA 7 在 `.fa-solid/.fa-regular` 上有一条 `width: var(--fa-width, 1.25em)`，优先级与 `.w-3` 相同且更靠后，
// 所以 w-3/w-4 这类宽度类在图标上是不生效的：箭头实际宽 1.25em（text-[10px] 时为 12.5px），
// 比文件行宽 0.5px，会让「目录行」的同名列比「文件行」整体右移半像素。这里显式钉成 12px 对齐。
// 箭头字形在盒内左对齐，钉窄后字形本身不动，只有其后的文件夹图标与名字左移 0.5px。
export const CONSOLE_TREE_LEAD_SLOT_PX = 12
export const consoleTreeLeadSlotStyle = { width: `${CONSOLE_TREE_LEAD_SLOT_PX}px` }

// 行尾「修改时间 / 大小」两列。
// 时间是 YYYY-MM-DD HH:mm:ss（19 字符定长），每行天然等宽，给 shrink-0 就够，不必钉宽度；
// 大小则长度不一（「7 KB」28.8px vs「18.7 KB」44.7px），不钉宽度会把时间列的右边缘顶得忽左忽右，
// 所以按实测最大内容给固定宽度 + 右对齐（字号改动需同步复测）。
//
// 两侧还会在面板被拖窄时整体收起（见 CONSOLE_TREE_META_VISIBILITY_CLASS）：这两列合计约 167px，
// 加上 24px 缩进、33px 占位+图标、56px 行尾操作位与内边距，面板一窄就只剩名字被压成省略号。
// 用容器查询（`@container` 落在树包裹层上）而不是 JS 监听宽度：拖拽改的是行内 width，
// 容器查询会自动响应，且不必因为几像素变化就重渲染整棵树。
export const CONSOLE_TREE_META_VISIBILITY_CLASS = 'hidden @min-[400px]:block'
export const CONSOLE_TREE_TIME_COLUMN_CLASS = `shrink-0 text-[10px] tabular-nums text-slate-400 ${CONSOLE_TREE_META_VISIBILITY_CLASS}`
export const CONSOLE_TREE_SIZE_COLUMN_CLASS = `w-[46px] shrink-0 text-right text-[10px] tabular-nums text-slate-400 ${CONSOLE_TREE_META_VISIBILITY_CLASS}`
