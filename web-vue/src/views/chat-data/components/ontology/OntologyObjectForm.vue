<script setup lang="ts">
/**
 * 本体对象类型创建/编辑弹窗：元数据 + 属性集 + 映射一次性整体提交（对齐后端 upsert 语义）。
 * 门禁交互：certified 冻结主键/标题键并禁删属性；映射/主键/标题键变更属破坏性，
 * 提交前弹显式确认（confirm_destructive=true）；deprecated 对象禁止编辑。
 */
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAdminOntologyObjectType, upsertAdminOntologyObjectType } from '../../api'
import type { OntologyItem, OntologyMappingItem, OntologyObjectTypeItem, OntologyPropertyItem } from '../../types'
import OntologyMappingEditor from './OntologyMappingEditor.vue'
import OntologyPropertyEditor from './OntologyPropertyEditor.vue'
import {
  crossMappingPropertyConflicts,
  haveMappingsChanged,
  ONTOLOGY_NAME_PATTERN,
  removeMappingPropertyKey,
  renameMappingPropertyKey,
  staleMappingPropertyNames
} from './ontologyMeta'

const props = defineProps({
  // 编辑的对象名；空串表示新建
  objectTypeName: { type: String, default: '' },
  // 本体清单（父组件加载；对象类型挂本体=纯导航归属，不约束映射组）
  ontologies: { type: Array as PropType<OntologyItem[]>, default: () => [] },
  // 新建时的预选本体（树右键「新增对象」从本体节点发起）
  defaultOntologyId: { type: Number as PropType<number | null>, default: null }
})

const emit = defineEmits<{ saved: [] }>()

const visible = defineModel<boolean>('visible', { default: false })

const loading = ref(false)
const saving = ref(false)
const editing = computed(() => !!props.objectTypeName)

// 既有对象元信息（编辑态回显状态门禁用）
const loadedDetail = ref<OntologyObjectTypeItem | null>(null)

const formName = ref('')
const formDisplayName = ref('')
const formDescription = ref('')
const formOntologyId = ref<number | null>(null)
const propertyRows = ref<OntologyPropertyItem[]>([])
const mappingRows = ref<OntologyMappingItem[]>([])

// 行增删由持有数组的父组件完成（子编辑器通过事件回传，避免直接改 props）
const addPropertyRow = () => {
  propertyRows.value.push({
    property_name: '',
    display_name: '',
    description: '',
    value_type: 'String',
    enum_ref: '',
    render_hints: {},
    is_identifier: propertyRows.value.length === 0,
    is_title: propertyRows.value.length === 0
  })
}

const removePropertyRow = (index: number) => {
  propertyRows.value.splice(index, 1)
}

const addMappingRow = () => {
  mappingRows.value.push({ semantic_config_id: '', column_mappings: {} })
}

const removeMappingRow = (index: number) => {
  mappingRows.value.splice(index, 1)
}

// 本体下拉标签：展示名（API 名）；本体不绑组，映射候选不限组
const ontologyLabel = (ontology: OntologyItem) =>
  ontology.display_name ? `${ontology.display_name} (${ontology.ontology_name})` : ontology.ontology_name

// certified 冻结名单：既有属性禁删禁改名禁改主键/标题标记
const frozenNames = computed(() =>
  loadedDetail.value?.status === 'certified' ? (loadedDetail.value.properties || []).map(p => p.property_name) : []
)

const resetForm = () => {
  formName.value = ''
  formDisplayName.value = ''
  formDescription.value = ''
  formOntologyId.value = props.defaultOntologyId
  propertyRows.value = [{ property_name: '', display_name: '', description: '', value_type: 'String', enum_ref: '', render_hints: {}, is_identifier: true, is_title: true }]
  mappingRows.value = []
  loadedDetail.value = null
}

const openLoad = async () => {
  resetForm()
  if (!props.objectTypeName) return
  loading.value = true
  try {
    const res = await getAdminOntologyObjectType(props.objectTypeName)
    if (res.status !== 'success' || !res.data) {
      ElMessage.error(res.msg || '获取对象类型详情失败')
      visible.value = false
      return
    }
    const detail = res.data
    loadedDetail.value = detail
    formName.value = detail.object_type_name
    formDisplayName.value = detail.display_name
    formDescription.value = detail.description
    formOntologyId.value = detail.ontology_id ?? null
    propertyRows.value = (detail.properties || []).map(item => ({ ...item, render_hints: { ...(item.render_hints || {}) } }))
    mappingRows.value = (detail.mappings || []).map(item => ({ ...item, column_mappings: { ...(item.column_mappings || {}) } }))
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取本体对象类型详情失败:', err)
    visible.value = false
  } finally {
    loading.value = false
  }
}

