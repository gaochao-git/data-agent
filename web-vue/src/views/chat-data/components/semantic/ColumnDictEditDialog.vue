<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getDatasourceColumnDict,
  getAdminDatasourceColumnDict,
  updateAdminDatasourceColumnDict,
  generateAdminDatasourceColumnDict,
} from '../../api'
import type { DatasourceColumnDictPayload, DatasourceColumnDictEnumItem } from '../../api'

const props = withDefaults(defineProps<{
  datasourceId: string
  tableName: string
  columnName: string
  adminMode?: boolean
}>(), {
  adminMode: false,
})

const visible = defineModel<boolean>('visible', { default: false })

const emit = defineEmits<{
  saved: [payload: DatasourceColumnDictPayload]
}>()

const columnDictRows = ref<DatasourceColumnDictEnumItem[]>([])
const columnDictSynonyms = ref<string[]>([])
const columnDictStorageFormat = ref('')
const columnDictUnit = ref('')
const loadingColumnDict = ref(false)
const savingColumnDict = ref(false)
const generatingColumnDict = ref(false)
const columnDictGenerateText = ref('')

const emptyColumnDict = (): DatasourceColumnDictPayload => ({
  enum_values: [],
  synonyms: [],
  storage_format: '',
  unit: '',
})

const applyColumnDictForm = (dict?: Partial<DatasourceColumnDictPayload>) => {
  const payload = { ...emptyColumnDict(), ...(dict || {}) }
  columnDictRows.value = (payload.enum_values || []).map(item => ({
    value: String(item.value || '').trim(),
    label: String(item.label || '').trim(),
  }))
  columnDictSynonyms.value = (payload.synonyms || []).map(item => String(item || '').trim()).filter(Boolean)
  columnDictStorageFormat.value = String(payload.storage_format || '').trim()
  columnDictUnit.value = String(payload.unit || '').trim()
}

let loadSeq = 0

watch(visible, async (value) => {
  if (!value) return
  const seq = ++loadSeq
  applyColumnDictForm()
  columnDictGenerateText.value = ''
  loadingColumnDict.value = true
  try {
    const getColumnDict = props.adminMode ? getAdminDatasourceColumnDict : getDatasourceColumnDict
    const res = await getColumnDict(props.datasourceId, props.tableName, props.columnName)
    if (seq !== loadSeq) return
    if (res.status === 'success') {
      applyColumnDictForm(res.data?.dict)
    } else {
      ElMessage.error(res.msg || '获取字段字典失败')
    }
  } catch {
    if (seq === loadSeq) {
      ElMessage.error('获取字段字典失败')
    }
  } finally {
    if (seq === loadSeq) {
      loadingColumnDict.value = false
    }
  }
})

const closeColumnDictModal = () => {
  if (savingColumnDict.value || generatingColumnDict.value) return
  visible.value = false
}

const addColumnDictEnumRow = () => {
  if (!props.adminMode || loadingColumnDict.value) return
  columnDictRows.value = [...columnDictRows.value, { value: '', label: '' }]
}

const removeColumnDictEnumRow = (index: number) => {
  if (!props.adminMode) return
  columnDictRows.value = columnDictRows.value.filter((_, itemIndex) => itemIndex !== index)
}

const normalizeColumnDictRows = () => {
  const seen = new Set<string>()
  const result: DatasourceColumnDictEnumItem[] = []
  for (const row of columnDictRows.value) {
    const enumValue = String(row.value || '').trim()
    if (!enumValue || seen.has(enumValue)) continue
    seen.add(enumValue)
    result.push({
      value: enumValue,
      label: String(row.label || '').trim(),
    })
  }
  return result
}

const mergeGeneratedColumnDict = (generatedDict: Partial<DatasourceColumnDictPayload>) => {
  const nextRows = columnDictRows.value.map(row => ({
    value: String(row.value || '').trim(),
    label: String(row.label || '').trim(),
  }))
  const indexMap = new Map<string, number>()
  nextRows.forEach((row, index) => {
    if (row.value && !indexMap.has(row.value)) {
      indexMap.set(row.value, index)
    }
  })
  let addedCount = 0
  let filledCount = 0
  ;(generatedDict.enum_values || []).forEach(item => {
    const enumValue = String(item.value || '').trim()
    const enumLabel = String(item.label || '').trim()
    if (!enumValue) return
    const existingIndex = indexMap.get(enumValue)
    if (existingIndex !== undefined) {
      const existingRow = nextRows[existingIndex]
      if (existingRow && !existingRow.label && enumLabel) {
        existingRow.label = enumLabel
        filledCount += 1
      }
      return
    }
    indexMap.set(enumValue, nextRows.length)
    nextRows.push({ value: enumValue, label: enumLabel })
    addedCount += 1
  })
  columnDictRows.value = nextRows

  let unitFilledCount = 0
  if (!columnDictUnit.value.trim() && generatedDict.unit) {
    columnDictUnit.value = String(generatedDict.unit || '').trim()
    unitFilledCount = 1
  }
  return { addedCount, filledCount, unitFilledCount }
}

