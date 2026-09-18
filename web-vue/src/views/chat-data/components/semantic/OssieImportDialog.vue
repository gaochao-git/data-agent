<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
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
/** 本轮校验已覆盖到的文件 id：区分「尚未校验」与「校验通过但文件内没有模型」，前者必须拦住导入 */
const previewedFileIds = ref<Record<number, boolean>>({})
/** 文件级校验失败原因：非空即该文件本轮校验请求失败，行内状态按错误展示并给出原因 */
const filePreviewError = ref<Record<number, string>>({})
const models = ref<DialogModel[]>([])
/**
 * 冲突模型的处理动作：模型 muid → replace/skip。
 * 09-18 产品口径收敛后，收起行不再有「替换 / 忽略」入口（是否导入只由左侧勾选决定，不想导入就删掉文件行），
 * 这里的取值因此恒为默认的 replace；保留结构是为了不动后端 replace/skip 契约 ——
 * 「他人私有配置」仍由后端强制 skip，前端不参与该选择。
 */
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
  previewedFileIds.value = {}
  filePreviewError.value = {}
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
// 同名冲突不阻断（可替换/忽略）；未勾选文件的模型不参与判定。
const allImportable = computed(() => activeModels.value.length > 0 && activeModels.value.every(model => model.importable))
/** 有导入请求在途：期间冻结一切会改动预览数据的操作，避免请求与状态互相踩踏 */
const busy = computed(() => importing.value)
/** 勾选但没拿到校验结果的文件数（含校验失败的）：这些文件会被导入循环跳过，必须拦在按钮上 */
const unvalidatedCount = computed(() =>
  activeFiles.value.filter(file => !previewedFileIds.value[file.id]).length)
const failedFileCount = computed(() =>
  activeFiles.value.filter(file => filePreviewError.value[file.id]).length)
const confirmDisabled = computed(() => !allImportable.value || busy.value || unvalidatedCount.value > 0)
const importableCount = computed(() => activeModels.value.filter(model => model.importable).length)
const replaceCount = computed(() =>
  activeModels.value.filter(model => model.existing_config && conflictActions.value[model.muid] !== 'skip').length)
// 同名但无操作权限的配置（他人私有）后端强制忽略，不参与 replace/skip 选择
const lockedCount = computed(() =>
  activeModels.value.filter(model => model.importable && model.conflict_locked).length)
const skipCount = computed(() =>
  lockedCount.value + activeModels.value.filter(model => model.existing_config && conflictActions.value[model.muid] === 'skip').length)
const newCount = computed(() =>
  activeModels.value.filter(model => model.importable && !model.existing_config && !model.conflict_locked).length)

type FooterTone = 'text-green-600' | 'text-amber-600' | 'text-red-500'

/**
 * 底部状态：门禁不通过时必须说清原因（按钮变灰又没有任何解释，是这套流程最容易卡住用户的地方）。
 * 返回 null 表示门禁通过，此时底部改为展示「将新建 / 替换 / 忽略」计数。
 */
const footerHint = computed((): { text: string; tone: FooterTone } | null => {
  if (!files.value.length) return null
  if (!activeFiles.value.length) return { text: '请勾选要导入的文件', tone: 'text-amber-600' }
  // 校验失败的判定要排在「还没校验过」之前：请求失败时 models 同样为空，先判后者会把失败说成没点过
  if (failedFileCount.value) {
    return { text: `${failedFileCount.value} 个文件校验失败，请查看文件行提示`, tone: 'text-red-500' }
  }
  if (!models.value.length) return { text: '请先点「模型校验」查看校验结果', tone: 'text-amber-600' }
  if (unvalidatedCount.value) {
    return { text: `${unvalidatedCount.value} 个文件尚未校验，请点「模型校验」`, tone: 'text-amber-600' }
  }
  if (!allImportable.value) return { text: '存在阻断问题，修正后重新校验', tone: 'text-red-500' }
  return null
})

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
  // 校验请求失败：状态按错误展示（原因见行内状态徽标的 title），不再伪装成「未校验」
  if (filePreviewError.value[fileId]) return 'fail'
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

