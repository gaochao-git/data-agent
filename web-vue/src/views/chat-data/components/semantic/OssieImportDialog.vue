<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  previewAdminOssieSemanticImport,
  importAdminOssieSemanticConfigs,
  getAdminSemanticConfigs,
  type OssieChecklistRow,
  type OssieImportModelPreview,
} from '../../api'
import type { SemanticConfigItem } from '../../types'
import OssieMergeDiff from './OssieMergeDiff.vue'

const props = defineProps<{
  datasourceId: string
}>()

const emit = defineEmits<{
  imported: [createdCount: number]
}>()

const visible = defineModel<boolean>('visible', { default: false })

interface LoadedYamlFile {
  /** 自增 id：文件可能重名，勾选状态与模型归属都按 id 关联 */
  id: number
  name: string
  content: string
}

/** 预览模型附带来源文件信息与稳定唯一键（muid），删除文件后状态不因索引位移而串位 */
type DialogModel = OssieImportModelPreview & {
  source_file?: string
  source_file_id?: number
  muid: string
}

let fileSeq = 0

const files = ref<LoadedYamlFile[]>([])
/** 文件勾选状态：Record<fileId, boolean>，缺省视为勾选；未勾选的文件不参与本轮校验与导入 */
const fileChecked = ref<Record<number, boolean>>({})
/** 文件详情展开状态：默认收起（文件表只显示检查结果摘要，点击「查看详情」展开） */
const detailOpen = ref<Record<number, boolean>>({})
const previewing = ref(false)
const importing = ref(false)
const models = ref<DialogModel[]>([])
/** 冲突模型的处理动作：模型 muid → replace/skip，默认替换（迭代闭环的常见路径） */
const conflictActions = ref<Record<string, 'replace' | 'skip'>>({})
const existingConfigsCache = ref<Record<string, SemanticConfigItem> | null>(null)
const diffOpen = ref<Record<string, boolean>>({})
/** 已解析出的现有配置：'missing' 表示配置列表里找不到该 config_id，无法对比 */
const diffExisting = ref<Record<string, SemanticConfigItem | 'missing'>>({})
const diffLoading = ref<Record<string, boolean>>({})

const resetState = () => {
  files.value = []
  fileChecked.value = {}
  detailOpen.value = {}
  previewing.value = false
  importing.value = false
  models.value = []
  conflictActions.value = {}
  existingConfigsCache.value = null
  diffOpen.value = {}
  diffExisting.value = {}
  diffLoading.value = {}
}

watch(visible, value => {
  if (!value) return
  resetState()
})

// 后端导入是「全有或全无」：任一模型存在阻断问题即整体拒绝，按钮须与该语义一致。
// 同名冲突不阻断（可替换/跳过）；未勾选文件的模型不参与判定。
const allImportable = computed(() => activeModels.value.length > 0 && activeModels.value.every(model => model.importable))
const confirmDisabled = computed(() => !allImportable.value || importing.value)
const importableCount = computed(() => activeModels.value.filter(model => model.importable).length)
const replaceCount = computed(() =>
  activeModels.value.filter(model => model.existing_config && conflictActions.value[model.muid] !== 'skip').length)
const skipCount = computed(() =>
  activeModels.value.filter(model => model.existing_config && conflictActions.value[model.muid] === 'skip').length)
const newCount = computed(() => activeModels.value.filter(model => model.importable && !model.existing_config).length)

const severityTextClass = (severity: string) =>
  severity === 'error' ? 'text-red-500' : severity === 'warning' ? 'text-amber-500' : 'text-gray-500'

const statusTextClass = (status: OssieChecklistRow['status']) =>
  status === 'pass'
    ? 'bg-green-100 text-green-600'
    : status === 'warn'
      ? 'bg-amber-100 text-amber-600'
      : status === 'fail'
        ? 'bg-red-100 text-red-600'
        : 'bg-gray-100 text-gray-400'

const statusLabel = (status: OssieChecklistRow['status']) =>
  status === 'pass' ? '通过' : status === 'warn' ? '警告' : status === 'fail' ? '未通过' : '跳过'

const groupLabels: Record<OssieChecklistRow['group'], string> = {
  schema: '① OSSI 校验',
  mapping: '② 结构映射',
  physical: '③ 物理校验',
  quality: '④ 质量规则',
}

