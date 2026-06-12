<script setup lang="ts">
/**
 * SQL 编辑器组件
 * 使用 CodeMirror 实现语法高亮和代码补全
 * 支持多条 SQL 语句执行，各自显示结果集
 * 支持 AI 生成 SQL
 */
import { ref, computed, watch, onMounted, onUnmounted, shallowRef, useAttrs, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { streamQuickSql } from '../api'
import { useQueryTask } from '../composables/useQueryTask'
import AsyncQueryResult from './AsyncQueryResult.vue'
import QuickQueries from './QuickQueries.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import QueryFavoriteModal from './QueryFavoriteModal.vue'
import ExecutionTargetPicker from './ExecutionTargetPicker.vue'
import type { ChatDataSqlChangePayload, ExecutionTargetOption } from '../types'

const md = new MarkdownIt({ html: false, breaks: true, linkify: true })
const renderMarkdown = (text: string) => md.render(text)
const { submitSqlTask } = useQueryTask()

// CodeMirror
import { Codemirror } from 'vue-codemirror'
import { sql, MySQL } from '@codemirror/lang-sql'
import { EditorView } from '@codemirror/view'
import type { Extension } from '@codemirror/state'
import IconPlay from './icons/IconPlay.vue'

const safeRandomId = (): string => {
  const c = (globalThis as any)?.crypto
  if (typeof c?.randomUUID === 'function') {
    return c.randomUUID()
  }
  if (typeof c?.getRandomValues === 'function') {
    const bytes = new Uint8Array(16)
    c.getRandomValues(bytes)
    return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('')
  }
  return `id_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

type SqlEditorSSEEventType = 'message' | 'error'

function parseSSEEvents(buffer: string): {
  events: Array<{ event: SqlEditorSSEEventType; data: string }>
  remaining: string
} {
  const events: Array<{ event: SqlEditorSSEEventType; data: string }> = []
  const blocks = buffer.split('\n\n')
  const remaining = blocks.pop() || ''

  for (const block of blocks) {
    if (!block.trim()) continue

    const lines = block.split('\n')
    let eventType: SqlEditorSSEEventType = 'message'
    const dataLines: string[] = []

    for (const line of lines) {
      if (line.startsWith('event:')) {
        const rawType = line.substring(6).trim()
        if (rawType === 'error') {
          eventType = 'error'
        }
      } else if (line.startsWith('data:')) {
        dataLines.push(line.substring(5).trim())
      }
    }

    const dataStr = dataLines.join('\n')
    if (dataStr) {
      events.push({ event: eventType, data: dataStr })
    }
  }

  return { events, remaining }
}

interface TableInfo {
  table_name: string
  description?: string
  columns?: Array<{
    name: string
    type?: string
    mysql_type?: string
    description?: string
    primary_key?: boolean
  }>
}

interface DataSource {
  id: string
  ds_name: string
  ds_tables?: TableInfo[]
  template_datasource_id?: string
  source_type?: string
  source_config?: {
    host?: string
    port?: number
    database?: string
  }
  member_sources?: Array<{
    member_datasource_id?: string
    datasource_name?: string
    source_type?: string
    host: string
    port: number
    database: string
  }>
}

interface Props {
  datasourceId: string
  datasource?: DataSource | null
  tableSchema?: Record<string, string[]>
  hideSidebar?: boolean
  initialSql?: string
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  hideExecutionTargetSelector?: boolean
  onSqlChange?: (payload: ChatDataSqlChangePayload) => void
}

const props = withDefaults(defineProps<Props>(), {
  datasource: null,
  tableSchema: () => ({}),
  hideSidebar: false,
  executionTargetMultiple: true,
  hideExecutionTargetSelector: false
})
const attrs = useAttrs()

// 表结构面板
const searchText = ref('')
const expandedTables = ref<Set<string>>(new Set())
const isDatasourceHovered = ref(false)

// 过滤表
const filteredTables = computed(() => {
  if (!props.datasource?.ds_tables) return []
  if (!searchText.value.trim()) return props.datasource.ds_tables
  const keyword = searchText.value.toLowerCase()
  return props.datasource.ds_tables.filter(t =>
    t.table_name.toLowerCase().includes(keyword) ||
    t.description?.toLowerCase().includes(keyword)
  )
})

// 切换表展开
const toggleTable = (tableName: string) => {
  const newSet = new Set(expandedTables.value)
  if (newSet.has(tableName)) {
    newSet.delete(tableName)
  } else {
    newSet.add(tableName)
  }
  expandedTables.value = newSet
}

const focusSqlEditor = () => {
  const view = codemirrorRef.value?.view
  if (!view) return
  view.focus()
  const mainSelection = view.state.selection.main
  const hasRange = mainSelection.from !== mainSelection.to
  const anchor = hasRange ? mainSelection.to : view.state.doc.length
  view.dispatch({
    selection: { anchor },
    scrollIntoView: true,
  })
}

// 插入表名到编辑器
const insertTableName = (tableName: string) => {
  const current = sqlCode.value
  sqlCode.value = current + (current && !current.endsWith(' ') ? ' ' : '') + tableName
  nextTick(() => focusSqlEditor())
}

// 插入列名到编辑器
const insertColumnName = (tableName: string, columnName: string) => {
  const current = sqlCode.value
  sqlCode.value = current + (current && !current.endsWith(' ') ? ' ' : '') + `${tableName}.${columnName}`
  nextTick(() => focusSqlEditor())
}

const resolveInitialSql = () => props.initialSql ?? ''
// SQL 内容
const sqlCode = ref(resolveInitialSql())
// 最近一次保存值（用于“保存SQL”按钮启用状态）
const savedSqlCode = ref(sqlCode.value)
const hasSqlChanged = computed(() => sqlCode.value !== savedSqlCode.value)

const handleSaveSql = () => {
  if (!hasSqlChanged.value) return
  const cb = props.onSqlChange
  if (!cb) {
    ElMessage.warning('未配置保存回调')
    return
  }
  cb({
    mode: 'editor',
    sql: sqlCode.value
  })
  savedSqlCode.value = sqlCode.value
  ElMessage.success('SQL 已保存')
}

// 执行状态
const executing = ref(false)
const executingTaskText = ref('')
const executingTaskId = ref('')
const executionTime = ref<number | null>(null)

// 多结果支持
interface QueryResultItem {
  sql: string
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  execution_time_ms: number
  error?: string
  title?: string
  memberDatasourceId?: string
  memberName?: string
}
const results = ref<QueryResultItem[]>([])
const activeResultIndex = ref(0)
const queryHistoryRefreshKey = ref(0)
const errorDetailsExpanded = ref(false)

const localExecutionTargets = computed<ExecutionTargetOption[]>(() => {
  const members = props.datasource?.member_sources || []
  if (members.length) {
    return members
      .filter(member => member.member_datasource_id)
      .map(member => ({
        member_datasource_id: String(member.member_datasource_id),
        datasource_name: member.datasource_name || '',
        source_type: member.source_type || props.datasource?.source_type || '',
        host: member.host,
        port: member.port,
        database: member.database,
        label: member.datasource_name || `${member.host}:${member.port}/${member.database}`,
      }))
  }
  if (!props.datasourceId) return []
  return [{
    member_datasource_id: props.datasource?.template_datasource_id || props.datasourceId,
    datasource_name: props.datasource?.ds_name || props.datasourceId,
    source_type: props.datasource?.source_type || '',
    host: props.datasource?.source_config?.host,
    port: props.datasource?.source_config?.port,
    database: props.datasource?.source_config?.database,
    label: props.datasource?.ds_name || props.datasourceId,
  }]
})

const emit = defineEmits<{
  'update:selectedMemberDatasourceIds': [value: string[]]
}>()

const executionTargets = computed<ExecutionTargetOption[]>(() => props.executionTargets ?? localExecutionTargets.value)
const internalSelectedMemberDatasourceIds = ref<string[]>([])
const normalizeSelectedMemberDatasourceIds = (value: string[]) => (
  props.executionTargetMultiple ? value : value.slice(0, 1)
)
const isSameStringArray = (left: string[], right: string[]) => {
  if (left.length !== right.length) return false
  return left.every((item, index) => item === right[index])
}
const selectedMemberDatasourceIds = computed({
  get: () => props.selectedMemberDatasourceIds ?? internalSelectedMemberDatasourceIds.value,
  set: (value: string[]) => {
    const normalizedValue = normalizeSelectedMemberDatasourceIds(value)
    if (props.selectedMemberDatasourceIds !== undefined) {
      if (isSameStringArray(normalizedValue, props.selectedMemberDatasourceIds)) return
      emit('update:selectedMemberDatasourceIds', normalizedValue)
      return
    }
    if (isSameStringArray(normalizedValue, internalSelectedMemberDatasourceIds.value)) return
    internalSelectedMemberDatasourceIds.value = normalizedValue
  }
})

const syncSelectedExecutionTargets = () => {
  const availableIds = executionTargets.value.map(item => item.member_datasource_id)
  if (!availableIds.length) {
    selectedMemberDatasourceIds.value = []
    return
  }
  const retainedIds = selectedMemberDatasourceIds.value.filter(id => availableIds.includes(id))
  if (retainedIds.length > 0) {
    selectedMemberDatasourceIds.value = normalizeSelectedMemberDatasourceIds(retainedIds)
    return
  }
  const templateId = props.datasource?.template_datasource_id
  if (templateId && availableIds.includes(templateId)) {
    selectedMemberDatasourceIds.value = [templateId]
    return
  }
  selectedMemberDatasourceIds.value = [availableIds[0]]
}

watch(
  () => [
    props.datasourceId,
    props.datasource?.template_datasource_id,
    executionTargets.value.map(item => item.member_datasource_id).join(','),
  ],
  () => {
    syncSelectedExecutionTargets()
  },
  { immediate: true }
)

// 收藏
const showFavoriteModal = ref(false)
const favoriteTitle = ref('')
const favoriteContent = ref('')

const openFavoriteModal = () => {
  const sql = sqlCode.value.trim()
  if (!sql) {
    ElMessage.warning('请先输入 SQL')
    return
  }
  favoriteTitle.value = sql.split('\n')[0]?.trim().slice(0, 40) || 'SQL 查询'
  favoriteContent.value = sql
  showFavoriteModal.value = true
}

const handleFavoriteSaved = () => {
  queryHistoryRefreshKey.value++
}

// 保存到看板
const showSaveModal = ref(false)
const saveSql = ref('')
const saveChartType = ref('table')
const handleSaveToDashboard = (sql: string, chartType: string) => {
  saveSql.value = sql
  saveChartType.value = chartType
  showSaveModal.value = true
}

// 编辑器引用
const containerRef = ref<HTMLDivElement | null>(null)
const editorContainerRef = ref<HTMLDivElement | null>(null)
const codemirrorRef = ref<any>(null)

// 编辑器高度（像素），null = 自然高度，拖动后为固定像素值
const editorHeight = ref<number | null>(null)
const isResizing = ref(false)
const DEFAULT_EDITOR_HEIGHT_RATIO = 0.55

// 编辑器/AI面板宽度
const editorWidthPercent = ref(50)
const isHorizontalResizing = ref(false)

// AI 生成 SQL 状态
const showAiPanel = ref(false)
const aiQuery = ref('')
const isAiGenerating = ref(false)
const aiStreamContent = ref('')
const aiMessages = ref<Array<{ role: 'user' | 'assistant'; content: string; time?: string }>>([])
const aiInputRef = ref<HTMLInputElement | null>(null)
const aiOutputRef = ref<HTMLDivElement | null>(null)
let abortController: AbortController | null = null
const aiGenerationSeq = ref(0)
// 多轮对话 session
const aiSessionId = ref<string>(safeRandomId())

// CodeMirror 主题 - Apple 风格
const appleTheme = EditorView.theme({
  '&': {
    backgroundColor: '#FFFFFF',
    color: '#1D1D1F',
    height: '100%',
    fontSize: '14px',
  },
  '&.cm-editor': {
    height: '100%',
  },
  '&.cm-editor.cm-focused': {
    outline: 'none',
  },
  '.cm-scroller': {
    overflow: 'auto',
    height: '100%',
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.6',
  },
  '.cm-content': {
    caretColor: '#1677FF',
    padding: '8px 0',
  },
  '.cm-cursor': {
    borderLeftColor: '#1677FF',
    borderLeftWidth: '2px',
  },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {
    backgroundColor: 'rgba(22, 119, 255, 0.12)',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(22, 119, 255, 0.04)',
  },
  '.cm-gutters': {
    backgroundColor: '#FAFAFA',
    color: '#AEAEB2',
    border: 'none',
    borderRight: '1px solid rgba(0, 0, 0, 0.06)',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 12px 0 12px',
    minWidth: '32px',
    fontSize: '12px',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
    color: '#1677FF',
  },
  // SQL 语法高亮
  '.cm-keyword': {
    color: '#1677FF',
    fontWeight: '600',
  },
  '.cm-string': {
    color: '#FF3B30',
  },
  '.cm-number': {
    color: '#FF9500',
  },
  '.cm-comment': {
    color: '#8E8E93',
    fontStyle: 'italic',
  },
  '.cm-operator': {
    color: '#1677FF',
  },
  '.cm-punctuation': {
    color: '#636366',
  },
  '.cm-variableName, .cm-propertyName': {
    color: '#30B0C7',
  },
  '.cm-typeName': {
    color: '#30B0C7',
  },
  // 自动补全样式
  '.cm-tooltip': {
    backgroundColor: '#FFFFFF',
    border: '1px solid rgba(0, 0, 0, 0.1)',
    borderRadius: '8px',
    boxShadow: '0 4px 16px rgba(0, 0, 0, 0.12)',
  },
  '.cm-tooltip-autocomplete': {
    '& > ul': {
      fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
      fontSize: '13px',
    },
    '& > ul > li': {
      padding: '4px 12px',
    },
    '& > ul > li[aria-selected]': {
      backgroundColor: 'rgba(22, 119, 255, 0.1)',
      color: '#1677FF',
    },
  },
})

// CodeMirror 扩展
const extensions = computed<Extension[]>(() => {
  return [
    sql({
      dialect: MySQL,
      schema: props.tableSchema,
      upperCaseKeywords: true,
    }),
    EditorView.lineWrapping,
    appleTheme,
  ]
})

// 解析多条 SQL 语句
function parseSQLStatements(sqlText: string): string[] {
  const statements: string[] = []
  let current = ''
  let inString = false
  let stringChar = ''

  for (let i = 0; i < sqlText.length; i++) {
    const char = sqlText[i]
    const prevChar = sqlText[i - 1]

    if ((char === "'" || char === '"') && prevChar !== '\\') {
      if (!inString) {
        inString = true
        stringChar = char
      } else if (char === stringChar) {
        inString = false
      }
    }

    if (char === ';' && !inString) {
      const trimmed = current.trim()
      if (trimmed) {
        statements.push(trimmed)
      }
      current = ''
    } else {
      current += char
    }
  }

  const trimmed = current.trim()
  if (trimmed) {
    statements.push(trimmed)
  }

  return statements
}

// 执行 SQL（支持多条）
const handleExecute = async () => {
  const sqlText = sqlCode.value.trim()
  if (!sqlText) {
    ElMessage.warning('请输入 SQL 语句')
    return
  }

  if (!props.datasourceId) {
    ElMessage.warning('请选择数据源')
    return
  }
  if (executionTargets.value.length > 0 && selectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行数据源')
    return
  }

  const statements = parseSQLStatements(sqlText)
  if (statements.length === 0) {
    ElMessage.warning('没有有效的 SQL 语句')
    return
  }

  executing.value = true
  executingTaskText.value = '任务创建中...'
  executingTaskId.value = ''
  results.value = []
  activeResultIndex.value = 0
  executionTime.value = null

  const newResults: QueryResultItem[] = []

  for (let i = 0; i < statements.length; i++) {
    const stmt = statements[i] ?? ''
    const startTime = Date.now()

    try {
      const task = await submitSqlTask({
        datasource_id: props.datasourceId,
        member_datasource_ids: selectedMemberDatasourceIds.value,
        sql: stmt,
        limit: 100,
        query_type: 'sql',
      }, {
        onProgress: (taskStatus) => {
          executingTaskText.value = taskStatus.progress_text || '执行中...'
          executingTaskId.value = taskStatus.task_id
        }
      })
      const elapsed = Date.now() - startTime
      const memberResults = task.results || []

      if (memberResults.length > 0) {
        memberResults.forEach((result, memberIndex) => {
          const memberLabel = result.member_name || result.member_datasource_id || `成员${memberIndex + 1}`
          const title = statements.length > 1 ? `${memberLabel} · SQL ${i + 1}` : memberLabel
          newResults.push({
            sql: result.sql || stmt,
            columns: result.columns || [],
            data: result.data || [],
            total_count: result.total_count || 0,
            execution_time_ms: result.execution_time_ms || elapsed,
            error: result.success ? undefined : (result.error || '执行失败'),
            title,
            memberDatasourceId: result.member_datasource_id,
            memberName: result.member_name,
          })
        })
      } else {
        newResults.push({
          sql: stmt,
          columns: [],
          data: [],
          total_count: 0,
          execution_time_ms: elapsed,
          error: task.error_message || '执行失败',
          title: statements.length > 1 ? `SQL ${i + 1}` : '结果1',
        })
      }
    } catch (err) {
      const elapsed = Date.now() - startTime
      newResults.push({
        sql: stmt,
        columns: [],
        data: [],
        total_count: 0,
        execution_time_ms: elapsed,
        error: err instanceof Error ? err.message : '执行失败',
        title: statements.length > 1 ? `SQL ${i + 1}` : '结果1',
      })
    }

    results.value = [...newResults]
  }

  executing.value = false
  executingTaskText.value = ''
  executingTaskId.value = ''

  // 如果有成功的查询，触发历史刷新
  const hasSuccess = newResults.some(r => !r.error)
  if (hasSuccess) {
    queryHistoryRefreshKey.value++
  }
}

// 格式化 SQL
const handleFormat = async () => {
  if (!sqlCode.value.trim()) return
  try {
    const { format } = await import('sql-formatter')
    sqlCode.value = format(sqlCode.value, {
      language: 'mysql',
      tabWidth: 2,
      keywordCase: 'upper'
    })
  } catch {
    ElMessage.warning('SQL 格式化失败')
  }
}

// 清空
const handleClear = () => {
  sqlCode.value = ''
  results.value = []
  executionTime.value = null
}

// 快捷键
const handleKeydown = (e: KeyboardEvent) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    handleExecute()
  }
}

// 拖动调整高度
const startResize = (e: MouseEvent) => {
  e.preventDefault()
  isResizing.value = true
}

const onMouseMove = (e: MouseEvent) => {
  if (isResizing.value && containerRef.value) {
    const rect = containerRef.value.getBoundingClientRect()
    const mouseY = e.clientY - rect.top
    const minH = rect.height * 0.2
    const maxH = rect.height * 0.8
    editorHeight.value = Math.max(minH, Math.min(maxH, mouseY))
  }

  if (isHorizontalResizing.value && editorContainerRef.value) {
    const rect = editorContainerRef.value.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    let newPercent = (mouseX / rect.width) * 100
    newPercent = Math.max(30, Math.min(70, newPercent))
    editorWidthPercent.value = newPercent
  }
}

const onMouseUp = () => {
  isResizing.value = false
  isHorizontalResizing.value = false
}

const syncEditorHeight = (forceDefault = false) => {
  if (!containerRef.value) return
  const rect = containerRef.value.getBoundingClientRect()
  if (!rect.height) return
  const minH = rect.height * 0.2
  const maxH = rect.height * 0.8
  const target = forceDefault || editorHeight.value === null
    ? rect.height * DEFAULT_EDITOR_HEIGHT_RATIO
    : editorHeight.value
  editorHeight.value = Math.max(minH, Math.min(maxH, target))
}

const handleWindowResize = () => {
  syncEditorHeight(false)
}

// AI 生成 SQL
const handleAiGenerate = async () => {
  if (!aiQuery.value.trim()) {
    ElMessage.warning('请输入查询描述')
    return
  }

  if (!props.datasourceId) {
    ElMessage.warning('请先选择数据源')
    return
  }

  const userMessage = aiQuery.value.trim()
  const generationSeq = aiGenerationSeq.value + 1
  aiGenerationSeq.value = generationSeq
  const now = new Date()
  const timeStr = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
  aiMessages.value.push({ role: 'user', content: userMessage, time: timeStr })
  aiQuery.value = ''

  abortController = new AbortController()
  isAiGenerating.value = true
  aiStreamContent.value = ''

  let fullContent = ''

  try {
    const response = await streamQuickSql({
      query: userMessage,
      datasource_id: props.datasourceId,
      member_datasource_ids: selectedMemberDatasourceIds.value,
      session_id: aiSessionId.value,
    }, abortController.signal)

    if (aiGenerationSeq.value !== generationSeq) return

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('无法获取响应流')
    }

    const decoder = new TextDecoder()
    let buffer = ''
    let streamEnded = false

    while (true) {
      const { done, value } = await reader.read()
      if (aiGenerationSeq.value !== generationSeq) return
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const { events, remaining } = parseSSEEvents(buffer)
      buffer = remaining

      for (const { event, data: dataStr } of events) {
        if (dataStr === '[DONE]') {
          streamEnded = true
          break
        }

        try {
          const data = JSON.parse(dataStr)
          if (event === 'error' || data.type === 'error') {
            throw new Error(data.message || '生成失败')
          }
          if (data.type === 'chunk') {
            fullContent += data.content || ''
            if (aiGenerationSeq.value === generationSeq) {
              aiStreamContent.value = fullContent
            }
            continue
          }
          if (data.type === 'complete') {
            streamEnded = true
            break
          }
        } catch (err) {
          console.error('解析 AI SQL SSE 数据失败:', err, dataStr)
          if (err instanceof Error) {
            throw err
          }
        }
      }

      if (streamEnded) break
    }

    if (fullContent) {
      if (aiGenerationSeq.value === generationSeq) {
        aiMessages.value.push({ role: 'assistant', content: fullContent })
      }
    }
  } catch (err: any) {
    if (err.name === 'AbortError') {
      if (fullContent) {
        if (aiGenerationSeq.value === generationSeq) {
          aiMessages.value.push({ role: 'assistant', content: fullContent })
        }
      }
    } else {
      console.error('AI 生成 SQL 失败:', err)
      ElMessage.error('生成失败，请重试')
      if (aiGenerationSeq.value === generationSeq) {
        aiMessages.value.push({ role: 'assistant', content: '抱歉，生成失败，请重试。' })
      }
    }
  } finally {
    if (aiGenerationSeq.value === generationSeq) {
      isAiGenerating.value = false
      aiStreamContent.value = ''
      abortController = null
    }
  }
}

// 停止生成
const handleStopGenerate = () => {
  if (abortController) {
    abortController.abort()
  }
}

// 复制文本到剪贴板
const copyToClipboard = (text: string) => {
  navigator.clipboard.writeText(text)
  ElMessage.success('已复制')
}

// 提取 SQL 并添加到编辑器
const extractAndAddSql = (content: string) => {
  // 提取 markdown 代码块中的 SQL
  const sqlMatch = content.match(/```sql\s*([\s\S]*?)```/i) ?? content.match(/```\s*([\s\S]*?)```/)
  const sqlContent = sqlMatch?.[1]?.trim() ?? content.trim()

  const trimmed = sqlCode.value.trim()
  if (trimmed && !trimmed.endsWith(';')) {
    sqlCode.value = trimmed + ';\n\n' + sqlContent
  } else if (trimmed) {
    sqlCode.value = trimmed + '\n\n' + sqlContent
  } else {
    sqlCode.value = sqlContent
  }

  ElMessage.success('已添加到编辑器')
}

// 清空 AI 对话
const clearAiMessages = () => {
  aiGenerationSeq.value += 1
  if (isAiGenerating.value) {
    handleStopGenerate()
  }
  isAiGenerating.value = false
  abortController = null
  aiMessages.value = []
  aiQuery.value = ''
  aiStreamContent.value = ''
  aiSessionId.value = safeRandomId()
  ElMessage.success('已新建会话')
}

// 当前结果
const currentResult = computed(() => results.value[activeResultIndex.value])
const currentErrorMeta = computed(() => {
  const rawMessage = String(currentResult.value?.error || '').trim()
  if (!rawMessage) {
    return {
      summary: '',
      details: '',
      hasDetails: false,
    }
  }

  const normalizedMessage = rawMessage.replace(/\s+/g, ' ').trim()
  if (normalizedMessage.startsWith('SQL语法错误:')) {
    const details = normalizedMessage.slice('SQL语法错误:'.length).trim()
    return {
      summary: 'SQL语法错误，请查看错误详情',
      details,
      hasDetails: Boolean(details),
    }
  }

  if (normalizedMessage.length <= 72) {
    return {
      summary: normalizedMessage,
      details: '',
      hasDetails: false,
    }
  }

  return {
    summary: `${normalizedMessage.slice(0, 72)}...`,
    details: normalizedMessage,
    hasDetails: true,
  }
})

// 当前结果的 JSON 字符串（用于 AsyncQueryResult）
const currentResultData = computed(() => {
  const result = currentResult.value
  if (!result) return null
  return JSON.stringify({
    success: !result.error,
    columns: result.columns,
    data: result.data,
    total_count: result.total_count,
    execution_time_ms: result.execution_time_ms,
    error: result.error
  })
})

// 数据源切换时重置 AI 会话（表结构变了，历史上下文无意义）
watch(() => props.datasourceId, () => {
  aiSessionId.value = safeRandomId()
})

// 外部更新初始化 SQL 时同步到编辑器
watch(() => props.initialSql, () => {
  const next = resolveInitialSql()
  // 避免外部更新直接覆盖用户未保存的本地编辑
  if (hasSqlChanged.value && next !== savedSqlCode.value) {
    ElMessage.warning('检测到本地未保存 SQL，已跳过外部覆盖')
    return
  }
  sqlCode.value = next
  savedSqlCode.value = next
})

watch(() => currentResult.value?.error, () => {
  errorDetailsExpanded.value = false
})

// 显示 AI 面板时自动聚焦
watch(showAiPanel, (show) => {
  if (show && aiInputRef.value) {
    setTimeout(() => aiInputRef.value?.focus(), 100)
  }
})

onMounted(() => {
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  document.addEventListener('keydown', handleKeydown)
  window.addEventListener('resize', handleWindowResize)
  nextTick(() => syncEditorHeight(true))
})

onUnmounted(() => {
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
  document.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', handleWindowResize)
})

// 暴露方法
defineExpose({
  setSql: (text: string) => { sqlCode.value = text },
  appendSql: (text: string) => {
    const trimmed = sqlCode.value.trim()
    if (trimmed && !trimmed.endsWith(';')) {
      sqlCode.value = trimmed + ';\n\n' + text
    } else if (trimmed) {
      sqlCode.value = trimmed + '\n\n' + text
    } else {
      sqlCode.value = text
    }
  },
  focusEditor: focusSqlEditor,
  insertTableName,
  insertColumnName
})
</script>

<template>
  <div ref="containerRef" v-bind="attrs" class="flex flex-row h-full bg-gradient-to-br from-slate-50 to-slate-100">
    <!-- 左侧：数据表面板 -->
    <div v-if="!props.hideSidebar" class="w-60 flex flex-col bg-white border-r border-black/[0.06]">
      <!-- 数据源选择（悬浮显示） -->
      <div class="relative" @mouseenter="isDatasourceHovered = true" @mouseleave="isDatasourceHovered = false">
        <div :class="[
          'overflow-hidden transition-all duration-300 bg-gradient-to-br from-white/95 to-gray-50/95 backdrop-blur-[10px] border-b border-black/[0.06]',
          isDatasourceHovered ? 'max-h-[60px] opacity-100 px-3 py-2.5' : 'max-h-0 opacity-0'
        ]">
          <div class="flex items-center gap-2 px-2.5 py-1.5 bg-[#1f82d9]/[0.05] rounded-lg border border-[#1f82d9]/10">
            <i class="fa-solid fa-database text-xs text-[#1f82d9]"></i>
            <span class="flex-1 text-[13px] text-gray-700 font-medium">{{ datasource?.ds_name || '未选择数据源' }}</span>
          </div>
        </div>
        <div :class="[
          'flex justify-center py-1 transition-all duration-300',
          isDatasourceHovered && 'opacity-0 h-0 py-0'
        ]">
          <div class="w-9 h-1 bg-gradient-to-b from-gray-400 to-gray-300 rounded-b transition-transform duration-200 hover:scale-x-110"></div>
        </div>
      </div>

      <div class="flex items-center justify-between p-4 border-b border-black/[0.06] bg-gradient-to-br from-blue-50 to-blue-50/50">
        <div class="flex items-center gap-2 text-sm font-semibold text-gray-800">
          <i class="fa-solid fa-database text-[#1f82d9]"></i>
          数据表
        </div>
      </div>

      <!-- 搜索框 -->
      <div class="relative p-3 border-b border-black/[0.06]">
        <i class="fa-solid fa-magnifying-glass absolute left-[22px] top-1/2 -translate-y-1/2 text-xs text-gray-400"></i>
        <input v-model="searchText" type="text" placeholder="搜索表..." class="w-full h-9 pl-8 pr-3 border border-black/10 rounded-lg text-[13px] outline-none transition-all duration-200 focus:border-[#1f82d9] focus:shadow-[0_0_0_3px_rgba(31,130,217,0.1)]" />
      </div>

      <!-- 表列表 -->
      <div class="flex-1 overflow-y-auto p-2">
        <div v-if="!datasource" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-[13px]">
          <i class="fa-solid fa-database"></i>
          <span>请选择数据源</span>
        </div>

        <div v-else-if="filteredTables.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-[13px]">
          <span>未找到表</span>
        </div>

        <template v-else>
          <div v-for="table in filteredTables" :key="table.table_name" class="mb-1.5">
            <!-- 表头 -->
            <div
              :class="[
                'flex items-center gap-2 px-3 py-2.5 rounded-[10px] cursor-pointer transition-all duration-200 bg-gradient-to-br from-blue-50/50 to-blue-100/30 border border-blue-500/10 hover:from-blue-100/80 hover:to-blue-200/50 hover:border-blue-500/20',
                expandedTables.has(table.table_name) && 'rounded-b-none'
              ]"
              @click="toggleTable(table.table_name)"
            >
              <div class="text-gray-400 text-xs">
                <i class="fa-solid fa-grip-vertical"></i>
              </div>
              <i class="fa-solid fa-table text-xs text-[#1f82d9]"></i>
              <span class="flex-1 text-[13px] font-medium text-gray-700 overflow-hidden text-ellipsis whitespace-nowrap cursor-pointer hover:text-[#1f82d9]" :title="table.table_name" @dblclick.stop="insertTableName(table.table_name)">
                {{ table.description || table.table_name }}
              </span>
              <button class="w-5 h-5 flex items-center justify-center border-none rounded bg-transparent text-gray-400 cursor-pointer text-[10px]">
                <i :class="['fa-solid', expandedTables.has(table.table_name) ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
              </button>
            </div>

            <!-- 字段列表 -->
            <div v-if="expandedTables.has(table.table_name)" class="ml-4 py-1 border-l-2 border-[#1f82d9]/20 bg-slate-50/50 rounded-b-lg">
              <div
                v-for="col in table.columns"
                :key="col.name"
                class="flex items-center gap-2 px-3 py-2 cursor-pointer transition-all duration-150 hover:bg-[#1f82d9]/[0.05]"
                @dblclick="insertColumnName(table.table_name, col.name)"
              >
                <div class="text-gray-400 text-[10px]">
                  <i class="fa-solid fa-grip-vertical"></i>
                </div>
                <span class="flex-1 text-xs text-gray-600">{{ col.description || col.name }}</span>
                <span class="text-[10px] text-gray-400 px-1.5 py-0.5 bg-black/[0.04] rounded font-mono">{{ col.mysql_type || col.type }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 右侧：编辑器区域 -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <!-- 编辑器区域 -->
      <div
        class="flex flex-col min-h-[250px] bg-white shrink-0 overflow-hidden"
        :style="editorHeight !== null ? { height: editorHeight + 'px' } : { height: `${Math.round(DEFAULT_EDITOR_HEIGHT_RATIO * 100)}%` }"
      >
      <!-- 工具栏 -->
      <div class="flex items-center justify-between px-3 py-2 bg-white border-b border-[#e3e5eb]">
        <div class="flex gap-2">
          <button
            class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3] disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="executing"
            @click="handleExecute"
          >
            <i v-if="executing" class="fa-solid fa-spinner fa-spin text-[12px]"></i>
            <IconPlay v-else />
            <span class="ml-0.5">{{ executing ? '查询中...' : '查询' }}</span>
          </button>
          <ExecutionTargetPicker
            v-if="!hideExecutionTargetSelector && executionTargets.length > 0"
            v-model="selectedMemberDatasourceIds"
            :targets="executionTargets"
            :multiple="executionTargetMultiple"
          />
          <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" @click="handleFormat">
            <i class="fa-solid fa-indent"></i>
            <span>格式化</span>
          </button>
          <button
            class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded text-[12px] font-semibold cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]"
            :class="showAiPanel ? 'bg-[#E8F3FF] border-[#165DFF] text-[#165DFF]' : 'bg-white text-[#21252C]'"
            @click="showAiPanel = !showAiPanel"
          >
            <i class="fa-regular fa-lightbulb"></i>
            <span>AI生成SQL</span>
          </button>
          <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" @click="openFavoriteModal">
            <i class="fa-regular fa-star"></i>
            <span>收藏</span>
          </button>
          <button
            v-if="props.onSqlChange"
            class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3] disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="!hasSqlChanged"
            @click="handleSaveSql"
          >
            <i class="fa-regular fa-floppy-disk"></i>
            <span>保存SQL</span>
          </button>
          <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" @click="handleClear">
            <i class="fa-regular fa-file"></i>
            <span>重置</span>
          </button>
        </div>
      </div>

      <!-- 编辑器区域（左右分屏） -->
      <div ref="editorContainerRef" class="flex-1 min-h-0 flex overflow-hidden">
        <!-- 左侧：SQL 编辑器 -->
        <div class="overflow-hidden bg-white editor-wrapper" :style="{ width: showAiPanel ? `${editorWidthPercent}%` : '100%' }">
          <Codemirror
            ref="codemirrorRef"
            v-model="sqlCode"
            :extensions="extensions"
            placeholder="输入 SQL 语句... (⌘+Enter 查询，输入表名或字段名触发自动补全)"
            :style="{ height: '100%' }"
            :autofocus="true"
            :indent-with-tab="true"
            :tab-size="2"
          />
        </div>

        <!-- 横向拖动分隔条 -->
        <div
          v-if="showAiPanel"
          :class="[
            'w-1.5 cursor-col-resize flex items-center justify-center bg-transparent transition-colors duration-150',
            isHorizontalResizing && 'bg-[#1677FF]/[0.05]'
          ]"
          @mousedown.prevent="isHorizontalResizing = true"
        >
          <div :class="[
            'w-0.5 h-6 rounded-sm bg-black/15 transition-all duration-150',
            isHorizontalResizing && 'bg-[#1677FF]'
          ]"></div>
        </div>

        <!-- 右侧：AI 辅助面板 -->
        <div v-if="showAiPanel" class="relative flex flex-col min-h-0 overflow-hidden bg-[#FAFAFA] border-l border-black/[0.06]" :style="{ width: `${100 - editorWidthPercent}%` }">
          <!-- AI 面板工具按钮 -->
          <button
            class="absolute top-3 left-3 h-7 px-2.5 flex items-center gap-1 border border-black/[0.08] rounded-md bg-white text-gray-500 text-xs cursor-pointer transition-all duration-150 z-10 hover:bg-black/[0.03] hover:text-[#1677FF]"
            title="清空当前 AI 对话并新建会话"
            @click="clearAiMessages"
          >
            <i class="fa-solid fa-rotate-right"></i>
            <span>新建会话</span>
          </button>
          <button class="absolute top-3 right-3 w-7 h-7 flex items-center justify-center border-none rounded-md bg-black/[0.04] text-gray-400 cursor-pointer transition-all duration-150 z-10 hover:bg-black/[0.04] hover:text-gray-700" @click="showAiPanel = false">
            <i class="fa-solid fa-xmark"></i>
          </button>

          <!-- AI 消息区域 -->
          <div ref="aiOutputRef" class="flex-1 min-h-0 overflow-y-auto px-3 pb-3 pt-12">
            <div v-if="aiMessages.length === 0 && !isAiGenerating" class="flex flex-col items-center justify-center h-full text-gray-400">
              <i class="fa-solid fa-wand-magic-sparkles text-2xl mb-3 text-[#1677FF] opacity-50"></i>
              <p class="text-[13px] m-0">输入自然语言描述</p>
              <p class="text-[11px] mt-1 text-gray-400">AI 将为你生成 SQL 语句</p>
            </div>
            <template v-else>
              <!-- 历史消息 -->
              <div v-for="(msg, idx) in aiMessages" :key="idx" class="mb-3" :class="msg.role === 'user' ? 'flex flex-col items-end' : ''">
                <div v-if="msg.role === 'assistant'" class="max-w-[85%] px-4 py-3 rounded-2xl rounded-bl bg-white border border-black/[0.06] shadow-[0_2px_8px_rgba(0,0,0,0.04)] text-[13px] leading-relaxed break-words markdown-body" v-html="renderMarkdown(msg.content)"></div>
                <div v-else class="max-w-[85%] px-3.5 py-2.5 rounded-2xl rounded-br bg-[#1677FF]/10 text-[#1F3A56] text-[13px] leading-relaxed">{{ msg.content }}</div>
                <div v-if="msg.role === 'user' && msg.time" class="text-[11px] text-gray-400 mt-1">{{ msg.time }}</div>
                <div v-if="msg.role === 'assistant'" class="flex items-center gap-2 mt-2 pl-1">
                  <button class="flex items-center gap-1 px-2 py-1 border-none rounded bg-transparent text-[11px] text-gray-400 cursor-pointer transition-all duration-150 hover:bg-black/[0.04] hover:text-[#1677FF]" title="复制" @click="copyToClipboard(msg.content)">
                    <i class="fa-regular fa-copy"></i>
                  </button>
                  <button class="flex items-center gap-1 px-2 py-1 border-none rounded bg-transparent text-[11px] text-gray-400 cursor-pointer transition-all duration-150 hover:bg-black/[0.04] hover:text-[#1677FF]" title="添加到编辑器" @click="extractAndAddSql(msg.content)">
                    <i class="fa-solid fa-check"></i>
                    接受
                  </button>
                </div>
              </div>

              <!-- 正在生成的消息 -->
              <div v-if="isAiGenerating" class="mb-3">
                <div class="max-w-[85%] px-4 py-3 rounded-2xl rounded-bl bg-white border border-black/[0.06] shadow-[0_2px_8px_rgba(0,0,0,0.04)] text-[13px] leading-relaxed break-words markdown-body">
                  <template v-if="aiStreamContent"><span v-html="renderMarkdown(aiStreamContent)"></span><span class="animate-pulse text-gray-400">▌</span></template>
                  <template v-else>思考中...</template>
                </div>
              </div>
            </template>
          </div>

          <!-- AI 输入区域 -->
          <div class="p-3 bg-[#FAFAFA] border-t border-black/[0.06]">
            <div class="flex items-center gap-2 px-3 py-2 rounded-[20px] bg-white border border-black/[0.08] shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
              <input
                ref="aiInputRef"
                v-model="aiQuery"
                type="text"
                placeholder="描述你想查询的数据..."
                class="flex-1 border-none bg-transparent text-[13px] outline-none text-gray-900 placeholder:text-gray-400"
                @keydown.enter.prevent="!isAiGenerating && handleAiGenerate()"
              />
              <button
                :class="[
                  'w-8 h-8 flex items-center justify-center border-none rounded-lg cursor-pointer transition-all duration-200',
                  isAiGenerating ? 'bg-[#1f82d9]/10 text-[#1f82d9]' : 'bg-gradient-to-br from-[#1f82d9] to-[#135589] text-white shadow-[0_2px_6px_rgba(31,130,217,0.3)]',
                  (!isAiGenerating && !aiQuery.trim()) && 'opacity-30 cursor-not-allowed'
                ]"
                :disabled="!isAiGenerating && !aiQuery.trim()"
                @click="isAiGenerating ? handleStopGenerate() : handleAiGenerate()"
              >
                <i v-if="isAiGenerating" class="fa-solid fa-stop"></i>
                <i v-else class="fa-solid fa-arrow-up"></i>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 拖动分隔条 -->
    <div
      class="h-1 cursor-row-resize flex items-center justify-center bg-[#f0f1f3] transition-colors duration-150 shrink-0 hover:bg-[#E8F3FF] group"
      :class="{ 'bg-[#E8F3FF]': isResizing }"
      @mousedown="startResize"
    >
      <div
        class="w-8 h-[2px] rounded-sm bg-[#93949580] transition-all duration-150 group-hover:w-12 group-hover:bg-[#165DFF]"
        :class="{ 'w-12 bg-[#165DFF]': isResizing }"
      ></div>
    </div>

    <!-- 结果区域 -->
    <div class="flex-1 flex flex-col min-h-[100px] overflow-hidden bg-white">
      <!-- 结果 Tabs（多结果时显示） -->
      <div v-if="results.length > 1" class="px-4 py-3 bg-gradient-to-br from-gray-50 to-slate-50 border-b border-black/[0.06]">
        <div class="flex items-center gap-1.5 p-1 bg-black/[0.04] rounded-xl w-fit">
          <button
            v-for="(result, index) in results"
            :key="index"
            :class="[
              'flex items-center gap-2 px-4 py-2 border-none rounded-[10px] bg-transparent text-[13px] font-medium text-gray-500 cursor-pointer transition-all duration-200 whitespace-nowrap',
              activeResultIndex === index ? 'bg-gradient-to-br from-[#1f82d9] to-[#135589] text-white shadow-[0_2px_8px_rgba(31,130,217,0.3)]' : 'hover:bg-white/60 hover:text-gray-700'
            ]"
            :title="result.sql"
            @click="activeResultIndex = index"
          >
            <i v-if="result.error" class="fa-solid fa-circle-xmark text-[10px] text-red-500"></i>
            <i v-else class="fa-solid fa-circle-check text-[10px] text-emerald-500"></i>
            <span>{{ result.title || `结果${index + 1}` }}</span>
          </button>
        </div>
      </div>

      <!-- 结果内容 -->
      <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
        <div v-if="currentResult?.error" class="m-4 p-4 rounded-xl bg-red-500/[0.05] border border-red-500/15 text-red-600">
          <div class="flex items-start gap-2.5">
            <i class="fa-solid fa-triangle-exclamation mt-0.5 shrink-0"></i>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-3 min-w-0 text-[13px] leading-relaxed">
                <span class="min-w-0 flex-1 truncate">{{ currentErrorMeta.summary }}</span>
                <button
                  v-if="currentErrorMeta.hasDetails"
                  class="inline-flex shrink-0 items-center gap-1.5 px-2.5 py-1 rounded-md border border-red-500/20 bg-white/70 text-[12px] font-medium text-red-600 transition-colors duration-150 hover:bg-white"
                  @click="errorDetailsExpanded = !errorDetailsExpanded"
                >
                  <i :class="['fa-solid text-[10px]', errorDetailsExpanded ? 'fa-chevron-up' : 'fa-chevron-down']"></i>
                  <span>{{ errorDetailsExpanded ? '收起错误详情' : '查看错误详情' }}</span>
                </button>
              </div>
              <pre
                v-if="currentErrorMeta.hasDetails && errorDetailsExpanded"
                class="mt-3 p-3 rounded-lg bg-white/80 border border-red-500/10 text-[12px] leading-6 text-red-700 whitespace-pre-wrap break-words overflow-auto max-h-40"
              >{{ currentErrorMeta.details }}</pre>
            </div>
          </div>
        </div>

        <AsyncQueryResult
          v-else-if="currentResultData"
          :result-data="currentResultData"
          :sql="currentResult?.sql"
          :on-save="handleSaveToDashboard"
        />

        <div v-else-if="executing" class="flex-1 flex flex-col items-center justify-center py-16 px-6 bg-gradient-to-br from-gray-50 to-slate-50">
          <div class="w-20 h-20 flex items-center justify-center rounded-[20px] bg-gradient-to-br from-blue-50 to-blue-100 mb-5 shadow-[0_4px_16px_rgba(31,130,217,0.1)]">
            <i class="fa-solid fa-spinner fa-spin text-[32px] bg-gradient-to-br from-[#1f82d9] to-[#135589] bg-clip-text text-transparent"></i>
          </div>
          <p class="text-base font-semibold text-gray-700 m-0 mb-2">{{ executingTaskText || '执行查询中...' }}</p>
          <p v-if="executingTaskId" class="m-0 text-[12px] text-gray-400">任务ID: {{ executingTaskId }}</p>
        </div>

        <div v-else class="flex-1 flex flex-col overflow-y-auto">
          <!-- 快捷查询历史 -->
          <QuickQueries
            v-if="datasourceId"
            :datasource-id="datasourceId"
            query-type="sql"
            :refresh-trigger="queryHistoryRefreshKey"
            :max-items="8"
            @select-query="(query) => { sqlCode = query }"
          />
        </div>
      </div>
    </div>
    </div>
  </div>

  <!-- 保存到看板弹窗 -->
  <SaveToPanelModal
    v-model:visible="showSaveModal"
    :sql="saveSql"
    :chart-type="saveChartType"
    :datasource-id="datasourceId"
  />

  <QueryFavoriteModal
    v-model:visible="showFavoriteModal"
    mode="sql"
    :datasource-id="datasourceId"
    :initial-title="favoriteTitle"
    :initial-content="favoriteContent"
    @saved="handleFavoriteSaved"
  />
</template>

<style scoped>
/* CodeMirror 编辑器样式 - 需要 :deep() 穿透 */
.editor-wrapper :deep(.cm-editor) {
  height: 100%;
}

/* Markdown 渲染样式 - 需要 :deep() 穿透 */
.markdown-body :deep(p) {
  margin: 0 0 8px 0;
}
.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(strong) {
  font-weight: 600;
  color: #1f2937;
}
.markdown-body :deep(em) {
  font-style: italic;
}
.markdown-body :deep(code) {
  background-color: #f3f4f6;
  color: #e11d48;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.markdown-body :deep(pre) {
  background-color: #1e293b;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 12px 16px;
  overflow-x: auto;
  margin: 8px 0;
}
.markdown-body :deep(pre code) {
  background: none;
  color: inherit;
  padding: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 4px 0 8px 0;
}
.markdown-body :deep(li) {
  margin: 2px 0;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-weight: 600;
  color: #111827;
  margin: 8px 0 4px 0;
}
.markdown-body :deep(h1) { font-size: 16px; }
.markdown-body :deep(h2) { font-size: 15px; }
.markdown-body :deep(h3) { font-size: 14px; }
.markdown-body :deep(blockquote) {
  border-left: 3px solid #d1d5db;
  padding-left: 12px;
  color: #6b7280;
  margin: 8px 0;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 8px 0;
}
</style>