// ============ 收起行（文件表）里的同名冲突处置 ============
// 产品口径（09-18 收敛）：收起行只做两件事 —— 左侧勾选决定是否导入（与「模型校验」同一口径，
// 勾选即参与校验与导入），同名冲突时给出唯一的「查看差异」入口。
// 不再有「替换 / 忽略 / 单文件导入」三个控件：不想导入直接删掉文件行即可，
// 冲突模型按文件统一按「替换」提交（覆盖前有二次确认兜底）。

/** 归属于某个文件的预览模型 */
const modelsOfFile = (fileId: number) => models.value.filter(model => model.source_file_id === fileId)

/**
 * 可查看差异的同名冲突模型。
 * - 他人私有（conflict_locked）不在其中：后端强制忽略，不提供差异对比；
 * - 已被阻断（!importable）的冲突模型也不在其中：该文件本来就导不了，行内只靠状态徽标的
 *   title 与底部提示承载，等修好重新校验后再出现差异入口。
 */
const fileConflictModels = (fileId: number) =>
  modelsOfFile(fileId).filter(model => model.importable && model.existing_config)

const fileHasConflict = (fileId: number) => fileConflictModels(fileId).length > 0

/** 行内冲突按钮的 title：单模型给启用态，多模型给数量（元信息不再重复出现在展开区清单里） */
const fileConflictTitle = (fileId: number) => {
  const list = fileConflictModels(fileId)
  const first = list[0]
  const base = list.length === 1 && first?.existing_config
    ? `已存在同名配置（${first.existing_config.enabled ? '启用中' : '禁用中'}）`
    : list.length ? `${list.length} 个模型与现有配置同名` : ''
  // 冲突默认按「替换」提交：把后果写在按钮上，避免只看到一个「查看差异」而误以为是新建
  return base ? `${base}，导入将覆盖该配置` : ''
}

/**
 * 模型行的导入动作徽章（替换/忽略/新增）。「同名」不进校验清单——覆盖已有模型再导入
 * 是正常工作流，后果在名称行直接展示；locked（无操作权限）由行内琥珀长文案单独表达，
 * 不再重复一个徽章；不可导入的模型没有动作可展示。
 */
const modelImportAction = (model: DialogModel): 'replace' | 'skip' | 'new' | null => {
  if (!model.importable || model.conflict_locked) return null
  if (model.existing_config) {
    return conflictActions.value[model.muid] === 'skip' ? 'skip' : 'replace'
  }
  return 'new'
}

/** 文件行「模式」列的徽章样式与文案 */
const IMPORT_MODE_LABELS: Record<'replace' | 'new' | 'skip', string> = {
  replace: '替换',
  new: '新增',
  skip: '忽略',
}

/** 模式徽章的悬浮说明：覆盖后果写在 title 里（原底部「（覆盖现有配置）」文案合并于此） */
const IMPORT_MODE_TITLES: Record<'replace' | 'new' | 'skip', string> = {
  replace: '替换会覆盖同名现有配置的内容并强制禁用（保留配置 ID），提交前会再确认一次',
  new: '不存在同名配置，导入后新建',
  skip: '同名配置无法操作，导入时将忽略该部分模型',
}

/**
 * 文件行「模式」列：该文件内可导入模型的导入动作汇总。
 * 单一动作直接给词（绝大多数文件单模型），混合动作带计数（如「替换 2 · 新增 1」）；
 * 未校验或没有可导入模型时为空（行内显示「—」）。同名不进校验清单——覆盖已有
 * 模型再导入是正常工作流，模式直接标在文件行让用户一眼看到后果。
 */
const fileImportModeChips = (fileId: number): Array<{ label: string; title: string }> => {
  const counts: Record<'replace' | 'new' | 'skip', number> = { replace: 0, new: 0, skip: 0 }
  modelsOfFile(fileId).forEach(model => {
    const action = modelImportAction(model)
    if (action) counts[action] += 1
  })
  const entries = (Object.entries(counts) as Array<['replace' | 'new' | 'skip', number]>)
    .filter(([, count]) => count > 0)
  const total = entries.reduce((sum, [, count]) => sum + count, 0)
  if (!total) return []
  return entries.map(([action, count]) => ({
    label: entries.length === 1 ? IMPORT_MODE_LABELS[action] : `${IMPORT_MODE_LABELS[action]} ${count}`,
    title: IMPORT_MODE_TITLES[action],
  }))
}

/** 无操作权限的同名冲突数量（不提供差异对比，导入时后端强制忽略） */
const fileLockedCount = (fileId: number) =>
  modelsOfFile(fileId).filter(model => model.importable && model.conflict_locked).length

