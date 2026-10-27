const TOOL_DISPLAY_NAMES: Record<string, string> = {
  data_analyzing_tool: '数据分析',
  calculator_tool: '精确计算',
  file_read: '读取文件',
  file_glob: '查找文件',
  file_grep: '搜索文本',
  file_diff: '对比文件',
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
  load_skill: '加载技能',
  mcp_tool_search: '搜索 MCP 工具',
  mcp_tool_call: '调用 MCP 工具',
}

export function getToolDisplayName(toolName?: string | null, args: ToolArgs = {}): string {
  const name = String(toolName || '').trim()
  if (!name) return '工具调用'
  if (name === 'mcp_tool_call') {
    const target = typeof args.name === 'string' ? args.name.trim() : ''
    return target ? `调用 MCP 工具：${target.replace(/^mcp_/, '')}` : '调用 MCP 工具'
  }
  // 控制工具用固定中文名；普通 MCP 限定名由后端 tools.<name>.label 下发，
  // 缺失时直接展示限定名（含 server 归属信息），而不是笼统的「工具调用」。
  if (name in TOOL_DISPLAY_NAMES) return TOOL_DISPLAY_NAMES[name] ?? name
  if (name.startsWith('mcp_')) return name
  return TOOL_DISPLAY_NAMES[name] || '工具调用'
}

