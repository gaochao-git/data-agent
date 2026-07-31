const TOOL_DISPLAY_NAMES: Record<string, string> = {
  select_semantic_config_tool: '选择指标集',
  dsl2sql_tool: '生成 SQL',
  semantic_query_tool: '查询数据',
  calculator_tool: '精确计算',
  generate_chart_tool: '生成图表',
  skill_load: '加载技能',
  load_skill_tool: '普通对话',
  file_read: '读取文件',
  file_glob: '查找文件',
  file_grep: '搜索文本',
  file_edit: '修改文件',
  file_write: '写入文件',
  file_move: '移动文件',
  file_delete: '删除文件',
  dir_create: '创建目录',
  dir_list: '列出目录',
  dir_delete: '删除目录',
  bash_run: '运行脚本',
  get_current_time: '获取当前时间',
  present_artifacts: '展示文件',
  output_artifact: '生成文件',
  semantic_dsl_to_sql: 'DSL 转 SQL',
  dashboard_spec_validate: '校验看板',
  dashboard_write: '写入看板',
}

export function getToolDisplayName(toolName?: string | null): string {
  const name = String(toolName || '').trim()
  return TOOL_DISPLAY_NAMES[name] || '工具调用'
}