/**
 * 「操作」列文字按钮（`查看差异` / `查看详情`）的态样式。
 * 静息时两者同款灰色文字按钮（看不出主次，这正是产品要的）；**展开后转为蓝色高亮**，
 * 让用户一眼看出当前打开的是差异还是详情 —— 两者互斥，同一行最多只有一个蓝的。
 * 颜色取项目的语义蓝（与差异区块的 `border-blue-100` / `bg-blue-50/40` 同色系）。
 * 冻结（busy）期间统一灰化，与「导入在途全弹窗冻结」保持一致。
 */
const rowActionClass = (active: boolean, frozen = false) => {
  if (frozen) return 'cursor-not-allowed text-gray-300'
  return active ? 'text-blue-600 hover:text-blue-700' : 'text-gray-500 hover:text-gray-700'
}

/** 收起某个文件内全部冲突模型的差异面板（差异与详情互斥时复用） */
const closeFileDiff = (fileId: number) => {
  fileConflictModels(fileId).forEach(model => {
    diffOpen.value[model.muid] = false
  })
}

const toggleFile = (fileId: number, event: Event) => {
  fileChecked.value = { ...fileChecked.value, [fileId]: (event.target as HTMLInputElement).checked }
}

/** 移除上传的文件：连同该文件的模型与冲突/diff 状态一起清理 */
const removeFile = (fileId: number) => {
  files.value = files.value.filter(file => file.id !== fileId)
  const checked = { ...fileChecked.value }
  delete checked[fileId]
  fileChecked.value = checked
  const previewed = { ...previewedFileIds.value }
  delete previewed[fileId]
  previewedFileIds.value = previewed
  const previewError = { ...filePreviewError.value }
  delete previewError[fileId]
  filePreviewError.value = previewError
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
  const next = !detailOpen.value[fileId]
  detailOpen.value = { ...detailOpen.value, [fileId]: next }
  // 差异与详情互斥：同一时刻只显示一个（内容与用途都不同），展开详情即收起该文件的差异面板
  if (next) closeFileDiff(fileId)
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
  // 校验结果按「当前勾选的文件」整体重建：未勾选文件的旧结果随之作废，
  // 因此这里同步重置「已校验」标记与失败原因，避免残留旧口径放行本次导入
  previewedFileIds.value = {}
  filePreviewError.value = {}
  try {
    const merged: DialogModel[] = []
    const previewed: Record<number, boolean> = {}
    const failed: Record<number, string> = {}
    // 逐文件请求：单个文件失败不影响其他文件；失败文件单独记账，导入时会被门禁拦住
    for (const file of activeFiles.value) {
      try {
        const res = await previewAdminOssieSemanticImport(props.datasourceId, file.content)
        if (res.status !== 'success') {
          const reason = res.msg || '解析失败，请检查 YAML 内容'
          failed[file.id] = reason
          ElMessage.error(`${file.name}：${reason}`)
          continue
        }
        previewed[file.id] = true
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
        const reason = '解析失败，请稍后重试'
        failed[file.id] = reason
        console.error('Ossie 导入预览失败:', err)
        ElMessage.error(`${file.name}：${reason}`)
      }
    }
    models.value = merged
    previewedFileIds.value = previewed
    filePreviewError.value = failed
    detailOpen.value = {}
    // 详情默认收起：文件表只显示聚合检查结果，点击「查看详情」展开模型卡片
    // 冲突模型默认选「替换」：迭代闭环里重新导入优化版模型是常见路径（覆盖前有二次确认）
    merged.forEach(model => {
      if (model.existing_config) conflictActions.value[model.muid] = 'replace'
    })
  } finally {
    previewing.value = false
  }
}

interface FileImportResult {
  created: number
  replaced: number
  skipped: number
}

/**
 * 以文件为粒度调用导入接口（后端「全有或全无」也以文件为粒度）。
 * 失败时同步该文件的最新问题清单并提示，返回 null；调用方需保证该文件已有预览模型。
 */
const importSingleFile = async (file: LoadedYamlFile): Promise<FileImportResult | null> => {
  const fileModels = modelsOfFile(file.id)
  if (!fileModels.length) return null
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
      fileModels
        .filter(model => model.existing_config)
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
    return null
  }
  return {
    created: res.data?.created?.length || 0,
    replaced: res.data?.replaced?.length || 0,
    skipped: res.data?.skipped?.length || 0,
  }
}