watch(visible, value => {
  if (value) void openLoad()
})

// ---------- 破坏性变更检测 ----------

const destructiveActions = computed(() => {
  const detail = loadedDetail.value
  if (!detail) return []
  const actions: string[] = []
  const oldIds = (detail.properties || []).filter(p => p.is_identifier).map(p => p.property_name).sort()
  const newIds = propertyRows.value.filter(p => p.is_identifier).map(p => p.property_name).sort()
  const oldTitles = (detail.properties || []).filter(p => p.is_title).map(p => p.property_name).sort()
  const newTitles = propertyRows.value.filter(p => p.is_title).map(p => p.property_name).sort()
  if (JSON.stringify(oldIds) !== JSON.stringify(newIds) || JSON.stringify(oldTitles) !== JSON.stringify(newTitles)) {
    actions.push('主键组合或标题键变更')
  }
  // 详情行由服务端附带 object_type_id/binding_type 等附加键，必须与表单行按同一口径规范化后再比较，
  // 否则对象只要有映射就会误判为「映射变更」，每次编辑都弹破坏性确认。
  if (haveMappingsChanged(detail.mappings, mappingRows.value)) {
    actions.push('映射变更')
  }
  return actions
})

// 属性改名/删除时同步映射键（后端按当前属性名校验，键不同步会拒绝提交）
const onPropertyRenamed = (oldName: string, newName: string) => {
  renameMappingPropertyKey(mappingRows.value, oldName, newName)
}

const onPropertyRemoved = (propertyName: string) => {
  removeMappingPropertyKey(mappingRows.value, propertyName)
}

// ---------- 提交 ----------

const validateForm = (): string => {
  if (!formName.value.trim()) return '请填写对象类型名'
  if (!ONTOLOGY_NAME_PATTERN.test(formName.value.trim())) return '对象类型名仅允许字母、数字、下划线，长度 1-128'
  if (!formDisplayName.value.trim()) return '请填写展示名'
  const rows = propertyRows.value
  if (rows.length === 0) return '对象类型至少需要一个属性'
  const seen = new Set<string>()
  let identifierCount = 0
  let titleCount = 0
  for (const row of rows) {
    const name = row.property_name.trim()
    if (!name) return '属性名不能为空'
    if (!ONTOLOGY_NAME_PATTERN.test(name)) return `属性名 ${name} 仅允许字母、数字、下划线，长度 1-128`
    if (seen.has(name)) return `属性名重复: ${name}`
    seen.add(name)
    if (row.value_type === 'enum' && !row.enum_ref.trim()) return `属性 ${name} 引用枚举类型时必须选择全局枚举`
    if (row.value_type === 'Decimal' && row.is_identifier) return `属性 ${name} 为 Decimal，浮点/小数类型禁止作为标识属性（主键）`
    if (row.is_identifier) identifierCount += 1
    if (row.is_title) titleCount += 1
  }
  if (identifierCount < 1) return '对象类型至少需要一个标识属性（主键），禁止用自增行号当主键'
  if (titleCount > 1) return '对象类型至多一个标题属性'
  const staleMappingKeys = staleMappingPropertyNames(
    mappingRows.value,
    rows.map(row => row.property_name)
  )
  if (staleMappingKeys.length > 0) {
    return `映射引用了不存在的属性：${staleMappingKeys.join('、')}，请先在映射编辑器中修正`
  }
  const conflicts = crossMappingPropertyConflicts(
    mappingRows.value,
    rows.filter(row => row.is_identifier).map(row => row.property_name.trim())
  )
  if (conflicts.length > 0) {
    const details = conflicts
      .map(item => `${item.property_name}（${item.config_ids.join(' / ')}）`)
      .join('；')
    return `非标识属性只能单源映射：${details}；标识属性才可作为跨源主键，请为属性只保留一个来源模型`
  }
  return ''
}