/** 清单行按分组插入组头，供表格逐行渲染 */
const checklistRowsWithGroups = (checklist: OssieChecklistRow[] | undefined) => {
  const rows: Array<{ kind: 'group'; label: string } | { kind: 'row'; row: OssieChecklistRow }> = []
  let lastGroup = ''
  for (const row of checklist || []) {
    if (row.group !== lastGroup) {
      rows.push({ kind: 'group', label: groupLabels[row.group] || row.group })
      lastGroup = row.group
    }
    rows.push({ kind: 'row', row })
  }
  return rows
}

const modelSummary = (model: OssieImportModelPreview) => {
  const parts = [
    `${model.dimensions?.length || 0} 维度`,
    `${model.metrics?.length || 0} 指标`,
    `${model.relationships?.length || 0} 关系`,
  ]
  return parts.join(' · ')
}

const readYamlFile = (file: File) =>
  new Promise<LoadedYamlFile>((resolve, reject) => {
    const reader = new FileReader()
    const fileId = ++fileSeq
    reader.onload = () => resolve({ id: fileId, name: file.name, content: String(reader.result || '') })
    reader.onerror = () => reject(new Error(file.name))
    reader.readAsText(file)
  })

const handleFileChange = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files || [])
  // 允许重复选择同一批文件时重新触发 change
  input.value = ''
  if (!selected.length) return
  // 追加上传：新文件追加进列表，保留已选文件的勾选与校验状态
  const results = await Promise.allSettled(selected.map(readYamlFile))
  const loaded = results
    .filter((result): result is PromiseFulfilledResult<LoadedYamlFile> => result.status === 'fulfilled')
    .map(result => result.value)
  const failedCount = results.length - loaded.length
  if (failedCount) {
    ElMessage.error(`${failedCount} 个文件读取失败，已跳过`)
  }
  if (!loaded.length) return
  const checked = { ...fileChecked.value }
  loaded.forEach(file => {
    checked[file.id] = true
  })
  files.value = [...files.value, ...loaded]
  fileChecked.value = checked
}

const isFileChecked = (fileId: number) => fileChecked.value[fileId] !== false

const toggleFileAll = (event: Event) => {
  const target = (event.target as HTMLInputElement).checked
  const checked: Record<number, boolean> = {}
  files.value.forEach(file => {
    checked[file.id] = target
  })
  fileChecked.value = checked
}

/** 当前勾选的文件：未勾选的文件不参与本轮校验与导入，其模型也不计入确认按钮判定 */
const activeFiles = computed(() => files.value.filter(file => isFileChecked(file.id)))

const activeModels = computed(() => {
  const activeIds = new Set(activeFiles.value.map(file => file.id))
  return models.value.filter(model => activeIds.has(model.source_file_id ?? -1))
})

/** 文件级聚合检查状态：任一模型有阻断问题=错误，否则有警告项=警告，其余=通过 */
type FileCheckStatus = 'pass' | 'warn' | 'fail' | 'none'

const fileCheckStatus = (fileId: number): FileCheckStatus => {
  const fileModels = models.value.filter(model => model.source_file_id === fileId)
  if (!fileModels.length) return 'none'
  if (fileModels.some(model => !model.importable)) return 'fail'
  const hasWarn = fileModels.some(
    model => model.warnings?.length || (model.checklist || []).some(row => row.status === 'warn'),
  )
  return hasWarn ? 'warn' : 'pass'
}

const fileStatusChipClass = (status: FileCheckStatus) =>
  status === 'pass'
    ? 'bg-green-100 text-green-600'
    : status === 'warn'
      ? 'bg-amber-100 text-amber-600'
      : status === 'fail'
        ? 'bg-red-100 text-red-600'
        : 'bg-gray-100 text-gray-400'

const fileStatusLabel = (status: FileCheckStatus) =>
  status === 'pass' ? '通过' : status === 'warn' ? '警告' : status === 'fail' ? '错误' : '未校验'

const toggleFile = (fileId: number, event: Event) => {
  fileChecked.value = { ...fileChecked.value, [fileId]: (event.target as HTMLInputElement).checked }
}