/** 导入结果提示：新建 / 替换 / 忽略三段（口径与底部汇总一致）；无新建无替换但有忽略时单独提示 */
const reportImportResult = ({ created, replaced, skipped }: FileImportResult) => {
  const parts = [
    created ? `新建 ${created}` : '',
    replaced ? `替换 ${replaced}` : '',
    skipped ? `忽略 ${skipped}` : '',
  ].filter(Boolean)
  if (created + replaced > 0) {
    ElMessage.success(`导入完成（${parts.join(' · ')}），配置为默认禁用，请人工复核后启用`)
  } else if (skipped > 0) {
    ElMessage.info('全部模型因同名冲突被忽略，未做任何变更')
  }
}

const handleImport = async () => {
  const targets = activeFiles.value.filter(file => modelsOfFile(file.id).length > 0)
  if (!targets.length) return
  importing.value = true
  try {
    let created = 0
    let replaced = 0
    let skipped = 0
    let doneFiles = 0
    // 按文件逐个请求：后端「全有或全无」以文件为粒度，任一文件失败即中止（该文件的问题清单已同步）。
    // 成功的文件立即从列表移除，中止后重试不会把它们再导一遍（与原「全部留在列表里」的差异所在）。
    for (const file of targets) {
      const result = await importSingleFile(file)
      if (!result) {
        if (doneFiles) {
          emit('imported', created + replaced)
          reportImportResult({ created, replaced, skipped })
          ElMessage.warning(`已导入 ${doneFiles} 个文件，剩余文件未导入`)
        }
        return
      }
      created += result.created
      replaced += result.replaced
      skipped += result.skipped
      doneFiles += 1
      removeFile(file.id)
    }
    emit('imported', created + replaced)
    reportImportResult({ created, replaced, skipped })
    // 批量路径已把整批处理完，这里只隐藏弹窗（不跳转页面、不重置其他面板状态）
    visible.value = false
  } catch (err) {
    console.error('Ossie 导入失败:', err)
    ElMessage.error('导入失败，请稍后重试')
  } finally {
    importing.value = false
  }
}

/**
 * 批量导入入口：命中「替换」时先二次确认。
 * 后端 replace 是「保留 config_id、全量覆盖内容并强制 enabled=False」（见导入接口），
 * 等于就地改掉一个可能正在被使用的配置，属不可逆动作，不能一次点击就落地；
 * 没有替换项时直接导入，不给迭代路径加多余摩擦。
 */
const confirmImport = () => {
  if (!replaceCount.value) {
    handleImport()
    return
  }
  ElMessageBox.confirm(
    `将覆盖 ${replaceCount.value} 个同名现有配置的内容，并强制为禁用状态（保留配置 ID）。确认继续导入？`,
    '确认替换现有配置',
    { type: 'warning', confirmButtonText: '继续导入', cancelButtonText: '取消' },
  ).then(() => handleImport()).catch(() => {})
}

// ============ 同名冲突 diff（CodeMirror MergeView 双栏对比，见 OssieMergeDiff.vue） ============
// 入口只有一个：文件行「操作」列里的「查看差异」按钮（与「查看详情」并列且互斥）。
// 差异面板**独立成块**渲染在文件表下方，不与「校验结果」详情区混在一起 ——
// 看差异不必展开详情，展开详情也会收起差异，两者各显示各的。

/** 该文件的冲突差异是否已全部展开（只取决于差异面板自身，与详情展开状态无关） */
const fileDiffOpen = (fileId: number) => {
  const list = fileConflictModels(fileId)
  return list.length > 0 && list.every(model => diffOpen.value[model.muid])
}

/** 当前已打开差异面板的模型：差异区按它渲染（按 models 原序，多文件多模型时顺序稳定） */
const openDiffModels = computed(() => models.value.filter(model => diffOpen.value[model.muid]))

const fileDiffLoading = (fileId: number) =>
  fileConflictModels(fileId).some(model => diffLoading.value[model.muid])