const generateColumnDict = async () => {
  if (!props.adminMode) return
  const text = columnDictGenerateText.value.trim()
  if (!text) {
    ElMessage.warning('请输入用于生成字段字典的文本')
    return
  }
  const seq = ++loadSeq
  generatingColumnDict.value = true
  try {
    const res = await generateAdminDatasourceColumnDict(
      props.datasourceId,
      props.tableName,
      props.columnName,
      text
    )
    if (seq !== loadSeq) return
    if (res.status === 'success') {
      const generatedDict = res.data?.dict || emptyColumnDict()
      if (
        (generatedDict.enum_values || []).length === 0 &&
        !generatedDict.unit
      ) {
        ElMessage.warning('未识别到明确的字段字典信息')
        return
      }
      const { addedCount, filledCount, unitFilledCount } = mergeGeneratedColumnDict(generatedDict)
      ElMessage.success(`已生成候选，新增枚举 ${addedCount} 个，补充枚举 ${filledCount} 个，补充单位 ${unitFilledCount} 个`)
    } else {
      ElMessage.error(res.msg || '生成字段字典失败')
    }
  } catch {
    if (seq === loadSeq) {
      ElMessage.error('生成字段字典失败')
    }
  } finally {
    if (seq === loadSeq) {
      generatingColumnDict.value = false
    }
  }
}

