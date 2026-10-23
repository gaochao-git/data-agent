<script setup lang="ts">
/**
 * 本体属性集编辑器：属性一等实体的行内编辑（名称/展示名/值类型/枚举引用/
 * 主键与标题标记/渲染提示）+ 派生属性添加（对齐 Palantir derived property：
 * 查询时计算、只读、不映射物理列）。属性随对象表单整体提交，这里不做独立保存。
 */
import { computed, onMounted, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { getAdminMetaEnums, getAdminOntologyObjectType, getAdminOntologyRelationTypes } from '../../api'
import type { OntologyPropertyItem, OntologyRelationTypeItem } from '../../types'
import { ONTOLOGY_RENDER_HINTS, ONTOLOGY_VALUE_TYPES } from './ontologyMeta'

const props = defineProps({
  // 属性行（父组件持有，就地编辑）
  rows: { type: Array as PropType<OntologyPropertyItem[]>, required: true },
  // certified 冻结名单：名单内的既有属性禁止删除/改名/改主键标记（对齐后端门禁）
  frozenNames: { type: Array as PropType<string[]>, default: () => [] },
  // 当前编辑的对象类型 API 名；空 = 新建态（后端要求派生属性在对象类型与关系类型创建后才能添加）
  objectTypeName: { type: String, default: '' }
})

// 属性改名/删除要同步映射键（父组件持有映射行，由表单统一处理）
const emit = defineEmits<{
  propertyRenamed: [oldName: string, newName: string]
  propertyRemoved: [propertyName: string]
  removeRow: [index: number]
  addDerived: [item: OntologyPropertyItem]
}>()

const isFrozen = (name: string) => props.frozenNames.includes(name)
// 派生属性只读、查询时计算、无物理列：名称/值类型/枚举/主键/标题/渲染提示均不可在表单改，仅展示名与描述可编辑。
const isDerived = (row: OntologyPropertyItem) => !!(row.derivation && row.derivation.kind)

// 冻结的标题键不可被其他行顶掉：认证后标题键随主键组合一起冻结
const titleLocked = computed(() => props.rows.some(row => isFrozen(row.property_name) && row.is_title))

// 全局枚举候选：value_type=enum 时供引用选择（"enum_name" 或 "domain:enum_name"）
const enumOptions = ref<Array<{ value: string; label: string }>>([])

onMounted(async () => {
  try {
    const res = await getAdminMetaEnums()
    if (res.status === 'success') {
      enumOptions.value = (res.data || []).map(item => ({
        value: item.business_domain ? `${item.business_domain}:${item.enum_name}` : item.enum_name,
        label: item.business_domain ? `${item.business_domain} / ${item.enum_name}` : item.enum_name
      }))
    }
  } catch (err) {
    // 枚举清单加载失败不阻断编辑，仍可手输引用
    console.error('获取全局枚举清单失败:', err)
  }
})

// 增删由父组件持有数组完成（子组件不直接改 props）：添加按钮在父组件，删除通过事件回传索引。
const removeRow = (index: number) => {
  const removedName = (props.rows[index]?.property_name || '').trim()
  emit('removeRow', index)
  if (removedName) emit('propertyRemoved', removedName)
}

// 属性名改名前后的快照：el-input 的 change 只在失焦/回车触发，避免输入过程中逐字符联动映射
const nameAtFocus = new WeakMap<OntologyPropertyItem, string>()

const onNameFocus = (row: OntologyPropertyItem) => {
  nameAtFocus.set(row, (row.property_name || '').trim())
}

const onNameChange = (row: OntologyPropertyItem) => {
  const oldName = nameAtFocus.get(row) ?? ''
  nameAtFocus.delete(row)
  const newName = (row.property_name || '').trim()
  if (oldName && newName && oldName !== newName) emit('propertyRenamed', oldName, newName)
}

// 值类型切走 enum 时清空枚举引用：后端要求非 enum 不得携带 enum_ref；
// Decimal 为浮点/小数类型，禁止作为标识属性（主键组合），切换时同步取消标识标记
const onValueTypeChange = (row: OntologyPropertyItem) => {
  if (row.value_type !== 'enum') row.enum_ref = ''
  if (row.value_type === 'Decimal') row.is_identifier = false
}

// 标题键至多一个：勾选某行时只取消其他「未冻结」行的标题标记，
// 冻结行的标题键属于认证门禁（改动会被后端拒绝），不能被本地编辑顶掉。
const onTitleChange = (row: OntologyPropertyItem, checked: boolean | string | number) => {
  if (!checked) return
  props.rows.forEach(item => {
    if (item !== row && !isFrozen(item.property_name)) item.is_title = false
  })
}

const toggleHint = (row: OntologyPropertyItem, key: string, checked: boolean | string | number) => {
  if (checked) {
    row.render_hints[key] = true
  } else {
    delete row.render_hints[key]
  }
}

const hintCount = (row: OntologyPropertyItem) => Object.keys(row.render_hints || {}).length

// ---------- 派生属性添加（对齐 Palantir derived property） ----------

const DERIVATION_OPERATORS = ['count', 'sum', 'avg', 'min', 'max'] as const
const BUILTIN_VALUE_TYPES = ['String', 'Integer', 'Decimal', 'Boolean', 'Date', 'DateTime']

const showDerivedForm = ref(false)
const derivedLoading = ref(false)
const derivedError = ref('')
const derivedForm = ref({
  property_name: '',
  display_name: '',
  kind: 'aggregate' as 'aggregate' | 'select',
  relation_type_name: '',
  operator: 'count',
  target_property: ''
})
// 关系候选（已按当前对象类型在 to/from 侧过滤，aggregate→to 侧、select→from 侧）
const relationOptions = ref<Array<{ name: string; relatedType: string; multiplicity: string }>>([])
// 目标属性候选（选定关系后拉对端对象类型详情，仅内建值类型可派生）
const targetOptions = ref<Array<{ name: string; value_type: string }>>([])

const canUseDerived = computed(() => !!props.objectTypeName)
const needsTarget = computed(
  () => derivedForm.value.kind === 'select' || (derivedForm.value.kind === 'aggregate' && derivedForm.value.operator !== 'count')
)

const openDerivedForm = async () => {
  if (!canUseDerived.value) return
  showDerivedForm.value = true
  derivedError.value = ''
  derivedForm.value = {
    property_name: '',
    display_name: '',
    kind: 'aggregate',
    relation_type_name: '',
    operator: 'count',
    target_property: ''
  }
  derivedLoading.value = true
  try {
    // 并行按 from/to 两个方向过滤：aggregate 要求本对象在 to 侧，select 要求在 from 侧
    const [fromRes, toRes] = await Promise.all([
      getAdminOntologyRelationTypes({ from_object_type: props.objectTypeName }),
      getAdminOntologyRelationTypes({ to_object_type: props.objectTypeName })
    ])
    if (fromRes.status !== 'success' || toRes.status !== 'success') {
      derivedError.value = fromRes.msg || toRes.msg || '获取关系类型清单失败'
      return
    }
    relationOptions.value = [...(fromRes.data?.relation_types || []), ...(toRes.data?.relation_types || [])]
      .filter(item => item.status !== 'deprecated')
      .map(item => ({
        name: item.relation_type_name,
        relatedType:
          item.from_object_type_name === props.objectTypeName
            ? item.to_object_type_name || ''
            : item.from_object_type_name || '',
        multiplicity: item.multiplicity
      }))
  } catch (err) {
    console.error('获取关系类型清单失败:', err)
    derivedError.value = '获取关系类型清单失败'
  } finally {
    derivedLoading.value = false
  }
}

const onDerivedRelationChange = async () => {
  targetOptions.value = []
  derivedForm.value.target_property = ''
  const relation = relationOptions.value.find(item => item.name === derivedForm.value.relation_type_name)
  if (!relation || !relation.relatedType) return
  try {
    const res = await getAdminOntologyObjectType(relation.relatedType)
    if (res.status === 'success' && res.data) {
      targetOptions.value = (res.data.properties || [])
        .filter(item => BUILTIN_VALUE_TYPES.includes(item.value_type))
        .map(item => ({ name: item.property_name, value_type: item.value_type }))
    }
  } catch (err) {
    console.error('获取对端对象类型属性失败:', err)
  }
}

const onDerivedKindChange = () => {
  // kind 切换时关系候选方向相反：清空已选关系与目标属性
  derivedForm.value.relation_type_name = ''
  derivedForm.value.target_property = ''
  targetOptions.value = []
  if (derivedForm.value.kind === 'aggregate') derivedForm.value.operator = 'count'
}

const submitDerived = () => {
  const name = derivedForm.value.property_name.trim()
  if (!/^[A-Za-z0-9_]{1,128}$/.test(name)) {
    derivedError.value = '属性名仅允许字母、数字、下划线，长度 1-128'
    return
  }
  if (props.rows.some(row => row.property_name === name)) {
    derivedError.value = `属性API名重复: ${name}`
    return
  }
  if (!derivedForm.value.relation_type_name) {
    derivedError.value = '请选择遍历的关系类型'
    return
  }
  if (derivedForm.value.kind === 'aggregate' && !DERIVATION_OPERATORS.includes(derivedForm.value.operator as never)) {
    derivedError.value = '请选择聚合算子'
    return
  }
  if (needsTarget.value && !derivedForm.value.target_property) {
    derivedError.value = '请选择对端目标属性'
    return
  }
  derivedError.value = ''
  const derivation: NonNullable<OntologyPropertyItem['derivation']> = {
    kind: derivedForm.value.kind,
    relation_type_name: derivedForm.value.relation_type_name,
    ...(derivedForm.value.kind === 'aggregate'
      ? { operator: derivedForm.value.operator as (typeof DERIVATION_OPERATORS)[number] }
      : {}),
    ...(needsTarget.value ? { target_property: derivedForm.value.target_property } : {})
  }
  emit('addDerived', {
    property_name: name,
    display_name: derivedForm.value.display_name.trim(),
    description: '',
    value_type: derivedForm.value.kind === 'aggregate' && derivedForm.value.operator === 'count' ? 'Integer' : 'Decimal',
    enum_ref: '',
    render_hints: {},
    is_identifier: false,
    is_title: false,
    derivation
  })
  showDerivedForm.value = false
}

watch(showDerivedForm, value => {
  if (!value) derivedError.value = ''
})

</script>

<template>
  <div class="flex flex-col gap-1.5">
    <!-- 表头 -->
    <div class="flex items-center gap-2 text-[11px] font-medium text-slate-400">
      <span class="w-36 shrink-0">属性名</span>
      <span class="w-28 shrink-0">展示名</span>
      <span class="w-28 shrink-0">值类型</span>
      <span class="w-40 shrink-0">枚举引用</span>
      <span class="w-14 shrink-0 text-center">主键</span>
      <span class="w-14 shrink-0 text-center">标题</span>
      <span class="w-16 shrink-0 text-center">渲染</span>
      <span class="min-w-0 flex-1">描述</span>
      <span class="w-8 shrink-0"></span>
    </div>
    <!-- 属性行 -->
    <div v-for="(row, index) in rows" :key="index" class="flex items-center gap-2">
      <el-input
        v-model="row.property_name"
        size="small"
        :disabled="isFrozen(row.property_name) || isDerived(row)"
        placeholder="如 order_no"
        class="!w-36 shrink-0 font-mono"
        @focus="onNameFocus(row)"
        @change="onNameChange(row)"
      />
      <el-input v-model="row.display_name" size="small" placeholder="展示名" class="!w-28 shrink-0" />
      <el-tooltip v-if="isDerived(row)" content="派生属性：只读、查询时计算、不映射物理列" placement="top">
        <el-tag size="small" type="warning" effect="plain" class="shrink-0">派生</el-tag>
      </el-tooltip>
      <el-select
        v-model="row.value_type"
        size="small"
        class="!w-28 shrink-0"
        :disabled="isDerived(row)"
        :empty-values="[null, undefined]"
        @change="onValueTypeChange(row)"
      >
        <el-option v-for="t in ONTOLOGY_VALUE_TYPES" :key="t" :label="t" :value="t" />
      </el-select>
      <el-select
        v-model="row.enum_ref"
        size="small"
        class="!w-40 shrink-0"
        :disabled="row.value_type !== 'enum' || isDerived(row)"
        filterable
        allow-create
        default-first-option
        :empty-values="[null, undefined]"
        :placeholder="row.value_type === 'enum' ? '选择全局枚举' : '-'"
      >
        <el-option v-for="opt in enumOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
      </el-select>
      <div class="flex w-14 shrink-0 justify-center">
        <el-checkbox
          v-model="row.is_identifier"
          :disabled="isFrozen(row.property_name) || row.value_type === 'Decimal' || isDerived(row)"
          :title="row.value_type === 'Decimal' ? 'Decimal 为浮点/小数类型，禁止作为标识属性' : undefined"
        />
      </div>
      <div class="flex w-14 shrink-0 justify-center">
        <el-checkbox
          v-model="row.is_title"
          :disabled="isFrozen(row.property_name) || (titleLocked && !row.is_title) || isDerived(row)"
          @change="(checked: boolean | string | number) => onTitleChange(row, checked)"
        />
      </div>
      <div class="flex w-16 shrink-0 justify-center">
        <el-popover placement="bottom" :width="180" trigger="click">
          <template #reference>
            <el-button link size="small" type="primary" :disabled="isDerived(row)">
              <i class="fa-solid fa-sliders text-[11px]"></i>
              <span v-if="hintCount(row)" class="ml-0.5 text-[11px]">{{ hintCount(row) }}</span>
            </el-button>
          </template>
          <div class="flex flex-col gap-2">
            <el-checkbox
              v-for="hint in ONTOLOGY_RENDER_HINTS"
              :key="hint.key"
              :model-value="!!row.render_hints[hint.key]"
              @change="(checked: boolean | string | number) => toggleHint(row, hint.key, checked)"
            >
              {{ hint.label }}
            </el-checkbox>
          </div>
        </el-popover>
      </div>
      <el-input v-model="row.description" size="small" placeholder="属性描述（可选）" class="min-w-0 flex-1" />
      <div class="w-8 shrink-0 text-right">
        <el-button
          link
          size="small"
          type="danger"
          :disabled="isFrozen(row.property_name)"
          @click="removeRow(index)"
        >
          <i class="fa-solid fa-trash text-[11px]"></i>
        </el-button>
      </div>
    </div>
    <div v-if="rows.length === 0" class="rounded border border-dashed border-slate-200 py-4 text-center text-xs text-slate-400">
      暂无属性，请至少添加一个属性（含标识属性作为主键）
    </div>

    <!-- 派生属性添加：查询时计算、只读、不映射物理列（对齐 Palantir derived property） -->
    <div class="flex items-center justify-between">
      <el-tooltip
        :content="canUseDerived ? '' : '保存对象类型后（编辑态）才能添加派生属性：需要先存在关系类型'"
        placement="top"
        :disabled="canUseDerived"
      >
        <el-button size="small" type="primary" link :disabled="!canUseDerived || derivedLoading" @click="openDerivedForm">
          <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i> 添加派生属性
        </el-button>
      </el-tooltip>
      <span v-if="derivedLoading" class="text-[11px] text-slate-400">加载关系候选中…</span>
    </div>
    <div v-if="showDerivedForm" class="rounded-md border border-amber-100 bg-amber-50/60 p-2.5">
      <div class="mb-2 flex items-center gap-2 text-[11px] text-amber-700">
        <i class="fa-solid fa-circle-info"></i>
        <span>
          aggregate：本对象类型须在关系 to 侧（聚合对端多行，如订单数）；select：须在 from 侧（取对端单对象属性）。
          值类型由服务端推导（count→Integer，sum/avg→Decimal，其余同目标属性）。
        </span>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <el-input
          v-model="derivedForm.property_name"
          size="small"
          placeholder="属性名（如 order_count）"
          class="!w-40 shrink-0 font-mono"
        />
        <el-input v-model="derivedForm.display_name" size="small" placeholder="展示名" class="!w-28 shrink-0" />
        <el-select v-model="derivedForm.kind" size="small" class="!w-32 shrink-0" @change="onDerivedKindChange">
          <el-option label="aggregate 聚合" value="aggregate" />
          <el-option label="select 选取" value="select" />
        </el-select>
        <el-select
          v-model="derivedForm.relation_type_name"
          size="small"
          filterable
          :empty-values="[null, undefined]"
          placeholder="选择关系类型"
          class="!w-52 shrink-0"
          @change="onDerivedRelationChange"
        >
          <el-option
            v-for="item in relationOptions"
            :key="item.name"
            :label="`${item.name}（对端 ${item.relatedType}）`"
            :value="item.name"
          />
        </el-select>
        <el-select
          v-if="derivedForm.kind === 'aggregate'"
          v-model="derivedForm.operator"
          size="small"
          class="!w-28 shrink-0"
        >
          <el-option v-for="op in DERIVATION_OPERATORS" :key="op" :label="op" :value="op" />
        </el-select>
        <el-select
          v-if="needsTarget"
          v-model="derivedForm.target_property"
          size="small"
          filterable
          :empty-values="[null, undefined]"
          placeholder="对端目标属性"
          class="!w-40 shrink-0"
        >
          <el-option
            v-for="item in targetOptions"
            :key="item.name"
            :label="`${item.name} (${item.value_type})`"
            :value="item.name"
          />
        </el-select>
        <el-button size="small" type="primary" @click="submitDerived">添加</el-button>
        <el-button size="small" @click="showDerivedForm = false">取消</el-button>
      </div>
      <div v-if="derivedError" class="mt-1.5 text-[11px] text-red-600">{{ derivedError }}</div>
    </div>
  </div>
</template>