/** 加载差异对比所需的现有配置（已加载过直接复用）：只取数据，不改展开状态 */
const ensureDiffLoaded = async (muid: string): Promise<boolean> => {
  if (diffExisting.value[muid]) return true
  const model = models.value.find(item => item.muid === muid)
  if (!model?.existing_config) return false
  if (!existingConfigsCache.value) {
    diffLoading.value[muid] = true
    try {
      const res = await getAdminSemanticConfigs(props.datasourceId)
      if (res.status !== 'success') {
        ElMessage.error(res.msg || '加载现有配置失败')
        return false
      }
      const map: Record<string, SemanticConfigItem> = {}
      ;(res.data?.configs || []).forEach(config => {
        map[config.id] = config
      })
      existingConfigsCache.value = map
    } catch (err) {
      console.error('加载现有配置失败:', err)
      ElMessage.error('加载现有配置失败，请稍后重试')
      return false
    } finally {
      diffLoading.value[muid] = false
    }
  }
  const existing = existingConfigsCache.value![model.existing_config.config_id]
  diffExisting.value[muid] = existing || 'missing'
  return true
}

/**
 * 操作列「查看差异」：打开该文件内全部冲突模型的差异面板；已全部打开时收起。
 * 与同文件的「查看详情」互斥：打开差异即收起详情，两块内容不同时出现。
 * 逐个 await 而非并发：多模型文件都要读同一份现有配置列表，并发只会打重复请求。
 */