const saveColumnDict = async () => {
  if (!props.adminMode) return
  const duplicateValues = columnDictRows.value
    .map(row => String(row.value || '').trim())
    .filter(Boolean)
    .filter((value, index, values) => values.indexOf(value) !== index)
  if (duplicateValues.length > 0) {
    ElMessage.warning('枚举值不能重复')
    return
  }
  const payload: DatasourceColumnDictPayload = {
    enum_values: normalizeColumnDictRows(),
    synonyms: columnDictSynonyms.value,
    storage_format: columnDictStorageFormat.value.trim(),
    unit: columnDictUnit.value.trim(),
  }
  savingColumnDict.value = true
  try {
    const res = await updateAdminDatasourceColumnDict(
      props.datasourceId,
      props.tableName,
      props.columnName,
      payload
    )
    if (res.status === 'success') {
      ElMessage.success('字段字典已保存')
      visible.value = false
      emit('saved', payload)
    } else {
      ElMessage.error(res.msg || '保存字段字典失败')
    }
  } catch {
    ElMessage.error('保存字段字典失败')
  } finally {
    savingColumnDict.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" width="760px" :close-on-click-modal="!savingColumnDict" :show-close="false">
    <template #header>
      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0 flex items-center gap-2">
          <i class="fa-solid fa-book text-blue-600"></i>
          <h3 class="m-0 truncate text-lg font-semibold text-slate-900">
            字段数据字典<span v-if="tableName && columnName"> - {{ tableName }}.{{ columnName }}</span>
          </h3>
        </div>
        <button
          type="button"
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          @click="closeColumnDictModal"
        >
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </template>

    <div class="space-y-3">
      <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-500">
        {{ props.adminMode
          ? '字段字典用于维护物理字段的枚举值、数据存储格式和单位，供语义模型展示和智能问数理解字段。'
          : '字段字典由系统统一维护，当前页面仅供查看，内容用于语义模型展示和智能问数理解字段。' }}
      </div>

      <div v-if="loadingColumnDict" class="flex min-h-[220px] flex-col items-center justify-center text-slate-400">
        <i class="fa-solid fa-spinner fa-spin mb-2 text-xl"></i>
        <span class="text-sm">正在加载字段字典...</span>
      </div>
      <template v-else>
        <div v-if="props.adminMode" class="rounded-xl border border-blue-100 bg-blue-50 p-3">
          <div class="mb-2 flex items-center justify-between gap-2">
            <div class="flex items-center gap-2 text-xs font-medium text-blue-700">
              <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i>
              <span>AI 生成字段字典</span>
            </div>
            <button
              type="button"
              :disabled="generatingColumnDict || !columnDictGenerateText.trim()"
              class="flex items-center gap-1.5 rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              @click="generateColumnDict"
            >
              <i :class="['fa-solid', generatingColumnDict ? 'fa-spinner fa-spin' : 'fa-wand-magic-sparkles', 'text-[10px]']"></i>
              <span>{{ generatingColumnDict ? '生成中' : '生成候选' }}</span>
            </button>
          </div>
          <textarea
            v-model="columnDictGenerateText"
            maxlength="10000"
            rows="3"
            placeholder="粘贴字段说明，例如：is_active 逻辑状态，1 表示在线，0 表示下线；amount 单位为元"
            class="w-full resize-none rounded border border-blue-100 bg-white px-2.5 py-2 text-xs leading-5 text-slate-700 outline-none focus:border-blue-500"
          ></textarea>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white">
          <div
            :class="[
              props.adminMode ? 'grid-cols-[1fr_1.5fr_56px]' : 'grid-cols-[1fr_1.5fr]',
              'grid gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500',
            ]"
          >
            <span>枚举值</span>
            <span>枚举含义</span>
            <span v-if="props.adminMode" class="text-right">操作</span>
          </div>
          <div class="max-h-[320px] overflow-y-auto p-2">
            <div v-if="columnDictRows.length === 0" class="flex min-h-[120px] items-center justify-center text-sm text-slate-400">
              暂无枚举值
            </div>
            <div
              v-for="(row, index) in columnDictRows"
              :key="index"
              :class="[
                props.adminMode ? 'grid-cols-[1fr_1.5fr_56px]' : 'grid-cols-[1fr_1.5fr]',
                'grid gap-2 rounded px-1 py-1.5 hover:bg-slate-50',
              ]"
            >
              <input
                v-model="row.value"
                type="text"
                maxlength="128"
                :readonly="!props.adminMode"
                placeholder="如 1"
                class="min-w-0 rounded border border-slate-200 px-2 py-1.5 text-xs font-mono text-slate-700 outline-none read-only:bg-slate-50 read-only:text-slate-500 focus:border-blue-500"
              />
              <input
                v-model="row.label"
                type="text"
                maxlength="255"
                :readonly="!props.adminMode"
                placeholder="如 在线"
                class="min-w-0 rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-700 outline-none read-only:bg-slate-50 read-only:text-slate-500 focus:border-blue-500"
              />
              <button
                v-if="props.adminMode"
                type="button"
                class="justify-self-end rounded px-2 py-1 text-xs text-red-500 hover:bg-red-50"
                @click="removeColumnDictEnumRow(index)"
              >删除</button>
            </div>
            <button
              v-if="props.adminMode"
              type="button"
              :disabled="loadingColumnDict"
              class="mt-2 flex items-center gap-1.5 rounded bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50"
              @click="addColumnDictEnumRow"
            >
              <i class="fa-solid fa-plus text-[10px]"></i>
              <span>新增枚举</span>
            </button>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-slate-600">数据存储格式</span>
            <input
              v-model="columnDictStorageFormat"
              type="text"
              maxlength="255"
              :readonly="!props.adminMode"
              placeholder="如 yyyyMMdd、yyyy-MM-dd HH:mm:ss"
              class="w-full rounded border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 outline-none read-only:bg-slate-50 read-only:text-slate-500 focus:border-blue-500"
            />
          </label>
          <label class="block">
            <span class="mb-1 block text-xs font-medium text-slate-600">单位</span>
            <input
              v-model="columnDictUnit"
              type="text"
              maxlength="100"
              :readonly="!props.adminMode"
              placeholder="如 元、万元、秒"
              class="w-full rounded border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 outline-none read-only:bg-slate-50 read-only:text-slate-500 focus:border-blue-500"
            />
          </label>
        </div>

      </template>

      <div class="flex items-center justify-end">
        <div class="flex items-center gap-2">
          <button
            type="button"
            :disabled="savingColumnDict"
            class="rounded px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            @click="closeColumnDictModal"
          >{{ props.adminMode ? '取消' : '关闭' }}</button>
          <button
            v-if="props.adminMode"
            type="button"
            :disabled="savingColumnDict || loadingColumnDict"
            class="flex items-center gap-2 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            @click="saveColumnDict"
          >
            <i v-if="savingColumnDict" class="fa-solid fa-spinner fa-spin"></i>
            <i v-else class="fa-solid fa-floppy-disk"></i>
            <span>{{ savingColumnDict ? '保存中...' : '保存字典' }}</span>
          </button>
        </div>
      </div>
    </div>
  </el-dialog>
</template>
