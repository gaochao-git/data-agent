<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { createQueryTask, getQueryTask } from '../../api'
import { isQuerySucceeded, quoteIdentifier } from '../../utils'

const props = defineProps<{
  datasourceId: string
  tableName: string
}>()

const visible = defineModel<boolean>('visible', { default: false })
const previewingTableName = defineModel<string>('previewingTableName', { default: '' })

const tablePreviewColumns = ref<string[]>([])
const tablePreviewRows = ref<Record<string, unknown>[]>([])
const tablePreviewError = ref('')
const tablePreviewMode = ref<'table' | 'json'>('table')

const formatJsonPreview = (value: unknown) => {
  if (value === null || value === undefined) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch (_err) {
    return String(value)
  }
}

const tablePreviewJsonText = computed(() => {
  if (tablePreviewRows.value.length === 0) return '[]'
  return formatJsonPreview(tablePreviewRows.value.length === 1 ? tablePreviewRows.value[0] : tablePreviewRows.value)
})

const quoteTableIdentifier = (name: string) => {
  return (name || '')
    .split('.')
    .map(part => quoteIdentifier(part.trim()))
    .join('.')
}

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))

const waitTablePreviewTask = async (taskId: string, pollIntervalMs = 1500) => {
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    const response = await getQueryTask(taskId)
    const task = response.data
    if (!task) {
      throw new Error(response.msg || '查询任务不存在')
    }
    if (task.status === 'succeeded' || task.status === 'partial_success') {
      return task
    }
    if (task.status === 'failed' || task.status === 'cancelled') {
      throw new Error(task.error_message || '查询失败')
    }
    await sleep(Math.max(500, pollIntervalMs))
  }
  throw new Error('查询超时，请稍后重试')
}

const previewTableData = async (tableName: string) => {
  if (!props.datasourceId || !tableName) return
  previewingTableName.value = tableName
  tablePreviewColumns.value = []
  tablePreviewRows.value = []
  tablePreviewError.value = ''
  tablePreviewMode.value = 'table'
  try {
    const createResp = await createQueryTask({
      datasource_id: props.datasourceId,
      sql: [`SELECT * FROM ${quoteTableIdentifier(tableName)} LIMIT 1`],
      limit: 1,
      query_type: 'sql',
      query_text: '表数据预览',
    })
    const taskId = createResp.data?.task_id
    if (createResp.status !== 'success' || !taskId) {
      tablePreviewError.value = createResp.msg || '查询任务创建失败'
      return
    }
    const task = await waitTablePreviewTask(taskId, createResp.data?.poll_interval_ms)
    const taskResults = Array.isArray(task.results) ? task.results : []
    const firstResult = taskResults.find(result => isQuerySucceeded(result)) || taskResults[0] || null
    if (!firstResult) {
      tablePreviewError.value = task.error_message || '查询结果为空'
      return
    }
    if (!isQuerySucceeded(firstResult)) {
      tablePreviewError.value = firstResult.error || '查询失败'
      return
    }
    const rows = firstResult.data || []
    const cols = (firstResult.columns && firstResult.columns.length > 0)
      ? firstResult.columns
      : (rows.length > 0 ? Object.keys(rows[0] ?? {}) : [])
    tablePreviewColumns.value = cols
    tablePreviewRows.value = rows
  } catch (err) {
    tablePreviewError.value = err instanceof Error ? err.message : '查询失败'
  } finally {
    previewingTableName.value = ''
  }
}

watch(visible, (value) => {
  if (value) void previewTableData(props.tableName)
})
</script>

<template>
  <el-dialog v-model="visible" width="820px" :title="`表数据预览 - ${tableName}`">
    <div v-if="previewingTableName" class="flex h-32 items-center justify-center text-sm text-gray-400">
      <i class="fa-solid fa-spinner fa-spin mr-2"></i>查询中...
    </div>
    <div v-else-if="tablePreviewError" class="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
      {{ tablePreviewError }}
    </div>
    <div v-else-if="tablePreviewRows.length === 0" class="flex h-32 items-center justify-center text-sm text-gray-400">
      暂无数据
    </div>
    <div v-else class="space-y-2">
      <div class="flex justify-end">
        <div class="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-[12px]">
          <button
            :class="[
              'rounded px-2.5 py-1 transition-colors',
              tablePreviewMode === 'table' ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:text-blue-600'
            ]"
            @click="tablePreviewMode = 'table'"
          >
            表格
          </button>
          <button
            :class="[
              'rounded px-2.5 py-1 transition-colors',
              tablePreviewMode === 'json' ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:text-blue-600'
            ]"
            @click="tablePreviewMode = 'json'"
          >
            JSON
          </button>
        </div>
      </div>
      <div v-if="tablePreviewMode === 'table'" class="max-h-[420px] overflow-auto rounded-md border border-gray-200">
        <table class="min-w-full border-collapse text-[12px]">
          <thead class="sticky top-0 bg-gray-50 text-gray-600">
            <tr>
              <th
                v-for="column in tablePreviewColumns"
                :key="`preview-head-${column}`"
                class="border border-gray-200 px-3 py-2 text-left font-medium whitespace-nowrap"
              >
                {{ column }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in tablePreviewRows" :key="`preview-row-${rowIndex}`">
              <td
                v-for="column in tablePreviewColumns"
                :key="`preview-cell-${rowIndex}-${column}`"
                class="max-w-[260px] truncate border border-gray-200 px-3 py-2 text-gray-700"
                :title="String(row[column] ?? '')"
              >
                {{ row[column] ?? '' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <pre v-else class="max-h-[420px] overflow-auto rounded-md border border-gray-200 bg-gray-50 p-3 text-[12px] leading-relaxed text-gray-700">{{ tablePreviewJsonText }}</pre>
    </div>
  </el-dialog>
</template>
