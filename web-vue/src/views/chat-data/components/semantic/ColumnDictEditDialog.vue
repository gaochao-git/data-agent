<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getDatasourceColumnDict,
  getAdminDatasourceColumnDict,
  updateAdminDatasourceColumnDict,
  generateAdminDatasourceColumnDict,
  getAdminMetaEnums,
  getAdminMetaEnum,
  upsertAdminMetaEnum,
} from '../../api'
import type {
  DatasourceColumnDictPayload,
  DatasourceColumnDictEnumItem,
} from '../../api'
import type { MetaGlobalEnum } from '../../types'

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

const columnDictEnumRef = ref('')
const columnDictRows = ref<DatasourceColumnDictEnumItem[]>([])
const columnDictStorageFormat = ref('')
const columnDictUnit = ref('')
const loadingColumnDict = ref(false)
const savingColumnDict = ref(false)
const generatingColumnDict = ref(false)
const columnDictGenerateText = ref('')

// 全局枚举引用：候选列表 + 引用态的枚举内容预览（二选一：引用后本地枚举值置空）
const globalEnumOptions = ref<MetaGlobalEnum[]>([])
const enumOptionsLoading = ref(false)
const refEnumDetail = ref<MetaGlobalEnum | null>(null)
const refDetailLoading = ref(false)

const isRefMode = computed(() => !!columnDictEnumRef.value.trim())

// 引用项展示标签：通用域仅枚举名，业务域 "域名 / 枚举名"
const refOptionLabel = (option: MetaGlobalEnum) => {
  const domain = String(option.business_domain || '').trim()
  const name = String(option.enum_name || '').trim()
  return domain ? `${domain} / ${name}` : name
}
// 选择框回显标签（selected 插槽按当前 enum_ref 渲染，不依赖候选列表匹配）
const selectedRefLabel = computed(() => {
  const { domain, enumName } = splitEnumRef(columnDictEnumRef.value)
  return domain ? `${domain} / ${enumName}` : enumName
})

// enum_ref 格式与后端约定一致：通用域 "enum_name"，业务域 "business_domain:enum_name"
const buildEnumRef = (domain: string, enumName: string) => {
  const d = String(domain || '').trim()
  return d ? `${d}:${String(enumName || '').trim()}` : String(enumName || '').trim()
}

const splitEnumRef = (enumRef: string): { domain: string; enumName: string } => {
  const text = String(enumRef || '').trim()
  if (text.includes(':')) {
    const index = text.lastIndexOf(':')
    return { domain: text.slice(0, index).trim(), enumName: text.slice(index + 1).trim() }
  }
  return { domain: '', enumName: text }
}

const emptyColumnDict = (): DatasourceColumnDictPayload => ({
  enum_ref: '',
  enum_values: [],
  storage_format: '',
  unit: '',
})

const applyColumnDictForm = (dict?: Partial<DatasourceColumnDictPayload>) => {
  const payload = { ...emptyColumnDict(), ...(dict || {}) }
  columnDictEnumRef.value = String(payload.enum_ref || '').trim()
  columnDictRows.value = (payload.enum_values || []).map(item => ({
    value: String(item.value || '').trim(),
    label: String(item.label || '').trim(),
  }))
  columnDictStorageFormat.value = String(payload.storage_format || '').trim()
  columnDictUnit.value = String(payload.unit || '').trim()
}

const loadGlobalEnumOptions = async () => {
  if (enumOptionsLoading.value) return
  enumOptionsLoading.value = true
  try {
    const res = await getAdminMetaEnums()
    if (res.status === 'success') {
      globalEnumOptions.value = res.data || []
    }
  } catch (err) {
    console.error('获取全局枚举列表失败:', err)
  } finally {
    enumOptionsLoading.value = false
  }
}

// 迟到的旧枚举详情响应不得覆盖新选中枚举的内容（快速切换引用目标时）
let refDetailSeq = 0

const loadRefEnumDetail = async (enumRef: string) => {
  const seq = ++refDetailSeq
  refEnumDetail.value = null
  const { domain, enumName } = splitEnumRef(enumRef)
  if (!enumName) return
  refDetailLoading.value = true
  try {
    const res = await getAdminMetaEnum(enumName, domain || undefined)
    if (seq !== refDetailSeq) return
    if (res.status === 'success') {
      refEnumDetail.value = res.data || null
    } else {
      ElMessage.warning(res.msg || `全局枚举 ${enumRef} 读取失败，请确认其未被删除`)
    }
  } catch {
    if (seq === refDetailSeq) {
      ElMessage.warning(`全局枚举 ${enumRef} 读取失败，请确认其未被删除`)
    }
  } finally {
    if (seq === refDetailSeq) {
      refDetailLoading.value = false
    }
  }
}

let loadSeq = 0