/** 移除上传的文件：连同该文件的模型与冲突/diff 状态一起清理 */
const removeFile = (fileId: number) => {
  files.value = files.value.filter(file => file.id !== fileId)
  const checked = { ...fileChecked.value }
  delete checked[fileId]
  fileChecked.value = checked
  const detail = { ...detailOpen.value }
  delete detail[fileId]
  detailOpen.value = detail
  const removedMuids = new Set(
    models.value.filter(model => model.source_file_id === fileId).map(model => model.muid),
  )
  models.value = models.value.filter(model => model.source_file_id !== fileId)
  const dropKeys = <T extends Record<string, unknown>>(record: T): T => {
    const next = { ...record }
    removedMuids.forEach(muid => delete next[muid])
    return next
  }
  conflictActions.value = dropKeys(conflictActions.value)
  diffOpen.value = dropKeys(diffOpen.value)
  diffExisting.value = dropKeys(diffExisting.value)
  diffLoading.value = dropKeys(diffLoading.value)
}

/** 按文件分组的模型卡片：文件表默认只显示检查结果摘要，点击「查看详情」展开 */
const fileModelGroups = computed(() =>
  files.value
    .map(file => ({
      file,
      entries: models.value.filter(model => model.source_file_id === file.id),
    }))
    .filter(group => group.entries.length > 0),
)

/** 单文件时展示文件名（noUncheckedIndexedAccess 下 files[0] 需收窄） */
const singleFileName = computed(() => (files.value.length === 1 ? files.value[0]?.name : ''))

const toggleDetail = (fileId: number) => {
  detailOpen.value = { ...detailOpen.value, [fileId]: !detailOpen.value[fileId] }
}

const handlePreview = async () => {
  if (!files.value.length) {
    ElMessage.warning('请先选择 Ossie 语义模型 YAML 文件')
    return
  }
  if (!activeFiles.value.length) {
    ElMessage.warning('请至少勾选一个文件')
    return
  }
  previewing.value = true
  models.value = []
  conflictActions.value = {}
  existingConfigsCache.value = null
  diffOpen.value = {}
  diffExisting.value = {}
  try {
    const merged: DialogModel[] = []
    // 逐文件请求：单个文件解析失败不影响其他文件的校验结果
    for (const file of activeFiles.value) {
      try {
        const res = await previewAdminOssieSemanticImport(props.datasourceId, file.content)
        if (res.status !== 'success') {
          ElMessage.error(`${file.name}：${res.msg || '解析失败，请检查 YAML 内容'}`)
          continue
        }
        const fileModels = res.data?.models || []
        if (!fileModels.length) {
          ElMessage.warning(`${file.name}：没有可导入的语义模型（semantic_model 为空）`)
        }
        fileModels.forEach((model, modelIndex) =>
          merged.push({
            ...model,
            source_file: file.name,
            source_file_id: file.id,
            muid: `${file.id}-${modelIndex}`,
          }))
      } catch (err) {
        console.error('Ossie 导入预览失败:', err)
        ElMessage.error(`${file.name}：解析失败，请稍后重试`)
      }
    }
    models.value = merged
    detailOpen.value = {}
    // 详情默认收起：文件表只显示聚合检查结果，点击「查看详情」展开模型卡片
    // 冲突模型默认选「替换」：迭代闭环里重新导入优化版模型是常见路径
    merged.forEach(model => {
      if (model.existing_config) conflictActions.value[model.muid] = 'replace'
    })
  } finally {
    previewing.value = false
  }
}

