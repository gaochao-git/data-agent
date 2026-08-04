const TOOL_DISPLAY_NAMES: Record<string, string> = {
  search_dataset: '搜索语义模型',
  dsl2sql_tool: '生成 SQL',
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
