/**
 * 控制台「工作区文件」目录树的折叠集合运算。
 *
 * 折叠状态用「已折叠路径的 Set」表示：集合里有的路径收起，没有的展开。
 * 顶层目录的路径就是目录名（uploads / workspace / outputs），更深层节点是 'a/b' 形式的完整相对路径。
 * 其余树逻辑（建树、展平可见行）仍留在 AnalysisConversation.vue，这里只放与折叠集合有关的纯函数，
 * 便于单测覆盖「一键全折叠 / 全展开」的边界（文件节点、空目录、已有默认折叠项）。
 */

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