const handleImport = async () => {
  if (!files.value.length || !models.value.length) return
  if (!activeFiles.value.length) {
    ElMessage.warning('请至少勾选一个文件')
    return
  }
  importing.value = true
  try {
    let created = 0
    let replaced = 0
    let skipped = 0
    // 按文件逐个请求：后端「全有或全无」以文件为粒度，失败时同步该文件的最新问题清单
    for (const file of activeFiles.value) {
      const fileModels = models.value.filter(model => model.source_file_id === file.id)
      if (!fileModels.length) continue
      const actions: Record<string, 'replace' | 'skip'> = {}
      fileModels.forEach(model => {
        if (model.existing_config) {
          actions[model.name] = conflictActions.value[model.muid] === 'skip' ? 'skip' : 'replace'
        }
      })
      const res = await importAdminOssieSemanticConfigs(props.datasourceId, file.content, actions)
      if (res.status !== 'success') {
        if (res.data?.models?.length) {
          // 失败时以文件为粒度刷新模型清单，沿用用户已选的冲突动作
          const previousActions: Record<string, 'replace' | 'skip'> = {}
          models.value
            .filter(model => model.source_file_id === file.id && model.existing_config)
            .forEach(model => {
              previousActions[model.name] = conflictActions.value[model.muid] ?? 'replace'
            })
          const refreshed = res.data.models.map((model, modelIndex) => ({
            ...model,
            source_file: file.name,
            source_file_id: file.id,
            muid: `${file.id}-${modelIndex}`,
          }))
          models.value = models.value
            .filter(model => model.source_file_id !== file.id)
            .concat(refreshed)
          refreshed.forEach(model => {
            if (model.existing_config) {
              conflictActions.value[model.muid] = previousActions[model.name] ?? 'replace'
            } else {
              delete conflictActions.value[model.muid]
            }
          })
        }
        ElMessage.error(`${file.name}：${res.msg || '导入失败，请根据问题清单修正后重试'}`)
        return
      }
      created += res.data?.created?.length || 0
      replaced += res.data?.replaced?.length || 0
      skipped += res.data?.skipped?.length || 0
    }
    if (created + replaced > 0) {
      const parts = [
        created ? `新建 ${created}` : '',
        replaced ? `替换 ${replaced}` : '',
        skipped ? `跳过 ${skipped}` : '',
      ].filter(Boolean)
      ElMessage.success(`导入完成（${parts.join(' · ')}），配置为默认禁用，请人工复核后启用`)
    } else if (skipped > 0) {
      ElMessage.info('全部模型因同名冲突被跳过，未做任何变更')
    }
    emit('imported', created + replaced)
    visible.value = false
  } catch (err) {
    console.error('Ossie 导入失败:', err)
    ElMessage.error('导入失败，请稍后重试')
  } finally {
    importing.value = false
  }
}

// ============ 同名冲突 diff（CodeMirror MergeView 双栏对比，见 OssieMergeDiff.vue） ============

const toggleDiff = async (muid: string) => {
  if (diffOpen.value[muid]) {
    diffOpen.value[muid] = false
    return
  }
  if (!diffExisting.value[muid]) {
    const model = models.value.find(item => item.muid === muid)
    if (!model?.existing_config) return
    if (!existingConfigsCache.value) {
      diffLoading.value[muid] = true
      try {
        const res = await getAdminSemanticConfigs(props.datasourceId)
        if (res.status !== 'success') {
          ElMessage.error(res.msg || '加载现有配置失败')
          return
        }
        const map: Record<string, SemanticConfigItem> = {}
        ;(res.data?.configs || []).forEach(config => {
          map[config.id] = config
        })
        existingConfigsCache.value = map
      } catch (err) {
        console.error('加载现有配置失败:', err)
        ElMessage.error('加载现有配置失败，请稍后重试')
        return
      } finally {
        diffLoading.value[muid] = false
      }
    }
    const existing = existingConfigsCache.value![model.existing_config.config_id]
    diffExisting.value[muid] = existing || 'missing'
  }
  diffOpen.value[muid] = true
}

/** diff 视图入参：已解析到现有配置时返回它，否则返回 undefined（模板渲染兜底文案） */
const diffExistingFor = (muid: string) => {
  const value = diffExisting.value[muid]
  return value && value !== 'missing' ? value : undefined
}
</script>