const toggleFileDiff = async (fileId: number) => {
  const list = fileConflictModels(fileId)
  if (!list.length) return
  if (fileDiffOpen(fileId)) {
    closeFileDiff(fileId)
    return
  }
  detailOpen.value = { ...detailOpen.value, [fileId]: false }
  for (const model of list) {
    if (await ensureDiffLoaded(model.muid)) {
      diffOpen.value = { ...diffOpen.value, [model.muid]: true }
    }
  }
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
    width="800px"
    title="导入 Ossie 语义模型"
    :close-on-click-modal="false"
  >
    <div class="space-y-3">
      <div class="text-xs leading-5 text-gray-500">
        上传 Ossie 语义模型 YAML 文件，模型校验通过后创建
        <span class="text-orange-500 font-medium">默认禁用</span>
        的草稿配置，需人工复核后启用。
      </div>

      <div class="flex items-center gap-2">
        <label
          class="h-8 shrink-0 flex items-center gap-1.5 border border-gray-200 rounded-md bg-white px-3 text-[12px] text-gray-600 transition-colors"
          :class="busy ? 'pointer-events-none opacity-50' : 'cursor-pointer hover:bg-gray-50'"
        >
          <i class="fa-solid fa-file-import"></i>
          {{ files.length ? '追加 YAML 文件' : '选择 YAML 文件' }}
          <input type="file" accept=".yaml,.yml" multiple class="hidden" @change="handleFileChange" />
        </label>
        <span v-if="files.length" class="min-w-0 flex-1 truncate text-[12px] text-gray-500">
          {{ singleFileName || `已选择 ${files.length} 个文件` }}
        </span>
        <span v-else class="min-w-0 flex-1 truncate text-[12px] text-gray-400">支持 .yaml / .yml，可由 AI 生成或从其他平台导出</span>
        <!-- 导入在途时禁止重新校验：重建 models 会让正在跑的导入循环丢掉剩余文件 -->
        <el-button size="small" class="shrink-0" :loading="previewing" :disabled="!files.length || busy" @click="handlePreview">
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
                :disabled="busy"
                @change="toggleFileAll"
              />
            </td>
            <td class="px-2 py-1">文件</td>
            <td class="w-[80px] px-2 py-1">模式</td>
            <td class="w-[80px] px-2 py-1">检查结果</td>
            <td class="w-[168px] px-2 py-1 text-right">操作</td>
          </tr>
        </thead>
        <tbody>
          <tr v-for="file in files" :key="file.id" class="border-t border-gray-100">
            <td class="px-2 py-1.5 text-center">
              <input
                type="checkbox"
                :checked="isFileChecked(file.id)"
                :disabled="busy"
                @change="toggleFile(file.id, $event)"
              />
            </td>
            <td class="px-2 py-1.5 text-gray-600">
              <div class="flex items-center gap-2">
                <span class="flex min-w-0 flex-1 items-center gap-1.5" :title="file.name">
                  <i class="fa-solid fa-file-lines shrink-0 text-gray-300"></i>
                  <span class="truncate">{{ file.name }}</span>
                </span>
                <!-- 同名但无操作权限（他人私有）：不提供差异对比，导入时后端强制忽略。
                     这是纯信息提示（非动作），留在文件名一侧；动作按钮统一在「操作」列。 -->
                <span
                  v-if="fileLockedCount(file.id)"
                  class="shrink-0 rounded border border-amber-200 bg-amber-50/60 px-1.5 py-0.5 text-[10px] text-amber-600"
                  title="已存在同名配置但无操作权限，导入时将忽略该模型"
                >
                  <i class="fa-solid fa-triangle-exclamation"></i>
                  同名无权限 · 忽略
                </span>
              </div>
            </td>
            <td class="px-2 py-1.5">
              <!-- 模式列：该文件内模型的导入动作（替换/新增/忽略），未校验或无可导入模型时为「—」；中性文本样式，后果悬浮在 title -->
              <span v-if="fileImportModeChips(file.id).length" class="flex flex-wrap items-center gap-1.5">
                <span
                  v-for="chip in fileImportModeChips(file.id)"
                  :key="chip.label"
                  class="text-[11px] text-gray-500"
                  :title="chip.title"
                >{{ chip.label }}</span>
              </span>
              <span v-else class="text-[11px] text-gray-300">—</span>
            </td>
            <td class="px-2 py-1.5">
              <!-- 校验失败时把后端原因挂在徽标 title 上，行内不额外占位 -->
              <span
                class="rounded px-1.5 py-0.5 text-[10px]"
                :class="fileStatusChipClass(fileCheckStatus(file.id))"
                :title="filePreviewError[file.id]"
              >
                {{ fileStatusLabel(fileCheckStatus(file.id)) }}
              </span>
            </td>
            <td class="px-2 py-1.5">
              <div class="flex items-center justify-end gap-2">
                <!-- 顺序：`查看详情` 在前（通用动作，新模型行也有，靠左可让它在所有行位置固定）、
                     `查看差异` 在后（仅同名冲突行有）、删除最右。
                     详情与差异同列同风格（静息为灰色文字按钮），两者互斥 —— 同一时刻只显示一个
                     （打开差异即收起详情，反之亦然），各看各的内容；
                     展开的那个转蓝色高亮，标出当前打开的是哪一块。 -->
                <!-- 顺序：`查看详情` 在前（通用动作，新模型行也有，靠左可让它在所有行位置固定）、
                     `查看差异` 在后（仅同名冲突行有）、删除最右。
                     详情与差异同列同风格（静息为灰色文字按钮），两者互斥 —— 同一时刻只显示一个
                     （打开差异即收起详情，反之亦然），各看各的内容；
                     展开的那个转蓝色高亮，标出当前打开的是哪一块。 -->
                <button
                  v-if="models.some(model => model.source_file_id === file.id)"
                  type="button"
                  class="transition-colors"
                  :class="rowActionClass(detailOpen[file.id] === true)"
                  @click="toggleDetail(file.id)"
                >
                  {{ detailOpen[file.id] ? '收起详情' : '查看详情' }}
                </button>
                <button
                  v-if="fileHasConflict(file.id)"
                  type="button"
                  class="flex shrink-0 items-center gap-1 transition-colors"
                  :class="rowActionClass(fileDiffOpen(file.id), busy)"
                  :disabled="busy"
                  :title="fileConflictTitle(file.id)"
                  @click="toggleFileDiff(file.id)"
                >
                  {{ fileDiffOpen(file.id) ? '收起差异' : '查看差异' }}
                  <i v-if="fileDiffLoading(file.id)" class="fa-solid fa-spinner fa-spin text-[10px] text-gray-400"></i>
                </button>
                <button
                  type="button"
                  class="transition-colors"
                  :class="busy ? 'cursor-not-allowed text-gray-200' : 'text-gray-300 hover:text-red-500'"
                  :disabled="busy"
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

      <!-- 差异对比：**单独一块**，不混进下面的「校验结果」详情区（与同文件的详情互斥，
           同一时刻只显示一个）。置于文件表正下方；
           单个 diff 面板高 260px，多冲突模型会很长，因此这块内部自己滚动，不撑高弹窗。 -->
      <div v-if="openDiffModels.length" class="rounded-md border border-blue-100">
        <div class="flex items-center justify-between border-b border-blue-100 bg-blue-50/40 px-3 py-1.5">
          <span class="flex items-center gap-1.5 text-[12px] font-medium text-gray-600">
            <i class="fa-solid fa-code-compare text-blue-500"></i>
            与现有配置的差异
          </span>
          <span class="text-[11px] text-gray-400">共 {{ openDiffModels.length }} 个同名模型</span>
        </div>
        <div class="max-h-[320px] space-y-3 overflow-y-auto px-3 py-2">
          <div v-for="model in openDiffModels" :key="`diff-${model.muid}`">
            <div class="flex items-center gap-2 text-[11px] text-gray-500">
              <span class="font-medium text-gray-600">{{ model.name || '未命名模型' }}</span>
              <span v-if="files.length > 1 && model.source_file" class="text-gray-400">来源文件：{{ model.source_file }}</span>
            </div>
            <OssieMergeDiff
              v-if="diffExistingFor(model.muid)"
              :existing="diffExistingFor(model.muid)!"
              :imported="model"
            />
            <div v-else class="mt-1 text-[11px] text-gray-400">未找到现有配置详情，无法对比</div>
          </div>
        </div>
      </div>

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
              <!-- 名称行：模型标识 + 状态 + 规模摘要。处置与差异入口都在收起态文件行（见文件表），
                   导入动作（替换/新增/忽略）由文件表「模式」列统一展示，展开区只承载详情。 -->
              <div class="flex items-center gap-2">
                <span class="text-[13px] font-medium text-gray-700 truncate">{{ model.name || '未命名模型' }}</span>
                <span
                  class="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                  :class="model.importable ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'"
                >
                  {{ model.importable ? '可导入' : '存在问题' }}
                </span>
                <div class="ml-auto flex shrink-0 items-center gap-2">
                  <!-- 差异入口只在收起态文件行（见文件表）：展开区是详情容器，不再重复一个同功能按钮 -->
                  <span
                    v-if="model.conflict_locked"
                    class="rounded border border-amber-200 bg-amber-50/60 px-2 py-0.5 text-[11px] text-amber-600"
                  >
                    <i class="fa-solid fa-triangle-exclamation"></i>
                    已存在同名配置但无操作权限，导入时将忽略该模型
                  </span>
                  <span class="shrink-0 text-[11px] text-gray-400">{{ modelSummary(model) }}</span>
                </div>
              </div>
              <div v-if="files.length > 1 && model.source_file" class="mt-1 text-[11px] text-gray-400">
                来源文件：{{ model.source_file }}
              </div>
              <!-- 列宽只能由 colgroup 决定：本表首行是 `colspan=3` 的分组行，而 table-fixed 会用首行推列宽，
                   写在 td 上的宽度会被忽略并退化成三等分（说明列只剩 1/3 宽，被迫折成 2~3 行、白占行高）。 -->
              <table v-if="model.checklist?.length" class="mt-2 w-full table-fixed border-collapse text-[11px] leading-4">
                <colgroup>
                  <col class="w-[140px]" />
                  <col class="w-[56px]" />
                  <col />
                </colgroup>
                <tbody>
                  <template v-for="(entry, i) in checklistRowsWithGroups(model.checklist)" :key="`cl-${model.muid}-${i}`">
                    <tr v-if="entry.kind === 'group'">
                      <td colspan="3" class="bg-gray-50 px-2 py-1 text-[10px] font-medium text-gray-400">
                        {{ entry.label }}
                      </td>
                    </tr>
                    <tr v-else class="border-t border-gray-100">
                      <td class="px-2 py-1.5 text-gray-600">{{ entry.row.name }}</td>
                      <td class="px-1 py-1.5">
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
        <!-- 门禁不通过时先说清原因：按钮变灰却没有解释，是这条流程最容易卡住用户的地方 -->
        <span v-if="footerHint" class="text-[11px]" :class="footerHint.tone">{{ footerHint.text }}</span>
        <span v-else-if="models.length" class="text-[11px] text-green-600">
          将新建 {{ newCount }}<template v-if="replaceCount"> · 替换 {{ replaceCount }}</template><template v-if="skipCount"> · 忽略 {{ skipCount }}</template>
        </span>
        <div class="ml-auto flex gap-2">
          <el-button :disabled="busy" @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="importing" :disabled="confirmDisabled" @click="confirmImport">
            确认导入
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>
