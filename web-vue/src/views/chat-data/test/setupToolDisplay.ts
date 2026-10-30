import { setToolDisplayNames } from '../utils/toolDisplay'

// 测试 fixture：模拟后端 /config 下发的 ToolDeclaration display_name。
// 生产代码不再维护第二张工具中文表。
setToolDisplayNames({
  file_read: '读取文件',
  file_edit: '修改文件',
  file_write: '写入文件',
  file_move: '移动文件',
  file_copy: '复制文件',
  file_delete: '删除文件',
  file_glob: '查找文件',
  file_grep: '搜索文本',
  file_diff: '对比文件',
  parse_document: '解析文档',
  read_image: '读取图片',
  dir_create: '创建目录',
  dir_list: '列出目录',
  dir_delete: '删除目录',
  present_artifacts: '展示文件',
  get_current_time: '获取当前时间',
  data_analyzing_tool: '数据分析',
  calculator_tool: '精确计算',
  bash_run: '运行脚本',
  officecli_run: '运行 OfficeCLI',
  ask_user_question: '询问用户',
  write_todos: '更新任务计划',
  task: '派发子智能体',
  load_skill: '加载技能',
  mcp_tool_search: '搜索 MCP 工具',
  mcp_tool_call: '调用 MCP 工具',
})
