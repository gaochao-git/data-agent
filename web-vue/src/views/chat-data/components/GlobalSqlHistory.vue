<script setup lang="ts">
/**
 * 全局历史 SQL。
 * 用于后台排查 SQL 执行情况，只展示历史记录和基础筛选，不承载编辑执行能力。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { cancelGlobalRunningQueryHistory, getGlobalQueryHistory } from '../api'
import type { DataSource, SqlHistoryItem } from '../types'
import { buildRecentDaysDateTimeRange } from '../utils/dateTime'

const props = withDefaults(defineProps<{
  datasources?: DataSource[]
}>(), {
  datasources: () => [],
})

const loading = ref(false)
const stoppingRunning = ref(false)
const hasLoaded = ref(false)
const histories = ref<SqlHistoryItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const errorDialogVisible = ref(false)
const errorDialogMessage = ref('')
const buildDefaultTimeRange = () => buildRecentDaysDateTimeRange(7)
const filters = ref({
  datasource_id: '',
  username: '',
  keyword: '',
  source: 'all',
  status: 'all',
  time_range: buildDefaultTimeRange() as [string, string] | null,
})

const defaultVisibleColumnKeys = [
  'time',
  'source',
  'status',
  'user',
  'member',
  'duration',
  'sql',
  'actions',
]

const visibleColumnKeys = ref<string[]>([...defaultVisibleColumnKeys])

const columnOptions = [
  { key: 'time', label: '时间', defaultVisible: true },
  { key: 'source', label: '来源', defaultVisible: true },
  { key: 'status', label: '状态', defaultVisible: true },
  { key: 'user', label: '用户', defaultVisible: true },
  { key: 'member', label: '执行对象', defaultVisible: true },
  { key: 'duration', label: '耗时', defaultVisible: true },
  { key: 'sql', label: 'SQL', defaultVisible: true },
  { key: 'actions', label: '操作', defaultVisible: true },
  { key: 'execution_id', label: '执行ID' },
  { key: 'task_id', label: '任务ID' },
  { key: 'datasource_id', label: '数据源ID' },
  { key: 'member_datasource_id', label: '成员ID' },
  { key: 'query_type', label: '查询类型' },
  { key: 'task_status', label: '任务状态' },
  { key: 'row_count', label: '结果行数' },
  { key: 'started_time', label: '开始时间' },
  { key: 'finished_time', label: '结束时间' },
  { key: 'error', label: '错误信息' },
]

const datasourceOptions = computed(() => props.datasources || [])
const visibleColumnSet = computed(() => new Set(visibleColumnKeys.value))
const visibleColumnCount = computed(() => Math.max(1, visibleColumnKeys.value.length))

const sourceOptions = [
  { label: '全部来源', value: 'all' },
  { label: '智能问数', value: 'agent' },
  { label: 'SQL查询', value: 'sql' },
  { label: '可视化查询', value: 'builder' },
  { label: '看板', value: 'dashboard' },
]

const statusOptions = [
  { label: '全部状态', value: 'all' },
  { label: '成功', value: 'succeeded' },
  { label: '失败', value: 'failed' },
  { label: '运行中', value: 'running' },
  { label: '已取消', value: 'cancelled' },
  { label: '超时', value: 'timeout' },
  { label: '取消失败', value: 'kill_failed' },
]

const statusTextMap: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  timeout: '超时',
  kill_failed: '取消失败',
}

const statusClassMap: Record<string, string> = {
  pending: 'bg-slate-100 text-slate-600',
  running: 'bg-blue-50 text-blue-600',
  succeeded: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700',
  cancelled: 'bg-amber-50 text-amber-700',
  timeout: 'bg-orange-50 text-orange-700',
  kill_failed: 'bg-red-50 text-red-700',
}

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 19)
}

const isColumnVisible = (key: string) => visibleColumnSet.value.has(key)
const getStatusText = (item: SqlHistoryItem) => statusTextMap[item.sql_status || ''] || item.sql_status || '-'
const getStatusClass = (item: SqlHistoryItem) => statusClassMap[item.sql_status || ''] || 'bg-slate-100 text-slate-600'
const getTaskStatusText = (item: SqlHistoryItem) => statusTextMap[item.task_status || ''] || item.task_status || '-'
const formatDurationSeconds = (value?: number | null) => {
  const milliseconds = Number(value)
  if (!Number.isFinite(milliseconds)) return '-'
  const seconds = milliseconds / 1000
  const decimalPlaces = seconds < 1 ? 3 : 2
  return `${seconds.toFixed(decimalPlaces).replace(/\.?0+$/, '')} 秒`
}

const cleanDisplayError = (value?: string | null) => {
  const rawText = String(value || '').replace(/\r\n?/g, '\n')
  const withoutAnsiEscapes = rawText
    .replace(/\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '')
    .replace(/\[(?:\d{1,3};)*\d{1,3}m/g, '')
  return withoutAnsiEscapes.trim() || '暂无错误信息'
}

const formatNullableText = (value?: string | number | null) => {
  const text = String(value ?? '').trim()
  return text || '-'
}

const getPanelExecutionPlanMemberIds = (item: SqlHistoryItem) => {
  const plan = item.panel_execution_plan
  if (!plan || typeof plan !== 'object') return []
  const result: string[] = []
  Object.values(plan).forEach((entry) => {
    const ids = Array.isArray(entry?.member_datasource_ids) ? entry.member_datasource_ids : []
    ids.forEach((rawId) => {
      const memberId = String(rawId || '').trim()
      if (memberId && !result.includes(memberId)) {
        result.push(memberId)
      }
    })
  })
  return result
}

const getExecutionTargetText = (item: SqlHistoryItem) => {
  const memberIds = getPanelExecutionPlanMemberIds(item)
  if ((item.query_type === 'dashboard' || item.source === '看板') && memberIds.length > 1) {
    return `${memberIds.length}个执行对象`
  }
  return item.member_name || item.member_datasource_id || '-'
}

const loadHistories = async () => {
  loading.value = true
  try {
    const response = await getGlobalQueryHistory({
      datasource_id: filters.value.datasource_id || undefined,
      username: filters.value.username.trim() || undefined,
      keyword: filters.value.keyword.trim() || undefined,
      source: filters.value.source as 'all' | 'sql' | 'agent' | 'builder' | 'dashboard',
      status: filters.value.status as 'all' | 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timeout' | 'kill_failed',
      start_time: filters.value.time_range?.[0],
      end_time: filters.value.time_range?.[1],
      page: page.value,
      page_size: pageSize.value,
    })
    if (response.status !== 'success') {
      ElMessage.error(response.msg || '加载历史 SQL 失败')
      return
    }
    histories.value = response.data?.queries || []
    total.value = Number(response.data?.total || 0)
    hasLoaded.value = true
  } catch (error) {
    console.error('加载历史 SQL 失败:', error)
    ElMessage.error('加载历史 SQL 失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  page.value = 1
  loadHistories()
}

const handleReset = () => {
  filters.value = {
    datasource_id: '',
    username: '',
    keyword: '',
    source: 'all',
    status: 'all',
    time_range: buildDefaultTimeRange(),
  }
  page.value = 1
  loadHistories()
}

const copySql = async (sql?: string) => {
  const text = String(sql || '').trim()
  if (!text) {
    ElMessage.warning('暂无 SQL 可复制')
    return
  }
  await navigator.clipboard.writeText(text)
  ElMessage.success('已复制 SQL')
}

const getSelectedDatasourceName = () => {
  const datasourceId = filters.value.datasource_id
  if (!datasourceId) return ''
  return datasourceOptions.value.find(item => item.id === datasourceId)?.group_name || datasourceId
}

const stopRunningSql = async () => {
  const datasourceName = getSelectedDatasourceName()
  const scopeText = datasourceName ? `数据源「${datasourceName}」` : '全局'
  try {
    await ElMessageBox.confirm(
      `确定要停止${scopeText}所有正在执行的 SQL 吗？该操作会停止等待中和执行中的任务。`,
      '停止SQL',
      {
        confirmButtonText: '停止',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  stoppingRunning.value = true
  try {
    const response = await cancelGlobalRunningQueryHistory({
      datasource_id: filters.value.datasource_id || undefined,
    })
    if (response.status !== 'success') {
      ElMessage.error(response.msg || '批量停止失败')
      return
    }
    const data = response.data
    if (!data || data.total === 0) {
      ElMessage.info('当前没有正在执行的 SQL')
    } else if (data.failed > 0) {
      ElMessage.warning(`已提交 ${data.total} 条停止请求，${data.failed} 条停止失败`)
    } else {
      ElMessage.success(`已提交 ${data.cancelled} 条停止请求`)
    }
    await loadHistories()
  } catch (error) {
    console.error('批量停止执行中 SQL 失败:', error)
    ElMessage.error('批量停止失败，请稍后重试')
  } finally {
    stoppingRunning.value = false
  }
}

const showError = (item: SqlHistoryItem) => {
  errorDialogMessage.value = cleanDisplayError(item.error_message)
  errorDialogVisible.value = true
}

const copyErrorMessage = async () => {
  const text = errorDialogMessage.value.trim()
  if (!text) {
    ElMessage.warning('暂无错误信息可复制')
    return
  }
  await navigator.clipboard.writeText(text)
  ElMessage.success('已复制错误信息')
}

const handlePageChange = (nextPage: number) => {
  page.value = nextPage
  loadHistories()
}

const handlePageSizeChange = (nextSize: number) => {
  pageSize.value = nextSize
  page.value = 1
  loadHistories()
}

const ensureLoaded = () => {
  if (hasLoaded.value || loading.value) return
  loadHistories()
}

defineExpose({
  ensureLoaded,
})
</script>

<template>
  <div class="chatdata-root flex h-full min-h-0 flex-col bg-white">
    <div class="shrink-0 border-b border-slate-100 p-3">
      <div class="flex flex-wrap items-center gap-2">
        <select v-model="filters.datasource_id" class="h-8 w-48 rounded border border-slate-200 bg-white px-2 text-xs text-slate-700 outline-none">
          <option value="">全部数据源</option>
          <option v-for="ds in datasourceOptions" :key="ds.id" :value="ds.id">{{ ds.group_name }}</option>
        </select>
        <select v-model="filters.source" class="h-8 w-28 rounded border border-slate-200 bg-white px-2 text-xs text-slate-700 outline-none">
          <option v-for="item in sourceOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <select v-model="filters.status" class="h-8 w-28 rounded border border-slate-200 bg-white px-2 text-xs text-slate-700 outline-none">
          <option v-for="item in statusOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <el-date-picker
          v-model="filters.time_range"
          type="datetimerange"
          value-format="YYYY-MM-DD HH:mm:ss"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          class="global-sql-history-time-picker"
        />
        <input v-model="filters.username" class="h-8 w-40 rounded border border-slate-200 px-2 text-xs text-slate-700 outline-none" placeholder="用户名" @keyup.enter="handleSearch" />
        <input v-model="filters.keyword" class="h-8 min-w-[220px] flex-1 rounded border border-slate-200 px-2 text-xs text-slate-700 outline-none" placeholder="SQL 关键词" @keyup.enter="handleSearch" />
        <button class="h-8 rounded bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700" @click="handleSearch">查询</button>
        <button class="h-8 rounded border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50" @click="handleReset">重置</button>
        <button
          class="h-8 rounded border border-slate-200 px-3 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="stoppingRunning"
          @click="stopRunningSql"
        >
          {{ stoppingRunning ? '停止中...' : '停止SQL' }}
        </button>
        <el-dropdown trigger="click" :hide-on-click="false">
          <button class="h-8 rounded border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50">
            字段显示
            <i class="fa-solid fa-chevron-down ml-1 text-[10px] text-slate-400"></i>
          </button>
          <template #dropdown>
            <div class="w-48 px-2 py-2">
              <el-checkbox-group v-model="visibleColumnKeys" class="global-sql-history-column-menu">
                <el-checkbox
                  v-for="column in columnOptions"
                  :key="column.key"
                  :label="column.key"
                >
                  {{ column.label }}
                </el-checkbox>
              </el-checkbox-group>
            </div>
          </template>
        </el-dropdown>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-auto">
      <table class="min-w-full border-separate border-spacing-0 text-left text-xs">
        <thead class="sticky top-0 z-10 bg-slate-50 text-slate-500">
          <tr>
            <th v-if="isColumnVisible('time')" class="border-b border-slate-100 px-3 py-2 font-medium">时间</th>
            <th v-if="isColumnVisible('source')" class="border-b border-slate-100 px-3 py-2 font-medium">来源</th>
            <th v-if="isColumnVisible('status')" class="border-b border-slate-100 px-3 py-2 font-medium">状态</th>
            <th v-if="isColumnVisible('user')" class="border-b border-slate-100 px-3 py-2 font-medium">用户</th>
            <th v-if="isColumnVisible('member')" class="border-b border-slate-100 px-3 py-2 font-medium">执行对象</th>
            <th v-if="isColumnVisible('duration')" class="border-b border-slate-100 px-3 py-2 font-medium">耗时</th>
            <th v-if="isColumnVisible('sql')" class="border-b border-slate-100 px-3 py-2 font-medium">SQL</th>
            <th v-if="isColumnVisible('execution_id')" class="border-b border-slate-100 px-3 py-2 font-medium">执行ID</th>
            <th v-if="isColumnVisible('task_id')" class="border-b border-slate-100 px-3 py-2 font-medium">任务ID</th>
            <th v-if="isColumnVisible('datasource_id')" class="border-b border-slate-100 px-3 py-2 font-medium">数据源ID</th>
            <th v-if="isColumnVisible('member_datasource_id')" class="border-b border-slate-100 px-3 py-2 font-medium">成员ID</th>
            <th v-if="isColumnVisible('query_type')" class="border-b border-slate-100 px-3 py-2 font-medium">查询类型</th>
            <th v-if="isColumnVisible('task_status')" class="border-b border-slate-100 px-3 py-2 font-medium">任务状态</th>
            <th v-if="isColumnVisible('row_count')" class="border-b border-slate-100 px-3 py-2 font-medium">结果行数</th>
            <th v-if="isColumnVisible('started_time')" class="border-b border-slate-100 px-3 py-2 font-medium">开始时间</th>
            <th v-if="isColumnVisible('finished_time')" class="border-b border-slate-100 px-3 py-2 font-medium">结束时间</th>
            <th v-if="isColumnVisible('error')" class="border-b border-slate-100 px-3 py-2 font-medium">错误信息</th>
            <th v-if="isColumnVisible('actions')" class="border-b border-slate-100 px-3 py-2 font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td :colspan="visibleColumnCount" class="px-3 py-12 text-center text-slate-400">
              <i class="fa-solid fa-spinner fa-spin mr-1"></i>
              加载中...
            </td>
          </tr>
          <tr v-else-if="histories.length === 0">
            <td :colspan="visibleColumnCount" class="px-3 py-12 text-center text-slate-400">暂无历史 SQL</td>
          </tr>
          <tr v-for="item in histories" v-else :key="item.execution_id || item.id" class="hover:bg-slate-50">
            <td v-if="isColumnVisible('time')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-500">{{ formatTime(item.executed_at || item.last_used) }}</td>
            <td v-if="isColumnVisible('source')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-600">{{ item.source || '-' }}</td>
            <td v-if="isColumnVisible('status')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2">
              <span :class="['rounded-full px-2 py-0.5', getStatusClass(item)]">{{ getStatusText(item) }}</span>
            </td>
            <td v-if="isColumnVisible('user')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-600">{{ item.operator_name || item.user_id || '-' }}</td>
            <td
              v-if="isColumnVisible('member')"
              class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-600"
              :title="getExecutionTargetText(item)"
            >
              {{ getExecutionTargetText(item) }}
            </td>
            <td v-if="isColumnVisible('duration')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-500">{{ formatDurationSeconds(item.execution_time_ms) }}</td>
            <td v-if="isColumnVisible('sql')" class="max-w-[460px] border-b border-slate-50 px-3 py-2">
              <div class="truncate font-mono text-[11px] text-slate-700" :title="item.query">{{ item.query }}</div>
            </td>
            <td v-if="isColumnVisible('execution_id')" class="max-w-[220px] border-b border-slate-50 px-3 py-2">
              <div class="truncate text-slate-500" :title="item.execution_id">{{ formatNullableText(item.execution_id) }}</div>
            </td>
            <td v-if="isColumnVisible('task_id')" class="max-w-[220px] border-b border-slate-50 px-3 py-2">
              <div class="truncate text-slate-500" :title="item.task_id">{{ formatNullableText(item.task_id) }}</div>
            </td>
            <td v-if="isColumnVisible('datasource_id')" class="max-w-[180px] border-b border-slate-50 px-3 py-2">
              <div class="truncate text-slate-500" :title="item.datasource_id">{{ formatNullableText(item.datasource_id) }}</div>
            </td>
            <td v-if="isColumnVisible('member_datasource_id')" class="max-w-[180px] border-b border-slate-50 px-3 py-2">
              <div class="truncate text-slate-500" :title="item.member_datasource_id">{{ formatNullableText(item.member_datasource_id) }}</div>
            </td>
            <td v-if="isColumnVisible('query_type')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-500">{{ formatNullableText(item.query_type) }}</td>
            <td v-if="isColumnVisible('task_status')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-500">{{ getTaskStatusText(item) }}</td>
            <td v-if="isColumnVisible('row_count')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-500">{{ item.result_row_count ?? '-' }}</td>
            <td v-if="isColumnVisible('started_time')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-500">{{ formatTime(item.started_time) }}</td>
            <td v-if="isColumnVisible('finished_time')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2 text-slate-500">{{ formatTime(item.finished_time) }}</td>
            <td v-if="isColumnVisible('error')" class="max-w-[320px] border-b border-slate-50 px-3 py-2">
              <div class="truncate text-slate-500" :title="item.error_message || ''">{{ item.error_message || '-' }}</div>
            </td>
            <td v-if="isColumnVisible('actions')" class="whitespace-nowrap border-b border-slate-50 px-3 py-2">
              <button class="rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50" @click="copySql(item.query)">复制 SQL</button>
              <button v-if="item.error_message" class="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50" @click="showError(item)">错误</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex shrink-0 items-center justify-between border-t border-slate-100 px-3 py-2">
      <span class="text-xs text-slate-400">共 {{ total }} 条</span>
      <el-pagination
        small
        background
        layout="sizes, prev, pager, next"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        @current-change="handlePageChange"
        @size-change="handlePageSizeChange"
      />
    </div>

    <el-dialog
      v-model="errorDialogVisible"
      title="执行错误"
      width="min(860px, 92vw)"
      append-to-body
      class="global-sql-history-error-dialog"
    >
      <pre class="error-detail-text">{{ errorDialogMessage }}</pre>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <button class="h-8 rounded border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50" @click="copyErrorMessage">
            复制错误信息
          </button>
          <button class="h-8 rounded bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700" @click="errorDialogVisible = false">
            关闭
          </button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.global-sql-history-time-picker {
  width: 320px;
  height: 32px;
}

.global-sql-history-column-menu {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: min(60vh, 460px);
  overflow-y: auto;
  padding-right: 4px;
}

.global-sql-history-column-menu :deep(.el-checkbox) {
  width: 100%;
  margin-right: 0;
}

.error-detail-text {
  max-height: min(58vh, 520px);
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border-radius: 8px;
  background: #f8fafc;
  padding: 12px;
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
  line-height: 1.7;
}
</style>