/** 工具执行耗时文案：由后端盖章的起止时间（epoch 毫秒）相减得出，数据无效返回空串。 */
export function formatToolDuration(startedAtMs?: number | null, endedAtMs?: number | null): string {
  if (!startedAtMs || !endedAtMs || endedAtMs < startedAtMs) return ''
  const ms = endedAtMs - startedAtMs
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const totalSeconds = Math.round(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  return minutes > 0
    ? `${minutes}m${String(totalSeconds % 60).padStart(2, '0')}s`
    : `${totalSeconds}s`
}

/** 工具执行起止本地时间文案（xxxx-xx-xx xx:xx:xx.xxx），无开始时间返回空串。 */
export function formatToolTimingText(startedAtMs?: number | null, endedAtMs?: number | null): string {
  if (!startedAtMs) return ''
  const format = (ms: number): string => {
    const date = new Date(ms)
    const pad = (value: number, length = 2) => String(value).padStart(length, '0')
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`
  }
  return endedAtMs && endedAtMs >= startedAtMs
    ? `${format(startedAtMs)} ~ ${format(endedAtMs)}`
    : `开始 ${format(startedAtMs)}`
}

const normalizeVisiblePath = (value: unknown): string => (
  String(value || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/\/+/g, '/')
)

export const compactInlineText = (value: unknown, maxLength = 120): string => {
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

const fileDiffDescription: DescriptionBuilder = (args) => {
  const fileA = normalizeVisiblePath(args.file_a)
  const fileB = normalizeVisiblePath(args.file_b)
  return fileA && fileB ? `${fileA} -> ${fileB}` : fileA || fileB
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

/** data_analyzing_tool 的动作中文名；历史会话旧工具名不进此表。 */
const DATA_ANALYZING_ACTION_NAMES: Record<string, string> = {
  validate_dsl: '编译 SQL',
  validate_dashboard: '校验看板',
  execute_query: '查询数据',
  check_yaml: '语义模型自检',
}

/** 数据分析合并工具的动作中文名；非数据分析工具或未知动作返回空串。 */
export function getDataAnalyzingActionName(action?: string | null): string {
  const key = String(action || '').trim()
  return DATA_ANALYZING_ACTION_NAMES[key] || ''
}

/** 判断是否为数据分析合并工具（历史会话的旧工具名不算）。 */
export function isDataAnalyzingTool(toolName?: string | null): boolean {
  return String(toolName || '').trim() === 'data_analyzing_tool'
}

const dataAnalyzingDescription: DescriptionBuilder = (args) => {
  const action = String(args.action || '').trim()
  if (action === 'execute_query') return normalizeVisiblePath((args.action_payload as ToolArgs)?.save_as)
  const payload = (args.action_payload || {}) as ToolArgs
  return compactInlineText(payload.user_query || payload.semantic_config_id)
}

const TOOL_DESCRIPTION_BUILDERS: Record<string, DescriptionBuilder> = {
  file_read: filePathDescription,
  file_edit: filePathDescription,
  file_write: filePathDescription,
  file_delete: filePathDescription,
  file_move: transferDescription,
  file_copy: transferDescription,
  file_glob: args => joinPathDetail(args.path || 'workspace', args.pattern),
  file_grep: args => joinPathDetail(args.path || 'workspace', args.pattern),
  file_diff: fileDiffDescription,
  dir_create: args => normalizeVisiblePath(args.path),
  dir_list: args => normalizeVisiblePath(args.path || 'workspace'),
  dir_delete: args => normalizeVisiblePath(args.path),
  parse_document: transferDescription,
  read_image: filePathDescription,
  present_artifacts: presentArtifactsDescription,
  data_analyzing_tool: dataAnalyzingDescription,
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

/**
 * 模型提供的调用意图（args.intent）：一句中文说明本次工具调用的目的。
 * 有值时列表默认展示意图，技术细节（命令/文件路径）降级为悬浮内容。
 */
const intentDescription = (args: ToolArgs): string => compactInlineText(args.intent, 80)

export function getToolCompactDescription(
  toolName?: string | null,
  args: ToolArgs = {},
): string {
  const intent = intentDescription(args)
  if (intent) return intent
  return getToolTechnicalDescription(toolName, args)
}

// ---------------------------------------------------------------------------
// file_diff 结果解析（ToolCallCard / ToolCallDetailPanel 共用）
// ---------------------------------------------------------------------------

export interface FileDiffPayload {
  file_a: string
  file_b: string
  status: 'identical' | 'changed'
  added_lines: number
  removed_lines: number
  diff?: string
}

export type FileDiffLineType = 'meta' | 'hunk' | 'add' | 'del' | 'context'

export interface FileDiffLine {
  type: FileDiffLineType
  content: string
  /** 简洁对比行号：del/context 行的旧文件行号（add 行无；由 hunk 头起始行号推算） */
  oldNo?: number
  /** 简洁对比行号：add/context 行的新文件行号（del 行无；由 hunk 头起始行号推算） */
  newNo?: number
}

const FILE_DIFF_RESULT_PREFIX = '[file_diff]\n'

/** 解析 file_diff 完成结果；非 file_diff / 未完成 / offload / 格式漂移时返回 null（调用方回退通用文本展示）。 */
export function parseFileDiffResult(
  toolName?: string | null,
  status?: string | null,
  result?: string | null,
): FileDiffPayload | null {
  if (String(toolName || '').trim() !== 'file_diff' || status !== 'completed') return null
  const text = String(result || '')
  if (!text.startsWith(FILE_DIFF_RESULT_PREFIX)) return null
  try {
    const payload = JSON.parse(text.slice(FILE_DIFF_RESULT_PREFIX.length)) as FileDiffPayload
    return payload && typeof payload.status === 'string' ? payload : null
  } catch {
    return null
  }
}

/** file_diff 结果超过上下文预算被卸载为指针时返回 true（此时无法解析 diff，前端给出友好提示）。 */
export function isFileDiffOffloaded(
  toolName?: string | null,
  status?: string | null,
  result?: string | null,
): boolean {
  return (
    String(toolName || '').trim() === 'file_diff' &&
    status === 'completed' &&
    String(result || '').startsWith('[tool_result_offloaded]')
  )
}

/** unified diff 文本按行分类：文件头灰、@@ 蓝、增行绿、删行红、上下文普通。
 * 进入 hunk 正文后，以 ---/+++ 开头的行按删除/新增处理（正文内容可能自带 - 前缀），避免误判成文件头。 */
export function splitFileDiffLines(diff?: string): FileDiffLine[] {
  if (!diff) return []
  // hunk 头形如 @@ -oldStart,oldCount +newStart,newCount @@（count 可省略）
  const HUNK_HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/
  const result: FileDiffLine[] = []
  let inHunk = false
  let oldNo = 0
  let newNo = 0
  for (const line of diff.split('\n')) {
    if (!inHunk) {
      if (line.startsWith('---') || line.startsWith('+++')) {
        result.push({ type: 'meta', content: line })
        continue
      }
      if (!line.startsWith('@@')) {
        result.push({ type: 'context', content: line })
        continue
      }
      inHunk = true
    }
    if (line.startsWith('@@')) {
      const m = line.match(HUNK_HEADER)
      if (m) {
        oldNo = Number(m[1])
        newNo = Number(m[2])
      }
      result.push({ type: 'hunk', content: line })
      continue
    }
    if (line.startsWith('+')) {
      result.push({ type: 'add', content: line, newNo: newNo++ })
      continue
    }
    if (line.startsWith('-')) {
      result.push({ type: 'del', content: line, oldNo: oldNo++ })
      continue
    }
    result.push({ type: 'context', content: line, oldNo: oldNo++, newNo: newNo++ })
  }
  return result
}

/**
 * 技术细节描述：由工具名 + 参数规则推导（命令、文件路径等），适合悬浮展示。
 */
export function getToolTechnicalDescription(
  toolName?: string | null,
  args: ToolArgs = {},
): string {
  const name = String(toolName || '').trim()
  return TOOL_DESCRIPTION_BUILDERS[name]?.(args) || ''
}

/**
 * 从工具入参中提取所有可能指向「被写入/产生」文件路径的原始值。
 * 覆盖：文件类工具的 file_path/target_path/destination_path/path、
 * data_analyzing_tool execute_query 的 action_payload.save_as、
 * present_artifacts 的 files[]（字符串数组）。
 * 间接写入（bash_run / officecli_run）改走 extractChangedFilePathsFromResult：
 * 服务端在命令执行后用工作区 manifest 前后对比产出 changed_files，是权威归因来源。
 * 返回值未做可见路径归一化，由调用方按自己的口径（如剥 /mnt/user-data 前缀）再处理。
 */
export function extractWrittenFilePathCandidates(args: ToolArgs = {}): string[] {
  const candidates: unknown[] = [
    args.file_path,
    args.target_path,
    args.destination_path,
    args.path,
    (args.action_payload as ToolArgs | undefined)?.save_as,
    ...(Array.isArray(args.files) ? args.files : []),
  ]
  return candidates
    .filter((value): value is string => typeof value === 'string' && value.trim() !== '')
}

/**
 * 从工具结果 payload 中提取命令类工具（bash_run / officecli_run）的间接写入路径。
 * 服务端 payload 形如 ``[tool_name]\n{"changed_files":{"created":[...],"updated":[...],"deleted":[...]}}``，
 * 覆盖 ``-o``/重定向等命令文本里没有结构化路径的写入。解析失败或字段缺省返回空数组。
 * 返回值未做可见路径归一化，由调用方按自己的口径再处理。
 */
export function extractChangedFilePathsFromResult(result?: string | null): string[] {
  if (!result) return []
  const jsonStart = result.indexOf('\n')
  if (jsonStart < 0) return []
  try {
    const payload = JSON.parse(result.slice(jsonStart + 1)) as {
      changed_files?: { created?: unknown; updated?: unknown; deleted?: unknown }
    }
    const changed = payload?.changed_files
    if (!changed || typeof changed !== 'object') return []
    return (['created', 'updated', 'deleted'] as const).flatMap((key) =>
      Array.isArray(changed[key])
        ? changed[key].filter((value): value is string => typeof value === 'string' && value.trim() !== '')
        : [],
    )
  } catch {
    return []
  }
}
