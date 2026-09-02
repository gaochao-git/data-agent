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
  read_image: '读取图片',
  dir_create: '创建目录',
  dir_list: '列出目录',
  dir_delete: '删除目录',
  bash_run: '运行脚本',
  officecli_run: '运行 OfficeCLI',
  get_current_time: '获取当前时间',
  present_artifacts: '展示文件',
  ask_user_question: '询问用户',
  write_todos: '更新任务计划',
  task: '派发子智能体',
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

const compactInlineText = (value: unknown, maxLength = 120): string => {
  const text = String(value || '').trim().replace(/\s+/g, ' ')
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text
}

const joinPathDetail = (path: unknown, detail: unknown): string => {
  const normalizedPath = normalizeVisiblePath(path)
  const normalizedDetail = compactInlineText(detail)
  if (!normalizedPath) return normalizedDetail
  if (!normalizedDetail) return normalizedPath
  return `${normalizedPath} · ${normalizedDetail}`
}

type ToolArgs = Record<string, unknown>
type DescriptionBuilder = (args: ToolArgs) => string

const filePathDescription: DescriptionBuilder = args => normalizeVisiblePath(args.file_path || args.path)

const transferDescription: DescriptionBuilder = (args) => {
  const source = normalizeVisiblePath(args.source_path)
  const target = normalizeVisiblePath(args.target_path || args.destination_path)
  return source && target ? `${source} -> ${target}` : source || target
}

const presentArtifactsDescription: DescriptionBuilder = (args) => {
  const files = Array.isArray(args.files)
    ? args.files.map(normalizeVisiblePath).filter(Boolean)
    : []
  if (files.length === 0) return ''
  return files.length === 1 ? files[0]! : `${files[0]} 等 ${files.length} 个文件`
}

const calculatorDescription: DescriptionBuilder = (args) => {
  const calculations = Array.isArray(args.calculations) ? args.calculations : []
  if (calculations.length === 0) return ''
  const first = calculations[0]
  const firstText = first && typeof first === 'object'
    ? compactInlineText((first as ToolArgs).name || (first as ToolArgs).expression)
    : compactInlineText(first)
  return calculations.length === 1 ? firstText : `${firstText} 等 ${calculations.length} 项`
}

const TOOL_DESCRIPTION_BUILDERS: Record<string, DescriptionBuilder> = {
  search_dataset: args => compactInlineText(args.query),
  semantic_search: args => compactInlineText(args.query || args.semantic_config_id || args.operation),
  file_read: filePathDescription,
  file_edit: filePathDescription,
  file_write: filePathDescription,
  file_delete: filePathDescription,
  file_move: transferDescription,
  file_copy: transferDescription,
  file_glob: args => joinPathDetail(args.path || 'workspace', args.pattern),
  file_grep: args => joinPathDetail(args.path || 'workspace', args.pattern),
  dir_create: args => normalizeVisiblePath(args.path),
  dir_list: args => normalizeVisiblePath(args.path || 'workspace'),
  dir_delete: args => normalizeVisiblePath(args.path),
  parse_document: transferDescription,
  read_image: filePathDescription,
  present_artifacts: presentArtifactsDescription,
  semantic_query_tool: args => normalizeVisiblePath(args.save_as),
  dsl2sql_tool: args => compactInlineText(args.user_query || args.semantic_config_id),
  calculator_tool: calculatorDescription,
  officecli_run: args => Array.isArray(args.command_args) ? compactInlineText(args.command_args.join(' ')) : '',
  bash_run: args => compactInlineText(args.command),
  task: args => {
    const type = String(args.subagent_type || '').trim()
    const summary = compactInlineText(args.description, 80)
    return type && summary ? `${type} · ${summary}` : type || summary
  },
  write_todos: args => {
    const todos = Array.isArray(args.todos) ? args.todos : []
    const done = todos.filter(item => item?.status === 'completed').length
    const current = todos.find(item => item?.status === 'in_progress')
    const base = `共 ${todos.length} 项，已完成 ${done}`
    return current ? `${base}，进行中：${compactInlineText(current.content, 40)}` : base
  },
  ask_user_question: args => {
    const questions = Array.isArray(args.questions) ? args.questions : []
    const first = questions[0]
    if (!first || typeof first !== 'object') return ''
    const text = compactInlineText((first as ToolArgs).question)
    return questions.length > 1 ? `${text} 等 ${questions.length} 个问题` : text
  },
}

export function getToolCompactDescription(
  toolName?: string | null,
  args: ToolArgs = {},
): string {
  const name = String(toolName || '').trim()
  return TOOL_DESCRIPTION_BUILDERS[name]?.(args) || ''
}
