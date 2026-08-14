const TOOL_DISPLAY_NAMES: Record<string, string> = {
  semantic_search: '语义检索',
  search_dataset: '检索语义模型',
  dsl2sql_tool: '编译 SQL',
  semantic_query_tool: '查询数据',
  calculator_tool: '精确计算',
  file_read: '读取文件',
  file_glob: '查找文件',
  file_grep: '搜索文本',
  file_edit: '修改文件',
  file_write: '写入文件',
  file_move: '移动文件',
  file_copy: '复制文件',
  file_delete: '删除文件',
  parse_document: '解析文档',
  dir_create: '创建目录',
  dir_list: '列出目录',
  dir_delete: '删除目录',
  bash_run: '运行脚本',
  officecli_run: '运行 OfficeCLI',
  get_current_time: '获取当前时间',
  present_artifacts: '展示文件',
}

export function getToolDisplayName(toolName?: string | null): string {
  const name = String(toolName || '').trim()
  return TOOL_DISPLAY_NAMES[name] || '工具调用'
}

const normalizeVisiblePath = (value: unknown): string => (
  String(value || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/\/+/g, '/')
)

export function getToolCompactDescription(
  toolName?: string | null,
  args: Record<string, unknown> = {},
): string {
  const name = String(toolName || '').trim()
  if (name === 'search_dataset') {
    return String(args.query || '').trim()
  }
  if (name === 'file_read' || name === 'file_edit' || name === 'file_write' || name === 'file_delete') {
    return normalizeVisiblePath(args.file_path || args.path)
  }
  if (name === 'file_move' || name === 'file_copy') {
    const source = normalizeVisiblePath(args.source_path)
    const target = normalizeVisiblePath(args.target_path || args.destination_path)
    return source && target ? `${source} -> ${target}` : source || target
  }
  if (name === 'present_artifacts') {
    const files = Array.isArray(args.files)
      ? args.files.map(normalizeVisiblePath).filter(Boolean)
      : []
    if (files.length === 0) return ''
    return files.length === 1 ? files[0]! : `${files[0]} 等 ${files.length} 个文件`
  }
  if (name === 'semantic_query_tool') {
    return normalizeVisiblePath(args.save_as)
  }
  return ''
}
