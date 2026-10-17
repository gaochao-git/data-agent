<script setup lang="ts">
/**
 * 本体关系新建/编辑弹窗 + 状态迁移弹窗 + 删除确认（写操作单一入口，无列表 UI）。
 * 从对象树右键发起：关系分组节点→新建（可预选起始对象），关系条目节点→编辑/状态/删除。
 * 关系状态 = 两侧对象状态的较低者（降级自动级联、升级手动迁移）；
 * 连接对象/关联列/基数变更属破坏性，提交前需显式确认。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  changeAdminOntologyRelationStatus,
  deleteAdminOntologyRelation,
  getAdminOntologyObject,
  upsertAdminOntologyRelation
} from '../../api'
import type { OntologyObjectItem, OntologyObjectStatus, OntologyPropertyItem, OntologyRelationItem } from '../../types'
import { ONTOLOGY_NAME_PATTERN, ONTOLOGY_STATUS_META } from './ontologyMeta'

const props = defineProps({
  // 对象清单：端点候选 + 状态上限计算（父面板已加载，直接复用）
  objects: { type: Array as () => OntologyObjectItem[], default: () => [] }
})

const emit = defineEmits<{ saved: [objectNames: string[]] }>()

// ---------- 对象属性加载（关联列候选） ----------

const propertyCache = ref<Map<string, OntologyPropertyItem[]>>(new Map())

const loadObjectProperties = async (objectName: string) => {
  if (!objectName || propertyCache.value.has(objectName)) return
  try {
    const res = await getAdminOntologyObject(objectName)
    if (res.status === 'success' && res.data) {
      propertyCache.value.set(objectName, res.data.properties || [])
    }
  } catch (err) {
    console.error('获取对象属性失败:', err)
  }
}

const propertiesOf = (objectName: string): OntologyPropertyItem[] => propertyCache.value.get(objectName) || []

// 目标侧关联列只能引用标识属性（FK→PK 红线，与后端校验一致）
const identifiersOf = (objectName: string): string[] =>
  propertiesOf(objectName)
    .filter(p => p.is_identifier)
    .map(p => p.property_name)

// ---------- 新建/编辑弹窗 ----------

const dialogVisible = ref(false)
const saving = ref(false)
const creating = ref(false)
const editingName = ref('')
// 编辑态缓存原始行：破坏性变更判定依据（连接对象/关联列/基数是否变化）
let editingRow: OntologyRelationItem | null = null
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

// 活跃对象才可作为关系端点（后端同样拒绝 deprecated 端点）
const activeObjectOptions = computed(() => props.objects.filter(item => item.status !== 'deprecated'))

// 跨领域提示：两端对象挂在不同领域（或一侧未挂领域）时提醒确认
const crossDomainHint = computed(() => {
  if (!formFromObject.value || !formToObject.value) return ''
  const fromObj = props.objects.find(item => item.object_name === formFromObject.value)
  const toObj = props.objects.find(item => item.object_name === formToObject.value)
  if (!fromObj || !toObj) return ''
  if (fromObj.domain_id != null && fromObj.domain_id === toObj.domain_id) return ''
  return '两端对象属于不同业务领域（或未挂领域）：这会是跨领域导航路径，请确认是有意为之。'
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

const openEdit = (row: OntologyRelationItem) => {
  creating.value = false
  editingName.value = row.relation_name
  editingRow = row
  resetForm()
  formName.value = row.relation_name
  formFromObject.value = row.from_object_name || ''
  formToObject.value = row.to_object_name || ''
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
const onFromObjectChange = (objectName: string) => {
  formFromColumns.value = []
  if (objectName) void loadObjectProperties(objectName)
}

const onToObjectChange = (objectName: string) => {
  formToColumns.value = []
  if (objectName) void loadObjectProperties(objectName)
}

// 破坏性变更：连接对象/关联列/基数变化（编辑态）
const isStructuralChanged = (row: OntologyRelationItem) =>
  row.from_object_name !== formFromObject.value ||
  row.to_object_name !== formToObject.value ||
  JSON.stringify([...(row.from_columns || [])].sort()) !== JSON.stringify([...formFromColumns.value].sort()) ||
  JSON.stringify([...(row.to_columns || [])].sort()) !== JSON.stringify([...formToColumns.value].sort()) ||
  row.multiplicity !== formMultiplicity.value

const saveRelation = async () => {
  const name = formName.value.trim()
  if (!name) {
    ElMessage.warning('请填写关系 API 名')
    return
  }
  if (!ONTOLOGY_NAME_PATTERN.test(name)) {
    ElMessage.warning('关系 API 名仅允许字母、数字、下划线，长度 1-128')
    return
  }
  if (!formFromObject.value || !formToObject.value) {
    ElMessage.warning('请选择起始对象与目标对象')
    return
  }
  if (formFromObject.value === formToObject.value) {
    ElMessage.warning('起始对象与目标对象不能相同（自关联请用 junction 对象表达）')
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
  let confirmDestructive = false
  if (!creating.value && editingRow && isStructuralChanged(editingRow)) {
    try {
      await ElMessageBox.confirm(
        '关系的连接对象、关联列或基数变更属于破坏性变更，可能影响已发布的本体导航路径，确定继续？',
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
    const res = await upsertAdminOntologyRelation(name, {
      from_object_name: formFromObject.value,
      to_object_name: formToObject.value,
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

const remove = async (row: OntologyRelationItem) => {
  try {
    await ElMessageBox.confirm(`确定删除关系「${row.relation_name}」？（软删）`, '删除本体关系', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntologyRelation(row.relation_name)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      emit('saved', [row.from_object_name || '', row.to_object_name || ''])
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('删除本体关系失败:', err)
  }
}

// ---------- 状态迁移（升级手动，上限 = 两侧对象状态的较低者） ----------

const MANUAL_STATUSES: Array<Exclude<OntologyObjectStatus, 'deprecated'>> = ['draft', 'experimental', 'certified']

const statusRankOf = (status: OntologyObjectStatus | undefined): number =>
  MANUAL_STATUSES.indexOf((status || 'draft') as Exclude<OntologyObjectStatus, 'deprecated'>)

const statusDialogVisible = ref(false)
const statusSaving = ref(false)
const statusRow = ref<OntologyRelationItem | null>(null)
const statusTarget = ref<Exclude<OntologyObjectStatus, 'deprecated'>>('experimental')

const objectStatusOf = (objectName: string | undefined): OntologyObjectStatus | undefined =>
  props.objects.find(item => item.object_name === objectName)?.status

// 上限 = 两侧对象状态的较低者；任一端废弃时关系只随对象级联，不可手动迁移
const statusUpperBound = computed<Exclude<OntologyObjectStatus, 'deprecated'> | 'deprecated' | null>(() => {
  if (!statusRow.value) return null
  const fromStatus = objectStatusOf(statusRow.value.from_object_name)
  const toStatus = objectStatusOf(statusRow.value.to_object_name)
  if (fromStatus === 'deprecated' || toStatus === 'deprecated') return 'deprecated'
  const bound = Math.min(statusRankOf(fromStatus), statusRankOf(toStatus))
  return MANUAL_STATUSES[bound] ?? null
})

const statusOptions = computed(() => {
  const bound = statusUpperBound.value
  if (!bound || bound === 'deprecated') return []
  const boundRank = statusRankOf(bound)
  return MANUAL_STATUSES.filter(status => status !== statusRow.value?.status && statusRankOf(status) <= boundRank)
})

const boundHint = computed(() => {
  const bound = statusUpperBound.value
  if (!statusRow.value) return ''
  if (bound === 'deprecated') return '连接对象已废弃：关系状态随对象级联为废弃，不可手动迁移。'
  if (!bound) return '无法确定两侧对象状态，请刷新后重试。'
  return `关系状态不得高于两侧对象的较低者（当前上限：${ONTOLOGY_STATUS_META[bound].label}）；对象升级后可再升级关系。`
})

const openStatus = (row: OntologyRelationItem) => {
  statusRow.value = row
  const current = row.status
  statusTarget.value = (MANUAL_STATUSES.includes(current as Exclude<OntologyObjectStatus, 'deprecated'>)
    ? current
    : 'draft') as Exclude<OntologyObjectStatus, 'deprecated'>
  statusDialogVisible.value = true
}

const confirmStatus = async () => {
  if (!statusRow.value) return
  statusSaving.value = true
  try {
    const res = await changeAdminOntologyRelationStatus(statusRow.value.relation_name, { status: statusTarget.value })
    if (res.status === 'success') {
      ElMessage.success(`状态已更新为 ${ONTOLOGY_STATUS_META[statusTarget.value].label}`)
      statusDialogVisible.value = false
      emit('saved', [statusRow.value.from_object_name || '', statusRow.value.to_object_name || ''])
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
        {{ creating ? '新建本体关系' : `编辑本体关系：${editingName}` }}
      </span>
    </template>
    <div class="flex max-h-[66vh] flex-col gap-3 overflow-y-auto pr-1">
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">关系 API 名（创建后不可改）</div>
          <el-input v-model="formName" size="small" :disabled="!creating" placeholder="如 order_belong_to_customer" class="font-mono" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">基数（多对多请用 junction 对象表达）</div>
          <el-radio-group v-model="formMultiplicity" size="small">
            <el-radio-button value="many_to_one">多对一 (N:1)</el-radio-button>
            <el-radio-button value="one_to_one">一对一 (1:1)</el-radio-button>
          </el-radio-group>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">起始对象（from）</div>
          <el-select
            v-model="formFromObject"
            size="small"
            filterable
            :empty-values="[null, undefined]"
            placeholder="选择起始对象"
            class="!w-full"
            @change="onFromObjectChange"
          >
            <el-option
              v-for="item in activeObjectOptions"
              :key="item.object_name"
              :label="`${item.display_name} (${item.object_name})`"
              :value="item.object_name"
            />
          </el-select>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">目标对象（to）</div>
          <el-select
            v-model="formToObject"
            size="small"
            filterable
            :empty-values="[null, undefined]"
            placeholder="选择目标对象"
            class="!w-full"
            @change="onToObjectChange"
          >
            <el-option
              v-for="item in activeObjectOptions"
              :key="item.object_name"
              :label="`${item.display_name} (${item.object_name})`"
              :value="item.object_name"
            />
          </el-select>
        </div>
      </div>

      <div
        v-if="crossDomainHint"
        class="rounded-lg border border-amber-100 bg-amber-50 p-2.5 text-[11px] leading-5 text-amber-600"
      >
        <i class="fa-solid fa-triangle-exclamation mr-1"></i>{{ crossDomainHint }}
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
              v-for="name in propertiesOf(formFromObject).map(p => p.property_name)"
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
        <el-input v-model="formDescription" size="small" maxlength="1024" placeholder="关系业务含义（可选）" />
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
      <span class="text-base font-medium text-slate-800">状态迁移：{{ statusRow?.relation_name }}</span>
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
        deprecated 为终态且不可逆；关系状态随连接对象级联。
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