<template>
  <el-dialog
    v-model="visible"
    width="720px"
    title="导入 Ossie 语义模型"
    :close-on-click-modal="false"
  >
    <div class="space-y-3">
      <div class="text-xs leading-5 text-gray-500">
        上传 Ossie 语义模型 YAML 文件（可多选批量校验，由 AI 生成或从其他平台导出），模型校验通过后创建
        <span class="text-orange-500 font-medium">默认禁用</span>
        的草稿配置，需人工复核后启用。
      </div>

      <div class="flex items-center gap-2">
        <label class="h-8 shrink-0 flex items-center gap-1.5 border border-gray-200 rounded-md bg-white px-3 text-[12px] text-gray-600 cursor-pointer transition-colors hover:bg-gray-50">
          <i class="fa-solid fa-file-import"></i>
          {{ files.length ? '追加 YAML 文件' : '选择 YAML 文件' }}
          <input type="file" accept=".yaml,.yml,.txt" multiple class="hidden" @change="handleFileChange" />
        </label>
        <span v-if="files.length" class="min-w-0 flex-1 truncate text-[12px] text-gray-500">
          {{ singleFileName || `已选择 ${files.length} 个文件` }}
        </span>
        <span v-else class="min-w-0 flex-1 truncate text-[12px] text-gray-400">支持 .yaml / .yml，可由 AI 生成或从其他平台导出</span>
        <el-button size="small" class="shrink-0" :loading="previewing" :disabled="!files.length" @click="handlePreview">
          模型校验
        </el-button>
      </div>

      <table v-if="files.length" class="w-full table-fixed border-collapse text-[11px] leading-4">
        <thead>
          <tr class="bg-gray-50 text-[10px] text-gray-400">
            <td class="w-9 px-2 py-1 text-center">
              <input
                type="checkbox"
                title="全选 / 全不选"
                :checked="files.length > 0 && files.every(file => isFileChecked(file.id))"
                @change="toggleFileAll"
              />
            </td>
            <td class="px-2 py-1">文件</td>
            <td class="w-[80px] px-2 py-1">检查结果</td>
            <td class="w-[92px] px-2 py-1 text-center">操作</td>
          </tr>
        </thead>
        <tbody>
          <tr v-for="file in files" :key="file.id" class="border-t border-gray-100">
            <td class="px-2 py-1.5 text-center">
              <input type="checkbox" :checked="isFileChecked(file.id)" @change="toggleFile(file.id, $event)" />
            </td>
            <td class="px-2 py-1.5 text-gray-600">
              <span class="flex items-center gap-1.5" :title="file.name">
                <i class="fa-solid fa-file-lines shrink-0 text-gray-300"></i>
                <span class="truncate">{{ file.name }}</span>
              </span>
            </td>
            <td class="px-2 py-1.5">
              <span class="rounded px-1.5 py-0.5 text-[10px]" :class="fileStatusChipClass(fileCheckStatus(file.id))">
                {{ fileStatusLabel(fileCheckStatus(file.id)) }}
              </span>
            </td>
            <td class="px-2 py-1.5">
              <div class="flex items-center justify-center gap-2">
                <button
                  v-if="models.some(model => model.source_file_id === file.id)"
                  type="button"
                  class="text-gray-500 transition-colors hover:text-gray-700"
                  @click="toggleDetail(file.id)"
                >
                  {{ detailOpen[file.id] ? '收起' : '查看详情' }}
                </button>
                <button
                  type="button"
                  class="text-gray-300 transition-colors hover:text-red-500"
                  title="移除该文件"
                  @click="removeFile(file.id)"
                >
                  <i class="fa-solid fa-trash-can text-[11px]"></i>
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="models.length" class="space-y-2">
        <div class="flex items-center justify-between">
          <span class="text-[12px] font-medium text-gray-600">校验结果</span>
          <span class="text-[11px] text-gray-400">
            共 {{ models.length }} 个模型 · {{ importableCount }} 个可导入
          </span>
        </div>
        <div class="max-h-[420px] space-y-2 overflow-y-auto pr-1">
          <template v-for="group in fileModelGroups" :key="`fg-${group.file.id}`">
          <div v-show="detailOpen[group.file.id]" class="space-y-2">
            <div
              v-for="model in group.entries"
              :key="model.muid"
              class="rounded-md border px-3 py-2.5"
              :class="model.importable ? 'border-gray-200' : 'border-red-200 bg-red-50/40'"
            >
              <div class="flex items-center gap-2">
                <span class="text-[13px] font-medium text-gray-700 truncate">{{ model.name || '未命名模型' }}</span>
                <span
                  class="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                  :class="model.importable ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'"
                >
                  {{ model.importable ? '可导入' : '存在问题' }}
                </span>
              <span class="ml-auto shrink-0 text-[11px] text-gray-400">{{ modelSummary(model) }}</span>
            </div>
            <div v-if="(files.length > 1 && model.source_file) || model.source_config_id" class="mt-1 text-[11px] text-gray-400">
              <span v-if="files.length > 1 && model.source_file">来源文件：{{ model.source_file }}</span>
              <span v-if="files.length > 1 && model.source_file && model.source_config_id"> · </span>
              <span v-if="model.source_config_id">来源配置：{{ model.source_config_id }}（将创建新草稿，不更新原配置）</span>
            </div>
            <div
              v-if="model.existing_config"
              class="mt-1.5 rounded border border-amber-200 bg-amber-50/60 px-2 py-1.5"
            >
              <div class="flex items-center gap-3 text-[11px]">
                <span class="text-amber-600">
                  <i class="fa-solid fa-triangle-exclamation"></i>
                  已存在同名配置（{{ model.existing_config.enabled ? '启用中' : '禁用中' }}）
                </span>
                <label class="ml-auto flex cursor-pointer items-center gap-1 text-gray-600">
                  <input
                    type="radio"
                    :name="`conflict-${model.muid}`"
                    value="replace"
                    :checked="conflictActions[model.muid] !== 'skip'"
                    @change="conflictActions[model.muid] = 'replace'"
                  />
                  替换原配置
                </label>
                <label class="flex cursor-pointer items-center gap-1 text-gray-600">
                  <input
                    type="radio"
                    :name="`conflict-${model.muid}`"
                    value="skip"
                    :checked="conflictActions[model.muid] === 'skip'"
                    @change="conflictActions[model.muid] = 'skip'"
                  />
                  跳过该模型
                </label>
              </div>
              <div class="mt-1 flex items-center gap-1.5">
                <button
                  type="button"
                  class="text-[11px] text-gray-500 transition-colors hover:text-gray-700"
                  @click="toggleDiff(model.muid)"
                >
                  {{ diffOpen[model.muid] ? '收起差异' : '查看与现有配置的差异' }}
                </button>
                <i v-if="diffLoading[model.muid]" class="fa-solid fa-spinner fa-spin text-[10px] text-gray-400"></i>
              </div>
              <div
                v-if="diffOpen[model.muid] && diffExistingFor(model.muid)"
                class="mt-1"
              >
                <OssieMergeDiff
                  :existing="diffExistingFor(model.muid)!"
                  :imported="model"
                />
              </div>
              <div
                v-else-if="diffOpen[model.muid] && diffExisting[model.muid] === 'missing'"
                class="mt-1 text-[11px] text-gray-400"
              >
                未找到现有配置详情，无法对比
              </div>
            </div>
              <table v-if="model.checklist?.length" class="mt-2 w-full table-fixed border-collapse text-[11px] leading-4">
                <tbody>
                  <template v-for="(entry, i) in checklistRowsWithGroups(model.checklist)" :key="`cl-${model.muid}-${i}`">
                    <tr v-if="entry.kind === 'group'">
                      <td colspan="3" class="bg-gray-50 px-2 py-1 text-[10px] font-medium text-gray-400">
                        {{ entry.label }}
                      </td>
                    </tr>
                    <tr v-else class="border-t border-gray-100">
                      <td class="w-[38%] px-2 py-1.5 text-gray-600">{{ entry.row.name }}</td>
                      <td class="w-[64px] px-1 py-1.5">
                        <span class="rounded px-1.5 py-0.5 text-[10px]" :class="statusTextClass(entry.row.status)">
                          {{ statusLabel(entry.row.status) }}
                        </span>
                      </td>
                      <td class="px-2 py-1.5 text-gray-400">{{ entry.row.message }}</td>
                    </tr>
                  </template>
                </tbody>
              </table>
              <ul v-if="model.errors?.length" class="mt-1.5 space-y-1">
                <li v-for="(error, i) in model.errors" :key="`e-${i}`" class="text-[11px] leading-4 text-red-500">
                  {{ error }}
                </li>
              </ul>
              <ul v-if="model.warnings?.length" class="mt-1.5 space-y-1">
                <li v-for="(warning, i) in model.warnings" :key="`w-${i}`" class="text-[11px] leading-4 text-amber-500">
                  {{ warning }}
                </li>
              </ul>
              <div v-if="model.quality?.findings?.length" class="mt-1.5 rounded border border-gray-100 bg-gray-50/60 px-2 py-1.5">
                <div class="text-[11px] font-medium text-gray-400">触发的质量规则</div>
                <ul class="mt-1 space-y-0.5">
                  <li
                    v-for="finding in model.quality.findings"
                    :key="finding.code"
                    class="text-[11px] leading-4"
                    :class="severityTextClass(finding.severity)"
                  >
                    [{{ finding.code }}] {{ finding.message }}
                  </li>
                </ul>
              </div>
            </div>
          </div>
          </template>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center gap-2">
        <span v-if="models.length" class="text-[11px]" :class="allImportable ? 'text-green-600' : 'text-red-500'">
          <template v-if="allImportable">
            将新建 {{ newCount }}<template v-if="replaceCount"> · 替换 {{ replaceCount }}</template><template v-if="skipCount"> · 跳过 {{ skipCount }}</template>
          </template>
          <template v-else>存在阻断问题，修正后重新校验</template>
        </span>
        <div class="ml-auto flex gap-2">
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="importing" :disabled="confirmDisabled" @click="handleImport">
            确认导入
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>
