<script setup lang="ts">
/**
 * 本体关系新建/编辑弹窗 + 状态迁移弹窗 + 删除确认（写操作单一入口，无列表 UI）。
 * 从对象树右键发起：关系分组节点→新建（可预选起始对象），关系条目节点→编辑/状态/删除。
 * 关系状态 = 两侧对象状态的较低者（降级自动级联、升级手动迁移）；
 * 连接对象类型/关联列/基数变更属破坏性，提交前需显式确认。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  changeAdminOntologyRelationTypeStatus,
  deleteAdminOntologyRelationType,
  getAdminOntologyObjectType,
  upsertAdminOntologyRelationType
} from '../../api'
import type { OntologyObjectTypeItem, OntologyObjectTypeStatus, OntologyPropertyItem, OntologyRelationTypeItem } from '../../types'
import {
  defaultRelationStatusTarget,
  ONTOLOGY_NAME_PATTERN,
  ONTOLOGY_STATUS_META,
  relationStatusTargets,
  relationStatusUpperBound,
  type ManualOntologyStatus
} from './ontologyMeta'

const props = defineProps({
  // 对象清单：端点候选 + 状态上限计算（父面板已加载，直接复用）
  object_types: { type: Array as () => OntologyObjectTypeItem[], default: () => [] }
})

const emit = defineEmits<{ saved: [objectTypeNames: string[]] }>()

// ---------- 对象属性加载（关联列候选） ----------

const propertyCache = ref<Map<string, OntologyPropertyItem[]>>(new Map())

const loadObjectProperties = async (objectTypeName: string) => {
  if (!objectTypeName || propertyCache.value.has(objectTypeName)) return
  try {
    const res = await getAdminOntologyObjectType(objectTypeName)
    if (res.status === 'success' && res.data) {
      propertyCache.value.set(objectTypeName, res.data.properties || [])
    }
  } catch (err) {
    console.error('获取对象属性失败:', err)
  }
}

const propertiesOf = (objectTypeName: string): OntologyPropertyItem[] => propertyCache.value.get(objectTypeName) || []

// 保存后作废并重拉端点属性缓存：属性集可能刚被对象编辑弹窗改动过，
// 缓存只增不减会让关联列候选长期陈旧（甚至引用已删除属性被后端拒绝）。
const refreshObjectProperties = async (objectTypeName: string) => {
  if (!objectTypeName) return
  propertyCache.value.delete(objectTypeName)
  await loadObjectProperties(objectTypeName)
}

// 目标侧关联列只能引用标识属性（FK→PK 红线，与后端校验一致）
const identifiersOf = (objectTypeName: string): string[] =>
  propertiesOf(objectTypeName)
    .filter(p => p.is_identifier)
    .map(p => p.property_name)


// ---------- 新建/编辑弹窗 ----------

const dialogVisible = ref(false)
const saving = ref(false)
const creating = ref(false)
const editingName = ref('')
// 编辑态缓存原始行：破坏性变更判定依据（连接对象类型/关联列/基数是否变化）
let editingRow: OntologyRelationTypeItem | null = null
const formName = ref('')
const formFromObject = ref('')
const formToObject = ref('')
const formMultiplicity = ref<'many_to_one' | 'one_to_one'>('many_to_one')
const formFromColumns = ref<string[]>([])
const formToColumns = ref<string[]>([])
const formFromDisplayName = ref('')
const formToDisplayName = ref('')
const formFromVerbalize = ref('')
const formToVerbalize = ref('')
const formDescription = ref('')

// 起始侧关联列候选：one_to_one 要求 from 侧列也引用标识属性（两侧都唯一）
const fromColumnCandidates = computed(() =>
  formMultiplicity.value === 'one_to_one'
    ? identifiersOf(formFromObject.value)
    : propertiesOf(formFromObject.value).map(p => p.property_name)
)

// 切到 one_to_one 时清掉非标识起始列并提示（属性未加载时跳过，避免误清空）
watch(formMultiplicity, value => {
  if (value !== 'one_to_one' || propertiesOf(formFromObject.value).length === 0) return
  const allowed = new Set(identifiersOf(formFromObject.value))
  const invalid = formFromColumns.value.filter(name => !allowed.has(name))
  if (invalid.length === 0) return
  formFromColumns.value = formFromColumns.value.filter(name => allowed.has(name))
  ElMessage.warning(`一对一关系类型的起始侧关联列必须是标识属性，已移除：${invalid.join('、')}`)
})

// 活跃对象才可作为关系端点（后端同样拒绝 deprecated 端点）
const activeObjectOptions = computed(() => props.object_types.filter(item => item.status !== 'deprecated'))

// 跨本体提示：两端对象挂在不同本体（或一侧未挂本体）时提醒确认
const crossOntologyHint = computed(() => {
  if (!formFromObject.value || !formToObject.value) return ''
  const fromObj = props.object_types.find(item => item.object_type_name === formFromObject.value)
  const toObj = props.object_types.find(item => item.object_type_name === formToObject.value)
  if (!fromObj || !toObj) return ''
  if (fromObj.ontology_id != null && fromObj.ontology_id === toObj.ontology_id) return ''
  return '两端对象类型属于不同本体（或未挂本体）：这会是跨本体导航路径，请确认是有意为之。'
})

const resetForm = () => {
  formName.value = ''
  formFromObject.value = ''
  formToObject.value = ''
  formMultiplicity.value = 'many_to_one'
  formFromColumns.value = []
  formToColumns.value = []
  formFromDisplayName.value = ''
  formToDisplayName.value = ''
  formFromVerbalize.value = ''
  formToVerbalize.value = ''
  formDescription.value = ''
}

const openCreate = (prefillFromObject = '') => {
  creating.value = true
  editingName.value = ''
  resetForm()
  if (prefillFromObject) {
    formFromObject.value = prefillFromObject
    void loadObjectProperties(prefillFromObject)
  }
  dialogVisible.value = true
}

const openEdit = (row: OntologyRelationTypeItem) => {
  creating.value = false
  editingName.value = row.relation_type_name
  editingRow = row
  resetForm()
  formName.value = row.relation_type_name
  formFromObject.value = row.from_object_type_name || ''
  formToObject.value = row.to_object_type_name || ''
  formMultiplicity.value = row.multiplicity
  formFromColumns.value = [...(row.from_columns || [])]
  formToColumns.value = [...(row.to_columns || [])]
  formFromDisplayName.value = row.from_display_name
  formToDisplayName.value = row.to_display_name
  formFromVerbalize.value = row.from_verbalize
  formToVerbalize.value = row.to_verbalize
  formDescription.value = row.description
  // 预取两端对象属性供关联列回显
  void loadObjectProperties(formFromObject.value)
  void loadObjectProperties(formToObject.value)
  dialogVisible.value = true
}

// 端点对象变化：清空关联列选择并预取属性
const onFromObjectChange = (objectTypeName: string) => {
  formFromColumns.value = []
  if (objectTypeName) void loadObjectProperties(objectTypeName)
}

const onToObjectChange = (objectTypeName: string) => {
  formToColumns.value = []
  if (objectTypeName) void loadObjectProperties(objectTypeName)
}

// 破坏性变更：连接对象类型/关联列/基数变化（编辑态）
const isStructuralChanged = (row: OntologyRelationTypeItem) =>
  row.from_object_type_name !== formFromObject.value ||
  row.to_object_type_name !== formToObject.value ||
  JSON.stringify([...(row.from_columns || [])].sort()) !== JSON.stringify([...formFromColumns.value].sort()) ||
  JSON.stringify([...(row.to_columns || [])].sort()) !== JSON.stringify([...formToColumns.value].sort()) ||
  row.multiplicity !== formMultiplicity.value

const saveRelation = async () => {
  const name = formName.value.trim()
  if (!name) {
    ElMessage.warning('请填写关系类型 API 名')
    return
  }
  if (!ONTOLOGY_NAME_PATTERN.test(name)) {
    ElMessage.warning('关系类型 API 名仅允许字母、数字、下划线，长度 1-128')
    return
  }
  if (!formFromObject.value || !formToObject.value) {
    ElMessage.warning('请选择起始对象类型与目标对象类型')
    return
  }
  if (formFromObject.value === formToObject.value) {
    ElMessage.warning('起始对象类型与目标对象类型不能相同（自关联请用 junction 对象类型表达）')
    return
  }
  if (formFromColumns.value.length === 0 || formToColumns.value.length === 0) {
    ElMessage.warning('请选择两侧关联列（等长一一对应，支持多条件关联）')
    return
  }
  if (formFromColumns.value.length !== formToColumns.value.length) {
    ElMessage.warning('两侧关联列必须等长且一一对应')
    return
  }
  if (formMultiplicity.value === 'one_to_one') {
    const allowed = new Set(identifiersOf(formFromObject.value))
    const invalid = formFromColumns.value.filter(name => !allowed.has(name))
    if (invalid.length > 0) {
      ElMessage.warning(`一对一关系类型的起始侧关联列必须引用起始对象类型的标识属性：${invalid.join('、')}`)
      return
    }
  }
  let confirmDestructive = false
  if (!creating.value && editingRow && isStructuralChanged(editingRow)) {
    try {
      await ElMessageBox.confirm(
        '关系类型的连接对象类型、关联列或基数变更属于破坏性变更，可能影响已发布的本体导航路径，确定继续？',
        '破坏性变更确认',
        { type: 'warning', confirmButtonText: '继续提交', cancelButtonText: '取消' }
      )
      confirmDestructive = true
    } catch {
      return
    }
  }
  saving.value = true
  try {
    const res = await upsertAdminOntologyRelationType(name, {
      from_object_type_name: formFromObject.value,
      to_object_type_name: formToObject.value,
      multiplicity: formMultiplicity.value,
      from_columns: formFromColumns.value,
      to_columns: formToColumns.value,
      from_display_name: formFromDisplayName.value.trim(),
      to_display_name: formToDisplayName.value.trim(),
      from_verbalize: formFromVerbalize.value.trim(),
      to_verbalize: formToVerbalize.value.trim(),
      description: formDescription.value.trim(),
      allow_overwrite: !creating.value,
      confirm_destructive: confirmDestructive
    })
    if (res.status === 'success') {
      ElMessage.success('保存成功')
      dialogVisible.value = false
      // 端点属性集可能在本次编辑期间被改过：作废缓存并重拉，避免关联列候选陈旧
      void refreshObjectProperties(formFromObject.value)
      void refreshObjectProperties(formToObject.value)
      emit('saved', [formFromObject.value, formToObject.value])
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如目标侧必须引用标识属性），不再重复提示
    console.error('保存本体关系失败:', err)
  } finally {
    saving.value = false
  }
}

// ---------- 删除（右键发起，确认框兜底） ----------

const remove = async (row: OntologyRelationTypeItem) => {
  try {
    await ElMessageBox.confirm(`确定删除关系类型「${row.relation_type_name}」？（软删）`, '删除关系类型', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntologyRelationType(row.relation_type_name)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      emit('saved', [row.from_object_type_name || '', row.to_object_type_name || ''])
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('删除本体关系失败:', err)
  }
}

// ---------- 状态迁移（升级手动，上限 = 两侧对象状态的较低者） ----------

const statusDialogVisible = ref(false)
const statusSaving = ref(false)
const statusRow = ref<OntologyRelationTypeItem | null>(null)
const statusTarget = ref<ManualOntologyStatus>('experimental')

const objectStatusOf = (objectTypeName: string | undefined): OntologyObjectTypeStatus | undefined =>
  props.object_types.find(item => item.object_type_name === objectTypeName)?.status

// 上限 = 两侧对象状态的较低者；任一端废弃时关系只随对象级联，不可手动迁移。
// 对象清单由父面板传全量 allObjects（不受工具栏筛选影响），否则端点被筛掉会退化成 draft 上限、无法升级。
const statusUpperBound = computed(() =>
  relationStatusUpperBound(
    objectStatusOf(statusRow.value?.from_object_type_name),
    objectStatusOf(statusRow.value?.to_object_type_name)
  )
)

const statusOptions = computed(() => relationStatusTargets(statusRow.value?.status, statusUpperBound.value))

const boundHint = computed(() => {
  const bound = statusUpperBound.value
  if (!statusRow.value) return ''
  if (bound === 'deprecated') return '连接对象类型已废弃：关系类型状态随对象类型级联为废弃，不可手动迁移。'
  if (!bound) return '无法确定两侧对象类型状态，请刷新后重试。'
  return `关系类型状态不得高于两侧对象类型的较低者（当前上限：${ONTOLOGY_STATUS_META[bound].label}）；对象类型升级后可再升级关系类型。`
})

const openStatus = (row: OntologyRelationTypeItem) => {
  statusRow.value = row
  // 默认选中第一个合法目标（合法目标已排除当前状态）：避免默认值与当前状态相同导致
  // 「确认迁移」变成无操作提交却仍提示“状态已更新”。
  statusTarget.value = defaultRelationStatusTarget(row.status, statusUpperBound.value) ?? 'experimental'
  statusDialogVisible.value = true
}

const confirmStatus = async () => {
  if (!statusRow.value) return
  if (statusTarget.value === statusRow.value.status) {
    ElMessage.warning('目标状态不能与当前状态相同，请选择其他目标状态')
    return
  }
  if (!statusOptions.value.includes(statusTarget.value)) {
    ElMessage.warning('目标状态超出两侧对象类型状态上限或不可迁移，请刷新后重试')
    return
  }
  statusSaving.value = true
  try {
    const res = await changeAdminOntologyRelationTypeStatus(statusRow.value.relation_type_name, { status: statusTarget.value })
    if (res.status === 'success') {
      ElMessage.success(`状态已更新为 ${ONTOLOGY_STATUS_META[statusTarget.value].label}`)
      statusDialogVisible.value = false
      emit('saved', [statusRow.value.from_object_type_name || '', statusRow.value.to_object_type_name || ''])
    } else {
      ElMessage.error(res.msg || '状态变更失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如超过对象状态上限），不再重复提示
    console.error('本体关系状态变更失败:', err)
  } finally {
    statusSaving.value = false
  }
}

defineExpose({ openCreate, openEdit, openStatus, remove })
</script>

<template>
  <!-- 新建/编辑弹窗 -->
  <el-dialog v-model="dialogVisible" width="min(680px, 94vw)" :append-to-body="true" :close-on-click-modal="false">
    <template #header>
      <span class="text-base font-medium text-slate-800">
        {{ creating ? '新建关系类型' : `编辑关系类型：${editingName}` }}
      </span>
    </template>
    <div class="flex max-h-[66vh] flex-col gap-3 overflow-y-auto pr-1">
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">关系类型 API 名（创建后不可改）</div>
          <el-input v-model="formName" size="small" :disabled="!creating" placeholder="关系类型 API 名" class="font-mono" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">基数（多对多请用 junction 对象类型表达）</div>
          <el-radio-group v-model="formMultiplicity" size="small">
            <el-radio-button value="many_to_one">多对一 (N:1)</el-radio-button>
            <el-radio-button value="one_to_one">一对一 (1:1)</el-radio-button>
          </el-radio-group>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">起始对象类型（from）</div>
          <el-select
            v-model="formFromObject"
            size="small"
            filterable
            :empty-values="[null, undefined]"
            placeholder="选择起始对象类型"
            class="!w-full"
            @change="onFromObjectChange"
          >
            <el-option
              v-for="item in activeObjectOptions"
              :key="item.object_type_name"
              :label="`${item.display_name} (${item.object_type_name})`"
              :value="item.object_type_name"
            />
          </el-select>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">目标对象类型（to）</div>
          <el-select
            v-model="formToObject"
            size="small"
            filterable
            :empty-values="[null, undefined]"
            placeholder="选择目标对象类型"
            class="!w-full"
            @change="onToObjectChange"
          >
            <el-option
              v-for="item in activeObjectOptions"
              :key="item.object_type_name"
              :label="`${item.display_name} (${item.object_type_name})`"
              :value="item.object_type_name"
            />
          </el-select>
        </div>
      </div>

      <div
        v-if="crossOntologyHint"
        class="rounded-lg border border-amber-100 bg-amber-50 p-2.5 text-[11px] leading-5 text-amber-600"
      >
        <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ crossOntologyHint }}
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">起始侧关联列（属性 API 名，外键侧）</div>
          <el-select
            v-model="formFromColumns"
            size="small"
            multiple
            filterable
            :empty-values="[null, undefined]"
            placeholder="选择起始侧属性"
            class="!w-full"
          >
            <el-option
              v-for="name in fromColumnCandidates"
              :key="name"
              :label="name"
              :value="name"
            />
          </el-select>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">目标侧关联列（必须是对方标识属性）</div>
          <el-select
            v-model="formToColumns"
            size="small"
            multiple
            filterable
            :empty-values="[null, undefined]"
            placeholder="选择目标侧标识属性"
            class="!w-full"
          >
            <el-option v-for="name in identifiersOf(formToObject)" :key="name" :label="name" :value="name" />
          </el-select>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">正向方向展示名</div>
          <el-input v-model="formFromDisplayName" size="small" maxlength="128" placeholder="如 归属于" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">反向方向展示名</div>
          <el-input v-model="formToDisplayName" size="small" maxlength="128" placeholder="如 拥有" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">正向自然语言模式串</div>
          <el-input v-model="formFromVerbalize" size="small" maxlength="512" placeholder='如 "{from} 归属于 {to}"' />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">反向自然语言模式串</div>
          <el-input v-model="formToVerbalize" size="small" maxlength="512" placeholder='如 "{to} 拥有 {from}"' />
        </div>
      </div>

      <div>
        <div class="mb-1 text-xs text-slate-500">描述</div>
        <el-input v-model="formDescription" size="small" maxlength="1024" placeholder="关系类型业务含义（可选）" />
      </div>
    </div>
    <template #footer>
      <el-button size="small" @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" size="small" :loading="saving" @click="saveRelation">保存</el-button>
    </template>
  </el-dialog>

  <!-- 状态迁移弹窗 -->
  <el-dialog v-model="statusDialogVisible" width="min(520px, 94vw)" :append-to-body="true">
    <template #header>
      <span class="text-base font-medium text-slate-800">状态迁移：{{ statusRow?.relation_type_name }}</span>
    </template>
    <div v-if="statusRow" class="flex flex-col gap-3">
      <div class="flex items-center gap-2 text-xs text-slate-500">
        <span>当前状态</span>
        <el-tag
          size="small"
          :type="ONTOLOGY_STATUS_META[statusRow.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'"
          effect="plain"
        >
          {{ ONTOLOGY_STATUS_META[statusRow.status as keyof typeof ONTOLOGY_STATUS_META]?.label || statusRow.status }}
        </el-tag>
        <span>{{ ONTOLOGY_STATUS_META[statusRow.status as keyof typeof ONTOLOGY_STATUS_META]?.hint }}</span>
      </div>
      <div
        v-if="statusRow.status !== 'deprecated'"
        class="rounded-lg border border-blue-100 bg-blue-50 p-2.5 text-[11px] leading-5 text-blue-700"
      >
        {{ boundHint }}
      </div>
      <div
        v-else
        class="rounded-lg border border-red-100 bg-red-50 p-2.5 text-[11px] leading-5 text-red-600"
      >
        deprecated 为终态且不可逆；关系类型状态随连接对象类型级联。
      </div>
      <div v-if="statusRow.status !== 'deprecated'">
        <div class="mb-1 text-xs text-slate-500">目标状态</div>
        <el-select v-model="statusTarget" size="small" :empty-values="[null, undefined]" class="!w-full">
          <el-option
            v-for="status in statusOptions"
            :key="status"
            :label="`${ONTOLOGY_STATUS_META[status].label}（${ONTOLOGY_STATUS_META[status].hint}）`"
            :value="status"
          />
        </el-select>
        <div v-if="statusOptions.length === 0" class="mt-1 text-[11px] text-slate-400">
          当前无可选目标状态（已达上限或与当前状态相同）。
        </div>
      </div>
    </div>
    <template #footer>
      <el-button size="small" @click="statusDialogVisible = false">取消</el-button>
      <el-button
        v-if="statusRow?.status !== 'deprecated' && statusOptions.length > 0"
        type="primary"
        size="small"
        :loading="statusSaving"
        @click="confirmStatus"
      >
        确认迁移
      </el-button>
    </template>
  </el-dialog>
</template>