const saveObject = async () => {
  const problem = validateForm()
  if (problem) {
    ElMessage.warning(problem)
    return
  }
  // 破坏性变更显式确认（对齐后端 confirm_destructive 门禁）
  let confirmDestructive = false
  if (editing.value && destructiveActions.value.length > 0) {
    try {
      await ElMessageBox.confirm(
        `以下变更属于破坏性变更：${destructiveActions.value.join('、')}。可能影响已发布的本体资产与下游消费，确定继续？`,
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
    const res = await upsertAdminOntologyObjectType(formName.value.trim(), {
      display_name: formDisplayName.value.trim(),
      description: formDescription.value.trim(),
      ontology_id: formOntologyId.value,
      properties: propertyRows.value.map(row => ({
        property_name: row.property_name.trim(),
        display_name: row.display_name.trim(),
        description: row.description.trim(),
        value_type: row.value_type,
        // 后端要求值类型非 enum 时不得携带 enum_ref，这里按值类型收敛，避免切换类型后残留旧引用被拒
        enum_ref: row.value_type === 'enum' ? row.enum_ref.trim() : '',
        render_hints: { ...row.render_hints },
        is_identifier: row.is_identifier,
        is_title: row.is_title
      })),
      mappings: mappingRows.value.map(row => ({
        semantic_config_id: row.semantic_config_id.trim(),
        column_mappings: Object.fromEntries(
          Object.entries(row.column_mappings || {})
            .map(([k, v]) => [k.trim(), (v || '').trim()])
            .filter(([k]) => k)
        )
      })),
      allow_overwrite: editing.value,
      confirm_destructive: confirmDestructive
    })
    if (res.status === 'success') {
      ElMessage.success('保存成功')
      visible.value = false
      emit('saved')
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如一源一对象类型冲突、枚举不存在），不再重复提示
    console.error('保存本体对象类型失败:', err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" width="min(1080px, 96vw)" :append-to-body="true" :close-on-click-modal="false">
    <template #header>
      <span class="text-base font-medium text-slate-800">
        {{ editing ? `编辑对象类型：${objectTypeName}` : '新建对象类型' }}
      </span>
      <span
        v-if="loadedDetail?.status === 'certified'"
        class="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-600"
      >
        已认证：主键/标题键冻结，禁止删除属性；如需结构性变更请先降级
      </span>
    </template>

    <div v-loading="loading" class="flex max-h-[72vh] flex-col gap-4 overflow-y-auto pr-1">
      <!-- 基本信息 -->
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">对象类型名（创建后不可改）</div>
          <el-input v-model="formName" size="small" :disabled="editing" placeholder="仅字母、数字、下划线" class="font-mono" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">展示名</div>
          <el-input v-model="formDisplayName" size="small" maxlength="128" placeholder="用户可见名称，如 订单" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">所属本体（纯导航归属，不影响取数）</div>
          <el-select
            v-model="formOntologyId"
            size="small"
            filterable
            clearable
            :empty-values="[null, undefined]"
            placeholder="选择本体"
            class="!w-full"
          >
            <el-option v-for="ontology in ontologies" :key="ontology.id" :label="ontologyLabel(ontology)" :value="ontology.id" />
          </el-select>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">描述</div>
          <el-input v-model="formDescription" size="small" maxlength="1024" placeholder="对象类型业务含义（进入 agent 上下文）" />
        </div>
      </div>

      <!-- 属性集 -->
      <div>
        <div class="mb-1.5 flex items-center justify-between">
          <span class="text-xs font-medium text-slate-600">属性集（至少一个标识属性作主键；至多一个标题属性）</span>
          <el-button size="small" type="primary" link @click="addPropertyRow">
            <i class="fa-solid fa-plus text-[11px]"></i> 添加属性
          </el-button>
        </div>
        <OntologyPropertyEditor
          :rows="propertyRows"
          :frozen-names="frozenNames"
          @property-renamed="onPropertyRenamed"
          @property-removed="onPropertyRemoved"
          @remove-row="removePropertyRow"
        />
      </div>

      <!-- 语义模型映射 -->
      <div>
        <div class="mb-1.5 flex items-center justify-between">
          <div class="flex flex-col">
            <span class="text-xs font-medium text-slate-600">语义模型映射</span>
            <span class="text-[11px] text-slate-400">
              层级 = 数据源组 → 语义模型 → 维度映射（不触碰物理表）：口径权威在语义层；每个语义模型映射只能归属一个对象类型；认证前至少需要一个映射
            </span>
          </div>
          <el-button size="small" link type="primary" @click="addMappingRow">
            <i class="fa-solid fa-plus text-[11px]"></i> 添加映射
          </el-button>
        </div>
        <OntologyMappingEditor
          :rows="mappingRows"
          :properties="propertyRows"
          @remove-row="removeMappingRow"
        />
      </div>
    </div>

    <template #footer>
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button type="primary" size="small" :loading="saving" @click="saveObject">保存</el-button>
    </template>
  </el-dialog>
</template>