watch(visible, async (value) => {
  if (!value) return
  const seq = ++loadSeq
  applyColumnDictForm()
  columnDictGenerateText.value = ''
  if (props.adminMode) void loadGlobalEnumOptions()
  loadingColumnDict.value = true
  try {
    const getColumnDict = props.adminMode ? getAdminDatasourceColumnDict : getDatasourceColumnDict
    const res = await getColumnDict(props.datasourceId, props.tableName, props.columnName)
    if (seq !== loadSeq) return
    if (res.status === 'success') {
      applyColumnDictForm(res.data?.dict)
      if (props.adminMode && columnDictEnumRef.value) void loadRefEnumDetail(columnDictEnumRef.value)
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
}, { immediate: true })

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

// 切换枚举来源（纯表单内存操作，不保存不落库）：引用模板时本地枚举行清空；切回本地时拷贝引用内容作为编辑起点
const onEnumSourceChange = async (value: string) => {
  const nextRef = String(value || '').trim()
  if (nextRef === columnDictEnumRef.value.trim()) return
  columnDictEnumRef.value = nextRef
  if (nextRef) {
    columnDictRows.value = []
    void loadRefEnumDetail(nextRef)
  } else {
    // 切回本地：把当前引用的枚举值拷贝成本地行，作为编辑起点（尚未保存，需手动保存）
    const items = refEnumDetail.value?.enum_items || []
    refEnumDetail.value = null
    columnDictRows.value = items.length > 0
      ? items.map((item: { value?: string; label?: string }) => ({ value: String(item.value || ''), label: String(item.label || '') }))
      : [{ value: '', label: '' }]
  }
}

// 存为模板：把当前本地枚举值登记为全局枚举，并切换为引用（保存后生效）。
// 业务域可选：留空归通用域（全公司共用），填写后与其他业务域同名枚举互相隔离。
const templateDialogVisible = ref(false)
const templateSaving = ref(false)
const templateDomain = ref('')
const templateName = ref('')
const templateDesc = ref('')

const ENUM_NAME_PATTERN = /^[A-Za-z0-9_]{1,128}$/

// 已有业务域候选（从全局枚举候选提取），供「存为模板」时选择或新建
const globalEnumDomains = computed(() => {
  const domains = new Set<string>()
  for (const option of globalEnumOptions.value) {
    const domain = String(option.business_domain || '').trim()
    if (domain) domains.add(domain)
  }
  return [...domains].sort()
})

const openSaveAsTemplate = () => {
  const normalized = normalizeColumnDictRows()
  if (normalized.length === 0) {
    ElMessage.warning('当前没有可存为模板的枚举值')
    return
  }
  templateDomain.value = ''
  templateName.value = ''
  templateDesc.value = `${props.tableName}.${props.columnName} 列字典沉淀`
  templateDialogVisible.value = true
}

const confirmSaveAsTemplate = async () => {
  const enumName = templateName.value.trim()
  if (!ENUM_NAME_PATTERN.test(enumName)) {
    ElMessage.warning('枚举名仅允许字母、数字、下划线，长度 1-128')
    return
  }
  templateSaving.value = true
  try {
    const res = await upsertAdminMetaEnum(enumName, {
      business_domain: templateDomain.value.trim(),
      enum_desc: templateDesc.value.trim(),
      enum_items: normalizeColumnDictRows(),
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '存为全局枚举失败')
      return
    }
    templateDialogVisible.value = false
    columnDictEnumRef.value = buildEnumRef(templateDomain.value.trim(), enumName)
    columnDictRows.value = []
    void loadGlobalEnumOptions()
    void loadRefEnumDetail(columnDictEnumRef.value)
    ElMessage.success('已创建全局枚举模板并切换为引用，记得点击保存')
  } catch {
    ElMessage.error('存为全局枚举失败，请稍后重试')
  } finally {
    templateSaving.value = false
  }
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
  const enumRef = columnDictEnumRef.value.trim()
  let payload: DatasourceColumnDictPayload
  if (enumRef) {
    // 引用态：本地枚举值必须为空（二选一，服务端二次校验）
    payload = {
      enum_ref: enumRef,
      enum_values: [],
      storage_format: columnDictStorageFormat.value.trim(),
      unit: columnDictUnit.value.trim(),
    }
  } else {
    const duplicateValues = columnDictRows.value
      .map(row => String(row.value || '').trim())
      .filter(Boolean)
      .filter((value, index, values) => values.indexOf(value) !== index)
    if (duplicateValues.length > 0) {
      ElMessage.warning('枚举值不能重复')
      return
    }
    payload = {
      enum_ref: '',
      enum_values: normalizeColumnDictRows(),
      storage_format: columnDictStorageFormat.value.trim(),
      unit: columnDictUnit.value.trim(),
    }
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
        <div v-if="props.adminMode" class="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
          <span class="shrink-0 text-xs font-medium text-slate-600">枚举来源</span>
          <el-select
            :model-value="columnDictEnumRef"
            size="small"
            :loading="enumOptionsLoading"
            :disabled="enumOptionsLoading"
            filterable
            :empty-values="[null, undefined]"
            class="!flex-1"
            @change="onEnumSourceChange(String($event ?? ''))"
          >
            <el-option label="本地枚举（本列自行维护）" value="" />
            <el-option
              v-for="option in globalEnumOptions"
              :key="buildEnumRef(option.business_domain, option.enum_name)"
              :value="buildEnumRef(option.business_domain, option.enum_name)"
            >
              <div class="flex items-center gap-1.5">
                <el-tag size="small" type="primary" class="shrink-0">引用模板</el-tag>
                <span class="min-w-0 truncate">{{ refOptionLabel(option) }}</span>
              </div>
            </el-option>
            <template #label="{ value }">
              <div v-if="isRefMode" class="flex min-w-0 items-center gap-1.5">
                <el-tag size="small" type="primary" class="shrink-0">引用模板</el-tag>
                <span class="min-w-0 truncate">{{ selectedRefLabel }}</span>
              </div>
              <span v-else>本地枚举（本列自行维护）</span>
            </template>
          </el-select>
        </div>

        <div v-if="props.adminMode && !isRefMode" class="rounded-xl border border-blue-100 bg-blue-50 p-3">
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

        <div v-if="isRefMode" class="rounded-xl border border-slate-200 bg-white">
          <div
            class="grid grid-cols-[1fr_1.5fr] gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500"
          >
            <span>枚举值</span>
            <span>枚举含义</span>
          </div>
          <div class="max-h-[320px] overflow-y-auto p-2">
            <div v-if="refDetailLoading" class="flex min-h-[80px] items-center justify-center text-xs text-slate-400">
              <i class="fa-solid fa-spinner fa-spin mr-2"></i>正在加载全局枚举...
            </div>
            <div
              v-else-if="!refEnumDetail || (refEnumDetail.enum_items || []).length === 0"
              class="flex min-h-[120px] items-center justify-center px-6 text-center text-sm text-slate-400"
            >
              {{ props.adminMode
                ? '全局枚举不存在或暂无枚举值'
                : `该列已引用全局枚举「${selectedRefLabel}」，枚举内容由管理员统一维护` }}
            </div>
            <div
              v-for="item in refEnumDetail?.enum_items || []"
              :key="item.value"
              class="grid grid-cols-[1fr_1.5fr] gap-2 rounded px-1 py-1.5"
            >
              <input
                :value="item.value"
                type="text"
                readonly
                class="min-w-0 rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs font-mono text-slate-500 outline-none"
              />
              <input
                :value="item.label || ''"
                type="text"
                readonly
                placeholder="-"
                class="min-w-0 rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs text-slate-500 outline-none"
              />
            </div>
          </div>
        </div>

        <div v-else class="rounded-xl border border-slate-200 bg-white">
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
            <div v-if="props.adminMode" class="mt-2 flex items-center gap-2">
              <button
                type="button"
                :disabled="loadingColumnDict"
                class="flex items-center gap-1.5 rounded bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50"
                @click="addColumnDictEnumRow"
              >
                <i class="fa-solid fa-plus text-[10px]"></i>
                <span>新增枚举</span>
              </button>
              <button
                type="button"
                :disabled="loadingColumnDict"
                class="flex items-center gap-1.5 rounded bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                @click="openSaveAsTemplate"
              >
                <i class="fa-solid fa-cloud-arrow-up text-[10px]"></i>
                <span>存为模板</span>
              </button>
            </div>
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

    <!-- 存为全局枚举模板弹窗：业务域 + 枚举名 + 说明（仅管理模式可触发） -->
    <el-dialog v-if="props.adminMode" v-model="templateDialogVisible" width="min(520px, 92vw)" :append-to-body="true" :close-on-click-modal="false">
      <template #header>
        <span class="text-base font-medium text-slate-800">存为全局枚举模板</span>
      </template>
      <div class="flex flex-col gap-3">
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="mb-1 text-xs text-slate-500">业务域</div>
            <el-select
              v-model="templateDomain"
              size="small"
              filterable
              allow-create
              default-first-option
              :empty-values="[null, undefined]"
              placeholder="请选择业务域，或输入新建"
              class="!w-full"
            >
              <el-option label="通用域（全公司共用）" value="" />
              <el-option v-for="domain in globalEnumDomains" :key="domain" :label="domain" :value="domain" />
            </el-select>
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">枚举名</div>
            <el-input v-model="templateName" size="small" maxlength="128" placeholder="仅字母、数字、下划线，如 pay_status" class="!w-full font-mono" />
          </div>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">说明</div>
          <el-input v-model="templateDesc" size="small" maxlength="512" placeholder="枚举业务含义说明（可选）" />
        </div>
        <div class="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
          将把当前 {{ columnDictRows.filter(row => String(row.value || '').trim()).length }} 个枚举值登记为全局枚举，
          登记后当前列切换为引用模板态（保存字典后生效）。
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="templateDialogVisible = false">取消</el-button>
        <el-button type="primary" size="small" :loading="templateSaving" @click="confirmSaveAsTemplate">创建并引用模板</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>
