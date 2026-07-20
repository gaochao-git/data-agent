<script setup lang="ts">
import { ref, watch } from 'vue'
import { getDatasourceColumnDict, type DatasourceColumnDictPayload } from '../../api'
import { hasFieldDictContent } from '../../utils/fieldDict'

const props = defineProps<{
  datasourceId: string
  tableName: string
  columnName: string
}>()

const visible = defineModel<boolean>('visible', { default: false })

const loadingColumnDict = ref(false)
const columnDictPayload = ref<DatasourceColumnDictPayload | null>(null)
const columnDictError = ref('')

watch(visible, async (value) => {
  if (!value) return
  columnDictPayload.value = null
  columnDictError.value = ''
  loadingColumnDict.value = true
  try {
    const res = await getDatasourceColumnDict(props.datasourceId, props.tableName, props.columnName)
    if (res.status !== 'success') {
      columnDictError.value = res.msg || '获取字段字典失败'
      return
    }
    columnDictPayload.value = res.data?.dict || null
  } catch (err) {
    const apiMsg = (err as { response?: { data?: { msg?: string } } } | null)?.response?.data?.msg
    const apiDetail = (err as { response?: { data?: { detail?: string } } } | null)?.response?.data?.detail
    columnDictError.value = apiMsg || apiDetail || (err instanceof Error ? err.message : '获取字段字典失败')
  } finally {
    loadingColumnDict.value = false
  }
})
</script>

<template>
  <el-dialog v-model="visible" width="620px" :title="`字段字典 - ${tableName}.${columnName}`">
    <div v-if="loadingColumnDict" class="flex h-32 items-center justify-center text-sm text-gray-400">
      <i class="fa-solid fa-spinner fa-spin mr-2"></i>加载中...
    </div>
    <div v-else-if="columnDictError" class="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
      {{ columnDictError }}
    </div>
    <div v-else-if="!hasFieldDictContent(columnDictPayload)" class="flex h-32 items-center justify-center text-sm text-gray-400">
      暂无字段字典
    </div>
    <div v-else class="max-h-[460px] space-y-3 overflow-auto text-[12px]">
      <div v-if="columnDictPayload?.enum_values?.length" class="rounded-md border border-gray-200">
        <div class="border-b border-gray-200 bg-gray-50 px-3 py-2 font-medium text-gray-600">枚举值</div>
        <table class="min-w-full border-collapse">
          <thead class="bg-gray-50 text-gray-500">
            <tr>
              <th class="border border-gray-200 px-3 py-2 text-left font-medium">枚举值</th>
              <th class="border border-gray-200 px-3 py-2 text-left font-medium">枚举含义</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in columnDictPayload.enum_values" :key="item.value">
              <td class="border border-gray-200 px-3 py-2 font-mono text-gray-700">{{ item.value }}</td>
              <td class="border border-gray-200 px-3 py-2 text-gray-700">{{ item.label || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="columnDictPayload?.storage_format || columnDictPayload?.unit" class="grid grid-cols-2 gap-3">
        <div v-if="columnDictPayload?.storage_format" class="rounded-md border border-gray-200">
          <div class="border-b border-gray-200 bg-gray-50 px-3 py-2 font-medium text-gray-600">数据存储格式</div>
          <div class="px-3 py-2 text-gray-700">{{ columnDictPayload.storage_format }}</div>
        </div>
        <div v-if="columnDictPayload?.unit" class="rounded-md border border-gray-200">
          <div class="border-b border-gray-200 bg-gray-50 px-3 py-2 font-medium text-gray-600">单位</div>
          <div class="px-3 py-2 text-gray-700">{{ columnDictPayload.unit }}</div>
        </div>
      </div>
    </div>
  </el-dialog>
</template>
